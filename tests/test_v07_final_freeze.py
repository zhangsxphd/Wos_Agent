import hashlib, json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/evidence_benchmarks'
MANIFEST=DATA/'v07_final_system_freeze_manifest.json'
class V07FinalFreezeTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.m=json.loads(MANIFEST.read_text())
 def test_final_prompt_is_byte_identical_i2_and_hash_frozen(self):
  a=(ROOT/'prompts/evidence_extraction_v07_i2.md').read_bytes(); b=(ROOT/'prompts/evidence_extraction_v07_final.md').read_bytes()
  self.assertEqual(a,b); self.assertEqual(hashlib.sha256(b).hexdigest(),self.m['final_prompt_sha256']); self.assertTrue(self.m['V07_FINAL_PROMPT_FROZEN']); self.assertTrue(self.m['STOP_PROMPT_TUNING'])
 def test_contract_validator_schema_metrics_frozen(self):
  files={'contract_v2_1':'docs/evidence_field_contract_v2_1.md','validator_v2':'scripts/extractors/schema_validator_v2.py','schema':'schemas/evidence_matrix.schema.json','metric_v1':'scripts/evidence/metrics.py','metric_v2':'scripts/evidence/metrics_v2.py'}
  for k,p in files.items(): self.assertEqual(hashlib.sha256((ROOT/p).read_bytes()).hexdigest(),self.m['artifact_sha256'][k],k)
 def test_protocol_is_preregistered_and_gates_are_explicit(self):
  p=ROOT/'docs/v07_final_evaluation_protocol.md'; text=p.read_text(); self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),self.m['artifact_sha256']['evaluation_protocol'])
  for x in ['Metric v2 corpus/micro F1 >= 0.80','schema, grounding, identity, and response contract','Unsupported field assertions = 0','Findings precision >= 0.95 and recall >= 0.85','no post-hoc per-field gates','owner must review all 30 papers']:
   self.assertIn(x,text)
 def test_only_holdout_identity_registry_hashed_not_abstracts(self):
  p=ROOT/'data/evidence_benchmarks/v07_future_holdout30/identities.json'; rows=json.loads(p.read_text())
  self.assertEqual(len(rows),30); self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),self.m['artifact_sha256']['future_holdout30_identities'])
  self.assertFalse(self.m['future_holdout_abstracts_accessed']); self.assertFalse(self.m['target_extraction_run']); self.assertFalse(self.m['target_predictions_generated']); self.assertFalse(self.m['benchmark_or_score_run'])
 def test_dev40_gold_content_and_historical_v06_gold_unchanged(self):
  p=ROOT/'data/evidence_benchmarks/v07_dev40_gold_final/candidate_gold.jsonl'; self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),self.m['artifact_sha256']['dev40_gold_reference'])
  old=ROOT/'data/evidence_benchmarks/v06_development_gold_final/candidate_gold.jsonl'
  if old.exists(): self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(),'fb58a888e06ee0efb7cba5de9d533b91582fb412697af2bf051ffb361fdf65c4')
 def test_candidate_is_not_approved_and_no_target_artifacts_exist(self):
  self.assertFalse(self.m['owner_human_review_complete']); self.assertEqual(self.m['V07_FINAL_QUALITY_GATE'],'FROZEN_NOT_EVALUATED')
  self.assertFalse((DATA/'v07_future_holdout30_gold_candidate').exists()); self.assertFalse((ROOT/'data/evidence_batches/v07_future_holdout30').exists())
if __name__=='__main__': unittest.main()
