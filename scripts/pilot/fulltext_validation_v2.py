"""Conservative table-row and arithmetic screens for model candidates.

Input is the immutable model response plus its bound parsed paper. Results are
separate from, and do not modify, model outputs or prior audit snapshots.
"""
import hashlib,json,math,re
from pathlib import Path
from scripts.pipeline_utils import ROOT

BASE=ROOT/'data/pilot/v08_codex_fulltext_20261030_v2'
OUTPUT=ROOT/'data/pilot/v08_candidate_validation_20261030_v1'
ID=r'[A-Za-z][A-Za-z0-9+_-]{0,15}'
NUMBER=r'[-+]?\d+(?:\.\d+)?'
PAIR=re.compile(r'^\s*('+NUMBER+r')(?:\s*[±]\s*'+NUMBER+r')?\s*(?:vs\.?|versus|compared\s+(?:with|to))\s*('+NUMBER+r')',re.I)
CALC=re.compile(r'\(?\s*('+NUMBER+r')\s*[-−]\s*('+NUMBER+r')\s*\)?\s*/\s*('+NUMBER+r')\s*[×x*]\s*100\s*=\s*('+NUMBER+r')\s*%',re.I)
LETTERS=re.compile(r'^\s*([a-z])\s*(?:vs\.?|versus|/)\s*([a-z])\s*$',re.I)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def token_on_line(line,token):
 return bool(re.search(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])',line,re.I))

def inspect_result(result,paras):
 checks=[];issues=[]
 pair=PAIR.match(result['value'])
 if pair and re.fullmatch(ID,result['treatment'].strip()) and re.fullmatch(ID,result['comparator'].strip()):
  pages={e['page'] for e in result['evidence'] if e.get('table_figure')}
  if pages:
   evidence=[]
   for label,number in zip((result['treatment'].strip(),result['comparator'].strip()),pair.groups()):
    found=[]
    for p in paras:
     if p['page'] not in pages:continue
     for index,line in enumerate(p['text'].splitlines(),1):
      if token_on_line(line,label) and token_on_line(line,number):
       found.append({'page':p['page'],'paragraph_id':p['paragraph_id'],'line':index,'excerpt':line[:500]})
    evidence.append({'group':label,'value':number,'matched_lines':found[:5],'total_matched_lines':len(found)})
   status='both_values_share_parsed_lines_with_groups' if all(x['matched_lines'] for x in evidence) else 'row_line_match_unconfirmed'
   checks.append({'check':'group_value_row_line','status':status,'evidence':evidence,'limitation':'Co-occurrence on a parsed line does not verify table column, season, visual layout, uncertainty, or statistical comparison.'})
   if status=='row_line_match_unconfirmed':issues.append({'severity':'medium','type':'group_value_row_line_unconfirmed','detail':evidence})
 for match in CALC.finditer(result['value']+' '+result['limitations']):
  a,b,denom,reported=map(float,match.groups())
  if denom==0:
   issues.append({'severity':'high','type':'zero_denominator','detail':match.group(0)});continue
  calc=(a-b)/denom*100
  tolerance=max(0.05,abs(reported)*0.001)
  consistent=math.isclose(calc,reported,rel_tol=0,abs_tol=tolerance)
  checks.append({'check':'explicit_percentage_formula','expression':match.group(0),'calculated':calc,'reported':reported,'tolerance_percentage_points':tolerance,'display_value_consistent':consistent,'limitation':'This checks only the displayed calculation, not the source of operands or author intent.'})
  if not consistent:issues.append({'severity':'high','type':'explicit_arithmetic_mismatch','detail':match.group(0)})
 letters=LETTERS.fullmatch(result.get('significance_letters',''))
 if letters:
  left,right=(x.lower() for x in letters.groups());sig=result.get('significance','').lower()
  if left==right and re.search(r'\bsignificant\b',sig) and not re.search(r'\b(?:not|no|non|insignificant)\b',sig):
   issues.append({'severity':'high','type':'possible_significance_letter_conflict','detail':'The same simple letter is paired with a claim of significance; verify the actual caption, test, grouping, and comparison.'})
 return {'checks':checks,'issues':issues}

def build():
 queue=json.loads((BASE/'queue.json').read_text());OUTPUT.mkdir(parents=True,exist_ok=True);summary=[]
 for r in queue:
  status=BASE/'papers'/hashlib.sha256(r['doi'].encode()).hexdigest()[:16]/'status.json'
  if not status.exists():continue
  s=json.loads(status.read_text())
  if s['state']!='model_completed':continue
  attempt=Path(s['attempt']);manifest=json.loads((attempt/'manifest.json').read_text());parsed=ROOT/r['parsed_file'];raw=ROOT/r['raw_file']
  if sha(parsed)!=manifest['parsed_sha256'] or sha(raw)!=manifest['raw_sha256']:raise RuntimeError('source_sha_mismatch:'+r['doi'])
  response=json.loads((attempt/'response.json').read_text());doc=json.loads(parsed.read_text());paras=[{'page':int(sec['section_id'].split('-')[-1]),**p} for sec in doc['sections'] for p in sec['paragraphs']]
  checked=[{'index':i,**inspect_result(x,paras)} for i,x in enumerate(response['results'])]
  result={'doi':r['doi'],'candidate_sha256':sha(attempt/'response.json'),'raw_sha256':sha(raw),'parsed_sha256':sha(parsed),'candidate_only':True,'checks':checked,'limitations':'No output value is automatically repaired. A line match is not a visual cell/column or scientific validity check; table and methods review remain necessary.'}
  fn=OUTPUT/(hashlib.sha256(r['doi'].encode()).hexdigest()[:16]+'.json');fn.write_text(json.dumps(result,ensure_ascii=False,indent=2))
  summary.append({'doi':r['doi'],'results':len(checked),'pair_row_checks':sum(c['check']=='group_value_row_line' for x in checked for c in x['checks']),'pair_row_matches':sum(c.get('status')=='both_values_share_parsed_lines_with_groups' for x in checked for c in x['checks']),'explicit_calculations':sum(c['check']=='explicit_percentage_formula' for x in checked for c in x['checks']),'issues':sum(len(x['issues']) for x in checked),'report':str(fn.relative_to(ROOT))})
 (OUTPUT/'summary.json').write_text(json.dumps({'papers':len(summary),'items':summary},ensure_ascii=False,indent=2))
 return summary
if __name__=='__main__':print(json.dumps({'papers':len(build())}))
