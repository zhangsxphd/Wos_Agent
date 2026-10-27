import unittest

from scripts.merge_searches import merge_records
from scripts.normalize_record import deduplicate_records


class DedupConflictTests(unittest.TestCase):
    def test_shared_uid_different_dois_are_never_merged(self):
        records = [{'uid': 'WOS:1', 'doi': '10.1234/a', 'title': 'A'},
                   {'uid': 'wos:1', 'doi': '10.1234/b', 'title': 'B'},
                   {'uid': 'WOS:1', 'doi': None, 'title': 'Unknown'}]
        for operation in (merge_records, deduplicate_records):
            with self.subTest(operation=operation.__name__):
                result = operation(records)
                self.assertEqual(len(result), 3)
                self.assertEqual({r.get('doi') for r in result}, {'10.1234/a', '10.1234/b', None})
                self.assertTrue(all(r['dedup_conflicts'][0]['kind'] == 'uid_multiple_dois' for r in result))

    def test_same_doi_still_merges_when_a_uid_is_conflicted(self):
        records = [{'uid': 'WOS:1', 'doi': '10.1234/a'},
                   {'uid': 'WOS:1', 'doi': '10.1234/b'},
                   {'uid': 'WOS:2', 'doi': '10.1234/a'}]
        for operation in (merge_records, deduplicate_records):
            with self.subTest(operation=operation.__name__):
                result = operation(records)
                self.assertEqual(len(result), 2)
                self.assertEqual({r['doi'] for r in result}, {'10.1234/a', '10.1234/b'})


if __name__ == '__main__':
    unittest.main()
