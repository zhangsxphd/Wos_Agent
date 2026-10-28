"""Quality checks use real source files, preserve inputs and disclose uncertainty."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from scripts.pipeline_utils import read_jsonl, write_jsonl
from scripts.quality.audit import audit, local_path
from scripts.fulltext.tei_parser import parse_tei
from scripts.fulltext.evidence import empty_fulltext
from tests.test_tei_identity import tei


class QualityAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write_records(self, records):
        path = self.root / 'records.jsonl'; write_jsonl(path, records); return path

    def test_duplicate_identifiers_and_year_are_reported_without_editing_input(self):
        base = {'uid':'U1', 'doi':'10.1234/one', 'title':'Same paper', 'source_title':'Journal',
                'publish_year':2027, 'matched_queries':['Q1'], 'provenance_history':[{'query_id':'Q1'}],
                'abstract':None, 'abstract_enrichment':{'provider_results':{}}}
        source = self.write_records([base, {**base, 'doi':'10.1234/two'}])
        manifest = self.root/'manifest.json'
        manifest.write_text(json.dumps({'searches':[{'query_id':'Q1', 'exact_query':'TS=rice AND PY=(2022-2026)',
                                                     'total_hits':2, 'collected':2, 'truncated':False}]}))
        before = source.read_bytes(); summary, rows = audit(source, search_manifest=manifest, root=self.root)
        self.assertEqual(summary['duplicate_uid_groups'], 1)
        self.assertEqual(summary['issue_counts']['publication_year_outside_query_range'], 2)
        self.assertEqual(len(summary['same_normalized_title_different_doi']), 1)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(rows[0]['abstract_state'], 'missing')

    def test_fake_stored_doi_cannot_make_blank_pdf_verified(self):
        writer = PdfWriter(); writer.add_blank_page(width=400, height=600)
        pdf = self.root/'paper.pdf'
        with pdf.open('wb') as f: writer.write(f)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        parsed = self.root/(sha+'.parsed.json')
        parsed.write_text(json.dumps({'pages':1, 'body_paragraph_count':1,
                                      'sections':[{'paragraphs':[{'text':'10.1234/one'}]}]}))
        record = {'uid':'U1', 'doi':'10.1234/one', 'title':'Paper', 'source_title':'Journal',
                  'publish_year':2025, 'abstract':'Text', 'abstract_source':'crossref',
                  'matched_queries':['Q1'], 'provenance_history':[{'query_id':'Q1'}]}
        source = self.write_records([record]); inv = self.root/'inventory.jsonl'
        write_jsonl(inv,[{'uid':'U1','doi':'10.1234/one','status':'available','sha256':sha,
                           'raw_file':'paper.pdf','parsed_file':parsed.name,'pages':1}])
        summary, rows = audit(source, inv, root=self.root)
        self.assertEqual(summary['fulltext_state_counts'], {'needs_review':1})
        self.assertIn('expected_doi_not_in_fresh_pdf_text', rows[0]['issues'])

    def test_path_traversal_and_outside_symlink_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsafe_path'):
            local_path(self.root, '../outside.pdf')
        with self.assertRaisesRegex(ValueError, 'unsafe_path'):
            local_path(self.root, '/tmp/outside.pdf')
        link = self.root/'external'; link.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unsafe_path'):
            local_path(self.root, 'external/outside.pdf')

    def test_unverified_abstracts_do_not_become_human_reviewed(self):
        source = self.write_records([{'uid':'U1','doi':'10.1234/one','title':'Paper', 'source_title':'Journal',
            'publish_year':2025,'matched_queries':['Q1'],'provenance_history':[{'query_id':'Q1'}],
            'abstract':'Existing source text.', 'abstract_source':'crossref'}])
        summary, rows = audit(source, root=self.root)
        self.assertEqual(rows[0]['abstract_state'], 'sourced_not_individually_verified')
        self.assertEqual(summary['candidate_abstract_input_count'], 1)

    def test_tei_admission_and_optional_anchor_are_not_inferred_from_available(self):
        record={'uid':'U1','doi':'10.1234/example','title':'Study','abstract':'Source','abstract_source':'crossref'}
        source=self.write_records([record]);raw=tei([record['doi']]);sha=hashlib.sha256(raw).hexdigest()
        (self.root/'paper.xml').write_bytes(raw);parsed=parse_tei(raw)
        (self.root/'parsed.json').write_text(json.dumps(parsed))
        manifest={'uid':'U1','doi':record['doi'],'status':'available','format':'tei_xml',
                  'raw_file':'paper.xml','parsed_file':'parsed.json','sha256':sha}
        inv=self.root/'inventory.jsonl';write_jsonl(inv,[manifest])
        ev=empty_fulltext();ev['methods']=[{'text':'Rice','evidence_source':'fulltext','anchor':{
            'source_scope':'fulltext','document_sha256':sha,'section_id':parsed['sections'][0]['section_id'],
            'paragraph_id':'wrong','char_start':0,'char_end':4}}]
        evidence=self.root/'evidence.jsonl';write_jsonl(evidence,[{'uid':'U1','doi':record['doi'],
            'fulltext_manifest':manifest,'fulltext_evidence':ev}])
        summary,rows=audit(source,inv,root=self.root,evidence_file=evidence)
        self.assertEqual(summary['evidence_admission_counts'],{'technical_ready':1})
        self.assertEqual(rows[0]['evidence_anchor_status'],'invalid_or_unverified')
        self.assertEqual(rows[0]['review_status'],'not_assessed')


if __name__ == '__main__':
    unittest.main()
