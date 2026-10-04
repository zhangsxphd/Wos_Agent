# v0.7 Field Contract v2 — Human Review

These are proposed ontology decisions only. The candidate has not been approved, and no extraction has run under it. Reply with `ACCEPT C01,C02...`, `REJECT C...`, or `MODIFY C...: ...`.

## #C01 — Soil descriptor versus land use

**Field:** `study_system.soil_type`
**v1 rule:** Soil classification tied to a site/sample/experiment; formal classes were easily interpreted too narrowly.
**v2 proposed rule:** Accept an ordinary soil/substrate descriptor (including saline-alkali, saline, salt-affected, sodic, coastal saline-alkali) when linked to actual sampled/experimental/site soil or pot substrate; formal taxonomy is unnecessary. Exclude paddy/upland field, cropland, region, application target, and generic motivation.
**Historical evidence:** 18 FN, 0 predictions. Human adjudications rejected land-use/application descriptors as soil_type.
**Positive example:** “The sampled saline-alkali soil was placed in pots” → soil_type.
**Negative example:** “Saline-alkali paddy fields are a regional application target” without substrate linkage → no soil_type.
**Expected benefit:** Repair systematic 0/18 abstention while preserving actual-system linkage.
**Regression risk:** Over-extracting generic regional/application descriptors.
**Recommended:** ACCEPT

## #C02 — Article salinity context and Review scope

**Field:** `study_system.salinity_context`
**v1 rule:** Actual Article system linkage required; Review scope allowed with attribution, but span/context distinctions were under-specified.
**v2 proposed rule:** For Articles require actual-system or treatment-induced salinity linkage; prefer the shortest full linked span. Keep motivation, application targets, background, and Review scope distinct; preserve Review attribution.
**Historical evidence:** 31 v1 errors; v2 reduced to 6 FP/5 FN with 10 boundary matches. Human decisions accepted linked monitored saline fields and rejected unlinked pot/application and generic Review context.
**Positive example:** “maize grown in severely saline-alkali soil” → actual-system context.
**Negative example:** “Saline soils threaten agriculture” in background → empty.
**Expected benefit:** Reduce false positive system assignments and missed explicitly linked systems.
**Regression risk:** Longer evidence spans may reduce exact lexical matching; Review attribution may be lost.
**Recommended:** ACCEPT

## #C03 — Plant phenotype and physiology

**Field:** `measurements.plant_growth`
**v1 rule:** Growth, morphology, physiology and development were included, but detailed classes were not listed.
**v2 proposed rule:** Explicit plant tissue/phenotype measurements include morphology, roots/leaves, photosynthesis, pigments/fluorescence, gas exchange, antioxidant enzymes, ROS, proline, plant Na+/K+, and plant stress genes.
**Historical evidence:** 32 v1 errors (2 FP, 30 FN); 18 exact same-support values were sent to `other`.
**Positive example:** Leaf SOD/CAT, proline in leaves, maize Na+ accumulation, ZmNHX1 expression, chlorophyll fluorescence → plant_growth.
**Negative example:** Soil urease or microbial `nirS` → not plant_growth.
**Expected benefit:** Correct the largest exact cross-field routing pattern.
**Regression risk:** Source ambiguity could move soil or microbial variables into plant_growth.
**Recommended:** ACCEPT

## #C04 — Make `other` a true last resort

**Field:** `measurements.other`
**v1 rule:** Unnamed measured variables could enter after named categories, but the residual boundary did not force an exhaustive check.
**v2 proposed rule:** `OTHER_IS_LAST_RESORT=true`; apply the named-field decision order before using `other`. Uncertainty alone means leave empty/flag, not `other`.
**Historical evidence:** 37 errors (23 FP, 14 FN), the largest v1 contribution.
**Positive example:** Explicit phosphorus measurement or soil quality index → other.
**Negative example:** Plant proline → plant_growth; nitrate → nitrogen; CH4 flux → greenhouse_gases.
**Expected benefit:** Reduce the 23 false positives and improve routing precision.
**Regression risk:** Over-tight routing could omit a genuine variable without a named home.
**Recommended:** ACCEPT

## #C05 — Microbial functions and networks

**Field:** `measurements.microbial`
**v1 rule:** Community, diversity, biomass, and microbial activity; functional genes and ecological/network properties were not enumerated.
**v2 proposed rule:** Include microbial abundance/biomass/diversity/composition, taxonomic abundance, functional genes/potential, community assembly, network/co-occurrence/connectivity, and microbial functions. Require explicit microbial origin for enzyme activity.
**Historical evidence:** 25 v1 errors (9 FP, 16 FN).
**Positive example:** `mcrA`, `nirS`, `nosZ`, fungal diversity, community assembly, network connectivity → microbial.
**Negative example:** Bulk soil urease with no microbial attribution → other; plant antioxidant genes → plant_growth.
**Expected benefit:** Recover microbial functional and ecological measurements without indiscriminate enzyme routing.
**Regression risk:** Ambiguous gene or enzyme source.
**Recommended:** ACCEPT

