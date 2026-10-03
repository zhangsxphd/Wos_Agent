"""Resolve full text via public OA first, then entitled Elsevier Article Retrieval API."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, new_run_id, read_jsonl, write_json, write_jsonl
from scripts.fulltext.openalex_content import OpenAlexContentClient
from scripts.fulltext.resolver import FulltextResolver
from scripts.fulltext.elsevier_content import ElsevierArticleClient
from scripts.fulltext.acquisition_router import AcquisitionRouter
from scripts.fulltext.provenance import FulltextError


def resolve_file(input_file, root=ROOT, output=None, refresh=False):
    root = Path(root)
    source = Path(input_file)
    if not source.is_absolute():
        source = root / source
    records = read_jsonl(source)
    secrets = known_secrets(root)
    assert_safe(records, secrets)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    oa_client = OpenAlexContentClient(root=root)
    oa_resolver = FulltextResolver(root=root, client=oa_client)
    elsevier = ElsevierArticleClient(root=root)
    router = AcquisitionRouter(root=root, oa_resolver=oa_resolver, elsevier_client=elsevier)

    run = new_run_id("entitled_fulltext")
    target = Path(output) if output else root / "data/fulltext/runs" / (run + ".jsonl")
    if not target.is_absolute():
        target = root / target
    if target.exists() or target.resolve() == source.resolve():
        raise FileExistsError("New sidecar output required")

    rows = []
    try:
        for index, record in enumerate(records, 1):
            work = None
            try:
                if record.get("doi") or record.get("openalex_id"):
                    work = oa_client.lookup(record.get("doi") or record.get("openalex_id"), refresh=refresh)
                result = router.resolve(record, work=work, refresh=refresh)
            except (FulltextError, ValueError, OSError, KeyError) as exc:
                code = exc.code if isinstance(exc, FulltextError) else "invalid_record_or_cache"
                result = {
                    "uid": record.get("uid"),
                    "doi": record.get("doi"),
                    "status": "error",
                    "reason": code,
                    "acquisition_route": None,
                    "router_attempts": [],
                }
            result.setdefault("acquisition_route", None)
            result.setdefault("source", None)
            result.setdefault("format", None)
            result.setdefault("http_status", None)
            result.setdefault("reason", result.get("router_reason"))
            result.setdefault("raw_file", None)
            result.setdefault("parsed_file", None)
            result.setdefault("body_paragraph_count", 0)
            rows.append(result)
            print(
                f"Fulltext {index}/{len(records)}: {result.get('status')} "
                f"({result.get('acquisition_route') or 'none'})",
                flush=True,
            )
    finally:
        oa_client.close()
        elsevier.close()

    if hashlib.sha256(source.read_bytes()).hexdigest() != before:
        raise RuntimeError("Canonical input changed")

    assert_safe(rows, known_secrets(root))
    write_jsonl(target, rows, known_secrets(root))
    report = {
        "run_id": run,
        "canonical_input": str(source),
        "canonical_sha256": before,
        "records": len(rows),
        "available": sum(r.get("status") == "available" for r in rows),
        "openalex_oa": sum(r.get("acquisition_route") == "openalex_oa" for r in rows),
        "elsevier_api": sum(r.get("acquisition_route") == "elsevier_api" for r in rows),
        "elsevier_fulltext_success": sum(
            r.get("status") == "available" and r.get("acquisition_route") == "elsevier_api"
            for r in rows
        ),
        "elsevier_access_denied": sum(
            any(a.get("error_code") == "elsevier_access_denied" for a in r.get("router_attempts", []))
            for r in rows
        ),
        "elsevier_not_found": sum(
            any(a.get("error_code") == "elsevier_not_found" for a in r.get("router_attempts", []))
            for r in rows
        ),
        "elsevier_rate_limited": sum(
            any(a.get("error_code") == "elsevier_rate_limited" for a in r.get("router_attempts", []))
            for r in rows
        ),
        "transient_errors": sum(
            any(a.get("error_code", "").startswith(("elsevier_network", "elsevier_request", "elsevier_server", "elsevier_retry")) for a in r.get("router_attempts", []))
            for r in rows
        ),
        "parsed_success": sum(r.get("status") == "available" and r.get("parse_status") == "parsed" for r in rows),
        "unavailable": sum(r.get("status") == "unavailable" for r in rows),
        "errors": sum(r.get("status") == "error" for r in rows),
        "openalex_http_stats": oa_client.stats,
        "elsevier_http_stats": elsevier.stats,
        "elsevier_rate_limit_headers": elsevier.rate_limit_history,
        "elsevier_configured": elsevier.configured,
        "actual_http_requests": oa_client.stats.get("requests", 0) + elsevier.stats.get("requests", 0),
        "cache_hits": {
            "openalex_work": oa_client.stats.get("work_cache_hits", 0),
            "openalex_fulltext": oa_resolver.cache_hits,
            "elsevier_fulltext": router.stats.get("cache_hits", 0),
        },
        "sidecar": str(target),
    }
    write_json(target.with_suffix(".manifest.json"), report, known_secrets(root))
    print(json.dumps(report, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        resolve_file(args.input, output=args.output, refresh=args.refresh)
    except (ValueError, OSError, RuntimeError, FileExistsError):
        print("Entitled full-text run failed; inspect safe sidecars.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
