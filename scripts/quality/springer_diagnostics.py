"""Read-only, source-anchored Springer abstract review candidates.

The previous publisher snapshot lacks an archived raw response and timestamp;
it is a comparison candidate only. Neither it nor a PDF span is approved.
"""
import argparse
import difflib
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from scripts.pipeline_utils import ROOT, known_secrets, read_jsonl, write_json, write_jsonl, utc_now
from scripts.fulltext.admission import project_file
from scripts.quality.literature_triage import write_csv
from scripts.recover_pdf_abstracts import abstract_span, abstract_boundary_diagnostics


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def words(value): return set(re.findall(r'[a-z0-9]+',str(value or '').casefold()))


def layout_hint(page):
    """Report repeated wide line gaps only as a layout-review hint."""
    width=page.get('width') or 0; items=page.get('words') or []
    heading=next((x for x in items if str(x[0]).casefold()=='abstract'),None)
    if not width or not heading:return 'not_established'
    lines={}
    for item in items:
        if heading[3]<=item[3]<heading[3]+220:
            lines.setdefault(round(item[3]/3)*3,[]).append(item)
    gaps=0
    for line in lines.values():
        line.sort(key=lambda item:item[1])
        if max((b[1]-a[2] for a,b in zip(line,line[1:])),default=0)>width*.075:gaps+=1
    return 'possible_two_column_or_wide_gap' if gaps>=2 else 'not_established'


def diagnose(record,quality,manifest,probe,publisher=None,snapshot_sha=None,root=ROOT):
    if record['uid']!=quality['uid'] or record['doi']!=quality['doi'] or manifest['uid']!=record['uid']:
        raise ValueError('identity_mismatch')
    raw=project_file(root,manifest['raw_file']);parsed_path=project_file(root,manifest['parsed_file'])
    if sha(raw)!=manifest['sha256']: raise ValueError('raw_hash_mismatch')
    parsed=json.loads(parsed_path.read_text());pages=['\n'.join(p['text'] for p in s['paragraphs']) for s in parsed['sections'][:2]]
    first=pages[0] if pages else '';span,reason=abstract_span(first)
    layout_pages=probe.get('pages') or []
    visual='\n'.join(p.get('text','') for p in layout_pages)
    page1_layout=layout_pages[0].get('text','') if layout_pages else ''
    normalized=lambda x:re.sub(r'\s+',' ',x).strip().casefold()
    parser_similarity=round(difflib.SequenceMatcher(None,normalized(first),normalized(page1_layout)).ratio(),3) if first and page1_layout else None
    heading=bool(re.search(r'(?im)^\s*abstract\b',first))
    p2keywords=len(pages)>1 and bool(re.search(r'(?im)^\s*keywords?\b',pages[1]))
    boundary=abstract_boundary_diagnostics(first,pages[1] if len(pages)>1 else '')
    other='';source=None;source_url=None;source_snapshot=None;overlap=None
    if publisher:
        if publisher.get('uid')!=record['uid'] or publisher.get('doi')!=record['doi']:
            raise ValueError('publisher_snapshot_identity_mismatch')
        other=publisher.get('abstract') or '';source='publisher_html_legacy_snapshot';source_url=publisher.get('source_url')
        source_snapshot=snapshot_sha
        tokens=words(other);overlap=round(len(tokens & words(visual))/len(tokens),3) if tokens and visual else None
    elif span:
        other=span['text'];source='existing_pdf_exact_first_page_span'
    existing=record.get('abstract') or ''
    return {'uid':record['uid'],'doi':record['doi'],'title':record.get('title'),
            'raw_file':manifest['raw_file'],'raw_sha256':manifest['sha256'],
            'parsed_file':manifest['parsed_file'],'parsed_sha256':sha(parsed_path),'parser':parsed.get('parser'),
            'pdf_page':'1-2' if p2keywords else '1 (plus 2 for context)',
            'existing_abstract':existing,'candidate_abstract':other,
            'candidate_source':source,'candidate_source_url':source_url,'candidate_source_snapshot_sha256':source_snapshot,
            'pdf_exact_span':{'page':1,'start':span['start'],'end':span['end']} if span else None,
            'existing_matches_pdf_span':bool(span and existing==span['text']),
            'pdfplumber_first_two_pages_token_coverage':overlap,
            'first_page_parser_text_similarity':parser_similarity,
            'first_page_layout_hint':layout_hint(layout_pages[0]) if layout_pages else 'not_established',
            'pypdf_abstract_heading_found':heading,'pypdf_page2_keyword_found':p2keywords,
            'abstract_heading_style':boundary['heading_style'],
            'metadata_or_highlights_present':boundary['metadata_or_highlights_present'],
            'pypdf_extraction_status':reason or 'exact_first_page_candidate',
            'pdfplumber_probe_status':probe.get('status'),'pdfplumber_pages_examined':len(layout_pages),
            'raw_text_excerpt':first[max(0,re.search(r'(?i)abstract',first).start()-60) if re.search(r'(?i)abstract',first) else 0:][:1200],
            'review_reason':('existing_pdf_span_needs_layout_review' if existing else 'pdf_abstract_boundary_or_order_unresolved'),
            'candidate_limitations':'仅为候选；对照原文检查双栏、跨页、特殊字符与边界。出版商旧快照没有原始响应归档。',
            'human_review_status':'pending','review_decision':'','review_notes':''}


