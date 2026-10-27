"""Acquisition budgets, institutional provenance and checkpoint regressions."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.acquire_fulltexts import acquire, import_browser_pdf
from scripts.fulltext.provenance import FulltextError
from scripts.fulltext.resolver import FulltextResolver, candidates
from scripts.pipeline_utils import read_jsonl, write_jsonl


def work():
    locations=[{'is_oa':True,'pdf_url':f'https://example.org/paper-{i}.pdf','landing_page_url':None} for i in range(5)]
    return {'id':'https://openalex.org/W123','doi':'https://doi.org/10.1234/test',
            'has_content':{'pdf':True},'content_urls':{'pdf':'https://content.openalex.org/works/W123.pdf'},
            'open_access':{'is_oa':True,'oa_status':'gold'},'locations':locations}


class AcquisitionTests(unittest.TestCase):
    def test_browser_import_rejects_paths_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root=Path(tmp); secret=Path(outside)/'paper.pdf'; secret.write_bytes(b'%PDF-mocked')
            (root/'linked.pdf').symlink_to(secret)
            parsed={'parser':'mock-parser','pages':1,'body_paragraph_count':1,
                    'sections':[{'paragraphs':[{'text':'10.1234/test'}]}]}
            with patch('scripts.acquire_fulltexts.parse_pdf',return_value=parsed):
                for filename in (str(secret), '../'+Path(outside).name+'/paper.pdf', 'linked.pdf'):
                    with self.subTest(filename=filename):
                        with self.assertRaises(FulltextError):
                            import_browser_pdf({'uid':'U1','doi':'10.1234/test'},
                                {'file':filename,'source_url':'https://publisher.example/paper'},root)

    def test_archive_skip_retains_all_explicit_oa_locations(self):
        choices=candidates(work(),include_archive=False)
        self.assertEqual(len(choices),5)
        self.assertTrue(all(c['source']=='oa_location' for c in choices))

    def test_candidate_budget_is_reported_as_incomplete_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=Mock();client.secrets=();client.get.side_effect=FulltextError('access_denied',403)
            resolver=FulltextResolver(tmp,client,include_archive=False,max_candidates=2)
            result=resolver.resolve({'uid':'U1','doi':'10.1234/test'},work=work())
            self.assertEqual(client.get.call_count,2)
            self.assertEqual(result['reason'],'candidate_budget_reached')
            self.assertEqual(result['candidate_count'],5)
            self.assertEqual(result['candidate_budget'],2)

    def test_local_pdf_wrong_identity_is_rejected_before_archiving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'paper.pdf').write_bytes(b'%PDF-mocked')
            parsed={'pages':2,'body_paragraph_count':2,'sections':[{'paragraphs':[{'text':'An unrelated DOI 10.1234/other'}]}]}
            with patch('scripts.acquire_fulltexts.parse_pdf',return_value=parsed):
                with self.assertRaisesRegex(FulltextError,'identity_requires_review'):
                    import_browser_pdf({'doi':'10.1234/test'},{'file':'paper.pdf','source_url':'https://publisher.example/paper'},root)
            self.assertFalse((root/'data/fulltext/raw').exists())

    def test_institutional_pdf_keeps_license_unknown_and_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=b'%PDF-mocked';(root/'paper.pdf').write_bytes(raw)
            parsed={'parser':'mock-parser','pages':2,'body_paragraph_count':2,'sections':[{'paragraphs':[{'text':'Primary article 10.1234/test'}]}]}
            with patch('scripts.acquire_fulltexts.parse_pdf',return_value=parsed):
                result=import_browser_pdf({'uid':'U1','doi':'10.1234/test'},{'file':'paper.pdf','source_url':'https://publisher.example/paper'},root)
            self.assertEqual(result['source'],'campus_browser')
            self.assertIsNone(result['license'])
            self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['identity_check'],'doi_on_first_page')
            self.assertTrue((root/result['raw_file']).exists())

    def test_printed_pdf_preserves_acquisition_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'paper.pdf').write_bytes(b'%PDF-mocked')
            parsed={'parser':'mock-parser','pages':15,'body_paragraph_count':1,
                    'sections':[{'paragraphs':[{'text':'Primary article 10.1234/test'}]}]}
            with patch('scripts.acquire_fulltexts.parse_pdf',return_value=parsed):
                result=import_browser_pdf({'uid':'U1','doi':'10.1234/test'},
                    {'file':'paper.pdf','source_url':'https://publisher.example/paper',
                     'acquisition_method':'browser_print_to_pdf'},root)
            saved=json.loads((root/result['manifest_file']).read_text())
            self.assertEqual(saved['acquisition_method'],'browser_print_to_pdf')
            self.assertIsNone(saved['license'])

    def test_checkpoint_order_queue_and_source_immutability(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root=Path(tmp)
            records=[{'uid':'U1','doi':'10.1234/one','title':'One','abstract':'Available'},
                     {'uid':'U2','doi':'10.1234/two','title':'Two','abstract':None}]
            metadata=[{'uid':r['uid'],'doi':r['doi'],'work':None} for r in records]
            write_jsonl(root/'input.jsonl',records);write_jsonl(root/'metadata.jsonl',metadata)
            before=(root/'input.jsonl').read_bytes()
            client=Mock();client.stats={'requests':0}
            result=acquire('input.jsonl','metadata.jsonl','run',root=root,client=client,skip_archive=True,limit=1)
            self.assertEqual(result['attempted_records'],1)
            self.assertEqual(result['downloaded'],0)
            self.assertEqual(read_jsonl(root/'run/checkpoint.jsonl')[0]['uid'],'U2')
            rows=read_jsonl(root/'run/fulltexts.jsonl')
            self.assertEqual([r['uid'] for r in rows],['U1','U2'])
            self.assertEqual(rows[0]['status'],'pending')
            self.assertEqual((root/'input.jsonl').read_bytes(),before)
            self.assertTrue((root/'run/browser_queue.csv').is_file())

    def test_work_doi_mismatch_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_jsonl(root/'input.jsonl',[{'uid':'U1','doi':'10.1234/test'}])
            write_jsonl(root/'metadata.jsonl',[{'uid':'U1','doi':'10.1234/test','work':{'doi':'https://doi.org/10.1234/wrong'}}])
            with self.assertRaisesRegex(ValueError,'Work DOI differs'):
                acquire('input.jsonl','metadata.jsonl','run',root=root,client=Mock())
            self.assertFalse((root/'run').exists())


if __name__=='__main__': unittest.main()
