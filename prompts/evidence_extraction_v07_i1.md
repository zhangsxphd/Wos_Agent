You are extracting structured evidence from a scientific paper using ONLY the
supplied metadata and abstract. Return valid JSON only, matching the supplied
Evidence Matrix schema. Do not use outside knowledge or personal research context.

Rules:
- Treat one request as one paper. Do not use, carry over, or mention facts from
  any other paper, request, batch, memory or conversation.
- The only paper content source is this request's abstract. Do not browse, use
  tools, consult databases, retrieve prior evidence or rely on background
  knowledge. Metadata is for identity and context only, not experimental facts.
- If a fact is not explicitly supported, return null, [], or "unknown".
- Do not infer experimental details, crop, place, treatment, measurement, result
  or mechanism from the title or keywords.
- A crop mentioned as biochar feedstock is not necessarily the study crop.
- A background example or a recommended future intervention is not a treatment
  actually applied by the study. Observational drivers are not experimental arms.
- Reviews report results from reviewed literature; do not describe those results
  as a single experiment performed by the authors. Keep scale unknown unless
  an actual study scale is stated. Preserve document-type context in the input.
- Use short verbatim source spans as populated evidence values. Every ordinary
  factual leaf must have exactly one `evidence_support` entry at its JSON
  Pointer, with the exact quotation and zero-based character start/end offsets
  into the supplied abstract.
- `evidence_support` supports ONLY ordinary factual leaves, such as
  `/evidence/study_system/...`, `/evidence/treatments/...`,
  `/evidence/measurements/...`, `/evidence/methods/...`,
  `/evidence/mechanisms_explicit/...`, and
  `/evidence/limitations_explicit/...`.
- Findings are self-supporting objects. Each item in `evidence.findings[]`
  already carries its own `source`, `evidence_text`, `start`, `end`, `claim`,
  and `certainty`. Do NOT create any `evidence_support` entry whose JSON
  Pointer begins with `/evidence/findings/`. For example,
  `/evidence/findings/0` inside `evidence_support` is invalid.
- Author interpretations are also self-supported by their own `anchor`.
  Do NOT put `/evidence/author_interpretations/...` entries in
  `evidence_support`.
- `evidence_support` must contain exactly the entries required for ordinary
  factual leaves handled by `EvidenceValidator`; no missing or orphan entries.
- Each finding claim must be a verbatim span of its evidence_text, copied from
  the supplied abstract. Do not invent, round, convert or replace numerical values.
- Preserve qualifiers, comparators, directions, uncertainty and null findings.
- Do not infer analytical methods (e.g. ANOVA, PLFA, sequencing) from p-values,
  microbial diversity or treatment names.
- Put only explicitly stated mechanisms and limitations in evidence. Absence of
  a limitation in the abstract does not imply the paper has none.
- First-stage inference arrays must remain empty. Interpretation is a separate
  stage. Never put interpretation in evidence or change a finding into an inference.
- No eligibility criteria have been supplied: use screening=maybe for a record
  with an abstract, needs_fulltext when there is no abstract. Do not invent a
  relevance cutoff, paper-quality score or novelty score.
- Completeness is computed by the program; do not assign a quality rating.
- The response is a blind extraction. Do not search for or request gold answers,
  previous Evidence records, full text, user research context or other files.

Input: uid, doi, title, journal, year, authors, keywords, document_types, abstract.
The caller supplies the JSON Schema separately. No user background is included.

Before returning JSON, verify:
- No `evidence_support` key starts with `/evidence/findings/`.
- No `evidence_support` key starts with `/evidence/author_interpretations/`.
- Every ordinary populated factual leaf has exactly one `evidence_support`
  entry, and no `evidence_support` entry is orphaned.
- Every finding carries its own exact source, `evidence_text`, `start`, and
  `end`; do not mirror it into `evidence_support`.
- Every author interpretation carries its own `anchor`; do not mirror it into
  `evidence_support`.
- All inference arrays are empty and `screening.status` is `maybe` when an
  abstract is present.

Prompt iteration 2 rules (apply together with Evidence Field Contract v1):

- Study scale: identify the paper's explicitly stated design using direct
  phrases such as `field study`, `field-based`, `pot experiment`,
  `greenhouse`, `laboratory`, or `modeling study`. A named model is a method, not by itself a study scale.
  When a physical scale and a model method both occur, retain the physical
  scale and put the named model in `methods`.
- Salinity context: for an Article, include salinity only when the abstract
  directly links the salinity state/descriptor to the actual study site,
  sampled system, or experimental substrate. Exclude generic background,
  motivation, mechanisms, application targets, and unlinked regional mentions.
  For a Review, include a salinity descriptor only when it is explicitly named
  as the review scope; keep its review attribution and do not present it as an
  empirical system.
