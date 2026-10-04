"""Validate and score a prompt iteration against the human-adjudicated dev Gold only."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from scripts.evidence.benchmark import benchmark
from scripts.evidence.metrics_v2 import corpus_metrics_v2
from scripts.evidence.prepare_dev_iteration2 import DEFAULT_OUTPUT
from scripts.pipeline_utils import ROOT, read_jsonl, write_json, write_jsonl


FINAL_GOLD = Path("data/evidence_benchmarks/gold_v04_development_final/candidate_gold.jsonl")
REPORT_DIR = Path("data/reports/v06_iteration2_20261004")
ITER1_DIR = Path("data/evidence_batches/v06_gold_blind_iteration1_20261004")
ITER2_DIR = Path("data/evidence_batches/v06_gold_blind_iteration2_20261004")


def _canonical_record(request: dict) -> dict:
    return {
        "uid": request["uid"], "doi": request["doi"], "title": request["title"],
        "abstract": request.get("abstract"), "authors": request.get("authors") or [],
        "source_title": request.get("journal"), "publish_year": request.get("year"),
        "author_keywords": request.get("keywords") or [],
        "document_types": request.get("document_types") or [],
        "matched_queries": [], "query_match_count": 0, "provenance_history": [],
    }


def _stage_bundle_responses(batch_dir: Path, worker_bundle: Path, iteration: int) -> int:
    manifest_path = batch_dir / "batch_manifest.json"
    batch = json.loads(manifest_path.read_text(encoding="utf-8"))
    copied = 0
    for item in batch["requests"]:
        name = Path(item["response_file"]).name
        source = worker_bundle / "responses" / name
        target = batch_dir / "responses" / name
        if not source.is_file():
            raise ValueError("Worker response is missing")
        envelope = json.loads(source.read_text(encoding="utf-8"))
        if any(envelope.get(key) != item.get(key) for key in ("payload_sha256", "request_sha256")):
            raise ValueError("Worker envelope does not match development request manifest")
        if target.exists():
            if target.read_bytes() != source.read_bytes():
                raise ValueError("Existing development response differs from the validated worker response")
        else:
            shutil.copyfile(source, target)
        item["response_valid"] = True
        item["status"] = f"iteration{iteration}_worker_protocol_valid"
        copied += 1
    if copied != 7:
        raise ValueError("Expected exactly seven development response envelopes")
    manifest_path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_manifest_path = batch_dir.parent / "manifest.json"
    run = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    run["status"] = "responses_complete"
    run["worker"]["responses_received"] = copied
    run["worker"]["protocol_valid"] = copied
    run_manifest_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return copied


def _run_metrics(run_dir: Path, canonical_path: Path, gold_path: Path,
                 report_dir: Path, root: Path):
    result = benchmark(run_dir, canonical_path, gold_path, root=root, report_dir=report_dir)
    metrics = result["metrics"]
    return {
        "v1_report": result["json"], "metrics": metrics,
        "quality_gate": result["quality_gate"],
    }


def evaluate(root: Path = ROOT, run_dir: Path = DEFAULT_OUTPUT,
             worker_bundle: Path = Path("/tmp/wos-v06-i2-worker-20261004-validated"),
             gold_path: Path = FINAL_GOLD, report_dir: Path = REPORT_DIR,
             compare_run: Path = ITER1_DIR):
    root = root.resolve()
    run_dir = run_dir if run_dir.is_absolute() else root / run_dir
    worker_bundle = worker_bundle.resolve()
    run_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    iteration = run_manifest.get("prompt_iteration")
    if iteration not in (2, 3):
        raise ValueError("Only Prompt iteration 2 or 3 can be evaluated by this development evaluator")
    batch_dir = run_dir / "batch_001"
    checker = worker_bundle / "validate_worker_outputs.py"
    if not checker.is_file() or not (worker_bundle / "TASK.md").is_file():
        raise FileNotFoundError("Validated ephemeral worker bundle is required")
    import subprocess
    check = subprocess.run([__import__("sys").executable, str(checker)], cwd=worker_bundle,
                           text=True, capture_output=True, check=False)
    marker = f"BLIND_WORKER_ITER{iteration}_SUCCESS requests=7 responses=7 protocol_valid=7 inference_empty=7"
    if check.returncode != 0 or marker not in check.stdout:
        raise ValueError(f"Iteration-{iteration} worker bundle failed its response checker")
    response_count = _stage_bundle_responses(batch_dir, worker_bundle, iteration)

    batch = json.loads((batch_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    canonical = []
    for item in batch["requests"]:
        request = json.loads((root / item["request_file"]).read_text(encoding="utf-8"))
        if request.get("uid") != item.get("uid"):
            raise ValueError("Development request identity mismatch")
        canonical.append(_canonical_record(request))
    if len(canonical) != 7 or len({row["uid"] for row in canonical}) != 7:
        raise ValueError(f"Iteration {iteration} must contain exactly seven unique development records")
    gold_rows = read_jsonl(gold_path if gold_path.is_absolute() else root / gold_path)
    if len(gold_rows) != 7 or {row["uid"] for row in gold_rows} != {row["uid"] for row in canonical}:
        raise ValueError("Final development Gold identity set differs from the worker requests")

    eval_dir = run_dir / "evaluation"
    eval_dir.mkdir(exist_ok=True)
    canonical_path = eval_dir / "canonical_development_only.jsonl"
    if canonical_path.exists():
        if read_jsonl(canonical_path) != canonical:
            raise ValueError("Existing development-only canonical file differs from the seven requests")
    else:
        write_jsonl(canonical_path, canonical)
    current_v1 = _run_metrics(run_dir, canonical_path, gold_path, report_dir / f"iteration{iteration}_v1", root)

    # The comparison baseline is another manifested seven-paper development run.
    baseline_run = compare_run if compare_run.is_absolute() else root / compare_run
    baseline_manifest = json.loads((baseline_run / "manifest.json").read_text(encoding="utf-8"))
    baseline_iteration = baseline_manifest.get("prompt_iteration")
    baseline_v1 = _run_metrics(baseline_run, canonical_path, gold_path,
                               report_dir / f"iteration{baseline_iteration}_rescored_final_dev_gold_v1", root)

    papers = []
    for uid in sorted(row["uid"] for row in canonical):
        response_path = next((batch_dir / "responses" / f"{item['payload_sha256']}.json"
                              for item in batch["requests"] if item["uid"] == uid), None)
        request = next(row for row in canonical if row["uid"] == uid)
        env = json.loads(response_path.read_text(encoding="utf-8"))
        papers.append((uid, next(row for row in gold_rows if row["uid"] == uid),
                       env["response"], request["abstract"]))
    current_v2 = corpus_metrics_v2(papers)

    baseline_batch = json.loads((baseline_run / "batch_001/batch_manifest.json").read_text(encoding="utf-8"))
    canonical_by_uid = {row["uid"]: row for row in canonical}
    baseline_papers = []
    for uid in sorted(canonical_by_uid):
        item = next(entry for entry in baseline_batch["requests"] if entry["uid"] == uid)
        env = json.loads((baseline_run / "batch_001/responses" / f"{item['payload_sha256']}.json").read_text(encoding="utf-8"))
        baseline_papers.append((uid, next(row for row in gold_rows if row["uid"] == uid),
                                env["response"], canonical_by_uid[uid]["abstract"]))
    baseline_v2 = corpus_metrics_v2(baseline_papers)

    def field_v1(result, suffix):
        return result["metrics"]["field_metrics"]["/evidence/study_system/" + suffix]

    def field_v2(result, suffix):
        path = "/evidence/study_system/" + suffix
        aggregate = {key: 0 for key in ("exact_tp", "boundary_tp", "fp", "fn", "gold_count", "predicted_count")}
        for item in result["per_record"].values():
            metric = item["fields"]["field_metrics"][path]
            for key in aggregate:
                aggregate[key] += metric[key]
        tp = aggregate["exact_tp"] + aggregate["boundary_tp"]
        precision = tp / (tp + aggregate["fp"]) if tp + aggregate["fp"] else (1.0 if not aggregate["gold_count"] else 0.0)
        recall = tp / (tp + aggregate["fn"]) if tp + aggregate["fn"] else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {**aggregate, "precision": precision, "recall": recall, "f1": f1}

    current_key = f"iteration{iteration}"
    baseline_key = f"iteration{baseline_iteration}"
    summary = {
        "evaluation": f"v0.6 Prompt iteration {iteration} development diagnostic",
        "development_set_only": True, "independent_performance": False,
        "holdout_read": False, "pilot_started": False,
        "prompt_iteration": iteration,
        "record_count": 7, "worker_responses": response_count,
        "prompt_sha256": batch["prompt_sha256"], "schema_sha256": batch["schema_sha256"],
        "contract_version": "Evidence Field Contract v1",
        "metric_v1_name": "v06_strict_lexical_v1",
        "metric_v2_name": "v0.6.1 calibrated source-boundary v2",
        "baseline_iteration": baseline_iteration,
        "baseline_rescored_on_final_development_gold": {
            "v1": baseline_v1["metrics"], "v2": baseline_v2,
        },
        current_key: {"v1": current_v1["metrics"], "v1_quality_gate": current_v1["quality_gate"], "v2": current_v2},
        "comparison": {
            "baseline_key": baseline_key,
            "v1_finding_precision_delta": current_v1["metrics"]["finding_metrics"]["precision"] - baseline_v1["metrics"]["finding_metrics"]["precision"],
            "v2_finding_precision_delta": current_v2["findings"]["precision"] - baseline_v2["findings"]["precision"],
            "v1_scale": field_v1(current_v1, "experimental_scale"),
            "v1_scale_baseline": field_v1(baseline_v1, "experimental_scale"),
            "v1_salinity_context": field_v1(current_v1, "salinity_context"),
            "v1_salinity_context_baseline": field_v1(baseline_v1, "salinity_context"),
            "v2_scale": field_v2(current_v2, "experimental_scale"),
            "v2_scale_baseline": field_v2(baseline_v2, "experimental_scale"),
            "v2_salinity_context": field_v2(current_v2, "salinity_context"),
            "v2_salinity_context_baseline": field_v2(baseline_v2, "salinity_context"),
        },
        "v1_report": current_v1["v1_report"],
    }
    report_dir = report_dir if report_dir.is_absolute() else root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"iteration{iteration}_development_metrics.json"
    write_json(report_path, summary)
    md = [
        f"# Prompt iteration {iteration} — development-only evaluation", "",
        "This is a seven-paper development diagnostic, not independent performance. The frozen 20-paper holdout was not read.", "",
        f"- Prompt SHA-256: `{summary['prompt_sha256']}`",
        f"- Schema SHA-256: `{summary['schema_sha256']}` (unchanged)",
        f"- Strict v1 finding precision/recall: {current_v1['metrics']['finding_metrics']['precision']:.3f}/{current_v1['metrics']['finding_metrics']['recall']:.3f}",
        f"- Calibrated v2 finding precision/recall: {current_v2['findings']['precision']:.3f}/{current_v2['findings']['recall']:.3f}",
        f"- Strict v1 scale precision/recall: {field_v1(current_v1, 'experimental_scale')['precision']:.3f}/{field_v1(current_v1, 'experimental_scale')['recall']:.3f}",
        f"- Strict v1 salinity-context precision/recall: {field_v1(current_v1, 'salinity_context')['precision']:.3f}/{field_v1(current_v1, 'salinity_context')['recall']:.3f}",
        f"- Strict v1 salinity-context false positives: {field_v1(current_v1, 'salinity_context')['fp']}",
        f"- Structural validity: schema {current_v1['metrics']['structural_validity']['schema_valid_rate']:.3f}, grounding {current_v1['metrics']['structural_validity']['grounding_valid_rate']:.3f}, unsupported {current_v1['metrics']['unsupported_field_count'] + current_v1['metrics']['unsupported_finding_count']}, invalid offsets {current_v1['metrics']['invalid_offset_count']}, orphan anchors {current_v1['metrics']['orphan_anchor_count']}",
        "", "All scores are development tuning diagnostics. No holdout or pilot was run.", "",
    ]
    (report_dir / f"iteration{iteration}_development_metrics.md").write_text("\n".join(md), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--worker-bundle", type=Path, default=Path("/tmp/wos-v06-i2-worker-20261004-validated"))
    parser.add_argument("--compare-run", type=Path, default=ITER1_DIR)
    parser.add_argument("--gold", type=Path, default=FINAL_GOLD)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()
    result = evaluate(args.root, args.run_dir, args.worker_bundle, args.gold,
                      args.report_dir, args.compare_run)
    key = f"iteration{result['prompt_iteration']}"
    print(json.dumps({"prompt_sha256": result["prompt_sha256"],
                      "v1_finding_metrics": result[key]["v1"]["finding_metrics"],
                      "v2_finding_metrics": result[key]["v2"]["findings"],
                      "report": str(args.report_dir / f"{key}_development_metrics.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
