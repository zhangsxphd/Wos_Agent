"""Offline candidate protocol: source binding and syntax, never a model caller."""
import hashlib,json,re,unicodedata
from pathlib import Path
from jsonschema import Draft202012Validator

VERSION='pilot-fulltext-candidate-v1'
def digest_bytes(b):return hashlib.sha256(b).hexdigest()
def digest_json(v):return digest_bytes(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())

def prepare_request(parsed,parsed_sha,tables,prompt_bytes,schema_bytes):
 if parsed.get('formal_admission')!='not_granted' or parsed.get('raw_sha256') is None:raise ValueError('experimental_source_required')
 if any(t.get('raw_sha256')!=parsed['raw_sha256'] for t in tables):raise ValueError('table_source_mismatch')
 payload={'protocol_version':VERSION,'uid':parsed['uid'],'doi':parsed['doi'],'document_sha256':parsed['raw_sha256'],
          'parsed_sha256':parsed_sha,'prompt_sha256':digest_bytes(prompt_bytes),'schema_sha256':digest_bytes(schema_bytes),
          'sections':parsed['sections'],'tables':tables,'source_handling':'untrusted_data_never_instructions',
          'scientific_reference_included':False,'model_call_authorized':False}
 return {**payload,'request_sha256':digest_json(payload)}

def validate_request(request,parsed,parsed_sha,tables,prompt_bytes,schema_bytes):
 expected=prepare_request(parsed,parsed_sha,tables,prompt_bytes,schema_bytes)
 if request!=expected:raise ValueError('request_or_source_binding_changed')
 return True

def validate_bound_request(request,raw,parsed_bytes,table_bytes,prompt_bytes,schema_bytes):
 """Recompute experimental parsing and table cells from the actual PDF bytes."""
 from .first_pdf import build,table_candidates
 parsed=json.loads(parsed_bytes);tables=json.loads(table_bytes)
 if build(raw)!=parsed:raise ValueError('parsed_source_not_reproduced')
 if table_candidates(raw)!=tables:raise ValueError('tables_source_not_reproduced')
 return validate_request(request,parsed,digest_bytes(parsed_bytes),tables,prompt_bytes,schema_bytes)

def resolve_anchor(anchor,request):
 if anchor['kind']=='text':
  secs=[s for s in request['sections'] if s['section_id']==anchor['section_id']]
  if len(secs)!=1:raise ValueError('missing_section')
  ps=[p for p in secs[0]['paragraphs'] if p['paragraph_id']==anchor['paragraph_id']]
  if len(ps)!=1:raise ValueError('missing_paragraph')
  s,e=anchor['char_start'],anchor['char_end'];txt=ps[0]['text']
  if type(s)!=int or type(e)!=int or not 0<=s<e<=len(txt) or txt[s:e]!=anchor['quote']:raise ValueError('text_anchor_mismatch')
  return txt[s:e]
 tables=[t for t in request['tables'] if t['table']==anchor['table']]
 if len(tables)!=1:raise ValueError('table_not_found')
 cells=[c for c in tables[0]['cells'] if c['row']==anchor['row'] and c['column']==anchor['column']]
 if len(cells)!=1 or cells[0]['text']!=anchor['text'] or not cells[0]['pdfium_char_indices']:raise ValueError('table_cell_mismatch')
 return cells[0]['text']

