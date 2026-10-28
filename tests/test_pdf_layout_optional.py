import importlib.util
import io
import unittest

from pypdf import PdfWriter
from scripts.fulltext.pdf_metadata import parse_pdfplumber


@unittest.skipUnless(importlib.util.find_spec('pdfplumber'), 'optional PDF layout environment not installed')
class OptionalPdfLayoutTests(unittest.TestCase):
    def test_real_pdf_optional_parser_is_reproducible_and_records_versions(self):
        writer=PdfWriter();writer.add_blank_page(width=400,height=300)
        stream=io.BytesIO();writer.write(stream);raw=stream.getvalue()
        first=parse_pdfplumber(raw);second=parse_pdfplumber(raw)
        self.assertEqual(first,second);self.assertEqual(first['pages'],1)
        self.assertTrue(first['parser_environment']['pdfplumber'])
        self.assertTrue(first['parser_environment']['pdfminer.six'])
        self.assertTrue(first['reading_order_requires_review'])


if __name__=='__main__':unittest.main()
