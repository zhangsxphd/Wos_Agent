"""Section-scoped manual evidence, exact anchors, independent source layers."""
import copy
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from .tei_parser import section_chunks
from ..extractors.schema_validator import EvidenceValidator, EvidenceValidationError
from ..pipeline_utils import ROOT,write_json,known_secrets,assert_safe

FACT_FIELDS=('experimental_design','treatments','sampling','measurements','analytical_methods','statistical_methods','methods','numerical_results','limitations_explicit')
CLAIM_TYPES=('causal','association','pathway','hypothesis','other')


def empty_fulltext():
    return {'evidence_source':'fulltext','study_system':{'crop':[],'soil_type':[],'salinity_context':[],'location':None,'experimental_scale':'unknown'},'system_support':{},**{k:[] for k in FACT_FIELDS},'findings':[],'author_interpretations':[]}


def upgrade_abstract(row,record):
    upgraded=EvidenceValidator().normalize(row,record)
    items=upgraded['evidence']['author_interpretations']
    existing={i['text'] for i in items}
    for index,text in enumerate(upgraded['evidence']['mechanisms_explicit']):
        if text not in existing:
            quote=upgraded['evidence_support']['/evidence/mechanisms_explicit/'+str(index)]
            # Compatibility conversion does not assert a causal category.
            items.append({'text':text,'claim_type':'other','source':'abstract','anchor':copy.deepcopy(quote)})
    EvidenceValidator().validate(upgraded,record)
    return upgraded


def anchor_text(anchor,parsed,document_sha256):
    if anchor.get('source_scope')!='fulltext' or anchor.get('document_sha256')!=document_sha256:
        raise EvidenceValidationError('Fulltext anchor scope/hash mismatch')
    matches=[p for s in parsed['sections'] if s['section_id']==anchor.get('section_id') for p in s['paragraphs'] if p['paragraph_id']==anchor.get('paragraph_id')]
    if len(matches)!=1: raise EvidenceValidationError('Fulltext paragraph anchor does not resolve')
    text=matches[0]['text'];start,end=anchor.get('char_start'),anchor.get('char_end')
    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text):
        raise EvidenceValidationError('Fulltext character offsets are invalid')
    return text[start:end]


def validate_fulltext(evidence,parsed,document_sha256):
    schema=json.loads((ROOT/'schemas/fulltext_evidence.schema.json').read_text())
    if next(Draft202012Validator(schema).iter_errors(evidence),None): raise EvidenceValidationError('Invalid fulltext evidence schema')
    for field in FACT_FIELDS:
        for item in evidence[field]:
            quote=anchor_text(item['anchor'],parsed,document_sha256)
            if item['text'] not in quote: raise EvidenceValidationError('Fulltext fact is not a source span')
    for finding in evidence['findings']:
        quote=anchor_text(finding['anchor'],parsed,document_sha256)
        if finding['evidence_text']!=quote or finding['claim'] not in quote:
            raise EvidenceValidationError('Fulltext finding or numerical result is unsupported')
    for item in evidence['author_interpretations']:
        quote=anchor_text(item['anchor'],parsed,document_sha256)
        if item['text'] not in quote: raise EvidenceValidationError('Fulltext author interpretation is unsupported')
        if item['claim_type']=='causal' and re.search(r'correlat|associat',quote,re.I) and not re.search(r'caus|caused|led to|driv|promot|inhibit',quote,re.I):
            raise EvidenceValidationError('Association alone is not a causal author claim')
    expected=set()
    for field,value in evidence['study_system'].items():
        values=value if isinstance(value,list) else [value]
        for index,v in enumerate(values):
            if v in (None,'unknown',''): continue
            pointer='/study_system/'+field+('/'+str(index) if isinstance(value,list) else '')
            expected.add(pointer)
            if pointer not in evidence['system_support']: raise EvidenceValidationError('Study system lacks fulltext support')
            quote=anchor_text(evidence['system_support'][pointer],parsed,document_sha256)
            if field=='experimental_scale':
                from ..extractors.schema_validator import SCALE_PATTERNS
                if not re.search(SCALE_PATTERNS[v],quote,re.I): raise EvidenceValidationError('Fulltext scale unsupported')
            elif str(v).casefold() not in quote.casefold(): raise EvidenceValidationError('Fulltext study system value unsupported')
    if set(evidence['system_support'])!=expected: raise EvidenceValidationError('Orphan fulltext system support')
    return evidence


