"""Build the first-paper reference candidates, source audit, and offline fixtures."""
import hashlib,json,re,subprocess,tempfile
from datetime import datetime,timezone
from pathlib import Path
from scripts.fulltext.admission import inspect_fulltext
from scripts.pipeline_utils import ROOT,read_jsonl,utc_now
from scripts.pilot.first_pdf import build,table_candidates,UID,DOI,RAW_SHA
from scripts.pilot.worker_protocol import prepare_request,validate_request,validate_bound_request,validate_response,merge_responses,compare_fields
from scripts.quality.snapshot import digest
from scripts.review.packet import CAN,INV,object_hash
from scripts.review.pilot_web import import_results

REVIEW='data/reviews/imports/pilot_first_human_20261028/validated_human_review.jsonl'
REVIEW_SUM='data/reviews/imports/pilot_first_human_20261028/import_summary.json'
WEB='data/reviews/pilot_first_interactive_20261028/review_manifest.json'
ORIGINAL='data/reviews/imports/v07_round1_20261028_user/validated_decisions.jsonl'
APPROVED='data/reviews/approved_first_review_20261028_v1/human_accepted_abstracts.jsonl'
PRIOR='data/reviews/v07_review_round1/review_manifest.json'
PROMPT='prompts/pilot_fulltext_candidate_v1.md'
SCHEMA='schemas/pilot_fulltext_worker_candidate_v1.schema.json'

