import hashlib, json, unittest
from pathlib import Path
from scripts.extractors.schema_validator_v2 import scale_supported
from scripts.evidence.validate_v07_gold_annotator import read_jsonl
ROOT=Path(__file__).resolve().parents[1]
FINAL=ROOT/'data/evidence_benchmarks/v07_dev40_gold_final'
WORK=ROOT/'data/evidence_benchmarks/v07_dev40_gold_work'
CARDS=ROOT/'data/reports/v07_dev40_r19_scale_evidence_cards.json'
class V07FinalGoldV21Tests(unittest.TestCase):
 def test_r19_context_scale_regressions(self):
  positive={'field':['We conducted field experiments in saline soil.','This study conducted experiments in 2022 in saline rice integrated fields.','This study investigated soil properties in a reclaimed coastal field.','A field-based experiment was performed on a farm.','Field-scale experiments were conducted on cropland.','The field scale study monitored soil salinity.','A field study sampled agricultural soils.','A field experiment was performed in rice paddies.'],'pot':['A pot experiment was conducted.','Salinity levels were maintained in respective pots.','Plants were grown in pots and treated with saline water.'],'greenhouse':['The greenhouse experiment was conducted for rice.'],'lab':['Laboratory incubation was conducted for 30 days.'],'model':['This was a simulation study.','A model-based study evaluated the system.','Modeling study results are presented.']}
  for scale,quotes in positive.items():
   for quote in quotes:
    with self.subTest(scale=scale,quote=quote): self.assertTrue(scale_supported(scale,quote))
  negatives=['The research field may benefit from these findings.','Field application may improve productivity.','Field conditions are important background context.','HYDRUS model was used.','A logistic regression model was applied.','Random forest model predicted yield.']
  for quote in negatives:
   with self.subTest(quote=quote): self.assertFalse(scale_supported('field',quote)); self.assertFalse(scale_supported('model',quote))
  self.assertFalse(scale_supported('pot','Pots were mentioned in the introduction.'))
 def test_r19_final_spans_and_gold_validate_under_v2(self):
  report=json.loads((FINAL/'gold_validation.json').read_text()); cards=json.loads(CARDS.read_text()); final={r['uid']:r for r in read_jsonl(FINAL/'candidate_gold.jsonl')}; abstracts={r['uid']:r for r in read_jsonl(WORK/'abstracts.jsonl')}
  self.assertEqual(report['r19_scale_valid'],6)
  for card in cards:
   r=final[card['uid']]; quote=card['selected_evidence_text']; self.assertEqual(abstracts[card['uid']]['abstract'][card['start']:card['end']],quote)
   self.assertEqual(r['evidence']['study_system']['experimental_scale'],{'WOS:001332147700001':'pot'}.get(card['uid'],'field'))
   self.assertEqual(r['evidence_support']['/evidence/study_system/experimental_scale'],{'source':'abstract','evidence_text':quote,'start':card['start'],'end':card['end']})
  self.assertEqual(report['schema_valid'],40); self.assertEqual(report['grounding_valid'],40); self.assertEqual(report['invalid_offset_count'],0); self.assertEqual(report['orphan_anchor_count'],0); self.assertEqual(report['missing_support_count'],0)
 def test_final_gold_freeze_status_and_dev_identity(self):
  m=json.loads((FINAL/'gold_freeze_manifest.json').read_text()); self.assertEqual(m['gold_status'],'human_approved_development_gold'); self.assertEqual(m['identities'],40); self.assertTrue(m['human_review_complete']); self.assertEqual(m['unresolved_count'],0); self.assertTrue(m['contract_v2_1_gold_freeze_ready'])
  expected={r['uid'] for r in json.loads((ROOT/'data/evidence_benchmarks/v07_dev40/identities.json').read_text())}; actual={r['uid'] for r in read_jsonl(FINAL/'candidate_gold.jsonl')}; self.assertEqual(actual,expected); self.assertEqual(m['identity_errors'],0)
 def test_contract_v21_frozen_and_findings_rule_preserved(self):
  p=ROOT/'docs/evidence_field_contract_v2_1.md'; text=p.read_text(); self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),'75fc4b301b598250d159de99c916959999297ecaf512a23a914f74991ae1ffad')
  for x in ['HUMAN_APPROVED','VERSION = `2.1`','SCHEMA = `Evidence Matrix v0.4 unchanged`','APPROVED_USING_DEVELOPMENT_DATA = `true`','FUTURE_HOLDOUT_ACCESSED = `false`','PRESERVE_BY_DEFAULT','Harvested product quality','Nutrient and productivity indices','Tissue-specific nutrient measurements','Water chemistry','Amendment and material characterization','DOM optical/source indices','Genotype/cultivar experimental factor','Microbial functional metabolites','Broad umbrella measurements','Explicit limitations','Experimental scale']: self.assertIn(x,text)
  self.assertEqual(hashlib.sha256((ROOT/'docs/evidence_field_contract_v2.md').read_bytes()).hexdigest(),'9823606637e1c61a19546b720d052368cbe7a9c85825a1ce2ae41c3194208dab')
 def test_v1_validator_immutable_and_holdout_untouched(self):
  self.assertEqual(hashlib.sha256((ROOT/'scripts/extractors/schema_validator.py').read_bytes()).hexdigest(),'58735927376d007f5bf004b9bf62cb5dc6acb56cc33a60ab0cb7f856740e95a1'); m=json.loads((FINAL/'gold_freeze_manifest.json').read_text())
  self.assertFalse(m['future_holdout_abstracts_accessed']); self.assertFalse(m['future_holdout_gold_created']); self.assertFalse(m['future_holdout_extraction_run']); self.assertFalse(m['prompt_modified']); self.assertFalse(m['dev40_extraction_run'])
 def test_all_review_log_decisions_are_human_and_r19_six_complete(self):
  log=[json.loads(x) for x in (FINAL/'human_decision_log.jsonl').read_text().splitlines()]; self.assertTrue(all(x['human_reviewed'] and x['approved_by']=='human' for x in log)); r19=[x for x in log if x['field']=='study_system.experimental_scale']; self.assertEqual(len(r19),6); self.assertTrue(all(x['decision']=='ACCEPT' for x in r19))
if __name__=='__main__': unittest.main()
