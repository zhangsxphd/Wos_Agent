# v0.7 Future Holdout30 Gold Preparation Status

This status records the reference-Gold preparation stage after the frozen final prompt and evaluation protocol were committed. It is separate from the preregistered evaluation protocol and does not change its gates.

- Final prompt: `prompts/evidence_extraction_v07_final.md`, SHA-256 `b94af358467e31e6fc30e6216c12201383e47ce4f6440881da9c9f5f0d9b7ea2`.
- Evaluation protocol: `docs/v07_final_evaluation_protocol.md`, preregistered and hash-recorded before the first holdout abstract was materialized.
- Holdout abstract access: true, solely for blind reference-Gold construction after the freezes above.
- Gold builders received the final extraction prompt: false.
- Target extraction, target predictions, benchmark, and scoring: not run.

Two independent fresh ephemeral annotators each labeled all 30 records. A third fresh ephemeral adjudicator considered all 193 A/B disagreement items. The candidate is stored under ignored `data/evidence_benchmarks/v07_future_holdout30_gold_candidate/` with status `ai_assisted_holdout_gold_candidate`; it is not human-approved. Validator v2 currently accepts grounding for 27/30 candidate records. Schema validity is 30/30, identity errors 0, invalid offsets 0, orphan anchors 0, and missing supports 0. Three candidate records retain unsupported measurement assertions, explicitly identified by field pointer in the owner review packet.

The full owner packet is `data/reports/v07_future_holdout30_gold_human_review.md`. It includes every paper, its complete abstract, A/B agreement and differences, C's item decisions, validation results, and the proposed Evidence Matrix. The owner must review all 30 papers before target extraction or evaluation. Do not treat agreement or C decisions as human approval.

Control artifacts and per-worker hashes are in ignored `data/evidence_benchmarks/v07_final_system_freeze_manifest.json` and `data/evidence_benchmarks/v07_future_holdout30_gold_candidate/workflow_manifest.json`. The abstracts, worker annotations, adjudications, candidate, validation reports, and packet remain in ignored `data/` and are not committed.
