"""v0.6.1a contract-first reconciliation; never changes official Gold or metrics."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from scripts.pipeline_utils import read_jsonl, write_json, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
DEV_IDS = Path("data/evidence_benchmarks/gold_v04/identities.json")
DEV_REQUEST_MANIFEST = Path("data/evidence_batches/v06_gold_blind_iteration1_20261004/batch_001/batch_manifest.json")
V1_CANDIDATE = Path("data/evidence_benchmarks/gold_v04_adjudicated_candidate")
CHANGE_LOG = V1_CANDIDATE / "change_log.jsonl"
OLD_REVIEW = Path("data/reports/v06_gold_candidate_human_review.md")
OUT_DIR = Path("data/evidence_benchmarks/gold_v04_adjudicated_candidate_v061a")
RECON_REPORT = Path("data/reports/v06_1a_contract_gold_reconciliation_20261004.json")
REVIEW_V2 = Path("data/reports/v06_gold_candidate_human_review_v2.md")
ORIGINAL_GOLD = Path("data/evidence/20261003_192326_169425_evidence_57015bbc_evidence.jsonl")
V1_REPORT = Path("data/reports/v06_gold_benchmark_20261004_123933.json")
V2_REPORT = Path("data/reports/v06_1_evaluation_calibration_20261004.json")


SCALE_PATTERNS = {
    "field": re.compile(r"\bfield(?:[- ]based)?(?:\s+[a-z-]+){0,4}\s+(?:study|experiment|monitoring|trial|survey)s?\b", re.I),
    "pot": re.compile(r"\bpot[- ](?:based[- ])?(?:experiment|study)s?\b", re.I),
    "greenhouse": re.compile(r"\bgreenhouse\s+(?:based\s+)?(?:experiment|study)s?\b", re.I),
    "lab": re.compile(r"\b(?:laboratory|lab|incubation)\s+(?:based\s+)?(?:experiment|study)s?\b", re.I),
}
EXPLICIT_MODEL_STUDY = re.compile(
    r"\b(?:model(?:l)?ing|simulation|model[- ]based)\s+(?:study|research|investigation|analysis)\b", re.I
)
MODEL_SCENARIO_DESIGN = re.compile(
    r"\b(?:model|models|modeling|modelling|simulation)\b.{0,180}\bscenarios?\s+(?:were\s+)?(?:designed|constructed|simulated|evaluated|quantified)\b",
    re.I | re.S,
)
METHOD_PATTERNS = (
    re.compile(r"\b(?:binary\s+)?logistic regression(?:[- ]based spatial analysis| models?)?\b", re.I),
    re.compile(r"\bHYDRUS(?:-\d+[A-Z]?(?:\.\d+)?)?(?:\s+model)?\b", re.I),
    re.compile(r"\bPLUS\s+model\b", re.I),
    re.compile(r"\bInVEST\s+model\b", re.I),
)


def resolve_scale_and_methods(abstract: str) -> dict:
    """Apply the v1 scale contract; named models are methods unless study design is model-based."""
    text = abstract or ""
    spans = []
    for scale, pattern in SCALE_PATTERNS.items():
        spans.extend((match.start(), scale) for match in pattern.finditer(text))
    physical = sorted({scale for _, scale in spans})
    methods = []
    for pattern in METHOD_PATTERNS:
        methods.extend(match.group(0) for match in pattern.finditer(text))
    model_design = bool(EXPLICIT_MODEL_STUDY.search(text) or MODEL_SCENARIO_DESIGN.search(text))

    if len(physical) > 1:
        scale, flags = "unknown", ["multi_scale_ambiguity"]
    elif physical:
        # An explicit physical system takes precedence; computational models remain methods.
        scale, flags = physical[0], []
    elif model_design:
        scale, flags = "model", []
    else:
        scale, flags = "unknown", []
    return {"experimental_scale": scale, "methods": methods, "flags": flags,
            "explicit_physical_scales": physical, "explicit_model_study_design": model_design}


def downstream_provenance(document_types, field_pointer: str) -> dict:
    """A graph projection policy; this does not add fields to the v0.4 schema."""
    is_review = any("review" in str(item).casefold() for item in (document_types or []))
    is_system = field_pointer.startswith("/evidence/study_system/")
    is_treatment = field_pointer.startswith("/evidence/treatments/")
    return {
        "paper_role": "review_scope" if is_review else "empirical_study",
        "system_value_role": "scope_descriptor" if is_review and is_system else ("empirical_system" if is_system else None),
        "empirical_system_observation": False if is_review and is_system else (True if is_system else None),
        "treatment_attribution": "review_derived" if is_review and is_treatment else ("study_treatment" if is_treatment else None),
        "author_operated_treatment": False if is_review and is_treatment else (True if is_treatment else None),
    }


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_development_requests(root, expected_uids):
    """Load only the seven existing blind-development payload files, never the shared corpus."""
    manifest = _read_json(Path(root) / DEV_REQUEST_MANIFEST)
    requests = manifest.get("requests") or []
    if len(requests) != len(expected_uids) or {r.get("uid") for r in requests} != set(expected_uids):
        raise ValueError("The iteration-1 request manifest does not match the seven development identities")
    records = {}
    for item in requests:
        payload = _read_json(Path(root) / item["request_file"])
        if payload.get("uid") != item["uid"]:
            raise ValueError("Development request identity mismatch")
        records[payload["uid"]] = payload
    return records


def _pointer_value(record, pointer):
    value = record
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value.get(part) if isinstance(value, dict) else None
    return value


def _candidate_field_values(record, pointer):
    value = _pointer_value(record.get("evidence", {}), pointer.removeprefix("/evidence"))
    if pointer.endswith("/experimental_scale"):
        return [] if value in (None, "unknown") else [value]
    return value if isinstance(value, list) else []


def _remove_value(record, pointer, value):
    values = _candidate_field_values(record, pointer)
    if values.count(value) != 1:
        raise ValueError(f"Expected exactly one candidate value for {pointer}: {value}")
    supports = record.get("evidence_support", {})
    retained, retained_supports = [], []
    for i, item in enumerate(values):
        if item == value:
            continue
        retained.append(item)
        retained_supports.append(supports.get(f"{pointer}/{i}"))
    target = _pointer_value(record.setdefault("evidence", {}), pointer.removeprefix("/evidence"))
    target[:] = retained
    for key in list(supports):
        if key.startswith(pointer + "/"):
            del supports[key]
    for i, support in enumerate(retained_supports):
        if support:
            supports[f"{pointer}/{i}"] = support
    return {"uid": record["uid"], "field": pointer, "operation": "REMOVE",
            "old_value": value, "new_value": None}


HUMAN_REVIEW_CASES = ["F001", "F002", "F013", "F016", "F017", "F038", "F039",
                      "F049", "F050", "F060", "F075"]

SCALE_FN_CLASSES = {
    "F003": ["TRUE_SCALE_FN"],
    "F018": ["TRUE_SCALE_FN"],
    "F023": ["TRUE_SCALE_FN"],
    "F040": ["TRUE_SCALE_FN"],
    "F051": ["METHOD_NOT_SCALE", "GOLD_SCALE_INCONSISTENCY"],
    "F068": ["TRUE_SCALE_FN"],
}

# Only previously reviewed/flagged changes in the requested contract areas are classified.
CASE_STATUS = {
    "F001": ("NEEDS_HUMAN_REVIEW", "The selected regional land resource may be a soil-system descriptor or background context; the abstract does not settle that boundary."),
    "F002": ("CONTRACT_CONSISTENT", "Article study-region salinity descriptor is explicitly linked to the selected WJP system."),
    "F003": ("CONTRACT_CONSISTENT", "Coupled regional models plus designed scenarios define the overall model-based study; no physical scale is stated."),
    "F004": ("CONTRACT_CONSISTENT", "Traditional irrigation mode is a model scenario, not an applied irrigation treatment."),
    "F005": ("CONTRACT_CONSISTENT", "Normal/dry years are scenario conditions, not water-regime treatments."),
    "F006": ("CONTRACT_CONSISTENT", "A development scenario is not an applied treatment arm."),
    "F007": ("CONTRACT_CONFLICT", "Eight modeled scenarios are not applied treatment arms; deterministic exclusion resolves the A/B uncertainty."),
    "F008": ("CONTRACT_CONSISTENT", "A land-improvement scenario is not an applied treatment arm."),
    "F011": ("CONTRACT_CONFLICT", "Scenario count is design metadata, not a named method or analysis technique."),
    "F012": ("CONTRACT_CONFLICT", "Normal/dry-year scenario conditions are not methods."),
    "F013": ("NEEDS_HUMAN_REVIEW", "Paddy/upland labels describe land-use systems; whether they are soil classifications in this record remains unresolved."),
    "F016": ("CONTRACT_CONSISTENT", "The monitored saline-affected farmland is the explicit empirical system."),
    "F017": ("CONTRACT_CONSISTENT", "The monitored saline-affected farmland is the explicit empirical system."),
    "F018": ("CONTRACT_CONSISTENT", "Field-based in-situ monitoring explicitly establishes field scale."),
    "F020": ("CONTRACT_CONSISTENT", "HYDRUS-3D is an explicit computational method."),
    "F021": ("CONTRACT_CONSISTENT", "A date range is not a method."),
    "F022": ("CONTRACT_CONSISTENT", "HYDRUS-3D is an explicit computational method."),
    "F023": ("CONTRACT_CONSISTENT", "The abstract explicitly says field study."),
    "F028": ("CONTRACT_CONFLICT", "Control treatment is design metadata, not an applied treatment candidate."),
    "F029": ("CONTRACT_CONSISTENT", "The dose belongs with the amendment it qualifies; the prior candidate move is contract-aligned."),
    "F037": ("CONTRACT_CONFLICT", "Field study is scale/design evidence, not a method under the contract."),
    "F038": ("NEEDS_HUMAN_REVIEW", "The pot-study target names saline-alkali paddy fields; confirm whether this describes the pot substrate or only the intended application."),
    "F039": ("CONTRACT_CONSISTENT", "The pot study is explicitly framed in saline-alkali paddy fields; the candidate omission still needs human approval."),
    "F040": ("CONTRACT_CONSISTENT", "Pot experiment explicitly establishes pot scale."),
    "F041": ("CONTRACT_CONSISTENT", "Flooded/shallow-wet are water-state regimes, not irrigation delivery methods."),
    "F047": ("CONTRACT_CONFLICT", "Pot experiment is scale evidence, not a method item."),
    "F048": ("CONTRACT_CONFLICT", "Pot experiment is scale evidence, not a method item."),
    "F049": ("NEEDS_HUMAN_REVIEW", "Regional saline-soil mention in a land-use model may be study scope or contextual background."),
    "F050": ("NEEDS_HUMAN_REVIEW", "Regional model scope versus an actual saline system needs an explicit system-boundary judgment."),
    "F051": ("CONTRACT_CONFLICT", "Logistic-regression models establish a method only; the abstract does not state an overall model/simulation study design."),
    "F058": ("CONTRACT_CONFLICT", "Climate records are data inputs, not a named method."),
    "F059": ("CONTRACT_CONFLICT", "Land-use data are data inputs, not a named method."),
    "F060": ("NEEDS_HUMAN_REVIEW", "Review paper soil values may be scope descriptors, but this abstract does not clearly distinguish scope from application discussion."),
    "F061": ("CONTRACT_CONFLICT", "The Review discusses saline-stress mechanisms for hydrogel application; it is not an explicit review-scope system."),
    "F062": ("CONTRACT_CONFLICT", "The Review discusses application soils, not an explicit review-scope soil system."),
    "F063": ("CONTRACT_CONFLICT", "Osmotic stress/ion toxicity are mechanisms, not a salinity setting of the Review scope."),
    "F068": ("CONTRACT_CONSISTENT", "Field soil-column experiment explicitly establishes field scale."),
    "F069": ("CONTRACT_CONSISTENT", "Straw removal is an applied factor that belongs in other_treatments, not amendments."),
    "F070": ("CONTRACT_CONSISTENT", "Candidate already places straw removal in other_treatments."),
    "F075": ("CONTRACT_CONSISTENT", "The candidate yield item fits the explicit harvested-output field; whether to supplement Gold remains a human decision."),
}


def _reconciliation_rows(change_rows):
    result = []
    for row in change_rows:
        case_id = row.get("case_id")
        if case_id not in CASE_STATUS:
            continue
        status, reason = CASE_STATUS[case_id]
        result.append({"case_id": case_id, "uid": row["uid"], "doi": row.get("doi"),
                       "field": row.get("field"), "status": status,
                       "prior_candidate_action": row.get("proposed_action"), "reason": reason,
                       "A_decision": (row.get("reviewer_A") or {}).get("category"),
                       "B_decision": (row.get("reviewer_B") or {}).get("category")})
    return result


def _review_packet(rows, candidate, canonical):
    by_case = {row["case_id"]: row for row in rows}
    blocks = ["# v0.6 Gold candidate — contract-aligned human review v2", "",
              "This packet contains only decisions the frozen Evidence Field Contract does not settle. Candidate answers remain unapproved. Reply `ACCEPT`, `REJECT`, or `MODIFY` for each item.", "",
              f"Items requiring review: **{len(HUMAN_REVIEW_CASES)}**.", ""]
    for n, case_id in enumerate(HUMAN_REVIEW_CASES, 1):
        row = by_case[case_id]
        pointer = row["field"]
        source = canonical[row["uid"]]
        record = candidate[row["uid"]]
        abstract = source.get("abstract") or ""
        context = row.get("abstract_context") or []
        if not context:
            support = row.get("gold") or row.get("prediction") or {}
            anchor = support.get("anchor") or {}
            context = [anchor.get("evidence_text") or abstract]
        candidate_values = _candidate_field_values(record, pointer)
        if pointer.endswith("/experimental_scale"):
            candidate_values = _pointer_value(record.get("evidence", {}), pointer.removeprefix("/evidence"))
        status = CASE_STATUS.get(case_id, ("NEEDS_HUMAN_REVIEW", "Contract boundary remains unresolved."))[0]
        rule = _rule_for(pointer)
        recommendation = {
            "F002": "ACCEPT or REJECT the candidate salinity-context addition.",
            "F016": "ACCEPT or REJECT the candidate saline paddy-field context.",
            "F017": "ACCEPT or REJECT the candidate saline upland-field context.",
            "F039": "ACCEPT, REJECT, or MODIFY the salinity context to distinguish pot system from intended application.",
            "F050": "ACCEPT only if the modelled land-use system itself is considered the saline study system; otherwise REJECT.",
            "F060": "ACCEPT only as explicit Review scope; otherwise REJECT as application discussion.",
            "F075": "ACCEPT, REJECT, or MODIFY the missing yield-measurement item.",
        }.get(case_id, "Resolve the soil-type versus study-system boundary; ACCEPT, REJECT, or MODIFY.")
        old_gold = row.get("gold")
        a, b = row.get("reviewer_A") or {}, row.get("reviewer_B") or {}
        blocks.extend([f"## {n}. {case_id}", "", f"- UID: `{row['uid']}`",
            f"- DOI: `{row.get('doi')}`", f"- Field: `{pointer}`",
            f"- Abstract context: {' / '.join(context)}",
            f"- Old Gold: `{json.dumps(old_gold, ensure_ascii=False)}`",
            f"- Candidate: `{json.dumps(candidate_values, ensure_ascii=False)}`",
            f"- Field Contract rule: {rule}",
            f"- A/B result: {a.get('category')} / {b.get('category')}",
            f"- Recommended decision: {recommendation}",
            f"- Reason: {CASE_STATUS.get(case_id, ('NEEDS_HUMAN_REVIEW', ''))[1]}", ""])
    return "\n".join(blocks) + "\n"


def _rule_for(pointer):
    if pointer.endswith("/salinity_context"):
        return "Require an explicit link to the Article study system/area; Review values must name review scope, not generic mechanism or application context."
    if pointer.endswith("/soil_type"):
        return "Use an explicit soil/substrate classification of the studied system; for Reviews label only explicit scope, never an empirical observation."
    if pointer.endswith("/yield"):
        return "Yield is harvested output/production; distinguish it from growth traits and other yield components."
    return "Use the Evidence Field Contract definition for the selected field; keep unsupported placement unresolved."


def run(root=ROOT):
    root = Path(root).resolve()
    output_dir = root / OUT_DIR
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite v0.6.1a candidate output: {output_dir}")
    dev_uids = set(_read_json(root / DEV_IDS))
    source = _load_development_requests(root, dev_uids)
    if len(source) != 7:
        raise ValueError("Expected the frozen seven-paper development identity set")
    candidate_rows = [copy.deepcopy(r) for r in read_jsonl(root / V1_CANDIDATE / "candidate_gold.jsonl")]
    candidate = {r["uid"]: r for r in candidate_rows}
    if set(candidate) != dev_uids:
        raise ValueError("v0.6.1 candidate identity set differs from development set")
    change_rows = [json.loads(line) for line in (root / CHANGE_LOG).read_text(encoding="utf-8").splitlines()]
    change_by_case = {row["case_id"]: row for row in change_rows}
    reconcile_rows = _reconciliation_rows(change_rows)

    deltas = []
    removals = [
        ("F011", "/evidence/methods", "eight different scenarios", "Scenario count is design metadata, not a named method."),
        ("F012", "/evidence/methods", "normal year and dry year", "Scenario conditions are not methods."),
        ("F047", "/evidence/methods", "a pot experiment", "A scale phrase is not a method; entropy-weighted TOPSIS remains."),
        ("F058", "/evidence/methods", "long-term climate records", "Data inputs are not methods."),
        ("F059", "/evidence/methods", "land-use data", "Data inputs are not methods."),
        ("F061", "/evidence/study_system/salinity_context", "saline- and alkali-stressed environments", "Review mechanism/application context is not review scope."),
        ("F062", "/evidence/study_system/salinity_context", "saline–alkali soils", "Review application soils are not review scope absent an explicit scope statement."),
        ("F063", "/evidence/study_system/salinity_context", "osmotic stress, ion toxicity and nutrient imbalance", "Mechanisms/stresses are not a salinity setting of the Review scope."),
    ]
    for case_id, pointer, value, reason in removals:
        row = change_by_case[case_id]
        uid = row["uid"]
        delta = _remove_value(candidate[uid], pointer, value)
        delta.update({"case_id": case_id, "doi": row.get("doi"), "reason": reason})
        deltas.append(delta)

    scale_row = change_by_case["F051"]
    scale_record = candidate[scale_row["uid"]]
    scale_pointer = "/evidence/study_system/experimental_scale"
    prior = _pointer_value(scale_record.get("evidence", {}), "/study_system/experimental_scale")
    if prior != "model":
        raise ValueError(f"Expected model scale in baseline candidate for {scale_row['uid']}")
    _pointer_value(scale_record.setdefault("evidence", {}), "/study_system")["experimental_scale"] = "unknown"
    scale_record.get("evidence_support", {}).pop(scale_pointer, None)
    deltas.append({"case_id": "F051", "uid": scale_row["uid"], "doi": scale_row.get("doi"),
                   "field": scale_pointer, "operation": "SET_UNKNOWN", "old_value": "model",
                   "new_value": "unknown", "reason": "Logistic regression is a method; no overall model/simulation-study scale is stated."})

    # The Review treatment audit is deliberately metadata-only here: no Review treatment rows exist in this development candidate.
    review_treatment_rows = [row for row in change_rows if "/treatments/" in (row.get("field") or "")
                             and "Review" in (source[row["uid"]].get("document_types") or [])]
    if review_treatment_rows:
        raise ValueError("Unexpected Review treatment change rows require explicit case-by-case reconciliation")
    review_uids = {uid for uid, record in source.items()
                   if any("review" in str(kind).casefold() for kind in (record.get("document_types") or []))}
    review_treatment_values = sum(
        len(values)
        for uid in review_uids
        for values in (candidate[uid].get("evidence", {}).get("treatments", {}) or {}).values()
        if isinstance(values, list)
    )

    review_rows = [change_by_case[case_id] for case_id in HUMAN_REVIEW_CASES]
    scale_fn_reclassification = []
    for case_id, classes in SCALE_FN_CLASSES.items():
        row = change_by_case[case_id]
        scale_fn_reclassification.append({"case_id": case_id, "uid": row["uid"], "doi": row.get("doi"),
                                          "classes": classes,
                                          "old_gold_scale": (row.get("gold") or {}).get("value"),
                                          "candidate_scale_after_reconciliation": (
                                              _pointer_value(candidate[row["uid"]].get("evidence", {}),
                                                             "/study_system/experimental_scale"))})
    output_dir.mkdir(parents=True)
    write_jsonl(output_dir / "candidate_gold.jsonl", [candidate[uid] for uid in sorted(candidate)])
    write_jsonl(output_dir / "candidate_delta.jsonl", deltas)
    write_jsonl(output_dir / "contract_reconciliation.jsonl", reconcile_rows)
    manifest = {"dataset_role": "unapproved_contract_reconciled_gold_candidate",
                "source_candidate": str(V1_CANDIDATE), "development_count": 7,
                "source_sha256": {"v061_candidate_gold": _sha256(root / V1_CANDIDATE / "candidate_gold.jsonl"),
                                  "original_gold": _sha256(root / ORIGINAL_GOLD),
                                  "strict_v1_report": _sha256(root / V1_REPORT),
                                  "metric_v2_report": _sha256(root / V2_REPORT)},
                "applied_contract_deltas": len(deltas), "performance_metrics_recomputed": False,
                "human_approved": False, "review_packet": str(REVIEW_V2),
                "review_items": len(review_rows), "review_treatment_change_rows": len(review_treatment_rows),
                "note": "Versioned derivative; original Gold, benchmark, v0.6.1 candidate, reports, and holdout were not modified."}
    write_json(output_dir / "manifest.json", manifest)
    report = {"version": "v0.6.1a", "official_metrics_recomputed": False,
              "scale_fn_reclassification": scale_fn_reclassification,
              "classification_changed_uids": [change_by_case["F051"]["uid"]],
              "candidate_modified_uids": sorted({delta["uid"] for delta in deltas}),
              "contract_consistent": sum(r["status"] == "CONTRACT_CONSISTENT" for r in reconcile_rows),
              "contract_conflict": sum(r["status"] == "CONTRACT_CONFLICT" for r in reconcile_rows),
              "needs_human_review": sum(r["status"] == "NEEDS_HUMAN_REVIEW" for r in reconcile_rows),
              "classifications": reconcile_rows, "candidate_deltas": deltas,
              "previous_review_packet_count": 24, "review_packet_count": len(review_rows),
              "candidate_delta_count": len(deltas), "review_treatment_rows": len(review_treatment_rows),
              "review_candidate_treatment_values": review_treatment_values,
              "review_downstream_policy": "Review study_system values are scope descriptors, not empirical observations; Review treatments are review-derived and never author-operated.",
              "holdout_touched": False, "prompt_iteration_2_started": False, "pilot_started": False}
    write_json(root / RECON_REPORT, report)
    (root / REVIEW_V2).parent.mkdir(parents=True, exist_ok=True)
    (root / REVIEW_V2).write_text(_review_packet(review_rows, candidate, source), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = run(args.root)
    print(json.dumps({k: result[k] for k in ("version", "contract_consistent", "contract_conflict",
                                               "needs_human_review", "review_packet_count", "review_treatment_rows",
                                               "review_candidate_treatment_values", "holdout_touched",
                                               "prompt_iteration_2_started", "pilot_started")}, indent=2))


if __name__ == "__main__":
    main()
