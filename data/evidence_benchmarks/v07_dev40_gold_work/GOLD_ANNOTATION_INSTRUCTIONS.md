# Gold Annotation Instructions — v0.7 Development Set

You are one independent human-quality Gold annotator. Annotate all 40 supplied records. Use only this bundle. Do not inspect parent directories, the repository, other annotations, model outputs, prompts, historical benchmark scores, or any other papers. Work independently; do not communicate your decisions to another annotator. Output one JSON object per input record in a JSONL file at the output path supplied by the launcher.

## Required output

Return exactly one record per UID with the existing Evidence Matrix v0.4 shape, validated against `evidence_matrix.schema.json`:

- `schema_version`: `"0.4"`; copy `uid`, `doi`, and `title` from the input.
- `screening`: `status="maybe"`, concise reason, confidence.
- `evidence`: all keys from the schema: `study_system`, `treatments`, `measurements`, `methods`, `findings`, `mechanisms_explicit`, `limitations_explicit`, `author_interpretations`.
- `evidence_support`: one JSON Pointer entry for each populated ordinary evidence leaf validated through the global support map. Do not add entries for `/evidence/findings/*` or `/evidence/author_interpretations/*`; these items carry their own nested exact anchors. Each anchor uses `source="abstract"`, exact verbatim `evidence_text`, zero-based Unicode character `start` and exclusive `end` offsets.
- `inference`: every existing inference array must be empty. Do not infer or personalize.

For `findings`, `evidence_text` is the complete exact source quote and `claim` must itself be a verbatim substring of that quote; do not paraphrase, normalize, or add interpretation to `claim`. Use only schema-supported certainty values. For `author_interpretations`, preserve the exact anchored span and supported `claim_type`. `mechanisms_explicit` contains only explicit mechanism relations. All evidence must be grounded in the abstract, with exact offsets. Use empty arrays/null/`unknown` when unsupported. Do not fabricate or repair source text. Do not use title text as evidence.

## Approved ontology rules

Follow `evidence_field_contract_v2.md` exactly. Key boundaries:

- Ordinary soil/substrate descriptors qualify for `soil_type` only when tied to the actual sampled, experimental, site soil, or pot substrate; land use and application targets do not.
- `salinity_context` requires an explicit actual-system or treatment-induced link for Articles; keep background, application, and attributed Review scope distinct.
- Plant tissue/phenotype physiology, pigments, photosynthesis, antioxidant enzymes, ROS, proline, plant Na/K, and plant stress genes go to `measurements.plant_growth`.
- `measurements.other` is last resort after all named categories.
- Microbial functional genes, community assembly, and network ecology are `measurements.microbial`; an unattributed bulk soil enzyme is not automatically microbial.
- Measured carbon fractions/properties go to `carbon`; greenhouse-gas flux stays in `greenhouse_gases`.
- Separate fertilization nutrient input from amendment soil-conditioning role; avoid unsupported duplication.
- Field-scale variants map to `field`; a named model is a method and alone does not set `experimental_scale=model`.
- Findings record what was found. Interpretations require a distinct, explicit author meaning layer. Mechanisms require explicit X affects Y through/by/via Z or equivalent. Do not duplicate a finding mechanically. A span may support two layers only when they are semantically distinct.

## Output checks

Before finishing, verify 40 unique UIDs, no missing record, JSONL syntax, exact anchors and offsets, all inference arrays empty, and no additional keys. Do not include commentary or benchmark scores in the output file. Final response should report only the output path, record count, and any records you could not annotate reliably.
