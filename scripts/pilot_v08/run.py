"""Preflight a three-paper pilot, and execute only with explicit CLI approval.

The model sees no reference answers. A whole paper is one request, with visible
page images plus the historically admitted text. Research outputs stay in data/.
"""
import argparse, base64, hashlib, io, json, math, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
import jsonschema
from dotenv import dotenv_values
from scripts.pipeline_utils import ROOT, read_jsonl
from scripts.review.packet import CAN, INV
from scripts.fulltext.admission import inspect_fulltext
from scripts.evidence.final_evaluation import verify_frozen

ROOT=Path(ROOT)
SELECT=ROOT/'data/pilot/v07_pilot_round1/selected_papers.json'
GOLD=ROOT/'data/pilot/first_gold_offline_20261028_v11'
PROMPT=Path(__file__).with_name('prompt.txt')
MODEL='gpt-5.4-2026-03-05'
FIRST='10.3390/w16182598'
MASK={FIRST:{3,5,6}}
MAX_OUT=20000
PRICE_IN=2.5; PRICE_CACHED=.25; PRICE_OUT=15.0
FIELDS=['study_object','design','treatments','control','replicates','sampling_replicates','measurements','numerical_results','significance','author_conclusion','source_conflict']

def sha(b):return hashlib.sha256(b).hexdigest()
def compact(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def write_json(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
nullable={'type':['string','null']}
ANCHOR=obj({'page':{'type':'integer'},'quote':{'type':'string'},'location':{'type':'string'},'table':nullable,'row':nullable,'column':nullable,'source':{'type':'string','enum':['text','visual']}})
FACT=obj({'field':{'type':'string','enum':FIELDS},'status':{'type':'string','enum':['reported','uncertain','not_reported','not_applicable']},'value':{'type':'string'},'treatment':nullable,'comparator':nullable,'unit':nullable,'significance':{'type':['string','null'],'enum':['significant','not_significant','not_reported','uncertain',None]},'category':{'type':'string','enum':['observation','author_interpretation','uncertain','conflict']},'evidence':{'type':'array','items':ANCHOR}})
PAGE_COVERAGE=obj({'page':{'type':'integer'},'status':{'type':'string','enum':['examined','uncertain']}})
SCHEMA=obj({'facts':{'type':'array','items':FACT},'limitations':{'type':'array','items':{'type':'string'}},'page_coverage':{'type':'array','items':PAGE_COVERAGE}})
jsonschema.Draft202012Validator.check_schema(SCHEMA)

def preflight(output):
 import pypdfium2 as pdfium
 selected=json.loads(SELECT.read_text()); records={r['uid']:r for r in read_jsonl(ROOT/CAN)}; manifests={r['uid']:r for r in read_jsonl(ROOT/INV)}
 if len(selected)!=3 or len({p['doi'] for p in selected})!=3:raise ValueError('unexpected_paper_selection')
 prior=json.loads((GOLD/'artifact_manifest.json').read_text())
 for f in ('gold_candidates.jsonl','source_conflicts.jsonl','human_observations.jsonl','parsed_first_pdf_v1.json','tables_v1.json'):
  if sha((GOLD/f).read_bytes())!=prior['files'][f]:raise ValueError('reference_artifact_changed:'+f)
 observations=read_jsonl(GOLD/'human_observations.jsonl')
 if len(observations)!=14 or any(o['doi']!=FIRST for o in observations):raise ValueError('human_reference_mismatch')
 frozen=verify_frozen()
 output.mkdir(parents=True,exist_ok=False)
 ordered=sorted(selected,key=lambda p:p['doi']!=FIRST)
 entries=[]
 for p in ordered:
  parsed,gate=inspect_fulltext(records[p['uid']],manifests[p['uid']],ROOT)
  if gate['raw_sha256']!=p['raw_sha256'] or gate['parsed_sha256']!=p['parsed_sha256']:raise ValueError('source_changed')
  raw=(ROOT/p['raw_file']).read_bytes();doc=pdfium.PdfDocument(raw)
  if len(doc)!=parsed['pages']:raise ValueError('page_count_mismatch')
  paperdir=output/gate['raw_sha256'][:12];paperdir.mkdir()
  pages=[];imgs=[]
  for i,s in enumerate(parsed['sections'],1):
   masked=i in MASK.get(p['doi'],set())
   texts=[] if masked else s['paragraphs']
   pages.append({'page':i,'text_omitted_due_to_known_hidden_duplicate':masked,'paragraphs':texts})
   bitmap=doc[i-1].render(scale=2).to_pil().convert('RGB');buf=io.BytesIO();bitmap.save(buf,format='JPEG',quality=90,optimize=True)
   path=paperdir/('page-%02d.jpg'%i);path.write_bytes(buf.getvalue())
   w,h=bitmap.size
   # Conservative bound for high detail: no image exceeds 2500 patches.
   patches=min(2500,math.ceil(w/32)*math.ceil(h/32))
   imgs.append({'page':i,'path':str(path.relative_to(output)),'sha256':sha(buf.getvalue()),'width':w,'height':h,'image_tokens_upper_bound':math.ceil(patches*1.2)})
  # Actual exact images and text are bound; no human observations or known contradictions are included.
  payload={'uid':p['uid'],'doi':p['doi'],'raw_sha256':gate['raw_sha256'],'parsed_sha256':gate['parsed_sha256'],'pages':pages,'images':imgs,'prompt_sha256':sha(PROMPT.read_bytes()),'schema_sha256':sha(compact(SCHEMA)),'runner_sha256':sha(Path(__file__).read_bytes()),'model':MODEL,'max_output_tokens':MAX_OUT,'reasoning_effort':'medium','image_detail':'high'}
  request_sha=sha(compact(payload)); entry={'title':p['title'],'raw_file':p['raw_file'],'parsed_file':p['parsed_file'],'gate':gate,'payload':payload,'request_sha256':request_sha}
  write_json(paperdir/'request.json',entry);entries.append(entry)
 text_bytes=sum(len(compact(e['payload']['pages'])) for e in entries)
 img_tokens=sum(i['image_tokens_upper_bound'] for e in entries for i in e['payload']['images'])
 # Byte count is deliberately conservative rather than claiming exact tokenizer output.
 text_est=math.ceil(text_bytes/3)+5000*3
 text_bound=text_bytes+15000*3
 estimated=(text_est+img_tokens)*PRICE_IN/1e6+3*10000*PRICE_OUT/1e6
 bound=(text_bound+img_tokens)*PRICE_IN/1e6+3*MAX_OUT*PRICE_OUT/1e6
 report={'created_utc':datetime.now(timezone.utc).isoformat(),'model':MODEL,'expected_calls':3,'max_calls_without_new_approval':3,'text_bytes':text_bytes,'estimated_text_tokens_approx':text_est,'image_tokens_conservative_bound':img_tokens,'estimated_output_tokens_assumption':30000,'estimated_usd':round(estimated,3),'conservative_budget_usd':round(bound,3),'pricing_per_million_usd':{'input':PRICE_IN,'cached_input':PRICE_CACHED,'output':PRICE_OUT},'assumptions':'Text estimate uses 3 UTF-8 bytes/token plus 5000/request; conservative allowance uses 1 byte/token plus 15000/request; 10000 output tokens/paper estimate; maximum output 20000/paper. Actual billing may differ; no automatic retry. Image cost uses up to 2500 patches times 1.2 per page. No supplementary files.','source':'https://developers.openai.com/api/docs/models/gpt-5.4','image_pricing_source':'https://developers.openai.com/api/docs/guides/images-vision','papers':[{'doi':e['payload']['doi'],'title':e['title'],'pages':len(e['payload']['pages']),'technical_admission':'pass','request_sha256':e['request_sha256'],'masked_pages':sorted(MASK.get(e['payload']['doi'],set()))} for e in entries],'model_calls':0,'api_key_present':bool(os.getenv('OPENAI_API_KEY') or dotenv_values(ROOT/'.env').get('OPENAI_API_KEY')),'frozen_components_verified':len(frozen),'reference_records_verified':len(observations),'gold_not_in_model_input':True,'supplementary_materials_not_included':[p['doi'] for p in selected if p['doi']!=FIRST]}
 write_json(output/'preflight.json',report)
 return report

def check_request(entry,folder):
 p=entry['payload']
 if sha(compact(p))!=entry['request_sha256'] or sha(PROMPT.read_bytes())!=p['prompt_sha256'] or sha(compact(SCHEMA))!=p['schema_sha256'] or sha(Path(__file__).read_bytes())!=p['runner_sha256']:raise ValueError('request_changed')
 records={r['uid']:r for r in read_jsonl(ROOT/CAN)};manifests={r['uid']:r for r in read_jsonl(ROOT/INV)}
 fresh,gate=inspect_fulltext(records[p['uid']],manifests[p['uid']],ROOT)
 if gate['raw_sha256']!=p['raw_sha256'] or gate['parsed_sha256']!=p['parsed_sha256']:raise ValueError('admission_changed')
 for page,s in zip(p['pages'],fresh['sections']):
  expected=[] if page['page'] in MASK.get(p['doi'],set()) else s['paragraphs']
  if page['paragraphs']!=expected:raise ValueError('text_source_changed')
 for image in p['images']:
  if sha((folder/image['path']).read_bytes())!=image['sha256']:raise ValueError('image_changed')
 return True

def body(entry,folder):
 p=entry['payload'];content=[]
 for page,img in zip(p['pages'],p['images']):
  content.append({'type':'input_text','text':json.dumps(page,ensure_ascii=False)})
  content.append({'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode((folder/img['path']).read_bytes()).decode(),'detail':'high'})
 return {'model':MODEL,'store':False,'reasoning':{'effort':'medium'},'max_output_tokens':MAX_OUT,'input':[{'role':'developer','content':[{'type':'input_text','text':PROMPT.read_text()}]},{'role':'user','content':content}],'text':{'format':{'type':'json_schema','name':'paper_evidence','strict':True,'schema':SCHEMA}}}

def validate_prediction(data,entry):
 jsonschema.validate(data,SCHEMA);pages={p['page']:p for p in entry['payload']['pages']}
 coverage=[x['page'] for x in data['page_coverage']]
 if len(coverage)!=len(set(coverage)) or set(coverage)!=set(pages):raise ValueError('incomplete_or_duplicate_page_coverage')
 for f in data['facts']:
  if f['status']=='reported' and not f['evidence']:raise ValueError('reported_without_evidence')
  if f['field']=='source_conflict' and len(f['evidence'])<2:raise ValueError('conflict_requires_two_anchors')
  for a in f['evidence']:
   if a['page'] not in pages or not a['quote'].strip():raise ValueError('invalid_evidence_page_or_quote')
   text='\n'.join(x['text'] for x in pages[a['page']]['paragraphs'])
   if a['source']=='text' and a['quote'] not in text:raise ValueError('text_evidence_not_exact')
 return True

def normalize(s):return re.sub(r'\s+',' ',str(s).casefold()).strip()
def compare(data):
 # Only a candidate field match. Scientific semantics require human judgment.
 mapping={'study_object':'study_object','design':'design','treatments':'treatments','control':'control','replicates':'replicates','sampling_replicates':'sampling_replicates','measures':'measurements','yield_results':'numerical_results','yield':'numerical_results','significance':'significance','author_conclusion':'author_conclusion'}
 comparisons=[]
 for ref in read_jsonl(GOLD/'gold_candidates.jsonl'):
  if ref['candidate_type']=='assistant_located_not_yet_human_approved':continue
  field=mapping.get(ref['field'],ref['field']); candidates=[f for f in data['facts'] if f['field']==field and (not ref.get('treatment') or normalize(f.get('treatment'))==normalize(ref['treatment']))]
  same=any(normalize(ref['value'])==normalize(f['value']) for f in candidates)
  comparisons.append({'reference_id':ref['reference_id'],'reference':ref,'model_candidates':candidates,'automatic_flag':'literal_match_pending_semantic_review' if same else ('field_candidates_need_review' if candidates else 'possible_omission'),'adjudication':'pending'})
 return {'comparisons':comparisons,'known_source_conflicts':read_jsonl(GOLD/'source_conflicts.jsonl'),'warning':'Field association and literal equality do not establish scientific correctness; no accuracy score.'}

def execute(folder,approved):
 if not approved:raise ValueError('explicit_approval_flag_required')
 import requests
 summary=json.loads((folder/'preflight.json').read_text())
 key=os.getenv('OPENAI_API_KEY') or dotenv_values(ROOT/'.env').get('OPENAI_API_KEY')
 if not key:raise ValueError('OPENAI_API_KEY_missing')
 if summary['expected_calls']!=3:raise ValueError('unexpected_call_count')
 requests_to_run=[]
 for p in summary['papers']:
  candidates=list(folder.glob('*/request.json')); found=[json.loads(q.read_text()) for q in candidates if json.loads(q.read_text())['payload']['doi']==p['doi']]
  if len(found)!=1 or found[0]['request_sha256']!=p['request_sha256']:raise ValueError('request_set_changed')
  check_request(found[0],folder);requests_to_run.append(found[0])
 # Reserve each paper before sending: no blind retries after uncertain transport failures.
 for entry in requests_to_run:
  request_id=entry['request_sha256'];result_dir=folder/'results'/request_id;result_dir.mkdir(parents=True,exist_ok=True)
  marker=result_dir/'attempt.json'
  if marker.exists():
   prediction_path=result_dir/'prediction.json'
   if not prediction_path.exists():raise ValueError('previous_attempt_outcome_unresolved_no_retry')
   validate_prediction(json.loads(prediction_path.read_text()),entry)
   print('Existing validated result, skipping:',entry['payload']['doi']);continue
  with marker.open('x') as out:json.dump({'state':'dispatching','started_utc':datetime.now(timezone.utc).isoformat(),'request_sha256':request_id},out)
  try:
   response=requests.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json=body(entry,folder),timeout=(30,900),allow_redirects=False)
   info={'http_status':response.status_code,'request_id':response.headers.get('x-request-id'),'received_utc':datetime.now(timezone.utc).isoformat()}
   if response.status_code!=200:
    info['state']='http_error';write_json(result_dir/'status.json',info);raise RuntimeError('provider_http_'+str(response.status_code))
   envelope=response.json();write_json(result_dir/'provider_response.json',envelope)
   info.update(state=envelope.get('status'),model=envelope.get('model'),response_id=envelope.get('id'),usage=envelope.get('usage'))
   usage=info['usage'] or {};cached=(usage.get('input_tokens_details') or {}).get('cached_tokens',0)
   if all(isinstance(usage.get(k),int) for k in ('input_tokens','output_tokens')):
    info['usage_based_cost_estimate_usd']=((usage['input_tokens']-cached)*PRICE_IN+cached*PRICE_CACHED+usage['output_tokens']*PRICE_OUT)/1e6
   info['actual_invoice_usd']=None;write_json(result_dir/'status.json',info)
   if info['state']!='completed':raise ValueError('provider_response_incomplete')
   texts=[c['text'] for item in envelope.get('output',[]) for c in item.get('content',[]) if c.get('type')=='output_text']
   if len(texts)!=1:raise ValueError('missing_or_multiple_output_text')
   prediction=json.loads(texts[0]);validate_prediction(prediction,entry);write_json(result_dir/'prediction.json',prediction)
   if entry['payload']['doi']==FIRST:write_json(result_dir/'comparison_pending_adjudication.json',compare(prediction))
   print('Saved candidate:',entry['payload']['doi'])
  except Exception as exc:
   write_json(result_dir/'error.json',{'error_type':type(exc).__name__,'message':str(exc),'automatic_retry':False});raise

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','run']);parser.add_argument('--folder',required=True);parser.add_argument('--approved',action='store_true');args=parser.parse_args()
 try:
  if args.action=='prepare':print(json.dumps(preflight(Path(args.folder)),ensure_ascii=False,indent=2))
  else:execute(Path(args.folder),args.approved)
 except Exception as e:print(type(e).__name__+': '+str(e),file=sys.stderr);sys.exit(1)
