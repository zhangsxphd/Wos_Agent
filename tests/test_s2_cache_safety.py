"""Regression tests for incomplete batch responses and damaged DOI caches."""

import json
from unittest.mock import patch

from scripts.providers.base_client import ProviderError
from test_s2_batch import S2BatchCase, doi, paper, response


class SemanticScholarCacheSafetyTests(S2BatchCase):
    def test_invalid_batch_lengths_never_create_negative_cache(self):
        ids = [doi(0), doi(1), doi(2)]
        for raw in ([], [paper(ids[0]), None], [paper(x) for x in ids] + [None]):
            with self.subTest(length=len(raw)):
                client = self.s2([response(raw)])
                result = client.get_many_by_doi(ids)
                self.assertTrue(all(isinstance(result[x], ProviderError) for x in ids))
                self.assertEqual(client.stats['not_found'], 0)
                self.assertFalse(list(client.cache_dir.glob('*.json')))

    def test_misaligned_batch_with_null_cannot_cache_not_found(self):
        ids = [doi(0), doi(1), doi(2)]
        client = self.s2([response([None, paper(ids[0]), paper(ids[2])])])
        result = client.get_many_by_doi(ids)
        self.assertTrue(all(isinstance(result[x], ProviderError) for x in ids))
        self.assertEqual(client.stats['not_found'], 0)
        self.assertFalse(list(client.cache_dir.glob('*.json')))

    def test_complete_aligned_batch_preserves_explicit_null(self):
        ids = [doi(0), doi(1), doi(2)]
        client = self.s2([response([paper(ids[0]), None, paper(ids[2])])])
        result = client.get_many_by_doi(ids)
        self.assertIsNone(result[ids[1]])
        self.assertEqual(result[ids[2]]['doi'], ids[2])
        self.assertEqual(json.loads(client._cache_path(ids[1]).read_text())['status'], 'not_found')
        self.assertEqual(client.lookup_metadata[ids[0]]['status'], 'found')

    def test_http_200_empty_single_response_is_error_without_negative_cache(self):
        client = self.s2([], get_replies=[response({})])
        with self.assertRaises(ProviderError):
            client.get_by_doi(doi(0))
        self.assertFalse(client._cache_path(doi(0)).exists())

    def test_invalid_cache_is_preserved_and_repaired_without_poisoning_batch(self):
        ids = [doi(0), doi(1)]
        client = self.s2([response([paper(x) for x in ids])])
        cache = client._cache_path(ids[0]); cache.parent.mkdir(parents=True)
        original = b'{"cache_version": 1, "old_raw": "retain this"}'
        cache.write_bytes(original)
        result = client.get_many_by_doi(ids)
        self.assertEqual([result[x]['doi'] for x in ids], ids)
        self.assertEqual(json.loads(cache.read_text())['status'], 'success')
        self.assertTrue(any(p.read_bytes() == original for p in (client.cache_dir/'quarantine').rglob('*') if p.is_file()))
        replay = self.s2([], cache_only=True).get_many_by_doi(ids)
        self.assertEqual([replay[x]['doi'] for x in ids], ids)

    def test_truncated_json_cache_does_not_abort_batch(self):
        ids = [doi(0), doi(1)]
        client = self.s2([response([paper(x) for x in ids])])
        cache = client._cache_path(ids[0]); cache.parent.mkdir(parents=True)
        cache.write_text('{"cache_version":')
        result = client.get_many_by_doi(ids)
        self.assertEqual([result[x]['doi'] for x in ids], ids)

    def test_failed_request_preserves_damaged_cache(self):
        client = self.s2([response([], 429)], retries=0)
        cache = client._cache_path(doi(0)); cache.parent.mkdir(parents=True)
        original = b'{"cache_version":'; cache.write_bytes(original)
        result = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsInstance(result, ProviderError)
        self.assertEqual(result.code, 'rate_limited')
        self.assertEqual(cache.read_bytes(), original)
        self.assertFalse((client.cache_dir/'quarantine').exists())

    def test_corrupt_cache_only_is_error_not_missing(self):
        client = self.s2([], cache_only=True)
        cache = client._cache_path(doi(0)); cache.parent.mkdir(parents=True)
        cache.write_text('{"cache_version":')
        result = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsInstance(result, ProviderError)
        self.assertEqual(result.code, 'invalid_cache')
        self.assertEqual(client.stats['not_found'], 0)
        client.session.post.assert_not_called()

    def test_legacy_200_negative_cache_is_not_treated_as_confirmed(self):
        client = self.s2([], cache_only=True)
        cache = client._cache_path(doi(0)); cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({'cache_version':2, 'provider':'semantic_scholar',
                                     'lookup_doi':doi(0), 'status':'not_found', 'http_status':200,
                                     'raw':None}))
        result = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsInstance(result, ProviderError)
        self.assertEqual(result.code, 'invalid_cache')
        self.assertEqual(client.stats['not_found'], 0)

    def test_missing_batch_archive_invalidates_its_negative_cache(self):
        first = self.s2([response([None])])
        self.assertIsNone(first.get_many_by_doi([doi(0)])[doi(0)])
        for archive in (first.cache_dir/'batches').glob('*.json'):
            archive.unlink()
        replay = self.s2([], cache_only=True).get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsInstance(replay, ProviderError)
        self.assertEqual(replay.code, 'invalid_cache')

    def test_long_retry_after_applies_cooldown_to_subsequent_batch(self):
        client = self.s2([response([], 429, {'Retry-After':'120'})], retries=1)
        client.clock = lambda: 0
        first = client.get_many_by_doi([doi(0)])[doi(0)]
        second = client.get_many_by_doi([doi(1)])[doi(1)]
        self.assertEqual(first.code, 'retry_after_exceeds_60s')
        self.assertEqual(second.code, 'provider_cooldown')
        self.assertEqual(client.session.post.call_count, 1)

    def test_refresh_preserves_previous_positive_cache(self):
        first = self.s2([response([paper(doi(0))])])
        first.get_many_by_doi([doi(0)])
        original = first._cache_path(doi(0)).read_bytes()
        newer = paper(doi(0), abstract='new abstract from refreshed source')
        refreshed = self.s2([response([newer])], refresh=True)
        self.assertEqual(refreshed.get_many_by_doi([doi(0)])[doi(0)]['abstract'], newer['abstract'])
        self.assertTrue(any(p.read_bytes() == original for p in (first.cache_dir/'history').rglob('*.bin')))

    def test_one_cache_write_failure_does_not_discard_other_result(self):
        ids = [doi(0), doi(1)]
        client = self.s2([response([paper(x) for x in ids])])
        write = client._write_cache
        def write_one(identifier, *args, **kwargs):
            if identifier == ids[0]:
                raise OSError('simulated write failure')
            return write(identifier, *args, **kwargs)
        with patch.object(client, '_write_cache', side_effect=write_one):
            result = client.get_many_by_doi(ids)
        self.assertIsInstance(result[ids[0]], ProviderError)
        self.assertEqual(result[ids[1]]['doi'], ids[1])
        self.assertTrue(client._cache_path(ids[1]).is_file())


if __name__ == '__main__':
    import unittest
    unittest.main()
