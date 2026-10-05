"""Validate one v0.7 dev40 independent Gold annotation; repair unique offsets only."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from jsonschema import Draft202012Validator
from scripts.extractors.schema_validator import EvidenceValidator, EvidenceValidationError, factual_leaves


def read_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]

def write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')

def unique_span(text, quote):
    found=[]; at=0
    while True:
        at=text.find(quote,at)
        if at<0: break
        found.append((at,at+len(quote))); at+=1
    return found[0] if len(found)==1 else None

def repair_offsets(item, abstract):
    repairs=[]
    for pointer,support in (item.get('evidence_support') or {}).items():
        if support.get('source')!='abstract': continue
        start,end=support.get('start'),support.get('end'); quote=support.get('evidence_text','')
        if type(start) is int and type(end) is int and abstract[start:end]==quote: continue
        span=unique_span(abstract,quote) if quote else None
        if span is not None:
            support['start'],support['end']=span; repairs.append(pointer)
    for i,finding in enumerate(item.get('evidence',{}).get('findings',[])):
        quote=finding.get('evidence_text',''); start,end=finding.get('start'),finding.get('end')
        if type(start) is int and type(end) is int and abstract[start:end]==quote: continue
        span=unique_span(abstract,quote) if quote else None
        if span is not None:
            finding['start'],finding['end']=span; repairs.append(f'/evidence/findings/{i}')
    for i,annotation in enumerate(item.get('evidence',{}).get('author_interpretations',[])):
        anchor=annotation.get('anchor',{}); quote=anchor.get('evidence_text','')
        start,end=anchor.get('start'),anchor.get('end')
        if type(start) is int and type(end) is int and abstract[start:end]==quote: continue
        span=unique_span(abstract,quote) if quote else None
        if span is not None:
            anchor['start'],anchor['end']=span; repairs.append(f'/evidence/author_interpretations/{i}/anchor')
    return repairs

def validate_file(annotation_path, input_path, schema_path, repaired_path=None):
    records={r['uid']:r for r in read_jsonl(input_path)}
    annotations=read_jsonl(annotation_path)
    schema=json.loads(Path(schema_path).read_text(encoding='utf-8'))
    schema_validator=Draft202012Validator(schema)
    grounding=EvidenceValidator(schema_path)
    seen=set(); rows=[]; repairs=[]; invalid_offsets=0; orphan_anchors=0; missing_support=0
    for ann in annotations:
        uid=ann.get('uid')
        if uid in seen: raise ValueError('duplicate_annotation_uid')
        seen.add(uid)
        record=records.get(uid)
        if record is None: raise ValueError('annotation_uid_not_in_bundle')
        fixed=repair_offsets(ann,record['abstract'])
        repairs.extend({'uid':uid,'pointer':p} for p in fixed)
        expected={pointer for pointer,_ in factual_leaves(ann.get('evidence',{}))}
        support_map=ann.get('evidence_support') or {}
        orphan_anchors += len(set(support_map)-expected)
        missing_support += len(expected-set(support_map))
        anchors=list(support_map.values())
        anchors.extend(ann.get('evidence',{}).get('findings',[]))
        anchors.extend(x.get('anchor',{}) for x in ann.get('evidence',{}).get('author_interpretations',[]))
        for support in anchors:
            if support.get('source')!='abstract':
                continue
            start,end=support.get('start'),support.get('end'); quote=support.get('evidence_text','')
            if type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(record['abstract']) or record['abstract'][start:end] != quote:
                invalid_offsets += 1
        schema_error=next(schema_validator.iter_errors(ann),None)
        grounding_error=None
        if schema_error is None:
            try: grounding.validate(ann,record)
            except EvidenceValidationError as e: grounding_error=str(e)
        rows.append({'uid':uid,'schema_valid':schema_error is None,'grounding_valid':schema_error is None and grounding_error is None,
                     'grounding_error':grounding_error})
    missing=set(records)-seen
    report={'expected':len(records),'received':len(annotations),'missing_uids':sorted(missing),'duplicate_uids':len(annotations)-len(seen),
            'schema_valid':sum(r['schema_valid'] for r in rows),'grounding_valid':sum(r['grounding_valid'] for r in rows),
            'grounding_errors':[{'uid':r['uid'],'error':r['grounding_error']} for r in rows if r['grounding_error']],
            'unique_offset_repairs':repairs,'invalid_offset_count':invalid_offsets,
            'orphan_anchor_count':orphan_anchors,'missing_support_count':missing_support,
            'unsupported':sum(not r['grounding_valid'] for r in rows), 'records':rows}
    if repaired_path:
        write_jsonl(repaired_path,annotations)
    return report

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--annotations',type=Path,required=True); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--schema',type=Path,required=True); ap.add_argument('--repaired-output',type=Path)
    a=ap.parse_args(); print(json.dumps(validate_file(a.annotations,a.input,a.schema,a.repaired_output),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
