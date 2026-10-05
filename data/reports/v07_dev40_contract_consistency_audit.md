# v0.7 Dev40 Field Contract v2 Consistency Audit

**Result: `CONTRACT_V2_AMENDMENT_NEEDED`**

Contract v2 remains frozen and was not changed. A/B disagreements, C adjudication, and the strict EvidenceValidator exposed repeated category boundaries that are not stable enough for a future Gold freeze:

| Repeating boundary | Evidence in Dev40 | Contract gap to resolve in a later reviewed amendment |
|---|---|---|
| Harvested product quality versus yield/other | WOS:000899201500001, WOS:001224081200001, WOS:001453401200001; C items D011, D013, D083, D085 | Define cotton fiber quality, grain palatability, and harvested product quality separately from yield quantity/components. |
| Combined N/P/K or water-fertilizer productivity | WOS:000899201500001, WOS:001461814100001; C items D009, D012, D014, D088 | Define cross-nutrient productivity indices and fertilizer-input productivity, including whether they belong in nitrogen, water_use, or other. |
| Tissue-specific nutrient concentration | WOS:001083747800001, WOS:001298590200001; C items D040, D041, D042 | Require the compartment in each measurement value and specify routing when one sentence reports soil, root, shoot, and grain values together. |
| Optical DOM/carbon-source indices | WOS:001488980100001; C items D101, D102, D104, D105 | State whether fluorescence/source indices are carbon measurements, other indices, or excluded when they describe source attribution rather than carbon amount/property. |
| Water and material-property category boundaries | WOS:001224081200001 and WOS:001586749800001; C items D049, D050, D139, D140 | Clarify floodwater chemistry versus soil chemistry and amendment-material salt/functional-group characterization versus soil chemistry or nitrogen. |

Other unresolved items concern local abstract attribution or insufficient context rather than a recurring ontology gap: broad Review scope, a 27.80% result with no stated referent, generic limitations, and whether recommendations describe applied treatments. These remain in the human-review packet and do not justify automatic changes to Contract v2.

The six experimental-scale assertions that failed the frozen validator are provisionally `unknown` in the candidate and grouped for review. This is a compatibility limitation in the unchanged v0.6 validator; no validator, schema, or Contract v2 changes were made in this phase.

This audit is development-set diagnostic evidence only. It does not represent independent performance. No future holdout abstract, Evidence, or response was accessed, and no extraction prompt or extractor was run.
