# Fulltext evidence extraction (one section chunk at a time)

Use ONLY the supplied section paragraphs and metadata. The title identifies the
paper and does not prove a treatment, measurement, mechanism or result.

Keep Methods, Results, Discussion and Conclusion scoped separately. Never
combine the complete paper into one model request. PDF page routing must be
reviewed before claiming a section label. No model API is connected in v0.5.

Extract experimental design, actual treatment arms, sampling, measurements,
analytical and statistical methods, literal numerical results, author claims and
explicit limitations. Empty = not supported. Do not manufacture missing units,
resolve damaged equation text from memory, infer a measurement method or invent
a mechanistic result. Referenced background is not the current study's finding.

Each item needs source_scope=fulltext, document_sha256, section_id, paragraph_id,
char_start and char_end in the ORIGINAL parsed paragraph. Quote exactly, including
negative results, tentative language, units and limitations. Findings require
claim and evidence_text; claim must be a span of evidence_text. Abstract evidence
is independently preserved. Record conflicts with both source anchors.

Author interpretations are author claims, not model inference or verified causal
truth. claim_type is causal/association/pathway/hypothesis/other. Correlation is
association. "May", "likely", "suggests" should retain uncertainty; no automatic
promotion to a causal claim. User personal research context is not input here.

Return valid JSON matching schemas/fulltext_evidence.schema.json, with source
anchors only inside the supplied paragraphs. Do not generate gaps, novelty
scores, citation networks or an experimental design recommendation.
