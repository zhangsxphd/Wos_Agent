"""Apply the user's 11 human decisions to v061b and freeze development Gold."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.evidence.contract_reconcile_v061a import DEV_IDS, ORIGINAL_GOLD, ROOT, _load_development_requests
from scripts.extractors.schema_validator import EvidenceValidator


SOURCE_DIR = Path("data/evidence_benchmarks/gold_v04_adjudicated_candidate_v061b")
OUTPUT_DIR = Path("data/evidence_benchmarks/gold_v04_development_final")
SCHEMA_PATH = Path("schemas/evidence_matrix.schema.json")
APPROVED_AT = "2026-10-04T08:27:10Z"

# Values refer to the human-review packet's stable case IDs and the v061b state.
DECISIONS = [
    {"case_id": "F001", "uid": "WOS:001487641900001", "doi": "10.1016/j.agwat.2025.109503",
     "field": "/evidence/study_system/soil_type", "decision": "REJECT",
     "remove": ["soda saline-alkali land"],
     "reason": "Remove the regional land descriptor from soil_type; it does not establish soil classification for the modeled study system."},
    {"case_id": "F002", "uid": "WOS:001487641900001", "doi": "10.1016/j.agwat.2025.109503",
     "field": "/evidence/study_system/salinity_context", "decision": "ACCEPT",
     "keep": ["soda saline-alkali land"],
     "reason": "The abstract explicitly links the saline-alkali setting to the selected western Jilin study area."},
    {"case_id": "F013", "uid": "WOS:001602265100001", "doi": "10.3390/w17202974",
     "field": "/evidence/study_system/soil_type", "decision": "REJECT",
     "remove": ["saline-affected paddy fields", "saline-affected upland fields"],
     "reason": "Paddy and upland are land-use classes, not explicit soil/substrate classifications."},
    {"case_id": "F016", "uid": "WOS:001602265100001", "doi": "10.3390/w17202974",
     "field": "/evidence/study_system/salinity_context", "decision": "ACCEPT",
     "keep": ["saline-affected paddy fields"],
     "reason": "The abstract directly describes the monitored paddy fields as saline-affected."},
    {"case_id": "F017", "uid": "WOS:001602265100001", "doi": "10.3390/w17202974",
     "field": "/evidence/study_system/salinity_context", "decision": "ACCEPT",
     "keep": ["saline-affected upland fields"],
     "reason": "The abstract directly describes the monitored upland fields as saline-affected."},
    {"case_id": "F038", "uid": "WOS:001713822800001", "doi": "10.3390/su18052185",
     "field": "/evidence/study_system/soil_type", "decision": "REJECT",
     "remove": ["saline–alkali paddy fields"],
     "reason": "The abstract names the target application context but does not establish the pot substrate's soil classification."},
    {"case_id": "F039", "uid": "WOS:001713822800001", "doi": "10.3390/su18052185",
     "field": "/evidence/study_system/salinity_context", "decision": "REJECT",
     "remove": ["saline–alkali paddy fields"],
     "reason": "The abstract does not explicitly link the pot substrate/system to saline-alkali paddy soil; leave salinity_context empty."},
    {"case_id": "F049", "uid": "WOS:001798349200001", "doi": "10.1016/j.agwat.2026.110526",
     "field": "/evidence/study_system/soil_type", "decision": "REJECT",
     "remove": ["saline-alkali soils"],
     "reason": "The regional saline-soil mention does not establish soil type for the modeled land-use study system."},
    {"case_id": "F050", "uid": "WOS:001798349200001", "doi": "10.1016/j.agwat.2026.110526",
     "field": "/evidence/study_system/salinity_context", "decision": "ACCEPT",
     "keep": [], "exact": True,
     "reason": "The abstract does not explicitly link the modeled system itself to a salinity context; keep the field empty."},
    {"case_id": "F060", "uid": "WOS:001831944400001", "doi": "10.3390/gels12070592",
     "field": "/evidence/study_system/soil_type", "decision": "REJECT",
     "remove": ["saline–alkali soils"],
     "reason": "Generic Review application context is not an empirical soil system or explicit review-scope classification."},
    {"case_id": "F075", "uid": "WOS:001859510400001", "doi": "10.3390/agriculture16161786",
     "field": "/evidence/measurements/yield", "decision": "REJECT",
     "remove": ["yield components"],
     "reason": "The generic phrase is not harvested output; no individual yield-component variables are explicitly named for reclassification."},
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get_pointer(root, pointer):
    value = root
    for segment in pointer.strip("/").split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    return value


def _set_pointer(root, pointer, new_value):
    segments = pointer.strip("/").split("/")
    parent = root
    for segment in segments[:-1]:
        segment = segment.replace("~1", "/").replace("~0", "~")
        parent = parent[int(segment)] if isinstance(parent, list) else parent[segment]
    key = segments[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(parent, list):
        parent[int(key)] = new_value
    else:
        parent[key] = new_value


def _apply_decision(row, decision):
    pointer = decision["field"]
    before = copy.deepcopy(_get_pointer(row, pointer))
    if "keep" in decision:
        expected = decision["keep"]
        if not isinstance(before, list) or any(value not in before for value in expected) or (decision.get("exact") and before != expected):
            raise ValueError(f"ACCEPT precondition mismatch for {decision['case_id']}")
        after = copy.deepcopy(before)
    else:
        if not isinstance(before, list):
            raise ValueError(f"Expected list field for {decision['case_id']}")
        removals = decision["remove"]
        if any(before.count(value) != 1 for value in removals):
            raise ValueError(f"REJECT precondition mismatch for {decision['case_id']}")
        after = [value for value in before if value not in removals]
        _set_pointer(row, pointer, after)
        support = row.get("evidence_support", {})
        prefix = pointer + "/"
        indexed_support = {}
        for key, item in list(support.items()):
            if key.startswith(prefix):
                suffix = key[len(prefix):]
                if suffix.isdigit():
                    indexed_support[int(suffix)] = item
                    del support[key]
        for new_index, value in enumerate(after):
            old_index = next(i for i, old in enumerate(before) if old == value and i not in {j for j, v in enumerate(before) if v in removals})
            if old_index in indexed_support:
                support[f"{pointer}/{new_index}"] = indexed_support[old_index]
    return before, after


def validate_final(records: list[dict], requests: dict[str, dict], schema_path: Path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_validator = Draft202012Validator(schema)
    evidence_validator = EvidenceValidator(schema_path)
    errors = {"schema_errors": 0, "grounding_errors": 0, "invalid_offsets": 0,
              "orphan_anchors": 0, "identity_errors": 0}
    for row in records:
        errors["schema_errors"] += sum(1 for _ in schema_validator.iter_errors(row))
        source = requests.get(row.get("uid"))
        if source is None or any(source.get(k) != row.get(k) for k in ("uid", "doi", "title")):
            errors["identity_errors"] += 1
        try:
            evidence_validator.validate(row, source or {})
        except ValueError as exc:
            message = str(exc).casefold()
            if "offset" in message:
                errors["invalid_offsets"] += 1
            elif "missing or orphan" in message:
                errors["orphan_anchors"] += 1
            elif "identifiers differ" in message:
                errors["identity_errors"] += 1
            elif "conform to the schema" not in message:
                errors["grounding_errors"] += 1
    return {"record_count": len(records), **errors}


def finalize(root: Path, source_dir: Path, output_dir: Path, original_gold: Path,
             schema_path: Path, approved_at=APPROVED_AT):
    source_path = source_dir / "candidate_gold.jsonl"
    requests = _load_development_requests(root, set(json.loads((root / DEV_IDS).read_text(encoding="utf-8"))))
    frozen_paths = [source_path, original_gold]
    frozen_hashes = {str(path): sha256(path) for path in frozen_paths}
    rows = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line]
    by_uid = {row["uid"]: row for row in rows}
    if len(by_uid) != len(rows) or set(by_uid) != set(requests):
        raise ValueError("v061b candidate identities differ from the exact seven-paper development set")

    log = []
    for decision in DECISIONS:
        row = by_uid.get(decision["uid"])
        if row is None or row.get("doi") != decision["doi"]:
            raise ValueError(f"Human decision identity mismatch for {decision['case_id']}")
        before, after = _apply_decision(row, decision)
        log.append({
            "case_id": decision["case_id"], "uid": decision["uid"], "doi": decision["doi"],
            "field": decision["field"], "human_reviewed": True,
            "human_decision": decision["decision"], "decision": decision["decision"],
            "before": before, "after": after, "reason": decision["reason"],
            "approved_by": "human", "approved_at": approved_at,
        })
    if len(log) != 11 or {item["case_id"] for item in log} != {f"F{i:03}" for i in (1, 2, 13, 16, 17, 38, 39, 49, 50, 60, 75)}:
        raise ValueError("Expected all eleven human decisions exactly once")
    validation = validate_final(rows, requests, schema_path)
    if any(validation[key] for key in ("schema_errors", "grounding_errors", "invalid_offsets", "orphan_anchors", "identity_errors")):
        raise ValueError(f"Final development Gold validation failed: {validation}")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite final development Gold: {output_dir}")

    output_dir.mkdir(parents=True)
    (output_dir / "candidate_gold.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    (output_dir / "human_decision_log.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in log),
        encoding="utf-8",
    )
    manifest = {
        "dataset_role": "human_adjudicated_development_gold",
        "source_candidate": str(source_dir), "record_count": len(rows),
        "human_decision_count": len(log), "human_reviewed": True,
        "approved_by": "human", "approved_at": approved_at,
        "validation": validation, "performance_metrics_recomputed": False,
        "independent_holdout": False,
        "source_sha256": frozen_hashes,
        "note": "Seven-paper development set only; the frozen 20-paper holdout was not accessed or modified.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if any(sha256(Path(path)) != digest for path, digest in frozen_hashes.items()):
        raise RuntimeError("A frozen source changed during finalization")
    return validation, log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--original-gold", type=Path, default=ROOT / ORIGINAL_GOLD)
    parser.add_argument("--schema", type=Path, default=ROOT / SCHEMA_PATH)
    args = parser.parse_args()
    validation, log = finalize(args.root, args.source_dir, args.output_dir,
                               args.original_gold, args.schema)
    print(json.dumps({"output_dir": str(args.output_dir), "decisions": len(log),
                      "validation": validation}, ensure_ascii=False))


if __name__ == "__main__":
    main()
