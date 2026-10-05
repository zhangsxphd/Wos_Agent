import hashlib, json, re, unittest
from pathlib import Path
from scripts.extractors.schema_validator_v2 import EvidenceValidator, SCALE_PATTERNS, scale_supported
from scripts.evidence.validate_v07_gold_annotator import validate_file

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/evidence_benchmarks/v07_dev40_gold_candidate'
CAND=ROOT/'data/evidence_benchmarks/v07_dev40_gold_candidate_r18'
WORK=ROOT/'data/evidence_benchmarks/v07_dev40_gold_work'

class Phase1CTests(unittest.TestCase):
 def test_v2_contract_separate_and_v2_hash_unchanged(self):
  self.assertEqual(hashlib.sha256((ROOT/'docs/evidence_field_contract_v2.md').read_bytes()).hexdigest(),'9823606637e1c61a19546b720d052368cbe7a9c85825a1ce2ae41c3194208dab')
  self.assertEqual(hashlib.sha256((ROOT/'scripts/extractors/schema_validator.py').read_bytes()).hexdigest(),'58735927376d007f5bf004b9bf62cb5dc6acb56cc33a60ab0cb7f856740e95a1')
  p=ROOT/'docs/evidence_field_contract_v2_1_candidate.md'; self.assertTrue(p.exists()); self.assertIn('DEVELOPMENT_AMENDMENT_CANDIDATE',p.read_text())
 def test_decision_routing_and_r19_pending(self):
  rows={json.loads(x)['uid']:json.loads(x) for x in (CAND/'candidate_gold.jsonl').read_text().splitlines()}
  self.assertEqual(rows['WOS:000730402400009']['evidence']['treatments']['amendments'],[])
  self.assertEqual(rows['WOS:000899201500001']['evidence']['measurements']['nitrogen'],[])
  self.assertIn('partial fertilizer (N, P, and K) productivity',rows['WOS:000899201500001']['evidence']['measurements']['other'])
  self.assertIn('nitrogen (N) use efficiency (NUE)',rows['WOS:000730402400009']['evidence']['measurements']['nitrogen'])
  self.assertIn('Floodwater EC',rows['WOS:001224081200001']['evidence']['measurements']['other'])
  self.assertEqual(rows['WOS:001224081200001']['evidence']['study_system']['experimental_scale'],'unknown')
  log=json.loads((CAND/'human_decision_log.json').read_text()); self.assertTrue(all(x['human_reviewed'] for x in log)); self.assertTrue(all(x['human_decision'] in {'ACCEPT','REJECT','MODIFY'} for x in log))
 def test_candidate_validates_v2_and_preserves_source(self):
  import scripts.evidence.validate_v07_gold_annotator as module
  original=module.EvidenceValidator
  module.EvidenceValidator=EvidenceValidator
  try: report=validate_file(CAND/'candidate_gold.jsonl',WORK/'abstracts.jsonl',ROOT/'schemas/evidence_matrix.schema.json')
  finally: module.EvidenceValidator=original
  self.assertEqual(report['schema_valid'],40); self.assertEqual(report['grounding_valid'],40)
  self.assertEqual(report['invalid_offset_count'],0); self.assertEqual(report['orphan_anchor_count'],0); self.assertEqual(report['missing_support_count'],0)
  self.assertEqual(hashlib.sha256((BASE/'candidate_gold.jsonl').read_bytes()).hexdigest(),'9db8a90759fe7f1b3d44990f51db99f4734ffe283d294f47b19b057d26cfa686')
  manifest=json.loads((CAND/'candidate_manifest.json').read_text()); self.assertEqual(manifest['status'],'development_gold_candidate'); self.assertFalse(manifest['human_approved'])
 def test_v2_scale_patterns_and_named_model_guard(self):
  examples={'field':['A field study was conducted.','This was a field-scale experiment.','This was a field scale study.','A field-based trial was run.'],'pot':['A pot experiment was performed.'],'greenhouse':['The greenhouse experiment ran for 8 weeks.'],'lab':['Laboratory incubation was used.'],'model':['A model-based study was conducted.']}
  for key,texts in examples.items():
   for text in texts: self.assertRegex(text,re.compile(SCALE_PATTERNS[key],re.I))
  self.assertFalse(scale_supported('model','We used the APSIM model.'))
 def test_r19_packet_contains_only_six_pending_dev_records_and_offsets(self):
  cards=json.loads((ROOT/'data/reports/v07_dev40_r19_scale_evidence_cards.json').read_text()); self.assertEqual(len(cards),6)
  sources={json.loads(x)['uid']:json.loads(x) for x in (WORK/'abstracts.jsonl').read_text().splitlines()}
  for c in cards:
   self.assertEqual(sources[c['uid']]['abstract'][c['start']:c['end']],c['selected_evidence_text'])
  self.assertFalse(any('future_holdout' in str(c) for c in cards))
 def test_findings_strategy_and_holdout_boundary_unchanged(self):
  contract=(ROOT/'docs/evidence_field_contract_v2_1_candidate.md').read_text()
  self.assertIn('PRESERVE_BY_DEFAULT',contract)
  manifest=json.loads((CAND/'candidate_manifest.json').read_text())
  self.assertFalse(manifest['future_holdout_abstracts_accessed']); self.assertFalse(manifest['future_holdout_gold_created']); self.assertFalse(manifest['future_holdout_extraction_run'])
  self.assertEqual(hashlib.sha256((BASE/'candidate_gold.jsonl').read_bytes()).hexdigest(),'9db8a90759fe7f1b3d44990f51db99f4734ffe283d294f47b19b057d26cfa686')

if __name__=='__main__': unittest.main()
