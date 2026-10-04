"""Create post-hoc, source-anchored packets for v0.6 benchmark adjudication.

This diagnostic never edits the official benchmark, Gold, prompt, schema,
canonical input, or worker responses. It only writes new reports and reviewer
payloads in caller-selected output directories.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.evidence.audit import audit_evidence
from scripts.evidence.metrics import KEY_FIELDS, normalize_item
from scripts.pipeline_utils import read_jsonl


FIELD_LABELS = (
    "TRUE_FP", "TRUE_FN", "LEXICAL_BOUNDARY_MISMATCH", "CATEGORY_MISMATCH",
    "GOLD_OMISSION_OR_INCONSISTENCY", "AMBIGUOUS",
)
FINDING_LABELS = (
    "TRUE_OVEREXTRACT", "SPAN_BOUNDARY_DIFFERENCE", "GOLD_OMISSION",
    "BACKGROUND_AS_FINDING", "METHOD_AS_FINDING", "OBJECTIVE_AS_FINDING",
    "IMPLICATION_AS_FINDING", "REVIEWED_LITERATURE_AS_FINDING",
    "DUPLICATE_FINDING", "AMBIGUOUS", "TRUE_MISSED_FINDING",
    "SPAN_MATCH_FAILURE", "GOLD_AMBIGUOUS",
)
REVIEW_LABELS = ("REAL_REVIEW_AS_EXPERIMENT_ERROR", "AUDIT_FLAG_ONLY")
SUBSTRING_LABELS = ("SEMANTICALLY_EQUIVALENT", "NOT_EQUIVALENT", "AMBIGUOUS")
FIELD_DEFINITIONS = {
    "TRUE_FP": "Prediction is explicitly present in the abstract but does not belong in this Evidence field.",
    "TRUE_FN": "Gold contains an explicitly supported fact that the prediction omitted.",
    "LEXICAL_BOUNDARY_MISMATCH": "Gold and prediction denote the same concept and source region, but use different verbatim span granularity.",
    "CATEGORY_MISMATCH": "Prediction captured a supported fact but assigned it to the wrong schema field.",
    "GOLD_OMISSION_OR_INCONSISTENCY": "Prediction is explicitly supported and fits the schema/prompt, but Gold omits it or places it inconsistently.",
    "AMBIGUOUS": "The abstract, schema, or prompt does not determine one classification.",
}
FINDING_DEFINITIONS = {
    "TRUE_OVEREXTRACT": "A predicted finding is not an eligible result claim under the extraction contract.",
    "SPAN_BOUNDARY_DIFFERENCE": "The predicted-only and Gold finding express the same claim with different evidence-span boundaries.",
    "GOLD_OMISSION": "The predicted finding is an eligible, abstract-supported result claim absent from Gold.",
    "BACKGROUND_AS_FINDING": "The item reports background or prior literature rather than this paper's findings.",
    "METHOD_AS_FINDING": "The item describes a method or study setup rather than a result.",
    "OBJECTIVE_AS_FINDING": "The item states an objective or question rather than a result.",
    "IMPLICATION_AS_FINDING": "The item states an implication or recommendation rather than a result.",
    "REVIEWED_LITERATURE_AS_FINDING": "The item is a result attributed to literature reviewed, not to this paper as an eligible finding.",
    "DUPLICATE_FINDING": "The item repeats the same finding already represented in this prediction.",
    "AMBIGUOUS": "The source text and contract do not determine one classification.",
    "TRUE_MISSED_FINDING": "Gold contains an eligible, explicit result claim that the prediction omitted.",
    "SPAN_MATCH_FAILURE": "Gold and the omitted prediction express the same finding, but their evidence spans differ enough to miss exact/substring matching.",
    "GOLD_AMBIGUOUS": "The Gold finding or extraction-contract boundary is not uniquely supported by the abstract.",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def value_at(document, pointer):
    value = document
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        if value is None:
            return None
        value = value[int(part)] if isinstance(value, list) else value.get(part) if isinstance(value, dict) else None
    return value


def field_values(document, pointer):
    value = value_at(document, pointer)
    if pointer.endswith("experimental_scale"):
        return [] if value in (None, "unknown") else [value]
    return value if isinstance(value, list) else []


def sentence_for(abstract, anchor):
    if not isinstance(anchor, dict):
        return None
    start, end = anchor.get("start"), anchor.get("end")
    if type(start) is not int or type(end) is not int or not (0 <= start < end <= len(abstract)):
        return None
    left = max(abstract.rfind(mark, 0, start) for mark in ".?!") + 1
    stops = [pos for mark in ".?!" if (pos := abstract.find(mark, end)) >= 0]
    right = min(stops) + 1 if stops else len(abstract)
    return abstract[left:right].strip()


def anchor_for(document, pointer, index=None, item=None):
    if item is not None and all(key in item for key in ("source", "evidence_text", "start", "end")):
        return {key: item.get(key) for key in ("source", "evidence_text", "start", "end")}
    key = pointer if index is None else f"{pointer}/{index}"
    support = (document.get("evidence_support") or {}).get(key)
    if not isinstance(support, dict):
        return None
    return {name: support.get(name) for name in ("source", "evidence_text", "start", "end")}


def item_objects(document, pointer):
    values = field_values(document, pointer)
    return [{"value": value, "anchor": anchor_for(document, pointer, index if not pointer.endswith("experimental_scale") else None)}
            for index, value in enumerate(values)]


def add_contexts(abstract, *items):
    contexts = []
    for item in items:
        if not item:
            continue
        anchor = item.get("anchor") if isinstance(item, dict) else None
        if anchor is None and isinstance(item, dict) and all(key in item for key in ("source", "evidence_text", "start", "end")):
            anchor = item
        context = sentence_for(abstract, anchor)
        if context and context not in contexts:
            contexts.append(context)
    return contexts


def markdown_context(items):
    return " / ".join(items).replace("|", "\\|")


def match_findings(gold_findings, predicted_findings):
    remaining = set(range(len(gold_findings)))
    pairs, predicted_only = [], []
    for pred_index, predicted in enumerate(predicted_findings):
        pred_text = normalize_item(predicted.get("evidence_text"))
        match = next((i for i in sorted(remaining)
                      if normalize_item(gold_findings[i].get("evidence_text")) == pred_text), None)
        exact = match is not None
        if match is None:
            match = next((i for i in sorted(remaining) if pred_text and
                          (pred_text in normalize_item(gold_findings[i].get("evidence_text")) or
                           normalize_item(gold_findings[i].get("evidence_text")) in pred_text)), None)
        if match is None:
            predicted_only.append(pred_index)
        else:
            pairs.append({"gold_index": match, "prediction_index": pred_index, "exact": exact})
            remaining.remove(match)
    return pairs, predicted_only, sorted(remaining)


def metric_audit_report(path):
    text = """# v0.6 metric audit — strict lexical v1

