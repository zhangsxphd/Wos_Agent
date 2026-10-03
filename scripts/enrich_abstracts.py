"""Enrich canonical WoS JSONL with verified DOI-only abstract candidates."""

import argparse
import copy
import hashlib
import itertools
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.merge_searches import collect_searches
from scripts.normalize_record import normalize_doi, normalize_title
from scripts.pipeline_utils import (ROOT, assert_safe, display_path, known_secrets,
                                    new_run_id, project_path, read_jsonl, redact,
                                    utc_now, write_json, write_jsonl)
from scripts.providers import CrossrefClient, SemanticScholarClient, OpenAlexClient, PROVIDER_NAMES
from scripts.providers.base_client import ProviderError, canonical_doi
from scripts.providers.crossref_client import clean_markup


PROTECTED_FIELDS = ("uid", "title", "authors", "publish_year", "source_title", "doi", "issn", "eissn",
                    "times_cited_wos", "matched_queries", "provenance_history")
POLICY = {"version": 1, "provider_priority": list(PROVIDER_NAMES), "title_similarity_min": 0.85,
          "year_tolerance": 1, "abstract_min_chars": 120, "abstract_max_chars": 60000,
          "abstract_agreement_min": 0.90,
          "conflict_action": "retain_previous_abstract_if_present_else_null",
          "missing_metadata": "missing title rejects; missing year warns"}


def similarity(left, right):
    return SequenceMatcher(None, left, right, autojunk=False).ratio() if left and right else 0.0


def year_number(value):
    try:
        return int(value) if not isinstance(value, bool) and str(value).isdigit() else None
    except (ValueError, TypeError):
        return None


def abstract_quality(text):
    if not isinstance(text, str) or not text.strip():
        return "empty_abstract"
    if len(text.strip()) < POLICY["abstract_min_chars"]:
        return "short_abstract"
    if len(text) > POLICY["abstract_max_chars"]:
        return "oversized_abstract"
    if re.search(r"<[A-Za-z/][^>]*>", text) or "\x00" in text:
        return "abnormal_abstract"
    if sum(c.isalpha() for c in text) / len(text) < 0.35:
        return "abnormal_abstract"
    if sum(ord(c) < 32 and c not in "\t\n\r" for c in text) / len(text) > 0.01:
        return "abnormal_abstract"
    return None


def validate_candidate(record, candidate):
    result = {key: candidate.get(key) for key in
              ("provider", "provider_id", "doi", "title", "year", "abstract", "url", "retrieved_at")}
    result["doi_exact_match"] = bool(record.get("doi") and
                                     normalize_doi(record.get("doi")) == normalize_doi(candidate.get("doi")))
    score = similarity(normalize_title(record.get("title")), normalize_title(clean_markup(candidate.get("title"))))
    result["title_similarity"] = round(score, 6)
    wos_year, provider_year = year_number(record.get("publish_year")), year_number(candidate.get("year"))
    difference = provider_year - wos_year if wos_year is not None and provider_year is not None else None
    result["year_difference"] = difference
    result["year_match"] = abs(difference) <= POLICY["year_tolerance"] if difference is not None else None
    warnings = []
    if difference is None:
        warnings.append("year_unavailable")
    elif difference:
        warnings.append("publication_year_differs")
    if candidate.get("parse_warning"):
        warnings.append(candidate["parse_warning"])
    reasons = []
    if not result["doi_exact_match"]:
        reasons.append("doi_mismatch_or_missing")
    if score < POLICY["title_similarity_min"]:
        reasons.append("title_mismatch_or_missing")
    if result["year_match"] is False:
        reasons.append("year_mismatch")
    quality = abstract_quality(result["abstract"])
    if quality:
        reasons.append(quality)
    result.update(accepted=not reasons, rejection_reasons=reasons, warnings=warnings)
    return result


