"""Validate and packetize an AI-assisted reference-Gold candidate, never predictions."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from jsonschema import Draft202012Validator
from scripts.extractors.schema_validator_v2 import EvidenceValidator,factual_leaves,scale_supported

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'data/evidence_benchmarks/v07_future_holdout30_gold_work'
CAND=ROOT/'data/evidence_benchmarks/v07_future_holdout30_gold_candidate'
PACKET=ROOT/'data/reports/v07_future_holdout30_gold_human_review.md'

def read_jsonl(path): return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def compact(value): return json.dumps(value,ensure_ascii=False,indent=2)
def validate(rows,inputs,schema_path):
 schema=json.loads(Path(schema_path).read_text(encoding='utf-8')); sv=Draft202012Validator(schema); ev=EvidenceValidator(schema_path); by={r['uid']:r for r in inputs}; seen=set(); per={}; counts={'records':len(rows),'identity_errors':0,'schema_valid':0,'grounding_valid':0,'invalid_offsets':0,'orphan_anchors':0,'missing_supports':0,'unsupported':0}
 for row in rows:
  uid=row.get('uid'); record=by.get(uid); errors=[]
  if record is None or uid in seen: counts['identity_errors']+=1; errors.append('identity_or_duplicate')
  else: seen.add(uid)
  serr=list(sv.iter_errors(row)); counts['schema_valid']+=not bool(serr)
  if serr: errors.append('schema')
  grounding_pointers=[]
  if record:
   try: ev.validate(row,record); counts['grounding_valid']+=1
   except Exception as exc:
    errors.append(str(exc))
    for pointer,value in factual_leaves(row.get('evidence',{})):
     anchor=(row.get('evidence_support') or {}).get(pointer) or {}; quote=anchor.get('evidence_text','')
     if pointer.endswith('/experimental_scale'):
      if not scale_supported(value,quote): grounding_pointers.append(pointer)
     elif str(value).casefold() not in quote.casefold(): grounding_pointers.append(pointer)
  paths={p for p,_ in factual_leaves(row.get('evidence',{}))}; support=row.get('evidence_support') or {}
  orphan=len(set(support)-paths); missing=len(paths-set(support)); counts['orphan_anchors']+=orphan; counts['missing_supports']+=missing
  if orphan: errors.append('orphan_anchors')
  if missing: errors.append('missing_supports')
  offsets=0
  if record:
   anchors=list(support.values())+row.get('evidence',{}).get('findings',[])+[x.get('anchor',{}) for x in row.get('evidence',{}).get('author_interpretations',[])]
   for a in anchors:
    if a.get('source')=='abstract':
     s,e,q=a.get('start'),a.get('end'),a.get('evidence_text','')
     if type(s)!=int or type(e)!=int or s<0 or e<=s or record['abstract'][s:e]!=q: offsets+=1
  counts['invalid_offsets']+=offsets
  if offsets: errors.append('invalid_offsets')
  counts['unsupported']+=int(any(x in ('Evidence text is not an abstract substring','Evidence offsets do not match the original abstract','Evidence value or numerical value is not a source span','Experimental scale is not explicitly supported','Finding claim or numerical value is not a verbatim supported span','Author interpretation has no verbatim support') for x in errors))
  per[uid]={'valid':not errors,'errors':errors,'schema_errors':len(serr),'grounding_valid':not any(x not in ('schema','orphan_anchors','missing_supports','invalid_offsets') for x in errors),'grounding_pointers':grounding_pointers,'invalid_offsets':offsets,'orphan_anchors':orphan,'missing_supports':missing}
 counts['missing_records']=len(set(by)-seen)
 return {'validator':'EvidenceValidator v2','schema':'Evidence Matrix v0.4','counts':counts,'per_uid':per}

def build_packet(inputs,a,b,candidate,comparison,decisions,validation,out):
 a_by={x['uid']:x for x in a}; b_by={x['uid']:x for x in b}; g_by={x['uid']:x for x in candidate}; in_by={x['uid']:x for x in inputs}
 dec_by={x['id']:x for x in decisions}; comp_by=comparison['per_uid']; disagreements=comparison['disagreements']; by_uid={}
 for i,item in enumerate(disagreements,1):
  item=dict(item); item['id']=f'D{i:04d}'; item['decision']=dec_by[item['id']]; by_uid.setdefault(item['uid'],[]).append(item)
 lines=['# v0.7 Future Holdout30 — Owner Human Review Packet','',
 '**Required owner action:** review all 30 papers below. This is an AI-assisted Gold candidate, not human-approved Gold. Each proposed paper action remains `REVIEW_REQUIRED`; do not start target extraction, predictions, benchmark, or scoring until all 30 are explicitly reviewed and approved.','',
 '## Packet status','',
 '- Dataset: frozen Future Holdout30, 30 identities.','- Final extraction prompt SHA256: `b94af358467e31e6fc30e6216c12201383e47ce4f6440881da9c9f5f0d9b7ea2` (not given to annotators or adjudicator).','- Reference annotators A and B: separate fresh ephemeral sessions. Adjudicator C: separate fresh ephemeral session.','- Candidate status: `ai_assisted_holdout_gold_candidate`; `human_approved=false`.','- Owner review requirement: all 30 papers.','- Target extraction/predictions/evaluation: not run.','- Metric values/performance scores: not computed.','',
 f'- A/B comparison: fields exact={comparison["fields"].get("exact",0)}, boundary-equivalent={comparison["fields"].get("boundary_equivalent",0)}, A-only={comparison["fields"].get("a_only",0)}, B-only={comparison["fields"].get("b_only",0)}; findings exact={comparison["findings"].get("exact",0)}, boundary={comparison["findings"].get("boundary",0)}, A-only={comparison["findings"].get("a_only",0)}, B-only={comparison["findings"].get("b_only",0)}.','',
 'This agreement summary is only between reference annotators; it is not extraction performance. For each paper, the full abstract, all differences, C decisions, validation status, and proposed Evidence Matrix are included. Exact support text and offsets are retained in the candidate JSON.','']
 for n,base in enumerate(inputs,1):
  uid=base['uid']; record=g_by[uid]; pair=comp_by[uid]; v=validation['per_uid'][uid]; diffs=by_uid.get(uid,[]); c_counts={}
  for x in diffs: c_counts[x['decision'].get('decision')]=c_counts.get(x['decision'].get('decision'),0)+1
  lines += [f'## Paper {n:02d} — {base.get("title") or "(title unavailable)"}','',f'- UID: `{uid}`',f'- DOI: `{base.get("doi") or "(not supplied)"}`',f'- Year: `{base.get("publish_year") or "(not supplied)"}`',f'- Journal: {base.get("source_title") or "(not supplied)"}',f'- Document type: {json.dumps(base.get("document_types"),ensure_ascii=False)}',f'- Proposed action: **REVIEW_REQUIRED**',f'- Candidate validator v2: **{"PASS" if v["valid"] else "REVIEW REQUIRED"}**; unsupported/grounding pointers: {json.dumps(v.get("grounding_pointers",[]),ensure_ascii=False)}; details: {json.dumps(v["errors"],ensure_ascii=False)}',f'- C decisions for this paper: {json.dumps(c_counts,ensure_ascii=False)}','', '**Abstract (complete):**','',base['abstract'],'', '**A/B comparison by field**','', '| Field | Exact | Boundary-equivalent | A-only | B-only |','|---|---:|---:|---:|---:|']
  for pointer,m in sorted(pair.get('fields',{}).items()): lines.append(f'| `{pointer}` | {m.get("exact_tp",0)} | {m.get("boundary_tp",0)} | {m.get("fn",0)} | {m.get("fp",0)} |')
  fm=pair.get('findings',{}); lines += ['',f'Findings — exact {fm.get("exact_match",0)}, boundary {fm.get("boundary_match",0)}, A-only {fm.get("unmatched_gold",0)}, B-only {fm.get("unmatched_prediction",0)}.','', '**A-only / B-only items and C adjudication**','']
  if not diffs: lines.append('No A/B-only discrepancy items. The paper still requires owner verification.')
  for x in diffs:
   item=x; c=x['decision']; direction=item.get('direction'); side=direction.split('_')[0] if direction else 'AB'
   val=item.get('claim') if item.get('kind')=='finding' else item.get('value')
   support=item.get('support') or item.get('a_support') or item.get('b_support')
   lines += [f'- **{item["id"]} · {item.get("kind")} · {item.get("field")} · {direction}**',f'  - {side} item: {json.dumps(val,ensure_ascii=False)}']
   if support: lines.append(f'  - {side} evidence: {json.dumps(support,ensure_ascii=False)}')
   lines += [f'  - C: **{c.get("decision")}** — {c.get("rationale")}',f'  - Contract rule: {c.get("contract_rule")}']
   if c.get('decision')=='MODIFY': lines += [f'  - C proposed: {json.dumps(c.get("candidate_value"),ensure_ascii=False)}',f'  - C support: {json.dumps(c.get("candidate_support"),ensure_ascii=False)}']
  lines += ['', '**Proposed Evidence Matrix (candidate JSON, exact anchors and offsets):**','', '```json',compact(record),'```','', 'Owner review: [ ] reviewed against abstract and Contract v2.1   Decision: ACCEPT_PAPER / REVIEW_REQUIRED','', '---','']
 Path(out).write_text('\n'.join(lines),encoding='utf-8')

def main():
 inputs=read_jsonl(BASE/'requests.jsonl'); a=read_jsonl(BASE/'annotator_A/annotations.jsonl'); b=read_jsonl(BASE/'annotator_B/annotations.jsonl'); rows=read_jsonl(CAND/'candidate_gold.jsonl'); decisions=read_jsonl(BASE/'adjudicator_C/adjudications.jsonl')
 cmp=json.loads((BASE/'ab_comparison.json').read_text()); d_items=cmp['disagreements']
 if len(inputs)!=30 or len(rows)!=30 or len(a)!=30 or len(b)!=30: raise ValueError('future_gold_not_30_each')
 if len(decisions)!=len(d_items) or len({x['id'] for x in decisions})!=len(decisions): raise ValueError('adjudication_coverage_not_exact')
 schema=BASE/'annotator_A/evidence_matrix.schema.json'; report=validate(rows,inputs,schema)
 a_validation=validate(a,inputs,schema); b_validation=validate(b,inputs,schema)
 (BASE/'annotator_A_validation.json').write_text(json.dumps(a_validation,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (BASE/'annotator_B_validation.json').write_text(json.dumps(b_validation,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (CAND/'validation_v2.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 build_packet(inputs,a,b,rows,cmp,decisions,report,PACKET)
 manifest=json.loads((CAND/'candidate_manifest.json').read_text())
 manifest.update({'status':'ai_assisted_holdout_gold_candidate','gold_status':'ai_assisted_holdout_gold_candidate','human_approved':False,'owner_human_review_complete':False,'future_holdout_abstracts_accessed':True,'gold_candidate_created':True,'target_extraction_run':False,'target_predictions_generated':False,'benchmark_or_score_run':False,'annotator_A_records':len(a),'annotator_B_records':len(b),'adjudicator_items':len(decisions),'adjudicator_decision_counts':{k:sum(x['decision']==k for x in decisions) for k in ('A','B','MODIFY','NEEDS_HUMAN_REVIEW')},'validation_v2':report['counts'],'candidate_sha256':sha(CAND/'candidate_gold.jsonl'),'review_packet_sha256':sha(PACKET),'annotator_A_sha256':sha(BASE/'annotator_A/annotations.jsonl'),'annotator_B_sha256':sha(BASE/'annotator_B/annotations.jsonl'),'adjudicator_sha256':sha(BASE/'adjudicator_C/adjudications.jsonl'),'V07_FINAL_PROMPT_FROZEN':True,'STOP_PROMPT_TUNING':True})
 manifest['future_holdout_gold_created']=True
 manifest['future_holdout_extraction_run']=False
 manifest['future_holdout_predictions_generated']=False
 manifest['future_holdout_benchmark_or_score_run']=False
 manifest['needs_human_review_uids']=sorted(uid for uid,v in report['per_uid'].items() if not v['valid'])
 (CAND/'candidate_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 # Update system-freeze contamination ledger only; frozen artifact hashes remain unchanged.
 fm_path=ROOT/'data/evidence_benchmarks/v07_final_system_freeze_manifest.json'; fm=json.loads(fm_path.read_text()); fm.update({'future_holdout_abstracts_accessed':True,'gold_builders_received_final_prompt':False,'target_extraction_run':False,'target_predictions_generated':False,'benchmark_or_score_run':False,'owner_human_review_complete':False,'gold_status':'ai_assisted_holdout_gold_candidate','gold_candidate_sha256':manifest['candidate_sha256'],'human_review_packet_sha256':manifest['review_packet_sha256']}); fm['development_response_sha256']={name:sha(ROOT/f'data/evidence_batches/v07_dev40_{name}/worker_response_sha256.json') for name in ('baseline','i1','i2')}
 fm.update({'V07_FUTURE_HOLDOUT_FROZEN_BEFORE_TUNING':True,'gold_candidate_created':True,'owner_review_packet_created':True,'future_holdout_extraction_run':False,'future_holdout_predictions_generated':False,'future_holdout_benchmark_or_score_run':False})
 fm_path.write_text(json.dumps(fm,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 selection_path=ROOT/'data/evidence_benchmarks/v07_future_holdout30/selection_manifest.json'; selection=json.loads(selection_path.read_text()); selection.update({'gold_created':True,'gold_status':'ai_assisted_holdout_gold_candidate','extraction_run':False,'future_holdout_abstracts_accessed':True,'abstracts_materialized':True}); selection_path.write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 workflow={'stage':'v0.7 final frozen-prompt reference-Gold preparation','annotators':{'A':{'fresh_ephemeral':True,'records':len(a),'sha256':sha(BASE/'annotator_A/annotations.jsonl'),'validation_v2':a_validation['counts']},'B':{'fresh_ephemeral':True,'records':len(b),'sha256':sha(BASE/'annotator_B/annotations.jsonl'),'validation_v2':b_validation['counts']}},'adjudicator_C':{'fresh_ephemeral':True,'items':len(decisions),'sha256':sha(BASE/'adjudicator_C/adjudications.jsonl')},'a_b_comparison_sha256':sha(BASE/'ab_comparison.json'),'candidate_sha256':manifest['candidate_sha256'],'review_packet_sha256':manifest['review_packet_sha256'],'target_extraction_run':False,'target_predictions_generated':False,'benchmark_or_score_run':False,'human_approved':False}
 (CAND/'workflow_manifest.json').write_text(json.dumps(workflow,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'candidate_status':manifest['gold_status'],'candidate_records':len(rows),'validation':report['counts'],'adjudicator_counts':manifest['adjudicator_decision_counts'],'human_review_packet':str(PACKET),'packet_sha256':manifest['review_packet_sha256'],'target_extraction_run':False,'target_predictions_generated':False,'benchmark_or_score_run':False},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
