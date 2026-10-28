"""Separate provider incidents, data problems and pending scientific screening.

Uses a hash-bound quality snapshot and never edits the canonical literature.
Keyword signals are navigational hints, not research findings or exclusions.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, read_jsonl, utc_now, write_json, write_jsonl
from scripts.providers.base_client import canonical_doi
from scripts.quality.snapshot import verify_snapshot


VERSION='literature-triage-v1'
TERMS={'rice_paddy':r'\b(?:rice|paddy)\b','other_crops':r'\b(?:maize|wheat|barley|quinoa|sorghum)\b',
       'salinity':r'\b(?:salin\w*|salt|sodic|alkali\w*)\b',
       'water':r'\b(?:irrigation|water|drainage|flood\w*|drought)\b',
       'microbial':r'\b(?:microb\w*|bacter\w*|rhizob\w*)\b',
       'amendment':r'\b(?:biochar|amendment\w*|fertili[sz]\w*|gypsum|manure)\b',
       'carbon_nitrogen':r'\b(?:carbon|nitrogen|phosphorus)\b',
       'field':r'\b(?:field|farmland)\b','pot':r'\b(?:pot|greenhouse)\b',
       'review':r'\b(?:review|meta.analysis)\b'}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signals(record):
    fields={'title':str(record.get('title') or ''), 'keywords':' '.join(str(x) for x in (record.get('author_keywords') or [])),
            'abstract':str(record.get('abstract') or '')}
    matches={key:[field for field,value in fields.items() if re.search(pattern,value,re.I)]
             for key,pattern in TERMS.items()}
    return {key:value for key,value in matches.items() if value}


def classify(record, quality):
    if record.get('uid')!=quality.get('uid') or canonical_doi(record.get('doi'))!=canonical_doi(quality.get('doi')):
        raise ValueError('quality_record_identity_mismatch')
    flags=signals(record); issues=quality.get('issues') or []
    identity=[x for x in issues if x in {'duplicate_uid','duplicate_doi','missing_uid','missing_or_invalid_doi',
              'fulltext_doi_mismatch','duplicate_fulltext_uid'} or x.startswith('admission:pdf_identity') or x.startswith('admission:tei_identity')]
    content=[x for x in issues if x in {'abstract_layout_review','abstract_anchor_mismatch','abstract_anchor_unreadable',
             'unresolved_abstract_conflict','evidence_anchor_or_source_invalid'} or x.startswith('admission:') and x not in identity]
    completeness=[]
    if quality['abstract_state']=='missing': completeness.append('abstract_missing')
    if quality['evidence_admission']=='not_available': completeness.append('fulltext_unavailable')
    if quality['abstract_state']=='needs_source_review': completeness.append('abstract_source_missing')
    scientific=['screening_pending']
    if 'publication_year_outside_query_range' in issues: scientific.append('publication_year_rule_review')
    providers=[]
    for name,data in ((record.get('abstract_enrichment') or {}).get('provider_results') or {}).items():
        if isinstance(data,dict) and data.get('status') in {'error','parse_error'}:
            providers.append({'provider':name,'status':data.get('status'),'error_code':data.get('error_code'),
                              'http_status':data.get('http_status')})
    if 'rice_paddy' in flags and 'salinity' in flags: suggested='possible_core_review'
    elif ('other_crops' in flags or 'microbial' in flags or 'amendment' in flags or 'water' in flags): suggested='possible_transfer_or_context'
    else: suggested='uncertain_insufficient_scope_evidence'
    priority=('high_review_first' if suggested=='possible_core_review' else
              'medium_review' if suggested=='possible_transfer_or_context' else 'manual_priority_uncertain')
    return {'uid':record['uid'],'doi':record['doi'],'title':record.get('title'),
            'publication_year':record.get('publish_year'),'profile_id':'saline_paddy',
            'abstract_state':quality['abstract_state'],'fulltext_admission':quality['evidence_admission'],
            'raw_file':quality.get('raw_file'),'parsed_file':quality.get('parsed_file'),
            'identity_issues':identity,'completeness_issues':completeness,'content_issues':content,
            'provider_incidents':providers,'scientific_review_items':scientific,
            'keyword_signals':flags,'automatic_suggestion':suggested,'supplement_priority':priority,
            'suggestion_basis':'关键词仅用于排序和定位，必须结合摘要或全文核对；不自动排除。',
            'human_review_status':'pending','review_decision':'','review_notes':''}


FIELDS=['uid','doi','title','publication_year','profile_id','abstract_state','fulltext_admission','raw_file','parsed_file',
        'identity_issues','completeness_issues','content_issues','provider_incidents','scientific_review_items',
        'keyword_signals','automatic_suggestion','supplement_priority','suggestion_basis','human_review_status','review_decision','review_notes']


def write_csv(path,rows,fields=FIELDS):
    with Path(path).open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fields,extrasaction='ignore');writer.writeheader()
        for row in rows:
            values={k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v) for k,v in row.items()}
            values={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in values.items()}
            writer.writerow(values)


def build(canonical, quality_dir, output, *, inventory=None, provider_report=None, root=ROOT):
    canonical=Path(canonical);quality_dir=Path(quality_dir);target=Path(output)
    snapshot=json.loads((quality_dir/'quality_summary.json').read_text());inputs=snapshot['inputs']
    records=read_jsonl(canonical);quality=read_jsonl(quality_dir/'quality_rows.jsonl')
    verify_snapshot(snapshot,quality_dir/'quality_rows.jsonl',canonical,inventory,quality,root)
    by_uid={r['uid']:r for r in quality}
    if len(by_uid)!=len(quality) or len(records)!=len(quality) or len({r['uid'] for r in records})!=len(records):
        raise ValueError('quality_identity_coverage_mismatch')
    rows=[classify(r,by_uid[r['uid']]) for r in records]
    if target.exists():raise FileExistsError('new_output_directory_required')
    target.mkdir(parents=True)
    secrets=known_secrets(root);assert_safe(rows,secrets)
    by=lambda predicate:[r for r in rows if predicate(r)]
    technical=by(lambda r:bool(r['identity_issues'] or r['content_issues']))
    missing_abstract=by(lambda r:'abstract_missing' in r['completeness_issues'])
    missing_fulltext=by(lambda r:'fulltext_unavailable' in r['completeness_issues'])
    abstract_review=by(lambda r:r['abstract_state']=='needs_layout_review' or ('abstract_missing' in r['completeness_issues'] and r['raw_file']))
    provider=by(lambda r:bool(r['provider_incidents']))
    queues={'technical_review_queue.csv':technical,'abstract_review_queue.csv':abstract_review,
            'missing_abstract_priority.csv':missing_abstract,'missing_fulltext_priority.csv':missing_fulltext,
            'both_missing_priority.csv':by(lambda r:'abstract_missing' in r['completeness_issues'] and 'fulltext_unavailable' in r['completeness_issues']),
            'metadata_missing_queue.csv':by(lambda r:bool(r['completeness_issues'])),
            'provider_retry_queue.csv':provider,'scientific_screening_queue.csv':rows,'screening_candidates.csv':rows}
    for name,selection in queues.items():write_csv(target/name,selection)
    write_jsonl(target/'quality_records_v2.jsonl',rows,secrets)
    only_provider=by(lambda r:r['provider_incidents'] and not r['identity_issues'] and not r['content_issues'] and not r['completeness_issues'] and 'publication_year_rule_review' not in r['scientific_review_items'])
    legacy_only_provider=sum(set(q.get('issues') or [])=={'provider_error_or_parse_error'} for q in quality)
    reported_requests=None
    if provider_report:
        report=json.loads(Path(provider_report).read_text())
        reported_requests={name:data.get('requests') for name,data in (report.get('provider_statistics') or {}).items()}
    summary={'created_at':utc_now(),'version':VERSION,'grain':'one row per canonical UID',
             'inputs':{'canonical':str(canonical),'canonical_sha256':sha(canonical),'quality_summary_sha256':sha(quality_dir/'quality_summary.json'),
                       'quality_rows_sha256':sha(quality_dir/'quality_rows.jsonl'),
                       'inventory_sha256':sha(inventory) if inventory else None,
                       'provider_report_sha256':sha(provider_report) if provider_report else None},
             'records':len(rows),'unique_uid':len({r['uid'] for r in rows}),'unique_doi':len({canonical_doi(r['doi']) for r in rows}),
             'abstract_fulltext_cross_tab':{'|'.join(k):v for k,v in Counter((r['abstract_state'],r['fulltext_admission']) for r in rows).items()},
             'paper_counts':{'technical_identity_or_content_review':len(technical),'abstract_review':len(abstract_review),
                 'missing_abstract':len(missing_abstract),'missing_fulltext':len(missing_fulltext),
                 'both_missing':sum('abstract_missing' in r['completeness_issues'] and 'fulltext_unavailable' in r['completeness_issues'] for r in rows),
                 'provider_incident':len(provider),'only_provider_no_other_recorded_issue':len(only_provider),
                 'legacy_issues_only_provider':legacy_only_provider,'scientific_screening_pending':len(rows)},
             'incident_counts':dict(Counter(p['provider']+':'+str(p['error_code']) for r in rows for p in r['provider_incidents'])),
             'provider_http_requests_from_linked_report':reported_requests,
             'suggestions':dict(Counter(r['automatic_suggestion'] for r in rows)),
             'limitations':['Tags are title/abstract/keyword matches, not confirmed study facts or screening decisions.',
                            'Provider incident paper counts are not HTTP request counts; request counts need linked run report.',
                            'All scientific screening and decisions remain pending human review.']}
    write_json(target/'quality_summary_v2.json',summary,secrets)
    write_json(target/'screening_summary.json',{'version':VERSION,'suggestions':summary['suggestions'],
               'scientific_review_pending':len(rows),'automatic_exclusions':0},secrets)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('canonical');p.add_argument('--quality-dir',required=True)
    p.add_argument('--inventory');p.add_argument('--provider-report');p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(build(a.canonical,a.quality_dir,a.output,inventory=a.inventory,provider_report=a.provider_report)['paper_counts'],ensure_ascii=False,indent=2))
