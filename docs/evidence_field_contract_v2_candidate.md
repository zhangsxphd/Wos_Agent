# Evidence Field Contract v2 Candidate

**Status:** candidate for human review; not approved for extraction.
**Schema:** Evidence Matrix v0.4, unchanged.
**Basis:** Field Contract v1, v0.6 historical holdout postmortem, human-adjudicated development and historical Gold decisions, and scientific field definitions. No v0.7 development or future-holdout abstracts were used.

This candidate resolves field boundaries before development-set reading. Values still require explicit abstract evidence and a verbatim anchor under the existing schema. Do not infer variables from titles, topic expectations, common knowledge, methods, or treatment names. Review content remains attributed to the reviewed literature and must not be presented as an empirical system operated by the Review authors.

## Study system

| Field | v2 candidate definition | Include | Exclude and boundary |
|---|---|---|---|
| `study_system.soil_type` | Descriptor of the actual studied soil or substrate; formal pedological taxonomy is not required. | Explicitly named sampled soil, experimental soil, study/site soil, or pot substrate. Descriptors may include saline-alkali soil, saline soil, salt-affected soil, sodic/sodic-alkaline soil, coastal saline-alkali soil, and saline-alkali paddy soil when linked to that actual system. | Do not put paddy field, upland field, cropland, land use, study region, application target, or generic saline-soil motivation here. A descriptor alone is not enough: establish which actual soil/substrate it describes. For a Review, report a named review scope only as attributed scope, not as one empirical soil system. |
| `study_system.salinity_context` | Salinity state/context of the Article's actual study system, or explicitly attributed Review scope. | For an Article, prefer the shortest complete span containing both the salinity descriptor and its explicit link to the sampled, experimental, or site system. A shorter descriptor is acceptable when its sentence directly identifies the actual sampled/experimental soil. Treatment-induced salinity is allowed when the salinity state is explicitly an outcome/context of the manipulated system. For a Review, record only an explicit review scope and preserve Review attribution. | Do not extract an unlinked bare mention, generic background, application target, mechanism context, or motivation. Do not infer salinity from crop, location, treatment, or title. Separate actual-system salinity, treatment-induced salinity, background, application target, and Review scope. A mention of “saline”, “salt”, or “alkali” alone is never a trigger. |
| `study_system.experimental_scale` | The physical or analytical study design represented by the existing enum; no schema change. | `field-scale`, `field scale`, `field-scale experiment`, `field-scale study`, `field study`, and `field-based` describing this study → `field`. Pot experiment/study → `pot`; greenhouse → `greenhouse`; laboratory/incubation → `lab`; an overall modeling/modelling/simulation/model-based study → `model`. | A named model is a method, not a scale. HYDRUS, logistic regression, or a machine-learning model alone does not imply `model`. If a physical field/pot/greenhouse experiment uses a named model, record the physical scale and put the named model in `methods`. Use `model` only when the study design as a whole is modeling/simulation. |

## Treatments: fertilization and amendments

Classify by the material's explicit **study role**, not its name alone.

| Field | v2 candidate definition | Include | Exclude and boundary |
|---|---|---|---|
| `treatments.fertilization` | Nutrient input or fertilizer-management treatment. | NPK, urea, organic/bio-organic fertilizer, fertilizer rate/substitution/timing/placement when applied as nutrient supply. | Do not classify a soil-conditioning material as fertilizer merely because it contains nutrients or is mixed with NPK. Do not classify measured N analytes here. |
| `treatments.amendments` | Soil-conditioning or remediation material applied to alter soil/substrate properties. | Gypsum, biochar, pyroligneous vinegar, silica material, lime, calcium material, or other explicit ameliorants. | A live organism is a biological treatment. Do not duplicate an entire mixed treatment under both fields. If the abstract separately specifies an amendment and matched NPK input, record each distinct input in its own field. Use both roles for one material only when the abstract explicitly establishes both. |

## Measurements

**`OTHER_IS_LAST_RESORT = true`**. Identify the measured entity first, then use the first applicable specific category below. `other` is available only after each named category has been considered and no category fits. Do not use uncertainty as a reason to route to `other`; leave unsupported/unclear items empty and flag them for adjudication.

Decision order: `soil_physical` → `soil_chemical` → `carbon` → `nitrogen` → `microbial` → `greenhouse_gases` → `plant_growth` → `yield` → `water_use` → `other`.