def jwrite(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def jlwrite(path,rows):path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
def payload_hash(data):return hashlib.sha256(data).hexdigest()

def replay(parsed,old):
 hits=[]
 for s in parsed['sections']:
  if s['section_id']!=old['section_id']:continue
  for p in s['paragraphs']:
   start=p['text'].find(old['evidence_text'])
   if start>=0:hits.append({'kind':'text','section_id':s['section_id'],'paragraph_id':p['paragraph_id'],'char_start':start,'char_end':start+len(old['evidence_text']),'quote':old['evidence_text']})
 return hits

def span(parsed,page,start_text,end_text=None,limit=700):
 section=parsed['sections'][page-1]
 for p in section['paragraphs']:
  i=p['text'].find(start_text)
  if i>=0:
   end=p['text'].find(end_text,i)+len(end_text) if end_text and end_text in p['text'][i:] else min(len(p['text']),i+limit)
   return {'kind':'text','section_id':section['section_id'],'paragraph_id':p['paragraph_id'],'char_start':i,'char_end':end,'quote':p['text'][i:end]}
 raise ValueError('source_phrase_missing:'+start_text)

def cell(tables,table,row,column):
 tab=next(x for x in tables if x['table']==table);c=next(x for x in tab['cells'] if x['row']==row and x['column']==column)
 if not c['text'] or not c['pdfium_char_indices']:raise ValueError('table_cell_missing')
 return {'kind':'table','table':table,'row':row,'column':column,'text':c['text']}

def build_output(output,download,root=ROOT):
 root=Path(root).resolve();output=Path(output)
 records=read_jsonl(root/CAN);rec=next(x for x in records if x['uid']==UID and x['doi']==DOI)
 inv=next(x for x in read_jsonl(root/INV) if x['uid']==UID);old,check=inspect_fulltext(rec,inv,root)
 if check['raw_sha256']!=RAW_SHA:raise ValueError('source_identity_changed')
 reviewpath=root/REVIEW;summary=json.loads((root/REVIEW_SUM).read_text());webpath=root/WEB
 if digest(reviewpath)!=summary['validated_sha256'] or digest(webpath)!=summary['manifest_sha256'] or digest(download)!=summary['returned_file_sha256']:raise ValueError('review_input_changed')
 # Replay the exact existing importer on the user-returned file; no prior records are overwritten.
 with tempfile.TemporaryDirectory() as tmp:
  verified=import_results(webpath,download,Path(tmp)/'checked',root=root)
  if verified['validated_sha256']!=digest(reviewpath):raise ValueError('review_revalidation_changed')
 decisions=read_jsonl(reviewpath);pilot=[x for x in decisions if x['kind']=='pilot'];abstract=[x for x in decisions if x['kind']=='abstract']
 if len(pilot)!=14 or len(abstract)!=7:raise ValueError('unexpected_human_review_coverage')
 web=json.loads(webpath.read_text());tasks={t['id']:t for t in web['tasks']}
 for d in decisions:
  task=tasks.get(d['task_id']);core={k:task[k] for k in ('kind','id','title','doi','content','files')} if task else None
  if not task or object_hash(core)!=d['object_sha256'] or d['doi']!=task['doi'] or not d['reviewer']:raise ValueError('review_object_mismatch')
  if datetime.fromisoformat(d['reviewed_at'].replace('Z','+00:00')).utcoffset() is None:raise ValueError('review_timestamp_invalid')
 raw=(root/inv['raw_file']).read_bytes();parsed=build(raw)
 if build(raw)!=parsed:raise ValueError('parser_not_reproducible')
 tables=table_candidates(raw)
 if table_candidates(raw)!=tables:raise ValueError('tables_not_reproducible')
 output.mkdir(parents=True,exist_ok=False)
 parsedpath=output/'parsed_first_pdf_v1.json';tablepath=output/'tables_v1.json';jwrite(parsedpath,parsed);jwrite(tablepath,tables)
 parsedsha=digest(parsedpath);tablesha=digest(tablepath)
 def anchor_exists(a):
  if a['kind']=='table':return any(t['table']==a['table'] and any(c['row']==a['row'] and c['column']==a['column'] and c['text']==a['text'] and c['pdfium_char_indices'] for c in t['cells']) for t in tables)
  return any(s['section_id']==a['section_id'] and any(p['paragraph_id']==a['paragraph_id'] and p['text'][a['char_start']:a['char_end']]==a['quote'] for p in s['paragraphs']) for s in parsed['sections'])
 observations=[];raw_replay_count=0;remap_count=0
 for d in pilot:
  t=tasks[d['task_id']];old_evidence=t['content']['evidence'];new=[];old_failed=[]
  for e in old_evidence:
   oldsec=next(s for s in old['sections'] if s['section_id']==e['section_id']);oldp=next(p for p in oldsec['paragraphs'] if p['paragraph_id']==e['paragraph_id'])
   if oldp['text'][e['char_start']:e['char_end']]!=e['evidence_text']:raise ValueError('old_user_anchor_mismatch')
   matches=replay(parsed,e)
   if len(matches)==1:new+=matches
   else:old_failed.append(e)
  if not old_failed:raw_replay_count+=1
  if d['task_id']=='pilot:conflict_total_nitrogen' and old_failed:
   new += [cell(tables,1,'ditch_water',x) for x in ('total_nitrogen','total_phosphorus','suspended_matter')];remap_count+=1
  elif old_failed:raise ValueError('new_anchor_replay_required:'+d['task_id'])
  if d['task_id']=='pilot:study_object':new += [span(parsed,2,'The experimental field was located','software).'),span(parsed,3,'Ningjing 47 was adopted','experimental variety.')]
  if d['task_id']=='pilot:measures':new.append(span(parsed,4,'The soil water content was determined','optical emission spectrometry.'))
  if d['task_id']=='pilot:author_conclusion':new.append(span(parsed,9,'(1) Ditch water salinity','security.'))
  if d['task_id']=='pilot:yield_table':new += [cell(tables,3,r,'yield') for r in ('T1','T2','T3','CK')]
  if d['task_id']=='pilot:contradiction_yield':new.append(span(parsed,8,'This was also proved by the fact','direct ditch water irrigation.'))
  if d['task_id']=='pilot:contradiction_as':new += [cell(tables,3,r,'As') for r in ('T1','T2','T3','CK')]
  if not new or not all(anchor_exists(a) for a in new):raise ValueError('new_anchor_invalid:'+d['task_id'])
  observations.append({'uid':UID,'doi':DOI,'record_id':d['task_id'],'human_record':d,'raw_sha256':RAW_SHA,
        'old_parsed_sha256':check['parsed_sha256'],'new_parsed_sha256':parsedsha,'new_tables_sha256':tablesha,
        'original_ui_anchors':old_evidence,'new_anchors':new,'all_original_ui_anchors_exactly_replayed':not old_failed,
        'remapping_note':'Removed clipped duplicate table text; visually calibrated PDF cell anchors used.' if old_failed else 'Exact quoted excerpt relocated; IDs and offsets recomputed.',
        'value_check':'Reported value may be a noncontiguous transcription; anchors locate its source, not an assertion of scientific truth.'})
 # Separate real source discrepancies, do not produce a single adjudicated value.
 conflicts=[
  {'conflict_id':'yield','topic':'yield_result_vs_author_statement','status':'unresolved','human_record_id':'pilot:contradiction_yield',
   'sides':[span(parsed,7,'The rice yields of the T1','T1 treatment had the lowest rice yield.'),span(parsed,7,'direct ditch water irrigation did not decrease the yield','yield.'),span(parsed,8,'This was also proved by the fact','direct ditch water irrigation.'),cell(tables,3,'T1','yield'),cell(tables,3,'CK','yield')],
   'note':'Source statements and table are separate; no corrected conclusion is inferred.'},
  {'conflict_id':'arsenic_significance','topic':'rice_As_text_vs_table_letters','status':'unresolved','human_record_id':'pilot:contradiction_as',
   'sides':[span(parsed,7,'but no significant','(p > 0.05).'),cell(tables,3,'T1','As'),cell(tables,3,'CK','As')],
   'note':'Text and table letter groups disagree; statistical output not available.'},
  {'conflict_id':'total_nitrogen','topic':'water_total_nitrogen_text_vs_table','status':'unresolved','human_record_id':'pilot:conflict_total_nitrogen',
   'sides':[span(parsed,4,'The total phosphorus and nitrogen','2.5 mg/L,'),cell(tables,1,'ditch_water','total_nitrogen'),cell(tables,1,'ditch_water','suspended_matter')],
   'note':'Keep both reported values; do not infer the true concentration.'}]
 for conflict in conflicts:
  conflict.update(uid=UID,doi=DOI,raw_sha256=RAW_SHA,parsed_sha256=parsedsha,tables_sha256=tablesha)
  if len(conflict['sides'])<2 or not all(anchor_exists(a) for a in conflict['sides']):raise ValueError('conflict_anchor_invalid')
 gold=[]
 for o in observations:
  tid=o['record_id'];field=tid.removeprefix('pilot:')
  if field in ('contradiction_yield','contradiction_as','conflict_total_nitrogen'):continue
  if field=='yield_table':
   for row in ('T1','T2','T3','CK'):
    a=cell(tables,3,row,'yield');gold.append({'reference_id':'yield_table_'+row,'field':'yield','candidate_type':'human_reviewed_table_transcription','value':a['text'],'unit':'kg/hm²','treatment':row,'anchors':[a],'human_record_id':tid,'evaluation_limit':'What the table reports; not resolution of conflicting text.'})
   continue
  tags=[]
  if field in ('yield_results','author_conclusion'):tags.append('yield')
  if field=='significance':tags.append('arsenic_significance')
  gold.append({'reference_id':field,'field':field,'candidate_type':'human_reviewed_source_observation','value':o['human_record']['value'],
               'anchors':o['new_anchors'],'human_record_id':tid,'conflict_ids':tags,
               'evaluation_limit':'Evaluate what the source reports; linked conflicts are not single truth values.' if tags else 'Human reference candidate; original text may be noncontiguous or use normalized notation.'})
 # New deterministic source finding, not falsely attributed to the reviewer.
 conductivity=span(parsed,4,'shaken at a soil-to-water ratio of 1:5','total salt con-\ntent [16].')
 gold.append({'reference_id':'soil_conductivity','field':'measurements','candidate_type':'assistant_located_not_yet_human_approved','value':conductivity['quote'],'anchors':[conductivity],
              'human_record_id':None,'evaluation_limit':'Additional candidate; may not be treated as a completed human reference.'})
 repobs=next(x for x in observations if x['record_id']=='pilot:sampling_replicates')
 gold.append({'reference_id':'replication_correspondence','field':'replication_correspondence','candidate_type':'human_note_uncertain','value':'uncertain',
              'anchors':[next(x for x in observations if x['record_id']=='pilot:replicates')['new_anchors'][0],repobs['new_anchors'][0]],
              'human_record_id':'pilot:sampling_replicates','evaluation_limit':'The user noted that the paper does not adequately explain the relationship; do not infer it.'})
 for candidate in gold:candidate.update(uid=UID,doi=DOI,raw_sha256=RAW_SHA,parsed_sha256=parsedsha,tables_sha256=tablesha)
 jlwrite(output/'human_observations.jsonl',observations);jlwrite(output/'gold_candidates.jsonl',gold);jlwrite(output/'source_conflicts.jsonl',conflicts)
 # Seven later accepts are bound to actual manifest candidate texts, not empty JSON values.
 accepted=read_jsonl(root/APPROVED);approved_by={x['uid']:x for x in accepted};olddec=read_jsonl(root/ORIGINAL)
 previous_accept={x['uid'] for x in olddec if x['type']=='abstract' and x['decision']=='accept'};new_accept={tasks[x['task_id']]['content']['uid'] for x in abstract if x['status']=='accept'}
 if len(new_accept)!=7 or set(approved_by)!=new_accept:raise ValueError('approved_abstract_coverage_changed')
 previous_manifest=json.loads((root/PRIOR).read_text());previous_tasks={(t.get('type'),t.get('uid')):t for t in previous_manifest['tasks']};abstract_details=[]
 for d in abstract:
  t=tasks[d['task_id']];a=approved_by[t['content']['uid']];text=t['content']['proposed_text'];prev=previous_tasks[('abstract',a['uid'])];prevdata=prev['data']
  if d['value'] or a['abstract']!=text or a['abstract_sha256']!=payload_hash(text.encode()) or d['reviewer']!=a['reviewer'] or d['reviewed_at']!=a['reviewed_at']:raise ValueError('approved_abstract_binding_mismatch')
  for f in [*a['source_file_bindings'],*prev['files']]:
   if digest(root/f['path'])!=f['sha256']:raise ValueError('approved_abstract_source_changed')
  snapshot_sha=prevdata.get('candidate_source_snapshot_sha256')
  if snapshot_sha and digest(root/'data/reports/literature_quality_20261028/springer/publisher_snapshot_from_prior_run.json')!=snapshot_sha:raise ValueError('publisher_snapshot_changed')
  abstract_details.append({'uid':a['uid'],'doi':a['doi'],'human_decision':'accept','reviewer':d['reviewer'],'reviewed_at':d['reviewed_at'],
      'returned_value_empty':not d['value'],'approved_text_sha256':a['abstract_sha256'],'approved_text_source':'user_supplied_note_then_manifest' if '这篇文章的摘要应该是：' in t['content']['previous_note'] else 'normalized_prior_candidate_then_manifest',
      'prior_candidate_source':prevdata['candidate_source'],'prior_candidate_text_sha256':payload_hash(prevdata['candidate_abstract'].encode()),
      'publisher_legacy_snapshot_sha256':snapshot_sha,'original_pdf_sha256':prevdata['raw_sha256'],'original_parsed_sha256':prevdata['parsed_sha256'],
      'prior_manifest_object_sha256':prev['object_sha256'],'current_manifest_object_sha256':t['object_sha256'],
      'previous_user_decision':t['content']['previous_decision'],'overlap_with_previous_accept':a['uid'] in previous_accept,
      'source_limitations':prevdata['candidate_limitations']})
 jlwrite(output/'abstract_acceptance_audit.jsonl',abstract_details)
 abstract_audit={'current_accepted':7,'previous_accepted':len(previous_accept),'overlap':len(new_accept&previous_accept),'distinct_uids_with_any_accept':len(new_accept|previous_accept),
                 'note':'Previous accepts concern prior candidate versions; this is not a claim that all distinct papers have a final normalized version.',
                 'uid_overlap':sorted(new_accept&previous_accept),'abstracts_sha256':digest(root/APPROVED),'result_values_empty':all(not x['value'] for x in abstract),
                 'candidate_text_recovered_from':'hash-bound review manifest'}
 jwrite(output/'abstract_acceptance_audit.json',abstract_audit)
 # Source-bound request. User references are deliberately separate from request.
 prompt=(root/PROMPT).read_bytes();schema=(root/SCHEMA).read_bytes();request=prepare_request(parsed,parsedsha,tables,prompt,schema)
 validate_bound_request(request,raw,parsedpath.read_bytes(),tablepath.read_bytes(),prompt,schema);jwrite(output/'mock_request.json',request)
 if 'gold_candidates' in json.dumps(request) or 'human_observations' in json.dumps(request):raise ValueError('reference_material_in_worker_input')
 # Deterministic mock responses, never a real model.
 sample=span(parsed,3,'Each treatment was repeated five times','five times.')
 a=cell(tables,3,'T1','yield');facts=[{'field':'replicates','category':'observation','value':sample['quote'],'anchors':[sample]},
  {'field':'yield','category':'observation','value':a['text'],'treatment':'T1','unit':'kg/hm²','anchors':[a]}]
 mock={k:request[k] for k in ('protocol_version','request_sha256','uid','doi','document_sha256','parsed_sha256','prompt_sha256','schema_sha256')}
 mock_conflicts=[{'topic':c['conflict_id'],'status':'unresolved','sides':[{'claim':a.get('quote',a.get('text')),'anchor':a} for a in c['sides']]} for c in conflicts]
 mock.update(model={'identifier':'offline-synthetic-fixture','simulated':True,'call_count':0,'input_tokens':None,'output_tokens':None,'actual_cost':None,'currency':None},facts=facts,conflicts=mock_conflicts)
 validate_response(mock,request,schema);merged=merge_responses([mock,mock],request,schema);differences=compare_fields(merged,gold)
 jwrite(output/'mock_response.json',mock);jwrite(output/'mock_merge.json',merged);jwrite(output/'mock_reference_comparison.json',differences)
 # Real-document mock keeps the three conflict records separate; the automated
 # synthetic unit tests validate conflict schema and both source anchors.
 metadata={'created_at':utc_now(),'uid':UID,'doi':DOI,'raw_sha256':RAW_SHA,'old_parsed_sha256':check['parsed_sha256'],'new_parsed_sha256':parsedsha,
           'tables_sha256':tablesha,'parser_code_sha256':digest(root/'scripts/pilot/first_pdf.py'),'review_sha256':digest(reviewpath),'review_manifest_sha256':digest(webpath),
           'old_ui_evidence_all_exactly_replayed_count':raw_replay_count,'old_ui_evidence_with_table_remap_count':remap_count,'review_items':len(observations),
           'gold_candidate_count':len(gold),'conflict_count':len(conflicts),'model_calls':0,'model_cost':'not_applicable_no_call','formal_admission_new_parser':'not_granted',
           'excluded_figure_forms':parsed['excluded_figure_forms'],'prompt_sha256':digest(root/PROMPT),'schema_sha256':digest(root/SCHEMA)}
 jwrite(output/'run_manifest.json',metadata)
 return metadata

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--download',required=True);a=p.parse_args();print(json.dumps(build_output(a.output,a.download),ensure_ascii=False,indent=2))
