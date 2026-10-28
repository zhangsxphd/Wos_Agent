"""Inspect three existing development requests; prepare human-only reference sheets."""
import csv, json, re
from pathlib import Path
from scripts.fulltext.admission import inspect_fulltext
from scripts.pipeline_utils import ROOT,read_jsonl
from scripts.quality.snapshot import digest,verify_snapshot
from scripts.review.packet import CAN,INV,QUALITY,csv_out

OLD='data/reports/literature_quality_20261028/pilot_v2/pilot_selection_manifest.json'
DEV='data/evidence_benchmarks/v07_dev40_gold_work/abstracts.jsonl'
DOIS=['10.1002/ldr.4815','10.1038/s41598-025-93619-9','10.3390/w16182598']
FIELDS=['study_object','study_method','treatment_factors_levels','control','replicates','measurements','numerical_results_units_comparators','statistical_significance','author_conclusion']
STATUSES=('reported','not_reported','not_applicable','uncertain')

def build(output,root=ROOT):
 root=Path(root).resolve();output=Path(output);rows=read_jsonl(root/QUALITY/'quality_rows.jsonl')
 verify_snapshot(json.loads((root/QUALITY/'quality_summary.json').read_text()),root/QUALITY/'quality_rows.jsonl',root/CAN,root/INV,rows,root)
 old=json.loads((root/OLD).read_text());known=read_jsonl(root/DEV)
 if digest(root/DEV)!=old['inputs']['dev40_sha256']:raise ValueError('dev40_changed')
 known_uid={r['uid'] for r in known};known_doi={(r.get('doi') or '').lower() for r in known}
 records={r['uid']:r for r in read_jsonl(root/CAN)};manifests={r['uid']:r for r in read_jsonl(root/INV)}
 selected=[];hints=[];templates=[];summaries=[]
 for doi in DOIS:
  paper=next(p for p in old['papers'] if p['doi']==doi)
  if paper['uid'] in known_uid or doi.lower() in known_doi:raise ValueError('pilot_dev40_overlap')
  parsed,gate=inspect_fulltext(records[paper['uid']],manifests[paper['uid']],root)
  if gate['raw_sha256']!=paper['raw_sha256'] or gate['parsed_sha256']!=paper['parsed_sha256']:raise ValueError('pilot_source_changed')
  sections={s['section_id']:s for s in parsed['sections']};spans={};duplicates=0;request_details=[]
  for req in paper['request_files']:
   if digest(root/req)!=paper['request_sha256'][req]:raise ValueError('pilot_request_changed')
   r=json.loads((root/req).read_text());s=r['section'];
   if r['uid']!=paper['uid'] or r['doi']!=doi or r['document_sha256']!=gate['raw_sha256'] or r['parsed_sha256']!=gate['parsed_sha256']:raise ValueError('pilot_request_identity_mismatch')
   for p in s['paragraphs']:
    original=next(p0 for p0 in sections[s['section_id']]['paragraphs'] if p0['paragraph_id']==p['paragraph_id'])['text']
    if original[p['char_start']:p['char_end']]!=p['text']:raise ValueError('pilot_chunk_anchor_mismatch')
    key=(s['section_id'],p['paragraph_id'],p['char_start'],p['char_end']);duplicates+=key in spans;spans[key]=True
   request_details.append({'path':req,'sha256':paper['request_sha256'][req],'section_id':s['section_id']})
  patterns={'methods':r'(?i)\b(?:materials\s+and\s+methods|methodology|methods)\b','results':r'(?i)\bresults\b','table':r'(?i)\btable\s+\d','figure':r'(?i)\bfig(?:ure)?\.?\s*\d','references':r'(?i)\breferences\b'}
  locations={k:[] for k in patterns}
  for sec in parsed['sections']:
   for para in sec['paragraphs']:
    for name,pat in patterns.items():
     match=re.search(pat,para['text'])
     if match and len(locations[name])<8:
      start=match.start();end=min(len(para['text']),match.end()+100)
      hit={'kind':name,'section_id':sec['section_id'],'paragraph_id':para['paragraph_id'],'start':start,'end':end,'source_excerpt':para['text'][start:end]}
      locations[name].append(sec['section_id']);hints.append({'uid':paper['uid'],'doi':doi,**hit})
  one={'uid':paper['uid'],'doi':doi,'title':paper['title'],'study_type':'original_research_candidate_human_confirmation_required','raw_file':paper['raw_file'],'raw_sha256':gate['raw_sha256'],'parsed_file':paper['parsed_file'],'parsed_sha256':gate['parsed_sha256'],'parser':gate['parser'],'known_dev40_overlap':False,'historical_holdout_overlap':'unknown_registry_missing','human_review_status':'pending','request_count':len(request_details),'locations':locations}
  selected.append(one)
  for field in FIELDS:templates.append({'uid':paper['uid'],'doi':doi,'field':field,'occurrence':'1','status':'pending','value':'','unit':'','comparison':'','section':'','page':'','paragraph_id':'','start':'','end':'','evidence_text':'','reviewer':'','reviewed_at':'','notes':'','raw_sha256':gate['raw_sha256'],'parsed_sha256':gate['parsed_sha256']})
  summaries.append({'uid':paper['uid'],'doi':doi,'requests':request_details,'exact_duplicate_chunk_spans':duplicates,'pages':len(sections),'page_scoped_chunks':True,'routing':'requires_review','cross_page_context':'not included automatically','methods_and_results_completeness':'not established by heading matching','figures_and_tables':'image-only content not extracted; human check required','boundary_risk':'page boundary may split sentences or treatments; no overlap guarantee','locations':locations})
 output.mkdir(parents=True,exist_ok=False)
 (output/'selected_papers.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
 (output/'source_hints.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in hints),encoding='utf-8')
 csv_out(output/'evidence_reference_template.csv',templates,list(templates[0]))
 summary={'model_invoked':False,'all_previous_papers':len(old['papers']),'all_previous_requests':old['request_count'],'selected_papers':len(selected),'selected_request_count':sum(len(x['requests']) for x in summaries),'dev40_sha256':digest(root/DEV),'holdout_overlap':'unknown_registry_missing','papers':summaries}
 (output/'worker_request_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
 lines=['# 首批全文提取准备情况','','三篇均是原始研究候选，研究类型及全文完整性仍需人工确认；它们属于开发诊断，不是独立留出集。未调用模型，未生成参考答案。','',f"原 12 篇共 {old['request_count']} 个请求；选中三篇共 {summary['selected_request_count']} 个请求。请求按页切分，均为 requires_review，未自动携带跨页信息。",'']
 for s in summaries:lines += [f"## {s['doi']}",f"PDF 页数 {s['pages']}；请求 {len(s['requests'])}；完全重复的分块锚点 {s['exact_duplicate_chunk_spans']}。",f"定位线索：{json.dumps(s['locations'],ensure_ascii=False)}。",'标题命中只是定位线索，不能证明 Methods/Results 完整。表格、图像、图注、跨页句子与跨分块处理组需要对照原 PDF 核对。','']
 lines+=['## 独立人工参考','先阅读原文，按需复制字段行以记录多条证据，填写原文页码、段落和原文；不得参考模型输出。reported、not_reported、not_applicable 和 uncertain 含义不同，未操作保持 pending。综述及 Meta 分析无需强行填写自己的实验处理和重复次数。','旧 Holdout20 和 Future Holdout30 登记缺失，无法宣称独立。真实模型调用、费用和准确率均尚不存在。']
 (output/'extraction_readiness_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 return {'papers':len(selected),'requests':summary['selected_request_count']}

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.output),ensure_ascii=False))


def validate_reference_rows(rows):
 """Check reference-row structure without manufacturing a reference answer."""
 seen=set()
 for row in rows:
  key=(row.get('uid'),row.get('field'),str(row.get('occurrence')))
  if not all(key) or key in seen:raise ValueError('duplicate_or_missing_reference_occurrence')
  seen.add(key);status=row.get('status')
  if status=='pending':continue
  if status not in STATUSES:raise ValueError('invalid_reference_status')
  if not row.get('reviewer'):raise ValueError('reference_reviewer_required')
  if status=='reported' and (not row.get('value') or not row.get('evidence_text')):raise ValueError('reported_reference_needs_evidence')
  if status in ('not_reported','not_applicable') and row.get('value'):raise ValueError('absence_status_cannot_have_value')
 return True