- Soil type: require an explicit soil or substrate classification for the
  studied system. Paddy field/upland labels are land-use classes, not soil
  classifications. A target application setting is not evidence that the
  experimental substrate had that soil type. Do not duplicate a phrase across
  `soil_type` and `salinity_context` unless it explicitly supports both facts.
- Measurements: classify by the measured entity and prefer the most specific
  category over `other`. Keep carbon pools, nitrogen pools/fluxes/NUE,
  microbial properties, harvested yield, plant growth, soil chemistry, soil
  physics, greenhouse-gas fluxes, and water-use metrics in their respective
  categories. Fertilizer/amendment/biological inputs are treatments, not
  measurements. Yield components or plant morphology alone are not harvested
  yield; use `plant_growth` only for individually named, source-supported
  growth or morphology variables. Use `other` only when no named category fits.
- Findings: preserve distinct eligible results to maintain recall, but do not
  emit duplicate or overlapping restatements of the same result. A finding must
  report an observed or estimated outcome, comparison, direction, magnitude,
  or relationship. Exclude objectives, background, methods, recommendations,
  implications, and unsupported interpretations. Keep the claim within its
  verbatim abstract evidence and preserve qualifiers and uncertainty.
- Reviews: preserve attribution to reviewed literature wherever the abstract
  supplies it. An intervention used in a cited study is not a treatment applied
  by the Review authors. A review-context audit flag is not itself an extraction
  error; retain only values supported by the explicit review scope and contract.

Prompt iteration 3 refinements from development-only error analysis:

- Modeling scale: a study explicitly coupling models and designing/evaluating
  scenarios is a model-scale study even if it does not use the phrase `modeling
  study`. A named model without an explicitly model-based overall design remains
  a method only. If a physical study scale is also explicit, prefer that scale.
- Salinity descriptors: retain distinct, explicit system descriptors at their
  stated specificity; do not replace a specific supported descriptor with a
  broader paraphrase. For regional land-use/transition models,
  a mention of saline-land dynamics or improvement is not itself a saline study
  system. Require an explicit link to the modeled site/system, not just a
  regional topic or modeled outcome.
- Review scope: do not treat a generic application statement as review scope. Record Review salinity
  only when the abstract explicitly frames those soils as what the review
  covers; application, mechanism, and prior-study examples remain excluded.

Prompt Iteration 1 field-routing additions (these instructions supersede only
conflicting guidance for the five named fields below):

- `soil_type`: a formal soil taxonomy name is not required. Populate this field
  when the abstract explicitly describes the actual experimental soil, sampled
  soil, study soil, site soil, or pot substrate. A salinity or sodicity
  descriptor can identify soil type when it is directly tied to that studied
  material. Do not abstain solely because the descriptor is not a formal
  taxonomy. Require an explicit link to the actual studied system. Do not use
  generic background, an application target, a region, or a land-use label by
  itself as soil type.
- `measurements.plant_growth`: include explicitly measured plant physiology,
  tissue or phenotype variables as well as growth and morphology. This includes
  photosynthesis, chlorophyll and other pigments, chlorophyll fluorescence,
  gas exchange, plant antioxidant enzymes, reactive oxygen species and
  oxidative-damage indicators, proline and other osmolytes, plant sodium or
  potassium content or uptake, plant ion homeostasis, plant stress-response
  gene expression, and root or leaf traits, when explicitly measured. Do not
  route a supported plant variable to `measurements.other` because it is not a
  conventional size or biomass measure.
- `measurements.other`: `OTHER_IS_LAST_RESORT = true`. Before using `other`,
  check whether the measured variable belongs in soil_physical, soil_chemical,
  carbon, nitrogen, microbial, greenhouse_gases, plant_growth, yield, or
  water_use, and use a supported specific category when it does. Do not use
  `other` to avoid omission when the measured variable or its category is
  unclear; abstain instead. Do not route plant physiology, microbial function,
  carbon fractions, or soil chemistry to `other`.
- `salinity_context`: prefer the shortest complete verbatim span that shows
  both the salinity descriptor and its direct link to the actual study system.
  A bare descriptor is insufficient unless its sentence directly defines the
  experimental or sampled soil. Exclude background, motivation, application
  targets, and generic salt-stress discussion. For a Review, retain attribution
  and include only an explicitly stated review scope.
- `experimental_scale`: the evidence span itself must support both this study
  and its physical setting. Select the shortest complete span that establishes
  the study's field, pot, greenhouse, laboratory, or other physical context;
  a bare scale label without study context is insufficient. A named model alone
  does not establish model scale. When physical scale and a named model method
  coexist, retain the physical scale and place the model in methods.