def select_abstract(record, candidates, provider_results):
    accepted = sorted((c for c in candidates if c["accepted"]),
                      key=lambda c: PROVIDER_NAMES.index(c["provider"]))
    pairs = [{"providers": [a["provider"], b["provider"]],
              "similarity": round(similarity(normalize_title(a["abstract"]), normalize_title(b["abstract"])), 6)}
             for a, b in itertools.combinations(accepted, 2)]
    conflict = any(p["similarity"] < POLICY["abstract_agreement_min"] for p in pairs)
    previous = bool(record.get("abstract"))
    selected = None
    if conflict:
        status = "conflict"
        reason = "Valid DOI/title/year candidates disagree below the abstract agreement threshold; " + (
            "previous canonical abstract retained explicitly." if previous else "canonical abstract remains null.")
    elif accepted:
        selected = accepted[0]
        status = "found"
        reason = ("All accepted candidates agree; " if len(accepted) > 1 else "One accepted candidate; ") + \
                 "selected by fixed priority crossref > semantic_scholar > openalex."
    elif previous:
        status, reason = "retained_previous", "No accepted new candidate; previous canonical abstract retained explicitly."
    else:
        errors = any(r["status"] == "error" for r in provider_results.values())
        status = "missing_with_errors" if errors else "missing"
        reason = "No nonempty abstract passed DOI, title, year and text-quality validation."
    enrichment = {"status": status, "candidates": candidates, "provider_results": provider_results,
                  "selected_provider": selected["provider"] if selected else record.get("abstract_source") if previous else None,
                  "selection_reason": reason, "conflict": conflict, "corroborated": len(accepted) > 1 and not conflict,
                  "pairwise_abstract_similarity": pairs, "previous_abstract_retained": previous and selected is None,
                  "policy_version": POLICY["version"]}
    if selected:
        record.update(abstract=selected["abstract"], abstract_source=selected["provider"],
                      abstract_retrieved_at=selected["retrieved_at"])
    elif not previous:
        record.update(abstract=None, abstract_source=None, abstract_retrieved_at=None)
    record["abstract_enrichment"] = enrichment
    return record


def enrich_record(original, clients, root=ROOT, skip_providers=(), batch_results=None):
    record = copy.deepcopy(original)
    if not record.get("doi") or not str(record["doi"]).strip():
        record.update(abstract=None, abstract_source=None, abstract_retrieved_at=None,
                      abstract_enrichment={"status": "no_doi", "candidates": [],
                                           "provider_results": {name: {"status": "skipped", "cache_hit": False} for name in skip_providers},
                                           "selected_provider": None, "selection_reason": "No DOI; no provider lookup performed.",
                                           "conflict": False, "corroborated": False, "policy_version": POLICY["version"]})
        return record
    candidates, outcomes = [], {}
    for name in PROVIDER_NAMES:
        if name in skip_providers:
            outcomes[name] = {"status": "skipped", "cache_hit": False}
            continue
        client = clients[name]
        try:
            doi = normalize_doi(record["doi"])
            provider_batch = None
            if isinstance(batch_results, dict) and name in batch_results:
                provider_batch = batch_results[name]
            elif name == "openalex":
                # Backwards-compatible direct calls may still pass the old
                # OpenAlex DOI -> result mapping.
                provider_batch = batch_results
            if provider_batch is not None and doi in provider_batch:
                response = provider_batch[doi]
                cache = client.lookup_metadata.get(doi, {})
                if isinstance(response, ProviderError):
                    raise response
            else:
                response = client.get_by_doi(record["doi"])
                cache = getattr(client, "last_lookup", {})
            outcome = {"status": cache.get("status", "found" if response else "not_found"),
                       "cache_hit": cache.get("cache_hit", False)}
            if response:
                candidate = validate_candidate(record, response)
                if cache.get("cache_file"):
                    candidate["cache_file"] = display_path(cache["cache_file"], root)
                candidates.append(candidate)
                outcome["abstract_accepted"] = candidate["accepted"]
                if response.get("parse_warning"):
                    outcome["parse_warning"] = response["parse_warning"]
            outcomes[name] = outcome
        except ProviderError as exc:
            outcomes[name] = {"status": "error", "error_code": exc.code, "http_status": exc.http_status,
                              "cache_hit": client.lookup_metadata.get(doi, {}).get("cache_hit", False)
                              if provider_batch is not None else getattr(client, "last_lookup", {}).get("cache_hit", False)}
    return select_abstract(record, candidates, outcomes)


