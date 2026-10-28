"""Fulltext evidence must not bypass validation at the screening CLI."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.fulltext.evidence import empty_fulltext
from scripts.pipeline_utils import ROOT, write_jsonl
from scripts.screening_profile import screen_file, classify, load_profile
from tests.test_v04 import source, valid


class ScreeningGateTests(unittest.TestCase):
    def test_nonempty_fulltext_without_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = source()
            evidence = {'uid': record['uid'], 'doi': record['doi'], 'title': record['title'],
                        'abstract_evidence': valid(record), 'fulltext_evidence': empty_fulltext()}
            evidence['fulltext_evidence']['study_system']['soil_type'] = ['saline soil']
            write_jsonl(root/'records.jsonl', [record]); write_jsonl(root/'evidence.jsonl', [evidence])
            with patch('scripts.screening_profile.ROOT', root):
                with self.assertRaises(ValueError):
                    screen_file(root/'records.jsonl', root/'evidence.jsonl',
                                ROOT/'profiles/saline_paddy.yaml', root/'out.jsonl')
            self.assertFalse((root/'out.jsonl').exists())

    def test_direct_classification_rejects_unchecked_fulltext(self):
        record=source(); row={'abstract_evidence':valid(record),'fulltext_evidence':empty_fulltext()}
        row['fulltext_evidence']['study_system']['soil_type']=['saline soil']
        with self.assertRaises(ValueError):
            classify(record,row,load_profile(ROOT/'profiles/saline_paddy.yaml'))


if __name__ == '__main__':
    unittest.main()
