import gzip
import unittest

from scripts.fulltext.provenance import FulltextError
from scripts.fulltext.tei_parser import parse_tei, tei_identity


def tei(header_dois=(), body_doi='', reference_doi='', body=True, header=True):
    ids = ''.join('<idno type="DOI">'+value+'</idno>' for value in header_dois)
    head = '<teiHeader><fileDesc><sourceDesc><biblStruct>'+ids+'</biblStruct></sourceDesc></fileDesc></teiHeader>' if header else ''
    return ('<TEI xmlns="http://www.tei-c.org/ns/1.0">'+head+'<text><body>'+
            ('<div><head>Methods</head><p>Rice '+body_doi+'</p></div>' if body else '')+
            '</body><back><listBibl><biblStruct><idno type="DOI">'+reference_doi+
            '</idno></biblStruct></listBibl></back></text></TEI>').encode()


class TeiIdentityTests(unittest.TestCase):
    def test_exact_normalized_header_variants(self):
        for value in ('10.1234/example', 'DOI: 10.1234/EXAMPLE', 'https://doi.org/10.1234/example',
                      'http://dx.doi.org/10.1234/example'):
            with self.subTest(value=value):
                self.assertEqual(tei_identity(tei([value]), '10.1234/example')['status'], 'doi_match')

    def test_reference_and_body_dois_do_not_identify_article(self):
        raw=tei(body_doi='10.1234/example',reference_doi='10.1234/example')
        self.assertEqual(tei_identity(raw,'10.1234/example')['status'],'header_doi_missing')

    def test_mismatch_conflicts_and_absent_header(self):
        for raw,expected in [(tei(['10.1234/example-extra']),'doi_mismatch'),
                             (tei(['10.1234/example','10.1234/other']),'conflicting_header_dois'),
                             (tei(header=False),'header_missing')]:
            self.assertEqual(tei_identity(raw,'10.1234/example')['status'],expected)

    def test_gzip_no_body_bad_xml_and_unsafe_xml(self):
        raw=tei(['10.1234/example'],body=False)
        self.assertEqual(tei_identity(gzip.compress(raw),'10.1234/example')['status'],'doi_match')
        self.assertEqual(parse_tei(raw)['body_paragraph_count'],0)
        for raw in (b'<TEI>', b'<!DOCTYPE TEI [<!ENTITY x "x">]><TEI/>', b'\x1f\x8bbad'):
            with self.assertRaises(FulltextError): parse_tei(raw)

    def test_gzip_expansion_is_bounded(self):
        raw=gzip.compress(b'X'*30_000_001)
        with self.assertRaisesRegex(FulltextError,'unsafe_xml'): parse_tei(raw)


if __name__ == '__main__': unittest.main()