def coverage_report(records, clients, skip_providers=()):
    count = len(records)
    found = sum(bool(r.get("abstract")) for r in records)
    valid_sets = [{c["provider"] for c in r["abstract_enrichment"]["candidates"] if c["accepted"]} for r in records]
    report = {"input_records": count, "records_with_doi": sum(bool(str(r.get("doi") or "").strip()) for r in records),
              "abstract_found_total": found, "abstract_missing_total": count - found,
              "coverage_percent": round(100 * found / count, 2) if count else 0,
              "found_by_multiple_sources": sum(len(s) > 1 for s in valid_sets),
              "conflict_count": sum(r["abstract_enrichment"]["conflict"] for r in records),
              "canonical_source_counts": dict(Counter(r.get("abstract_source") or "unknown" for r in records if r.get("abstract"))),
              "status_counts": dict(Counter(r["abstract_enrichment"]["status"] for r in records)),
              "selection_policy": POLICY,
              "provider_statistics": {name: ({"requests": 0, "cache_hits": 0, "not_found": 0, "errors": 0,
                                              "error_counts": {}, "status": "skipped"} if name in skip_providers
                                            else dict(clients[name].stats, error_counts=dict(clients[name].error_counts)))
                                      for name in PROVIDER_NAMES},
              "records": [{"uid": r.get("uid"), "doi": r.get("doi"), "title": r.get("title"),
                           "abstract_source": r.get("abstract_source"), "abstract_status": r["abstract_enrichment"]["status"],
                           "provider_results": r["abstract_enrichment"]["provider_results"]} for r in records]}
    report["records_without_doi"] = count - report["records_with_doi"]
    for name in PROVIDER_NAMES:
        report["found_by_" + name] = sum(name in providers for providers in valid_sets)
    report["openalex_rate_limit_headers"] = getattr(clients.get("openalex"), "rate_limit_history", [])
    report["semantic_scholar_rate_limit_headers"] = getattr(
        clients.get("semantic_scholar"), "rate_limit_history", []
    )
    report["provider_overlaps"] = {
        "crossref_and_semantic_scholar": sum({"crossref", "semantic_scholar"}.issubset(s) for s in valid_sets),
        "crossref_and_openalex": sum({"crossref", "openalex"}.issubset(s) for s in valid_sets),
        "semantic_scholar_and_openalex": sum({"semantic_scholar", "openalex"}.issubset(s) for s in valid_sets),
        "all_three": sum(len({"crossref", "semantic_scholar", "openalex"} & s) == 3 for s in valid_sets),
    }
    report["actual_http_request_count"] = sum(p["requests"] for p in report["provider_statistics"].values())
    report["unresolved"] = count - found
    report["openalex_coverage"] = {
        "records_with_doi": report["records_with_doi"], "found": report["found_by_openalex"],
        "coverage_percent": round(100 * report["found_by_openalex"] / report["records_with_doi"], 2) if report["records_with_doi"] else 0,
        "lookup_status_counts": dict(Counter(r["abstract_enrichment"]["provider_results"].get("openalex", {}).get("status", "no_doi") for r in records)),
    }
    return report


