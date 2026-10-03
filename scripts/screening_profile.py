"""Explicit profile classification from structured evidence, never title guesses."""
import argparse
import copy
import json
import sys
from pathlib import Path
import yaml
if not __package__: sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.pipeline_utils import ROOT,read_jsonl,write_jsonl,known_secrets,assert_safe

ELIGIBILITY={'include','exclude','uncertain','needs_fulltext'}
ROLES={'core_experimental','mechanism_transfer','review_synthesis','regional_context','methods_reference','out_of_scope','unresolved'}


def load_profile(path):
    profile=yaml.safe_load(Path(path).read_text())
    if not isinstance(profile,dict) or not profile.get('id') or set(profile.get('eligibility_statuses',[]))!=ELIGIBILITY or set(profile.get('evidence_roles',[]))!=ROLES:
        raise ValueError('Invalid screening profile')
    if not profile.get('core_experimental') or profile.get('policy',{}).get('automatic_exclusion') is not False:
        raise ValueError('Profile requires conservative explicit screening policy')
    return profile


def classify(record, evidence, profile):
    # v0.5 source layers remain independent. No concatenated title/keywords.
    a=evidence.get('abstract_evidence',evidence).get('evidence',{})
    f=evidence.get('fulltext_evidence',{})
    system=a.get('study_system',{})
    full_system=f.get('study_system',{})
    def values(name): return system.get(name) or full_system.get(name) or []
    crop=values('crop');soil=values('soil_type');salinity=values('salinity_context')
    scope=system.get('experimental_scale','unknown')
    if scope=='unknown': scope=full_system.get('experimental_scale','unknown')
    context=' '.join([*soil,*salinity]).casefold()
    system_text=' '.join([*crop,*soil]).casefold()
    rules=profile['core_experimental']
    saline=any(t.casefold() in context for t in rules['salinity_terms'])
    rice=any(t.casefold() in system_text for t in rules['system_terms'])
    treatments=a.get('treatments',{})
    metrics=a.get('measurements',{})
    ftreat=f.get('treatments',[])
    fm=f.get('measurements',[])
    themes=[]
    if treatments.get('irrigation') or treatments.get('water_regime'): themes.append('irrigation')
    if metrics.get('soil_chemical'): themes.append('water_salt')
    if metrics.get('carbon') or metrics.get('nitrogen'): themes.append('carbon_nitrogen')
    if metrics.get('microbial'): themes.append('microbial')
    if treatments.get('amendments') or treatments.get('biological_treatments'): themes.append('amendment')
    for key in ('yield','water_use','greenhouse_gases'):
        if metrics.get(key): themes.append(key)
    themes.extend(item.get('theme') for item in [*ftreat,*fm] if item.get('theme') in rules['themes'])
    themes=list(dict.fromkeys(themes))
    has_content=bool(record.get('abstract')) or bool(f.get('findings') or f.get('methods') or f.get('measurements'))
    review=any('review' in str(t).casefold() for t in record.get('document_types',[]))
    if not has_content: status,role,reason='needs_fulltext','unresolved','No abstract or validated fulltext evidence.'
    elif review: status,role,reason='include','review_synthesis','Review retained as synthesis; cited results are not an author-conducted experiment.'
    elif scope=='model': status,role,reason='include','regional_context','Model/scenario evidence retained for regional context, not a core experiment.'
    elif saline and rice and scope in rules['scales'] and any(t in rules['themes'] for t in themes):
        status,role,reason='include','core_experimental','Supported saline rice/paddy system, experimental/field observational design, and a directly relevant theme.'
    elif themes: status,role,reason='uncertain','mechanism_transfer','Relevant measured themes; core system eligibility still needs confirmation.'
    elif a.get('methods') or f.get('methods'): status,role,reason='uncertain','methods_reference','Methods information exists; direct system eligibility is uncertain.'
    else: status,role,reason='uncertain','unresolved','Insufficient structured evidence for eligibility.'
    anchors=[]
    for path in evidence.get('abstract_evidence',evidence).get('evidence_support',{}): anchors.append('/abstract_evidence'+path)
    anchors.extend('/fulltext_evidence'+path for path in f.get('system_support',{}))
    for field in ('methods','treatments','measurements','findings'):
        anchors.extend('/fulltext_evidence/'+field+'/'+str(i) for i,_ in enumerate(f.get(field,[])))
    return {'profile_id':profile['id'],'eligibility_status':status,'evidence_role':role,'reason':reason,
            'criteria':{'salinity_context':saline,'rice_paddy_system':rice,'scale':scope,'themes':themes},'evidence_anchors':anchors,
            'automatic_exclusion':False}


def screen_file(input_file,evidence_file,profile_file,output):
    records=read_jsonl(input_file);rows=read_jsonl(evidence_file);profile=load_profile(profile_file)
    mapped={r['uid']:r for r in rows}
    if len(mapped)!=len(records) or set(mapped)!=set(r['uid'] for r in records): raise ValueError('Screening identities differ')
    secrets=known_secrets();assert_safe([records,rows,profile],secrets)
    result=[]
    for r in records:
        if any(mapped[r['uid']].get(k)!=r.get(k) for k in ('uid','doi','title')): raise ValueError('Screening identifiers differ')
        from scripts.extractors.schema_validator import EvidenceValidator
        abstract=mapped[r['uid']].get('abstract_evidence',mapped[r['uid']])
        EvidenceValidator().validate(abstract,r)
        if 'fulltext_evidence' in mapped[r['uid']]:
            from scripts.fulltext.evidence import validate_fulltext
            manifest=mapped[r['uid']].get('fulltext_manifest',{})
            if manifest.get('parsed_file'):
                parsed=json.loads((ROOT/manifest['parsed_file']).read_text())
                validate_fulltext(mapped[r['uid']]['fulltext_evidence'],parsed,manifest['sha256'])
        row=copy.deepcopy(mapped[r['uid']]);row['screening_profile']=classify(r,row,profile);result.append(row)
    write_jsonl(output,result,secrets)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('--evidence',required=True);p.add_argument('--profile',default=str(ROOT/'profiles/saline_paddy.yaml'));p.add_argument('--output',required=True)
    a=p.parse_args();screen_file(a.input,a.evidence,a.profile,a.output)
