import csv,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.review.packet import object_hash
from scripts.review.import_decisions import import_decisions
from scripts.review.pilot import validate_reference_rows

class TestReviewImport(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
  q=self.root/'data/reports/review_workbench_20261028/quality';q.mkdir(parents=True)
  (q/'quality_rows.jsonl').write_text('');(q/'quality_summary.json').write_text('{}')
  (self.root/'source.pdf').write_bytes(b'test pdf')
  import hashlib
  file={'path':'source.pdf','sha256':hashlib.sha256(b'test pdf').hexdigest(),'role':'raw_file'}
  self.tasks=[]
  for typ in ('abstract','fulltext'):
   obj={'type':typ,'uid':'WOS:1','doi':'10.1234/example','title':'Example','data':{},'files':[file]}
   self.tasks.append({**obj,'object_sha256':object_hash(obj),'status':'pending','decision':'','reviewer':'','reviewed_at':'','notes':''})
  self.manifest={'version':'review-packet-v1','root':str(self.root),'sources':[],'choices':{'abstract':['accept','correct','reject','uncertain'],'fulltext':['identity_confirmed','identity_conflict','needs_reprocessing','uncertain']},'tasks':self.tasks}
  self.mp=self.root/'manifest.json';self.mp.write_text(json.dumps(self.manifest));self.dp=self.root/'return.jsonl'
  self.patcher=patch('scripts.review.import_decisions.verify_snapshot',return_value=True);self.patcher.start()
 def tearDown(self):self.patcher.stop();self.tmp.cleanup()
 def run_import(self,rows,out='out',csv_mode=False):
  fields=['type','uid','doi','title','object_sha256','status','decision','reviewer','reviewed_at','notes']
  if csv_mode:
   path=self.root/'return.csv'
   with path.open('w',newline='') as f:
    w=csv.DictWriter(f,fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
  else:
   path=self.dp;path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
  return import_decisions(self.mp,path,self.root/out,root=self.root)
 def test_pending_and_versioned_outputs(self):
  result=self.run_import(self.tasks);self.assertEqual((result['decided'],result['pending']),(0,2))
  with self.assertRaises(FileExistsError):self.run_import(self.tasks)
 def test_csv_uid_doi_and_hash_checked(self):
  for key,value in [('uid','WOS:2'),('doi','10.9999/no'),('object_sha256','wrong')]:
   rows=[dict(x) for x in self.tasks];rows[0][key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):self.run_import(rows,csv_mode=True)
 def test_separate_types_and_source_changes(self):
  rows=[dict(x) for x in self.tasks];rows[0].update(status='decided',decision='identity_confirmed',reviewer='human',reviewed_at='2026-10-28T00:00:00+00:00')
  with self.assertRaises(ValueError):self.run_import(rows)
  rows[0]['decision']='accept';(self.root/'source.pdf').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.run_import(rows)
 def test_missing_source_no_confirmation(self):
  obj={k:self.tasks[0][k] for k in ('type','uid','doi','title','data','files')};obj['files']=[]
  self.tasks[0]={**obj,'object_sha256':object_hash(obj),'status':'pending','decision':'','reviewer':'','reviewed_at':'','notes':''};self.manifest['tasks']=self.tasks;self.mp.write_text(json.dumps(self.manifest))
  rows=[dict(x) for x in self.tasks];rows[0].update(status='decided',decision='accept',reviewer='human',reviewed_at='2026-10-28T00:00:00+00:00')
  with self.assertRaisesRegex(ValueError,'source_required'):self.run_import(rows)
 def test_valid_review_does_not_upgrade_admission(self):
  rows=[dict(x) for x in self.tasks];rows[0].update(status='decided',decision='accept',reviewer='human',reviewed_at='2026-10-28T00:00:00+00:00')
  result=self.run_import(rows);self.assertEqual(result['decided'],1);self.assertFalse(result['technical_admission_changed'])

class TestReferenceRows(unittest.TestCase):
 def test_multiple_results_and_distinct_absence_statuses(self):
  base={'uid':'WOS:1','field':'result','reviewer':'human'}
  rows=[{**base,'occurrence':1,'status':'reported','value':'1','evidence_text':'1 mg'}, {**base,'occurrence':2,'status':'reported','value':'2','evidence_text':'2 mg'}, {**base,'field':'replicates','occurrence':1,'status':'not_reported'}, {**base,'field':'treatment','occurrence':1,'status':'not_applicable'}]
  self.assertTrue(validate_reference_rows(rows));self.assertNotEqual(rows[2]['status'],rows[3]['status'])
  with self.assertRaises(ValueError):validate_reference_rows(rows+[rows[0]])

if __name__=='__main__':unittest.main()
