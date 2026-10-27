"""Portable v0.7 frozen-component runs, validation and reviewed evaluation.

Development runs are diagnostics only. New holdouts require a complete, hash-
bound owner review before requests can be prepared. No model is invoked here.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.evidence.metrics import calculate_benchmark
from scripts.evidence.metrics_v2 import corpus_metrics_v2
from scripts.extractors.base import build_payload, payload_key
from scripts.extractors.schema_validator_v2 import EvidenceValidator
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, read_jsonl, redact, utc_now, write_json, write_jsonl


FROZEN = {
    "prompts/evidence_extraction_v07_final.md": "b94af358467e31e6fc30e6216c12201383e47ce4f6440881da9c9f5f0d9b7ea2",
    "docs/evidence_field_contract_v2_1.md": "75fc4b301b598250d159de99c916959999297ecaf512a23a914f74991ae1ffad",
    "scripts/extractors/schema_validator_v2.py": "cfb9877271f948541c2a7fd2596d1aac0c347402106a99cc435f6c24fc041680",
    "schemas/evidence_matrix.schema.json": "33e289f1b2911024f00748ac2580ab7d2926369e7f4ac2ef48b28da8573dae36",
    "scripts/evidence/metrics.py": "4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a",
    "scripts/evidence/metrics_v2.py": "54262802b4970429c1a218272145e229f44b9553f0dc5ba7cfceb070dfad4720",
}
PROMPT = "prompts/evidence_extraction_v07_final.md"
SCHEMA = "schemas/evidence_matrix.schema.json"
BOUND_KEYS = ("payload_sha256", "request_sha256", "prompt_sha256", "schema_sha256", "canonical_input_sha256")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_frozen(root=ROOT):
    actual = {path: file_hash(Path(root) / path) for path in FROZEN}
    if actual != FROZEN:
        raise ValueError("frozen_component_hash_mismatch")
    return actual


def index_records(rows):
    result, dois = {}, set()
    if not rows:
        raise ValueError("empty_dataset")
    for row in rows:
        uid = row.get("uid")
        if not isinstance(uid, str) or not uid.strip() or uid in result:
            raise ValueError("missing_or_duplicate_uid")
        doi = normalize_doi(row.get("doi"))
        if doi and doi in dois:
            raise ValueError("duplicate_doi")
        if doi:
            dois.add(doi)
        if not isinstance(row.get("abstract"), str) or not row["abstract"].strip():
            raise ValueError("abstract_required")
        result[uid] = row
    return result


def validate_reference(records, references):
    by_uid = index_records(records)
    by_ref = {r.get("uid"): r for r in references}
    if len(by_ref) != len(references) or set(by_ref) != set(by_uid):
        raise ValueError("reference_identity_coverage_mismatch")
    validator = EvidenceValidator()
    for uid, record in by_uid.items():
        validator.validate(by_ref[uid], record)
        if by_ref[uid]["screening"]["status"] != "maybe" or any(by_ref[uid]["inference"].values()):
            raise ValueError("reference_response_contract_invalid")
    return by_ref


def review_packet(canonical_file, reference_file, output, root=ROOT):
    """Create an unapproved review packet; never manufacture owner decisions."""
    records, references = read_jsonl(canonical_file), read_jsonl(reference_file)
    by_ref = validate_reference(records, references)
    secrets = known_secrets(root)
    target = Path(output)
    if target.exists():
        raise FileExistsError("review_directory_exists")
    decisions, lines = [], ["# 恢复数据证据审阅包", "", "状态：待人工逐篇审核。AI 辅助开发参考；未批准。", ""]
    for n, record in enumerate(records, 1):
        ref = by_ref[record["uid"]]
        decisions.append({"uid": record["uid"], "canonical_record_sha256": digest(record),
                          "reference_record_sha256": digest(ref), "decision": "REVIEW_REQUIRED",
                          "reviewer": None, "reviewed_at": None})
        lines += [f"## Paper {n:02d} — {record.get('title')}", "", f"UID: `{record['uid']}`", "",
                  "原始摘要：", "", record["abstract"], "", "候选证据（含原文与位置）：", "", "```json",
                  json.dumps(ref, ensure_ascii=False, indent=2), "```", "",
                  "审核：REVIEW_REQUIRED。请在 decisions.template.jsonl 中逐篇记录真实决定、审核人和带时区时间。", ""]
    assert_safe([records, references, decisions, lines], secrets)
    target.mkdir(parents=True)
    write_jsonl(target / "decisions.template.jsonl", decisions, secrets)
    (target / "review.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(target / "manifest.json", {"records": len(records), "human_review_complete": False,
               "canonical_sha256": file_hash(canonical_file), "reference_sha256": file_hash(reference_file),
               "created_at": utc_now()}, secrets)
    return {"records": len(records), "human_review_complete": False}


def verify_review(records, references, log_file):
    by_ref = validate_reference(records, references)
    logs = read_jsonl(log_file)
    by_log = {row.get("uid"): row for row in logs}
    if len(by_log) != len(logs) or set(by_log) != {row["uid"] for row in records}:
        raise ValueError("owner_review_coverage_mismatch")
    for row in records:
        entry = by_log[row["uid"]]
        if (entry.get("decision") != "ACCEPT" or not isinstance(entry.get("reviewer"), str)
                or not entry["reviewer"].strip() or entry.get("canonical_record_sha256") != digest(row)
                or entry.get("reference_record_sha256") != digest(by_ref[row["uid"]])):
            raise ValueError("owner_review_required_or_stale")
        stamp = datetime.fromisoformat(entry.get("reviewed_at") or "")
        if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
            raise ValueError("owner_review_timestamp_invalid")
    return True


def check_new_holdout(records, root=ROOT):
    if len(records) != 30:
        raise ValueError("new_holdout_requires_30_records")
    known = read_jsonl(Path(root) / "data/evidence_benchmarks/v07_dev40_gold_work/abstracts.jsonl")
    old_uids = {r["uid"] for r in known}
    old_dois = {normalize_doi(r.get("doi")) for r in known} - {None}
    if any(r["uid"] in old_uids or normalize_doi(r.get("doi")) in old_dois for r in records):
        raise ValueError("holdout_overlaps_known_development")


def prepare_run(canonical_file, output, role="development", reference_file=None, review_log=None, root=ROOT):
    frozen = verify_frozen(root)
    if role not in {"development", "new_holdout"}:
        raise ValueError("unknown_run_role")
    records = read_jsonl(canonical_file)
    index_records(records)
    secrets = known_secrets(root)
    assert_safe(records, secrets)
    references = read_jsonl(reference_file) if reference_file else None
    if references is not None:
        validate_reference(records, references)
        assert_safe(references, secrets)
    if role == "new_holdout":
        check_new_holdout(records, root)
        if references is None or review_log is None:
            raise ValueError("reviewed_reference_required_before_holdout_preparation")
        verify_review(records, references, review_log)
    target = Path(output).resolve()
    if target.exists():
        raise FileExistsError("run_directory_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a complete prepared bundle; failed validation leaves no run.
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=target.parent) as tmp:
        staging = Path(tmp) / "run"
        staging.mkdir()
        write_jsonl(staging / "canonical.jsonl", records, secrets)
        canonical_sha = file_hash(staging / "canonical.jsonl")
        worker = staging / "worker"
        (worker / "requests").mkdir(parents=True)
        (worker / "responses").mkdir()
        shutil.copyfile(Path(root) / PROMPT, worker / "prompt.md")
        shutil.copyfile(Path(root) / SCHEMA, worker / "schema.json")
        request_items = []
        for record in records:
            key = payload_key(record)
            request = {**build_payload(record), "payload_sha256": key, "prompt_sha256": frozen[PROMPT],
                       "schema_sha256": frozen[SCHEMA], "canonical_input_sha256": canonical_sha}
            request["request_sha256"] = digest(request)
            name = key + ".json"
            write_json(worker / "requests" / name, request, secrets)
            request_items.append({"uid": record["uid"], "request_file": "requests/" + name,
                                  "response_file": "responses/" + name, "request_sha256": request["request_sha256"]})
        write_json(worker / "manifest.json", {"protocol": "0.7-rebuild", "requests": request_items,
                   "records": len(records), "contains_reference": False}, secrets)
        (worker / "TASK.md").write_text(
            "# Evidence extraction\n\nRead only this directory. Follow prompt.md and schema.json. Process each request independently. "
            "Write one response envelope to its response_file in manifest.json. Copy payload_sha256, request_sha256, "
            "prompt_sha256, schema_sha256 and canonical_input_sha256 from the request; set model_label=null unless known; "
            "put the Evidence Matrix object in response. Keep screening.status=maybe and every inference array empty. "
            "Use exact abstract spans and offsets. Findings and author interpretations support themselves; "
            "do not add their pointers to evidence_support. No other files, prior outputs, reference answers or network sources.\n",
            encoding="utf-8")
        manifest = {"protocol": "0.7-rebuild", "role": role, "records": len(records), "prepared_at": utc_now(),
                    "historical_experiment_restored": False, "canonical_sha256": canonical_sha,
                    "frozen_components": frozen, "worker_manifest_sha256": file_hash(worker / "manifest.json"),
                    "worker_task_sha256": file_hash(worker / "TASK.md"),
                    "reference_sha256": file_hash(reference_file) if reference_file else None,
                    "review_log_sha256": file_hash(review_log) if role == "new_holdout" else None,
                    "owner_review_complete": role == "new_holdout",
                    "historical_holdout_overlap": "unknown_original_registry_lost" if role == "new_holdout" else "not_applicable",
                    "target_extraction_run": False}
        write_json(staging / "manifest.json", manifest, secrets)
        staging.rename(target)
    return manifest


def validate_run(run_dir, root=ROOT):
    frozen = verify_frozen(root)
    run = Path(run_dir)
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest.get("protocol") != "0.7-rebuild" or manifest.get("frozen_components") != frozen:
        raise ValueError("run_freeze_mismatch")
    if manifest.get("role") not in {"development", "new_holdout"}:
        raise ValueError("unknown_run_role")
    if manifest["role"] == "new_holdout" and manifest.get("owner_review_complete") is not True:
        raise ValueError("owner_review_required")
    if file_hash(run / "canonical.jsonl") != manifest["canonical_sha256"]:
        raise ValueError("canonical_hash_mismatch")
    worker = run / "worker"
    for name, key in [("manifest.json", "worker_manifest_sha256"), ("TASK.md", "worker_task_sha256")]:
        if file_hash(worker / name) != manifest[key]:
            raise ValueError("worker_control_hash_mismatch")
    if file_hash(worker / "prompt.md") != frozen[PROMPT] or file_hash(worker / "schema.json") != frozen[SCHEMA]:
        raise ValueError("worker_component_hash_mismatch")
    records = index_records(read_jsonl(run / "canonical.jsonl"))
    bundle = json.loads((worker / "manifest.json").read_text())
    if len(records) != manifest["records"] or len(bundle["requests"]) != len(records):
        raise ValueError("request_coverage_mismatch")
    validator, predictions, results, seen = EvidenceValidator(), {}, {}, set()
    expected_files = set()
    for item in bundle["requests"]:
        uid = item["uid"]
        if uid not in records or uid in seen:
            raise ValueError("request_identity_mismatch")
        seen.add(uid)
        record = records[uid]
        key = payload_key(record)
        if item["request_file"] != f"requests/{key}.json" or item["response_file"] != f"responses/{key}.json":
            raise ValueError("worker_path_contract_mismatch")
        request = json.loads((worker / item["request_file"]).read_text())
        supplied_hash = request.pop("request_sha256", None)
        expected = {**build_payload(record), "payload_sha256": key, "prompt_sha256": frozen[PROMPT],
                    "schema_sha256": frozen[SCHEMA], "canonical_input_sha256": manifest["canonical_sha256"]}
        if request != expected or supplied_hash != digest(expected) or supplied_hash != item["request_sha256"]:
            raise ValueError("request_hash_or_payload_mismatch")
        request["request_sha256"] = supplied_hash
        path = worker / item["response_file"]
        expected_files.add(path.name)
        results[uid] = {"status": "missing", "response_sha256": None}
        if not path.is_file():
            continue
        try:
            envelope = json.loads(path.read_text())
            assert_safe(envelope, known_secrets(root))
            if any(envelope.get(k) != request[k] for k in BOUND_KEYS):
                raise ValueError("response_envelope_mismatch")
            row = envelope.get("response")
            validator.validate(row, record)
            if row["screening"]["status"] != "maybe" or any(row["inference"].values()):
                raise ValueError("response_contract_invalid")
            predictions[uid] = row
            results[uid] = {"status": "accepted", "response_sha256": file_hash(path)}
        except (ValueError, TypeError, KeyError, AttributeError):
            results[uid] = {"status": "rejected", "response_sha256": file_hash(path)}
    unexpected = sorted(p.name for p in (worker / "responses").iterdir() if p.name not in expected_files)
    summary = {"records": len(records), "accepted": len(predictions),
               "missing": sum(r["status"] == "missing" for r in results.values()),
               "rejected": sum(r["status"] == "rejected" for r in results.values()),
               "unexpected_response_files": unexpected, "results": results}
    summary["complete_valid"] = len(predictions) == len(records) and not unexpected
    return manifest, records, predictions, summary


def evaluate_run(run_dir, reference_file, review_log=None, root=ROOT):
    manifest, records, predictions, validation = validate_run(run_dir, root)
    references = read_jsonl(reference_file)
    gold = validate_reference(list(records.values()), references)
    if file_hash(reference_file) != manifest.get("reference_sha256"):
        raise ValueError("reference_not_frozen_before_run")
    if manifest["role"] == "new_holdout":
        if review_log is None or file_hash(review_log) != manifest.get("review_log_sha256"):
            raise ValueError("review_log_hash_mismatch")
        verify_review(list(records.values()), references, review_log)
        check_new_holdout(list(records.values()), root)
    report = {"role": manifest["role"], "historical_experiment_restored": False, "validation": validation,
              "evaluated_at": utc_now(), "metrics": None, "reference_sha256": file_hash(reference_file),
              "response_hashes": {uid: result["response_sha256"] for uid, result in validation["results"].items()}}
    if not validation["complete_valid"]:
        report["quality_gate"] = "FAIL" if manifest["role"] == "new_holdout" else "NOT_EVALUATED_INCOMPLETE"
        return report
    flags = {uid: {"schema_valid": True, "grounding_valid": True, "identifier_match": True,
                   "response_contract_valid": True, "accepted": True} for uid in records}
    v1 = calculate_benchmark(gold, predictions, flags)
    v2 = corpus_metrics_v2([(uid, gold[uid], predictions[uid], r["abstract"]) for uid, r in records.items()])
    report["metrics"] = {"v1": v1, "v2": v2}
    findings = v1["finding_metrics"]
    checks = {"structure_and_grounding": validation["complete_valid"], "field_v2_f1": v2["fields"]["f1"] >= 0.80,
              "findings_precision": findings["precision"] >= 0.95, "findings_recall": findings["recall"] >= 0.85}
    report["gate_checks"] = checks
    report["quality_gate"] = ("PASS" if all(checks.values()) else "FAIL") if manifest["role"] == "new_holdout" else "NOT_APPLICABLE_DEVELOPMENT"
    report["independent_performance_claim"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("review"); p.add_argument("input"); p.add_argument("--reference", required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("prepare"); p.add_argument("input"); p.add_argument("--output", required=True); p.add_argument("--role", choices=("development", "new_holdout"), default="development"); p.add_argument("--reference"); p.add_argument("--review-log")
    p = sub.add_parser("validate"); p.add_argument("run_dir"); p.add_argument("--output")
    p = sub.add_parser("evaluate"); p.add_argument("run_dir"); p.add_argument("--reference", required=True); p.add_argument("--review-log"); p.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "review":
            result = review_packet(args.input, args.reference, args.output)
        elif args.command == "prepare":
            result = prepare_run(args.input, args.output, args.role, args.reference, args.review_log)
        elif args.command == "validate":
            result = validate_run(args.run_dir)[3]
            if args.output: write_json(args.output, result, known_secrets())
        else:
            result = evaluate_run(args.run_dir, args.reference, args.review_log)
            write_json(args.output, result, known_secrets())
        print(json.dumps({k: v for k, v in result.items() if k not in ("results", "metrics", "response_hashes")}, ensure_ascii=False, indent=2))
        if args.command == "validate" and not result["complete_valid"]: return 2
        if args.command == "evaluate" and result["quality_gate"] not in {"PASS", "NOT_APPLICABLE_DEVELOPMENT"}: return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("Run failed: " + redact(exc, known_secrets()), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