| Field | v2 candidate definition | Include | Exclude and boundary |
|---|---|---|---|
| `measurements.plant_growth` | Plant phenotype and physiology, not only biomass growth. | Explicit plant morphology, biomass/growth, root and leaf traits, pigments/chlorophyll, chlorophyll fluorescence, photosynthesis/gas exchange, plant antioxidant enzymes, ROS/oxidative damage, osmolytes such as proline, plant Na+/K+ uptake/content/homeostasis, plant stress-response gene expression, and other measured plant physiological responses. | Source must identify the measured variable as coming from plant tissue or plant phenotype. Soil enzyme activity, soil chemistry, microbial genes/community measures, and harvested yield are not plant growth. Plant physiological/biochemical response takes precedence over `other`. Examples: leaf SOD/CAT, leaf proline, maize Na+ accumulation, ZmSOD4/ZmNHX1 expression, and plant chlorophyll fluorescence → `plant_growth`. |
| `measurements.microbial` | Measured microbial abundance, biomass, diversity, composition, function, or ecology. | Microbial abundance/biomass; alpha/beta diversity; community composition and taxonomic abundance; functional genes and functional potential (e.g. `mcrA`, `nirS`, `nirK`, `nosZ`, AOA/AOB); community assembly; microbial network connectivity and co-occurrence/network properties; microbial ecological functions. | A live organism applied as intervention is `biological_treatments`. Plant genes and plant enzymes are `plant_growth`. Soil chemistry is `soil_chemical`. Bulk soil urease/phosphatase or other soil enzyme activity without explicit microbial origin goes to `other`, not automatically to `microbial`. |
| `measurements.carbon` | Explicit measured carbon property, pool, fraction, stock, flux, or sequestration outcome. | SOC, SIC, TOC, POC, MAOC, carbon stock/sequestration/mineralization, aromatic-C, carboxyl/aliphatic-C, and other explicitly measured carbon-containing fractions when reported as a property of the studied material/system. | A carbon-containing amendment is a treatment, not a carbon measurement. A term containing “carbon” is insufficient without an explicit measured property. CO2/CH4/N2O gas emissions/fluxes → `greenhouse_gases`; do not duplicate a gas-only outcome as carbon. |
| `measurements.soil_chemical` | Explicit soil chemical property not better represented by a more specific named category. | Soil pH, EC/conductivity, CEC, exchangeable ions, soil salinity/ionic composition, and soil nutrient chemistry. | N analytes/fluxes → `nitrogen`; carbon pools/fractions → `carbon`; plant ion content → `plant_growth`; generic soil quality index → `other`; bulk enzyme activity without microbial attribution → `other`. Soil salinity measurement is a measurement, distinct from system-level `salinity_context`. |
| `measurements.other` | Residual measured variable with no applicable named category. | Explicit phosphorus measures, non-GHG environmental indices, economic indices, and explicit soil enzyme activity without stated microbial attribution, when no specific field exists. | Any item fitting a named measurement field; treatments, methods, scale, result claims without a measured variable, or uncertainty. Preserve the measured entity instead of copying a broad analysis phrase. |
| Other named measurement fields | Retain v1 boundaries, applying the decision order above. | `soil_physical`, `nitrogen`, `greenhouse_gases`, `yield`, and `water_use` retain their v1 definitions. | Specific analyte/field takes precedence over `other`; plant phenotype measures are separated from harvested output; greenhouse-gas emissions remain separate from carbon pools. |

## Author interpretations and explicit mechanisms

| Field | v2 candidate definition | Include | Exclude and boundary |
|---|---|---|---|
| `author_interpretations` | An explicit interpretation made by the paper's authors from the study results: association interpretation, causal interpretation, hypothesis, mechanistic interpretation, or management implication. Preserve the existing schema's `text`, `claim_type`, `source`, and anchor. | Result-grounded language attributed to the authors and supported by an explicit abstract span. Reviews may state their own synthesis/interpretation, with attribution preserved. | Background motivation, objective, generic literature statement, recommendation without an author interpretation, and a mechanical duplicate of a finding. Use the original evidence span; do not turn a result paraphrase into a second interpretation. |
| `mechanisms_explicit` | An explicit relation stating that X affects Y through/by/via mechanism Z, or equivalent direct causal/mechanistic wording. | A concise mechanism relation explicitly stated by the authors and anchored to the abstract. Prefer an author mechanism-summary sentence. | Do not promote a list of co-reported measurements, a correlation alone, or a plausible scientific explanation into a mechanism. Keep detailed Na/K, enzyme, or ROS measurements in their measurement fields; a separate explicit mechanism summary may coexist if it adds a distinct relationship. |

## Findings (frozen by default)

`PRESERVE_BY_DEFAULT = true`. The v1 findings rule is copied unchanged because the historical holdout yielded precision 1.0000, recall 0.9394, and F1 0.9688. Field ontology work must not rewrite this rule absent a clear finding-specific failure on new development evidence.

| Field | Definition | Include | Exclude | Boundary rule | Review-paper rule | Example | Counterexample |
|---|---|---|---|---|---|---|---|
| `findings` | Explicit result of the present study or an attributed result synthesized by a Review. | Observed/estimated outcome, comparison, direction, magnitude, or relationship reported as a result. | Objective, background, method, recommendation, future implication, unsupported interpretation, or duplicate restatement. | A finding must answer what the study found. One-to-one output: do not extract the same result twice as a long sentence and short restatement. Preserve claim and evidence span exactly. | A Review may report synthesis results, but do not state them as experiments performed by the Review authors; preserve attribution where the abstract supplies it. | Abstract: field monitoring found seasonal water–salt transport patterns (WOS:001602265100001). | `To identify an optimized strategy...` is an objective, not a finding. |

## Cross-field decision order

1. Decide whether the evidence is a system descriptor, applied intervention, measured variable, method, result, or author interpretation.
2. For `soil_type`, require an actual studied soil/substrate link; formal taxonomy is unnecessary, but land use/region/target is insufficient.
3. For `salinity_context`, require actual-system linkage for an Article; keep treatment-induced state, background, application target, and Review scope distinct.
4. For a measured outcome, route plant phenotype/physiology before the residual field; route named analytes before broad soil-chemistry or `other` categories.
5. For treatments, separate nutrient supply from soil conditioning by study role and preserve distinct inputs without unsupported duplication.
6. A named model is a method. Select physical scale when explicitly stated; select `model` only for an overall modeling/simulation study design.
7. Do not duplicate a phrase across fields unless each field's distinct definition is met. If the abstract does not resolve a boundary, leave it empty/unknown and request adjudication.
8. For Reviews, preserve attribution of reviewed studies, scope, treatments, findings, and measurements. A review-context flag is not itself an extraction error.
