# v0.7 Final Evaluation Protocol (Preregistered)

Status: **FROZEN before opening Future Holdout30 abstracts**. This protocol is locked for the final evaluation. No thresholds, metrics, prompt, contract, schema, or validator may be changed after holdout abstracts are accessed.

## Frozen materials and scope

- Dataset: exactly 30 papers from the frozen `v07_future_holdout30/identities.json` identity registry.
- Final extraction prompt SHA-256: `b94af358467e31e6fc30e6216c12201383e47ce4f6440881da9c9f5f0d9b7ea2`.
- Evidence Field Contract: v2.1, SHA-256 `75fc4b301b598250d159de99c916959999297ecaf512a23a914f74991ae1ffad`.
- EvidenceValidator: v2, SHA-256 `cfb9877271f948541c2a7fd2596d1aac0c347402106a99cc435f6c24fc041680`.
- Evidence Matrix schema: v0.4 unchanged, SHA-256 `33e289f1b2911024f00748ac2580ab7d2926369e7f4ac2ef48b28da8573dae36`.
- Metric v1 SHA-256 `4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a` (diagnostic only).
- Metric v2 SHA-256 `54262802b4970429c1a218272145e229f44b9553f0dc5ba7cfceb070dfad4720` (primary field metric).

## Gold construction and order of operations

Two fresh, isolated blind annotators independently annotate all 30 abstracts using only each paper's metadata and abstract, the frozen schema, Contract v2.1, and the Gold annotation protocol. They must not receive the final extraction prompt, target-extractor instructions, development predictions, benchmark results, historic worker outputs, or each other's annotations. A fresh adjudicator resolves each A/B comparison using only the corresponding abstract, A, B, and Contract v2.1. The adjudicator must not see the final extraction prompt, target output, or Dev40 metric results. The resulting Gold is an AI-assisted candidate, not human-approved Gold.

Generate a complete owner-review packet for all 30 papers, including both agreements and disagreements, identity metadata, abstract, proposed Evidence Matrix with evidence text and offsets, A/B annotations, adjudication, and reviewer action. The owner must review all 30 papers and explicitly approve the Gold before target extraction or evaluation begins.

Only after this preregistration and all freeze hashes are recorded may the 30 holdout abstracts be read for reference-Gold construction. Target extraction, target predictions, benchmarking, scoring, or prompt modification against holdout data are prohibited until owner review is complete. No target extraction or evaluation is authorized by this protocol alone.

## Primary metrics and fixed decision gates

Primary field metric: Metric v2 corpus/micro F1. Metric v1 is reported as a diagnostic only. Report precision, recall, exact-match, boundary-match, FP, and FN alongside the primary metric. Report field-level metrics descriptively; there are no post-hoc per-field gates.

`V07_FINAL_QUALITY_GATE=PASS` only if every condition below is met; otherwise it is `FAIL`:

1. Structural validity is 1.00 for schema, grounding, identity, and response contract.
2. Unsupported field assertions = 0 and unsupported finding assertions = 0.
3. Invalid offsets = 0; orphan anchors = 0; rejected responses = 0.
4. Metric v2 corpus/micro F1 >= 0.80 (overall field micro-F1).
5. Findings precision >= 0.95 and recall >= 0.85; report findings F1 as well.

These are the frozen final-evaluation gates. No post-hoc relaxation or threshold changes. A failed gate is reported as a failure and does not trigger tuning on this holdout.

## Development-only prompt selection disclosure

Prompt selection used Dev40 only. Iteration 2 was selected for the final evaluation despite known regressions in soil_type (0.8936 to 0.8571) and methods (0.6197 to 0.5797), because overall Metric v2 F1 improved from 0.8294 to 0.8564, Validator v2 rejected responses fell from 2 to 0, and the other prioritized development criteria were met. These are development diagnostics, not independent performance.

## Contamination ledger at preregistration

- Future Holdout30 identities frozen before prompt tuning: true.
- Final prompt and evaluation gate frozen before holdout abstracts opened: true.
- Gold builders received final extraction prompt: false.
- Target predictions generated: false.
- Target extraction before owner verification of all 30 Gold papers: prohibited and false.
- Prompt modified after holdout access: prohibited.
