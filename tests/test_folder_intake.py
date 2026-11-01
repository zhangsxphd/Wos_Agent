import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('folder_intake',ROOT/'research_projects/awd_n2o_mvp/folder_intake.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class FolderIntakeTest(unittest.TestCase):
    def test_review_requires_matching_hash_page_and_original_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); src=root/'papers';src.mkdir();pdf=src/'paper.pdf';pdf.write_bytes(b'fixture')
            fake={'sections':[{'paragraphs':[{'text':'10.1234/example Three independent pots.'}]}],
                  'body_paragraph_count':1,'pages':1}
            review=root/'review.json'; review.write_text(json.dumps([{'pdf_sha256':hashlib.sha256(b'fixture').hexdigest(),
                'reviewer':'assistant','page':1,'quote':'invented phrase','field':'replicates','value':3}]))
            def fake_discover(paths,out): out.mkdir(parents=True,exist_ok=True)
            with patch.object(m,'parse_pdf',return_value=fake),patch.object(m,'verify_pdf_identity'):
                with self.assertRaisesRegex(ValueError,'原句未在所选页找到'):
                    m.run_folder(src,'soil',root/'out',fake_discover,review)
                row=json.loads(review.read_text());row[0]['quote']='Three independent pots.';review.write_text(json.dumps(row))
                result=m.run_folder(src,'soil',root/'out',fake_discover,review)
            self.assertEqual(result['quote_located'],1)
            evidence=json.loads((root/'out'/'normalized_evidence.json').read_text())
            self.assertEqual(evidence['comparisons'],[])
            self.assertEqual(evidence['source_observations'][0]['status'],'reviewer_assertion_quote_located_not_gold')
