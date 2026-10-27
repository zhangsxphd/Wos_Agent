"""An anomalous 200 response is not evidence that a DOI is missing."""

import json
from scripts.providers import CrossrefClient, OpenAlexClient
from scripts.providers.base_client import ProviderError
from test_v03 import DOI, OfflineCase, crossref_raw, response
from test_openalex_batch import doi, work


class ProviderResponseSafetyTests(OfflineCase):
    def test_openalex_negative_replay_requires_original_intact_batch(self):
        first = self.client(OpenAlexClient, replies=[response({'meta': {'count': 0}, 'results': []})])
        self.assertIsNone(first.get_many_by_doi([doi(0)])[doi(0)])
        entry = json.loads(first._cache_path(doi(0)).read_text())
        archive = self.root / entry['batch_response_file']
        archive.unlink()
        replay = self.client(OpenAlexClient, cache_only=True)
        self.assertIsInstance(replay.get_many_by_doi([doi(0)])[doi(0)], ProviderError)

    def test_crossref_empty_success_is_error_and_never_negative_cached(self):
        client = self.client(CrossrefClient, replies=[response({'message': {}})])
        with self.assertRaises(ProviderError):
            client.get_by_doi(DOI)
        self.assertFalse(list(client.cache_dir.glob('*.json')))
        self.assertEqual(client.stats['not_found'], 0)

    def test_crossref_mismatched_doi_is_not_success_cached(self):
        client = self.client(CrossrefClient, replies=[response(crossref_raw(DOI='10.1000/other'))])
        with self.assertRaisesRegex(ProviderError, 'doi_mismatch'):
            client.get_by_doi(DOI)
        self.assertFalse(list(client.cache_dir.glob('*.json')))

    def test_openalex_singleton_mismatched_doi_is_not_success_cached(self):
        client = self.client(OpenAlexClient, replies=[response(work(doi(1)))])
        with self.assertRaisesRegex(ProviderError, 'doi_mismatch'):
            client.get_by_doi(doi(0))
        self.assertFalse(list(client.cache_dir.glob('*.json')))

    def test_openalex_missing_count_cannot_prove_absence(self):
        ids = [doi(0), doi(1)]
        client = self.client(OpenAlexClient, replies=[response({'results':[work(ids[0])]})])
        values = client.get_many_by_doi(ids)
        self.assertEqual(values[ids[0]]['doi'], ids[0])
        self.assertIsInstance(values[ids[1]], ProviderError)
        self.assertFalse(client._cache_path(ids[1]).exists())

    def test_openalex_inconsistent_count_cannot_prove_absence(self):
        ids = [doi(0), doi(1)]
        client = self.client(OpenAlexClient, replies=[response({'meta':{'count':0}, 'results':[work(ids[0])]})])
        values = client.get_many_by_doi(ids)
        self.assertIsInstance(values[ids[1]], ProviderError)
        self.assertFalse(client._cache_path(ids[1]).exists())

    def test_openalex_batch_404_does_not_claim_papers_absent(self):
        client = self.client(OpenAlexClient, replies=[response({'error':'endpoint not found'}, 404)])
        result = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsInstance(result, ProviderError)
        self.assertEqual(client.stats['not_found'], 0)
        self.assertFalse(client._cache_path(doi(0)).exists())

    def test_openalex_bad_inverted_index_is_parse_error_not_missing_abstract(self):
        raw = work(doi(0)); raw['abstract_inverted_index'] = {'first':[0], 'third':[2]}
        client = self.client(OpenAlexClient, replies=[response({'meta':{'count':1}, 'results':[raw]})])
        result = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsNone(result['abstract'])
        self.assertEqual(client.lookup_metadata[doi(0)]['status'], 'parse_error')
        self.assertEqual(client.stats['no_abstract'], 0)
        self.assertEqual(__import__('json').loads(client._cache_path(doi(0)).read_text())['status'], 'parse_error')

    def test_crossref_record_without_abstract_reports_no_abstract(self):
        client = self.client(CrossrefClient, replies=[response(crossref_raw(abstract=None))])
        result = client.get_by_doi(DOI)
        self.assertIsNone(result['abstract'])
        self.assertEqual(client.last_lookup['status'], 'no_abstract')

    def test_crossref_found_record_reports_found_on_cache_replay(self):
        first = self.client(CrossrefClient, replies=[response(crossref_raw())])
        first.get_by_doi(DOI)
        second = self.client(CrossrefClient, cache_only=True)
        second.get_by_doi(DOI)
        self.assertEqual(second.last_lookup['status'], 'found')
