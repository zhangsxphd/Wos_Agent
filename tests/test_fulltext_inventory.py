"""Scientific source identity and ambiguous-layout rejection regressions."""

import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.consolidate_fulltexts import consolidate, identity_text
from scripts.pipeline_utils import read_jsonl, write_jsonl
from scripts.recover_pdf_abstracts import abstract_span


class InventoryTests(unittest.TestCase):
    def test_source_hash_corruption_stops_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'paper.pdf').write_bytes(b'%PDF-changed')
            write_jsonl(root/'records.jsonl',[{'uid':'U1','doi':'10.1234/test'}])
            write_jsonl(root/'run.jsonl',[{'uid':'U1','doi':'10.1234/test','status':'available',
                'format':'pdf','raw_file':'paper.pdf','sha256':hashlib.sha256(b'%PDF-original').hexdigest()}])
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                consolidate('records.jsonl',['run.jsonl'],'inventory',root)
            self.assertFalse((root/'inventory/fulltexts.jsonl').exists())

    def test_wrong_doi_is_not_counted_as_available(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root=Path(tmp);raw=b'%PDF-mock';(root/'paper.pdf').write_bytes(raw)
            write_jsonl(root/'records.jsonl',[{'uid':'U1','doi':'10.1234/test'}])
            write_jsonl(root/'run.jsonl',[{'uid':'U1','doi':'10.1234/test','status':'available',
                'format':'pdf','source':'oa_location','raw_file':'paper.pdf','sha256':hashlib.sha256(raw).hexdigest()}])
            parsed={'pages':1,'body_paragraph_count':1,'sections':[{'paragraphs':[{'text':'10.1234/wrong'}]}]}
            with patch('scripts.consolidate_fulltexts.parse_pdf',return_value=parsed):
                result=consolidate('records.jsonl',['run.jsonl'],'inventory',root)
            self.assertEqual(result['status_counts'],{'needs_review':1})
            self.assertEqual(read_jsonl(root/'inventory/fulltexts.jsonl')[0]['reason'],'expected_doi_not_on_first_page')

    def test_unicode_doi_hyphens_are_typography_only(self):
        self.assertEqual(identity_text('10.1186/s12284‑022‑00564‑6'),identity_text('10.1186/s12284-022-00564-6'))

    def test_abstract_spans_keep_source_offsets_and_hyphens(self):
        text='Header\nAbstract\n  '+('Exact source exam-\nple. '*25)+' \nKeywords Rice\nIntroduction'
        span,reason=abstract_span(text)
        self.assertIsNone(reason)
        self.assertEqual(text[span['start']:span['end']],span['text'])
        self.assertIn('exam-\nple',span['text'])

    def test_interleaved_introduction_is_left_for_review(self):
        text='Abstract\n'+('Source text. '*30)+'\nIntroduction\nDifferent section.\nKeywords Rice'
        span,reason=abstract_span(text)
        self.assertIsNone(span)
        self.assertEqual(reason,'possible_interleaved_text_requires_review')

    def test_cross_page_abstract_without_boundary_is_left_for_review(self):
        span,reason=abstract_span('Abstract\n'+('Unfinished source text. '*30))
        self.assertIsNone(span)
        self.assertEqual(reason,'no_keyword_boundary_on_first_page')


if __name__=='__main__': unittest.main()
