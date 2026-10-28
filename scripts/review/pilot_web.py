"""Create a local, offline, source-bound interface for the first-paper review."""
import argparse, json
from datetime import datetime
from pathlib import Path
from scripts.fulltext.admission import project_file
from scripts.pipeline_utils import ROOT,read_jsonl,utc_now
from scripts.quality.snapshot import digest
from scripts.review.packet import object_hash

BASE='data/reports/pilot_first_20261028'
MANIFEST='data/reviews/v07_review_round1/review_manifest.json'
IMPORTED='data/reviews/imports/v07_round1_20261028_user/validated_decisions.jsonl'
RAW='data/fulltext/raw/da4b840de18c6966df14b7fc301203cc7e471eff93ee7c8e86677f4f4f917470.pdf'
PARSED='data/fulltext/parsed/da4b840de18c6966df14b7fc301203cc7e471eff93ee7c8e86677f4f4f917470.pypdf-page-v2-fonttools.json'
LABELS={'study_object':'研究对象与地点','design':'试验设计','treatments':'处理措施与水平','control':'对照组','replicates':'试验重复次数','sampling_replicates':'采样重复次数','measures':'测量指标','yield_results':'产量比较结果','yield_table':'产量原文表格','significance':'显著性文字','contradiction_yield':'产量文字前后冲突','contradiction_as':'砷显著性表述冲突','author_conclusion':'作者结论','conflict_total_nitrogen':'总氮数值与表格不一致'}
PAGES={1:'page01-pdfium.png',3:'page03.png',5:'page05.png',7:'page-07.png',8:'page-08.png',9:'page09-pdfium.png'}

def safe_read(root,name):return project_file(root,name)
def source(root,name,role):return {'path':name,'sha256':digest(safe_read(root,name)),'role':role}
def task(kind,key,title,doi,content,files):
 obj={'kind':kind,'id':key,'title':title,'doi':doi,'content':content,'files':files};return {**obj,'object_sha256':object_hash(obj)}

def anchor(parsed,sid,pid,phrase,size):
 para=next(x for x in next(s for s in parsed['sections'] if s['section_id']==sid)['paragraphs'] if x['paragraph_id']==pid)
 start=para['text'].find(phrase)
 if start<0:raise ValueError('anchor_not_found:'+phrase)
 end=min(start+size,len(para['text']))
 return {'page':int(sid.split('-')[1]),'section_id':sid,'paragraph_id':pid,'char_start':start,'char_end':end,'evidence_text':para['text'][start:end]}

