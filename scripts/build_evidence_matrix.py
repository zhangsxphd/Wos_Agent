"""Build traceable evidence JSONL from enriched records; offline by default."""

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.extractors import CachedExtractor, ManualExtractor, ExtractorError, build_payload, payload_key, empty_record
from scripts.extractors.schema_validator import EvidenceValidator, EvidenceValidationError
from scripts.pipeline_utils import ROOT, assert_safe, display_path, known_secrets, new_run_id, project_path, read_jsonl, redact, utc_now, write_json, write_jsonl


def prepare_manual(records, run_dir, secrets=()):
    items = []
    for record in records:
        if not record.get("abstract"):
            continue
        key = payload_key(record)
        request = run_dir / "requests" / (key + ".json")
        template = run_dir / "templates" / (key + ".json")
        write_json(request, build_payload(record), secrets)
        write_json(template, empty_record(record), secrets)
        items.append({"uid": record.get("uid"), "doi": record.get("doi"), "payload_key": key,
                      "request_file": str(request), "response_template": str(template)})
    return items


def build_matrix(input_file, output=None, root=ROOT, extractor=None, manual_dir=None,
                 cache_only=False, refresh=False, prepare_only=False):
    root = Path(root).resolve()
    source = project_path(input_file, root)
    secrets = known_secrets(root)
    assert_safe([str(source), str(output or ""), str(manual_dir or "")], secrets)
    records = read_jsonl(source)
    assert_safe(records, secrets)
    input_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    run_id = new_run_id("evidence")
    raw_dir = root / "data/raw/evidence" / run_id
    target = project_path(output, root) if output else root / "data/evidence" / (run_id + "_evidence.jsonl")
    manifest_path = target.with_suffix(".manifest.json")
    if target.resolve() == source.resolve() or target.exists() or manifest_path.exists():
        raise FileExistsError("Evidence output needs a new path; canonical JSONL is never overwritten")
    if cache_only and (refresh or manual_dir):
        raise ValueError("Cache-only mode cannot refresh or use manual fallback")
    validator = EvidenceValidator()
    prepared = prepare_manual(records, raw_dir, secrets)
    if prepare_only:
        manifest = {"run_id": run_id, "status": "prepared", "canonical_input": display_path(source, root),
                    "input_sha256": input_hash, "requests": prepared, "records_without_abstract": sum(not r.get("abstract") for r in records)}
        write_json(raw_dir / "prepare_manifest.json", manifest, secrets)
        print(f"Prepared {len(prepared)} manual requests: {raw_dir}")
        return manifest
    if extractor is None:
        fallback = ManualExtractor(project_path(manual_dir, root)) if manual_dir else None
        if not cache_only and fallback is None:
            raise ValueError("Supply --manual-dir or --cache-only; use --prepare-only to create manual templates")
        extractor = CachedExtractor(fallback=fallback, root=root, refresh=refresh)
    rows, results = [], []
    for index, record in enumerate(records, 1):
        key = payload_key(record)
        raw_path = raw_dir / "responses" / (f"{index:04d}_" + key + ".json")
        status, error_code = "extracted", None
        if not record.get("abstract"):
            evidence = validator.normalize(empty_record(record), record)
            status = "needs_fulltext_no_abstract"
            # No extractor invocation, fabricated raw response, or inference.
            raw_file = None
        else:
            raw = None
            try:
                raw = extractor.extract(record)
                assert_safe(raw, secrets)
                write_json(raw_path, raw, secrets)
                raw_file = display_path(raw_path, root)
                evidence = validator.normalize(raw, record)
            except (ExtractorError, EvidenceValidationError, ValueError, KeyError, OSError) as exc:
                # Safe raw data, even rejected hallucinations, is retained for
                # review. Credentials are never archived, even on rejection.
                raw = raw if raw is not None else getattr(extractor, "last_raw_response", None)
                raw_file = None
                if raw is not None:
                    try:
                        assert_safe(raw, secrets)
                        if not raw_path.exists():
                            write_json(raw_path, raw, secrets)
                        raw_file = display_path(raw_path, root)
                    except (ValueError, OSError):
                        pass
                status, error_code = "rejected", "extraction_rejected"
                evidence = validator.normalize(empty_record(record, "needs_fulltext",
                    "Extraction could not be validated; source content requires manual review."), record)
        metadata = copy.deepcopy(getattr(extractor, "last_metadata", {})) if record.get("abstract") else {}
        evidence["extraction_provenance"] = {
            "canonical_input": display_path(source, root), "canonical_input_sha256": input_hash,
            "payload_sha256": key, "raw_response_file": raw_file, "built_at": utc_now(),
            "abstract_source": record.get("abstract_source"), "abstract_retrieved_at": record.get("abstract_retrieved_at"),
            "document_types": record.get("document_types") or [], "extractor": metadata.get("extractor", getattr(extractor, "name", "manual")) if record.get("abstract") else "none",
            "cache_hit": metadata.get("cache_hit", False), "schema_sha256": getattr(extractor, "schema_sha256", None),
            "prompt_sha256": getattr(extractor, "prompt_sha256", None),
        }
        validator.validate(evidence, record)
        assert_safe(evidence, secrets)
        rows.append(evidence)
        results.append({"uid": record.get("uid"), "doi": record.get("doi"), "status": status,
                        "error_code": error_code, "raw_response_file": raw_file, "cache_hit": metadata.get("cache_hit", False)})
        print(f"Evidence {index}/{len(records)}: {evidence['screening']['status']}", flush=True)
    if hashlib.sha256(source.read_bytes()).hexdigest() != input_hash:
        raise RuntimeError("Canonical input changed during extraction")
    counts = {name: sum(r["screening"]["status"] == name for r in rows) for name in ("include", "maybe", "exclude", "needs_fulltext")}
    manifest = {"schema_version": "0.4", "run_id": run_id, "built_at": utc_now(),
                "canonical_input": display_path(source, root), "input_sha256": input_hash,
                "evidence_file": display_path(target, root), "raw_dir": display_path(raw_dir, root),
                "records": len(rows), "screening_counts": counts,
                "completeness_counts": dict(Counter(r["evidence_completeness"] for r in rows)),
                "extraction_stats": getattr(extractor, "stats", {}), "rejected": sum(r["status"] == "rejected" for r in results),
                "requests": prepared, "results": results, "screening_policy": "No formal eligibility criteria: abstract-bearing records remain maybe unless explicitly reviewed otherwise.",
                "inference_policy": "Separate stage; no automatic inference or research-gap ranking in this run."}
    assert_safe(manifest, secrets)
    write_jsonl(target, rows, secrets)
    write_json(manifest_path, manifest, secrets)
    print(f"Evidence JSONL: {target}")
    print(json.dumps(counts))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--manual-dir")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        manifest = build_matrix(args.input, args.output, manual_dir=args.manual_dir,
                                cache_only=args.cache_only, refresh=args.refresh, prepare_only=args.prepare_only)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Evidence build failed: {redact(exc, known_secrets())}", file=sys.stderr)
        return 1
    return 1 if manifest.get("rejected") else 0


if __name__ == "__main__":
    raise SystemExit(main())
