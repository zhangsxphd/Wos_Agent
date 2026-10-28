"""Recover missing abstracts only from exact, bounded first-page PDF spans.

Cross-page, ambiguous and apparently interleaved spans are left for review.
Text is never rewritten or dehyphenated. The source parse and offsets remain
available to reproduce every recovered character.
"""

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from scripts.pipeline_utils import ROOT, known_secrets, read_jsonl, utc_now, write_json, write_jsonl
from scripts.fulltext.admission import inspect_fulltext
from scripts.extractors.schema_validator import EvidenceValidationError


def abstract_span(text):
    start = re.search(r'(?mi)^\s*Abstract[ \t]*\n', text)
    if not start:
        return None, 'no_standalone_abstract_heading_on_first_page'
    end = re.search(r'(?mi)^\s*Key\s?words?\b', text[start.end():])
    if not end:
        return None, 'no_keyword_boundary_on_first_page'
    left, right = start.end(), start.end() + end.start()
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    value = text[left:right]
    if not 300 <= len(value) <= 6000:
        return None, 'abstract_span_length_requires_review'
    if re.search(r'(?mi)^\s*(?:\d\s+)?Introduction\b|Responsible Editor|Received:|Accepted:|©|/\w+\.tnum', value):
        return None, 'possible_interleaved_text_requires_review'
    if value[-1:] not in '.?!':
        return None, 'abstract_terminal_boundary_requires_review'
    return {'start': left, 'end': right, 'text': value}, None


def abstract_boundary_diagnostics(first_page, second_page=''):
    """Describe ambiguous boundaries without constructing or approving text."""
    heading = re.search(r'(?mi)^\s*Abstract\b([^\n]*)', first_page)
    style = ('missing' if not heading else 'inline' if heading.group(1).strip() else 'standalone')
    start = heading.end() if heading else 0
    keywords = bool(re.search(r'(?mi)^\s*Key\s?words?\b', first_page[start:])) if heading else False
    next_keywords = bool(re.search(r'(?mi)^\s*Key\s?words?\b', second_page))
    contamination = bool(re.search(r'(?mi)^\s*(?:Responsible Editor|Supplementary Information|Highlights)\b',
                                   first_page[start:])) if heading else False
    return {'heading_style':style,'first_page_keywords':keywords,'second_page_keywords':next_keywords,
            'metadata_or_highlights_present':contamination,
            'decision':'needs_layout_review'}


def recover(canonical, inventory, output, root=ROOT):
    root = Path(root).resolve()
    source = root / canonical
    records = read_jsonl(source)
    fulltexts = {r['uid']: r for r in read_jsonl(root / inventory)}
    results = copy.deepcopy(records)
    recovered, review = [], []
    secrets = known_secrets(root)
    for record in results:
        fulltext = fulltexts.get(record['uid'])
        if record.get('abstract') or not fulltext or fulltext['status'] != 'available':
            continue
        if record['doi'] != fulltext['doi']:
            raise ValueError('Fulltext DOI differs from canonical input')
        try:
            parsed, admission = inspect_fulltext(record, fulltext, root)
        except (EvidenceValidationError, OSError) as exc:
            review.append({'uid':record['uid'], 'doi':record['doi'],
                           'reason':str(exc) if isinstance(exc,EvidenceValidationError) else 'file_read_failed',
                           'raw_file':fulltext.get('raw_file'), 'parsed_file':fulltext.get('parsed_file')})
            continue
        page = '\n'.join(p['text'] for p in parsed['sections'][0]['paragraphs'])
        span, reason = abstract_span(page)
        if span is None:
            review.append({'uid': record['uid'], 'doi': record['doi'], 'reason': reason,
                           'raw_file': fulltext['raw_file'], 'parsed_file': fulltext['parsed_file']})
            continue
        anchor = {'uid': record['uid'], 'doi': record['doi'], 'raw_sha256': fulltext['sha256'],
                  'raw_file': fulltext['raw_file'], 'parsed_file': fulltext['parsed_file'],
                  'page': 1, 'page_text_recipe': "newline join of first section paragraph text in stored order",
                  'start': span['start'], 'end': span['end'], 'text': span['text'],
                  'method': 'exact span between standalone Abstract and Keywords headings',
                  'human_reviewed': False, 'semantic_rewriting': False}
        assert page[anchor['start']:anchor['end']] == span['text']
        record['abstract_enrichment_before_pdf_recovery'] = record.get('abstract_enrichment')
        record.update(abstract=span['text'], abstract_source='publisher_pdf', abstract_retrieved_at=utc_now(),
                      abstract_enrichment={**(record.get('abstract_enrichment') or {}),
                                           'status': 'selected', 'conflict': False,
                                           'selection_method': 'publisher_pdf_exact_heading_span',
                                           'requires_human_layout_review': True}, abstract_pdf_anchor=anchor)
        recovered.append(anchor)
    target = root / output
    write_jsonl(target, results, secrets)
    prior = json.loads(source.with_suffix('.manifest.json').read_text())
    report = target.with_suffix('.abstract_recovery.json')
    summary = {'created_at': utc_now(), 'canonical_input': canonical,
               'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
               'fulltext_inventory': inventory, 'records': len(results), 'new_pdf_abstracts': len(recovered),
               'abstracts': sum(bool(r.get('abstract')) for r in results),
               'missing_abstracts': sum(not r.get('abstract') for r in results),
               'abstract_sources': dict(Counter(r.get('abstract_source') for r in results if r.get('abstract'))),
               'recovered': recovered, 'layout_review_queue': review,
               'scope': 'Source-text recovery only; no scientific evidence annotation or human validation'}
    write_json(report, summary, secrets)
    prior.update(canonical_input=canonical, input_sha256=summary['input_sha256'], processed_file=output,
                 phase='exact_pdf_abstract_span_recovery', abstract_recovery_report=str(report.relative_to(root)))
    write_json(target.with_suffix('.manifest.json'), prior, secrets)
    return {k: v for k, v in summary.items() if k not in {'recovered', 'layout_review_queue'}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('canonical')
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(recover(args.canonical, args.inventory, args.output), ensure_ascii=False, indent=2))
