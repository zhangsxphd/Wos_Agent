"""Validate returned human decisions into a new, separate review version."""
import argparse,csv,json
from datetime import datetime
from pathlib import Path
from scripts.fulltext.admission import project_file
from scripts.pipeline_utils import ROOT,read_jsonl,utc_now
from scripts.quality.snapshot import digest,verify_snapshot
from scripts.review.packet import CAN,INV,QUALITY,object_hash

def import_decisions(manifest_file,decisions_file,output,root=ROOT):
 root=Path(root).resolve();manifest_file=Path(manifest_file);decisions_file=Path(decisions_file);output=Path(output)
 manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
 if manifest.get('version')!='review-packet-v1' or manifest.get('root')!=str(root):raise ValueError('review_manifest_invalid')
 for s in manifest['sources']:
  if digest(project_file(root,s['path']))!=s['sha256']:raise ValueError('review_source_changed:'+s['path'])
 quality=read_jsonl(root/QUALITY/'quality_rows.jsonl')
 verify_snapshot(json.loads((root/QUALITY/'quality_summary.json').read_text()),root/QUALITY/'quality_rows.jsonl',root/CAN,root/INV,quality,root)
 expected={}
 for t in manifest['tasks']:
  k=(t['type'],t['uid']);obj={key:t[key] for key in ('type','uid','doi','title','data','files')}
  if k in expected or object_hash(obj)!=t['object_sha256']:raise ValueError('review_manifest_task_invalid')
  expected[k]=t
 if decisions_file.suffix.lower()=='.csv':
  with decisions_file.open(encoding='utf-8-sig',newline='') as f:decisions=list(csv.DictReader(f))
 else:decisions=read_jsonl(decisions_file)
 seen=set();accepted=[];pending=0
 for row in decisions:
  k=(row.get('type'),row.get('uid'));t=expected.get(k)
  if not t or k in seen:raise ValueError('unknown_or_duplicate_review_task')
  seen.add(k)
  if row.get('doi')!=t['doi'] or row.get('object_sha256')!=t['object_sha256'] or row.get('title')!=t['title']:raise ValueError('review_identity_or_hash_mismatch')
  status=row.get('status');decision=row.get('decision') or ''
  if status=='pending' and not decision:
   if row.get('reviewer') or row.get('reviewed_at') or row.get('notes'):raise ValueError('pending_record_has_review_data')
   pending+=1;continue
  if status!='decided' or not isinstance(decision,str) or decision not in manifest['choices'][t['type']]:raise ValueError('review_decision_invalid')
  if not isinstance(row.get('reviewer'),str) or not row['reviewer'].strip():raise ValueError('reviewer_required')
  try:timestamp=datetime.fromisoformat(row['reviewed_at'].replace('Z','+00:00'))
  except (KeyError,ValueError,TypeError,AttributeError):raise ValueError('review_time_invalid') from None
  if timestamp.tzinfo is None or timestamp.utcoffset() is None:raise ValueError('review_time_timezone_required')
  if decision in ('correct','exclude') and not (row.get('notes') or '').strip():raise ValueError('review_notes_required')
  if t['type'] in ('abstract','fulltext') and decision in ('accept','correct','identity_confirmed') and not any(f['role']=='raw_file' for f in t['files']):raise ValueError('source_required_for_confirmation')
  for f in t['files']:
   if digest(project_file(root,f['path']))!=f['sha256']:raise ValueError('review_evidence_changed:'+f['path'])
  accepted.append({key:row.get(key,'') for key in ('type','uid','doi','title','object_sha256','status','decision','reviewer','reviewed_at','notes')})
 if len(seen)!=len(expected):raise ValueError('review_export_incomplete')
 output.mkdir(parents=True,exist_ok=False)
 (output/'validated_decisions.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in accepted),encoding='utf-8')
 result={'version':'review-import-v1','created_at':utc_now(),'source_manifest_sha256':digest(manifest_file),'returned_file_sha256':digest(decisions_file),'validated_decisions_sha256':digest(output/'validated_decisions.jsonl'),'decided':len(accepted),'pending':pending,'technical_admission_changed':False}
 (output/'import_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--decisions',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(import_decisions(a.manifest,a.decisions,a.output),ensure_ascii=False,indent=2))
