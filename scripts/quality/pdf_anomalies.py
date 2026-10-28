"""Inspect formerly blocked PDFs without changing their original bytes."""
import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from scripts.extractors.schema_validator import EvidenceValidationError
from scripts.fulltext.admission import inspect_fulltext,project_file
from scripts.pipeline_utils import ROOT,known_secrets,read_jsonl,write_json,write_jsonl,utc_now
from scripts.quality.literature_triage import write_csv

VISUALLY_INSPECTED={
    ('WOS:000768870800001','10.1186/s12284-022-00564-6'),
    ('WOS:001030352200001','10.3389/fmicb.2023.1210938'),
    ('WOS:001010809700001','10.3389/fmicb.2023.1165631'),
    ('WOS:000874616200001','10.3389/fpls.2022.989504'),
    ('WOS:000730402400009','10.32604/phyton.2022.018396')}


def diagnose(record,manifest,probe,root=ROOT):
    root=Path(root).resolve();path=project_file(root,manifest['raw_file'])
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if sha!=manifest['sha256']: raise ValueError('raw_hash_mismatch')
    parsers={};metadata={};pages=None;first=''
    for strict in (True,False):
        try:
            reader=PdfReader(path,strict=strict);count=len(reader.pages)
            parsers['pypdf_strict' if strict else 'pypdf_nonstrict']='readable'
            if not strict:
                pages=count;metadata={str(k):str(v) for k,v in (reader.metadata or {}).items()}
                first=reader.pages[0].extract_text() or '' if count else ''
        except Exception as exc:parsers['pypdf_strict' if strict else 'pypdf_nonstrict']=type(exc).__name__
    try:
        result=subprocess.run(['pdfinfo',str(path)],capture_output=True,text=True,timeout=30,check=False)
        parsers['poppler_pdfinfo']='readable' if result.returncode==0 else 'failed'
        pdfinfo={line.split(':',1)[0].strip():line.split(':',1)[1].strip() for line in result.stdout.splitlines() if ':' in line}
    except (OSError,subprocess.TimeoutExpired):pdfinfo={};parsers['poppler_pdfinfo']='unavailable'
    parsers['pdfplumber_first_pages']=probe.get('status','not_checked')
    page_text='\n'.join(p.get('text','') for p in probe.get('pages',[]))
    matches=sorted(set(x.rstrip('.,;:)]}') for x in re.findall(r'(?i)\b10\.\d{4,9}/[^\s<>"\'\[\]{}]+',unicodedata.normalize('NFKC',page_text))))
    try:
        _,admission=inspect_fulltext(record,manifest,root);status='technical_ready';reason=None
    except (EvidenceValidationError,OSError) as exc:
        status='needs_review';reason=str(exc) if isinstance(exc,EvidenceValidationError) else 'file_read_failed'
    pair=(record['uid'],record['doi']);visual=pair in VISUALLY_INSPECTED
    return {'uid':record['uid'],'doi':record['doi'],'title':record.get('title'),'source_url':manifest.get('source_url'),
            'raw_file':manifest['raw_file'],'raw_sha256':sha,'pdf_header':raw.lstrip().startswith(b'%PDF-'),
            'pdf_eof':b'%%EOF' in raw[-4096:],'page_count_pypdf':pages,'page_count_pdfinfo':pdfinfo.get('Pages'),
            'metadata_title':metadata.get('/Title') or pdfinfo.get('Title'),'pdfinfo_encrypted':pdfinfo.get('Encrypted'),
            'first_two_page_detected_dois':matches,'parser_results':parsers,'admission':status,'admission_reason':reason,
            'first_page_preview':('data/reports/literature_quality_20261028/previews/'+record['uid'].replace(':','_')+'.png') if visual else None,
            'assistant_visual_check':'first_page_title_and_doi_match_record' if visual else 'not_performed',
            'visual_check_limitation':'Assistant screenshot inspection is not user approval or proof of whole-file completeness.',
            'recommended_action':('review_parse_layout_and_content' if status=='technical_ready' else 'review_parser_reproducibility_or_reacquire_lawfully'),
            'human_review_required':True,'review_decision':'','review_notes':''}


def build(canonical,inventory,old_quality,probe_file,output,root=ROOT):
    records={r['uid']:r for r in read_jsonl(canonical)};inventory_rows={r['uid']:r for r in read_jsonl(inventory)}
    quality=read_jsonl(old_quality);probes={r['uid']:r for r in read_jsonl(probe_file)}
    chosen=[q for q in quality if q['evidence_admission']=='needs_review']
    results=[diagnose(records[q['uid']],inventory_rows[q['uid']],probes[q['uid']],root) for q in chosen]
    target=Path(output);target.mkdir(parents=True,exist_ok=False);secrets=known_secrets(root)
    write_jsonl(target/'pdf_anomalies.jsonl',results,secrets)
    fields=['uid','doi','title','source_url','raw_file','raw_sha256','pdf_header','pdf_eof','page_count_pypdf',
            'page_count_pdfinfo','metadata_title','pdfinfo_encrypted','first_two_page_detected_dois','parser_results',
            'admission','admission_reason','first_page_preview','assistant_visual_check','visual_check_limitation',
            'recommended_action','human_review_required','review_decision','review_notes']
    write_csv(target/'pdf_anomalies.csv',results,fields)
    summary={'created_at':utc_now(),'records':len(results),'technical_ready':sum(r['admission']=='technical_ready' for r in results),
             'still_needs_review':sum(r['admission']=='needs_review' for r in results),
             'visual_first_page_match':sum(r['assistant_visual_check']=='first_page_title_and_doi_match_record' for r in results),
             'inputs':{str(path):hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (canonical,inventory,old_quality,probe_file)},
             'confirmed_wrong_or_corrupt':None,'note':'No wrong-document or corruption conclusion without stronger evidence.'}
    write_json(target/'pdf_anomalies_summary.json',summary,secrets);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('canonical');p.add_argument('--inventory',required=True);p.add_argument('--old-quality',required=True);p.add_argument('--probe',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(build(a.canonical,a.inventory,a.old_quality,a.probe,a.output),ensure_ascii=False,indent=2))
