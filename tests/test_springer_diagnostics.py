import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality.springer_diagnostics import diagnose,layout_hint


class SpringerDiagnosticsTests(unittest.TestCase):
    def test_candidate_publisher_snapshot_is_never_human_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=b'%PDF-test';(root/'paper.pdf').write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
            (root/'parsed.json').write_text(json.dumps({'parser':'fixture','sections':[{'paragraphs':[{'text':'Abstract Inline text\nNo keyword.'}]}]}))
            rec={'uid':'U1','doi':'10.1007/example','title':'Paper','abstract':None}
            q={'uid':'U1','doi':rec['doi']};manifest={'uid':'U1','raw_file':'paper.pdf','parsed_file':'parsed.json','sha256':digest}
            pub={'uid':'U1','doi':rec['doi'],'abstract':'Inline text','source_url':'https://link.springer.com/article/10.1007/example'}
            row=diagnose(rec,q,manifest,{'status':'readable','pages':[]},pub,'snapshot-hash',root)
            self.assertEqual(row['candidate_abstract'],'Inline text')
            self.assertEqual(row['human_review_status'],'pending');self.assertEqual(row['review_decision'],'')
            self.assertIsNone(row['pdf_exact_span'])
            self.assertEqual(row['abstract_heading_style'],'inline')

    def test_layout_hint_does_not_claim_single_column_without_evidence(self):
        self.assertEqual(layout_hint({'width':600,'words':[]}), 'not_established')


if __name__=='__main__':unittest.main()
