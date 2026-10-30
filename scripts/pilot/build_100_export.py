"""Combine existing candidate generations without modifying historic review or Gold."""
import argparse,csv,hashlib,html,json
from pathlib import Path
from scripts.pipeline_utils import ROOT,utc_now

BASE=ROOT/'data/pilot/v08_codex_fulltext_20261030_v2'
PREP=ROOT/'data/pilot/v08_100paper_preparation_20261029_v2/admitted_100.jsonl'
FIVE=ROOT/'data/pilot/v08_100_fulltext_20261030_v1'
OLD=ROOT/'data/pilot/v08_twenty_paper_overview_20261029_v3/papers.json'

def lines(p):return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def jsonl(p,rows):p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
def csvfile(p,rows,headers):
 with p.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore');w.writeheader()
  for r in rows:
   x={k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else ('' if v is None else v) for k,v in r.items()}
   x={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in x.items()}
   w.writerow(x)

def build(output,allow_incomplete=False):
 output=Path(output).resolve()
 if output.exists():raise FileExistsError('new_output_directory_required')
 admitted=lines(PREP);q=json.loads((BASE/'queue.json').read_text());old=json.loads(OLD.read_text())['records'];five=lines(FIVE/'five_fields.jsonl');five_results=lines(FIVE/'result_records.jsonl')
 all_ids=[r['doi'] for r in admitted]; q_ids=[r['doi'] for r in q];old_by={x['doi']:x for x in old};five_ids={x['doi'] for x in five}
 if len(all_ids)!=100 or len(set(all_ids))!=100 or len(q_ids)!=75 or len(set(q_ids))!=75 or len(old_by)!=20 or len(five_ids)!=5:raise ValueError('population_size_or_identity_mismatch')
 if set(q_ids)&(set(old_by)|five_ids) or set(old_by)&five_ids or set(all_ids)!=(set(q_ids)|set(old_by)|five_ids):raise ValueError('population_partition_mismatch')
 by={x['doi']:x for x in admitted};status={};results=[];fields=[];issues=[]
 for doi in q_ids:
  r=by[doi];d=BASE/'papers'/hashlib.sha256(doi.encode()).hexdigest()[:16];sp=d/'status.json';s=json.loads(sp.read_text()) if sp.exists() else {'state':'pending'};status[doi]=s
  if s['state']!='model_completed':continue
  a=Path(s['attempt']);m=json.loads((a/'manifest.json').read_text());raw=ROOT/r['raw_file'];parsed=ROOT/r['parsed_file']
  if doi!=m['doi'] or sha(raw)!=m['raw_sha256'] or sha(parsed)!=m['parsed_sha256']:raise ValueError('source_identity_mismatch:'+doi)
  x=json.loads((a/'response.json').read_text());
  if x['doi']!=doi:raise ValueError('response_identity_mismatch:'+doi)
  model='Codex CLI ChatGPT; requested gpt-6.1-sol; resolved model not independently recorded'
  for k,v in x['fields'].items():fields.append({'doi':doi,'field':k,'value':v['value'],'status':v['status'],'record_class':'candidate_pending_review','model_source':model,'evidence':v['evidence'],'source_sha256':m['raw_sha256']})
  for i,v in enumerate(x['results']):results.append({'doi':doi,'index':i,**v,'record_class':'candidate_pending_review','model_source':model,'source_sha256':m['raw_sha256']})
  check=a/'scientific_audit.json'
  if check.exists():
   for item in json.loads(check.read_text())['issues']:issues.append({'doi':doi,'source':'v1_screen',**item})
  v2=ROOT/'data/pilot/v08_candidate_validation_20261030_v1'/f'{hashlib.sha256(doi.encode()).hexdigest()[:16]}.json'
  if v2.exists():
   v=json.loads(v2.read_text())
   if v['candidate_sha256']!=sha(a/'response.json'):raise ValueError('validation_binding_mismatch:'+doi)
   for checked in v['checks']:
    for item in checked['issues']:issues.append({'doi':doi,'source':'v2_screen','path':'results/'+str(checked['index']),**item})
 if not allow_incomplete and (any(s['state']!='model_completed' for s in status.values()) or len(status)!=75):raise RuntimeError('batch_not_fully_completed')
 for r in old:
  for x in r['records']:fields.append({'doi':r['doi'],'field':x['field'],'value':x['value'],'status':x.get('review_status','historic_candidate'),'record_class':'historic_record_as_previously_labeled','model_source':str(r.get('method','unknown'))+'; '+str(r.get('model','unknown')),'evidence':{'source_file':r.get('source_file'),'record_metadata':x},'source_sha256':r.get('pdf_sha256')})
 for x in five:fields.append({'doi':x['doi'],'field':x['field'],'value':x['value'],'status':x['status'],'record_class':'candidate_pending_review','model_source':x['model_source'],'evidence':x['evidence'],'source_sha256':x['raw_sha256']})
 for i,x in enumerate(five_results):results.append({'doi':x['doi'],'index':i,'indicator':x['metric'],'treatment':'see comparison','comparator':x['comparison'],'time':'see evidence','value':x['values'],'unit':x['unit'],'significance':x['significance'],'significance_letters':'see significance','reporting_origin':x['source_kind'],'limitations':x['limitations'],'evidence':x['evidence'],'record_class':x['status'],'model_source':x['model_source'],'source_sha256':x['evidence']['raw_sha256']})
 papers=[]
 for r in admitted:
  doi=r['doi']
  if doi in old_by:state='historic_candidate_retained';model=str(old_by[doi].get('model','unknown'));source=old_by[doi].get('source_file','')
  elif doi in five_ids:state='previous_five_candidate_retained';model='Codex conversation; exact runtime model not independently recorded';source='data/pilot/v08_100_fulltext_20261030_v1/five_fields.jsonl'
  else:state=status[doi]['state'];model='Codex CLI requested gpt-6.1-sol; resolved model unknown';source=status[doi].get('attempt','')
  papers.append({'uid':r['uid'],'doi':doi,'title':r['title'],'extraction_status':state,'model_source':model,'source_file':source,'raw_sha256':r['technical_admission']['raw_sha256'],'raw_file':r['raw_file'],'human_relevance':r['human_relevance'],'holdout_overlap':r['historical_holdout_overlap']})
 output.mkdir(parents=True)
 for name,rows in [('papers',papers),('fields',fields),('results',results),('issues',issues)]:
  jsonl(output/(name+'.jsonl'),rows)
  headers=list(dict.fromkeys(k for row in rows for k in row))
  if headers:csvfile(output/(name+'.csv'),rows,headers)
 stats={'created_at':utc_now(),'papers_total':len(papers),'historical20':len(old_by),'prior_five':len(five_ids),'new75_completed':sum(s['state']=='model_completed' for s in status.values()),'new75_unfinished':sum(s['state']!='model_completed' for s in status.values()),'fields':len(fields),'results':len(results),'automatic_flags':len(issues),'candidate_not_gold':True,'accuracy_calculated':False,'history_preserved':True,'scope':'Counts include only distinct existing records; historical records retain their prior wording and review status. Automatic flags are not confirmed errors.'}
 (output/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
 def url(path):return Path(path).relative_to(ROOT) if Path(path).is_absolute() else Path(path)
 nav=[('历史20篇与既有审核',OLD.parent/'index.html'),('已有5篇字段与结果',FIVE/'index.html'),('本批模型候选与自动核验',BASE/'index.html')]
 links=''.join('<li><a href="'+html.escape(str(Path(__import__('os').path.relpath(p,output))))+'">'+html.escape(label)+'</a></li>' for label,p in nav if p.exists())
 document='<meta charset="utf-8"><title>100篇科研信息总览</title><style>body{max-width:1000px;margin:30px auto;font:16px/1.6 sans-serif}pre{white-space:pre-wrap}td,th{border:1px solid #ccc;padding:6px;text-align:left}</style><h1>100篇科研信息总览</h1><p>候选、历史人工决定与模型来源分别保留。自动核验标记不能当作准确率或Gold。</p><pre>'+html.escape(json.dumps(stats,ensure_ascii=False,indent=2))+'</pre><h2>查看原始版本</h2><ul>'+links+'</ul><h2>结构化数据</h2><ul>' + ''.join('<li><a href="'+n+'.csv">'+n+'.csv</a> / <a href="'+n+'.jsonl">JSONL</a></li>' for n in ('papers','fields','results','issues') if (output/(n+'.csv')).exists())+'</ul>'
 (output/'index.html').write_text(document)
 return stats
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--allow-incomplete',action='store_true');a=p.parse_args();print(json.dumps(build(a.output,a.allow_incomplete),ensure_ascii=False))