def enrich_file(input_file, output_file, root=ROOT, clients=None, refresh=False, cache_only=False,
                refresh_providers=(), skip_providers=None, cache_only_providers=()):
    root = Path(root).resolve()
    source, output = project_path(input_file, root), project_path(output_file, root)
    manifest_path = output.with_suffix(".manifest.json")
    if source.resolve() == output.resolve() or output.exists() or manifest_path.exists():
        raise FileExistsError("Enrichment needs a new output path; existing canonical files are never overwritten")
    secrets = known_secrets(root)
    assert_safe([str(source), str(output)], secrets)
    original = read_jsonl(source)
    assert_safe(original, secrets)
    owned = clients is None
    # Explicit injected client dictionaries retain the old offline API
    # contract; owned clients now enable all three providers by default.
    skip_providers = tuple() if skip_providers is None else tuple(skip_providers)
    refresh_providers = set(refresh_providers)
    if any(name not in PROVIDER_NAMES for name in (*skip_providers, *refresh_providers, *cache_only_providers)):
        raise ValueError("Unknown provider option")
    if refresh_providers.intersection(skip_providers):
        raise ValueError("A skipped provider cannot be refreshed")
    if (cache_only and (refresh or refresh_providers)) or refresh_providers.intersection(cache_only_providers):
        raise ValueError("A provider cannot be refreshed in cache-only mode")
    if owned and refresh_providers and "semantic_scholar" not in refresh_providers:
        # Preserve the v0.3 selective-refresh contract: an explicit refresh of
        # another provider does not silently activate a new S2 network call.
        skip_providers = tuple(dict.fromkeys((*skip_providers, "semantic_scholar")))
    if owned:
        classes = {"crossref": CrossrefClient, "semantic_scholar": SemanticScholarClient,
                   "openalex": OpenAlexClient}
        clients = {name: None if name in skip_providers else classes[name](
                    root=root, refresh=refresh or name in refresh_providers,
                    cache_only=cache_only or name in cache_only_providers) for name in PROVIDER_NAMES}
    started = utc_now()
    try:
        batch_results = None
        batch_results = {}
        if "semantic_scholar" not in skip_providers and hasattr(clients["semantic_scholar"], "get_many_by_doi"):
            dois = []
            for record in original:
                try:
                    dois.append(canonical_doi(record.get("doi")))
                except ValueError:
                    pass
            batch_results["semantic_scholar"] = clients["semantic_scholar"].get_many_by_doi(dois)
        if "openalex" not in skip_providers and hasattr(clients["openalex"], "get_many_by_doi"):
            dois = []
            for record in original:
                try:
                    dois.append(canonical_doi(record.get("doi")))
                except ValueError:
                    pass
            batch_results["openalex"] = clients["openalex"].get_many_by_doi(dois)
        if not batch_results:
            batch_results = None
        enriched = []
        for index, record in enumerate(original, 1):
            enriched.append(enrich_record(record, clients, root, skip_providers, batch_results))
            if any(enriched[-1].get(key) != record.get(key) or (key in enriched[-1]) != (key in record)
                   for key in PROTECTED_FIELDS):
                raise RuntimeError("Enrichment changed a protected WoS field")
            print(f"Enrichment {index}/{len(original)}: {enriched[-1]['abstract_enrichment']['status']}", flush=True)
        report = coverage_report(enriched, clients, skip_providers)
        run_id = new_run_id("abstracts")
        report_path = root / "data/reports" / (run_id + "_abstract_coverage.json")
        report.update(run_id=run_id, started_at=started, completed_at=utc_now(),
                      canonical_input=display_path(source, root), enriched_output=display_path(output, root),
                      input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), refresh=refresh, cache_only=cache_only,
                      refresh_providers=sorted(refresh_providers), skipped_providers=list(skip_providers),
                      cache_only_providers=list(cache_only_providers),
                      openalex_lookup_mode="batch" if isinstance(batch_results, dict) and "openalex" in batch_results else "legacy",
                      semantic_scholar_lookup_mode="batch" if isinstance(batch_results, dict) and "semantic_scholar" in batch_results else "legacy")
        manifest = {"schema_version": 3, "run_id": run_id, "canonical_input": display_path(source, root),
                    "processed_file": display_path(output, root), "records": len(enriched),
                    "coverage_report": display_path(report_path, root), "retrieved_at": report["completed_at"],
                    "searches": collect_searches([source], [original], root)}
        # Validate all serialized data before creating any canonical/report output.
        for value in (enriched, report, manifest):
            assert_safe(value, secrets)
        write_jsonl(output, enriched, secrets)
        write_json(report_path, report, secrets)
        write_json(manifest_path, manifest, secrets)
        print(f"Abstracts: {report['abstract_found_total']}/{len(original)}; conflicts: {report['conflict_count']}")
        print(f"Coverage report: {report_path}")
        return report
    finally:
        if owned:
            for client in clients.values():
                if client is not None and hasattr(client, "close"):
                    client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="Refresh success and negative caches")
    group.add_argument("--cache-only", action="store_true", help="Offline cache replay; never make a network request")
    parser.add_argument("--refresh-provider", action="append", choices=["semantic_scholar", "openalex"], default=[],
                        help="Refresh one provider only; other providers keep their caches")
    parser.add_argument("--cache-only-provider", action="append", choices=["crossref", "semantic_scholar", "openalex"], default=[],
                        help="Never request this provider; read its existing cache only")
    args = parser.parse_args()
    try:
        enrich_file(args.input, args.output, refresh=args.refresh, cache_only=args.cache_only,
                    refresh_providers=args.refresh_provider, cache_only_providers=args.cache_only_provider)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Enrichment failed: {redact(exc, known_secrets())}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
