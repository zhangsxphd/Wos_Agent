# v0.6.3 frozen independent holdout evaluation

The 20-paper holdout was evaluated once using the frozen prompt and the human-approved Gold in `data/evidence_benchmarks/v06_holdout20_gold_final/`. The set is now historical independent holdout data. Do not rerun extraction or metrics against it, tune against its results, or use it for a pilot. Future optimization requires new development data and a new future holdout.

All 14 user review groups were applied to a new Gold derivative. It contains 20 identities and passed JSON Schema, the frozen `EvidenceValidator`, identity, evidence grounding, offsets, anchor, and unsupported-content checks: schema errors 0; grounding errors 0; invalid offsets 0; orphan anchors 0; identity errors 0; unsupported fields/findings 0. Gold status is `human_approved_holdout_gold` and `HOLDOUT_GOLD_FROZEN=true`.

The fresh ephemeral worker processed only its isolated request bundle. It returned 20/20 responses, all passing the local blind protocol checker; all inference arrays were empty and `screening.status` was `maybe`. The controller copied response files byte-for-byte. `batch_prepare --resume` reused 20 validated responses. The production reader confirmed payload, request, prompt, schema, canonical input, identity, JSON Schema, grounding, and response-contract checks at 20/20; unsupported fields/findings, invalid offsets, orphan anchors, and rejected responses were all zero.

## Frozen inputs

| Input | SHA-256 |
|---|---|
| Prompt | `f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1` |
| Schema | `33e289f1b2911024f00748ac2580ab7d2926369e7f4ac2ef48b28da8573dae36` |
| Evidence Field Contract v1 | `b667329e16edcf2e6ed0cc300518b45c39f9b8178f4257900762b084686e9e7b` |
| Metric v1 | `4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a` |
| Metric v2 | `54262802b4970429c1a218272145e229f44b9553f0dc5ba7cfceb070dfad4720` |
| Canonical input | `3df4c7e3c9301522c27bbc0d9c7b03f4e62839dd8cecf777d190a35f697b203a` |
| Final Gold JSONL | `fb58a888e06ee0efb7cba5de9d533b91582fb412697af2bf051ffb361fdf65c4` |

## Strict metric v1

Structural validity was 100% for schema, grounding, identifier match, and response contract. Field micro metrics were TP=167, FP=89, FN=135; precision 0.6523, recall 0.5530, micro-F1 0.5986. The pre-registered v1 quality gate **failed only `field_micro_f1`** (threshold 0.90). Findings were TP=93 by overlap, FP=0, FN=6; precision 1.0000, recall 0.9394, F1 0.9688. Exact evidence-text matches: 90; overlap matches: 93. All 93 predicted finding claims were grounded.

| Field | Gold | Predicted | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| study_system.crop | 18 | 18 | 16 | 2 | 2 | 0.8889 | 0.8889 | 0.8889 |
| study_system.soil_type | 18 | 0 | 0 | 0 | 18 | 0.0000 | 0.0000 | 0.0000 |
| study_system.salinity_context | 18 | 19 | 3 | 16 | 15 | 0.1579 | 0.1667 | 0.1622 |
| study_system.experimental_scale | 2 | 2 | 2 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| treatments.irrigation | 2 | 2 | 1 | 1 | 1 | 0.5000 | 0.5000 | 0.5000 |
| treatments.water_regime | 3 | 4 | 3 | 1 | 0 | 0.7500 | 1.0000 | 0.8571 |
| treatments.amendments | 20 | 20 | 16 | 4 | 4 | 0.8000 | 0.8000 | 0.8000 |
| treatments.fertilization | 11 | 11 | 6 | 5 | 5 | 0.5455 | 0.5455 | 0.5455 |
| treatments.biological_treatments | 4 | 4 | 4 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| treatments.other_treatments | 7 | 11 | 5 | 6 | 2 | 0.4545 | 0.7143 | 0.5556 |
| measurements.soil_physical | 11 | 11 | 9 | 2 | 2 | 0.8182 | 0.8182 | 0.8182 |
| measurements.soil_chemical | 32 | 31 | 24 | 7 | 8 | 0.7742 | 0.7500 | 0.7619 |
| measurements.carbon | 31 | 25 | 20 | 5 | 11 | 0.8000 | 0.6452 | 0.7143 |
| measurements.nitrogen | 10 | 10 | 9 | 1 | 1 | 0.9000 | 0.9000 | 0.9000 |
| measurements.microbial | 35 | 28 | 19 | 9 | 16 | 0.6786 | 0.5429 | 0.6032 |
| measurements.greenhouse_gases | 4 | 4 | 2 | 2 | 2 | 0.5000 | 0.5000 | 0.5000 |
| measurements.plant_growth | 37 | 9 | 7 | 2 | 30 | 0.7778 | 0.1892 | 0.3043 |
| measurements.yield | 5 | 5 | 5 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| measurements.water_use | 0 | 1 | 0 | 1 | 0 | 0.0000 | 1.0000 | 0.0000 |
| measurements.other | 16 | 25 | 2 | 23 | 14 | 0.0800 | 0.1250 | 0.0976 |
| methods | 18 | 16 | 14 | 2 | 4 | 0.8750 | 0.7778 | 0.8235 |

`treatments.*` and `measurements.*` are strict exact-value matching, as defined by the frozen v1 metric.

## Calibrated metric v2

| Scope | Exact TP | Boundary TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fields overall | 167 | 29 | 60 | 106 | 0.7656 | 0.6490 | 0.7025 |
| Findings | 90 | 3 | 0 | 6 | 1.0000 | 0.9394 | 0.9688 |
| experimental_scale | 2 | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| salinity_context | 3 | 10 | 6 | 5 | 0.6842 | 0.7222 | 0.7027 |

Metric v2 is reported descriptively; no additional gate was applied.

## H03 representation limitation

For UID `WOS:000933785000001` (DOI `10.3390/app13031436`), the worker returned `experimental_scale="unknown"` with no field-level support span. The canonical abstract explicitly says “field-scale fallow period and crop production experiment” at offsets 382–438. The frozen validator accepted the worker response. This remains `KNOWN_SYSTEM_LIMITATION`; the record remains in all scores and no Gold, prompt, validator, or metric was changed after evaluation.

## Reports and local provenance

- Strict v1: `data/reports/v06_holdout20_frozen_v1/v06_gold_benchmark_20261004_235122.json` and `.md`.
- Calibrated v2: `data/reports/v06_holdout20_frozen_v2/v06_holdout20_metric_v2_20261004.json` and `.md`.
- Final Gold and human decisions: `data/evidence_benchmarks/v06_holdout20_gold_final/`.
- Response SHA-256 values and frozen input hashes: `data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json`.
- Per-response protocol and validation evidence: `data/evidence_batches/v06_holdout20_eval_20261004/frozen_worker_validation_manifest.json`.

All `data/` artifacts remain gitignored. No pilot was run.
