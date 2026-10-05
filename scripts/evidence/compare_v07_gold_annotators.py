"""Deterministic A/B agreement and disagreement packet for v0.7 dev Gold."""
from __future__ import annotations
import argparse,json
from collections import Counter,defaultdict
from pathlib import Path
from scripts.evidence.metrics_v2 import KEY_FIELDS, SCALE_FIELD, _field_items, field_metrics_v2, finding_metrics_v2

ONTOLOGY_FIELDS=(
 '/evidence/study_system/soil_type','/evidence/study_system/salinity_context',
 '/evidence/measurements/plant_growth','/evidence/measurements/other',
 '/evidence/measurements/microbial','/evidence/measurements/carbon',
 '/evidence/measurements/soil_chemical','/evidence/treatments/fertilization',
 '/evidence/treatments/amendments')

def read_jsonl(p): return [json.loads(x) for x in Path(p).read_text(encoding='utf-8').splitlines() if x.strip()]
def support_key(item):
 s=item.get('support') or {}
 if s.get('source')!='abstract': return None
 return (s.get('start'),s.get('end'),s.get('evidence_text'))

def category_pairs(uid,a,b,abstract):
    amap=defaultdict(list); bmap=defaultdict(list)
    for pointer in KEY_FIELDS:
        for i,item in enumerate(_field_items(a,pointer)):
            key=support_key(item)
            if key: amap[key].append((pointer,i,item))
        for i,item in enumerate(_field_items(b,pointer)):
            key=support_key(item)
            if key: bmap[key].append((pointer,i,item))
    pairs=[]
    for key in sorted(set(amap)&set(bmap),key=lambda x:(x[0] or -1,x[1] or -1,str(x[2]))):
        af=sorted({x[0] for x in amap[key]}); bf=sorted({x[0] for x in bmap[key]})
        if set(af)==set(bf): continue
        pairs.append({'uid':uid,'start':key[0],'end':key[1],'evidence_text':key[2],
                      'a_fields':af,'a_items':[x[2]['value'] for x in amap[key]],
                      'b_fields':bf,'b_items':[x[2]['value'] for x in bmap[key]],
                      'context':abstract[max(0,key[0]-220):min(len(abstract),key[1]+220)]})
    return pairs

def _item_context(item,abstract):
    support=item.get('support') or item.get('anchor') or item
    start,end=support.get('start'),support.get('end')
    text=support.get('evidence_text')
    if type(start) is int and type(end) is int:
        return abstract[max(0,start-220):min(len(abstract),end+220)]
    return text or ''

def _disagreement_items(uid,a,b,abstract):
    out=[]
    fm=field_metrics_v2(a,b,abstract)['field_metrics']
    for pointer,m in fm.items():
        ai=_field_items(a,pointer); bi=_field_items(b,pointer)
        paired_a={x['gold_index'] for x in m.get('matches',[])}
        paired_b={x['prediction_index'] for x in m.get('matches',[])}
        for idx,item in enumerate(ai):
            if idx not in paired_a:
                out.append({'uid':uid,'kind':'field','field':pointer,'direction':'A_only','value':item['value'],'support':item.get('support'),
                            'context':_item_context(item,abstract)})
        for idx,item in enumerate(bi):
            if idx not in paired_b:
                out.append({'uid':uid,'kind':'field','field':pointer,'direction':'B_only','value':item['value'],'support':item.get('support'),
                            'context':_item_context(item,abstract)})
    dm=finding_metrics_v2(a,b,abstract)
    for item in dm['unmatched_gold_items']:
        out.append({'uid':uid,'kind':'finding','field':'/evidence/findings','direction':'A_only','value':item.get('value'),
                    'support':item.get('support'),'claim':item.get('claim'),'context':_item_context(item,abstract)})
    for item in dm['unmatched_predictions']:
        out.append({'uid':uid,'kind':'finding','field':'/evidence/findings','direction':'B_only','value':item.get('value'),
                    'support':item.get('support'),'claim':item.get('claim'),'context':_item_context(item,abstract)})
    for key in ('mechanisms_explicit','limitations_explicit'):
        pointer='/evidence/'+key
        for direction,rec in (('A_only',a),('B_only',b)):
            other=b if direction=='A_only' else a
            left=rec.get('evidence',{}).get(key,[]) or []; right=other.get('evidence',{}).get(key,[]) or []
            right_norm={str(x).casefold() for x in right}
            for idx,value in enumerate(left):
                if str(value).casefold() in right_norm: continue
                support=(rec.get('evidence_support',{}) or {}).get(f'{pointer}/{idx}')
                item={'value':value,'support':support}
                out.append({'uid':uid,'kind':'field','field':pointer,'direction':direction,'value':value,'support':support,
                            'context':_item_context(item,abstract)})
    for direction,rec in (('A_only',a),('B_only',b)):
        other=b if direction=='A_only' else a
        left=rec.get('evidence',{}).get('author_interpretations',[]) or []
        right=other.get('evidence',{}).get('author_interpretations',[]) or []
        right_norm={str(x.get('text','')).casefold() for x in right}
        for item in left:
            if item.get('text','').casefold() in right_norm: continue
            out.append({'uid':uid,'kind':'author_interpretation','field':'/evidence/author_interpretations',
                        'direction':direction,'value':item.get('text'),'support':item.get('anchor'),
                        'context':_item_context(item.get('anchor',{}),abstract)})
    av=(a.get('evidence',{}).get('study_system',{}).get('experimental_scale') or 'unknown')
    bv=(b.get('evidence',{}).get('study_system',{}).get('experimental_scale') or 'unknown')
    if av!=bv:
        out.append({'uid':uid,'kind':'experimental_scale','field':SCALE_FIELD,'direction':'AB_disagree','a_value':av,'b_value':bv,
                    'a_support':(a.get('evidence_support') or {}).get(SCALE_FIELD),
                    'b_support':(b.get('evidence_support') or {}).get(SCALE_FIELD),'context':abstract})
    return out

