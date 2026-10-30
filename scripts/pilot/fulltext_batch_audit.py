"""Candidate-only source checks and consolidated review export. Never changes model output."""
import csv,hashlib,html,json,re,unicodedata
from collections import Counter
from pathlib import Path

def normalized(x):return ''.join(unicodedata.normalize('NFKC',x).split())
def nums(x):return re.findall(r'(?<![A-Za-z\d])[-−]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?',x)
def audit(response,paras):
 issues=[];corrections=[]
 def flag(severity,kind,path,detail):issues.append(dict(severity=severity,type=kind,path=path,detail=detail,status='pending_review'))
 targets=[('fields/'+k,v) for k,v in response['fields'].items()]+[('results/'+str(i),v) for i,v in enumerate(response['results'])]+[('conflicts/'+str(i),{'evidence':v['sides']}) for i,v in enumerate(response['conflicts'])]
 for path,target in targets:
  evidence=target.get('evidence',[]);bound=[]
  if target.get('status')=='reported' and not evidence:flag('high','missing_evidence',path,'Reported field has no source anchor.')
  for i,e in enumerate(evidence):
   exact=[p for p in paras if p['paragraph_id']==e['paragraph_id'] and p['page']==e['page']]
   if e['quote'] and any(normalized(e['quote']) in normalized(p['text']) for p in exact):bound+=exact
   else:
    matches=[p for p in paras if e['quote'] and normalized(e['quote']) in normalized(p['text'])]
    if len(matches)==1:
     p=matches[0];corrections.append(dict(path=path+'/evidence/'+str(i),original=e,proposed={'page':p['page'],'paragraph_id':p['paragraph_id']},reason='unique_whitespace_and_NFKC_normalized_quote_relocation',applied_to_original=False));bound.append(p)
     flag('medium','anchor_relocation_candidate',path,'Exact normalized quote uniquely found in a different source location; original preserved.')
    else:flag('high','unverified_quote_or_page',path,e)
   if e['table_figure']:
    if not e['row'] or not e['column']:flag('high','missing_table_row_column',path,e['table_figure'])
    # Text membership cannot establish visual row/column alignment.
    flag('medium','table_figure_alignment_requires_review',path,{'table_figure':e['table_figure'],'row':e['row'],'column':e['column'],'model_visual_claim':e['page'] in response['visual_pages_reviewed']})
  if path.startswith('results/'):
   if not evidence:flag('high','result_without_evidence',path,'')
   joined=' '.join(p['text'] for p in bound);nsource={n.lstrip('+−-') for n in nums(joined)}
   missing=[n for n in nums(target['value']) if n.lstrip('+−-') not in nsource]
   if missing:flag('high','numbers_not_in_bound_paragraphs',path,missing)
   for k in ['treatment','comparator','unit','significance']:
    value=target[k]
    if not value.strip() or re.search(r'uncertain|unspecified|not.reported|unknown',value,re.I):flag('medium','missing_or_uncertain_'+k,path,value)
   unit=target['unit'];aliases={'kg/ha':'kg/hm2','mm/d':'mm·d','cmol/kg':'cmol','mg/L':'mg','pH':'pH','%':'%'}
   if unit and normalized(unit) not in normalized(joined) and normalized(aliases.get(unit,unit)) not in normalized(joined):flag('medium','unit_not_text_verified',path,unit)
   if re.search(r'calculat|deriv|recomput',target['reporting_origin'],re.I):flag('high','calculated_result_needs_operands',path,'Free-text schema does not bind machine-readable operands; no automatic numeric correction.')
   if 'not significant' in target['significance'].lower() and re.search(r'(?<!not )\bsignificant\b',target['significance'].lower()):flag('medium','mixed_significance_language',path,target['significance'])
 for k,v in response['fields'].items():
  if v['status'] in ['uncertain','not_reported']:flag('medium','field_'+v['status'],'fields/'+k,v['value'])
 for i,c in enumerate(response['conflicts']):
  flag('high','model_reported_source_conflict','conflicts/'+str(i),c)
  if len(c['sides'])<2:flag('high','conflict_missing_both_sides','conflicts/'+str(i),c)
 if len(response['results'])<5:flag('medium','fewer_than_five_results','results',response['fewer_than_five_reason'])
 return {'issues':issues,'automatic_corrections':corrections,'candidate_only':True,'scope':'Schema, source quote/page, numeric-token and unit screening. Row/column semantics, exact significance, arithmetic operands and replication independence are not independently proven by text matching.'}

