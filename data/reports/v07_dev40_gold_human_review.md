# v0.7 Dev40 Gold Human Review

Status: `development_gold_candidate`; not human-approved. These groups contain C-unresolved A/B disagreements plus validator-rejected scale assertions. Candidate scale is provisionally `unknown` where the frozen EvidenceValidator cannot verify explicit support. No extraction prompt, model prediction, future holdout abstract, or future holdout Gold was used.

Human decision groups: 19 (target ≤25). C-unresolved disagreement items: 41. Scale grounding cases: 6.

## #1 R01 HRFS role ambiguity

- Affected UIDs (1): WOS:000730402400009
- Field: /evidence/treatments/amendments
- Cases: 1

- Case D001 (`WOS:000730402400009`)
- Abstract context: We conducted a pot experiment to examine the feasibility of applying a reaction-finished solution of hydrochar (HRFS) to enhance rice production in a saline soil. With this purpose, HRFS was applied (0, 10, 20, 40, 60, 80 and 100 mL/pot) and rice yield and nitrogen (N) use efficiency (NUE) were determined. HRFS application significantly (P
- A: ["HRFS was applied (0, 10, 20, 40, 60, 80 and 100 mL/pot)"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Application is explicit, but the snippet does not establish a soil-conditioning rather than nutrient-supply role for HRFS.
  - Contract: treatments.amendments
- Recommended action: NEEDS_HUMAN_REVIEW

## #2 R02 Combined nutrient-productivity routing

- Affected UIDs (2): WOS:000899201500001, WOS:001461814100001
- Field: /evidence/measurements/nitrogen; /evidence/measurements/other
- Cases: 3

- Case D009 (`WOS:000899201500001`)
- Abstract context:  during different cotton growth stages. The daily relative content of chlorophyll increased with the increasing BCAR and were larger at biochar treatments of 50 and 100 t ha (cid:0) 1 than 0, 10, and 25 t ha (cid:0) 1 . The growth and yield-related indicators (plant height, stem diameter, leaf area index and seed yield), irrigation water productivity, and partial fertilizer (N, P, and K) productivity consistently increased than control
- A: ["partial fertilizer (N, P, and K) productivity"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — The combined N/P/K productivity metric is not an N analyte; retained v1 nitrogen boundaries are not supplied sufficiently to settle its routing.
  - Contract: measurements specific-category priority

- Case D014 (`WOS:000899201500001`)
- Abstract context: ochar treatments of 50 and 100 t ha (cid:0) 1 than 0, 10, and 25 t ha (cid:0) 1 . The growth and yield-related indicators (plant height, stem diameter, leaf area index and seed yield), irrigation water productivity, and partial fertilizer (N, P, and K) productivity consistently increased than control under biochar application conditions, and the BCAR of 10 t ha (cid:0) 1 showed the greatest advantages in enhancing germination rates, cot
- A: ["cotton fiber quality indices (length, Micronaire, strength or uniformity index)"]
- B: ["partial fertilizer (N, P, and K) productivity"]
  - C: NEEDS_HUMAN_REVIEW — Combined N/P/K productivity may involve a specific nutrient-use category whose retained v1 boundary is unavailable.
  - Contract: OTHER_IS_LAST_RESORT

- Case D088 (`WOS:001461814100001`)
- Abstract context: , sodium removal rate, and pH by 51.5–60.2, 9.5–35.2, and 9.9 %, respectively, which increased soil organic carbon and total nitrogen by 31.9 and 22.2 %, respectively. Overall, ISCM was the most prominent in processing. As a result, ISCM increased yield and partial factor productivity of nitrogen by 37.2 and 94.5 %, respectively, and reduced N fertilizer use by 28.6 %. Therefore, soil–crop management, particularly the ISCM strategy, can
- A: ["N fertilizer use"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Fertilizer use is an input-management quantity, not an N analyte; the retained v1 nitrogen-use boundary is not supplied.
  - Contract: measurements.nitrogen
- Recommended action: NEEDS_HUMAN_REVIEW

## #3 R03 Harvested quality versus yield/other

- Affected UIDs (2): WOS:000899201500001, WOS:001453401200001
- Field: /evidence/measurements/other; /evidence/measurements/yield
- Cases: 4

- Case D011 (`WOS:000899201500001`)
- Abstract context: cid:0) 1 showed the greatest advantages in enhancing germination rates, cotton yields, water-fertilizer productivities, and financial income than biochar treatments of 0, 25, 50 and 100 t ha (cid:0) 1 . However, neither cotton fiber quality indices (length, Micronaire, strength or uniformity index) were significantly affected by biochar applications. Based on the economic analysis, the rational BCAR was 10 t ha (cid:0) 1 each year (cont
- A: ["cotton yields"]
- B: ["cotton fiber quality indices (length, Micronaire, strength or uniformity index)"]
  - C: NEEDS_HUMAN_REVIEW — The supplied contract does not fully specify whether harvested fiber quality belongs under retained v1 yield or another category.
  - Contract: other named measurement fields

- Case D013 (`WOS:000899201500001`)
- Abstract context: BCAR of 10 t ha (cid:0) 1 showed the greatest advantages in enhancing germination rates, cotton yields, water-fertilizer productivities, and financial income than biochar treatments of 0, 25, 50 and 100 t ha (cid:0) 1 . However, neither cotton fiber quality indices (length, Micronaire, strength or uniformity index) were significantly affected by biochar applications. Based on the economic analysis, the rational BCAR was 10 t ha (cid:0)
- A: ["cotton fiber quality indices (length, Micronaire, strength or uniformity index)"]
- B: ["partial fertilizer (N, P, and K) productivity"]
  - C: NEEDS_HUMAN_REVIEW — Fiber quality is explicit; its boundary with retained v1 yield requires clarification before routing to other.
  - Contract: OTHER_IS_LAST_RESORT

- Case D083 (`WOS:001453401200001`)
- Abstract context:  The results indicated that, compared with CK, the A3 increased the SPAD value and net photosynthetic rate by 2.26% and 28.59%, respectively. Rice yield increased by 12.34%, water use efficiency (WUE) by 10.67%, and the palatability score by 2.82%, while amylose content decreased by 8.00%. The bacterial OTUs (Operational Taxonomic Units) and fungal OTUs increased by 2.18% and 22.39%, respectively. Under the condition of applying 30 kg·h
- A: []
- B: ["palatability score", "amylose content"]
  - C: NEEDS_HUMAN_REVIEW — Palatability is a quality score; the retained v1 yield boundary is insufficiently specified to settle this category.
  - Contract: other named measurement fields

- Case D085 (`WOS:001453401200001`)
- Abstract context: nts on rice growth, photosynthesis, yield, quality, and microbial communities. The results indicated that, compared with CK, the A3 increased the SPAD value and net photosynthetic rate by 2.26% and 28.59%, respectively. Rice yield increased by 12.34%, water use efficiency (WUE) by 10.67%, and the palatability score by 2.82%, while amylose content decreased by 8.00%. The bacterial OTUs (Operational Taxonomic Units) and fungal OTUs increa
- A: ["palatability score", "amylose content"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Palatability is measured, but the harvested-quality boundary with yield must be resolved before assigning other.
  - Contract: OTHER_IS_LAST_RESORT
- Recommended action: NEEDS_HUMAN_REVIEW

## #4 R04 Grain biochemical quality category

- Affected UIDs (1): WOS:001453401200001
- Field: /evidence/measurements/other; /evidence/measurements/yield
- Cases: 2

- Case D084 (`WOS:001453401200001`)
- Abstract context: ed with CK, the A3 increased the SPAD value and net photosynthetic rate by 2.26% and 28.59%, respectively. Rice yield increased by 12.34%, water use efficiency (WUE) by 10.67%, and the palatability score by 2.82%, while amylose content decreased by 8.00%. The bacterial OTUs (Operational Taxonomic Units) and fungal OTUs increased by 2.18% and 22.39%, respectively. Under the condition of applying 30 kg·hm−2 of Bacillus subtilis agent (A3)
- A: []
- B: ["palatability score", "amylose content"]
  - C: NEEDS_HUMAN_REVIEW — Amylose is a grain biochemical constituent; the snippets and supplied retained boundaries do not conclusively settle yield, plant physiology or other.
  - Contract: measurements specific-category priority

- Case D086 (`WOS:001453401200001`)
- Abstract context: nts on rice growth, photosynthesis, yield, quality, and microbial communities. The results indicated that, compared with CK, the A3 increased the SPAD value and net photosynthetic rate by 2.26% and 28.59%, respectively. Rice yield increased by 12.34%, water use efficiency (WUE) by 10.67%, and the palatability score by 2.82%, while amylose content decreased by 8.00%. The bacterial OTUs (Operational Taxonomic Units) and fungal OTUs increa
- A: ["palatability score", "amylose content"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Amylose content requires resolution of the grain-quality and plant-biochemistry boundaries before routing to other.
  - Contract: OTHER_IS_LAST_RESORT
- Recommended action: NEEDS_HUMAN_REVIEW

## #5 R05 Review attribution and scope

- Affected UIDs (3): WOS:000931065500001, WOS:001004405000001, WOS:001304976800001
- Field: /evidence/findings; /evidence/measurements/yield; /evidence/study_system/crop; /evidence/study_system/salinity_context; /evidence/study_system/soil_type
- Cases: 7

- Case D021 (`WOS:000931065500001`)
- Abstract context: r grain filling. In this review, the current environmental stimuli, their dose–response effect on grain filling, and the physiological and molecular mechanisms involved are discussed. Furthermore, what we can do to help cereal crops adapt to environmental stimuli is elaborated. Overall, we call for future research to delve deeper into the gene function-related research and the commercialization of gene-edited crops. Meanwhile, smart agr
- A: []
- B: ["cereal crops"]
  - C: NEEDS_HUMAN_REVIEW — The crop scope is explicit in a review, but the bare value and support omit Review attribution.
  - Contract: Review attribution

- Case D026 (`WOS:001004405000001`)
- Abstract context:  theory, key technologies, and industrialized applications. This paper also shows that soil conditions can be improved and crop yields can be increased by using FGD alone or in combination with humic acid or fertilizer. FGD gypsum plus K–Zn–Mn fertilizer increased the yield of rice by 135%. In alkaline, salinized, and secondary salinized soils, FGD gypsum combined with organic fertilizer or organic plus chemical fertilizer increased the
- A: ["In alkaline, salinized, and secondary salinized soils"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — The soils occur in summarized literature results; the snippet does not establish an empirical system of these authors or provide attributed review scope.
  - Contract: study_system.soil_type Review attribution

- Case D027 (`WOS:001004405000001`)
- Abstract context: use of FGD gypsum in agricultural land is still debated in some countries even though its effectiveness in soil management has been reported in many studies. Thus, the changes in the levels of soil salinity, alkalinity, crop yield, and other physicochemical properties in different soil types and crops after reclamation and planting with FGD gypsum over four years are evaluated in this paper. The main aim of this paper is to review the e
- A: []
- B: ["crop yield"]
  - C: NEEDS_HUMAN_REVIEW — Crop yield is reviewed, but the candidate and short support omit that attribution.
  - Contract: Review attribution

- Case D063 (`WOS:001304976800001`)
- Abstract context: tacles. These techniques can be classified into physical treatments, chemical treatments, biological treatments, and combined treatments; these different measures are all aimed at primarily solving saline–alkali stress. In general, the improvement and utilization of saline–alkali soil contribute to soil health improvement, concentrating on high-quality development, food security, ecological security, cultivated land protection, and agri
- A: ["the improvement and utilization of saline–alkali soil contribute to soil health improvement"]
- B: ["These techniques can be classified into physical treatments, chemical treatments, biological treatments, and combined treatments; these different measures are all aimed at primarily solving saline–alkali stress."]
  - C: NEEDS_HUMAN_REVIEW — The statement is a general improvement implication, without explicit actual-system or attributed Review-scope linkage.
  - Contract: study_system.salinity_context Review scope
- Additional cases in this group: 3; see the linked C item IDs in the workflow manifest/artifacts.
- Recommended action: NEEDS_HUMAN_REVIEW

## #6 R06 Mixed or broad soil-property measures

- Affected UIDs (1): WOS:001047181300014
- Field: /evidence/measurements/soil_physical
- Cases: 2

- Case D030 (`WOS:001047181300014`)
- Abstract context: ffects of straw returning and biological carbon on saline alkali land development in northeast Jiangsu University, with the goal of addressing the issue of weak soil internal structure in some places of northeast China. The experiment analyzed the growth and photosynthesis dynamics, material accumulation and transformation, root activity, soil physical and chemical properties, and the spike characteristics of Kenjiandao 5, respectively.
- A: ["soil physical and chemical properties"]
- B: ["soil structure"]
  - C: NEEDS_HUMAN_REVIEW — The phrase combines physical and chemical properties without identifying variables; it cannot be assigned wholly to physical measurements.
  - Contract: measurements specific-category priority

- Case D031 (`WOS:001047181300014`)
- Abstract context: il can effectively improve the organic matter in the soil and reduce its pH value. Compared with CK, the highest increase is 27.80%. In general, the use of biological carbon and straw return can effectively optimize the soil structure, increase the yield, reduce the waste of straw resources, and have a strong practical effect on the actual saline alkali land improvement in Northeast China.
- A: ["soil physical and chemical properties"]
- B: ["soil structure"]
  - C: NEEDS_HUMAN_REVIEW — Soil structure occurs as a broad concluding benefit without a specified measured structure variable.
  - Contract: measurements explicit measured entity
- Recommended action: NEEDS_HUMAN_REVIEW

## #7 R07 Unspecified 27.80% outcome

- Affected UIDs (1): WOS:001047181300014
- Field: /evidence/findings
- Cases: 1

- Case D035 (`WOS:001047181300014`)
- Abstract context: 65%, and 6 treatments had the highest traumatic discharge, 29.96% higher than CK. At the same time, biochar and straw returning to the soil can effectively improve the organic matter in the soil and reduce its pH value. Compared with CK, the highest increase is 27.80%. In general, the use of biological carbon and straw return can effectively optimize the soil structure, increase the yield, reduce the waste of straw resources, and have a
- A: ["Compared with CK, the highest increase is 27.80%."]
- B: []
  - C: NEEDS_HUMAN_REVIEW — The 27.80% increase has no unambiguous measured referent in the displayed context.
  - Contract: findings explicit outcome
- Recommended action: NEEDS_HUMAN_REVIEW

## #8 R08 Recommendation versus applied treatment

- Affected UIDs (2): WOS:001067878900006, WOS:001366379700001
- Field: /evidence/treatments/biological_treatments; /evidence/treatments/irrigation
- Cases: 2

- Case D037 (`WOS:001067878900006`)
- Abstract context: dments can be a pragmatic solution for improving soil physico-chemical and biological properties and sustaining crop productivity. Municipal solid waste compost (MSWC) available in abundant quantity if enriched with the efficient halophilic microbial consortium and used in conjunction with a reduced dose of gypsum can be a cost-effective approach for sustainable reclamation of alkali soils and harnessing their productivity potential. He
- A: []
- B: ["efficient halophilic microbial consortium"]
  - C: NEEDS_HUMAN_REVIEW — The consortium occurs in a conditional management proposal; the snippet ends before establishing actual application.
  - Contract: treatments biological role

- Case D078 (`WOS:001366379700001`)
- Abstract context: nic–mineral amendments of 4 tons/fed of compost plus 400 kg/fed of mineral sulfur and 5 kg/fed of humic acid plus natural rock fertilizer is the best safe management for reclamation and improvement of saline soils using partially treated saline irrigation water and natural resources.
- A: []
- B: ["partially treated saline irrigation water"]
  - C: NEEDS_HUMAN_REVIEW — Partially treated saline irrigation water appears only in a concluding recommendation; actual irrigation application is not established in this snippet.
  - Contract: treatments applied role
- Recommended action: NEEDS_HUMAN_REVIEW

## #9 R09 Zn measurement compartment

- Affected UIDs (1): WOS:001083747800001
- Field: /evidence/measurements/plant_growth
- Cases: 3

- Case D040 (`WOS:001083747800001`)
- Abstract context: nd 33.9, respectively. Maximum chlorophyll contents, photosynthetic rate, transpiration rate, stomatal conductance, and sub-stomatal carbon dioxide were with the application of ZnTrp to rice grown on salt affected soil. Maximum increase in Zn concentration in soil (391.3 %), roots (251.8 %), shoots (232.9 %), and paddy (287.8 %) were increased with the application of ZnTrp at 12 mg/kg compared to that with control. Maximum decrease in p
- A: ["growth, nutrient uptake, and Zn fortification in rice plants", "Zn concentration"]
- B: ["roots", "shoots", "paddy"]
  - C: NEEDS_HUMAN_REVIEW — Zn concentration is reported jointly for soil and several plant tissues; the bare candidate does not distinguish the compartments.
  - Contract: measurements.plant_growth tissue linkage

- Case D041 (`WOS:001083747800001`)
- Abstract context: tosynthetic rate, transpiration rate, stomatal conductance, and sub-stomatal carbon dioxide were with the application of ZnTrp to rice grown on salt affected soil. Maximum increase in Zn concentration in soil (391.3 %), roots (251.8 %), shoots (232.9 %), and paddy (287.8 %) were increased with the application of ZnTrp at 12 mg/kg compared to that with control. Maximum decrease in phytic acid in rice paddy was observed with the applicati
- A: ["growth, nutrient uptake, and Zn fortification in rice plants", "Zn concentration"]
- B: ["roots", "shoots", "paddy"]
  - C: NEEDS_HUMAN_REVIEW — Roots identify a tissue, while the actual variable is Zn concentration; the bare tissue label is not a measured variable.
  - Contract: measurements preserve measured entity

- Case D042 (`WOS:001083747800001`)
- Abstract context:  transpiration rate, stomatal conductance, and sub-stomatal carbon dioxide were with the application of ZnTrp to rice grown on salt affected soil. Maximum increase in Zn concentration in soil (391.3 %), roots (251.8 %), shoots (232.9 %), and paddy (287.8 %) were increased with the application of ZnTrp at 12 mg/kg compared to that with control. Maximum decrease in phytic acid in rice paddy was observed with the application of ZnTrp at hi
- A: ["growth, nutrient uptake, and Zn fortification in rice plants", "Zn concentration"]
- B: ["roots", "shoots", "paddy"]
  - C: NEEDS_HUMAN_REVIEW — Shoots identify a tissue, while the actual variable is Zn concentration; the bare tissue label is not a measured variable.
  - Contract: measurements preserve measured entity
- Recommended action: NEEDS_HUMAN_REVIEW

## #10 R10 Floodwater EC category

- Affected UIDs (1): WOS:001224081200001
- Field: /evidence/measurements/other; /evidence/measurements/soil_chemical
- Cases: 2

- Case D049 (`WOS:001224081200001`)
- Abstract context: olatilization and DOM using a soil column experiment. The results revealed that soil NH3 volatilization significantly increased by 56.1 % in the treatment with 1.5 % (w/w) HBC compared to the control without PBC or HBC. Conversely, PBC and the lower application rate of HBC led to decreases in NH3 volatilization ranging from 2.4 % to 12.1 %. Floodwater EC is a dominant factor in NH3 emission. Furthermore, the fluorescence intensities of
- A: ["Floodwater EC"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Floodwater EC is a water property, and the supplied soil-chemical definition does not explicitly resolve its boundary with other.
  - Contract: measurements.soil_chemical

- Case D050 (`WOS:001224081200001`)
- Abstract context: ased by 56.1 % in the treatment with 1.5 % (w/w) HBC compared to the control without PBC or HBC. Conversely, PBC and the lower application rate of HBC led to decreases in NH3 volatilization ranging from 2.4 % to 12.1 %. Floodwater EC is a dominant factor in NH3 emission. Furthermore, the fluorescence intensities of the four fractions (all humic substances) were found to be significantly higher in the 1.5 % (w/w) HBC treatment applied co
- A: []
- B: ["Floodwater EC"]
  - C: NEEDS_HUMAN_REVIEW — Floodwater EC may require a chemical-property category; the supplied boundaries do not settle water versus soil chemistry.
  - Contract: OTHER_IS_LAST_RESORT
- Recommended action: NEEDS_HUMAN_REVIEW

## #11 R11 Cellular damage measurement support

- Affected UIDs (1): WOS:001298590200001
- Field: /evidence/measurements/plant_growth
- Cases: 1

- Case D060 (`WOS:001298590200001`)
- Abstract context: Soil salinization, a rising issue globally, is a negative effect of the ever-changing climate, which has drawn attention to, and exacerbated problems related to soil degradation and the decline in wetland rice (Oryza sativa L.) production, leading to an unstable national economy.The use of rhizosphere inhabiting microorganisms (plant growth-promoting rhizobacteria, PGPR) is a viable method for boosting agricultural production on saline
- A: ["cellular damage"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Cellular damage appears in an explanatory protection claim; no explicit damage assay or measured variable is established.
  - Contract: measurements explicit measured entity
- Recommended action: NEEDS_HUMAN_REVIEW

## #12 R12 Generic limitation versus study limitation

- Affected UIDs (1): WOS:001304976800001
- Field: /evidence/limitations_explicit
- Cases: 1

- Case D067 (`WOS:001304976800001`)
- Abstract context: ovement and utilization of saline–alkali soil contribute to soil health improvement, concentrating on high-quality development, food security, ecological security, cultivated land protection, and agricultural upgrading. However, the risks of various technologies in the practical production process should be highlighted; green and healthy measures are still expected to be applied to saline–alkali land.
- A: []
- B: ["However, the risks of various technologies in the practical production process should be highlighted; green and healthy measures are still expected to be applied to saline–alkali land."]
  - C: NEEDS_HUMAN_REVIEW — This is a generic risk warning and future expectation; no specific study limitation is established and retained schema boundaries are not supplied.
  - Contract: explicit limitations
- Recommended action: NEEDS_HUMAN_REVIEW

## #13 R13 DOM optical index category

- Affected UIDs (1): WOS:001488980100001
- Field: /evidence/measurements/carbon; /evidence/measurements/other
- Cases: 4

- Case D101 (`WOS:001488980100001`)
- Abstract context: ly with concentrations of nitrite ([NO2--N]) and nitrate ([NO3--N]), with photolysis of NO2- and NO3-, and DOM contributing 19.50 %, 6.93 %, and 73.57 % to •OH formation, respectively. [RIs]ss positively correlated with fluorescence index and negatively with autochthonous index, indicating exogenous DOM as a major RIs source. Additionally, [•OH]ss was linked to aromatic content and DOM molecular weight, highlighting the importance of DO
- A: ["triplet excited-state dissolved organic matter"]
- B: ["fluorescence index", "autochthonous index"]
  - C: NEEDS_HUMAN_REVIEW — The index is linked to DOM source inference, but no explicit carbon property is specified; Contract v2 does not conclusively classify this optical index.
  - Contract: measurements.carbon versus other

- Case D102 (`WOS:001488980100001`)
- Abstract context: --N]) and nitrate ([NO3--N]), with photolysis of NO2- and NO3-, and DOM contributing 19.50 %, 6.93 %, and 73.57 % to •OH formation, respectively. [RIs]ss positively correlated with fluorescence index and negatively with autochthonous index, indicating exogenous DOM as a major RIs source. Additionally, [•OH]ss was linked to aromatic content and DOM molecular weight, highlighting the importance of DOM structure in •OH production. These fi
- A: ["triplet excited-state dissolved organic matter"]
- B: ["fluorescence index", "autochthonous index"]
  - C: NEEDS_HUMAN_REVIEW — The source-related optical index is explicit but its status as a carbon property is not established by the supplied boundaries.
  - Contract: measurements.carbon versus other

- Case D104 (`WOS:001488980100001`)
- Abstract context: ce cultivation water from a typical saline-alkali region in China. Singlet oxygen was more pH-sensitive than hydroxyl radical (•OH), while triplet excited-state dissolved organic matter were more influenced by salinity. Steady-state •OH concentration ([•OH]ss) correlated strongly with concentrations of nitrite ([NO2--N]) and nitrate ([NO3--N]), with photolysis of NO2- and NO3-, and DOM contributing 19.50 %, 6.93 %, and 73.57 % to •OH fo
- A: ["fluorescence index", "autochthonous index"]
- B: ["triplet excited-state dissolved organic matter"]
  - C: NEEDS_HUMAN_REVIEW — The DOM optical index boundary with carbon requires clarification before other is used.
  - Contract: OTHER_IS_LAST_RESORT

- Case D105 (`WOS:001488980100001`)
- Abstract context: ce cultivation water from a typical saline-alkali region in China. Singlet oxygen was more pH-sensitive than hydroxyl radical (•OH), while triplet excited-state dissolved organic matter were more influenced by salinity. Steady-state •OH concentration ([•OH]ss) correlated strongly with concentrations of nitrite ([NO2--N]) and nitrate ([NO3--N]), with photolysis of NO2- and NO3-, and DOM contributing 19.50 %, 6.93 %, and 73.57 % to •OH fo
- A: ["fluorescence index", "autochthonous index"]
- B: ["triplet excited-state dissolved organic matter"]
  - C: NEEDS_HUMAN_REVIEW — The DOM source index boundary with carbon requires clarification before other is used.
  - Contract: OTHER_IS_LAST_RESORT
- Recommended action: NEEDS_HUMAN_REVIEW

## #14 R14 Genotype as treatment or study material

- Affected UIDs (1): WOS:001503693000001
- Field: /evidence/treatments/other_treatments
- Cases: 1

- Case D108 (`WOS:001503693000001`)
- Abstract context: promote carbon neutrality, cultivating Miscanthus on marginal land, especially in saline soils in China, is a recommended strategy. However, the adaptability of Miscanthus species in saline soil remains largely unknown. In this study, a total of 354 genotypes, including Miscanthus sinensis, Miscanthus floridulus, Miscanthus sacchariflorus, Miscanthus lutarioriparius and interspecific species hybrids derived from M. sinensis and M. lutar
- A: ["354 genotypes"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Genotypes are evaluated, but the supplied contract does not establish whether sampled genetic material belongs under other treatments.
  - Contract: treatments versus study system
- Recommended action: NEEDS_HUMAN_REVIEW

## #15 R15 Conidia characterization versus microbial measure

- Affected UIDs (1): WOS:001586749800001
- Field: /evidence/measurements/microbial
- Cases: 1

- Case D126 (`WOS:001586749800001`)
- Abstract context: perties of soils. At 1%, both biochar enhanced carboxymethylcellulase and filter paperase activity. Alternative alginate rice straw biochar also increased both cellulolytic enzymes at the highest salinity (EC: 40 dS/m). Scanning electron microscopy and energy dispersive X-ray spectroscopy encountered conidia trapped within the bead with the components: C, O, and Si. When applied to salt affected soil (EC 20 dS/m), the soil amendments in
- A: ["conidia trapped within the bead", "microbial viability and function"]
- B: []
  - C: NEEDS_HUMAN_REVIEW — Microscopy identifies trapped inoculum conidia, but the snippet does not establish a microbial abundance, function or ecology measurement rather than material characterization.
  - Contract: measurements.microbial
- Recommended action: NEEDS_HUMAN_REVIEW

## #16 R16 Straw decomposition category

- Affected UIDs (1): WOS:001586749800001
- Field: /evidence/measurements/other
- Cases: 1

- Case D128 (`WOS:001586749800001`)
- Abstract context:  salt-affected soil, hence improving soil fertility and contributing to agricultural sustainability. • Cellulolytic fungus was incorporated by sodium alginate and biochar from rice straw or bamboo as effective inoculum. • Alginate-biochar-immobilized spored increased rice straw decomposition by enhancing cellulase activity, even in high salinity condition. • Combining alginate-biochar immobilized fungus and soil amendment significantly
- A: ["rice straw decomposition"]
- B: ["protease", "fluorescein diacetate hydrolase", "invertase"]
  - C: NEEDS_HUMAN_REVIEW — Straw decomposition could represent carbon turnover or microbial function; the candidate and supplied boundaries do not uniquely settle its category.
  - Contract: measurements specific-category priority
- Recommended action: NEEDS_HUMAN_REVIEW

## #17 R17 Amendment material-property categories

- Affected UIDs (1): WOS:001619334500001
- Field: /evidence/measurements/other
- Cases: 2

- Case D139 (`WOS:001619334500001`)
- Abstract context:  growth, using pristine rice straw (PRB) and maize straw biochars (PMB), and their nitric acid-modified counterparts (HRB and HMB). Compared to pristine biochar, acid-modified biochar exhibited significant reductions in total salinity content and pH, while introducing new functional groups such as N-O, quaternary-N, and pyridinic-N. The acid-modified biochar demonstrated significantly greater improvement effects on saline-alkali soil re
- A: []
- B: ["total salinity content", "N-O, quaternary-N, and pyridinic-N"]
  - C: NEEDS_HUMAN_REVIEW — Biochar salinity is explicit, but the supplied contract does not settle material salt characterization versus a chemical category for substrates.
  - Contract: OTHER_IS_LAST_RESORT

- Case D140 (`WOS:001619334500001`)
- Abstract context: eir nitric acid-modified counterparts (HRB and HMB). Compared to pristine biochar, acid-modified biochar exhibited significant reductions in total salinity content and pH, while introducing new functional groups such as N-O, quaternary-N, and pyridinic-N. The acid-modified biochar demonstrated significantly greater improvement effects on saline-alkali soil remediation and cotton plant growth than pristine biochar. Relative to the CK (no
- A: []
- B: ["total salinity content", "N-O, quaternary-N, and pyridinic-N"]
  - C: NEEDS_HUMAN_REVIEW — Nitrogen-containing functional groups are measured material properties; the retained nitrogen boundary does not settle nitrogen characterization versus other.
  - Contract: measurements specific-category priority
- Recommended action: NEEDS_HUMAN_REVIEW

## #18 R18 Mixed microbial/nitrogen compound measures

- Affected UIDs (1): WOS:001839277700002
- Field: /evidence/measurements/microbial
- Cases: 2

- Case D161 (`WOS:001839277700002`)
- Abstract context: y reports, for the first time, the sulphur oxidation (S°–oxidation) capability of Acinetobacter lwoffii strain 5M1, isolated from highly saline soil (electrical conductivity of 36 dS m⁻¹) surrounding a hot-water spring. The strain released appreciable amounts of plant growth-promoting compounds, including ammonia and indole–3–acetic acid (IAA). A. lwoffii 5M1 formed biofilms around S°–particles in growth media. It harbours the soxA (720
- A: ["ammonia and indole–3–acetic acid (IAA)", "S°-oxidation"]
- B: ["plant growth-promoting compounds"]
  - C: NEEDS_HUMAN_REVIEW — The grouped compounds include an N analyte and a microbial metabolite; the combined label cannot be uniquely routed under the supplied decision order.
  - Contract: measurements specific-category priority

- Case D163 (`WOS:001839277700002`)
- Abstract context: oxidation (S°–oxidation) capability of Acinetobacter lwoffii strain 5M1, isolated from highly saline soil (electrical conductivity of 36 dS m⁻¹) surrounding a hot-water spring. The strain released appreciable amounts of plant growth-promoting compounds, including ammonia and indole–3–acetic acid (IAA). A. lwoffii 5M1 formed biofilms around S°–particles in growth media. It harbours the soxA (720 bp), soxB (590 bp), and soxY (329 bp) gene
- A: ["ammonia and indole–3–acetic acid (IAA)", "S°-oxidation"]
- B: ["plant growth-promoting compounds"]
  - C: NEEDS_HUMAN_REVIEW — The generic compounds label overlaps explicit ammonia and IAA, whose specific-analyte routing needs clarification.
  - Contract: measurements preserve measured entity
- Recommended action: NEEDS_HUMAN_REVIEW

## #19 R19 Experimental-scale explicit support

- Affected UIDs (6): WOS:000794189500001, WOS:000899201500001, WOS:001332147700001, WOS:001453401200001, WOS:001461814100001, WOS:001745283700001
- Field: /evidence/study_system/experimental_scale
- Cases: 6

- Case WOS:000794189500001 (`WOS:000794189500001`)
- Abstract context: The selected scale assertion did not pass the frozen validator; candidate provisionally leaves it unknown.
- A: "field"
- B: "field"

- Case WOS:000899201500001 (`WOS:000899201500001`)
- Abstract context: The selected scale assertion did not pass the frozen validator; candidate provisionally leaves it unknown.
- A: "field"
- B: "field"

- Case WOS:001332147700001 (`WOS:001332147700001`)
- Abstract context: The selected scale assertion did not pass the frozen validator; candidate provisionally leaves it unknown.
- A: "pot"
- B: "pot"

- Case WOS:001453401200001 (`WOS:001453401200001`)
- Abstract context: The selected scale assertion did not pass the frozen validator; candidate provisionally leaves it unknown.
- A: "field"
- B: "field"
- Additional cases in this group: 2; see the linked C item IDs in the workflow manifest/artifacts.
- Recommended action: NEEDS_HUMAN_REVIEW
