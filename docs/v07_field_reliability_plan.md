# v0.7 Field Reliability Plan

## Phase 0 status and scope

The v0.6 evaluation is closed. The 20-paper v0.6 holdout is historical and locked. Phase 0 does not change the extraction prompt, field contract, schema, validator, or metrics, and runs no extraction or pilot. The new v0.7 development set and future holdout identities are frozen from metadata before any v0.7 tuning. Only the development set may be used for later prompt or contract work. The future holdout is identity-only and remains inaccessible to tuning.

## Priorities from the v0.6 error Pareto

The ranking uses strict v1 FP+FN over 224 field mismatches. The first eight fields contribute 82.1% of these errors.

| Rank | Field | FP | FN | Errors | Share | Cumulative |
|---:|---|---:|---:|---:|---:|---:|
| 1 | measurements.other | 23 | 14 | 37 | 16.5% | 16.5% |
| 2 | plant_growth | 2 | 30 | 32 | 14.3% | 30.8% |
| 3 | salinity_context | 16 | 15 | 31 | 13.8% | 44.6% |
| 4 | microbial | 9 | 16 | 25 | 11.2% | 55.8% |
| 5 | soil_type | 0 | 18 | 18 | 8.0% | 63.8% |
| 6 | carbon | 5 | 11 | 16 | 7.1% | 71.0% |
| 7 | soil_chemical | 7 | 8 | 15 | 6.7% | 77.7% |
| 8 | fertilization | 5 | 5 | 10 | 4.5% | 82.1% |

Development work should first resolve the `measurements.other` and `plant_growth` boundary, including the 18 exact shared-evidence routing cases found in this postmortem. Next address salinity_context actual-system linkage and v2 residuals (6 FP, 5 FN), then microbial routing, soil_type positive extraction/descriptor policy, carbon and soil_chemical routing, and fertilization/amendment boundaries. Although soil_type has fewer total mismatches than the first four fields, its 0/18 recall makes it a required focused workstream.

The soil_type result is systematic abstention, but frozen outputs alone do not distinguish prompt conservatism from a descriptor-versus-taxonomy expectation mismatch. The v0.7 development set should test this distinction with explicit evidence-linked examples; do not change the contract based on this postmortem alone.

For salinity_context, prefer the shortest span that contains both the salinity descriptor and its explicit link to the sampled or experimental system. Keep generic application motivation, review scope, and actual study-system context separate. The postmortem uses stored evidence spans only; where that span cannot establish context, it records the limitation.

## Regression constraints

Finding extraction is `PRESERVE_BY_DEFAULT`: v0.6 finding P/R/F1 was 1.0000 / 0.9394 / 0.9688. Do not rewrite finding rules for field improvements unless v0.7 development evidence identifies a clear finding-specific failure.

The v0.6 strict gate `field v1 F1 >= 0.90` remains a permanent historical gate and its failure remains recorded. Do not lower it retroactively. A future v0.7 gate may use metric v2 as the primary boundary-aware field measure while retaining v1 as a strict diagnostic, but its threshold must be specified before a new independent evaluation and must not be selected to pass based on v0.6 holdout scores.

## Dataset freeze

Seed: `20261005`. The metadata-only selector excludes the old 7-paper development set and the historical 20-paper holdout from a corpus of 185 abstract-bearing records, yielding 158 eligible identities. It freezes 40 development identities and 30 disjoint future-holdout identities with deterministic stratification over year, document type, query membership, and journal diversity. Selection does not inspect evidence content.

`V07_FUTURE_HOLDOUT_FROZEN_BEFORE_TUNING = true`

`future_holdout_abstracts_accessed = false`

`gold_created = false`; `extraction_run = false`.

See the identity registries and manifests under `data/evidence_benchmarks/v07_dev40/` and `data/evidence_benchmarks/v07_future_holdout30/`. Future-holdout identities contain bibliographic metadata only. Do not look up its abstracts or use those identities in tuning. The full selection and error packet are in `data/reports/v07_historical_holdout_postmortem.json`.

## Phase 1B: Dev40 Gold construction

The formal human-approved Field Contract v2 was frozen before the first Dev40 abstract was read. Contract SHA-256 is recorded in `data/evidence_benchmarks/v07_dev40/contract_freeze_manifest.json`. C10's human modification establishes the distinction between observed findings and a separate author interpretation or explicit mechanism, with mechanical duplication prohibited.

The 40 Dev40 abstracts were placed in an allowlisted annotation bundle with only the schema, Contract v2, and Gold annotation instructions. Two fresh isolated annotators produced independent JSONL files; a third fresh isolated adjudicator saw only A/B disagreement items with their corresponding abstract context. The comparison reports exact and boundary-equivalent agreement separately. Annotator A passed schema for 40/40 and grounding for 34/40; annotator B passed schema for 40/40 and grounding for 28/40. Their remaining validation failures were unsupported experimental-scale anchors, so the candidate provisionally leaves those six scale values `unknown` for review.

The unapproved candidate is `data/evidence_benchmarks/v07_dev40_gold_candidate/candidate_gold.jsonl`. It contains exactly the 40 Dev40 identities and passes schema and grounding validation 40/40, with zero invalid offsets, orphan anchors, missing supports, or identity errors. C resolved 127 disagreement items; 41 remain for human review in 19 grouped decisions at `data/reports/v07_dev40_gold_human_review.md`. This remains a development set, not an independent performance result, and it is not human-approved Gold.

The consistency audit is `CONTRACT_V2_AMENDMENT_NEEDED`: repeated measurement-category boundaries need a later reviewed clarification. Contract v2, schema, extraction prompt, frozen validator, metrics, and all v0.6 frozen artifacts remain unchanged in this phase. No Dev40 extraction, future-holdout access, future Gold, or pilot was performed. Wait for human review before freezing the Dev40 Gold or beginning any subsequent stage.
