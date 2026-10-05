"""Cluster unresolved Dev40 A/B/C decisions into a compact human-review packet."""
from __future__ import annotations
import argparse,json
from collections import OrderedDict,defaultdict
from pathlib import Path
from scripts.evidence.validate_v07_gold_annotator import read_jsonl

# Stable, evidence-based clusters. Each ID remains individually traceable in the packet.
GROUPS=OrderedDict([
 ('R01 HRFS role ambiguity',['D001']),
 ('R02 Combined nutrient-productivity routing',['D009','D014','D088']),
 ('R03 Harvested quality versus yield/other',['D011','D013','D083','D085']),
 ('R04 Grain biochemical quality category',['D084','D086']),
 ('R05 Review attribution and scope',['D021','D026','D027','D063','D064','D065','D066']),
 ('R06 Mixed or broad soil-property measures',['D030','D031']),
 ('R07 Unspecified 27.80% outcome',['D035']),
 ('R08 Recommendation versus applied treatment',['D037','D078']),
 ('R09 Zn measurement compartment',['D040','D041','D042']),
 ('R10 Floodwater EC category',['D049','D050']),
 ('R11 Cellular damage measurement support',['D060']),
 ('R12 Generic limitation versus study limitation',['D067']),
 ('R13 DOM optical index category',['D101','D102','D104','D105']),
 ('R14 Genotype as treatment or study material',['D108']),
 ('R15 Conidia characterization versus microbial measure',['D126']),
 ('R16 Straw decomposition category',['D128']),
 ('R17 Amendment material-property categories',['D139','D140']),
 ('R18 Mixed microbial/nitrogen compound measures',['D161','D163']),
 ('R19 Experimental-scale explicit support',['SCALE_VALIDATION']),
])

def item_value(item): return item.get('claim') if item.get('kind')=='finding' else item.get('value')
def build(results,comparison,candidate,output):
 items={x['id']:x for x in json.loads((results/'adjudicator_C/disagreements.json').read_text())['items']}
 decisions={x['id']:x for x in read_jsonl(results/'adjudicator_C/adjudications.jsonl')}
 comp=json.loads((results/'ab_comparison.json').read_text())
 a={x['uid']:x for x in read_jsonl(results/'annotator_A/validated.jsonl')};b={x['uid']:x for x in read_jsonl(results/'annotator_B/validated.jsonl')}
 gold={x['uid']:x for x in read_jsonl(candidate/'candidate_gold.jsonl')}
 manifest=json.loads((candidate/'candidate_manifest.json').read_text())
 unresolved={x for x,d in decisions.items() if d['decision']=='NEEDS_HUMAN_REVIEW'}
 groups=[]
 for label,ids in GROUPS.items():
  if label.startswith('R19'):
   members=[]
   for row in manifest['scale_validator_review']:
    members.append({'uid':row['uid'],'field':'/evidence/study_system/experimental_scale','kind':'experimental_scale','a_value':row['provisional_value'],'b_value':row['provisional_value'],'context':'The selected scale assertion did not pass the frozen validator; candidate provisionally leaves it unknown.'})
  else:
   ids=[x for x in ids if x in unresolved]
   members=[]
   for ident in ids:
    item=items[ident];uid=item['uid']; same=[x for x in comp['disagreements'] if x['uid']==uid and x['field']==item['field'] and x['kind']==item['kind']]
    av=[item_value(x) for x in same if x['direction']=='A_only']
    bv=[item_value(x) for x in same if x['direction']=='B_only']
    members.append({'id':ident,'uid':uid,'field':item['field'],'kind':item['kind'],'a_value':av,'b_value':bv,
                    'context':item.get('context','')[:440],'contract_rule':decisions[ident].get('contract_rule'),
                    'c_reason':decisions[ident].get('rationale')})
  if members:groups.append((label,members))
 lines=['# v0.7 Dev40 Gold Human Review','',
        'Status: `development_gold_candidate`; not human-approved. These groups contain C-unresolved A/B disagreements plus validator-rejected scale assertions. Candidate scale is provisionally `unknown` where the frozen EvidenceValidator cannot verify explicit support. No extraction prompt, model prediction, future holdout abstract, or future holdout Gold was used.','',
        f'Human decision groups: {len(groups)} (target ≤25). C-unresolved disagreement items: {len(unresolved)}. Scale grounding cases: {len(manifest["scale_validator_review"])}.','']
 for i,(label,members) in enumerate(groups,1):
  uids=sorted({m['uid'] for m in members})
  fields=sorted({m.get('field','/evidence/study_system/experimental_scale') for m in members})
  lines += [f'## #{i} {label}','',f'- Affected UIDs ({len(uids)}): '+', '.join(uids),f'- Field: '+('; '.join(fields)),f'- Cases: {len(members)}']
  for m in members[:4]:
   uid=m['uid']; lines += ['',f'- Case {m.get("id",uid)} (`{uid}`)']
   lines += [f'- Abstract context: {m.get("context","").rstrip()}',f'- A: {json.dumps(m.get("a_value"),ensure_ascii=False)}',f'- B: {json.dumps(m.get("b_value"),ensure_ascii=False)}']
   if m.get('c_reason'):lines.append(f'  - C: NEEDS_HUMAN_REVIEW — {m["c_reason"]}')
   if m.get('contract_rule'):lines.append(f'  - Contract: {m["contract_rule"]}')
  if len(members)>4: lines.append(f'- Additional cases in this group: {len(members)-4}; see the linked C item IDs in the workflow manifest/artifacts.')
  lines += ['- Recommended action: NEEDS_HUMAN_REVIEW','']
 Path(output).write_text('\n'.join(lines),encoding='utf-8')
 return {'groups':len(groups),'unresolved_items':len(unresolved),'scale_cases':len(manifest['scale_validator_review']),'uids':sum(len({m['uid'] for m in ms}) for _,ms in groups)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--results',type=Path,required=True);ap.add_argument('--candidate',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);x=ap.parse_args();print(json.dumps(build(x.results,x.results/'ab_comparison.json',x.candidate,x.output),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
