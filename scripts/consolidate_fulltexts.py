"""Build an offline, identity-checked fulltext inventory from immutable run files."""

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from importlib.metadata import version
from pathlib import Path

from scripts.fulltext.pdf_metadata import parse_pdf
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, read_jsonl, utc_now, write_json, write_jsonl


def identity_text(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    value = value.translate(str.maketrans({c: '-' for c in '‐‑‒–−'}))
    return re.sub(r'\s+', '', value)


def consolidate(canonical, runs, output, root=ROOT):
    root = Path(root).resolve()
    canonical = root / canonical
    records = read_jsonl(canonical)
    by_uid = {r['uid']: r for r in records}
    if len(by_uid) != len(records):
        raise ValueError('Duplicate canonical UID')
    secrets = known_secrets(root)
    selected = {}
    run_hashes = {}
    for name in runs:
        path = root / name
        run_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        for row in read_jsonl(path):
            record = by_uid.get(row.get('uid'))
            if record is None or normalize_doi(record['doi']) != normalize_doi(row.get('doi')):
                raise ValueError('Run identity differs from canonical input')
            old = selected.get(row['uid'])
            if old is None or row['status'] == 'available':
                selected[row['uid']] = dict(row, inventory_source_run=name)
    target = root / output
    target.mkdir(parents=True, exist_ok=False)
    rows = []
    for record in records:
        row = dict(selected.get(record['uid'], {'uid': record['uid'], 'doi': record['doi'], 'status': 'pending'}))
        row.update(title=record.get('title'), abstract_available=bool(record.get('abstract')))
        if row['status'] == 'available':
            raw = (root / row['raw_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('Raw fulltext hash mismatch')
            if row.get('format') != 'pdf':
                raise ValueError('This inventory currently verifies PDF files only')
            parser_name = 'pypdf-page-v2-fonttools'
            environment = {'pypdf': version('pypdf'), 'fonttools': version('fonttools')}
            parsed_path = root / 'data/fulltext/parsed' / (row['sha256'] + '.' + parser_name + '.json')
            if row.get('alternate_parsed_file'):
                parsed_path = root / row['alternate_parsed_file']
                parsed = json.loads(parsed_path.read_text())
                if parsed.get('raw_sha256') != row['sha256'] or parsed.get('parser') != 'pdfplumber-page-v1':
                    raise ValueError('Alternate PDF parse is not bound to the raw PDF')
            elif parsed_path.exists():
                parsed = json.loads(parsed_path.read_text())
                if parsed.get('parser_environment') != environment:
                    raise ValueError('Parser environment changed; use a new parser version')
            else:
                parsed = parse_pdf(raw)
                parsed['parser'] = parser_name
                parsed['parser_environment'] = environment
                write_json(parsed_path, parsed, secrets)
            first = '\n'.join(p['text'] for p in parsed['sections'][0]['paragraphs'])
            if identity_text(record['doi']) not in identity_text(first):
                row.update(status='needs_review', reason='expected_doi_not_on_first_page')
            elif not parsed['body_paragraph_count']:
                row.update(status='needs_review', reason='pdf_requires_ocr')
            else:
                row.update(identity_check='doi_on_first_page_unicode_normalized', reason=None)
            row.update(previous_parsed_file=row.get('parsed_file'), parsed_file=str(parsed_path.relative_to(root)),
                       pages=parsed['pages'], parse_status='parsed')
            text = '\n\n'.join(p['text'] for s in parsed['sections'] for p in s['paragraphs'])
            assert_safe(text, secrets)
            text_file = target / (record['uid'].replace(':', '_') + '.txt')
            text_file.write_text(text, encoding='utf-8')
            row.update(text_file=str(text_file.relative_to(root)), text_characters=len(text))
        rows.append(row)
    write_jsonl(target / 'fulltexts.jsonl', rows, secrets)
    fields = ['uid', 'doi', 'title', 'abstract_available', 'status', 'reason', 'source', 'pages',
              'source_url', 'raw_file', 'text_file', 'parsed_file', 'sha256', 'identity_check']
    for name, values in [('inventory.csv', rows), ('remaining.csv', [r for r in rows if r['status'] != 'available'])]:
        with (target / name).open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(values)
    summary = {'created_at': utc_now(), 'canonical_input': str(canonical.relative_to(root)),
               'canonical_sha256': hashlib.sha256(canonical.read_bytes()).hexdigest(), 'run_sha256': run_hashes,
               'records': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
               'available_sources': dict(Counter(r['source'] for r in rows if r['status'] == 'available')),
               'abstracts': sum(bool(r.get('abstract')) for r in records),
               'available_without_abstract': sum(r['status'] == 'available' and not r['abstract_available'] for r in rows),
               'raw_bytes': sum((root / r['raw_file']).stat().st_size for r in rows if r['status'] == 'available'),
               'network_requests': 0}
    write_json(target / 'manifest.json', summary, secrets)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('canonical')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(consolidate(args.canonical, args.runs, args.output), ensure_ascii=False, indent=2))
