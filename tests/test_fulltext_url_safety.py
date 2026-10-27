"""Signed download URLs must not become persistent provenance credentials."""

import unittest

from scripts.fulltext.provenance import FulltextError, safe_url


class FulltextUrlSafetyTests(unittest.TestCase):
    def test_signed_temporary_access_is_not_returned_as_provenance(self):
        for query in ('X-Amz-Security-Token=private', 'X-Amz-Signature=private',
                      'X-Goog-Signature=private', 'AWSAccessKeyId=private&Signature=private'):
            with self.subTest(query=query), self.assertRaises(FulltextError):
                safe_url('https://publisher.example/paper.pdf?' + query)

    def test_ordinary_public_identifiers_are_preserved(self):
        self.assertEqual(safe_url('https://publisher.example/paper.pdf?version=1'),
                         'https://publisher.example/paper.pdf?version=1')


if __name__ == '__main__':
    unittest.main()