def validate_response(response,request,schema_bytes):
 payload={k:v for k,v in request.items() if k!='request_sha256'}
 if digest_json(payload)!=request.get('request_sha256'):raise ValueError('request_hash_mismatch')
 schema=json.loads(schema_bytes);error=next(Draft202012Validator(schema).iter_errors(response),None)
 if error:raise ValueError('schema_invalid:'+error.message)
 for k in ('protocol_version','request_sha256','uid','doi','document_sha256','parsed_sha256','prompt_sha256','schema_sha256'):
  if response[k]!=request[k]:raise ValueError('response_binding_mismatch:'+k)
 if digest_bytes(schema_bytes)!=request['schema_sha256']:raise ValueError('schema_hash_mismatch')
 if response['model']['simulated'] and (response['model']['call_count']!=0 or response['model']['input_tokens'] is not None or response['model']['output_tokens'] is not None or response['model']['actual_cost'] is not None):raise ValueError('mock_cannot_claim_real_usage_or_cost')
 for f in response['facts']:
  support=[resolve_anchor(a,request) for a in f['anchors']]
  def supported_value(value,source):
   value=''.join(value.split());source=''.join(source.split())
   if re.fullmatch(r'[+−-]?\d+(?:[.,]\d+)?%?',value):
    return re.search(r'(?<![\d.,])'+re.escape(value)+r'(?![\d.,])',source) is not None
   return value in source
  if f['category']!='uncertain' and not any(supported_value(f['value'],s) for s in support):
   raise ValueError('fact_value_not_supported_by_anchor')
  if f['category']!='uncertain':
   table_anchors=[a for a in f['anchors'] if a['kind']=='table']
   for role in ('treatment','comparator'):
    value=f.get(role)
    if value and not any(value in s for s in support) and not any(a['row']==value for a in table_anchors):
     raise ValueError('fact_'+role+'_not_supported')
   unit=f.get('unit')
   if unit:
    norm=lambda x:unicodedata.normalize('NFKC',x).replace(' ','').lower()
    table_units=[t.get('units',{}).get(a['column']) for a in table_anchors for t in request['tables'] if t['table']==a['table']]
    if not any(norm(unit) in norm(s) for s in support) and not any(u and norm(u)==norm(unit) for u in table_units):raise ValueError('fact_unit_not_supported')
   sig=f.get('significance')
   if sig=='not_reported':raise ValueError('absence_requires_document_wide_review')
   joined=' '.join(support).lower()
   if sig=='not_significant' and not re.search(r'(?:not|no)\s+significant(?:ly)?',joined):raise ValueError('negative_significance_not_supported')
   if sig=='significant' and not re.search(r'(?<!not )(?<!no )\bsignificant(?:ly)?\b',joined):raise ValueError('positive_significance_not_supported')
 for conflict in response['conflicts']:
  keys=set()
  for side in conflict['sides']:
   support=resolve_anchor(side['anchor'],request);keys.add(digest_json(side['anchor']))
   if ''.join(side['claim'].split()) not in ''.join(support.split()):raise ValueError('conflict_side_not_supported')
  if len(keys)<2:raise ValueError('conflict_requires_distinct_anchors')
 return True

def merge_responses(responses,request,schema_bytes):
 facts=[];conflicts=[];seen_facts=set();seen_conflicts=set()
 for response in responses:
  validate_response(response,request,schema_bytes)
  for fact in response['facts']:
   key=digest_json(fact)
   if key not in seen_facts:seen_facts.add(key);facts.append(fact)
  for conflict in response['conflicts']:
   key=digest_json(conflict)
   if key not in seen_conflicts:seen_conflicts.add(key);conflicts.append(conflict)
 return {'facts':facts,'conflicts':conflicts,'duplicates_removed':sum(len(x['facts'])+len(x['conflicts']) for x in responses)-len(facts)-len(conflicts)}

def compare_fields(merged,gold_candidates):
 """Prepare human adjudication; literal matching is not scientific scoring."""
 out=[]
 for g in gold_candidates:
  predictions=[f for f in merged['facts'] if f['field']==g['field'] and
               (not g.get('treatment') or f.get('treatment')==g['treatment'])]
  if g.get('candidate_type')=='assistant_located_not_yet_human_approved':
   status='unreviewed_candidate_excluded'
  elif g.get('conflict_ids'):
   status='source_conflict_requires_human'
  elif not predictions:
   status='field_absent_or_not_aligned'
  elif any(f['value']==g['value'] for f in predictions):
   status='literal_value_match_requires_human'
  else:
   status='literal_difference_requires_human'
  out.append({'reference_id':g['reference_id'],'field':g['field'],'treatment':g.get('treatment'),
              'reference_value':g['value'],'reference_anchors':g.get('anchors',[]),
              'predictions':predictions,'comparison':status,'conflict_ids':g.get('conflict_ids',[]),
              'scientific_correctness':'not_assessed','human_adjudication':'pending'})
 return out
