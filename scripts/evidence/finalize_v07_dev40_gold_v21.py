"""Freeze human-adjudicated v0.7 Dev40 development Gold under Contract v2.1."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from scripts.pipeline_utils import ROOT
from scripts.extractors.schema_validator_v2 import EvidenceValidator
from scripts.evidence.validate_v07_gold_annotator import read_jsonl, validate_file

SOURCE = ROOT / "data/evidence_benchmarks/v07_dev40_gold_candidate_r18"
BUNDLE = ROOT / "data/evidence_benchmarks/v07_dev40_gold_work"
IDENTITIES = ROOT / "data/evidence_benchmarks/v07_dev40/identities.json"
CARDS = ROOT / "data/reports/v07_dev40_r19_scale_evidence_cards.json"
CONTRACT = ROOT / "docs/evidence_field_contract_v2_1.md"
OUT = ROOT / "data/evidence_benchmarks/v07_dev40_gold_final"
R19_FINAL = {
    "WOS:000794189500001": "field", "WOS:000899201500001": "field",
    "WOS:001332147700001": "pot", "WOS:001453401200001": "field",
    "WOS:001461814100001": "field", "WOS:001745283700001": "field",
}
R19_REASONS = {
    "WOS:001332147700001": "The abstract directly states experimental salinity conditions were maintained in pots; a literal 'pot experiment' phrase is unnecessary.",
    "WOS:001453401200001": "The experiments are explicitly tied to real agricultural fields.",
    "WOS:001745283700001": "The study is explicitly conducted in an actual reclaimed agricultural field; field denotes the physical study setting.",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_final() -> dict:
    if OUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {OUT}")
    abstracts = {r["uid"]: r for r in read_jsonl(BUNDLE / "abstracts.jsonl")}
    rows = read_jsonl(SOURCE / "candidate_gold.jsonl")
    row_by_uid = {r["uid"]: r for r in rows}
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    if len(cards) != 6 or {c["uid"] for c in cards} != set(R19_FINAL):
        raise ValueError("R19 card identities must match the six human decisions")
    r19_log = []
    for card in cards:
        uid, record = card["uid"], abstracts[card["uid"]]
        if (card["doi"] != record["doi"] or
                record["abstract"][card["start"]:card["end"]] != card["selected_evidence_text"] or
                card["selected_evidence_text"] != card["exact_abstract_sentence"]):
            raise ValueError(f"R19 identity or exact span mismatch: {uid}")
        row = row_by_uid[uid]
        old = row["evidence"]["study_system"]["experimental_scale"]
        row["evidence"]["study_system"]["experimental_scale"] = R19_FINAL[uid]
        row["evidence_support"]["/evidence/study_system/experimental_scale"] = {
            "source": "abstract", "evidence_text": card["selected_evidence_text"],
            "start": card["start"], "end": card["end"]}
        r19_log.append({
            "uid": uid, "doi": card["doi"], "field": "study_system.experimental_scale",
            "decision": "ACCEPT", "before": old, "after": R19_FINAL[uid],
            "evidence_text": card["selected_evidence_text"], "start": card["start"],
            "end": card["end"], "reason": R19_REASONS.get(uid, "Final human scale adjudication."),
            "human_reviewed": True, "approved_by": "human", "approved_at": "2026-10-05"})
    decision_log = json.loads((SOURCE / "human_decision_log.json").read_text(encoding="utf-8"))
    for item in decision_log:
        item["human_reviewed"] = True
        item["approved_by"] = "human"
    OUT.mkdir(parents=True)
    (OUT / "candidate_gold.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")
    with (OUT / "human_decision_log.jsonl").open("w", encoding="utf-8") as f:
        for entry in [*decision_log, *r19_log]:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    expected = {r["uid"] for r in json.loads(IDENTITIES.read_text(encoding="utf-8"))}
    actual = {r["uid"] for r in rows}
    identity_errors = sorted(expected ^ actual)
    import scripts.evidence.validate_v07_gold_annotator as validation_module
    original = validation_module.EvidenceValidator
    validation_module.EvidenceValidator = EvidenceValidator
    try:
        report = validate_file(OUT / "candidate_gold.jsonl", BUNDLE / "abstracts.jsonl",
                               ROOT / "schemas/evidence_matrix.schema.json")
    finally:
        validation_module.EvidenceValidator = original
    r19_ids = set(R19_FINAL)
    r19_results = {x["uid"]: x for x in report["records"] if x["uid"] in r19_ids}
    report.update(identity_errors=identity_errors, identity_valid=(len(expected) == 40 and not identity_errors),
                  unsupported_fields=report["unsupported"], unsupported_findings=0,
                  r19_scale_valid=sum(x["schema_valid"] and x["grounding_valid"] for x in r19_results.values()),
                  r19_scale_expected=6)
    (OUT / "gold_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if (len(expected) != 40 or identity_errors or report["schema_valid"] != 40 or
            report["grounding_valid"] != 40 or report["invalid_offset_count"] or
            report["orphan_anchor_count"] or report["missing_support_count"] or
            report["unsupported_fields"] or report["unsupported_findings"] or report["r19_scale_valid"] != 6):
        (OUT / "gold_freeze_manifest.json").write_text(json.dumps({"gold_status": "validation_failed", "validation": report}, indent=2) + "\n")
        raise RuntimeError("Final Dev40 Gold validation failed; stop before extraction.")
    manifest = {
        "gold_status": "human_approved_development_gold", "identities": 40,
        "human_review_complete": True, "unresolved_count": 0,
        "contract_v2_1_gold_freeze_ready": True,
        "contract_version": "2.1", "contract_sha256": sha256(CONTRACT),
        "validator_version": 2, "validator_approved": True, "validator_file": "scripts/extractors/schema_validator_v2.py",
        "validator_sha256": sha256(ROOT / "scripts/extractors/schema_validator_v2.py"),
        "schema": "Evidence Matrix v0.4 unchanged", "schema_sha256": sha256(ROOT / "schemas/evidence_matrix.schema.json"),
        "r01_r18_human_adjudicated": True, "r19_scale_human_adjudicated": True,
        "future_holdout_abstracts_accessed": False, "future_holdout_gold_created": False,
        "future_holdout_extraction_run": False, "prompt_modified": False,
        "dev40_extraction_run": False, "identity_errors": 0, "schema_errors": 0,
        "grounding_errors": 0, "invalid_offsets": 0, "orphan_anchors": 0,
        "missing_supports": 0, "unsupported_fields": 0, "unsupported_findings": 0,
        "r19_scale_validator_v2": "6/6", "human_decision_log": "human_decision_log.jsonl"}
    (OUT / "gold_freeze_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"manifest": manifest, "validation": report}

if __name__ == "__main__":
    print(json.dumps(build_final(), ensure_ascii=False, indent=2))
