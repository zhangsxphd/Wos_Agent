"""Build an offline, hash-bound human review packet. No decisions are made here."""
import csv, difflib, hashlib, json, re, shutil, subprocess
from pathlib import Path
from scripts.pipeline_utils import ROOT, read_jsonl
from scripts.quality.snapshot import digest, verify_snapshot
from scripts.fulltext.admission import project_file

CAN='data/processed/fulltext_verified_20261027_v5.jsonl'
INV='data/fulltext/runs/review_reprocessed_20261028/fulltexts.jsonl'
QUALITY='data/reports/review_workbench_20261028/quality'
SPRINGER='data/reports/review_workbench_20261028/springer/springer_abstract_candidates.jsonl'
TRIAGE='data/reports/review_workbench_20261028/triage/quality_records_v2.jsonl'
YEARS='data/reports/literature_quality_20261028/year_review/year_review.jsonl'
ANOMALIES='data/reports/review_workbench_20261028/pdf_anomalies/pdf_anomalies.jsonl'
PROBE='data/reports/literature_quality_20261028/pdf_layout_probe_v2.jsonl'
PRIORITIES=['10.1007/s00344-022-10786-z','10.1007/s10705-026-10500-5','10.1007/s11104-023-06371-2','10.1007/s11356-022-23511-w','10.1007/s11104-025-08213-9','10.1007/s11033-023-08998-x','10.1007/s42729-025-02927-6']
PDFS=['10.3389/fmicb.2023.1210938','10.3389/fmicb.2023.1165631','10.3389/fpls.2022.989504','10.32604/phyton.2022.018396']
CHOICES={'abstract':['accept','correct','reject','uncertain'],'fulltext':['identity_confirmed','identity_conflict','needs_reprocessing','uncertain'],'screening':['include_core','include_methods','include_background','exclude','uncertain'],'year_policy':['first_online','issue_year','defer']}

