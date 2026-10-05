"""Build an unapproved dev40 candidate from independent A/B labels and C decisions."""
from __future__ import annotations
import argparse, copy, json
from collections import defaultdict
from pathlib import Path
from scripts.evidence.metrics_v2 import KEY_FIELDS, SCALE_FIELD, _field_items
from scripts.extractors.schema_validator import factual_leaves

SPECIAL_LISTS=('/evidence/mechanisms_explicit','/evidence/limitations_explicit')

def read_jsonl(path): return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
def write_jsonl(path, rows): Path(path).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def pointer_parent(obj,pointer):
    parts=pointer.split('/')[1:]
    cur=obj
    for part in parts[:-1]: cur=cur[part.replace('~1','/').replace('~0','~')]
    return cur,parts[-1].replace('~1','/').replace('~0','~')
def pointer_set(obj,pointer,value):
    parent,key=pointer_parent(obj,pointer); parent[key]=value
def support_equal(a,b):
    a=a or {}; b=b or {}
    return all(a.get(k)==b.get(k) for k in ('source','evidence_text','start','end'))
def match_index(items,value,support):
    matches=[i for i,item in enumerate(items) if item.get('value')==value and support_equal(item.get('support'),support)]
    if len(matches)==1:return matches[0]
    matches=[i for i,item in enumerate(items) if item.get('value')==value]
    return matches[0] if len(matches)==1 else None
def abstract_anchor(support,abstract,value=None):
    if not support or not isinstance(support.get('evidence_text'),str): return None
    quote=support['evidence_text']
    if value is not None and str(value).casefold() not in quote.casefold(): return None
    positions=[]; start=0
    while quote:
        idx=abstract.find(quote,start)
        if idx<0:break
        positions.append(idx);start=idx+1
    if len(positions)!=1:return None
    at=positions[0]
    return {'source':'abstract','evidence_text':quote,'start':at,'end':at+len(quote)}
def get_pointer(obj,pointer):
    cur=obj
    for part in pointer.split('/')[1:]: cur=cur[part.replace('~1','/').replace('~0','~')]
    return cur
def compact(value,limit=720):
    if isinstance(value,list): value=value[:6]+([f'… ({len(value)-6} more)'] if len(value)>6 else [])
    text=json.dumps(value,ensure_ascii=False)
    return text if len(text)<=limit else text[:limit-1]+'…'
def find_nested_item(items,kind,source_item):
    if kind=='finding':
        claim=source_item.get('claim')
        found=[i for i,x in enumerate(items) if x.get('claim')==claim and x.get('evidence_text')==source_item.get('evidence_text')]
    else:
        text=source_item.get('text')
        anchor=source_item.get('anchor',{})
        found=[i for i,x in enumerate(items) if x.get('text')==text and support_equal(x.get('anchor'),anchor)]
    return found[0] if len(found)==1 else None

