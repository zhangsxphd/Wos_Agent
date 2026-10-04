"""Build v0.6.1 evaluation-calibration artifacts; never runs extraction."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.evidence.audit_v2 import audit_evidence_v2
from scripts.evidence.freeze_holdout import DEFAULT_CORPUS, DEFAULT_OUTPUT, DEV_IDENTITIES, freeze_holdout
from scripts.evidence.metrics_v2 import corpus_metrics_v2
from scripts.pipeline_utils import read_jsonl, write_json, write_jsonl


DEV_IDENTITIES_FILE = Path("data/evidence_benchmarks/gold_v04/identities.json")
GOLD_FILE = Path("data/evidence/20261003_192326_169425_evidence_57015bbc_evidence.jsonl")
BATCH_MANIFEST = Path("data/evidence_batches/v06_gold_blind_iteration1_20261004/batch_001/batch_manifest.json")
V1_REPORT = Path("data/reports/v06_gold_benchmark_20261004_123933.json")
FIELD_PACKET = Path("data/reports/v06_field_discrepancies_20261004.json")
FINDING_PACKET = Path("data/reports/v06_finding_discrepancies_20261004.json")
CANDIDATE_DIR = Path("data/evidence_benchmarks/gold_v04_adjudicated_candidate")
HUMAN_PACKET = Path("data/reports/v06_gold_candidate_human_review.md")
CALIBRATION_JSON = Path("data/reports/v06_1_evaluation_calibration_20261004.json")
CALIBRATION_MD = Path("data/reports/v06_1_evaluation_calibration_20261004.md")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _value_at(document, pointer):
    value = document
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _gold_values(record, pointer):
    value = _value_at(record.get("evidence", {}), pointer.removeprefix("/evidence"))
    if pointer.endswith("/experimental_scale"):
        return [] if value in (None, "unknown") else [value]
    return value if isinstance(value, list) else []


def _support_for(item):
    if not item:
        return None
    if isinstance(item.get("anchor"), dict):
        return item["anchor"]
    if "evidence_text" in item:
        return {k: item.get(k) for k in ("source", "evidence_text", "start", "end")}
    return None


def _append_field(record, pointer, value, support):
    evidence_pointer = pointer.removeprefix("/evidence")
    target = _value_at(record.setdefault("evidence", {}), evidence_pointer)
    if not isinstance(target, list):
        raise ValueError(f"Candidate ADD requires an array field: {pointer}")
    if value in target:
        return False
    index = len(target)
    target.append(value)
    if support:
        record.setdefault("evidence_support", {})[f"{pointer}/{index}"] = copy.deepcopy(support)
    return True


def _remove_field(record, pointer, value):
    values = _gold_values(record, pointer)
    found = value in values
    supports = record.get("evidence_support", {})
    retained = []
    retained_supports = []
    for index, current in enumerate(values):
        if current == value:
            continue
        retained.append(current)
        retained_supports.append(supports.get(f"{pointer}/{index}"))
    target = _value_at(record.setdefault("evidence", {}), pointer.removeprefix("/evidence"))
    if not isinstance(target, list):
        raise ValueError(f"Candidate MOVE requires an array field: {pointer}")
    target[:] = retained
    for key in list(supports):
        if key.startswith(pointer + "/"):
            del supports[key]
    for index, support in enumerate(retained_supports):
        if support:
            supports[f"{pointer}/{index}"] = support
    return found


def _copy_prediction_support(prediction, field_pointer):
    if not prediction or not isinstance(prediction, dict):
        return None
    support = prediction.get("anchor")
    return copy.deepcopy(support) if support else None


def _field_action(row, gold, uid_rows):
    label = row.get("adjudication_consensus")
    field = row["field"]
    side = row["strict_mismatch_side"]
    value = (row.get("prediction") or {}).get("value") if side == "FP" else (row.get("gold") or {}).get("value")
    if label in ("NEEDS_HUMAN_REVIEW", "AMBIGUOUS"):
        return "NEEDS_HUMAN_REVIEW", None, "Reviewer disagreement or unresolved ontology boundary."
    if label == "TRUE_FP":
        return "REMOVE", None, "Prediction is supported text but does not belong in this field; Gold stays unchanged."
    if label == "TRUE_FN":
        return "KEEP", None, "Gold contains an explicit item omitted by the prediction; no Gold edit."
    if label == "LEXICAL_BOUNDARY_MISMATCH":
        return "KEEP", None, "Metric v2 represents this as a source-boundary match; retain the independent Gold wording."
    if label == "CATEGORY_MISMATCH":
        if side == "FN":
            return "KEEP", None, "Gold already places this item in its current field; the mismatch is the worker's cross-category placement."
        counterpart = next((r for r in uid_rows if r["uid"] == row["uid"] and r["field"] != field
                            and ((r.get("gold") or {}).get("value") == value
                                 or (r.get("prediction") or {}).get("value") == value)), None)
        target = counterpart["field"] if counterpart else "/evidence/treatments/other_treatments"
        return "MOVE_CATEGORY", target, f"The source-supported item belongs under {target}; preserve existing Gold unless the category move is accepted."
    if label == "GOLD_OMISSION_OR_INCONSISTENCY":
        if side == "FN":
            if value == "two levels (1% and 3% by weight)" and field.endswith("other_treatments"):
                return "MOVE_CATEGORY", "/evidence/treatments/amendments", "Dose/level wording belongs with the amendment it qualifies; proposed move awaits human acceptance."
            return "KEEP", None, "Gold already contains the fact; the mismatch is a prediction omission, not a missing reference item."
        if value == "flooded and shallow–wet" and field.endswith("irrigation"):
            return "MOVE_CATEGORY", "/evidence/treatments/water_regime", "Contract v1 assigns water-state regimes to water_regime; matching Gold is already present there."
        if row["case_id"] in {"F004", "F005", "F006", "F008"}:
            return "REMOVE", None, "This is a model scenario/background condition, not an applied irrigation or treatment arm under Contract v1."
        if row["case_id"] in {"F028", "F050", "F075"}:
            return "NEEDS_HUMAN_REVIEW", None, "Contract boundary needs human confirmation before changing the candidate reference."
        return "ADD", None, "A/B agree the supported item is missing from the field; this is only an unapproved candidate addition."
    return "NEEDS_HUMAN_REVIEW", None, f"Unmapped adjudication label: {label}"


def _finding_action(row):
    label = row.get("adjudication_consensus")
    if label == "GOLD_OMISSION":
        return "ADD", "A/B agree this abstract-supported synthesis/result is absent from Gold; candidate addition remains unapproved."
    if label == "TRUE_MISSED_FINDING":
        return "KEEP", "Gold already contains an eligible result omitted by the worker."
    if label == "SPAN_MATCH_FAILURE":
        return "KEEP", "Metric v2 matches this one-to-one boundary difference; preserve original Gold span."
    if label in ("DUPLICATE_FINDING", "IMPLICATION_AS_FINDING", "TRUE_OVEREXTRACT",
                 "BACKGROUND_AS_FINDING", "METHOD_AS_FINDING", "OBJECTIVE_AS_FINDING",
                 "REVIEWED_LITERATURE_AS_FINDING"):
        return "REMOVE", "Do not add this prediction to candidate Gold; it is a prediction-side extraction issue."
    return "NEEDS_HUMAN_REVIEW", "A/B did not establish a unique eligible finding decision."


def _apply_proposal(record, action, row, target_field=None):
    side = row.get("strict_mismatch_side")
    field = row.get("field")
    if row.get("case_type") == "finding":
        if action != "ADD":
            return False
        prediction = row.get("prediction")
        if not prediction:
            return False
        record.setdefault("evidence", {}).setdefault("findings", []).append(copy.deepcopy(prediction))
        return True
    if action == "ADD" and side == "FP":
        prediction = row.get("prediction") or {}
        return _append_field(record, field, prediction.get("value"), _support_for(prediction))
    if action == "MOVE_CATEGORY" and row["case_id"] == "F029":
        original = (row.get("gold") or {}).get("value")
        support = _support_for(row.get("gold"))
        if _remove_field(record, field, original):
            return _append_field(record, target_field, original, support)
    return False


def _record_predictions(root, manifest):
    result = {}
    for request in manifest["requests"]:
        path = root / request["response_file"]
        result[request["uid"]] = _read(path)["response"]
    return result


def _load_development(root):
    uids = set(_read(root / DEV_IDENTITIES_FILE))
    canonical = {row["uid"]: row for row in read_jsonl(root / DEFAULT_CORPUS) if row["uid"] in uids}
    gold = {row["uid"]: row for row in read_jsonl(root / GOLD_FILE) if row.get("uid") in uids}
    manifest = _read(root / BATCH_MANIFEST)
    predictions = _record_predictions(root, manifest)
    if len(uids) != 7 or set(canonical) != uids or set(gold) != uids or set(predictions) != uids:
        raise ValueError("Development-set identity mismatch across corpus, Gold, and worker responses")
    papers = [(uid, gold[uid], predictions[uid], canonical[uid].get("abstract") or "") for uid in sorted(uids)]
    return uids, canonical, gold, predictions, papers


def _lexical_recovery(metrics, field_packet):
    lexical = [row for row in field_packet["field_mismatches"]
               if row.get("adjudication_consensus") == "LEXICAL_BOUNDARY_MISMATCH"]
    matched_case_ids = set()
    boundary_pairs = []
    for uid, detail in metrics["per_record"].items():
        for field, result in detail["fields"]["field_metrics"].items():
            for match in result["matches"]:
                if match["level"] != "BOUNDARY_EQUIVALENT":
                    continue
                rows = [row for row in lexical if row["uid"] == uid and row["field"] == field]
                gold_row = next((row for row in rows if row["strict_mismatch_side"] == "FN"
                                 and (row.get("gold") or {}).get("value") == match["gold"]
                                 and row["case_id"] not in matched_case_ids), None)
                pred_row = next((row for row in rows if row["strict_mismatch_side"] == "FP"
                                 and (row.get("prediction") or {}).get("value") == match["prediction"]
                                 and row["case_id"] not in matched_case_ids), None)
                if gold_row and pred_row:
                    matched_case_ids.update((gold_row["case_id"], pred_row["case_id"]))
                    boundary_pairs.append({"uid": uid, "field": field,
                                           "gold_case": gold_row["case_id"], "prediction_case": pred_row["case_id"],
                                           "gold": match["gold"], "prediction": match["prediction"]})
    true_cases = [row for row in field_packet["field_mismatches"]
                  if row.get("adjudication_consensus") in ("TRUE_FP", "TRUE_FN")]
    absorbed = [row["case_id"] for row in true_cases if row["case_id"] in matched_case_ids]
    return {"expected_lexical_mismatch_rows": len(lexical),
            "boundary_match_pairs": len(boundary_pairs),
            "recovered_lexical_mismatch_rows": len(matched_case_ids),
            "recovered_lexical_mismatch_cases": f"{len(matched_case_ids)}/{len(lexical)}",
            "unresolved_lexical_case_ids": sorted(row["case_id"] for row in lexical
                                                   if row["case_id"] not in matched_case_ids),
            "true_fp_fn_absorbed_count": len(absorbed), "true_fp_fn_absorbed_case_ids": absorbed,
            "boundary_pairs": boundary_pairs}


def build_candidate(root, out_dir=CANDIDATE_DIR, human_packet_path=HUMAN_PACKET):
    root, out_dir = Path(root).resolve(), Path(root).resolve() / out_dir
    if out_dir.exists():
        raise FileExistsError(f"Refusing to overwrite Gold candidate directory: {out_dir}")
    identities = set(_read(root / DEV_IDENTITIES_FILE))
    source_rows = [row for row in read_jsonl(root / GOLD_FILE) if row.get("uid") in identities]
    if len(source_rows) != 7:
        raise ValueError("Candidate must contain exactly the seven development Gold records")
    source_rows_by_uid = {row["uid"]: row for row in source_rows}
    candidate = {row["uid"]: copy.deepcopy(row) for row in source_rows}
    field_packet = _read(root / FIELD_PACKET)
    finding_packet = _read(root / FINDING_PACKET)
    all_field = field_packet["field_mismatches"]
    uid_rows = all_field
    rows = []
    for row in all_field:
        copy_row = {**row, "case_type": "field"}
        action, target_field, reason = _field_action(copy_row, candidate[row["uid"]], uid_rows)
        applied = _apply_proposal(candidate[row["uid"]], action, copy_row, target_field)
        rows.append({**copy_row, "proposed_action": action, "target_field": target_field,
                     "proposal_applied_to_candidate_file": applied, "proposal_reason": reason})
    for row in finding_packet["mismatches"]:
        copy_row = {**row, "field": "/evidence/findings", "case_type": "finding",
                    "strict_mismatch_side": row["mismatch_side"]}
        action, reason = _finding_action(copy_row)
        applied = _apply_proposal(candidate[row["uid"]], action, copy_row)
        rows.append({**copy_row, "proposed_action": action, "target_field": None,
                     "proposal_applied_to_candidate_file": applied, "proposal_reason": reason})
    if not identities == set(candidate):
        raise AssertionError("Candidate identity set changed")

    for row in rows:
        field = row.get("field") or "/evidence/findings"
        target_field = row.get("target_field") or field
        if field == "/evidence/findings":
            original_values = (row.get("gold_candidate_findings") or row.get("gold_findings_in_record") or [])
            candidate_values = _value_at(candidate[row["uid"]].get("evidence", {}), "/findings") or []
        else:
            original_values = _gold_values(source_rows_by_uid[row["uid"]], field)
            candidate_values = _gold_values(candidate[row["uid"]], target_field)
        row["original_gold"] = copy.deepcopy(original_values)
        row["candidate_gold_values"] = copy.deepcopy(candidate_values)
        row["candidate_gold"] = copy.deepcopy(candidate_values)
        row["support_span"] = _support_for(row.get("gold")) or _support_for(row.get("prediction"))
        row["A_decision"] = copy.deepcopy(row.get("reviewer_A"))
        row["B_decision"] = copy.deepcopy(row.get("reviewer_B"))

    by_uid = {row["uid"]: row for row in candidate.values()}
    output_rows = [by_uid[uid] for uid in sorted(by_uid)]
    out_dir.mkdir(parents=True)
    write_jsonl(out_dir / "candidate_gold.jsonl", output_rows)
    counts = Counter(row["proposed_action"] for row in rows)
    write_json(out_dir / "candidate_manifest.json", {
        "dataset_role": "unapproved_gold_candidate",
        "source_gold": str(GOLD_FILE), "development_identities": sorted(identities),
        "source_gold_records": 7, "candidate_records": len(output_rows),
        "candidate_changes_are_human_approved": False,
        "candidate_action_counts": dict(counts),
        "change_log": "change_log.jsonl",
        "note": "A/B consensus is not human Gold. Candidate is not a replacement reference and must not be used as a final performance estimate.",
    })
    write_jsonl(out_dir / "change_log.jsonl", [{k: value for k, value in row.items()
                                                 if k not in ("gold_same_field_items", "prediction_same_field_items",
                                                              "gold_candidate_findings", "prediction_candidate_findings",
                                                              "gold_findings_in_record", "prediction_findings_in_record")}
                                                  for row in rows])

    # Compact, ontology-focused review: A/B conflicts, category errors, and
    # non-lexical Gold proposals that require a field-boundary decision.
    review_rows = []
    for row in rows:
        if row.get("case_type") != "field":
            continue
        category = row.get("adjudication_consensus")
        is_ab_disagreement = category == "NEEDS_HUMAN_REVIEW"
        is_category_error = category == "CATEGORY_MISMATCH"
        is_ontology_omission = category == "GOLD_OMISSION_OR_INCONSISTENCY" and row.get("strict_mismatch_side") == "FP"
        if is_ontology_omission and row["case_id"] in {"F045", "F046"}:
            is_ontology_omission = False  # obvious yield/other-analyte placements, not ontology disputes
        is_move = row.get("proposed_action") == "MOVE_CATEGORY"
        if not (is_ab_disagreement or is_category_error or is_ontology_omission or is_move):
            continue
        if row not in review_rows:
            review_rows.append(row)
    unique_review = {row["case_id"]: row for row in review_rows}
    review_rows = [unique_review[key] for key in sorted(unique_review)]
    if len(review_rows) > 25:
        raise AssertionError(f"Human review packet exceeded the 25-item target: {len(review_rows)}")
    lines = ["# v0.6 Gold candidate — minimal human review", "",
        "This is a proposal packet, not human-approved Gold. The original Gold, strict benchmark, worker responses, and adjudication reports remain unchanged. For each item, reply `ACCEPT`, `REJECT`, or `MODIFY` (with the corrected value/category).", "",
        f"Items requiring review: **{len(review_rows)}**. Includes all A/B disagreements, category mismatches, and field-ontology candidate changes; obvious yield/other-analyte placements and strict lexical-only differences are omitted.", ""]
    for index, row in enumerate(review_rows, 1):
        record = candidate[row["uid"]]
        lines.extend([f"## {index}. {row['case_id']} — {row['uid']}", "",
            f"- Paper: {row.get('title')} ({row.get('doi')})",
            f"- Field: `{row['field']}`" + (f" → `{row['target_field']}`" if row.get("target_field") else ""),
            f"- Abstract context: {' / '.join(row.get('abstract_context') or [])}",
            f"- Old Gold: {json.dumps(row.get('gold'), ensure_ascii=False)}",
            f"- Candidate: {json.dumps(row.get('candidate_gold_values'), ensure_ascii=False)}",
            f"- A/B: {row.get('reviewer_A', {}).get('category')} / {row.get('reviewer_B', {}).get('category')}",
            f"- Recommended action: `{row['proposed_action']}` — {row['proposal_reason']}",
            f"- Support: {json.dumps(_support_for(row.get('gold')) or _support_for(row.get('prediction')), ensure_ascii=False)}", ""])
    Path(root / human_packet_path).parent.mkdir(parents=True, exist_ok=True)
    (root / human_packet_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"candidate_records": len(output_rows), "change_log_rows": len(rows),
            "action_counts": dict(counts), "human_review_items": len(review_rows),
            "candidate_directory": str(out_dir), "human_review_packet": str(root / human_packet_path)}


def run_calibration(root=Path("."), output_json=CALIBRATION_JSON, output_md=CALIBRATION_MD,
                    candidate_dir=CANDIDATE_DIR, human_packet=HUMAN_PACKET, holdout_dir=DEFAULT_OUTPUT):
    root = Path(root).resolve()
    dev_uids, canonical, gold, predictions, papers = _load_development(root)
    v2 = corpus_metrics_v2(papers)
    v1 = _read(root / V1_REPORT)
    if v1["metrics"]["field_micro"]["fp"] != 38 or v1["metrics"]["field_micro"]["fn"] != 37:
        raise ValueError("Frozen v1 report does not match the seven-paper development baseline")
    field_packet, finding_packet = _read(root / FIELD_PACKET), _read(root / FINDING_PACKET)
    recovery = _lexical_recovery(v2, field_packet)
    audit_flags = []
    for uid in sorted(dev_uids):
        audit_flags.extend({"uid": uid, **flag} for flag in audit_evidence_v2(predictions[uid], canonical[uid]))
    audit_counts = Counter(flag["flag"] for flag in audit_flags)
    false_flags = [x for x in audit_flags if x["flag"] == "review_as_experiment_error"]
    candidate_path = root / candidate_dir
    if candidate_path.exists():
        manifest = _read(candidate_path / "candidate_manifest.json")
        candidate_report = {"candidate_records": manifest["candidate_records"],
                            "change_log_rows": sum(1 for _ in (candidate_path / "change_log.jsonl").open()),
                            "action_counts": manifest["candidate_action_counts"],
                            "human_review_items": sum(1 for line in (root / human_packet).read_text().splitlines()
                                                      if line.startswith("## ") and line[3:4].isdigit()),
                            "candidate_directory": str(candidate_path),
                            "human_review_packet": str(root / human_packet)}
    else:
        candidate_report = build_candidate(root, candidate_dir, human_packet)
    development_manifest_path = root / "data/evidence_benchmarks/gold_v04/development_manifest.json"
    development_manifest = {
        "dataset_role": "development", "identity_file": str(DEV_IDENTITIES_FILE),
        "record_count": len(dev_uids), "historical_benchmark_version": "v06_strict_lexical_v1",
        "independent_test_set": False,
        "reason": "The original seven-paper Gold set became a development set after discrepancy adjudication and must not be used as an independent final performance estimate.",
    }
    if development_manifest_path.exists():
        if _read(development_manifest_path) != development_manifest:
            raise ValueError("Development manifest exists with different provenance")
    else:
        write_json(development_manifest_path, development_manifest)
    holdout_path = root / holdout_dir
    if holdout_path.exists():
        holdout_manifest = _read(holdout_path / "selection_manifest.json")
        identities = _read(holdout_path / "identities.json")
        if len(identities) != 20 or any("abstract" in row for row in identities):
            raise ValueError("Existing holdout identity file is not metadata-only 20-paper selection")
        if set(row["uid"] for row in identities) & dev_uids:
            raise ValueError("Existing frozen holdout overlaps development identities")
    else:
        holdout_manifest = freeze_holdout(root, DEFAULT_CORPUS, holdout_dir)
    summary = {
        "evaluation_version": "v0.6.1", "development_set_role": "development",
        "development_records": len(dev_uids), "independent_performance_estimate": False,
        "frozen_v1": {"version": "v06_strict_lexical_v1",
                      "field_precision": v1["metrics"]["field_micro"]["precision"],
                      "field_recall": v1["metrics"]["field_micro"]["recall"],
                      "field_f1": v1["metrics"]["field_micro"]["f1"],
                      "field_exact_tp": v1["metrics"]["field_micro"]["tp"],
                      "field_fp": v1["metrics"]["field_micro"]["fp"],
                      "field_fn": v1["metrics"]["field_micro"]["fn"],
                      "finding_precision": v1["metrics"]["finding_metrics"]["precision"],
                      "finding_recall": v1["metrics"]["finding_metrics"]["recall"],
                      "finding_exact_match": v1["metrics"]["finding_metrics"]["exact_evidence_text_match"],
                      "finding_overlap_match": v1["metrics"]["finding_metrics"]["overlap_match"],
                      "quality_gate": v1["quality_gate"]["status"]},
        "metric_v2": {"field": v2["fields"], "findings": v2["findings"],
                      "rules": ["one-to-one within field", "NFKC/casefold/whitespace exact first",
                                "boundary requires verified same-abstract overlapping spans and deterministic literal containment",
                                "no cross-field match", "experimental_scale exact enum only", "no embedding or LLM"]},
        "lexical_case_recovery": recovery,
        "audit_v2": {"review_context_flag": audit_counts["review_context_flag"],
                     "review_as_experiment_error": audit_counts["review_as_experiment_error"],
                     "flags": audit_flags,
                     "semantics": "Review document type is context, not an error. An error requires an explicit review_author attribution on an experimental-fact support anchor that conflicts with source attribution to prior work; absent attribution is unknown."},
        "gold_candidate": candidate_report,
        "holdout": {"frozen": True, "count": holdout_manifest["sample_size"],
                    "development_excluded": holdout_manifest["development_count_excluded"],
                    "HOLDOUT_FROZEN_BEFORE_PROMPT_ITERATION_2": True,
                    "abstracts_or_evidence_or_responses_created": False},
    }
    write_json(root / output_json, summary, overwrite=True)
    fm, dm = summary["metric_v2"]["field"], summary["metric_v2"]["findings"]
    lines = ["# v0.6.1 Evaluation Calibration", "",
        "The seven-paper set is a development set. **Do not interpret its v2 metrics as independent model performance.** v1 remains a separately preserved historical result.", "",
        "## Metric v1 (unchanged)", "",
        f"Field exact TP={summary['frozen_v1']['field_exact_tp']}, FP={summary['frozen_v1']['field_fp']}, FN={summary['frozen_v1']['field_fn']}; precision={summary['frozen_v1']['field_precision']:.3f}, recall={summary['frozen_v1']['field_recall']:.3f}, F1={summary['frozen_v1']['field_f1']:.3f}.",
        f"Findings precision={summary['frozen_v1']['finding_precision']:.3f}, recall={summary['frozen_v1']['finding_recall']:.3f}; gate={summary['frozen_v1']['quality_gate']}.", "",
        "## Metric v2 on development set", "",
        f"Field: exact TP={fm['exact_tp']}, boundary-equivalent TP={fm['boundary_tp']}, FP={fm['fp']}, FN={fm['fn']}, precision={fm['precision']:.3f}, recall={fm['recall']:.3f}, F1={fm['f1']:.3f}.",
        f"Findings: exact={dm['exact_match']}, boundary={dm['boundary_match']}, FP={dm['unmatched_prediction']}, FN={dm['unmatched_gold']}, precision={dm['precision']:.3f}, recall={dm['recall']:.3f}.",
        f"The 29 adjudicated lexical-boundary mismatch rows: {recovery['recovered_lexical_mismatch_cases']} resolved by {recovery['boundary_match_pairs']} one-to-one pairs. TRUE_FP/FN accidentally absorbed: {recovery['true_fp_fn_absorbed_count']}.", "",
        "V2 never matches across fields and uses exact enum matching for experimental scale. Gold omission is not promoted to TP. V2 is deterministic and non-LLM.", "",
        "## Review audit v2", "",
        f"review_context_flag items: {audit_counts['review_context_flag']}; review_as_experiment_error items: {audit_counts['review_as_experiment_error']}. A Review document type alone is context only.", "",
        "## Gold candidate and human review", "",
        f"Unapproved candidate records: {candidate_report['candidate_records']}; candidate action rows: {candidate_report['change_log_rows']}; human review items: {candidate_report['human_review_items']}. A/B consensus is not human Gold.", "",
        "## Holdout", "",
        f"Frozen identities: {holdout_manifest['sample_size']}; eligible pool after excluding development: {holdout_manifest['eligible_count']}; seed={holdout_manifest['seed']}. `HOLDOUT_FROZEN_BEFORE_PROMPT_ITERATION_2 = true`. No extraction, Evidence, or responses were created.", "",
        "The original seven-paper set is development-only after adjudication. No Prompt iteration 2, holdout run, or pilot was started."]
    (root / output_md).parent.mkdir(parents=True, exist_ok=True)
    (root / output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = run_calibration(args.root)
    print(json.dumps({"metric_v2_field": {k: result["metric_v2"]["field"][k]
                                           for k in ("exact_tp", "boundary_tp", "fp", "fn", "precision", "recall", "f1")},
                      "metric_v2_findings": {k: result["metric_v2"]["findings"][k]
                                              for k in ("exact_match", "boundary_match", "unmatched_prediction", "unmatched_gold", "precision", "recall")},
                      "human_review_items": result["gold_candidate"]["human_review_items"],
                      "holdout_count": result["holdout"]["count"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