def object_hash(obj):
 return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def csv_out(path, rows, fields):
 with path.open('x',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
  for row in rows:
   item={}
   for k,v in row.items():
    if isinstance(v,(dict,list)):v=json.dumps(v,ensure_ascii=False)
    if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')):v="'"+v
    item[k]=v
   writer.writerow(item)

def page_differences(first, layout):
 # Comparison aid only: differences can include title, DOI, metadata or body.
 tokenize=lambda value:re.findall(r'\w+|[^\w\s]',value.lower())[:1800]
 a=tokenize(first);b=tokenize(layout);result=[]
 for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
  if tag!='equal':
   result.append({'pypdf_token_range':[i,j],'pdfplumber_token_range':[k,l],
                  'pypdf':' '.join(a[i:min(j,i+35)]),'pdfplumber':' '.join(b[k:min(l,k+35)])})
   if len(result)==5:break
 return result


def task(kind,uid,doi,title,data,files):
 obj={'type':kind,'uid':uid,'doi':doi,'title':title,'data':data,'files':files}
 return {**obj,'object_sha256':object_hash(obj),'status':'pending','decision':'','reviewer':'','reviewed_at':'','notes':''}

def build(output,root=ROOT,preview_dir=Path('/private/tmp/wos-review-preview-round1')):
 root=Path(root).resolve();output=Path(output);rows=read_jsonl(root/QUALITY/'quality_rows.jsonl')
 verify_snapshot(json.loads((root/QUALITY/'quality_summary.json').read_text()),root/QUALITY/'quality_rows.jsonl',root/CAN,root/INV,rows,root)
 records={x['uid']:x for x in read_jsonl(root/CAN)};quality={x['uid']:x for x in rows}
 manifests={x['uid']:x for x in read_jsonl(root/INV)};springer=read_jsonl(root/SPRINGER);triage=read_jsonl(root/TRIAGE);years=read_jsonl(root/YEARS)
 anomalies={x['doi']:x for x in read_jsonl(root/ANOMALIES)};probes={x['uid']:x for x in read_jsonl(root/PROBE)}
 output.mkdir(parents=True,exist_ok=False);(output/'previews').mkdir()
 def files_of(uid):
  q=quality[uid];out=[]
  for field in ('raw_file','parsed_file'):
   name=q.get(field);h=(q.get('observed_file_sha256') or {}).get(field)
   if name and h and h!='unreadable_or_unsafe':out.append({'path':name,'sha256':h,'role':field})
  return out
 def copy_preview(src,label):
  if not src.exists():return None
  dest=output/'previews'/label;shutil.copyfile(src,dest)
  return {'path':str(dest.resolve().relative_to(root)),'sha256':digest(dest),'role':'preview'}
 tasks=[]
 for s in sorted(springer,key=lambda x:(PRIORITIES.index(x['doi']) if x['doi'] in PRIORITIES else 999,x['doi'])):
  f=files_of(s['uid']);previews=[]
  if s['doi'] in PRIORITIES:
   for p in (1,2):
    name=s['uid'].replace(':','_')+f'-{p:02}.png';v=copy_preview(preview_dir/name,name)
    if not v:
     target=output/'previews'/name
     subprocess.run(['pdftoppm','-f',str(p),'-l',str(p),'-scale-to','1300','-png','-singlefile',str(project_file(root,s['raw_file'])),str(target.with_suffix(''))],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
     v={'path':str(target.resolve().relative_to(root)),'sha256':digest(target),'role':'preview'}
    f.append(v);previews.append('previews/'+name)
  if not previews:
   name=s['uid'].replace(':','_')+'-01.png';target=output/'previews'/name
   subprocess.run(['pdftoppm','-f','1','-l','1','-scale-to','1300','-png','-singlefile',str(project_file(root,s['raw_file'])),str(target.with_suffix(''))],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   f.append({'path':str(target.resolve().relative_to(root)),'sha256':digest(target),'role':'preview'});previews.append('previews/'+name)
  parsed=json.loads(project_file(root,s['parsed_file']).read_text());pages=[]
  for sec in parsed.get('sections',[]):
   if sec.get('section_id') in ('page-1','page-2'):
    pages += [p.get('text','') for p in sec.get('paragraphs',[])]
  layout_pages=[p.get('text','') for p in probes[s['uid']]['pages']]
  s=dict(s,priority=s['doi'] in PRIORITIES,preview_images=previews,pdf_extracted_pages='\n'.join(pages)[:25000],pdfplumber_extracted_pages='\n'.join(layout_pages)[:25000],first_page_differences=page_differences(pages[0] if pages else '',layout_pages[0] if layout_pages else ''))
  tasks.append(task('abstract',s['uid'],s['doi'],s['title'],s,f))
 for doi in PDFS:
  a=anomalies[doi];q=quality[a['uid']];f=files_of(a['uid']);name=a['uid'].replace(':','_')+'-identity.png'
  v=copy_preview(root/a['first_page_preview'],name);previews=[]
  if v:f.append(v);previews=['previews/'+name]
  data=dict(a,preview_images=previews,current_technical_status=q['evidence_admission'],current_issues=q['issues'],
            repair_note=('已重建 pdfplumber 解析并通过技术核验；阅读顺序仍需人工检查。' if doi!=PDFS[-1] else '严格 pypdf 解析失败，未放宽准入；pdfplumber 未读出页面。'))
  tasks.append(task('fulltext',a['uid'],doi,a['title'],data,f))
 years=[dict(y,crossref_source='https://api.crossref.org/works/'+y['doi']) for y in years]
 tasks.append(task('year_policy','__search_scope__','','统一年份规则',{'papers':years,'note':'按同一规则处理；在线日期未知的论文单独保持待定。'},[]))
 for row in triage:
  rec=records[row['uid']];data=dict(row,abstract=rec.get('abstract') or '',note='关键词只是定位线索，不是已确认研究事实，也不自动纳入或排除。')
  tasks.append(task('screening',row['uid'],row.get('doi') or '',row['title'],data,files_of(row['uid'])))
 sources=[CAN,INV,QUALITY+'/quality_rows.jsonl',QUALITY+'/quality_summary.json',SPRINGER,TRIAGE,YEARS,ANOMALIES,PROBE]
 manifest={'version':'review-packet-v1','root':str(root),'sources':[{'path':x,'sha256':digest(root/x)} for x in sources],
           'choices':CHOICES,'tasks':tasks,'limitations':['All decisions are pending until recorded by a human.','Review does not grant technical admission.','Licensed text stays local.']}
 (output/'review_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 fields=['type','uid','doi','title','object_sha256','status','decision','reviewer','reviewed_at','notes']
 csv_out(output/'review_decisions.template.csv',tasks,fields)
 (output/'review_decisions.pending.jsonl').write_text(''.join(json.dumps({k:t[k] for k in fields},ensure_ascii=False)+'\n' for t in tasks),encoding='utf-8')
 for kind,name in [('abstract','abstract_review.csv'),('fulltext','fulltext_review.csv'),('screening','screening_review.csv')]:
  subset=[dict(t,**{'details':t['data']}) for t in tasks if t['type']==kind];csv_out(output/name,subset,fields+['details'])
 csv_out(output/'year_review.csv',years,list(years[0].keys()))
 csv_out(output/'review_overview.csv',[{'type':kind,'total':sum(t['type']==kind for t in tasks),'pending':sum(t['type']==kind for t in tasks)} for kind in CHOICES],['type','total','pending'])
 template=(Path(__file__).parent/'review.html').read_text(encoding='utf-8')
 payload=json.dumps({'tasks':tasks,'choices':CHOICES,'manifest_sha256':digest(output/'review_manifest.json')},ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
 (output/'index.html').write_text(template.replace('__PACKET_JSON__',payload),encoding='utf-8')
 return {'tasks':len(tasks),'abstracts':len(springer),'priority':len(PRIORITIES),'fulltext':len(PDFS),'screening':len(triage)}

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.output),ensure_ascii=False))
