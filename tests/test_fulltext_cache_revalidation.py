import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.fulltext.resolver import FulltextResolver
from scripts.pipeline_utils import write_json
from tests.test_tei_identity import tei


class FulltextCacheRevalidationTests(unittest.TestCase):
    def test_old_tei_available_does_not_imply_article_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); record={'uid':'U1','doi':'10.1234/example'}
            raw=tei(reference_doi=record['doi']); sha=hashlib.sha256(raw).hexdigest()
            raw_path=root/'data/fulltext/raw/legacy.xml';raw_path.parent.mkdir(parents=True);raw_path.write_bytes(raw)
            key=hashlib.sha256(record['doi'].encode()).hexdigest()
            old={'uid':'U1','doi':record['doi'],'status':'available','format':'tei_xml',
                 'raw_file':str(raw_path.relative_to(root)),'sha256':sha,'identity_check':'doi_match'}
            path=root/'data/fulltext/manifests'/key/'old.json';write_json(path,old)
            client=Mock(secrets=())
            result=FulltextResolver(root,client).resolve(record)
            self.assertEqual(result['status'],'needs_review')
            self.assertEqual(result['identity_status'],'header_doi_missing')
            self.assertEqual(json.loads(path.read_text()),old)
            client.get.assert_not_called()

    def test_new_unidentified_tei_is_retained_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record={'uid':'U1','doi':'10.1234/example'}
            raw=tei(reference_doi=record['doi'])
            client=Mock(secrets=());client.get.return_value=(raw,'https://content.openalex.org/works/W1.grobid-xml')
            work={'id':'https://openalex.org/W1','doi':'https://doi.org/'+record['doi'],
                  'has_content':{'grobid_xml':True},
                  'content_urls':{'grobid_xml':'https://content.openalex.org/works/W1.grobid-xml'}}
            result=FulltextResolver(root,client).resolve(record,work)
            self.assertEqual(result['status'],'needs_review')
            self.assertEqual(result['identity_status'],'header_doi_missing')
            self.assertEqual((root/result['raw_file']).read_bytes(),raw)
            self.assertTrue(result['parsed_sha256'])


if __name__=='__main__': unittest.main()
