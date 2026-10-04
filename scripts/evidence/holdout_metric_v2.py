"""Calculate the frozen calibrated v2 metric for one validated holdout batch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.evidence.batch_ingest import read_batch_responses
from scripts.evidence.metrics_v2 import corpus_metrics_v2
from scripts.pipeline_utils import ROOT, project_path, read_jsonl, write_json


def run(batch_dir, canonical_input, gold_file, report_dir="data/reports", root=ROOT, run_id="frozen"):
    root = Path(root).resolve()
    canonical = read_jsonl(project_path(canonical_input, root))
    gold_rows = read_jsonl(project_path(gold_file, root))
    canonical_by_uid = {r["uid"]: r for r in canonical}
    gold_by_uid = {r["uid"]: r for r in gold_rows}
    extracted, validation, _ = read_batch_responses(batch_dir, canonical, root)
    if len(gold_by_uid) != 20 or len(extracted) != 20 or any(not validation.get(uid, {}).get("accepted") for uid in gold_by_uid):
        raise ValueError("v2_requires_20_fully_validated_responses")
    papers = [(uid, gold_by_uid[uid], extracted[uid]["evidence"], canonical_by_uid[uid]["abstract"])
              for uid in gold_by_uid]
    metrics = corpus_metrics_v2(papers)
    report = {"metric_version": "v0.6.1_calibrated_source_boundary_v2",
              "evaluation_role": "historical_independent_holdout", "records": 20,
              "fields": metrics["fields"], "findings": metrics["findings"],
              "experimental_scale": metrics["per_record"], "per_record": metrics["per_record"]}
    # Keep a compact field-level summary for the two predeclared sensitive fields.
    for pointer, label in (("/evidence/study_system/experimental_scale", "experimental_scale"),
                           ("/evidence/study_system/salinity_context", "salinity_context")):
        counts = {key: 0 for key in ("exact_tp", "boundary_tp", "fp", "fn", "gold_count", "predicted_count")}
        for record in metrics["per_record"].values():
            for key in counts:
                counts[key] += record["fields"]["field_metrics"][pointer][key]
        tp = counts["exact_tp"] + counts["boundary_tp"]
        precision = tp / (tp + counts["fp"]) if tp + counts["fp"] else (1.0 if not counts["gold_count"] else 0.0)
        recall = tp / (tp + counts["fn"]) if tp + counts["fn"] else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        report[label] = {**counts, "precision": precision, "recall": recall, "f1": f1}
    report["findings"] = {**metrics["findings"],
                          "exact": metrics["findings"]["exact_match"],
                          "boundary": metrics["findings"]["boundary_match"],
                          "fp": metrics["findings"]["unmatched_prediction"],
                          "fn": metrics["findings"]["unmatched_gold"],
                          "f1": (2 * metrics["findings"]["precision"] * metrics["findings"]["recall"] /
                                 (metrics["findings"]["precision"] + metrics["findings"]["recall"]))
                                if metrics["findings"]["precision"] + metrics["findings"]["recall"] else 0.0}
    field_paths = next(iter(metrics["per_record"].values()))["fields"]["field_metrics"]
    aggregated = {}
    for pointer in field_paths:
        counts = {key: 0 for key in ("exact_tp", "boundary_tp", "fp", "fn", "gold_count", "predicted_count")}
        for record in metrics["per_record"].values():
            for key in counts:
                counts[key] += record["fields"]["field_metrics"][pointer][key]
        tp = counts["exact_tp"] + counts["boundary_tp"]
        p = tp / (tp + counts["fp"]) if tp + counts["fp"] else (1.0 if not counts["gold_count"] else 0.0)
        r = tp / (tp + counts["fn"]) if tp + counts["fn"] else 1.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        aggregated[pointer] = {**counts, "precision": p, "recall": r, "f1": f1}
    report["fields"] = {**report["fields"], "field_metrics": aggregated}
    out = project_path(report_dir, root)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"v06_holdout20_metric_v2_{run_id}.json"
    md_path = out / f"v06_holdout20_metric_v2_{run_id}.md"
    write_json(json_path, report)
    lines = ["# Frozen Holdout Metric v2", "", "Records: 20", "", "## Aggregate fields", "",
             f"Exact TP: {report['fields']['exact_tp']}; boundary TP: {report['fields']['boundary_tp']}; FP: {report['fields']['fp']}; FN: {report['fields']['fn']}",
             f"Precision / recall / F1: {report['fields']['precision']:.4f} / {report['fields']['recall']:.4f} / {report['fields']['f1']:.4f}", "",
             "## Findings", "",
             f"Exact: {report['findings']['exact']}; boundary: {report['findings']['boundary']}; FP: {report['findings']['fp']}; FN: {report['findings']['fn']}",
             f"Precision / recall / F1: {report['findings']['precision']:.4f} / {report['findings']['recall']:.4f} / {report['findings']['f1']:.4f}", ""]
    for label in ("experimental_scale", "salinity_context"):
        item = report[label]
        lines.extend([f"## {label}", "", f"Exact TP: {item['exact_tp']}; boundary TP: {item['boundary_tp']}; FP: {item['fp']}; FN: {item['fn']}",
                      f"Precision / recall / F1: {item['precision']:.4f} / {item['recall']:.4f} / {item['f1']:.4f}", ""])
    lines.extend(["## Fields by contract path", "", "| Field | Exact TP | Boundary TP | FP | FN | P | R | F1 |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for pointer, item in report["fields"]["field_metrics"].items():
        lines.append(f"| `{pointer}` | {item['exact_tp']} | {item['boundary_tp']} | {item['fp']} | {item['fn']} | {item['precision']:.4f} | {item['recall']:.4f} | {item['f1']:.4f} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path), "fields": report["fields"],
            "findings": report["findings"], "experimental_scale": report["experimental_scale"],
            "salinity_context": report["salinity_context"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir")
    parser.add_argument("--canonical-input", required=True)
    parser.add_argument("--gold-evidence", required=True)
    parser.add_argument("--report-dir", default="data/reports")
    parser.add_argument("--run-id", default="frozen")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.batch_dir, args.canonical_input, args.gold_evidence,
                             args.report_dir, run_id=args.run_id), ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"Metric v2 failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