def build(input_path,a_path,b_path,comparison_path,disagreements_path,adjudication_path,out_path,review_path,manifest_path):
    inputs={r['uid']:r for r in read_jsonl(input_path)}
    aa={r['uid']:r for r in read_jsonl(a_path)}; bb={r['uid']:r for r in read_jsonl(b_path)}
    comp=json.loads(Path(comparison_path).read_text(encoding='utf-8'))
    items={x['id']:x for x in json.loads(Path(disagreements_path).read_text(encoding='utf-8'))['items']}
    decisions={x['id']:x for x in read_jsonl(adjudication_path)}
    if set(inputs)!=set(aa) or set(inputs)!=set(bb): raise ValueError('identity_set_mismatch')
    if set(items)!=set(decisions): raise ValueError('adjudication_set_mismatch')
    result={uid:copy.deepcopy(aa[uid]) for uid in inputs}
    # Track array fields with each item's original grounding, allowing exact index regeneration.
    tracked=(set(KEY_FIELDS)-{SCALE_FIELD})|set(SPECIAL_LISTS)
    lists={uid:{p:_field_items(aa[uid],p) for p in tracked} for uid in inputs}
    scales={uid:(aa[uid]['evidence']['study_system'].get('experimental_scale') or 'unknown') for uid in inputs}
    scale_support={uid:copy.deepcopy((aa[uid].get('evidence_support') or {}).get(SCALE_FIELD)) for uid in inputs}
    unresolved=[]; applied=defaultdict(int); modified_review=[]
    for item_id,item in items.items():
        uid=item['uid']; d=decisions[item_id]; decision=d['decision']; direction=item.get('direction')
        if decision=='NEEDS_HUMAN_REVIEW':
            unresolved.append((item_id,item,d)); continue
        if item['kind']=='experimental_scale':
            if decision in ('A','B'):
                side='A' if decision=='A' else 'B'; value=(aa if side=='A' else bb)[uid]['evidence']['study_system'].get('experimental_scale') or 'unknown'
                support=((aa if side=='A' else bb)[uid].get('evidence_support') or {}).get(SCALE_FIELD)
            elif decision=='MODIFY':
                value=d.get('candidate_value'); support=abstract_anchor(d.get('candidate_support'),inputs[uid]['abstract'],value)
                if support is None: unresolved.append((item_id,item,d));continue
            else: raise ValueError(f'bad_scale_decision:{item_id}')
            scales[uid]=value or 'unknown';scale_support[uid]=copy.deepcopy(support);applied[decision]+=1;continue
        if item['kind'] in ('finding','author_interpretation'):
            ptr='/evidence/findings' if item['kind']=='finding' else '/evidence/author_interpretations'
            arr=result[uid]['evidence']['findings' if item['kind']=='finding' else 'author_interpretations']
            source_side='A' if direction=='A_only' else 'B'
            source_record=(aa if source_side=='A' else bb)[uid]
            if item['kind']=='finding':
                src_items=source_record['evidence']['findings']; claim=item.get('claim')
                matches=[x for x in src_items if x.get('claim')==claim]
                source_obj=copy.deepcopy(matches[0]) if len(matches)==1 else None
                found=[i for i,x in enumerate(arr) if source_obj and x==source_obj]
                idx=found[0] if len(found)==1 else None
            else:
                matches=[x for x in source_record['evidence'].get('author_interpretations',[]) if x.get('text')==item.get('value')]
                source_obj=copy.deepcopy(matches[0]) if len(matches)==1 else None
                found=[i for i,x in enumerate(arr) if source_obj and x==source_obj]
                idx=found[0] if len(found)==1 else None
            if decision in ('A','B'):
                chosen='A' if decision=='A' else 'B'
                if direction=='A_only' and chosen=='B':
                    if idx is not None: arr.pop(idx)
                elif direction=='B_only' and chosen=='B':
                    if source_obj is not None and source_obj not in arr: arr.append(source_obj)
                # selecting side A leaves A-only and excludes B-only
            else: # MODIFY
                value=d.get('candidate_value'); support=abstract_anchor(d.get('candidate_support'),inputs[uid]['abstract'],value)
                if source_obj is None or support is None:
                    unresolved.append((item_id,item,d));continue
                if idx is not None and direction=='A_only': arr.pop(idx)
                if item['kind']=='finding':
                    new_obj=copy.deepcopy(source_obj);new_obj['claim']=value;new_obj['source']=support['source'];new_obj['evidence_text']=support['evidence_text'];new_obj['start']=support['start'];new_obj['end']=support['end']
                else:
                    new_obj=copy.deepcopy(source_obj);new_obj['text']=value;new_obj['anchor']=support
                if new_obj not in arr: arr.append(new_obj)
            applied[decision]+=1;continue
        pointer=item['field'];
        if pointer==SCALE_FIELD:
            if decision in ('A','B'):
                source=(aa if decision=='A' else bb)[uid]
                scales[uid]=source['evidence']['study_system'].get('experimental_scale') or 'unknown'
                scale_support[uid]=copy.deepcopy((source.get('evidence_support') or {}).get(SCALE_FIELD))
            elif decision=='MODIFY':
                value=d.get('candidate_value'); anchor=abstract_anchor(d.get('candidate_support'),inputs[uid]['abstract'],value)
                if anchor is None: unresolved.append((item_id,item,d));continue
                scales[uid]=value or 'unknown';scale_support[uid]=anchor
            applied[decision]+=1;continue
        if pointer not in tracked: raise ValueError(f'untracked_pointer:{pointer}')
        current=lists[uid][pointer]
        source_side='A' if direction=='A_only' else 'B'
        if item['kind']=='experimental_scale': raise AssertionError
        source_records=aa if source_side=='A' else bb
        source_items=_field_items(source_records[uid],pointer)
        value=item.get('value')
        support=item.get('support')
        idx=match_index(current,value,support) if direction=='A_only' else None
        add_item=None
        if direction=='A_only':
            if decision=='B' and idx is not None: current.pop(idx)
            elif decision=='MODIFY':
                anchor=abstract_anchor(d.get('candidate_support'),inputs[uid]['abstract'],d.get('candidate_value'))
                if anchor is None: unresolved.append((item_id,item,d));continue
                if idx is not None: current.pop(idx)
                add_item={'value':d['candidate_value'],'support':anchor}
        elif direction=='B_only':
            source_idx=match_index(source_items,value,support)
            if source_idx is not None: add_item=copy.deepcopy(source_items[source_idx])
            if decision=='B' and add_item is not None: current.append(add_item)
            elif decision=='MODIFY':
                anchor=abstract_anchor(d.get('candidate_support'),inputs[uid]['abstract'],d.get('candidate_value'))
                if anchor is None: unresolved.append((item_id,item,d));continue
                current.append({'value':d['candidate_value'],'support':anchor})
        else:
            raise ValueError(f'unsupported_direction:{item_id}:{direction}')
        applied[decision]+=1
    # Materialize tracked arrays and regenerate their support pointers.
    for uid,row in result.items():
        for pointer in tracked:
            values=lists[uid][pointer]
            # Schema array fields are unique sets in practice; reconciliation can surface
            # the same selected value through both an A/B modification and its counterpart.
            deduped=[]; seen_values=set()
            for item in values:
                key=json.dumps(item['value'],ensure_ascii=False,sort_keys=True)
                if key not in seen_values:
                    deduped.append(item);seen_values.add(key)
            values=lists[uid][pointer]=deduped
            pointer_set(row,pointer,[x['value'] for x in values])
            sm=row.setdefault('evidence_support',{})
            for key in list(sm):
                if key==pointer or key.startswith(pointer+'/'): del sm[key]
            for i,x in enumerate(values):
                if x.get('support') is not None: sm[f'{pointer}/{i}']=copy.deepcopy(x['support'])
        pointer_set(row,SCALE_FIELD,scales[uid])
        if scale_support[uid] is not None: row['evidence_support'][SCALE_FIELD]=copy.deepcopy(scale_support[uid])
        else: row['evidence_support'].pop(SCALE_FIELD,None)
    # If a selected scale phrase fails the frozen contract validator, preserve it as unknown and surface one grouped review item.
    from scripts.extractors.schema_validator import EvidenceValidator, EvidenceValidationError
    validator=EvidenceValidator()
    scale_review=[]
    for uid,row in result.items():
        try: validator.validate(row,inputs[uid])
        except EvidenceValidationError as exc:
            if str(exc)=='Experimental scale is not explicitly supported' and row['evidence']['study_system']['experimental_scale']!='unknown':
                previous=row['evidence']['study_system']['experimental_scale']
                row['evidence']['study_system']['experimental_scale']='unknown';row['evidence_support'].pop(SCALE_FIELD,None)
                scale_review.append({'uid':uid,'provisional_value':previous,'reason':'Current anchor does not meet explicit experimental-scale validator; provisional candidate leaves scale unknown.'})
    report={'status':'development_gold_candidate','record_count':len(result),'uids':sorted(result),'adjudicator_decisions_applied':dict(applied),
            'unresolved_item_ids':[x[0] for x in unresolved],'scale_validator_review':scale_review,
            'human_approved':False,'future_holdout_abstracts_accessed':False,'future_holdout_gold_created':False,'future_holdout_extraction_run':False}
    write_jsonl(out_path,[result[u] for u in sorted(result)])
    Path(manifest_path).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Cluster by field and kind, keeping all affected IDs/UIDs while showing one concise example.
    clusters=defaultdict(list)
    for item_id,item,d in unresolved:
        clusters[(item.get('field','unknown'),item.get('kind','unknown'))].append((item_id,item,d))
    lines=['# v0.7 Dev40 Gold Human Review','', 'Status: development Gold candidate; not human-approved. A/B agreement and C decisions were incorporated where determinate. `unknown` remains provisional where the strict abstract grounding validator cannot support an experimental-scale assertion. Future holdout material was not accessed.','',f'Unresolved C decisions: {len(unresolved)} items across {len(clusters)+bool(scale_review)} review groups.','']
    n=0
    for (field,kind),members in sorted(clusters.items()):
        n+=1;uids=sorted({x[1]['uid'] for x in members});example=members[0]
        eu=example[1]['uid']; pointer=field
        side_items=[x for x in comp['disagreements'] if x.get('uid')==eu and x.get('field')==field and x.get('kind')==kind]
        av=[(x.get('claim') if kind=='finding' else x.get('value')) for x in side_items if x.get('direction')=='A_only']
        bv=[(x.get('claim') if kind=='finding' else x.get('value')) for x in side_items if x.get('direction')=='B_only']
        if kind=='experimental_scale':
            av=aa[eu]['evidence']['study_system'].get('experimental_scale');bv=bb[eu]['evidence']['study_system'].get('experimental_scale')
        candidate_value=get_pointer(result[eu],pointer) if pointer not in ('/evidence/findings','/evidence/author_interpretations') else (result[eu]['evidence']['findings'] if kind=='finding' else result[eu]['evidence'].get('author_interpretations',[]))
        lines += [f'## G{n:02d} — {field} ({kind})','',f'- Affected UIDs ({len(uids)}): '+', '.join(uids),f'- C item IDs ({len(members)}): '+', '.join(x[0] for x in members),
                  f'- Contract rule: {example[2].get("contract_rule") or field}',f'- C: NEEDS_HUMAN_REVIEW — {example[2].get("rationale")}',
                  f'- Context: {example[1].get("context","")[:440]}',f'- A: {compact(av)}',f'- B: {compact(bv)}',f'- Provisional candidate: {compact(candidate_value)}', '- Recommended action: NEEDS_HUMAN_REVIEW','']
    if scale_review:
        n+=1;lines += [f'## G{n:02d} — experimental_scale explicit grounding','',f'- Affected UIDs ({len(scale_review)}): '+', '.join(x['uid'] for x in scale_review),'- Candidate value: unknown pending review','- Contract rule: experimental scale must be explicitly identified by the abstract; named models and generic background/application mentions do not establish physical study scale.','- Examples: '+ '; '.join(f"{x['uid']}={x['provisional_value']}" for x in scale_review[:5]),'- Recommended action: NEEDS_HUMAN_REVIEW','']
    Path(review_path).write_text('\n'.join(lines),encoding='utf-8')
    return report

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--a',type=Path,required=True);ap.add_argument('--b',type=Path,required=True);ap.add_argument('--comparison',type=Path,required=True);ap.add_argument('--disagreements',type=Path,required=True);ap.add_argument('--adjudications',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--review',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True);args=ap.parse_args();print(json.dumps(build(args.input,args.a,args.b,args.comparison,args.disagreements,args.adjudications,args.output,args.review,args.manifest),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
