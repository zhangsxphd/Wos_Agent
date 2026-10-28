import hashlib,json,tempfile,unittest
from pathlib import Path
from scripts.review.packet import object_hash
from scripts.review.pilot_web import import_results

class PilotWebImportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();(self.root/'source.pdf').write_bytes(b'original')
  self.source={'path':'source.pdf','sha256':hashlib.sha256(b'original').hexdigest(),'role':'raw'}
  self.tasks=[]
  for typ in ('pilot','abstract'):
   core={'kind':typ,'id':typ+':1','title':'paper','doi':'10.1000/test','content':{},'files':[self.source]}
   self.tasks.append({**core,'object_sha256':object_hash(core)})
  self.mp=self.root/'manifest.json';self.mp.write_text(json.dumps({'version':'pilot-review-web-v1','root':str(self.root),'sources':[self.source],'tasks':self.tasks}))
  self.rp=self.root/'result.json';self.records=[]
  for t in self.tasks:self.records.append({'task_id':t['id'],'kind':t['kind'],'doi':t['doi'],'object_sha256':t['object_sha256'],'occurrence':1,'status':'pending','value':'','notes':'','reviewer':'','reviewed_at':''})
 def tearDown(self):self.tmp.cleanup()
 def run_import(self,entries,out='out'):
  self.rp.write_text(json.dumps({'version':'pilot-review-results-v1','manifest_sha256':hashlib.sha256(self.mp.read_bytes()).hexdigest(),'entries':entries}));return import_results(self.mp,self.rp,self.root/out,self.root)
 def test_pending_and_versioning(self):
  result=self.run_import(self.records);self.assertEqual((result['decided'],result['pending']),(0,2));self.assertFalse(result['model_authorized'])
  with self.assertRaises(FileExistsError):self.run_import(self.records)
 def test_multiple_human_results_stay_distinct(self):
  rows=[dict(x) for x in self.records];rows[0].update(status='reported',value='result one',reviewer='human',reviewed_at='2026-10-28T01:00:00Z')
  extra={**rows[0],'occurrence':2,'value':'result two'};summary=self.run_import(rows+[extra]);self.assertEqual(summary['decided'],2);self.assertEqual(summary['pending'],1)
 def test_stale_source_and_identity_rejected(self):
  rows=[dict(x) for x in self.records];rows[0]['doi']='10.1000/other'
  with self.assertRaises(ValueError):self.run_import(rows)
  (self.root/'source.pdf').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'source_changed'):self.run_import(self.records)
 def test_reported_needs_value_and_absence_needs_reason(self):
  for status in ('reported','not_reported','not_applicable'):
   rows=[dict(x) for x in self.records];rows[0].update(status=status,reviewer='human',reviewed_at='2026-10-28T01:00:00Z')
   with self.subTest(status=status),self.assertRaises(ValueError):self.run_import(rows)
 def test_absence_cannot_contain_reported_value(self):
  rows=[dict(x) for x in self.records];rows[0].update(status='not_reported',value='reported value',notes='searched',reviewer='human',reviewed_at='2026-10-28T01:00:00Z')
  with self.assertRaisesRegex(ValueError,'absence_status_cannot_have_value'):self.run_import(rows)
 def test_wrong_type_and_missing_task_rejected(self):
  rows=[dict(x) for x in self.records];rows[0]['status']='accept';rows[0]['reviewer']='human';rows[0]['reviewed_at']='2026-10-28T01:00:00Z'
  with self.assertRaises(ValueError):self.run_import(rows)
  with self.assertRaisesRegex(ValueError,'missing_base_task'):self.run_import(self.records[:1])

if __name__=='__main__':unittest.main()