## #C06 — Measured carbon fractions versus materials and gases

**Field:** `measurements.carbon`
**v1 rule:** Carbon pools and stocks were included; measured fractions and carbon-containing functional fractions were not explicit.
**v2 proposed rule:** Include SOC/SIC/TOC/POC/MAOC, stocks, sequestration/mineralization, and explicit measured carbon fractions. Route gas emissions to greenhouse_gases.
**Historical evidence:** 16 v1 errors (5 FP, 11 FN).
**Positive example:** Measured aromatic-C content or MAOC → carbon.
**Negative example:** Biochar application alone → amendment; CH4 emission → greenhouse_gases.
**Expected benefit:** Improve recall for explicitly measured carbon properties.
**Regression risk:** Treating any word containing “carbon” as an analyte.
**Recommended:** ACCEPT

## #C07 — Soil chemistry boundaries

**Field:** `measurements.soil_chemical`
**v1 rule:** Soil pH, EC, salinity and ions were included; specific-category and residual boundaries need clearer examples.
**v2 proposed rule:** Include pH, EC, CEC, exchangeable ions, soil salinity/ionic composition and soil nutrient chemistry; route N, carbon, plant ions, bulk enzymes and indices to their respective fields.
**Historical evidence:** 15 v1 errors (7 FP, 8 FN).
**Positive example:** Soil EC or exchangeable sodium → soil_chemical.
**Negative example:** Leaf K+ → plant_growth; SOC → carbon; soil quality index → other.
**Expected benefit:** Keep broad soil properties distinct from specific analytes and plant outcomes.
**Regression risk:** Over-broad chemistry assignment.
**Recommended:** ACCEPT

## #C08 — Fertilizer input versus soil amendment

**Field:** `treatments.fertilization` and `treatments.amendments`
**v1 rule:** Fertilizer products/rates and soil amendments had separate fields, but role-based handling of dual/mixed applications was not explicit.
**v2 proposed rule:** Fertilization is nutrient input/management; amendment is soil conditioning/remediation. Separate distinct components when explicitly reported; do not duplicate a whole mixed treatment without evidence.
**Historical evidence:** Fertilization 10 errors (5 FP, 5 FN); amendments 8 (4 FP, 4 FN).
**Positive example:** Pyroligneous vinegar → amendment; separately specified matched NPK → fertilization.
**Negative example:** Copying the entire vinegar treatment into fertilization.
**Expected benefit:** Reduce role confusion and unsupported duplication.
**Regression risk:** Unclear primary role for materials that have both effects.
**Recommended:** ACCEPT

## #C09 — Physical field scale versus named models

**Field:** `study_system.experimental_scale` and `methods`
**v1 rule:** Field/pot/greenhouse/lab/model enum; a named model alone must not determine scale, but field-scale wording was not mapped.
**v2 proposed rule:** Field-scale variants map to `field`. Named models remain methods; only overall modeling/simulation study design maps to `model`.
**Historical evidence:** No v1 metric mismatch; one human-adjudicated field-scale case could not be represented and was kept unknown.
**Positive example:** Field-scale experiment using HYDRUS → `field`, HYDRUS in methods.
**Negative example:** “HYDRUS model” alone → not `model` scale.
**Expected benefit:** Represent physical field scale without confusing model names with study design.
**Regression risk:** Missing scale when abstracts state only a method/model.
**Recommended:** ACCEPT

## #C10 — Interpretations, explicit mechanisms, and preserved findings

**Field:** `author_interpretations`, `mechanisms_explicit`, `findings`
**v1 rule:** Findings were explicit results; interpretation/mechanism boundaries were incomplete.
**v2 proposed rule:** Require anchored, result-grounded author interpretation; explicit mechanism requires a stated X→Y through/by/via Z relation. Keep findings rules verbatim and `PRESERVE_BY_DEFAULT`.
**Historical evidence:** Human adjudications retained distinct interpretation/hypothesis and one explicit mechanism summary, while rejecting background and duplicate result summaries. Findings holdout P/R/F1 was 1.0000/0.9394/0.9688.
**Positive example:** “X altered Y by changing Z” → explicit mechanism; “correlation analysis showed X related to Y” → association interpretation.
**Negative example:** A list of co-measured Na/K/ROS values alone → no mechanism; a finding repeated as interpretation → no duplicate.
**Expected benefit:** Clarify existing schema fields while protecting strong finding performance.
**Regression risk:** Over-extraction or duplicate claims.
**Recommended:** ACCEPT