Official benchmark label: `benchmark_version = v06_strict_lexical_v1`. This is a post-hoc definition audit only. It does not revise the benchmark's official scores.

## Field item matching

`normalize_item` applies Unicode NFKC normalization, `casefold()`, and collapse/trim of whitespace, then `item_metrics` compares normalized sets with exact equality (`scripts/evidence/metrics.py`, `normalize_item` and `item_metrics`). It does not normalize punctuation, hyphen variants, morphology, abbreviations, or synonyms. There is no semantic synonym matcher: **no**. Field evidence-span overlap is not used for matching: **no**. The field score therefore penalizes same-concept values when their normalized strings differ.

## Finding matching

Finding matching first consumes exact normalized evidence-text matches. It then accepts a non-exact pair only when either normalized evidence string is a substring of the other (`finding_metrics`). This is substring containment, not token/semantic/span-offset overlap. Of 31 accepted overlap matches, 27 are exact and 4 are substring-only; those four are separately packeted for source-level review.

## `review_as_experiment` audit flag

The flag can be emitted solely because any `document_types` value contains `review`: `audit_evidence` ORs that metadata test with a review-language regex for every support span and finding. `calculate_benchmark` then counts every `review_language` flag as `review_as_experiment`. Thus the count **can arise from Review document type alone** and does not itself prove that the worker claimed the review authors conducted an experiment. Each flagged item is adjudicated separately as `REAL_REVIEW_AS_EXPERIMENT_ERROR` or `AUDIT_FLAG_ONLY`.

## Scope and interpretation

