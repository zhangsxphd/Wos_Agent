# v0.6 Final Assessment

`VERSION_STATUS = EVALUATION_COMPLETE`

`PRODUCTION_EXTRACTION_READY = false`

Reason: field-level extraction failed the pre-registered quality gate. The v0.6 gate remains unchanged; its sole failure was `field_micro_f1` under strict metric v1.

The frozen 20-paper evaluation is historical. Do not rerun its extraction, change its Gold or scores, use it to re-prove the same prompt, or treat it as a future pilot.

| Area | Final result | Status |
|---|---|---|
| Structural and provenance pipeline | 100% structural validity; schema, grounding, identifier, and response contract checks passed | Validated |
| Finding extraction | Precision 1.0000, recall 0.9394, F1 0.9688 | Production-quality candidate; preserve by default |
| Field extraction, strict v1 | Precision 0.6523, recall 0.5530, F1 0.5986 | Not production ready |
| Field extraction, calibrated v2 | Precision 0.7656, recall 0.6490, F1 0.7025; 29 boundary matches | Diagnostic semantic metric; not a retroactive gate replacement |
| Historical holdout | 20 papers | Locked; rerun prohibited |

Frozen source SHA256 values:

- Prompt: `f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1`
- Schema: `33e289f1b2911024f00748ac2580ab7d2926369e7f4ac2ef48b28da8573dae36`
- Field Contract: `b667329e16edcf2e6ed0cc300518b45c39f9b8178f4250762b084686e9e7b`
- Metric v1: `4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a`
- Metric v2: `54262802b4970429c1a218272145e229f44b9553f0dc5ba7cfceb070dfad4720`
- Historical Holdout Gold: `fb58a888e06ee0efb7cba5de9d533b91582fb412697af2bf051ffb361fdf65c4`

The detailed, read-only error analysis is in [v07_historical_holdout_postmortem.md](../data/reports/v07_historical_holdout_postmortem.md). It informs a new development set only; no postmortem value is an independent performance claim.
