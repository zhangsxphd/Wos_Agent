"""Combine existing candidate generations without modifying historic review or Gold."""
import argparse,csv,hashlib,html,json,os
from pathlib import Path
from scripts.pipeline_utils import ROOT,utc_now

BASE=ROOT/'data/pilot/v08_codex_fulltext_20261030_v2'
PREP=ROOT/'data/pilot/v08_100paper_preparation_20261029_v2/admitted_100.jsonl'
FIVE=ROOT/'data/pilot/v08_100_fulltext_20261030_v1'
FIVE_INPUT=ROOT/'data/pilot/v08_100paper_candidate_batch1_20261029_v1/candidates.jsonl'
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

def build(output,allow_incomplete=False,review=None):
 output=Path(output).resolve()
 if output.exists():raise FileExistsError('new_output_directory_required')
 admitted=lines(PREP);q=json.loads((BASE/'queue.json').read_text());old=json.loads(OLD.read_text())['records'];five=lines(FIVE/'five_fields.jsonl');five_results=lines(FIVE/'result_records.jsonl');five_info={x['doi']:x for x in lines(FIVE_INPUT)}
 all_ids=[r['doi'] for r in admitted]; q_ids=[r['doi'] for r in q];old_by={x['doi']:x for x in old};five_ids={x['doi'] for x in five}
 if len(all_ids)!=100 or len(set(all_ids))!=100 or len(q_ids)!=75 or len(set(q_ids))!=75 or len(old_by)!=20 or len(five_ids)!=5:raise ValueError('population_size_or_identity_mismatch')
 if set(q_ids)&(set(old_by)|five_ids) or set(old_by)&five_ids or set(all_ids)!=(set(q_ids)|set(old_by)|five_ids):raise ValueError('population_partition_mismatch')
 by={x['doi']:x for x in admitted};status={};results=[];fields=[];issues=[];supplements=[];supplement_files=[];historic_claims=[];pmc_latest={}
 requests_file=ROOT/'data/pilot/v08_supplement_requests_20261030_v1/missing_supplements.jsonl'
 supplement_requests=lines(requests_file) if requests_file.exists() else []
 for rec in supplement_requests:
  if rec['doi'] not in by or sha(ROOT/rec['source_pdf'])!=rec['source_sha256']:raise ValueError('supplement_request_source_mismatch:'+rec['doi'])
  issues.append({'doi':rec['doi'],'source':'supplement_availability','severity':'medium','type':'supplement_not_obtained','path':'supplement','detail':rec['model_note'],'status':'pending_review'})
 pmc_dir=ROOT/'data/pilot/v08_pmc_supplement_batch_20261030_v1'
 for inventory in sorted(pmc_dir.glob('inventory_*.jsonl')):
  for rec in lines(inventory):
   doi=rec['doi']
   # Never replace a successful inventory with a later transient error.
   if doi not in pmc_latest or rec['status']=='checked' or pmc_latest[doi]['status']!='checked':pmc_latest[doi]=rec
 for doi,rec in pmc_latest.items():
  for f in rec.get('files',[]):
   if f.get('status') in ('downloaded','reused_previously_downloaded'):
    if sha(ROOT/f['local_file'])!=f['sha256']:raise ValueError('pmc_supplement_changed:'+doi)
    supplement_files.append({'doi':doi,'pmcid':rec.get('pmcid'),'license':rec.get('license'),'source':rec.get('source'),**f})
 def add_historical_supp(doi,path,expected,source,identity):
  file=ROOT/path
  if not file.is_file():
   supplement_files.append({'doi':doi,'local_file':str(path),'sha256':expected,'status':'historical_file_missing','source':source,'identity':identity});return
  if sha(file)!=expected:raise ValueError('historic_supplement_changed:'+doi)
  if not any(x['doi']==doi and x['sha256']==expected for x in supplement_files):
   supplement_files.append({'doi':doi,'filename':file.name,'local_file':str(path),'sha256':expected,'status':'historical_file_retained','source':source,'identity':identity,'bytes':file.stat().st_size})
 old_dir=ROOT/'data/pilot/v08_20paper_20261029_review_v2'
 for rec in json.loads((old_dir/'supplement_inventory.json').read_text()):
  add_historical_supp(rec['doi'],str((old_dir/'supplements'/rec['file']).relative_to(ROOT)),rec['sha256'],rec.get('acquisition','historical'),rec.get('publisher_article_page',''))
 bdir=ROOT/'data/pilot/v08_20paper_20261029_batch2_v1'
 for rec in json.loads((bdir/'supplement_manifest.json').read_text())['items']:
  for key,hkey in [('pdf','pdf_sha256'),('zip','zip_sha256'),('file','sha256')]:
   if rec.get(key) and rec.get(hkey):add_historical_supp(rec['doi'],str((bdir/'supplements'/rec[key]).relative_to(ROOT)),rec[hkey],rec.get('received_from','historical'),rec.get('identity_evidence',''))
 m=ROOT/'data/pilot/v08_20paper_20261029_remaining_v2/manifest.json'
 for rec in json.loads(m.read_text()).get('user_supplied_supplements',[]):
  add_historical_supp(rec['doi'],rec['path'],rec['sha256'],'user supplied',rec.get('identity',''))
 for rec in five_info.values():
  if rec.get('supplement') and rec.get('supplement_sha256'):
   file=FIVE_INPUT.parent/rec['supplement'];add_historical_supp(rec['doi'],str(file.relative_to(ROOT)),rec['supplement_sha256'],'user supplied',rec.get('supplement_status',''))
 for supp_file in sorted((ROOT/'data/pilot/v08_supplements_20261030_v1').glob('supplement_observations_*.jsonl')):
  for rec in lines(supp_file):
   if rec['doi'] not in by:raise ValueError('supplement_doi_not_in_population')
   for ev in rec['evidence']:
    if sha(ROOT/ev['file'])!=ev['sha256']:raise ValueError('supplement_source_changed:'+rec['doi'])
    if ev.get('derived_from') and sha(ROOT/ev['derived_from']['file'])!=ev['derived_from']['sha256']:raise ValueError('supplement_original_changed:'+rec['doi'])
   supplements.append(rec)
   if rec['record_kind']=='source_conflict_candidate':
    issues.append({'doi':rec['doi'],'source':'supplement_visual_candidate','severity':'high','type':'possible_source_conflict','path':'supplement/'+rec['topic'],'detail':rec['observation'],'status':'pending_review'})
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
  # Preserve the detailed structured historic claims as a distinct dataset;
  # the older three web answers remain linked in their original form.
  f=ROOT/r['source_file']
  if not f.is_file():raise FileNotFoundError('historic_candidate_missing:'+r['doi'])
  candidates=lines(f) if f.suffix=='.jsonl' else [json.loads(f.read_text())]
  matching=[x for x in candidates if isinstance(x,dict) and x.get('doi')==r['doi'] and ('facts' in x or 'claims' in x)]
  if len(matching)>1:raise ValueError('ambiguous_historic_candidate:'+r['doi'])
  if matching:
   x=matching[0];expected=x.get('raw_sha256',x.get('source_sha256'))
   if expected!=r['pdf_sha256'] or sha(ROOT/r['pdf'])!=expected:raise ValueError('historic_candidate_source_mismatch:'+r['doi'])
   for claim in x.get('facts',x.get('claims',[])):
    historic_claims.append({'doi':r['doi'],'field':claim.get('field'),'value':claim.get('value'),'status':claim.get('status'),'evidence':claim.get('evidence'),'model_source':x.get('model','unknown'),'source_file':r['source_file'],'source_file_sha256':sha(f),'raw_pdf_sha256':expected,'record_class':'historic_claim_original_status','original_record':claim})
 for x in five:fields.append({'doi':x['doi'],'field':x['field'],'value':x['value'],'status':x['status'],'record_class':'candidate_pending_review','model_source':x['model_source'],'evidence':x['evidence'],'source_sha256':x['raw_sha256']})
 for i,x in enumerate(five_results):results.append({'doi':x['doi'],'index':i,'indicator':x['metric'],'treatment':'see comparison','comparator':x['comparison'],'time':'see evidence','value':x['values'],'unit':x['unit'],'significance':x['significance'],'significance_letters':'see significance','reporting_origin':x['source_kind'],'limitations':x['limitations'],'evidence':x['evidence'],'record_class':x['status'],'model_source':x['model_source'],'source_sha256':x['evidence']['raw_sha256']})
 papers=[]
 for r in admitted:
  doi=r['doi']
  if doi in old_by:state='historic_candidate_retained';model=str(old_by[doi].get('model','unknown'));source=old_by[doi].get('source_file','')
  elif doi in five_ids:state='previous_five_candidate_retained';model='Codex conversation; exact runtime model not independently recorded';source='data/pilot/v08_100_fulltext_20261030_v1/five_fields.jsonl'
  else:state=status[doi]['state'];model='Codex CLI requested gpt-6.1-sol; resolved model unknown';source=status[doi].get('attempt','')
  papers.append({'supplement_status':(old_by[doi].get('supplement_status','not_fully_assessed') if doi in old_by else five_info[doi].get('supplement_status','not_fully_assessed') if doi in five_info else 'available_partially_checked' if any(x['doi']==doi for x in supplements) else 'available_not_yet_scientifically_reviewed' if any(x['doi']==doi and x['status']!='historical_file_missing' for x in supplement_files) else 'referenced_not_obtained' if any(x['doi']==doi for x in supplement_requests) else 'not_fully_assessed'),'uid':r['uid'],'doi':doi,'title':r['title'],'extraction_status':state,'model_source':model,'source_file':source,'historic_response_file':old_by[doi].get('response_file','') if doi in old_by else '', 'raw_sha256':r['technical_admission']['raw_sha256'],'raw_file':r['raw_file'],'human_relevance':r['human_relevance'],'holdout_overlap':r['historical_holdout_overlap']})
 output.mkdir(parents=True)
 for name,rows in [('papers',papers),('fields',fields),('results',results),('issues',issues),('supplements',supplements),('supplement_files',supplement_files),('historic_claims',historic_claims),('supplement_requests',supplement_requests)]:
  jsonl(output/(name+'.jsonl'),rows)
  headers=list(dict.fromkeys(k for row in rows for k in row))
  if headers:csvfile(output/(name+'.csv'),rows,headers)
 stats={'created_at':utc_now(),'papers_total':len(papers),'historical20':len(old_by),'prior_five':len(five_ids),'new75_completed':sum(s['state']=='model_completed' for s in status.values()),'new75_unfinished':sum(s['state']!='model_completed' for s in status.values()),'fields':len(fields),'results':len(results),'historic_source_claims':len(historic_claims),'supplement_observation_candidates':len(supplements),'supplement_files_available':sum(x['status']!='historical_file_missing' for x in supplement_files),'historical_supplement_files_missing':sum(x['status']=='historical_file_missing' for x in supplement_files),'papers_with_supplement_files':len({x['doi'] for x in supplement_files if x['status']!='historical_file_missing'}),'supplement_requests_screened':len(supplement_requests),'automatic_flags':len(issues),'candidate_not_gold':True,'accuracy_calculated':False,'history_preserved':True,'scope':'Results count comprises the new 75 and prior five records; older 20 detailed claims and original web responses are preserved separately. Supplement requests are screened, not necessarily exhaustive. Automatic flags are not confirmed errors.'}
 (output/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
 def url(path):return Path(path).relative_to(ROOT) if Path(path).is_absolute() else Path(path)
 nav=[('历史20篇与既有审核',OLD.parent/'index.html'),('已有5篇字段与结果',FIVE/'index.html'),('本批模型候选与自动核验',BASE/'index.html'),('补充材料候选与原文冲突',ROOT/'data/pilot/v08_supplements_20261030_v1/supplement_observations.html')]
 nav.insert(0,('待下载补充材料清单',requests_file.parent/'index.html'))
 if review:
  review=Path(review).resolve();review.relative_to(ROOT)
  if not review.is_file():raise FileNotFoundError('review_page_missing')
  nav.insert(0,('集中审核与问题分类',review))
 links=''.join('<li><a href="'+html.escape(str(Path(os.path.relpath(p,output))))+'">'+html.escape(label)+'</a></li>' for label,p in nav if p.exists())
 packet={'papers':papers,'fields':fields,'results':results,'supplements':supplements,'historic_claims':historic_claims,'issue_counts':{doi:sum(x['doi']==doi for x in issues) for doi in all_ids},'raw_prefix':os.path.relpath(ROOT,output)+'/'}
 encoded=json.dumps(packet,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
 document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>100 篇科研信息总览</title>
 <style>body{max-width:1400px;margin:30px auto;padding:0 20px;font:15px/1.55 system-ui,sans-serif;color:#20313a}h1,h2{color:#174c64}a{color:#075985}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f7f8;padding:12px;border-radius:6px}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #d8e1e4;padding:8px;vertical-align:top;text-align:left}th{background:#e8f1f3}input,select{font:inherit;padding:8px;border:1px solid #a5b8bd;border-radius:4px}input{width:min(38rem,85%)}.hint{background:#eff7f5;padding:12px}.scroll{overflow-x:auto}details{min-width:12em}summary{cursor:pointer;color:#075985}small{color:#53676f}</style>
 <h1>100 篇科研信息总览</h1><p class="hint">结果为候选，自动标记是核查线索。历史人工决定独立保留；新取得的附件未自动进入此前模型输入。</p>
 <pre>'''+html.escape(json.dumps(stats,ensure_ascii=False,indent=2))+'''</pre><h2>集中审核与已有版本</h2><ul>'''+links+'''</ul><h2>查找论文、字段或结果</h2>
 <label>关键词或 DOI <input id="query" type="search" placeholder="输入 DOI、处理组、指标等"></label> <label>提取来源 <select id="scope"><option value="all">全部 100 篇</option><option value="model_completed">本批模型候选</option><option value="historic_candidate_retained">历史 20 篇</option><option value="previous_five_candidate_retained">已有 5 篇</option></select></label> <span id="count" aria-live="polite"></span>
 <div class="scroll"><table><thead><tr><th>DOI / 原文</th><th>论文</th><th>状态 / 补充材料</th><th>本批自动标记</th><th>字段、结果和证据</th></tr></thead><tbody id="rows"></tbody></table></div>
 <h2>结构化数据</h2><p><a href="wos_100_candidates.xlsx">可筛选 Excel（在表格生成后可用）</a></p><ul>'''+''.join('<li><a href="'+n+'.csv">'+n+'.csv</a> / <a href="'+n+'.jsonl">JSONL</a></li>' for n in ('papers','fields','results','issues','supplements','supplement_files','historic_claims','supplement_requests') if (output/(n+'.csv')).exists())+'''</ul>
 <script type="application/json" id="packet">'''+encoded+'''</script><script>
 const data=JSON.parse(document.getElementById('packet').textContent), q=document.getElementById('query'), scope=document.getElementById('scope'), tbody=document.getElementById('rows'), count=document.getElementById('count');
 const buckets={}; for(const p of data.papers)buckets[p.doi]={paper:p,fields:[],results:[],supplements:[],historic_claims:[]};
 for(const type of ['fields','results','supplements','historic_claims'])for(const item of data[type])if(buckets[item.doi])buckets[item.doi][type].push(item);
 const items=Object.values(buckets).map(x=>({...x,search:JSON.stringify(x).toLowerCase()}));
 function cell(row,text){const c=document.createElement('td');c.textContent=text;row.append(c);return c}
 function render(){const term=q.value.trim().toLowerCase(),visible=items.filter(x=>(scope.value==='all'||x.paper.extraction_status===scope.value)&&(!term||x.search.includes(term)));tbody.replaceChildren();count.textContent=`显示 ${visible.length} / ${items.length} 篇`;
 for(const x of visible){const row=document.createElement('tr'),p=x.paper,c=cell(row,p.doi+' '),a=document.createElement('a');a.href=data.raw_prefix+p.raw_file;a.target='_blank';a.rel='noopener';a.textContent='原 PDF';c.append(a);cell(row,p.title);cell(row,p.extraction_status+' / '+p.supplement_status);cell(row,p.extraction_status==='model_completed'?String(data.issue_counts[p.doi]||0):'见历史审核');const d=document.createElement('details'),s=document.createElement('summary'),resultLabel=p.extraction_status==='historic_candidate_retained'?'历史结果见详细记录或原回答':`定量结果 ${x.results.length}`;s.textContent=`字段 ${x.fields.length} · ${resultLabel} · 历史详细记录 ${x.historic_claims.length} · 补充观察 ${x.supplements.length}`;d.append(s);if(p.historic_response_file){const h=document.createElement('a');h.href=data.raw_prefix+p.historic_response_file;h.target='_blank';h.rel='noopener';h.textContent='打开历史完整回答';d.append(h)}d.addEventListener('toggle',()=>{if(d.open&&!d.querySelector('pre')){const pre=document.createElement('pre');pre.textContent=JSON.stringify({fields:x.fields,results:x.results,historic_claims:x.historic_claims,supplements:x.supplements},null,2);d.append(pre)}});const last=document.createElement('td');last.append(d);row.append(last);tbody.append(row)}}
 q.addEventListener('input',render);scope.addEventListener('change',render);render();</script></html>'''
 (output/'index.html').write_text(document)
 return stats
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--allow-incomplete',action='store_true');p.add_argument('--review');a=p.parse_args();print(json.dumps(build(a.output,a.allow_incomplete,a.review),ensure_ascii=False))