def compare(input_path,a_path,b_path):
 inputs={r['uid']:r for r in read_jsonl(input_path)}; aa={r['uid']:r for r in read_jsonl(a_path)}; bb={r['uid']:r for r in read_jsonl(b_path)}
 if set(aa)!=set(inputs) or set(bb)!=set(inputs): raise ValueError('annotator_identity_mismatch')
 fields=Counter(); ontology={p:Counter() for p in ONTOLOGY_FIELDS}; findings=Counter(); scale={'exact':0,'disagreement':0}; per_uid={}; categories=[]
 for uid,record in inputs.items():
  a,b=aa[uid],bb[uid]; fm=field_metrics_v2(a,b,record['abstract']); dm=finding_metrics_v2(a,b,record['abstract'])
  per_uid[uid]={'fields':{},'findings':{k:dm[k] for k in ('exact_match','boundary_match','unmatched_prediction','unmatched_gold','gold_count','predicted_count')}}
  for pointer,m in fm['field_metrics'].items():
   fields.update({'exact':m['exact_tp'],'boundary_equivalent':m['boundary_tp'],'a_only':m['fn'],'b_only':m['fp']})
   per_uid[uid]['fields'][pointer]={k:m[k] for k in ('exact_tp','boundary_tp','fp','fn')}
   if pointer in ontology:
    ontology[pointer].update({'exact':m['exact_tp'],'boundary_equivalent':m['boundary_tp'],'a_only':m['fn'],'b_only':m['fp']})
  findings.update({'exact':dm['exact_match'],'boundary':dm['boundary_match'],'a_only':dm['unmatched_gold'],'b_only':dm['unmatched_prediction']})
  av=a.get('evidence',{}).get('study_system',{}).get('experimental_scale','unknown')
  bv=b.get('evidence',{}).get('study_system',{}).get('experimental_scale','unknown')
  if av==bv: scale['exact']+=1
  else: scale['disagreement']+=1
  categories.extend(category_pairs(uid,a,b,record['abstract']))
  per_uid[uid]['disagreements']=_disagreement_items(uid,a,b,record['abstract'])
 return {'record_count':len(inputs),'agreement_role':'deterministic inter-annotator agreement; not extraction performance',
         'fields':dict(fields),'findings':dict(findings),'experimental_scale_exact_enum_only':scale,
         'ontology_focused_disagreement_counts':{p:dict(v) for p,v in ontology.items()},
         'category_disagreement_count':len(categories),'category_disagreements':categories,
         'disagreements':[item for rec in per_uid.values() for item in rec['disagreements']], 'per_uid':per_uid}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--a',type=Path,required=True);ap.add_argument('--b',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
 x=ap.parse_args();result=compare(x.input,x.a,x.b);x.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'records':result['record_count'],'field_totals':result['fields'],'finding_totals':result['findings'],'category_disagreements':result['category_disagreement_count']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
