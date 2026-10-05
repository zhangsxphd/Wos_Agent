# Evidence Field Contract v2.1

STATUS = `HUMAN_APPROVED`

VERSION = `2.1`

SCHEMA = `Evidence Matrix v0.4 unchanged`

APPROVED_USING_DEVELOPMENT_DATA = `true`

FUTURE_HOLDOUT_ACCESSED = `false`

BASIS = Dev40 human adjudication after Contract v2 freeze.

This approved amendment is based only on the 40-paper Dev40 development set and human adjudication after Contract v2 was frozen. The `future_holdout30` abstracts and evidence were not accessed. Contract v2 remains preserved unchanged.

## Ontology additions

### A. Harvested product quality
`yield` means harvested production quantity. Harvested product quality or composition goes to `measurements.other` when no more specific schema field exists, including fiber quality, palatability, grain quality, and amylose.

### B. Nutrient and productivity indices
A single-nutrient N-use metric goes to `nitrogen`; water productivity and WUE go to `water_use`. Mixed N/P/K productivity and mixed water-fertilizer productivity go to `other`, unless the abstract reports independently separable metrics.

### C. Tissue-specific nutrient measurements
Preserve analyte plus compartment. Plant-tissue ion or nutrient concentration goes to `plant_growth`; soil chemical concentration goes to `soil_chemical` or the specific nitrogen/carbon field. A tissue name by itself is not a measurement.

### D. Water chemistry
The schema has no `water_chemistry` field. Floodwater EC, irrigation-water chemistry, and water ionic/chemical indices go to `other`, not `soil_chemical`.

### E. Amendment and material characterization
Properties of the treatment material itself, including pH, salinity, functional groups, elemental/material chemistry, and surface/material characterization, go to `other` unless a more specific field applies. Do not treat them as soil or system nitrogen measurements.

### F. DOM optical/source indices
Fluorescence/source indices, autochthonous index, and photochemical DOM state/index go to `other`. They are not carbon measurements unless the abstract explicitly measures a carbon pool, fraction, content/concentration, or stock.

### G. Genotype/cultivar experimental factor
A genotype or cultivar explicitly used as a comparison factor goes to `treatments.other_treatments`. Identity/background material without an experimental-factor role is not a treatment.

### H. Microbial functional metabolites
Microbial-produced metabolites/compounds used to characterize microbial function (for example IAA production, strain-produced ammonia, or sulfur-oxidation capability) go to `measurements.microbial`. System-level soil/water/plant nitrogen pools or fluxes remain `nitrogen`.

### I. Broad umbrella measurements
Do not emit umbrella labels such as “soil physical and chemical properties” or “plant traits” unless the schema explicitly defines that umbrella. Prefer specific measured variables.

### J. Explicit limitations
`limitations_explicit` accepts only an explicitly stated study, method/data/design, or Review-synthesis limitation. Exclude generic risks, future expectations, and general cautions.

## K. Experimental scale: explicit physical or analytical setting
`experimental_scale` describes the study's explicit physical or analytical setting. A field scale may be assigned when the abstract links this study's experiment, investigation, monitoring, sampling, or treatment to an actual agricultural/experimental field or site. Supported forms include field experiment(s), field study, field-based, field-scale, experiments conducted in named fields, and investigation in a reclaimed/coastal field. A generic mention of a research field, field of study, field conditions, or field application potential is insufficient unless the actual study setting is explicit.

A pot scale may be assigned when experimental organisms, treatments, salinity levels, substrates, or conditions are explicitly grown, applied, tested, conducted, or maintained in pot(s). “Pot experiment” is not required when the physical pot setting is direct, such as “maintained in respective pots.” Greenhouse, laboratory/lab, and incubation settings are recognized when linked to the study design. Model scale is reserved for an overall modeling/modelling, simulation, or model-based study; a named model alone is a method and does not determine scale.

## Human adjudication status
R01–R18 ontology decisions and all six R19 scale cases were human adjudicated using development data. All Dev40 human-review items are resolved. The approved Contract does not expose or use future holdout data.

## Findings rule unchanged
Findings remain `PRESERVE_BY_DEFAULT`. The R07 adjudication rejects one finding with an unresolved referent; it does not change the general findings extraction rule.