def build(output,root=ROOT):
 root=Path(root).resolve();output=Path(output).resolve()
 try:relative_output=output.relative_to(root)
 except ValueError:raise ValueError('output_must_be_in_project')
 if len(relative_output.parts)!=3 or relative_output.parts[:2]!=('data','reviews'):raise ValueError('output_must_be_data_reviews_round')
 parsed=json.loads(safe_read(root,PARSED).read_text());rawsha=digest(safe_read(root,RAW));parsedsha=digest(safe_read(root,PARSED))
 candidates=read_jsonl(root/BASE/'evidence_candidates.jsonl');abstracts=read_jsonl(root/BASE/'abstract_revision_comparison.jsonl');prior=json.loads((root/MANIFEST).read_text())
 prior_by={(x['kind'] if 'kind' in x else x.get('type'),x['uid']):x for x in prior['tasks']};tasks=[]
 for item in candidates:
  if item['raw_sha256']!=rawsha or item['parsed_sha256']!=parsedsha:raise ValueError('pilot_source_changed')
  sec=next(x for x in parsed['sections'] if x['section_id']==item['section_id']);para=next(x for x in sec['paragraphs'] if x['paragraph_id']==item['paragraph_id'])
  if para['text'][item['char_start']:item['char_end']]!=item['evidence_text']:raise ValueError('pilot_anchor_changed')
  excerpts=[{k:item[k] for k in ('page','section_id','paragraph_id','char_start','char_end','evidence_text')}];field=item['field']
  if field=='contradiction_yield':excerpts+=[anchor(parsed,'page-7','page7-p1','The rice yields of the T1',580),anchor(parsed,'page-8','page8-p1','T1 0.23',510)]
  if field=='contradiction_as':excerpts+=[anchor(parsed,'page-7','page7-p1','The Cr content of rice',410),anchor(parsed,'page-8','page8-p1','T1 0.23',510)]
  data={'field':field,'evidence':excerpts,'status_hint':'原文定位辅助，尚未由你填写人工参考答案。','pages':sorted(set(x['page'] for x in excerpts))}
  files=[source(root,RAW,'raw'),source(root,PARSED,'parsed')]
  for n in data['pages']:
   if n in PAGES:files.append(source(root,BASE+'/previews/'+PAGES[n],'preview'))
  tasks.append(task('pilot','pilot:'+field,LABELS[field],'10.3390/w16182598',data,files))
 excerpts=[anchor(parsed,'page-4','page4-p1','The total phosphorus and nitrogen',210),anchor(parsed,'page-5','page5-p3','Table 1. Water quality',1100)]
 data={'field':'conflict_total_nitrogen','evidence':excerpts,'status_hint':'正文与表格需对照第 5 页实际列位置；避免依赖表格线性提取。','pages':[4,5]}
 tasks.append(task('pilot','pilot:conflict_total_nitrogen',LABELS['conflict_total_nitrogen'],'10.3390/w16182598',data,[source(root,RAW,'raw'),source(root,PARSED,'parsed'),source(root,BASE+'/previews/page05.png','preview')]))
 for a in abstracts:
  if not a['priority']:continue
  prev=prior_by[('abstract',a['uid'])];d=prev['data'];previews=[x for x in prev['files'] if x['role']=='preview']
  files=[source(root,d['raw_file'],'raw')]+previews
  for f in previews:
   if digest(safe_read(root,f['path']))!=f['sha256']:raise ValueError('abstract_preview_changed')
  data={'uid':a['uid'],'previous_decision':a['human_decision'],'previous_note':a['human_note'],'old_text':a['old_text'],'source_candidate':a['source_candidate'],'proposed_text':a['proposed_text'],'risk_flags':a['risk_flags'],'page':a['pdf_page'],'source':a['source'],'preview_files':[f['path'] for f in previews]}
  tasks.append(task('abstract','abstract:'+a['uid'],a['title'],a['doi'],data,files))
 sources=[source(root,x,'input') for x in (BASE+'/evidence_candidates.jsonl',BASE+'/abstract_revision_comparison.jsonl',IMPORTED,MANIFEST)]
 manifest={'version':'pilot-review-web-v1','root':str(root),'created_at':utc_now(),'sources':sources,'tasks':tasks,'model_invoked':False,'note':'Source-navigation assisted; no reference or abstract decision exists until the human records it.'}
 if output.exists():raise FileExistsError('new_output_directory_required')
 output.mkdir(parents=True);mp=output/'review_manifest.json';mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 view={'manifest_sha256':digest(mp),'tasks':tasks,'root_depth':'../../../'}
 # Paths use relative file URLs: output is data/reviews/<round>.
 view['tasks']=[dict(t,files=[dict(f,url='../../../'+f['path']) for f in t['files']]) for t in tasks]
 encoded=json.dumps(view,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
 template=(Path(__file__).parent/'pilot_web.html').read_text(encoding='utf-8')
 (output/'index.html').write_text(template.replace('__PACKET_JSON__',encoded),encoding='utf-8')
 return {'output':str(output),'pilot_tasks':sum(x['kind']=='pilot' for x in tasks),'abstract_tasks':sum(x['kind']=='abstract' for x in tasks),'pending':len(tasks)}

def import_results(manifest_file,results_file,output,root=ROOT):
 root=Path(root).resolve();manifest_file=Path(manifest_file);results_file=Path(results_file);output=Path(output)
 manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
 if manifest.get('version')!='pilot-review-web-v1' or manifest.get('root')!=str(root):raise ValueError('manifest_invalid')
 for f in manifest['sources']:
  if digest(safe_read(root,f['path']))!=f['sha256']:raise ValueError('source_changed:'+f['path'])
 by={}
 for t in manifest['tasks']:
  obj={k:t[k] for k in ('kind','id','title','doi','content','files')}
  if object_hash(obj)!=t['object_sha256'] or t['id'] in by:raise ValueError('task_invalid')
  by[t['id']]=t
 result=json.loads(results_file.read_text(encoding='utf-8'))
 if result.get('version')!='pilot-review-results-v1' or result.get('manifest_sha256')!=digest(manifest_file):raise ValueError('result_manifest_mismatch')
 entries=result.get('entries')
 if not isinstance(entries,list):raise ValueError('entries_invalid')
 seen=set();accepted=[]
 for e in entries:
  if not isinstance(e,dict):raise ValueError('entry_invalid')
  t=by.get(e.get('task_id'));occ=e.get('occurrence')
  if not t or type(occ)!=int or occ<1 or (t['id'],occ) in seen:raise ValueError('unknown_or_duplicate_task')
  seen.add((t['id'],occ))
  if e.get('object_sha256')!=t['object_sha256'] or e.get('kind')!=t['kind'] or e.get('doi')!=t['doi']:raise ValueError('task_identity_mismatch')
  status=e.get('status');allowed=('reported','not_reported','not_applicable','uncertain') if t['kind']=='pilot' else ('accept','correct','uncertain')
  if status=='pending':
   if e.get('reviewer') or e.get('reviewed_at') or e.get('value') or e.get('notes'):raise ValueError('pending_contains_decision')
   continue
  if not isinstance(status,str) or status not in allowed:raise ValueError('invalid_status')
  if not isinstance(e.get('value',''),str) or not isinstance(e.get('notes',''),str):raise ValueError('invalid_text')
  if status=='reported' and not e.get('value','').strip():raise ValueError('reported_value_required')
  if status in ('not_reported','not_applicable') and e.get('value','').strip():raise ValueError('absence_status_cannot_have_value')
  if status in ('correct','not_reported','not_applicable') and not e.get('notes','').strip():raise ValueError('explanation_required')
  if not isinstance(e.get('reviewer'),str) or not e['reviewer'].strip():raise ValueError('reviewer_required')
  try:when=datetime.fromisoformat(e['reviewed_at'].replace('Z','+00:00'))
  except (KeyError,ValueError,TypeError,AttributeError):raise ValueError('invalid_timestamp') from None
  if when.tzinfo is None or when.utcoffset() is None:raise ValueError('timezone_required')
  for f in t['files']:
   if digest(safe_read(root,f['path']))!=f['sha256']:raise ValueError('evidence_changed:'+f['path'])
  accepted.append(e)
 if any((k,1) not in seen for k in by):raise ValueError('missing_base_task')
 output.mkdir(parents=True,exist_ok=False)
 path=output/'validated_human_review.jsonl';path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in accepted),encoding='utf-8')
 summary={'created_at':utc_now(),'manifest_sha256':digest(manifest_file),'returned_file_sha256':digest(results_file),'validated_sha256':digest(path),'decided':len(accepted),'pending':len(by)-sum(1 for x in accepted if x['occurrence']==1),'canonical_modified':False,'model_authorized':False,'reference_method':'human answers with automated source-location assistance'}
 (output/'import_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 b=sub.add_parser('build');b.add_argument('--output',required=True)
 i=sub.add_parser('import');i.add_argument('--manifest',required=True);i.add_argument('--results',required=True);i.add_argument('--output',required=True)
 a=p.parse_args();r=build(a.output) if a.command=='build' else import_results(a.manifest,a.results,a.output);print(json.dumps(r,ensure_ascii=False,indent=2))