def build(canonical,quality_file,inventory,probe_file,output,publisher_snapshot=None,root=ROOT):
    records=read_jsonl(canonical); quality=read_jsonl(quality_file); manifests=read_jsonl(inventory); probes=read_jsonl(probe_file)
    q={x['uid']:x for x in quality};m={x['uid']:x for x in manifests};p={x['uid']:x for x in probes}
    if len(q)!=len(quality) or len(m)!=len(manifests) or len(p)!=len(probes): raise ValueError('duplicate_uid')
    pub=json.loads(Path(publisher_snapshot).read_text()) if publisher_snapshot else []
    pubs={x['uid']:x for x in pub};snapshot_sha=sha(publisher_snapshot) if publisher_snapshot else None
    chosen=[r for r in records if r.get('doi','').startswith('10.1007/') and
            (q[r['uid']]['abstract_state']=='needs_layout_review' or
             (q[r['uid']]['abstract_state']=='missing' and q[r['uid']]['evidence_admission']=='technical_ready'))]
    rows=[diagnose(r,q[r['uid']],m[r['uid']],p[r['uid']],pubs.get(r['uid']),snapshot_sha,root) for r in chosen]
    target=Path(output);target.mkdir(parents=True,exist_ok=False);secrets=known_secrets(root)
    write_jsonl(target/'springer_abstract_candidates.jsonl',rows,secrets)
    fields=['uid','doi','title','raw_file','raw_sha256','parsed_file','parsed_sha256','parser','pdf_page',
            'raw_text_excerpt','existing_abstract','candidate_abstract','candidate_source','candidate_source_url',
            'pdf_exact_span','existing_matches_pdf_span','pdfplumber_first_two_pages_token_coverage',
            'first_page_parser_text_similarity','first_page_layout_hint',
            'pypdf_abstract_heading_found','pypdf_page2_keyword_found','pypdf_extraction_status',
            'abstract_heading_style','metadata_or_highlights_present',
            'pdfplumber_probe_status','review_reason','candidate_limitations','human_review_status','review_decision','review_notes']
    write_csv(target/'springer_abstract_diagnostics.csv',rows,fields)
    summary={'created_at':utc_now(),'records':len(rows),'candidate_sources':dict(Counter(r['candidate_source'] or 'none' for r in rows)),
             'pypdf_extraction_status':dict(Counter(r['pypdf_extraction_status'] for r in rows)),
             'existing_matches_pdf_span':sum(r['existing_matches_pdf_span'] for r in rows),
             'human_review_pending':len(rows),'approved':0,
             'inputs':{'canonical_sha256':sha(canonical),'quality_sha256':sha(quality_file),'inventory_sha256':sha(inventory),
                       'probe_sha256':sha(probe_file),'publisher_snapshot_sha256':snapshot_sha},
             'limitations':['Existing span equality does not establish correct layout or semantics.',
                            'Publisher snapshot lacks raw HTML and retrieval timestamp and is not automatically accepted.',
                            'Token overlap is a comparison aid, not proof of identity or complete abstract.']}
    write_json(target/'springer_diagnostics_summary.json',summary,secrets);return summary


if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('canonical');a.add_argument('--quality',required=True);a.add_argument('--inventory',required=True);a.add_argument('--probe',required=True);a.add_argument('--publisher-snapshot');a.add_argument('--output',required=True)
    o=a.parse_args();print(json.dumps(build(o.canonical,o.quality,o.inventory,o.probe,o.output,o.publisher_snapshot),ensure_ascii=False,indent=2))
