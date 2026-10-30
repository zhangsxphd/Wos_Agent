"""Create an immutable snapshot using the existing pilot review workbench."""
import argparse, hashlib, json
from collections import defaultdict
from pathlib import Path
from scripts.pipeline_utils import ROOT, utc_now
from scripts.quality.snapshot import digest
from scripts.review.pilot_web import source, task, import_results

BASE = ROOT / 'data/pilot/v08_codex_fulltext_20261030_v2'

def build(output, root=ROOT):
 root=Path(root).resolve();output=Path(output).resolve()
 if len(output.relative_to(root).parts)!=3 or output.relative_to(root).parts[:2]!=('data','reviews'):raise ValueError('output_must_be_data_reviews_round')
 if output.exists():raise FileExistsError('new_output_directory_required')
 queue=json.loads((BASE/'queue.json').read_text()); bydoi={r['doi']:r for r in queue}
 grouped=defaultdict(list); extra={}
 issue_file=BASE/'issues.jsonl'
 for line in issue_file.read_text().splitlines():
  i=json.loads(line);grouped[(i['doi'],i['path'])].append(i)
 validation=ROOT/'data/pilot/v08_candidate_validation_20261030_v1'
 for f in validation.glob('*.json'):
  if f.name=='summary.json':continue
  data=json.loads(f.read_text());doi=data['doi'];folder=BASE/'papers'/hashlib.sha256(doi.encode()).hexdigest()[:16]
  sp=folder/'status.json'
  if not sp.exists():continue
  status=json.loads(sp.read_text());response=Path(status['attempt'])/'response.json'
  if not response.exists() or digest(response)!=data['candidate_sha256']:continue
  for c in data['checks']:
   path='results/'+str(c['index']);extra[(doi,path)]=(c,f)
   for issue in c['issues']:grouped[(doi,path)].append({'doi':doi,'path':path,'status':'pending_review',**issue})
 tasks=[];source_files={}
 ordered=sorted(grouped.items(),key=lambda x:(0 if any(i['severity']=='high' for i in x[1]) else 1,x[0]))
 for (doi,path),issues in ordered:
  r=bydoi[doi]; folder=BASE/'papers'/hashlib.sha256(doi.encode()).hexdigest()[:16]
  status=json.loads((folder/'status.json').read_text());attempt=Path(status['attempt'])
  result=json.loads((attempt/'response.json').read_text());parts=path.split('/')
  try:
   val=result[parts[0]][int(parts[1]) if parts[0] in ('results','conflicts') else parts[1]]
  except (KeyError,IndexError,ValueError):continue
  evidence=val.get('evidence',val.get('sides',[])) if isinstance(val,dict) else []
  excerpts=[{'page':e['page'],'section_id':'page-'+str(e['page']),'paragraph_id':e['paragraph_id'],'evidence_text':e['quote'] or '模型未给出可校验的原文引文。'} for e in evidence]
  if not excerpts:excerpts=[{'page':1,'section_id':'unknown','paragraph_id':'unknown','evidence_text':'模型未提供原文证据。请在原始 PDF 中核对；不能仅凭此记录确认。'}]
  sev='高' if any(i['severity']=='high' for i in issues) else '中'
  hint='Candidate；高、中级只是自动核验优先级，不代表已证实错误。请对照原始 PDF；作者原文冲突、模型错误和无法确认应分别记录。'
  candidate=json.dumps({'candidate':val,'automatic_flags':issues,'additional_checks':extra.get((doi,path),(None,None))[0]},ensure_ascii=False,indent=2)
  data={'field':path,'evidence':excerpts,'status_hint':hint,'pages':sorted({e['page'] for e in excerpts}),'candidate':candidate,'severity':sev}
  files=[]
  for f,role in [(root/r['raw_file'],'raw'),(root/r['parsed_file'],'parsed'),(attempt/'response.json','model_candidate'),(attempt/'scientific_audit.json','automatic_audit')]:
   rel=str(f.resolve().relative_to(root));item=source(root,rel,role);files.append(item);source_files[(rel,role)]=item
  if (doi,path) in extra:
   f=extra[(doi,path)][1];rel=str(f.resolve().relative_to(root));item=source(root,rel,'additional_validation');files.append(item);source_files[(rel,'additional_validation')]=item
  key='batch:'+hashlib.sha256((doi+'|'+path+'|'+digest(attempt/'response.json')).encode()).hexdigest()[:24]
  tasks.append(task('pilot',key,sev+'优先级 · '+path,doi,data,files))
 manifest={'version':'pilot-review-web-v1','root':str(root),'created_at':utc_now(),'sources':list(source_files.values()),'tasks':tasks,'model_invoked':False,'note':'Frozen snapshot of automated flags, never Gold. Human review is source-assisted and not automatically independent.'}
 output.mkdir(parents=True);mp=output/'review_manifest.json';mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 view={'manifest_sha256':digest(mp),'tasks':[dict(t,files=[dict(f,url='../../../'+f['path']) for f in t['files']]) for t in tasks],'root_depth':'../../../'}
 encoded=json.dumps(view,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
 template=(root/'scripts/review/pilot_web.html').read_text()
 template=template.replace('首篇论文与 7 篇重点摘要 · 直接在本机操作，无需联网','批量论文问题快照 · 高优先级在前 · 本机审核')
 template=template.replace('① 首篇论文','批量问题').replace('② 重点摘要','重点摘要（本批无）')
 template=template.replace('<button id="tab-abstract">','<button id="tab-abstract" hidden>')
 template=template.replace("let message=element('p',t.content.status_hint);", "let box=element('div');box.className='paper';box.append(element('strong','模型候选和自动核验标记（未确认）'),element('pre',t.content.candidate));left.append(box);let message=element('p',t.content.status_hint);")
 template=template.replace('<div class="toolbar"><label for="reviewer"', '<div class="toolbar"><label for="priority" style="margin:0">筛选</label><select id="priority" style="width:120px"><option value="all">全部问题</option><option value="高">高优先级</option></select><label for="reviewer"')
 template=template.replace("const list=packet.tasks.filter(t=>t.kind===kind);", "const list=packet.tasks.filter(t=>t.kind===kind&&($('priority').value==='all'||t.content.severity===$('priority').value));")
 template=template.replace("let l=packet.tasks.filter(x=>x.kind===kind);", "let l=packet.tasks.filter(x=>x.kind===kind&&($('priority').value==='all'||x.content.severity===$('priority').value));if(!l.length)return;")
 template=template.replace("let notes=element('textarea');", "let classification=element('select');classification.id='classification';for(let c of [['','请选择问题类型'],['source_conflict','作者原文冲突'],['model_error','模型提取错误'],['parser_error','解析问题'],['unconfirmed','无法确认'],['no_material_issue','核对后无实质问题']])classification.add(new Option(c[1],c[0]));classification.value=state.classification||'';right.append(element('label','问题类型'),classification);let notes=element('textarea');")
 template=template.replace("if(!reviewer||!decision){", "if(!reviewer||!decision||!classification.value){")
 template=template.replace("请先填写审核人并选择审核状态。", "请先填写审核人，选择审核状态和问题类型。")
 template=template.replace("notes:notes.value.trim(),reviewer,reviewed_at", "notes:notes.value.trim(),classification:classification.value,reviewer,reviewed_at")
 template=template.replace("render();\n</script>", "$('priority').onchange=()=>{let l=packet.tasks.filter(t=>t.kind===kind&&($('priority').value==='all'||t.content.severity===$('priority').value));if(l.length)selected=l[0];render()};render();\n</script>")
 template=template.replace("a.download='首篇论文审核结果.json'","a.download='批量论文审核结果.json'")
 (output/'index.html').write_text(template.replace('__PACKET_JSON__',encoded))
 return {'output':str(output),'tasks':len(tasks),'papers':len({t['doi'] for t in tasks}),'created_at':manifest['created_at'],'independent_human_gold':False}

def import_batch_results(manifest,results,output,root=ROOT):
 data=json.loads(Path(results).read_text());allowed={'source_conflict','model_error','parser_error','unconfirmed','no_material_issue'}
 for e in data.get('entries',[]):
  if e.get('status')!='pending' and e.get('classification') not in allowed:raise ValueError('invalid_or_missing_issue_classification')
 return import_results(manifest,results,output,root=root)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output');p.add_argument('--import-manifest');p.add_argument('--import-results');a=p.parse_args()
 if not a.output:p.error('--output is required')
 if bool(a.import_manifest)!=bool(a.import_results):p.error('supply both import arguments')
 result=import_batch_results(a.import_manifest,a.import_results,a.output) if a.import_manifest else build(a.output)
 print(json.dumps(result,ensure_ascii=False))
