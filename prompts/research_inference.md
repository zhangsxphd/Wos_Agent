This is the SECOND stage: interpretation, not source-derived extraction.
Use only the supplied validated Evidence Matrix and separately supplied user
research context. Do not modify evidence. Return valid JSON only in the inference
object: mechanistic_interpretation, connection_to_user_research, possible_gap,
transferable_idea, needs_fulltext_for.

Each item must contain statement, kind="inference", and nonempty evidence_anchors
using JSON Pointers to populated supported facts or existing findings. A missing
or empty anchor is forbidden. Do not anchor to another inference or to a title.
If there is no supporting evidence, return []. No unanchored research gap is allowed.

Relevant comparison concepts may include saline-alkali paddy, irrigation/AWD/
flooding, microalgae/biological amendment, microbial community, PLFA, microbial
necromass, SOC, nitrogen transformation, DO/ORP/EC, greenhouse gas, yield and water
footprint. These are user interests, NOT facts measured in the supplied paper.
Do not populate evidence using these interests. Do not claim that an entire
research field has a gap based on one abstract. Distinguish missing reporting in
this source from an unstudied scientific question. Preserve the reported study
scale and avoid treating pot or model results as verified field outcomes.
