# v0.7 Dev40 Final Prompt Selection

**Scope:** Dev40 development data only. These values are not independent performance and do not estimate future-holdout performance. No third prompt iteration was run. Iteration 2 is selected and frozen for final evaluation; tuning stops here.

| Metric | Baseline | Iteration 1 | Iteration 2 |
|---|---:|---:|---:|
| Metric v1 field micro F1 | 0.6170 | 0.6909 | 0.7817 |
| Metric v2 field micro F1 (primary development comparison) | 0.7472 | 0.8294 | 0.8564 |
| Validator v2 rejected responses | 5 | 2 | 0 |
| Findings precision (v1) | 0.9695 | 0.9756 | 0.9645 |
| Findings recall (v1) | 0.9191 | 0.9249 | 0.9422 |
| Findings F1 (v1) | 0.9436 | 0.9496 | 0.9532 |

Iteration 2 meets the preregistered development-selection priorities: all 40 responses pass Validator v2; overall Metric v2 F1 improves by 0.0270 over Iteration 1; findings precision remains above 0.95; soil_type F1 is 0.8571 (a 0.0365 reduction from I1); plant_growth is 0.8553; salinity_context is 0.9254; experimental_scale is 0.9630 with zero grounding rejection; and measurements.other F1 rises from 0.4706 to 0.7619. Known residual cost: methods F1 decreases from 0.6197 to 0.5797. Do not tune further against Dev40 or the future holdout.

## Principal field results (Metric v2 F1)

| Field | Baseline | I1 | I2 |
|---|---:|---:|---:|
| soil_type | 0.0800 | 0.8936 | 0.8571 |
| salinity_context | 0.7532 | 0.8615 | 0.9254 |
| experimental_scale | 0.8462 | 0.8889 | 0.9630 |
| plant_growth | 0.4381 | 0.8707 | 0.8553 |
| other | 0.3934 | 0.4706 | 0.7619 |
| microbial | 0.8052 | 0.7671 | 0.8732 |
| carbon | 0.8400 | 0.8163 | 0.8163 |
| soil_chemical | 0.9241 | 0.8917 | 0.9542 |
| methods | 0.6842 | 0.6197 | 0.5797 |

Fertilization, amendments, and other_treatments have no distinct metric path under this benchmark's field decomposition; no standalone F1 is asserted here.