No metric code, thresholds, validator, prompt, schema, Gold item, response, or official benchmark report was changed. Post-hoc labels are diagnosis only; they are not a corrected score. The 0.599 field micro-F1 and 0.756 finding precision remain the official `v06_strict_lexical_v1` values.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_packets(root, batch_dir, canonical_path, gold_path, benchmark_path, output_dir, reviewer_dir):
    root = Path(root).resolve()
    batch_dir = Path(batch_dir)
    run_dir = batch_dir.parent
    canonical = {r["uid"]: r for r in read_jsonl(root / canonical_path)}
    gold_rows = read_jsonl(root / gold_path)
    gold = {r["uid"]: r for r in gold_rows if r.get("screening", {}).get("status") == "maybe" and r.get("uid") in canonical}
    batch_manifest = read_json(run_dir / "manifest.json")
    batch = read_json(batch_dir / "batch_manifest.json")
    predictions = {}
    for item in batch["requests"]:
        response_path = root / item["response_file"]
        predictions[item["uid"]] = read_json(response_path)["response"]
    official = read_json(root / benchmark_path)
    if official["metrics"]["field_micro"]["fp"] != 38 or official["metrics"]["field_micro"]["fn"] != 37:
        raise ValueError("The frozen official benchmark does not match expected field mismatch counts")
    if official["metrics"]["finding_metrics"]["predicted_finding_count"] - official["metrics"]["finding_metrics"]["overlap_match"] != 10:
        raise ValueError("The frozen official benchmark does not match expected finding FP count")

    field_cases, paper_refs = [], {}
    for uid in sorted(gold):
        record, gold_record, prediction = canonical[uid], gold[uid], predictions.get(uid)
        paper_ref = f"P{len(paper_refs) + 1:02d}"
        paper_refs[uid] = paper_ref
        abstract = record.get("abstract") or ""
        for field in KEY_FIELDS:
            gold_items = item_objects(gold_record, field)
            pred_items = item_objects(prediction, field) if prediction else []
            gmap = {normalize_item(x["value"]): x for x in gold_items if normalize_item(x["value"])}
            pmap = {normalize_item(x["value"]): x for x in pred_items if normalize_item(x["value"])}
            for side, norms in (("FP", sorted(pmap.keys() - gmap.keys())), ("FN", sorted(gmap.keys() - pmap.keys()))):
                for norm in norms:
                    gold_target = gmap.get(norm)
                    pred_target = pmap.get(norm)
                    field_cases.append({
                        "case_id": f"F{len(field_cases)+1:03d}", "uid": uid, "doi": record.get("doi"),
                        "title": record.get("title"), "document_types": record.get("document_types", []),
                        "field": field, "strict_mismatch_side": side, "strict_normalized_value": norm,
                        "gold": gold_target, "prediction": pred_target,
                        "gold_same_field_items": gold_items, "prediction_same_field_items": pred_items,
                        "abstract_context": add_contexts(abstract, gold_target, pred_target, *gold_items, *pred_items),
                        "classification": None,
                    })
    if len(field_cases) != 75:
        raise ValueError(f"Expected 75 field mismatches, got {len(field_cases)}")

    finding_mismatches, substring_only, review_cases = [], [], []
    for uid in sorted(gold):
        record, gold_record, prediction = canonical[uid], gold[uid], predictions.get(uid)
        abstract = record.get("abstract") or ""
        gfind = gold_record.get("evidence", {}).get("findings", [])
        pfind = prediction.get("evidence", {}).get("findings", []) if prediction else []
        pairs, pred_only, gold_only = match_findings(gfind, pfind)
        for pair in pairs:
            if not pair["exact"]:
                gitem, pitem = gfind[pair["gold_index"]], pfind[pair["prediction_index"]]
                substring_only.append({
                    "case_id": f"S{len(substring_only)+1:02d}", "paper_ref": paper_refs[uid],
                    "uid": uid, "doi": record.get("doi"), "title": record.get("title"),
                    "gold": anchor_for(gold_record, "/evidence/findings", item=gitem) | {"claim": gitem.get("claim")},
                    "prediction": anchor_for(prediction, "/evidence/findings", item=pitem) | {"claim": pitem.get("claim")},
                    "abstract_context": add_contexts(abstract, {"anchor": gitem}, {"anchor": pitem}),
                    "semantic_equivalence": None,
                })
        gold_candidate_findings = [anchor_for(gold_record, "/evidence/findings", item=x) | {"claim": x.get("claim")} for x in gfind]
        pred_candidate_findings = [anchor_for(prediction, "/evidence/findings", item=x) | {"claim": x.get("claim")} for x in pfind]
        for index in pred_only:
            pred_item = pred_candidate_findings[index]
            finding_mismatches.append({
                "case_id": f"D{len(finding_mismatches)+1:02d}", "uid": uid, "doi": record.get("doi"),
                "title": record.get("title"), "document_types": record.get("document_types", []),
                "mismatch_side": "PREDICTED_ONLY", "gold": None, "prediction": pred_item,
                "gold_candidate_findings": gold_candidate_findings, "prediction_findings_in_record": pred_candidate_findings,
                "abstract_context": add_contexts(abstract, pred_item, *gold_candidate_findings), "classification": None,
            })
        for index in gold_only:
            gold_item = gold_candidate_findings[index]
            finding_mismatches.append({
                "case_id": f"D{len(finding_mismatches)+1:02d}", "uid": uid, "doi": record.get("doi"),
                "title": record.get("title"), "document_types": record.get("document_types", []),
                "mismatch_side": "GOLD_ONLY", "gold": gold_item, "prediction": None,
                "gold_findings_in_record": gold_candidate_findings, "prediction_candidate_findings": pred_candidate_findings,
                "abstract_context": add_contexts(abstract, gold_item, *pred_candidate_findings), "classification": None,
            })
        for flag in audit_evidence(prediction, record):
            if flag.get("flag") != "review_language":
                continue
            pointer = flag.get("field", "")
            support = (prediction.get("evidence_support") or {}).get(pointer)
            if pointer.startswith("/evidence/findings/"):
                finding_index = int(pointer.rsplit("/", 1)[-1])
                predicted_item = pred_candidate_findings[finding_index]
                field = "/evidence/findings"
            else:
                field = pointer.rsplit("/", 1)[0] if pointer.rsplit("/", 1)[-1].isdigit() else pointer
                pred_candidate = flag.get("value")
                predicted_item = {"value": pred_candidate,
                                  "anchor": {k: support.get(k) for k in ("source", "evidence_text", "start", "end")} if support else None}
            review_cases.append({
                "case_id": f"R{len(review_cases)+1:02d}", "uid": uid, "doi": record.get("doi"),
                "title": record.get("title"), "document_types": record.get("document_types", []),
                "field": field, "prediction": predicted_item,
                "gold_same_field_items": item_objects(gold_record, field) if field != "/evidence/findings" else gold_candidate_findings,
                "abstract_context": add_contexts(abstract, predicted_item), "classification": None,
            })
    if len(finding_mismatches) != 12 or len(substring_only) != 4:
        raise ValueError(f"Expected 12 finding mismatches and 4 substring-only matches; got {len(finding_mismatches)}, {len(substring_only)}")
    if len(review_cases) != 17:
        raise ValueError(f"Expected 17 review-language audit flags, got {len(review_cases)}")

    audit = {
        "benchmark_version": "v06_strict_lexical_v1", "official_benchmark": str(benchmark_path),
        "field_mismatch_count": len(field_cases), "field_false_positive_count": sum(x["strict_mismatch_side"] == "FP" for x in field_cases),
        "field_false_negative_count": sum(x["strict_mismatch_side"] == "FN" for x in field_cases),
        "finding_mismatch_count": len(finding_mismatches), "finding_predicted_only_count": sum(x["mismatch_side"] == "PREDICTED_ONLY" for x in finding_mismatches),
        "finding_gold_only_count": sum(x["mismatch_side"] == "GOLD_ONLY" for x in finding_mismatches),
        "finding_overlap_count": official["metrics"]["finding_metrics"]["overlap_match"],
        "finding_exact_count": official["metrics"]["finding_metrics"]["exact_evidence_text_match"],
        "substring_only_matches": substring_only, "finding_mismatches": finding_mismatches,
        "field_mismatches": field_cases, "review_as_experiment_flags": review_cases,
    }
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    write_json(out / "v06_field_discrepancies_20261004.json", audit)
    write_json(out / "v06_finding_discrepancies_20261004.json", {
        "benchmark_version": audit["benchmark_version"], "mismatches": finding_mismatches,
        "substring_only_matches": substring_only,
    })
    metric_audit_report(out / "v06_metric_audit_20261004.md")
    field_lines = ["# v0.6 field discrepancy packet", "", "Official score remains unchanged: field micro-F1 0.599; this packet is post-hoc diagnosis only.",
                   "", f"Cases: {len(field_cases)} (FP={audit['field_false_positive_count']}, FN={audit['field_false_negative_count']}). Labels are unadjudicated until both independent reviewers agree.", "",
                   "| Case | UID | DOI | Field | Side | Gold value/support | Prediction value/support | Abstract context |", "|---|---|---|---|---|---|---|---|"]
    for x in field_cases:
        def cell(obj):
            if not obj: return "—"
            a = obj.get("anchor") or {}
            return f"{obj.get('value')!r}; {a.get('evidence_text')!r} [{a.get('start')},{a.get('end')}]"
        field_lines.append(f"| {x['case_id']} | {x['uid']} | {x['doi']} | `{x['field']}` | {x['strict_mismatch_side']} | {cell(x['gold'])} | {cell(x['prediction'])} | {markdown_context(x['abstract_context'])} |")
    (out / "v06_field_discrepancies_20261004.md").write_text("\n".join(field_lines) + "\n", encoding="utf-8")
    finding_lines = ["# v0.6 finding discrepancy packet", "", "Official finding score remains unchanged: precision 0.756, recall 0.939.", "",
                     f"Unmatched cases: {len(finding_mismatches)}; substring-only matches to inspect separately: {len(substring_only)}.", "",
                     "## Unmatched findings", "", "| Case | UID | Side | Gold item | Prediction item | Abstract context |", "|---|---|---|---|---|---|"]
    for x in finding_mismatches:
        def finding_cell(obj):
            if not obj: return "—"
            return f"{obj.get('evidence_text')!r}; [{obj.get('start')},{obj.get('end')}]"
        finding_lines.append(f"| {x['case_id']} | {x['uid']} | {x['mismatch_side']} | {finding_cell(x['gold'])} | {finding_cell(x['prediction'])} | {markdown_context(x['abstract_context'])} |")
    finding_lines.extend(["", "## Substring-only matched pairs", "", "| Case | Gold span | Prediction span | Abstract context |", "|---|---|---|---|"])
    for x in substring_only:
        finding_lines.append(f"| {x['case_id']} | {x['gold'].get('evidence_text')!r} | {x['prediction'].get('evidence_text')!r} | {markdown_context(x['abstract_context'])} |")
    (out / "v06_finding_discrepancies_20261004.md").write_text("\n".join(finding_lines) + "\n", encoding="utf-8")

    schema = read_json(root / "schemas/evidence_matrix.schema.json")
    evidence_schema = schema["properties"]["evidence"]["properties"]
    relevant = {}
    for path in sorted({x["field"] for x in field_cases}):
        name = path.removeprefix("/evidence/")
        fragment = evidence_schema
        for part in name.split("/"):
            fragment = fragment["properties"][part] if "properties" in fragment else fragment[part]
        relevant[path] = fragment
    relevant["/evidence/findings/items"] = evidence_schema["findings"]
    review_definitions = {
        "REAL_REVIEW_AS_EXPERIMENT_ERROR": "The extraction attributes an item from literature discussed in a review to the review authors as their own treatment, scale, measurement, method, or experiment.",
        "AUDIT_FLAG_ONLY": "The field is a defensible abstract-grounded fact; the audit flag alone (including Review document type) does not demonstrate an author-attribution error.",
    }
    for reviewer in ("A", "B"):
        reviewer_path = Path(reviewer_dir) / f"reviewer_{reviewer}"
        cases_for_reviewer = []
        for group, rows, labels in (("field", field_cases, FIELD_DEFINITIONS), ("finding", finding_mismatches, FINDING_DEFINITIONS), ("review_flag", review_cases, review_definitions)):
            for row in rows:
                ref = paper_refs[row["uid"]]
                case = {"case_id": row["case_id"], "case_type": group, "paper_ref": ref,
                    "gold": row.get("gold"), "prediction": row.get("prediction"),
                    "gold_candidates": row.get("gold_same_field_items") or row.get("gold_candidate_findings") or row.get("gold_findings_in_record"),
                    "prediction_candidates": row.get("prediction_same_field_items") or row.get("prediction_candidate_findings") or row.get("prediction_findings_in_record"),
                    "classification_definitions": labels}
                if "field" in row:
                    case["field"] = row["field"]
                if group == "finding" and row.get("mismatch_side") == "GOLD_ONLY":
                    case["classification_definitions"] = {key: FINDING_DEFINITIONS[key] for key in
                        ("TRUE_MISSED_FINDING", "SPAN_MATCH_FAILURE", "GOLD_AMBIGUOUS")}
                elif group == "finding":
                    case["classification_definitions"] = {key: value for key, value in FINDING_DEFINITIONS.items()
                                                           if key not in ("TRUE_MISSED_FINDING", "SPAN_MATCH_FAILURE", "GOLD_AMBIGUOUS")}
                cases_for_reviewer.append(case)
        for row in substring_only:
            cases_for_reviewer.append({"case_id": row["case_id"], "case_type": "substring_pair", "paper_ref": row["paper_ref"],
                "gold": row["gold"], "prediction": row["prediction"],
                "classification_definitions": {"SEMANTICALLY_EQUIVALENT": "Both quoted spans state the same finding in this abstract.", "NOT_EQUIVALENT": "The spans do not state the same finding.", "AMBIGUOUS": "The abstract does not settle equivalence."}})
        payload = {
            "instructions": "Independently adjudicate each case using only the abstract identified by paper_ref, the relevant JSON Schema fragment, Gold/prediction values and anchors, document type where supplied, and classification definitions. Do not browse or use outside knowledge. Return JSON with one decision per case_id: category, confidence (high/medium/low), and a concise source-based rationale. Do not calculate aggregate metrics or propose prompt changes.",
            "field_labels": FIELD_LABELS, "finding_labels": FINDING_LABELS,
            "review_labels": REVIEW_LABELS, "substring_labels": SUBSTRING_LABELS,
            "schema_definitions": relevant,
            "papers": [{"paper_ref": ref, "abstract": canonical[uid]["abstract"]} for uid, ref in sorted(paper_refs.items(), key=lambda item: item[1])],
            "cases": cases_for_reviewer,
        }
        reviewer_path.mkdir(parents=True, exist_ok=True)
        write_json(reviewer_path / "reviewer_input.json", payload)
        (reviewer_path / "TASK.md").write_text(
            "# Independent adjudication\n\nRead only `reviewer_input.json`. Write `reviewer_decisions.json` as `{'decisions':[{'case_id':'...','category':'...','confidence':'high|medium|low','rationale':'...'}]}`. Do not inspect any other path, search for Gold, compute scores, or communicate with the other reviewer.\n",
            encoding="utf-8")
    return audit