def prepare_sections(record,parsed,manifest,directory,secrets=()):
    payloads=[]
    chunks=section_chunks(parsed)
    # PDF has only page anchors until reviewed section routing is supplied.
    # Prepare pages separately, never send a whole PDF as a single request.
    if not chunks and parsed.get('section_routing_requires_review'):
        chunks=section_chunks(parsed,scopes={'other'})
        for chunk in chunks: chunk['scope']='requires_review'
    for index,chunk in enumerate(chunks,1):
        payload={'uid':record.get('uid'),'doi':record.get('doi'),'title':record.get('title'),'document_sha256':manifest['sha256'],'source_scope':'fulltext','section':chunk}
        path=Path(directory)/('chunk_'+str(index).zfill(3)+'.json')
        write_json(path,payload,secrets);payloads.append(str(path))
    write_json(Path(directory)/'response_template.json',empty_fulltext(),secrets)
    return payloads


def completeness(abstract,fulltext,previous,fulltext_available):
    e=abstract['evidence']
    flags={'has_methods':bool(e['methods'] or fulltext['methods'] or fulltext['analytical_methods'] or fulltext['statistical_methods'] or fulltext['experimental_design']),
           'has_treatments':bool(any(e['treatments'].values()) or fulltext['treatments']),
           'has_measurements':bool(any(e['measurements'].values()) or fulltext['measurements']),
           'has_results':bool(e['findings'] or fulltext['findings']),
           'has_mechanism':any(i['claim_type'] in {'causal','pathway','hypothesis'}
                               for i in [*e.get('author_interpretations',[]),*fulltext['author_interpretations']]),
           'has_limitations':bool(e['limitations_explicit'] or fulltext['limitations_explicit'])}
    sufficient=all(flags[k] for k in ('has_methods','has_treatments','has_measurements','has_results'))
    flags['needs_fulltext']=not sufficient if fulltext_available else previous['needs_fulltext']
    return flags


def combine(record,abstract_row,fulltext=None,parsed=None,manifest=None,conflicts=None,comparison_reviewed=False):
    abstract=upgrade_abstract(abstract_row,record)
    fulltext=copy.deepcopy(fulltext or empty_fulltext());manifest=manifest or {}
    if parsed is not None: validate_fulltext(fulltext,parsed,manifest['sha256'])
    elif any(fulltext[k] for k in (*FACT_FIELDS,'findings','author_interpretations')) or fulltext['system_support']:
        raise EvidenceValidationError('Fulltext content needs parsed source')
    # Only exact fulltext duplicates; abstract and fulltext remain independent.
    seen=set();unique=[]
    for finding in fulltext['findings']:
        key=(finding['source'],finding['evidence_text'])
        if key not in seen: seen.add(key);unique.append(finding)
    fulltext['findings']=unique
    conflicts=copy.deepcopy(conflicts or [])
    for conflict in conflicts:
        if not conflict.get('description') or not conflict.get('abstract_anchor') or not conflict.get('fulltext_anchor'):
            raise EvidenceValidationError('Conflict requires both source anchors')
        from ..extractors.schema_validator import resolve_pointer
        pointer=conflict['abstract_anchor']
        if pointer not in abstract['evidence_support'] and not re.fullmatch(r'/evidence/findings/\d+',pointer):
            raise EvidenceValidationError('Conflict abstract anchor must identify supported evidence')
        value=resolve_pointer(abstract,conflict['abstract_anchor'])
        if value in (None,[],{},'unknown',''): raise EvidenceValidationError('Conflict abstract anchor empty')
        anchor_text(conflict['fulltext_anchor'],parsed,manifest['sha256'])
    before=abstract_row['completeness']
    after=completeness(abstract,fulltext,before,bool(parsed))
    return {'schema_version':'0.5','uid':record.get('uid'),'doi':record.get('doi'),'title':record.get('title'),
            'abstract_evidence':abstract,'fulltext_evidence':fulltext,'fulltext_manifest':manifest,
            'evidence_conflict':bool(conflicts),'evidence_conflicts':conflicts,
            'conflict_check_status':'conflicts_identified' if conflicts else 'no_conflicts_identified' if comparison_reviewed else 'not_assessed',
            'completeness_before':copy.deepcopy(before),'completeness_after':after,
            'source_priority':'Independent sources retained; no silent overwrite.'}
