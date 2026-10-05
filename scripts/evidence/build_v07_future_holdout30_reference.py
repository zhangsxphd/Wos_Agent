"""Prepare isolated reference-Gold annotator bundles for frozen Future Holdout30.

This tool is for reference-Gold construction only. It does not import or call
an extraction prompt, target extractor, or evaluation metric.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess
from pathlib import Path
from scripts.evidence.freeze_v07_datasets import metadata_only_row, _skip_string, _skip_value, _skip_ws

ROOT=Path(__file__).resolve().parents[2]
CORPUS=ROOT/'data/processed/saline_paddy_v056_full_enriched_20261003.jsonl'
IDENTITIES=ROOT/'data/evidence_benchmarks/v07_future_holdout30/identities.json'
SELECT=ROOT/'data/evidence_benchmarks/v07_future_holdout30/selection_manifest.json'
FREEZE=ROOT/'data/evidence_benchmarks/v07_final_system_freeze_manifest.json'
SCHEMA=ROOT/'schemas/evidence_matrix.schema.json'
CONTRACT=ROOT/'docs/evidence_field_contract_v2_1.md'
WORK=ROOT/'data/evidence_benchmarks/v07_future_holdout30_gold_work'
CANDIDATE=ROOT/'data/evidence_benchmarks/v07_future_holdout30_gold_candidate'

ANNOTATION_PROTOCOL='''# Gold annotation protocol — v0.7 Future Holdout30

This is reference-Gold annotation, not target extraction. Annotate each supplied paper independently and use only that record's abstract for scientific claims. Metadata is only for identity. Follow the supplied Evidence Matrix schema and Evidence Field Contract v2.1. Do not use outside information, browse, search databases, consult memories or repository files, or use any extraction prompts, model predictions, benchmark results, or other papers.

For every input UID return exactly one Evidence Matrix v0.4 record. Copy uid, doi, and title exactly. Use screening.status `maybe` unless the abstract is absent (all supplied records should have abstracts). Include all schema-required `evidence` keys. Leave unsupported values empty/null/`unknown`; do not infer. Keep every inference array empty.

For each populated ordinary evidence leaf add exactly one matching JSON Pointer in `evidence_support` with `source=abstract`, a verbatim evidence_text, and zero-based Unicode character offsets `[start,end)` into that record's exact abstract. Findings and author interpretations use their own nested source/evidence_text/start/end anchors and must not be duplicated in the global support map. Finding claim must be a verbatim substring of evidence_text. Never invent or paraphrase evidence; don't use a title as support. Do not add keys or modify the supplied schema.

Apply the supplied Contract v2.1 to ontology boundaries. In particular: actual study-system linkage for soil type and salinity; distinguish study scale from named model; plant physiology and plant ions route to plant_growth; nutrients, carbon, soil chemistry, microbial variables, water use, and other measurements remain in their contract categories; distinguish actually imposed treatments from recommendations/background; record findings only when supported; preserve explicit attribution for Reviews. When a passage does not support a category, omit that value. Do not optimize for agreement with another annotator.

Before writing, check complete 30-UID coverage, no duplicate UIDs, schema shape, exact support strings/offsets, empty inference arrays, and absence of extra keys. Write only `annotations.jsonl`, one JSON object per line. Do not print paper contents in the final message.
'''

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def raw_value(line:bytes,wanted:set[str]):
 data=line.strip(); i=_skip_ws(data,0)
 if not data or data[i]!=123: raise ValueError('bad_jsonl_record')
 i+=1; found={}
 while True:
  i=_skip_ws(data,i)
  if i>=len(data): raise ValueError('unterminated_json')
  if data[i]==125: break
  end_key=_skip_string(data,i); key=json.loads(data[i:end_key].decode())
  i=_skip_ws(data,end_key)
  if data[i]!=58: raise ValueError('expected_colon')
  start=_skip_ws(data,i+1); end=_skip_value(data,start)
  if key in wanted: found[key]=data[start:end]
  i=_skip_ws(data,end)
  if i<len(data) and data[i]==44: i+=1
 return found

def get_holdout_records():
 rows=json.loads(IDENTITIES.read_text(encoding='utf-8'))
 if not isinstance(rows,list) or len(rows)!=30 or len({r['uid'] for r in rows})!=30: raise ValueError('holdout_identity_registry_not_exactly_30')
 wanted={r['uid']:r for r in rows}; out={}
 with CORPUS.open('rb') as stream:
  for line in stream:
   if not line.strip(): continue
   meta,_=metadata_only_row(line)
   uid=meta['uid']
   if uid not in wanted: continue
   raw=raw_value(line,{'abstract'})
   if 'abstract' not in raw: raise ValueError('holdout_abstract_missing:'+uid)
   abstract=json.loads(raw['abstract'].decode('utf-8'))
   if not isinstance(abstract,str) or not abstract.strip(): raise ValueError('holdout_abstract_empty:'+uid)
   # The identity registry is canonical. Verify metadata; do not let a corpus drift alter the holdout.
   for k in ('uid','doi','title','publish_year','document_types','source_title'):
    if meta.get(k)!=wanted[uid].get(k): raise ValueError('holdout_identity_metadata_mismatch:'+uid+':'+k)
   out[uid]={k:meta.get(k) for k in ('uid','doi','title','publish_year','document_types','source_title')}
   out[uid]['abstract']=abstract
 if set(out)!=set(wanted): raise ValueError(f'holdout_abstract_coverage:{len(out)}/30')
 return [out[u] for u in sorted(wanted)]

def prepare():
 if WORK.exists(): raise FileExistsError('refusing_to_overwrite_future_gold_work')
 freeze=json.loads(FREEZE.read_text())
 if freeze.get('V07_FINAL_PROMPT_FROZEN') is not True or freeze.get('STOP_PROMPT_TUNING') is not True: raise ValueError('final_prompt_not_frozen')
 if freeze.get('future_holdout_abstracts_accessed') is not False: raise ValueError('abstract_access_flag_not_false_before_first_read')
 for key,path in [('final_prompt','prompts/evidence_extraction_v07_final.md'),('contract_v2_1','docs/evidence_field_contract_v2_1.md'),('validator_v2','scripts/extractors/schema_validator_v2.py'),('schema','schemas/evidence_matrix.schema.json'),('metric_v1','scripts/evidence/metrics.py'),('metric_v2','scripts/evidence/metrics_v2.py'),('future_holdout30_identities','data/evidence_benchmarks/v07_future_holdout30/identities.json'),('evaluation_protocol','docs/v07_final_evaluation_protocol.md')]:
  if sha(ROOT/path)!=freeze['artifact_sha256'][key]: raise ValueError('frozen_artifact_hash_changed:'+key)
 select=json.loads(SELECT.read_text())
 if select.get('identities_sha256')!=freeze['artifact_sha256']['future_holdout30_identities'] or select.get('V07_FUTURE_HOLDOUT_FROZEN_BEFORE_TUNING') is not True: raise ValueError('holdout_identity_freeze_mismatch')
 # This is the first deliberate abstract materialization, after all required freezes above.
 records=get_holdout_records()
 WORK.mkdir(parents=True)
 req=WORK/'requests.jsonl'; req.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
 (WORK/'evidence_matrix.schema.json').write_bytes(SCHEMA.read_bytes())
 (WORK/'evidence_field_contract_v2_1.md').write_bytes(CONTRACT.read_bytes())
 (WORK/'GOLD_ANNOTATION_PROTOCOL.md').write_text(ANNOTATION_PROTOCOL,encoding='utf-8')
 for name in ('A','B'):
  bundle=WORK/f'annotator_{name}'; bundle.mkdir()
  for f in ('requests.jsonl','evidence_matrix.schema.json','evidence_field_contract_v2_1.md','GOLD_ANNOTATION_PROTOCOL.md'):
   shutil.copyfile(WORK/f,bundle/f)
  (bundle/'TASK.md').write_text(f'''# Task for blind Gold Annotator {name}\n\nRead only the files in this directory. Follow `GOLD_ANNOTATION_PROTOCOL.md` exactly. Annotate every record in `requests.jsonl` as an independent reference-Gold annotator. Use only `evidence_matrix.schema.json` and `evidence_field_contract_v2_1.md` for structure and rules. Write exactly 30 JSON objects to `annotations.jsonl`, one per line, preserving exact identifiers and evidence offsets. Do not create any other file, read parent directories, or access tools outside this bundle. Do not summarize the annotations in terminal output.\n''',encoding='utf-8')
  assert {p.name for p in bundle.iterdir()}=={'requests.jsonl','evidence_matrix.schema.json','evidence_field_contract_v2_1.md','GOLD_ANNOTATION_PROTOCOL.md','TASK.md'}
 # Mark abstract access immediately after materialization, before workers.
 select.update({'abstracts_materialized':True,'future_holdout_abstracts_accessed':True,'gold_created':False,'extraction_run':False})
 SELECT.write_text(json.dumps(select,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 freeze.update({'future_holdout_abstracts_accessed':True,'gold_builders_received_final_prompt':False,'target_extraction_run':False,'target_predictions_generated':False,'benchmark_or_score_run':False,'owner_human_review_complete':False})
 FREEZE.write_text(json.dumps(freeze,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return {'count':len(records),'uids_sha256':sha(IDENTITIES),'request_sha256':sha(req),'annotator_bundle_files':sorted(p.name for p in (WORK/'annotator_A').iterdir()),'abstracts_accessed':True}

def run_worker(name,codex='/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'):
 bundle=WORK/f'annotator_{name}'; out=bundle/'annotations.jsonl'
 if out.exists(): raise FileExistsError('refusing_worker_overwrite:'+name)
 env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ('API_KEY','APIKEY','SECRET','TOKEN','OPENAI_API_KEY','ANTHROPIC'))}
 cmd=[codex,'exec','--ephemeral','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--sandbox','workspace-write','--cd',str(bundle),'--disable','apps','--disable','browser_use','--disable','browser_use_external','--disable','computer_use','--disable','in_app_browser','--disable','memories','--disable','plugins','--disable','skill_mcp_dependency_install','-c','sandbox_workspace_write.network_access=false','-c','web_search="disabled"','-c','memories.use_memories=false','-c','memories.generate_memories=false','Follow TASK.md exactly. Read only the files in this directory, process all 30 records independently, save annotations.jsonl, and report only the saved output path and record count.']
 proc=subprocess.run(cmd,cwd=bundle,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60*60,check=False)
 (WORK/f'annotator_{name}_worker.log').write_text(proc.stdout,encoding='utf-8')
 if proc.returncode!=0 or not out.is_file(): raise RuntimeError(f'blind_annotator_{name}_failed')
 return {'annotator':name,'exit_code':proc.returncode,'output_sha256':sha(out),'output_bytes':out.stat().st_size}

def main():
 ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest='cmd',required=True)
 sp.add_parser('prepare')
 p=sp.add_parser('worker');p.add_argument('--annotator',choices=['A','B'],required=True);p.add_argument('--codex',default='/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
 sp.add_parser('prepare-c')
 c=sp.add_parser('adjudicator');c.add_argument('--codex',default='/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
 a=ap.parse_args(); result=prepare() if a.cmd=='prepare' else run_worker(a.annotator,a.codex) if a.cmd=='worker' else prepare_c() if a.cmd=='prepare-c' else run_adjudicator(a.codex); print(json.dumps(result,ensure_ascii=False,indent=2))
ADJ_PROTOCOL='''# Blind Gold adjudication protocol — v0.7 Future Holdout30

You are the independent adjudicator for a reference-Gold candidate. For each provided A/B discrepancy item choose exactly one decision: `A`, `B`, `MODIFY`, or `NEEDS_HUMAN_REVIEW`. Use only the supplied abstract, A/B alternatives and their exact source support, and Contract v2.1. Do not read outside this directory. You must not access the final extraction prompt, target-extractor responses, development predictions/metrics, other papers, user profile, or external sources.

Use A or B only when the supplied evidence and Contract clearly support that choice. Use MODIFY only when the abstract directly supports a different candidate value; candidate_support must quote an exact abstract span with zero-based Unicode character offsets. If the contract/context does not resolve the conflict, evidence is ambiguous, or neither candidate is supportable, choose NEEDS_HUMAN_REVIEW. Do not invent evidence, silently delete findings, or interpret unsupported text. A/B choices preserve that side's value and source support. The controller will retain C-unresolved cases provisionally from A and clearly flag them for owner review.

Output one JSON object per supplied item in `adjudications.jsonl`, with exactly: `id`, `decision`, `candidate_value` (only for MODIFY; otherwise null), `candidate_support` (only for MODIFY; otherwise null), `contract_rule`, `rationale`. Every supplied ID must occur exactly once. Keep rationale short and specific. Do not include paper contents in terminal output.
'''

def prepare_c():
 comp_path=WORK/'ab_comparison.json'
 if not comp_path.exists(): raise FileNotFoundError('ab_comparison_missing')
 comp=json.loads(comp_path.read_text())
 req_by={r['uid']:r for r in json.loads('['+','.join((WORK/'requests.jsonl').read_text().splitlines())+']')}
 a_by={r['uid']:r for r in json.loads('['+','.join((WORK/'annotator_A/annotations.jsonl').read_text().splitlines())+']')}
 b_by={r['uid']:r for r in json.loads('['+','.join((WORK/'annotator_B/annotations.jsonl').read_text().splitlines())+']')}
 items=[]
 for i,item in enumerate(comp.get('disagreements',[]),1):
  uid=item['uid']; item=dict(item); item['id']=f'D{i:04d}'
  pointer=item.get('field',''); kind=item.get('kind')
  if kind=='experimental_scale':
   ae=(a_by[uid].get('evidence',{}).get('study_system',{}).get('experimental_scale') or 'unknown'); be=(b_by[uid].get('evidence',{}).get('study_system',{}).get('experimental_scale') or 'unknown')
   item.update(a_value=ae,b_value=be)
  elif kind=='finding':
   item['a_values']=a_by[uid]['evidence'].get('findings',[]); item['b_values']=b_by[uid]['evidence'].get('findings',[])
  elif kind=='author_interpretation':
   item['a_values']=a_by[uid]['evidence'].get('author_interpretations',[]); item['b_values']=b_by[uid]['evidence'].get('author_interpretations',[])
  else:
   def ptr(obj):
    cur=obj
    for part in pointer.split('/')[1:]: cur=cur[part.replace('~1','/').replace('~0','~')]
    return cur
   item['a_values']=ptr(a_by[uid]); item['b_values']=ptr(b_by[uid])
  item['abstract']=req_by[uid]['abstract']; item['doi']=req_by[uid].get('doi'); item['title']=req_by[uid].get('title')
  items.append(item)
 cdir=WORK/'adjudicator_C'
 if cdir.exists(): raise FileExistsError('refusing_to_overwrite_adjudicator_bundle')
 cdir.mkdir()
 (cdir/'adjudication_requests.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in items),encoding='utf-8')
 shutil.copyfile(CONTRACT,cdir/'evidence_field_contract_v2_1.md')
 (cdir/'GOLD_ADJUDICATION_PROTOCOL.md').write_text(ADJ_PROTOCOL,encoding='utf-8')
 (cdir/'TASK.md').write_text('''# Task for fresh blind adjudicator C\n\nRead only the files in this directory. Adjudicate every item in `adjudication_requests.jsonl` using `GOLD_ADJUDICATION_PROTOCOL.md` and the supplied Contract v2.1. The request contains abstract context plus A/B discrepancy evidence. Write exactly one JSON object per item to `adjudications.jsonl`, with every ID exactly once. Do not create any other file or inspect parent directories. Report only output path and item count.\n''',encoding='utf-8')
 assert {p.name for p in cdir.iterdir()}=={'adjudication_requests.jsonl','evidence_field_contract_v2_1.md','GOLD_ADJUDICATION_PROTOCOL.md','TASK.md'}
 return {'items':len(items),'ab_field_disagreement_items':len(items),'bundle_files':sorted(p.name for p in cdir.iterdir()),'target_prompt_included':False,'development_predictions_or_metrics_included':False}

def run_adjudicator(codex='/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'):
 bundle=WORK/'adjudicator_C'; out=bundle/'adjudications.jsonl'
 if out.exists(): raise FileExistsError('refusing_adjudicator_overwrite')
 env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ('API_KEY','APIKEY','SECRET','TOKEN','OPENAI_API_KEY','ANTHROPIC'))}
 cmd=[codex,'exec','--ephemeral','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--sandbox','workspace-write','--cd',str(bundle),'--disable','apps','--disable','browser_use','--disable','browser_use_external','--disable','computer_use','--disable','in_app_browser','--disable','memories','--disable','plugins','--disable','skill_mcp_dependency_install','-c','sandbox_workspace_write.network_access=false','-c','web_search="disabled"','-c','memories.use_memories=false','-c','memories.generate_memories=false','Follow TASK.md exactly. Read only the files in this directory, adjudicate all listed items, save adjudications.jsonl, and report only the output path and item count.']
 proc=subprocess.run(cmd,cwd=bundle,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60*60,check=False)
 (WORK/'adjudicator_C_worker.log').write_text(proc.stdout,encoding='utf-8')
 if proc.returncode!=0 or not out.is_file(): raise RuntimeError('fresh_blind_adjudicator_failed')
 return {'exit_code':proc.returncode,'output_sha256':sha(out),'output_bytes':out.stat().st_size}

if __name__=='__main__': main()
