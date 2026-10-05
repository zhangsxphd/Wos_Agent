# v0.7 Dev40 frozen-prompt baseline

Phase 2A evaluates the exact v0.6 extraction prompt on the 40-paper, human-approved Dev40 development set. Results are development diagnostics only and must not be described as independent performance.

The prompt SHA-256 is `f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1`. Evidence Matrix schema v0.4, Field Contract v2.1, Validator v2, strict Metric v1, and calibrated Metric v2 are hash-pinned by `scripts/evidence/run_v07_dev40_baseline.py`. This baseline does not revise any of them.

The controller scans canonical-record metadata while skipping abstract values. It decodes abstract, author, and keyword values only for UIDs in the frozen Dev40 registry. The isolated worker bundle contains the prompt, schema, 40 request files, task/instructions, a local checker, and manifests. It excludes the approved Gold, both Field Contract files, validators, metrics, previous responses, and future-holdout material. Gold and abstract data remain controller-side. The worker is started once in a fresh ephemeral context with network access disabled; a failed or incomplete worker run is not retried or repaired.

To execute the staged baseline from the repository root:

```bash
python3 scripts/evidence/run_v07_dev40_baseline.py prepare
python3 scripts/evidence/run_v07_dev40_baseline.py worker
python3 scripts/evidence/run_v07_dev40_baseline.py evaluate
```

The local worker bundle is saved under ignored `data/evidence_batches/v07_dev40_baseline/batch_001/`. Response SHA-256 values and controller-side Gold integrity are recorded outside that bundle. Detailed diagnostics are written to `data/reports/v07_dev40_baseline_errors.json` and `.md`; machine metrics and v2 validation are saved beside the batch. The 30-paper future holdout remains frozen and untouched; this phase must not read its abstracts or Gold, launch another worker, run a pilot, or change the extraction prompt, schema, contract, validator, or metric implementations.

## Completed run

`DEV40_BASELINE_COMPLETE = true`. The fresh worker produced 40/40 protocol-valid responses with empty inference arrays and `screening.status="maybe"`. Validator v2 found schema, identifier, and response-contract validity on 40/40; grounding validity on 35/40; five responses were rejected because their `experimental_scale` values were not supported by the anchored abstract text. There were 5 unsupported scale fields, 0 unsupported findings, 0 invalid offsets, and 0 orphan anchors.

Strict Metric v1 field micro P/R/F1 was 0.6654 / 0.5751 / 0.6170 (TP 360, FP 181, FN 266). Calibrated Metric v2 field P/R/F1 was 0.8059 / 0.6965 / 0.7472 (exact TP 360, boundary TP 76, FP 105, FN 190). Findings v1 and v2 both scored P/R/F1 0.9695 / 0.9191 / 0.9436; v2 counted 144 exact and 15 boundary matches. Soil-type recall was 0.0435 (1 exact TP, 22 FN); salinity-context v2 recall was 0.8286 with 26 boundary matches. The baseline records error classes and source-span routing counts without changing findings rules or applying a quality threshold.

These are Dev40 development diagnostics, not independent performance. The frozen historical prompt SHA is unchanged. No future-holdout identities, abstracts, or Gold were accessed or used; no holdout extraction or pilot ran. All local generated data and reports remain gitignored.
