import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.fulltext.resolver import FulltextResolver, verify_pdf_identity
from scripts.fulltext.provenance import FulltextError


class FulltextResolverIdentityTests(unittest.TestCase):
    def test_typographic_hyphens_and_digit_font_names_are_decoded(self):
        examples=[('10.1186/s12284‑022‑00564‑6','10.1186/s12284-022-00564-6'),
                  ('/one.tnum/zero.tnum./three.tnum/three.tnum/eight.tnum/nine.tnum/fmicb./two.tnum/zero.tnum/two.tnum/three.tnum./one.tnum/two.tnum/one.tnum/zero.tnum/nine.tnum/three.tnum/eight.tnum',
                   '10.3389/fmicb.2023.1210938')]
        for text,doi in examples:
            with self.subTest(doi=doi):
                verify_pdf_identity({'sections':[{'paragraphs':[{'text':'DOI '+text}]}]}, {'doi':doi})

    def test_doi_must_match_completely(self):
        parsed = {'sections': [{'paragraphs': [{'text': 'DOI 10.1234/right-extra'}]}]}
        with self.assertRaises(FulltextError):
            verify_pdf_identity(parsed, {'doi': '10.1234/right'})
        verify_pdf_identity(parsed, {'doi': '10.1234/right-extra'})

    def test_unrelated_pdf_is_not_promoted_to_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock(secrets=())
            client.get.return_value = (b'%PDF-mocked', 'https://publisher.example/a.pdf')
            work = {'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/10.1234/right',
                    'best_oa_location': {'is_oa': True, 'pdf_url': 'https://publisher.example/a.pdf'}}
            parsed = {'parser': 'mock', 'body_paragraph_count': 1,
                      'sections': [{'paragraphs': [{'text': 'Other DOI 10.1234/wrong'}]}]}
            with patch('scripts.fulltext.resolver.parse_pdf', return_value=parsed):
                result = FulltextResolver(Path(tmp), client).resolve({'doi': '10.1234/right'}, work)
            self.assertNotEqual(result['status'], 'available')
            self.assertEqual(result['status'], 'needs_review')
            self.assertTrue((Path(tmp) / result['raw_file']).is_file())


if __name__ == '__main__':
    unittest.main()
