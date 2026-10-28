"""Audit a literature pool and a fulltext inventory without changing inputs.

Outputs describe technical checks and review queues, not scientific accuracy.
The optional search manifest is a record of the original query execution.
"""

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from pypdf import PdfReader

from scripts.consolidate_fulltexts import identity_text
from scripts.normalize_record import normalize_title
from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, read_jsonl, utc_now, write_json, write_jsonl
from scripts.providers.base_client import canonical_doi
from scripts.fulltext.admission import inspect_fulltext, validate_record_evidence
from scripts.extractors.schema_validator import EvidenceValidationError
from scripts.quality.snapshot import context, digest as snapshot_digest, fingerprints


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_path(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError('unsafe_path')
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError('unsafe_path') from None
    return path


def doi_value(value):
    try:
        return canonical_doi(value)
    except (ValueError, TypeError, AttributeError):
        return None


def pdf_check(root, record, row):
    """Check file bytes, PDF readability, page count and stored first-page DOI."""
    issues = []
    try:
        raw_path = local_path(root, row.get('raw_file'))
        parsed_path = local_path(root, row.get('parsed_file'))
    except ValueError:
        return 'failed', ['unsafe_fulltext_path']
    if not raw_path.is_file():
        return 'missing', ['raw_pdf_missing']
    raw = raw_path.read_bytes()
    if digest(raw) != row.get('sha256'):
        return 'failed', ['raw_pdf_hash_mismatch']
    if not raw.lstrip().startswith(b'%PDF-') or b'%%EOF' not in raw[-4096:]:
        return 'failed', ['pdf_header_or_eof_missing']
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or not reader.pages:
            return 'failed', ['pdf_unreadable_or_empty']
        pages = len(reader.pages)
        first_raw = reader.pages[0].extract_text() or ''
    except Exception:
        return 'failed', ['pdf_parse_failed']
    if row.get('pages') is not None and pages != row['pages']:
        issues.append('page_count_mismatch')
    expected_doi = doi_value(record.get('doi'))
    digit_names = dict(zip(('zero','one','two','three','four','five','six','seven','eight','nine'), '0123456789'))
    first_raw = re.sub(r'/(zero|one|two|three|four|five|six|seven|eight|nine)\.tnum',
                       lambda match: digit_names[match.group(1)], first_raw)
    if not expected_doi or identity_text(expected_doi) not in identity_text(first_raw):
        issues.append('expected_doi_not_in_fresh_pdf_text')
    if not parsed_path.is_file():
        return 'needs_review', issues + ['parsed_text_missing']
    try:
        parsed = json.loads(parsed_path.read_text(encoding='utf-8'))
        first = '\n'.join(p['text'] for p in parsed['sections'][0]['paragraphs'])
        if parsed.get('pages') != pages:
            issues.append('parsed_page_count_mismatch')
        if parsed.get('raw_sha256') is not None and parsed['raw_sha256'] != row['sha256']:
            issues.append('parsed_raw_hash_mismatch')
        # Older parsers do not embed a raw hash: the name and inventory link are
        # checked, but the parser JSON itself has no previously recorded hash.
        if not parsed_path.name.startswith(row['sha256'] + '.'):
            issues.append('parsed_filename_hash_mismatch')
        if not expected_doi or identity_text(expected_doi) not in identity_text(first):
            issues.append('expected_doi_not_in_stored_first_page')
        if not parsed.get('body_paragraph_count'):
            issues.append('no_extracted_body_text')
    except (ValueError, TypeError, KeyError, IndexError, OSError):
        return 'failed', issues + ['parsed_text_invalid']
    return ('needs_review' if issues else 'identity_hash_checked'), issues


def audit(canonical_file, inventory_file=None, search_manifest=None, root=ROOT, evidence_file=None):
    root = Path(root).resolve()
    source = Path(canonical_file).resolve()
    source_hash = digest(source.read_bytes())
    records = read_jsonl(source)
    inventory = read_jsonl(inventory_file) if inventory_file else []
    inventory_hash = digest(Path(inventory_file).read_bytes()) if inventory_file else None
    evidence = read_jsonl(evidence_file) if evidence_file else []
    evidence_hash = digest(Path(evidence_file).read_bytes()) if evidence_file else None
    evidence_by_uid = defaultdict(list)
    for item in evidence: evidence_by_uid[item.get('uid')].append(item)
    searches, search_hash = [], None
    if search_manifest:
        path = Path(search_manifest)
        search_hash = digest(path.read_bytes())
        searches = json.loads(path.read_text(encoding='utf-8')).get('searches', [])
    by_uid, by_doi, titles = defaultdict(list), defaultdict(list), defaultdict(list)
    for position, record in enumerate(records):
        uid = record.get('uid')
        if uid:
            by_uid[str(uid).strip().upper()].append(position)
        doi = doi_value(record.get('doi'))
        if doi:
            by_doi[doi].append(position)
        if isinstance(record.get('title'), str) and record['title'].strip():
            titles[normalize_title(record['title'])].append(position)
    inv_uid = defaultdict(list)
    for item in inventory:
        inv_uid[item.get('uid')].append(item)
    year_ranges = set()
    for search in searches:
        match = re.search(r'\bPY\s*=\s*\(\s*(\d{4})\s*-\s*(\d{4})\s*\)', search.get('exact_query') or '', re.I)
        if match:
            year_ranges.add((int(match[1]), int(match[2])))
    bounds = next(iter(year_ranges)) if len(year_ranges) == 1 else None
    rows = []
    for record in records:
        uid = record.get('uid'); doi = doi_value(record.get('doi')); issues = []
        if not uid: issues.append('missing_uid')
        elif len(by_uid[str(uid).strip().upper()]) > 1: issues.append('duplicate_uid')
        if not doi: issues.append('missing_or_invalid_doi')
        elif len(by_doi[doi]) > 1: issues.append('duplicate_doi')
        if not isinstance(record.get('title'), str) or not record['title'].strip(): issues.append('missing_or_invalid_title')
        if not record.get('source_title'): issues.append('missing_journal')
        if record.get('publish_year') is None: issues.append('missing_publication_year')
        elif bounds and (not isinstance(record['publish_year'], int) or not bounds[0] <= record['publish_year'] <= bounds[1]):
            issues.append('publication_year_outside_query_range')
        if not record.get('matched_queries') or not record.get('provenance_history'):
            issues.append('missing_search_provenance')
        enrichment = record.get('abstract_enrichment') or {}
        if enrichment.get('conflict'):
            abstract_state = 'conflict'
        elif not record.get('abstract'):
            abstract_state = 'missing'
        elif not record.get('abstract_source'):
            abstract_state = 'needs_source_review'; issues.append('abstract_source_missing')
        elif enrichment.get('requires_human_layout_review'):
            abstract_state = 'needs_layout_review'
        else:
            abstract_state = 'sourced_not_individually_verified'
        anchor = record.get('abstract_pdf_anchor')
        if anchor:
            try:
                parsed = json.loads(local_path(root, anchor['parsed_file']).read_text(encoding='utf-8'))
                page = '\n'.join(p['text'] for p in parsed['sections'][anchor['page'] - 1]['paragraphs'])
                if page[anchor['start']:anchor['end']] != record.get('abstract'):
                    issues.append('abstract_anchor_mismatch')
            except (ValueError, TypeError, KeyError, IndexError, OSError):
                issues.append('abstract_anchor_unreadable')
        fulltext_state = 'not_provided'
        identity_status = 'not_checked'; parse_status = 'not_checked'; admission = 'not_available'
        admission_reason = None; anchor_status = 'not_checked'; item = {}
        pdf_issues = []
        items = inv_uid.get(uid, [])
        if inventory_file:
            if len(items) != 1:
                fulltext_state = 'conflict' if items else 'missing'
                issues.append('duplicate_fulltext_uid' if items else 'fulltext_inventory_missing')
            else:
                item = items[0]
                if doi_value(item.get('doi')) != doi:
                    fulltext_state = 'conflict'; issues.append('fulltext_doi_mismatch')
                elif item.get('status') == 'available':
                    if item.get('format') == 'pdf' or (not item.get('format') and str(item.get('raw_file') or '').lower().endswith('.pdf')):
                        fulltext_state, pdf_issues = pdf_check(root, record, item)
                    else:
                        fulltext_state = 'needs_review'
                    try:
                        _, technical = inspect_fulltext(record, item, root)
                        identity_status = technical['identity']['status']
                        parse_status = 'reproduced_from_raw'
                        admission = 'technical_ready'
                    except (EvidenceValidationError, OSError) as exc:
                        admission_reason = str(exc) if isinstance(exc, EvidenceValidationError) else 'file_read_failed'
                        admission = 'needs_review'
                        if admission_reason.startswith('tei_identity_'):
                            identity_status = admission_reason.removeprefix('tei_identity_')
                        elif admission_reason in {'parsed_content_not_reproduced', 'parser_environment_changed', 'fulltext_no_body_text'}:
                            identity_status = 'doi_match'
                        elif admission_reason == 'pdf_identity_requires_review':
                            identity_status = 'needs_review'
                        parse_status = admission_reason if admission_reason.startswith(('parsed_', 'parser_', 'fulltext_no_body')) else 'not_verified'
                elif item.get('status') == 'error':
                    fulltext_state = 'failed'
                elif item.get('status') in {'unavailable', 'pending'}:
                    fulltext_state = 'missing'
                else:
                    fulltext_state = 'needs_review'
        issues.extend(pdf_issues)
        if admission_reason: issues.append('admission:' + admission_reason)
        if evidence_file:
            evidence_items = evidence_by_uid.get(uid, [])
            if len(evidence_items) != 1:
                anchor_status = 'missing_or_duplicate_evidence'
            elif 'fulltext_evidence' not in evidence_items[0]:
                anchor_status = 'no_fulltext_evidence'
            else:
                try:
                    checked = validate_record_evidence(record, evidence_items[0], root)
                    anchor_status = 'valid' if checked else 'empty_fulltext_evidence'
                except (EvidenceValidationError, OSError):
                    anchor_status = 'invalid_or_unverified'
                    issues.append('evidence_anchor_or_source_invalid')
        failures = [name for name, data in (enrichment.get('provider_results') or {}).items()
                    if isinstance(data, dict) and data.get('status') in {'error', 'parse_error'}]
        if failures: issues.append('provider_error_or_parse_error')
        if abstract_state == 'conflict': issues.append('unresolved_abstract_conflict')
        if abstract_state == 'needs_layout_review': issues.append('abstract_layout_review')
        rows.append({'uid': uid, 'doi': record.get('doi'), 'title': record.get('title'),
                     'publish_year': record.get('publish_year'), 'abstract_state': abstract_state,
                     'abstract_source': record.get('abstract_source'), 'fulltext_state': fulltext_state,
                     'fulltext_format': item.get('format'), 'fulltext_source': item.get('source'),
                     'raw_file': item.get('raw_file'), 'parsed_file': item.get('parsed_file'),
                     'observed_file_sha256': fingerprints(root,item),
                     'fulltext_identity_status': identity_status, 'parse_status': parse_status,
                     'evidence_admission': admission, 'evidence_anchor_status': anchor_status,
                     'review_status': 'not_assessed', 'provider_failures': failures, 'issues': issues})
    overlap = Counter()
    for record in records:
        ids = sorted(set(record.get('matched_queries') or []))
        for a, b in combinations(ids, 2): overlap[a + ' | ' + b] += 1
    similar_titles = [{'normalized_title': title, 'uids': [records[i].get('uid') for i in indices],
                       'dois': [records[i].get('doi') for i in indices]}
                      for title, indices in titles.items() if len(indices) > 1 and len({doi_value(records[i].get('doi')) for i in indices}) > 1]
    unknown_inventory_uids = sorted(str(uid) for uid in inv_uid if uid not in {r.get('uid') for r in records})
    provider_statuses, provider_errors = Counter(), Counter()
    for record in records:
        for provider, outcome in ((record.get('abstract_enrichment') or {}).get('provider_results') or {}).items():
            if isinstance(outcome, dict):
                provider_statuses[provider + ':' + str(outcome.get('status') or 'unknown')] += 1
                if outcome.get('error_code'):
                    provider_errors[provider + ':' + str(outcome['error_code'])] += 1
    result = {'created_at': utc_now(), 'grain': 'one canonical literature record per UID; one inventory row per UID',
              'inputs': {'canonical': str(source), 'canonical_sha256': source_hash,
                         'inventory': str(inventory_file) if inventory_file else None, 'inventory_sha256': inventory_hash,
                         'evidence': str(evidence_file) if evidence_file else None, 'evidence_sha256': evidence_hash,
                         'search_manifest': str(search_manifest) if search_manifest else None, 'search_sha256': search_hash},
              'records': len(records), 'valid_doi_records': sum(bool(doi_value(r.get('doi'))) for r in records),
              'unique_dois': len(by_doi), 'duplicate_uid_groups': sum(len(v) > 1 for v in by_uid.values()),
              'duplicate_doi_groups': sum(len(v) > 1 for v in by_doi.values()),
              'abstract_present': sum(bool(r.get('abstract')) for r in records),
              'abstract_state_counts': dict(Counter(r['abstract_state'] for r in rows)),
              'abstract_sources': dict(Counter(r.get('abstract_source') or 'missing' for r in records)),
              'fulltext_state_counts': dict(Counter(r['fulltext_state'] for r in rows)),
              'fulltext_identity_counts': dict(Counter(r['fulltext_identity_status'] for r in rows)),
              'parse_status_counts': dict(Counter(r['parse_status'] for r in rows)),
              'evidence_admission_counts': dict(Counter(r['evidence_admission'] for r in rows)),
              'evidence_anchor_counts': dict(Counter(r['evidence_anchor_status'] for r in rows)),
              'issue_counts': dict(Counter(issue for row in rows for issue in row['issues'])),
              'provider_status_counts': dict(provider_statuses), 'provider_error_counts': dict(provider_errors),
              'candidate_abstract_input_count': sum(r['abstract_state'] == 'sourced_not_individually_verified' for r in rows),
              'searches': [{'query_id': s.get('query_id'), 'total_hits': s.get('total_hits'), 'collected': s.get('collected'),
                            'truncated': s.get('truncated'), 'status': s.get('status')} for s in searches],
              'matched_query_counts': dict(Counter(q for r in records for q in set(r.get('matched_queries') or []))),
              'query_pair_overlap': dict(overlap), 'publication_year_range_in_queries': bounds,
              'same_normalized_title_different_doi': similar_titles, 'unknown_inventory_uids': unknown_inventory_uids,
              'limitations': ['Input-ready means technically usable, not reviewed or scientifically correct.',
                              'PDF identity uses hash, fresh first-page extraction and stored parse; layout, full content and scientific interpretation need review.',
                              'Parser JSON is not independently cryptographically bound to original PDF in older runs.',
                              'Year outside the query range requires early-access versus issue-date review.']}
    if (digest(source.read_bytes()) != source_hash or (inventory_file and digest(Path(inventory_file).read_bytes()) != inventory_hash)
            or (evidence_file and digest(Path(evidence_file).read_bytes()) != evidence_hash)):
        raise RuntimeError('Input changed during read-only audit')
    return result, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('canonical'); parser.add_argument('--fulltexts'); parser.add_argument('--search-manifest'); parser.add_argument('--evidence')
    parser.add_argument('--output', required=True, help='New output directory; existing directories are never overwritten')
    args = parser.parse_args()
    result, rows = audit(args.canonical, args.fulltexts, args.search_manifest, evidence_file=args.evidence)
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=False)
    secrets = known_secrets()
    assert_safe([result, rows], secrets)
    write_jsonl(target / 'quality_rows.jsonl', rows, secrets)
    result['snapshot_integrity']={'version':'quality-snapshot-v2',
                                  'quality_rows_sha256':snapshot_digest(target/'quality_rows.jsonl'),
                                  'validation_context':context()}
    write_json(target / 'quality_summary.json', result, secrets)
    with (target / 'review_queue.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        fields = ['uid', 'doi', 'title', 'publish_year', 'doi_url', 'abstract_source', 'abstract_state', 'fulltext_format',
                  'fulltext_source', 'raw_file', 'parsed_file', 'fulltext_identity_status', 'parse_status',
                  'evidence_admission', 'evidence_anchor_status', 'review_status', 'review_action',
                  'review_decision', 'review_notes', 'provider_failures', 'issues']
        writer = csv.DictWriter(stream, fields, extrasaction='ignore'); writer.writeheader()
        priority=[]; fulltext_review=[]
        for r in rows:
            if not r['issues'] and r['evidence_admission'] == 'technical_ready': continue
            actions=[]
            if r['abstract_state']=='needs_layout_review': actions.append('核对摘要版面与边界')
            if r['abstract_state']=='missing' and r['raw_file']: actions.append('从原文核对摘要')
            if 'publication_year_outside_query_range' in r['issues']: actions.append('核对在线日期与刊期及纳入规则')
            if r['evidence_admission']=='needs_review': actions.append('核对全文身份、原文与解析一致性')
            if r['evidence_admission']=='not_available': actions.append('核对可获取的全文')
            entry={**r,'doi_url':'https://doi.org/'+r['doi'] if r.get('doi') else '',
                   'review_action':'；'.join(actions),'provider_failures':'; '.join(r['provider_failures']),'issues':'; '.join(r['issues'])}
            # Prevent spreadsheet formula execution from untrusted source text.
            safe_entry={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in entry.items()}
            writer.writerow(safe_entry)
            if (r['abstract_state']=='needs_layout_review' or
                    (r['abstract_state']=='missing' and r['raw_file']) or
                    'publication_year_outside_query_range' in r['issues']):
                priority.append(entry)
            if r['evidence_admission']=='needs_review': fulltext_review.append(entry)
    with (target / 'abstract_year_review.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fields,extrasaction='ignore');writer.writeheader()
        writer.writerows({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v)
                          for k,v in entry.items()} for entry in priority)
    write_jsonl(target / 'abstract_year_review.jsonl',priority,secrets)
    with (target / 'fulltext_review.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fields,extrasaction='ignore');writer.writeheader()
        writer.writerows({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v)
                          for k,v in entry.items()} for entry in fulltext_review)
    write_jsonl(target / 'fulltext_review.jsonl',fulltext_review,secrets)
    print(json.dumps({k: result[k] for k in ('records', 'abstract_present', 'abstract_state_counts', 'fulltext_state_counts', 'issue_counts')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
