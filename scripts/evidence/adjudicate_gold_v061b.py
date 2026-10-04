"""Create a deterministic, unapproved v0.6.1b derivative of the v061a candidate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.evidence.contract_reconcile_v061a import (
    DEV_IDS,
    ORIGINAL_GOLD,
    ROOT,
    V1_REPORT,
    V2_REPORT,
    _load_development_requests,
)
from scripts.extractors.schema_validator import EvidenceValidator


SOURCE_DIR = Path("data/evidence_benchmarks/gold_v04_adjudicated_candidate_v061a")
OUTPUT_DIR = Path("data/evidence_benchmarks/gold_v04_adjudicated_candidate_v061b")
SCHEMA = Path("schemas/evidence_matrix.schema.json")
FINDING_INDICES = {
    "WOS:001487641900001": [4, 5],
    "WOS:001602265100001": [7, 8, 9],
    "WOS:001631731300001": [4],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def migrate_legacy_findings(records: list[dict], requests: dict[str, dict], finding_indices=None):
    """Add explicit certainty only to exact, source-verified legacy finding objects."""
    migrated = copy.deepcopy(records)
    changes = []
    finding_indices = FINDING_INDICES if finding_indices is None else finding_indices
    by_uid = {row["uid"]: row for row in migrated}
    if set(by_uid) != set(requests):
        raise ValueError("Candidate identities differ from development request identities")
    if set(finding_indices) - set(by_uid):
        raise ValueError("Expected inherited finding-error record is absent")

    for uid, indices in finding_indices.items():
        row, source = by_uid[uid], requests[uid]
        abstract = source.get("abstract") or ""
        if any(source.get(k) != row.get(k) for k in ("uid", "doi", "title")):
            raise ValueError(f"Candidate identity mismatch for {uid}")
        findings = row.get("evidence", {}).get("findings", [])
        for index in indices:
            finding = findings[index]
            if finding.get("certainty") in {"explicit", "implicit"}:
                continue
            text = finding.get("evidence_text")
            start, end = finding.get("start"), finding.get("end")
            if (finding.get("source") != "abstract" or not text or
                    finding.get("claim") != text or type(start) is not int or
                    type(end) is not int or start < 0 or end <= start or
                    abstract[start:end] != text or text not in abstract):
                raise ValueError(f"Finding is not eligible for deterministic migration: {uid}#{index}")
            before = copy.deepcopy(finding)
            finding["certainty"] = "explicit"
            changes.append({
                "uid": uid,
                "doi": row.get("doi"),
                "finding_index": index,
                "before": before,
                "after": copy.deepcopy(finding),
                "reason": "The verbatim claim equals its exact abstract evidence span; certainty is explicitly stated and no claim or source evidence changes.",
                "repair_type": "LEGACY_EXPLICIT_CERTAINTY_MIGRATION",
            })
    return migrated, changes


def validate_candidate(records: list[dict], requests: dict[str, dict], schema_path: Path):
    schema = _load_json(schema_path)
    schema_validator = Draft202012Validator(schema)
    evidence_validator = EvidenceValidator(schema_path)
    schema_errors = grounding_errors = invalid_offsets = orphan_anchors = identity_errors = 0
    for row in records:
        schema_errors += sum(1 for _ in schema_validator.iter_errors(row))
        source = requests.get(row.get("uid"))
        if not source or any(source.get(k) != row.get(k) for k in ("uid", "doi", "title")):
            identity_errors += 1
        try:
            evidence_validator.validate(row, source or {})
        except ValueError as exc:
            # EvidenceValidator returns one contract error; classify it without
            # exposing abstract or candidate text in validation output.
            message = str(exc).casefold()
            if "offset" in message:
                invalid_offsets += 1
            elif "missing or orphan" in message:
                orphan_anchors += 1
            elif "identifiers differ" in message:
                identity_errors += 1
            else:
                grounding_errors += 1
        for finding in row.get("evidence", {}).get("findings", []):
            if finding.get("source") == "abstract":
                abstract = (source or {}).get("abstract") or ""
                start, end = finding.get("start"), finding.get("end")
                if (type(start) is not int or type(end) is not int or start < 0 or
                        end <= start or abstract[start:end] != finding.get("evidence_text")):
                    invalid_offsets += 1
    return {
        "record_count": len(records),
        "schema_errors": schema_errors,
        "grounding_errors": grounding_errors,
        "invalid_offsets": invalid_offsets,
        "orphan_anchors": orphan_anchors,
        "identity_errors": identity_errors,
    }


def build_candidate(root: Path, source_dir: Path, output_dir: Path,
                    original_gold: Path, strict_v1: Path, metric_v2: Path,
                    schema_path: Path, finding_indices=None):
    source_candidate = source_dir / "candidate_gold.jsonl"
    requests = _load_development_requests(root, set(_load_json(root / DEV_IDS)))
    original_hashes = {str(path): sha256(path) for path in
                       (source_candidate, original_gold, strict_v1, metric_v2)}
    source_records = [json.loads(line) for line in source_candidate.read_text(encoding="utf-8").splitlines() if line]
    records, changes = migrate_legacy_findings(source_records, requests, finding_indices)
    validation = validate_candidate(records, requests, schema_path)
    if any(validation[key] for key in ("schema_errors", "grounding_errors", "invalid_offsets", "orphan_anchors", "identity_errors")):
        raise ValueError(f"v061b validation failed: {validation}")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite an existing derivative: {output_dir}")

    output_dir.mkdir(parents=True)
    (output_dir / "candidate_gold.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in records),
        encoding="utf-8",
    )
    # Carry forward the v061a contract delta as provenance; this migration adds
    # certainty fields only and does not alter the earlier field-contract edits.
    for name in ("candidate_delta.jsonl", "contract_reconciliation.jsonl"):
        path = source_dir / name
        if path.exists():
            shutil.copyfile(path, output_dir / name)
    (output_dir / "change_log.jsonl").write_text(
        "".join(json.dumps(change, ensure_ascii=False, separators=(",", ":")) + "\n" for change in changes),
        encoding="utf-8",
    )
    manifest = {
        "dataset_role": "unapproved_deterministically_repaired_gold_candidate",
        "source_candidate": str(source_dir),
        "source_sha256": original_hashes,
        "development_count": len(records),
        "repair_count": len(changes),
        "repair_type": "LEGACY_EXPLICIT_CERTAINTY_MIGRATION",
        "validation": validation,
        "performance_metrics_recomputed": False,
        "human_approved": False,
        "review_packet": "data/reports/v06_development_gold_human_review_final.md",
        "note": "Derived only from v061a development candidate. Original Gold, v061a, schema, validator, prompt, benchmark reports, and holdout identities are unchanged.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if original_hashes[str(source_candidate)] != sha256(source_candidate):
        raise RuntimeError("Source v061a changed unexpectedly")
    if original_hashes[str(original_gold)] != sha256(original_gold):
        raise RuntimeError("Original Gold changed unexpectedly")
    if original_hashes[str(strict_v1)] != sha256(strict_v1) or original_hashes[str(metric_v2)] != sha256(metric_v2):
        raise RuntimeError("Frozen benchmark report changed unexpectedly")
    return changes, validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--original-gold", type=Path, default=ROOT / ORIGINAL_GOLD)
    parser.add_argument("--strict-v1", type=Path, default=ROOT / V1_REPORT)
    parser.add_argument("--metric-v2", type=Path, default=ROOT / V2_REPORT)
    parser.add_argument("--schema", type=Path, default=ROOT / SCHEMA)
    args = parser.parse_args()
    changes, validation = build_candidate(args.root, args.source_dir, args.output_dir,
                                          args.original_gold, args.strict_v1,
                                          args.metric_v2, args.schema)
    print(json.dumps({"output_dir": str(args.output_dir), "repairs": len(changes), "validation": validation}, ensure_ascii=False))


if __name__ == "__main__":
    main()
