"""Per-section manual extraction, independent abstract/fulltext layers and profile."""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
if not __package__: sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from scripts.pipeline_utils import ROOT,read_jsonl,write_jsonl,write_json,known_secrets,assert_safe,new_run_id
from scripts.extractors.base import BaseExtractor,ExtractorError
from scripts.fulltext.evidence import empty_fulltext,validate_fulltext,combine,prepare_sections,FACT_FIELDS
from scripts.extractors.schema_validator import EvidenceValidationError
from scripts.screening_profile import load_profile,classify


def chunk_key(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class ManualSectionExtractor(BaseExtractor):
    name='manual_fulltext_section'
    def __init__(self,directory): self.directory=Path(directory)
    def extract(self,payload):
        path=self.directory/(payload['document_sha256'])/(chunk_key(payload)+'.json')
        if not path.exists(): raise ExtractorError('manual_fulltext_chunk_missing')
        try: return json.loads(path.read_text())
        except (ValueError,OSError): raise ExtractorError('manual_fulltext_chunk_invalid') from None


def validate_chunk(row,payload,parsed,document_sha256):
    validate_fulltext(row,parsed,document_sha256)
    allowed={p['paragraph_id']:(p['char_start'],p['char_end']) for p in payload['section']['paragraphs']}
    anchors=list(row['system_support'].values())+[i['anchor'] for k in (*FACT_FIELDS,'findings','author_interpretations') for i in row[k]]
    for anchor in anchors:
        if anchor['section_id']!=payload['section']['section_id'] or anchor['paragraph_id'] not in allowed:
            raise EvidenceValidationError('Chunk response cites another section')
        start,end=allowed[anchor['paragraph_id']]
        if not start<=anchor['char_start']<anchor['char_end']<=end:
            raise EvidenceValidationError('Chunk response cites unsupplied text')
    return row


def merge_chunks(rows):
    combined=empty_fulltext()
    for row in rows:
        for k in (*FACT_FIELDS,'findings','author_interpretations'): combined[k].extend(copy.deepcopy(row[k]))
        for field,value in row['study_system'].items():
            if isinstance(value,list):
                target=combined['study_system'][field]
                for i,v in enumerate(value):
                    if v not in target:
                        j=len(target);target.append(v)
                        combined['system_support'][f'/study_system/{field}/{j}']=copy.deepcopy(row['system_support'][f'/study_system/{field}/{i}'])
            elif value not in (None,'unknown',''):
                current=combined['study_system'][field]
                if current not in (None,'unknown',value): raise EvidenceValidationError('Conflicting fulltext study system requires manual review')
                combined['study_system'][field]=value
                combined['system_support']['/study_system/'+field]=copy.deepcopy(row['system_support']['/study_system/'+field])
    return combined


def build_fulltext(input_file,abstract_file,fulltext_file,output=None,manual_dir=None,profile_file=None,root=ROOT,prepare_only=False):
    root=Path(root);secrets=known_secrets(root)
    paths=[root/Path(x) for x in (input_file,abstract_file,fulltext_file)]
    source,abstract_path,ft_path=paths
    baseline={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    records=read_jsonl(source);abstracts=read_jsonl(abstract_path);fulltexts=read_jsonl(ft_path)
    assert_safe([records,abstracts,fulltexts],secrets)
    mapped={r['uid']:r for r in abstracts};ft={r['uid']:r for r in fulltexts}
    identities={r['uid'] for r in records}
    if len(mapped)!=len(records) or len(ft)!=len(records) or set(mapped)!=identities or set(ft)!=identities: raise ValueError('Sidecar identities differ')
    run=new_run_id('fulltext_evidence')
    target=root/Path(output) if output else root/'data/evidence'/(run+'.jsonl')
    assert_safe([str(p) for p in paths]+[str(target),str(manual_dir or '')],secrets)
    if target.exists() or target.with_suffix('.manifest.json').exists(): raise FileExistsError('New evidence sidecar required')
    raw_dir=root/'data/raw/fulltext_extraction'/run
    profile=load_profile(profile_file or root/'profiles/saline_paddy.yaml')
    extractor=ManualSectionExtractor(root/Path(manual_dir)) if manual_dir else None
    rows=[];results=[]
    for record in records:
        uid=record['uid'];manifest=ft[uid];original=mapped[uid]
        if any(original.get(k)!=record.get(k) or manifest.get(k)!=record.get(k) for k in ('uid','doi')): raise ValueError('Source identity mismatch')
        parsed=None;responses=[];requests=[];errors=[]
        if manifest.get('status')=='available' and manifest.get('parsed_file'):
            parsed_path=root/manifest['parsed_file'];parsed=json.loads(parsed_path.read_text());assert_safe(parsed,secrets)
            raw_path=root/manifest['raw_file']
            if hashlib.sha256(raw_path.read_bytes()).hexdigest()!=manifest['sha256']: raise ValueError('Fulltext raw hash changed')
            requests=prepare_sections(record,parsed,manifest,raw_dir/manifest['sha256'],secrets)
            if extractor and not prepare_only:
                for index,path in enumerate(requests,1):
                    payload=json.loads(Path(path).read_text())
                    raw=None
                    try:
                        raw=extractor.extract(payload);assert_safe(raw,secrets)
                        write_json(raw_dir/manifest['sha256']/'responses'/(str(index).zfill(3)+'.json'),raw,secrets)
                        validate_chunk(raw,payload,parsed,manifest['sha256']);responses.append(raw)
                    except (ValueError,OSError,ExtractorError,EvidenceValidationError):
                        errors.append({'chunk_key':chunk_key(payload),'status':'rejected_or_missing'})
        merged=merge_chunks(responses)
        comparison={}
        if extractor and parsed:
            comparison_file=extractor.directory/manifest['sha256']/'comparison.json'
            if comparison_file.exists():
                comparison=json.loads(comparison_file.read_text());assert_safe(comparison,secrets)
                source_hash=hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                if comparison.get('abstract_record_sha256')!=source_hash or comparison.get('document_sha256')!=manifest['sha256']:
                    raise EvidenceValidationError('Comparison provenance does not match sources')
        reviewed=comparison.get('reviewed') is True and bool(responses) and not errors
        row=combine(record,original,merged,parsed,manifest,conflicts=comparison.get('conflicts',[]),comparison_reviewed=reviewed)
        # No new extraction means original completeness must not be upgraded
        # solely because a parseable file happens to be present.
        if not responses: row['completeness_after']['needs_fulltext']=original['completeness']['needs_fulltext']
        row['screening_profile']=classify(record,row,profile)
        row['extraction_provenance']={'extractor':'manual_fulltext_section' if responses else 'none','raw_dir':str(raw_dir.relative_to(root)),'requests':requests,'validated_chunks':len(responses),'errors':errors,'parsed_sha256':hashlib.sha256((root/manifest['parsed_file']).read_bytes()).hexdigest() if parsed else None}
        rows.append(row);results.append({'uid':uid,'validated_chunks':len(responses),'chunks':len(requests),'errors':errors})
    for path,digest in baseline.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest: raise RuntimeError('Input source changed')
    report={'schema_version':'0.5','run_id':run,'output':str(target),'protected_inputs':baseline,'records':len(rows),'prepared_only':prepare_only,'results':results,'fulltext_methods_records':sum(bool(r['fulltext_evidence']['methods'] or r['fulltext_evidence']['analytical_methods']) for r in rows),'fulltext_measurement_records':sum(bool(r['fulltext_evidence']['measurements']) for r in rows),'fulltext_finding_records':sum(bool(r['fulltext_evidence']['findings']) for r in rows),'fulltext_limitation_records':sum(bool(r['fulltext_evidence']['limitations_explicit']) for r in rows),'needs_fulltext_before':sum(r['completeness_before']['needs_fulltext'] for r in rows),'needs_fulltext_after':sum(r['completeness_after']['needs_fulltext'] for r in rows)}
    write_jsonl(target,rows,secrets);write_json(target.with_suffix('.manifest.json'),report,secrets)
    print(json.dumps({k:v for k,v in report.items() if k not in ('protected_inputs','results')},ensure_ascii=False))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('--abstract-evidence',required=True);p.add_argument('--fulltext',required=True);p.add_argument('--output');p.add_argument('--manual-dir');p.add_argument('--profile');p.add_argument('--prepare-only',action='store_true')
    a=p.parse_args()
    try: build_fulltext(a.input,a.abstract_evidence,a.fulltext,a.output,a.manual_dir,a.profile,prepare_only=a.prepare_only)
    except (ValueError,OSError,RuntimeError): print('Fulltext evidence failed; inspect safe per-chunk sidecars.',file=sys.stderr);raise SystemExit(1)