def merge_reviews(output_dir, reviewer_a_dir, reviewer_b_dir):
    out = Path(output_dir)
    field_path = out / "v06_field_discrepancies_20261004.json"
    finding_path = out / "v06_finding_discrepancies_20261004.json"
    field_packet, finding_packet = read_json(field_path), read_json(finding_path)
    input_path_a = Path(reviewer_a_dir) / "reviewer_input.json"
    input_path_b = Path(reviewer_b_dir) / "reviewer_input.json"
    expected_types = {case["case_id"]: case["case_type"] for case in read_json(input_path_a)["cases"]}
    if expected_types != {case["case_id"]: case["case_type"] for case in read_json(input_path_b)["cases"]}:
        raise ValueError("Reviewer input case sets differ")

    def load_decisions(path):
        data = read_json(path)
        rows = data.get("decisions", [])
        decisions = {row.get("case_id"): row for row in rows}
        if len(decisions) != len(rows) or set(decisions) != set(expected_types):
            raise ValueError(f"Reviewer decision coverage invalid: {path}")
        for case_id, row in decisions.items():
            group, label = expected_types[case_id], row.get("category")
            allowed = {"field": FIELD_LABELS, "finding": FINDING_LABELS,
                       "review_flag": REVIEW_LABELS, "substring_pair": SUBSTRING_LABELS}[group]
            if label not in allowed:
                raise ValueError(f"Invalid reviewer label for {case_id}: {label}")
        return decisions

    decisions_a = load_decisions(Path(reviewer_a_dir) / "reviewer_decisions.json")
    decisions_b = load_decisions(Path(reviewer_b_dir) / "reviewer_decisions.json")
    by_id = {}
    for row in field_packet["field_mismatches"] + field_packet["review_as_experiment_flags"] + finding_packet["mismatches"] + finding_packet["substring_only_matches"]:
        by_id[row["case_id"]] = row
    if set(by_id) != set(expected_types):
        raise ValueError("Reviewer cases do not match discrepancy packets")
    agreements = Counter()
    consensus_counts = {group: Counter() for group in ("field", "finding", "review_flag", "substring_pair")}
    unresolved = Counter()
    for case_id, group in expected_types.items():
        a, b = decisions_a[case_id], decisions_b[case_id]
        same = a["category"] == b["category"]
        agreements[group] += int(same)
        row = by_id[case_id]
        row["reviewer_A"] = {k: a.get(k) for k in ("category", "confidence", "rationale")}
        row["reviewer_B"] = {k: b.get(k) for k in ("category", "confidence", "rationale")}
        if same:
            row["adjudication_consensus"] = a["category"]
            consensus_counts[group][a["category"]] += 1
        else:
            row["adjudication_consensus"] = "NEEDS_HUMAN_REVIEW"
            unresolved[group] += 1
            consensus_counts[group]["NEEDS_HUMAN_REVIEW"] += 1
    write_json(field_path, field_packet)
    write_json(finding_path, finding_packet)

    by_field = defaultdict(Counter)
    for row in field_packet["field_mismatches"]:
        by_field[row["field"]][row["adjudication_consensus"]] += 1
    primary_cases = len(field_packet["field_mismatches"]) + len(finding_packet["mismatches"])
    primary_agreements = agreements["field"] + agreements["finding"]
    all_cases = len(expected_types)
    all_agreements = sum(agreements.values())
    scale_rows = [x for x in field_packet["field_mismatches"] if x["field"].endswith("experimental_scale") and x["strict_mismatch_side"] == "FN"]
    salinity_fp_rows = [x for x in field_packet["field_mismatches"] if x["field"].endswith("salinity_context") and x["strict_mismatch_side"] == "FP"]
    substring_rows = finding_packet["substring_only_matches"]
    salinity_scope_notes = {
        "F002": ("SITE_DESCRIPTOR", "The abstract identifies the selected WJP study area as a concentrated distribution area of soda saline-alkali land."),
        "F016": ("ACTUAL_STUDY_SYSTEM", "Field-based monitoring was conducted in the saline-affected paddy fields."),
        "F017": ("ACTUAL_STUDY_SYSTEM", "Field-based monitoring was conducted in the saline-affected upland fields."),
        "F039": ("ACTUAL_STUDY_SYSTEM", "The abstract states a pot experiment in saline-alkali paddy fields."),
        "F050": ("REGIONAL_RESEARCH_OBJECT", "Saline-alkali land dynamics are a central regional research object, but the cited sentence does not itself describe a measured experimental system."),
        "F061": ("REVIEW_SCOPE_OR_GENERIC_MECHANISM", "The Review discusses hydrogel behavior in saline/alkali-stressed environments without naming one empirical study system."),
        "F062": ("GENERIC_REVIEW_APPLICATION_CONTEXT", "The Review describes hydrogel use for amelioration in saline-alkali soils as a general application context."),
    }
    summary = {
        "benchmark_version": "v06_strict_lexical_v1",
        "official_scores_unchanged": {"field_micro_f1": 0.5989304812834225, "finding_precision": 0.7560975609756098, "finding_recall": 0.9393939393939394, "quality_gate": "FAIL"},
        "field_mismatches": {"total": len(field_packet["field_mismatches"]), "category_counts": dict(consensus_counts["field"]),
                             "by_field": {k: dict(v) for k, v in by_field.items()}},
        "finding_mismatches": {"total": len(finding_packet["mismatches"]), "category_counts": dict(consensus_counts["finding"])},
        "review_flags": {"total": len(field_packet["review_as_experiment_flags"]), "category_counts": dict(consensus_counts["review_flag"])},
        "substring_only_matches": {"total": len(substring_rows), "category_counts": dict(consensus_counts["substring_pair"])},
        "reviewer_agreement": {"primary_cases": primary_cases, "primary_agreements": primary_agreements,
                               "primary_rate": primary_agreements / primary_cases if primary_cases else 0,
                               "all_cases": all_cases, "all_agreements": all_agreements,
                               "all_rate": all_agreements / all_cases if all_cases else 0},
        "needs_human_review": {"primary": unresolved["field"] + unresolved["finding"],
                               "field": unresolved["field"], "finding": unresolved["finding"],
                               "review_flags": unresolved["review_flag"], "substring_pairs": unresolved["substring_pair"],
                               "all": sum(unresolved.values())},
        "experimental_scale_gold_fn": [{"case_id": x["case_id"], "uid": x["uid"], "doi": x["doi"],
            "gold_scale": x["gold"], "abstract_context": x["abstract_context"],
            "reviewer_A": x["reviewer_A"]["category"], "reviewer_B": x["reviewer_B"]["category"],
            "adjudication_consensus": x["adjudication_consensus"],
            "explicit_scale_phrase_present": any(t in " ".join(x["abstract_context"]).casefold() for t in
                ("field study", "field experiment", "field soil column", "field-based", "pot experiment", "pot study", "greenhouse experiment", "laboratory experiment", "incubation experiment", "logistic regression model", "plus model", "model-invest"))}
            for x in scale_rows],
        "salinity_context_false_positives": [{"case_id": x["case_id"], "uid": x["uid"], "doi": x["doi"],
            "prediction": x["prediction"], "abstract_context": x["abstract_context"],
            "reviewer_A": x["reviewer_A"]["category"], "reviewer_B": x["reviewer_B"]["category"],
            "adjudication_consensus": x["adjudication_consensus"],
            "controller_scope_assessment": salinity_scope_notes[x["case_id"]][0],
            "controller_scope_basis": salinity_scope_notes[x["case_id"]][1]} for x in salinity_fp_rows],
        "substring_only_pairs": [{"case_id": x["case_id"], "uid": x["uid"], "gold": x["gold"], "prediction": x["prediction"],
            "reviewer_A": x["reviewer_A"]["category"], "reviewer_B": x["reviewer_B"]["category"],
            "adjudication_consensus": x["adjudication_consensus"]} for x in substring_rows],
        "metric_v2_recommendation": "METRIC_V2_NEEDED" if consensus_counts["field"]["LEXICAL_BOUNDARY_MISMATCH"] >= 20 and consensus_counts["substring_pair"]["SEMANTICALLY_EQUIVALENT"] >= 3 else "METRIC_V1_KEEP",
    }
    write_json(out / "v06_gold_adjudication_summary_20261004.json", summary)
    lines = ["# v0.6 Gold error adjudication summary", "",
        "## Frozen official benchmark", "",
        "`benchmark_version = v06_strict_lexical_v1`; field micro-F1 **0.599**, finding precision **0.756**, finding recall **0.939**, official quality gate **FAIL**. No adjusted score is reported.", "",
        "## Field mismatch consensus", "", f"75 cases; reviewer agreement {primary_agreements}/{primary_cases} = {summary['reviewer_agreement']['primary_rate']:.1%} across field and finding cases.", "",
        "| Classification | Count |", "|---|---:|"]
    for category in FIELD_LABELS + ("NEEDS_HUMAN_REVIEW",):
        lines.append(f"| {category} | {consensus_counts['field'][category]} |")
    lines.extend(["", "### By field", "", "| Field | " + " | ".join(FIELD_LABELS) + " | NEEDS_HUMAN_REVIEW |", "|---|" + "---:|" * (len(FIELD_LABELS) + 1)])
    for field, counts in sorted(by_field.items()):
        lines.append("| `" + field + "` | " + " | ".join(str(counts[label]) for label in FIELD_LABELS) + " | " + str(counts["NEEDS_HUMAN_REVIEW"]) + " |")
    lines.extend(["", "## Finding mismatches", "", "| Classification | Count |", "|---|---:|"])
    for category in FINDING_LABELS + ("NEEDS_HUMAN_REVIEW",):
        lines.append(f"| {category} | {consensus_counts['finding'][category]} |")
    lines.extend(["", "## Experimental scale Gold FN", "", "| UID | Gold scale | Source quote | A | B | Consensus | Explicit phrase |", "|---|---|---|---|---|---|---|"])
    for x in summary["experimental_scale_gold_fn"]:
        anchor = (x["gold_scale"] or {}).get("anchor") or {}
        lines.append(f"| {x['uid']} | {x['gold_scale'].get('value')} | {anchor.get('evidence_text')} | {x['reviewer_A']} | {x['reviewer_B']} | {x['adjudication_consensus']} | {x['explicit_scale_phrase_present']} |")
    lines.extend(["", "## Salinity-context false positives", "", "A/B category agreement records whether the prediction is supported under the current broad field definition. The separate controller scope note asks whether it describes a concrete study system; it is not substituted for reviewer consensus.", "", "| UID | Predicted context | Abstract sentence | A/B consensus | Context type |", "|---|---|---|---|---|"])
    for x in summary["salinity_context_false_positives"]:
        lines.append(f"| {x['uid']} | {x['prediction']['value']} | {' / '.join(x['abstract_context'])} | {x['adjudication_consensus']} | {x['controller_scope_assessment']} — {x['controller_scope_basis']} |")
    lines.extend(["", "## Review audit flags", "", f"17 flags: REAL_REVIEW_AS_EXPERIMENT_ERROR={consensus_counts['review_flag']['REAL_REVIEW_AS_EXPERIMENT_ERROR']}; AUDIT_FLAG_ONLY={consensus_counts['review_flag']['AUDIT_FLAG_ONLY']}; NEEDS_HUMAN_REVIEW={unresolved['review_flag']}.",
        "The code-level audit confirmed that a `Review` document type by itself can emit `review_language`; that flag is not proof that the prediction attributes experiments to the review authors.",
        "", "## Substring-only finding matches", "", "| Case | Gold span | Prediction span | A | B | Consensus |", "|---|---|---|---|---|---|"])
    for x in substring_rows:
        lines.append(f"| {x['case_id']} | {x['gold']['evidence_text']} | {x['prediction']['evidence_text']} | {x['reviewer_A']['category']} | {x['reviewer_B']['category']} | {x['adjudication_consensus']} |")
    lines.extend(["", f"A/B agreement across all {all_cases} cases: {all_agreements}/{all_cases} = {summary['reviewer_agreement']['all_rate']:.1%}.",
        f"`NEEDS_HUMAN_REVIEW`: {summary['needs_human_review']['primary']} primary field/finding cases; {summary['needs_human_review']['all']} including audit and substring cases.",
        "", f"Metric recommendation: **{summary['metric_v2_recommendation']}**. This is a recommendation to evaluate a separately versioned diagnostic metric; it does not alter v1 scores.",
        "", "## Prompt iteration 2 recommendations (not applied)", "",
        "1. Salinity context: operationalize this as salinity of the paper's actual study system/site, and distinguish site descriptors from regional motivation and review scope. All 7 strict FPs were independently classified as Gold omission/inconsistency under the current broad field definition; five are study-area/site or central regional context (for example `WOS:001602265100001`, `WOS:001713822800001`), while two values in Review `WOS:001831944400001` describe generic application/stress contexts rather than a single empirical system. Require an explicit link to the studied system and preserve Review attribution.",
        "2. Measurement/method ontology: state category boundaries and use the narrowest supported labels; 21 measurement mismatches were unanimous lexical-boundary cases, including microbial (7), other (6), nitrogen (2), yield (2), plant growth (2), and soil chemistry (2). Methods had 10 mismatches: 4 lexical-boundary cases, 4 reviewer disagreements, 1 true FP, and 1 Gold omission. This is primarily strict-string/category-boundary behavior; define when a method name versus an entire study-design phrase is the value. Examples: `WOS:001631731300001`, `WOS:001713822800001`.",
        "3. Experimental scale: map explicit phrases to the fixed schema (`field` from `field study`/`field-based`/`field soil column experiment`, `pot` from `pot experiment`, and `model` from stated modeling methods); all 6 Gold-only scale items were explicit in the abstract and both reviewers labeled them TRUE_FN. Examples: `WOS:001487641900001`, `WOS:001602265100001`, `WOS:001859510400001`.",
        "4. Treatment boundaries: separate applied treatment arms from observational drivers and named scenarios; adjudication found 2 Gold-omission cases each in irrigation and water_regime, 4 Gold-omission plus 1 category mismatch in other_treatments, and 1 amendment category mismatch. State where irrigation mode belongs and keep climate, land-use scenario, and observed conditions out of treatment arms. Examples: `WOS:001487641900001`, `WOS:001798349200001`.",
        "5. Review handling: attribute claims to the paper's own contribution versus cited/reviewed studies. All 17 review-language flags were adjudicated AUDIT_FLAG_ONLY; `audit.py` can emit the flag from Review document type alone, so do not count it as an extraction error without author-attribution evidence. Example: `WOS:001831944400001`.",
        "6. Findings: keep abstract-supported results, consolidate repeated claims, and exclude implications. Among the 10 predicted-only findings, reviewers identified 6 Gold omissions, 3 duplicates, and 1 implication; the 2 Gold-only cases were 1 span-match failure and 1 true missed finding. Examples: `WOS:001831944400001`, `WOS:001859510400001`.",
        "", "No prompt, schema, Gold, response, official benchmark, metric implementation, or quality threshold was modified. No pilot or iteration 2 was started."])
    (out / "v06_gold_adjudication_summary_20261004.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--batch-dir", type=Path, default=Path("data/evidence_batches/v06_gold_blind_iteration1_20261004/batch_001"))
    parser.add_argument("--canonical", type=Path, default=Path("data/processed/saline_paddy_v056_full_enriched_20261003.jsonl"))
    parser.add_argument("--gold", type=Path, default=Path("data/evidence/20261003_192326_169425_evidence_57015bbc_evidence.jsonl"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/reports/v06_gold_benchmark_20261004_123933.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/reports"))
    parser.add_argument("--reviewer-dir", type=Path, default=Path("/tmp/v06_gold_adjudication_20261004"))
    parser.add_argument("--merge-reviewers", nargs=2, metavar=("REVIEWER_A_DIR", "REVIEWER_B_DIR"))
    args = parser.parse_args()
    if args.merge_reviewers:
        result = merge_reviews(args.output_dir, *args.merge_reviewers)
        print(json.dumps({"reviewer_agreement": result["reviewer_agreement"],
                          "needs_human_review": result["needs_human_review"],
                          "metric_v2_recommendation": result["metric_v2_recommendation"]}, ensure_ascii=False))
        return 0
    result = build_packets(args.root, args.batch_dir, args.canonical, args.gold, args.benchmark, args.output_dir, args.reviewer_dir)
    print(json.dumps({k:result[k] for k in ("benchmark_version", "field_mismatch_count", "field_false_positive_count", "field_false_negative_count", "finding_mismatch_count", "finding_predicted_only_count", "finding_gold_only_count", "finding_overlap_count", "finding_exact_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