def export(root,out):
 q=json.loads((out/'queue.json').read_text());papers=[];results=[];fields=[];issues=[];usage=Counter()
 for r in q:
  d=out/'papers'/hashlib.sha256(r['doi'].encode()).hexdigest()[:16];sp=d/'status.json';s=json.loads(sp.read_text()) if sp.exists() else {'state':'pending'}
  paper={'doi':r['doi'],'title':r['title'],'state':s['state'],'candidate_status':'Candidate','results':0,'raw_file':r['raw_file'],'attempt':s.get('attempt','')}
  if s['state']=='model_completed':
   a=Path(s['attempt']);j=json.loads((a/'response.json').read_text());parsed=json.loads((root/r['parsed_file']).read_text());paras=[{'page':int(sec['section_id'].split('-')[-1]),**p} for sec in parsed['sections'] for p in sec['paragraphs']]
   checked=audit(j,paras);(a/'scientific_audit.json').write_text(json.dumps(checked,ensure_ascii=False,indent=2));paper['results']=len(j['results'])
   for k,v in j['fields'].items():fields.append({'doi':r['doi'],'field':k,**v,'candidate_status':'Candidate'})
   for i,v in enumerate(j['results']):results.append({'doi':r['doi'],'result_index':i,**v,'candidate_status':'Candidate'})
   for v in checked['issues']:issues.append({'doi':r['doi'],**v})
   for u in json.loads((a/'usage.json').read_text()):usage.update({k:v for k,v in u.get('usage',{}).items() if isinstance(v,int)})
  papers.append(paper)
 issues.sort(key=lambda x:({'high':0,'medium':1,'low':2}[x['severity']],x['doi']))
 stats={'queue_total':len(q),'states':dict(Counter(p['state'] for p in papers)),'field_status_counts':dict(Counter(f['status'] for f in fields)),'field_cells':len(fields),'quantitative_candidate_records':len(results),'issue_flags':len(issues),'papers_with_flags':len({x['doi'] for x in issues}),'flag_types':dict(Counter(x['type'] for x in issues)),'usage_on_completed_attempts':dict(usage),'requested_model':'gpt-6.1-sol','resolved_model':None,'gold':0,'accuracy':None,'note':'75-paper queue includes initial successful paper; other historical25 untouched. Counts are model candidates, not scientific validation success. Token totals exclude failed attempts.'}
 for name,rows in [('papers',papers),('results',results),('fields',fields),('issues',issues)]:
  (out/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows))
  if rows:
   keys=list(dict.fromkeys(k for x in rows for k in x))
   with (out/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
    for row in rows:
     row={k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()}
     row={k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()};w.writerow(row)
 (out/'statistics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
 body='<meta charset="utf-8"><title>全文 Candidate 集中审核</title><style>body{max-width:1100px;margin:30px auto;font:16px/1.6 sans-serif}article{border:1px solid #ccc;padding:14px;margin:10px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}.high{border-left:5px solid #c33}input{width:80%;padding:12px}</style><h1>全文 Candidate 集中审核</h1><p>按严重程度排序。模型提取完成不等于科研结果已核实；全部保持Candidate。原始回答和自动修正建议分开保存。</p><pre>'+html.escape(json.dumps(stats,ensure_ascii=False,indent=2))+'</pre><input id="q" placeholder="按DOI、问题或数值搜索" oninput="document.querySelectorAll(\'article\').forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(this.value.toLowerCase()))"><h2>论文状态及原始输出</h2>'
 for p in papers:
  body+='<article>'+html.escape(p['doi']+' | '+p['state']+' | '+p['title'])
  if p['attempt']:
   rel=Path(p['attempt']).relative_to(out);body+=' <a href="'+str(rel)+'/response.json">原始模型JSON</a> <a href="'+str(rel)+'/scientific_audit.json">自动核验</a>'
  body+='</article>'
 body+='<h2>待集中审核问题</h2>'
 for x in issues:body+='<article class="'+x['severity']+'"><b>'+html.escape(x['severity']+' '+x['doi']+' '+x['type'])+'</b><pre>'+html.escape(json.dumps(x,ensure_ascii=False,indent=2))+'</pre></article>'
 (out/'index.html').write_text(body)
 return stats
