import hashlib,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/evidence_benchmarks/v07_future_holdout30_gold_work'
CAND=ROOT/'data/evidence_benchmarks/v07_future_holdout30_gold_candidate'
FREEZE=ROOT/'data/evidence_benchmarks/v07_final_system_freeze_manifest.json'
PACKET=ROOT/'data/reports/v07_future_holdout30_gold_human_review.md'
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
class V07FutureGoldCandidateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.freeze=json.loads(FREEZE.read_text()); cls.manifest=json.loads((CAND/'candidate_manifest.json').read_text()); cls.validation=json.loads((CAND/'validation_v2.json').read_text())
 def test_three_fresh_blind_workers_and_minimal_bundles(self):
  a=BASE/'annotator_A'; b=BASE/'annotator_B'; c=BASE/'adjudicator_C'
  allowed_ab={'GOLD_ANNOTATION_PROTOCOL.md','TASK.md','evidence_field_contract_v2_1.md','evidence_matrix.schema.json','requests.jsonl','annotations.jsonl'}
  self.assertEqual({p.name for p in a.iterdir()},allowed_ab); self.assertEqual({p.name for p in b.iterdir()},allowed_ab)
  self.assertEqual((a/'requests.jsonl').read_bytes(),(b/'requests.jsonl').read_bytes())
  self.assertNotIn('annotations.jsonl',{p.name for p in b.iterdir() if p.name.startswith('annotator_A')})
  self.assertEqual({p.name for p in c.iterdir()},{'adjudication_requests.jsonl','evidence_field_contract_v2_1.md','GOLD_ADJUDICATION_PROTOCOL.md','TASK.md','adjudications.jsonl','disagreements.json'})
  wf=json.loads((CAND/'workflow_manifest.json').read_text()); self.assertTrue(wf['annotators']['A']['fresh_ephemeral']);self.assertTrue(wf['annotators']['B']['fresh_ephemeral']);self.assertTrue(wf['adjudicator_C']['fresh_ephemeral'])
  for p in (a,b,c):
   self.assertFalse(any('evidence_extraction_v07_final' in x.name for x in p.iterdir()))
 def test_exact_30_identities_for_inputs_annotations_and_candidate(self):
  identities=json.loads((ROOT/'data/evidence_benchmarks/v07_future_holdout30/identities.json').read_text()); uids={x['uid'] for x in identities}; self.assertEqual(len(uids),30)
  for p in [BASE/'requests.jsonl',BASE/'annotator_A/annotations.jsonl',BASE/'annotator_B/annotations.jsonl',CAND/'candidate_gold.jsonl']:
   items=rows(p); self.assertEqual(len(items),30,p.name); self.assertEqual({x['uid'] for x in items},uids,p.name)
 def test_adjudicator_covers_every_ab_disagreement_once(self):
  req=rows(BASE/'adjudicator_C/adjudication_requests.jsonl'); dec=rows(BASE/'adjudicator_C/adjudications.jsonl')
  self.assertEqual(len(req),193); self.assertEqual(len(dec),193); self.assertEqual({x['id'] for x in req},{x['id'] for x in dec}); self.assertEqual(len({x['id'] for x in dec}),193)
  self.assertTrue(all(x['decision'] in {'A','B','MODIFY','NEEDS_HUMAN_REVIEW'} for x in dec))
 def test_candidate_validation_and_review_required(self):
  c=self.validation['counts']; self.assertEqual(c['records'],30); self.assertEqual(c['identity_errors'],0); self.assertEqual(c['missing_records'],0); self.assertEqual(c['schema_valid'],30); self.assertEqual(c['grounding_valid'],27); self.assertEqual(c['invalid_offsets'],0); self.assertEqual(c['orphan_anchors'],0); self.assertEqual(c['missing_supports'],0); self.assertEqual(c['unsupported'],3)
  self.assertEqual(self.manifest['status'],'ai_assisted_holdout_gold_candidate'); self.assertEqual(self.manifest['gold_status'],'ai_assisted_holdout_gold_candidate'); self.assertFalse(self.manifest['human_approved']); self.assertFalse(self.manifest['owner_human_review_complete'])
  self.assertEqual(len(self.manifest['needs_human_review_uids']),3)
 def test_full_owner_packet_covers_all_30_with_abstracts_and_candidate(self):
  text=PACKET.read_text(); self.assertEqual(text.count('## Paper '),30); self.assertIn('Required owner action',text); self.assertIn('REVIEW_REQUIRED',text); self.assertIn('Proposed Evidence Matrix',text); self.assertIn('Abstract (complete)',text); self.assertEqual(digest(PACKET),self.manifest['review_packet_sha256'])
  self.assertNotIn('evidence_extraction_v07_final',text); self.assertNotIn('overall field micro-F1',text)
 def test_contamination_ledger_no_target_extraction_or_scoring(self):
  m=json.loads(FREEZE.read_text()); self.assertTrue(m['V07_FINAL_PROMPT_FROZEN']); self.assertTrue(m['STOP_PROMPT_TUNING']); self.assertTrue(m['future_holdout_abstracts_accessed']); self.assertFalse(m['gold_builders_received_final_prompt']); self.assertFalse(m['target_extraction_run']); self.assertFalse(m['target_predictions_generated']); self.assertFalse(m['benchmark_or_score_run']); self.assertFalse(m['owner_human_review_complete'])
  self.assertFalse((ROOT/'data/evidence_batches/v07_future_holdout30').exists())
  for key,name in [('baseline','baseline'),('i1','i1'),('i2','i2')]: self.assertEqual(digest(ROOT/f'data/evidence_batches/v07_dev40_{name}/worker_response_sha256.json'),m['development_response_sha256'][key])
 def test_original_dev40_gold_content_unchanged(self):
  self.assertEqual(digest(ROOT/'data/evidence_benchmarks/v07_dev40_gold_final/candidate_gold.jsonl'),self.freeze['artifact_sha256']['dev40_gold_reference'])
if __name__=='__main__': unittest.main()
