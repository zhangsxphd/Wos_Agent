"""Offline OpenAlex batch regression tests; no live keys or external requests."""

import io
import json
import os
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

import requests

from scripts.enrich_abstracts import enrich_file, enrich_record
from scripts.pipeline_utils import known_secrets, write_jsonl
from scripts.providers import CrossrefClient, OpenAlexClient
from scripts.providers.base_client import ProviderError, cache_key
from scripts.providers.openalex_client import RATE_LIMIT_HEADERS, SELECT_FIELDS, reconstruct_abstract
from test_v03 import OfflineCase, TEXT, TITLE, crossref_raw, providers, response, source_record


def doi(index):
    return f"10.2000/rice{index}"


def work(identifier, abstract=TEXT):
    index = None
    if abstract:
        index = {}
        for position, word in enumerate(abstract.split()):
            index.setdefault(word, []).append(position)
    return {"id": "https://openalex.org/W" + identifier.rsplit("/", 1)[-1],
            "doi": "https://doi.org/" + identifier, "title": TITLE,
            "publication_year": 2025, "abstract_inverted_index": index}


def batch(items, count=None):
    return {"meta": {"count": len(items) if count is None else count}, "results": items}


class OpenAlexBatchTests(OfflineCase):
    def oa(self, replies, **changes):
        return self.client(OpenAlexClient, replies=replies, **changes)

    def test_ten_dois_one_http_request(self):
        ids = [doi(i) for i in range(10)]
        client = self.oa([response(batch([work(d) for d in reversed(ids)]))])
        result = client.get_many_by_doi(ids)
        self.assertEqual(client.stats["requests"], 1)
        self.assertEqual(list(result), ids)
        self.assertTrue(all(result[d]["doi"] == d for d in ids))
        args, kwargs = client.session.get.call_args
        self.assertEqual(args[0], "https://api.openalex.org/works")
        self.assertEqual(kwargs["params"], {"filter": "doi:" + "|".join("https://doi.org/" + d for d in ids),
                                            "per_page": 100, "select": SELECT_FIELDS})

    def test_hundred_dois_one_http_request(self):
        ids = [doi(i) for i in range(100)]
        client = self.oa([response(batch([work(d) for d in ids]))])
        values = client.get_many_by_doi(ids)
        self.assertEqual(len(values), 100)
        self.assertEqual(client.session.get.call_count, 1)

    def test_hundred_and_one_dois_two_http_requests(self):
        ids = [doi(i) for i in range(101)]
        client = self.oa([response(batch([work(d) for d in ids[:100]])), response(batch([work(ids[100])]))])
        values = client.get_many_by_doi(ids)
        self.assertEqual(len(values), 101)
        self.assertEqual(client.stats["requests"], 2)
        sizes = [len(call.kwargs["params"]["filter"][4:].split("|")) for call in client.session.get.call_args_list]
        self.assertEqual(sizes, [100, 1])

    def test_partial_response_maps_by_doi_and_missing_is_negative(self):
        ids = [doi(i) for i in range(3)]
        client = self.oa([response(batch([work(ids[2]), work(ids[0])]))])
        values = client.get_many_by_doi(ids)
        self.assertEqual(values[ids[0]]["doi"], ids[0])
        self.assertEqual(values[ids[2]]["doi"], ids[2])
        self.assertIsNone(values[ids[1]])
        cache = json.loads(client._cache_path(ids[1]).read_text())
        self.assertEqual(cache["status"], "not_found")
        self.assertIn("complete", cache["not_found_evidence"])

    def test_doi_normalization_deduplicates_batch_and_response_keys(self):
        record = work(doi(1))
        record["doi"] = record["doi"].upper()
        client = self.oa([response(batch([record]))])
        values = client.get_many_by_doi([" DOI:" + doi(1).upper() + " ", "https://dx.doi.org/" + doi(1), doi(1)])
        self.assertEqual(list(values), [doi(1)])
        self.assertEqual(client.session.get.call_count, 1)
        self.assertEqual(client.session.get.call_args.kwargs["params"]["filter"], "doi:https://doi.org/" + doi(1))

    def test_inverted_index_reconstruction_is_deterministic(self):
        self.assertEqual(reconstruct_abstract({"water": [1], "rice": [2, 0]}), "rice water rice")
        client = self.oa([response(batch([work(doi(1))]))])
        self.assertEqual(client.get_many_by_doi([doi(1)])[doi(1)]["abstract"], TEXT)

    def test_http_200_empty_abstract_is_no_abstract_not_not_found(self):
        client = self.oa([response(batch([work(doi(1), abstract=None)]))])
        values = client.get_many_by_doi([doi(1)])
        self.assertIsNotNone(values[doi(1)])
        self.assertIsNone(values[doi(1)]["abstract"])
        self.assertEqual(client.lookup_metadata[doi(1)]["status"], "no_abstract")
        self.assertEqual(json.loads(client._cache_path(doi(1)).read_text())["status"], "no_abstract")
        self.assertEqual(client.stats["not_found"], 0)
        self.assertEqual(client.stats["no_abstract"], 1)

    def test_429_never_negative_cached_and_next_lookup_can_retry(self):
        client = self.oa([response({}, 429), response(batch([work(doi(1))]))], retries=0)
        first = client.get_many_by_doi([doi(1)])
        self.assertIsInstance(first[doi(1)], ProviderError)
        self.assertEqual(first[doi(1)].code, "rate_limited")
        self.assertFalse(client._cache_path(doi(1)).exists())
        self.assertEqual(list(client.cache_dir.glob("batches/*.json")), [])
        self.assertIsNotNone(client.get_many_by_doi([doi(1)])[doi(1)]["abstract"])
        self.assertEqual(client.stats["requests"], 2)

    def test_timeout_and_5xx_never_negative_cached(self):
        for index, error in enumerate((requests.Timeout("request details omitted"), response({}, 503))):
            identifier = doi(index)
            client = self.oa([error, response(batch([work(identifier)]))], retries=0)
            self.assertIsInstance(client.get_many_by_doi([identifier])[identifier], ProviderError)
            self.assertFalse(client._cache_path(identifier).exists())
            self.assertIsNotNone(client.get_many_by_doi([identifier])[identifier])

    def test_batch_429_and_5xx_retry_backoff(self):
        client = self.oa([response({}, 429, {"Retry-After": "3"}), response({}, 502), response(batch([work(doi(1))]))])
        self.assertIsNotNone(client.get_many_by_doi([doi(1)])[doi(1)])
        self.assertEqual(client.stats["requests"], 3)
        self.assertTrue(any(call.args == (3.0,) for call in client.sleep.call_args_list))

    def test_cache_hits_excluded_from_batch_request(self):
        client = self.oa([response(batch([work(doi(1))])), response(batch([work(doi(2))]))])
        client.get_many_by_doi([doi(1)])
        values = client.get_many_by_doi([doi(1), doi(2)])
        self.assertEqual(client.stats["cache_hits"], 1)
        self.assertEqual(client.session.get.call_args.kwargs["params"]["filter"], "doi:https://doi.org/" + doi(2))
        self.assertEqual(values[doi(1)]["doi"], doi(1))

    def test_all_cache_hits_zero_requests_including_no_abstract_and_negative(self):
        first = self.oa([response(batch([work(doi(1)), work(doi(2), abstract=None)]))])
        first.get_many_by_doi([doi(1), doi(2), doi(3)])
        replay = self.oa([response({})], cache_only=True)
        values = replay.get_many_by_doi([doi(1), doi(2), doi(3)])
        replay.session.get.assert_not_called()
        self.assertEqual(replay.stats["cache_hits"], 3)
        self.assertIsNone(values[doi(3)])
        self.assertEqual(replay.lookup_metadata[doi(2)]["status"], "no_abstract")

    def test_refresh_bypasses_existing_cache(self):
        first = self.oa([response(batch([work(doi(1), abstract=None)]))])
        first.get_many_by_doi([doi(1)])
        refreshed = self.oa([response(batch([work(doi(1))]))])
        self.assertEqual(refreshed.get_many_by_doi([doi(1)], refresh=True)[doi(1)]["abstract"], TEXT)
        self.assertEqual(refreshed.stats["cache_hits"], 0)

    def test_existing_v03_singleton_cache_still_usable(self):
        legacy = self.oa([response(work(doi(1)))])
        legacy.get_by_doi(doi(1))
        client = self.oa([response({})])
        self.assertEqual(client.get_many_by_doi([doi(1)])[doi(1)]["abstract"], TEXT)
        client.session.get.assert_not_called()

    def test_incomplete_response_does_not_negative_cache_missing_doi(self):
        client = self.oa([response(batch([work(doi(1))], count=2))])
        values = client.get_many_by_doi([doi(1), doi(2)])
        self.assertEqual(values[doi(1)]["doi"], doi(1))
        self.assertIsInstance(values[doi(2)], ProviderError)
        self.assertFalse(client._cache_path(doi(2)).exists())

    def test_malformed_200_does_not_claim_not_found(self):
        client = self.oa([response({"error": "bad response"})])
        values = client.get_many_by_doi([doi(1)])
        self.assertIsInstance(values[doi(1)], ProviderError)
        self.assertFalse(client._cache_path(doi(1)).exists())

    def test_batch_http_404_negative_cache(self):
        client = self.oa([response({"error": "not found"}, 404)])
        self.assertIsNone(client.get_many_by_doi([doi(1)])[doi(1)])
        self.assertEqual(json.loads(client._cache_path(doi(1)).read_text())["http_status"], 404)

    def test_rate_limit_header_whitelist_and_cache_capture(self):
        headers = dict(zip(RATE_LIMIT_HEADERS, ["1000", "999", "1", "86400"]))
        headers["Authorization"] = "irrelevant-header-not-recorded"
        client = self.oa([response(batch([work(doi(1))]), headers=headers)])
        client.get_many_by_doi([doi(1)])
        saved = client.rate_limit_history[0]["headers"]
        self.assertEqual(set(saved), set(RATE_LIMIT_HEADERS))
        self.assertEqual(saved["X-RateLimit-Remaining"], "999")
        self.assertEqual(json.loads(client._cache_path(doi(1)).read_text())["rate_limit_headers"], saved)

    def test_api_key_not_in_success_cache_response_log_or_query_params(self):
        secret = "sentinel-" + uuid.uuid4().hex
        with patch.dict(os.environ, {"OPENALEX_API_KEY": secret}):
            client = self.oa([response(batch([work(doi(1))]))])
            captured = io.StringIO()
            with redirect_stdout(captured):
                values = client.get_many_by_doi([doi(1)])
        self.assertEqual(client.session.headers["Authorization"], "Bearer " + secret)
        self.assertNotIn("api_key", client.session.get.call_args.kwargs["params"])
        text = json.dumps(values) + captured.getvalue() + json.dumps(client.rate_limit_history)
        for path in client.cache_dir.rglob("*.json"):
            text += path.read_text()
        self.assertNotIn(secret, text)

    def test_key_echo_in_response_or_rate_headers_rejected_without_cache(self):
        secret = "sentinel-" + uuid.uuid4().hex
        with patch.dict(os.environ, {"OPENALEX_API_KEY": secret}):
            for index, reply in enumerate((response({"results": [], "echo": secret}),
                                           response(batch([]), headers={"X-RateLimit-Limit": secret}))):
                client = self.oa([reply])
                values = client.get_many_by_doi([doi(index)])
                self.assertIsInstance(values[doi(index)], ProviderError)
                self.assertNotIn(secret, str(values[doi(index)]))
                self.assertFalse(client._cache_path(doi(index)).exists())

    def test_skipped_semantic_scholar_never_called_or_marks_missing_as_error(self):
        clients = providers({"semantic_scholar": ProviderError("semantic_scholar", "must_not_call")})
        record = enrich_record(source_record(), clients, skip_providers=("semantic_scholar",))
        self.assertEqual(record["abstract_enrichment"]["provider_results"]["semantic_scholar"]["status"], "skipped")
        self.assertEqual(clients["semantic_scholar"].calls, [])
        self.assertEqual(record["abstract_enrichment"]["status"], "missing")

    def test_pipeline_refresh_only_openalex_skips_s2_and_uses_crossref_cache(self):
        ids = [doi(i) for i in range(10)]
        cached = self.client(CrossrefClient, [response(crossref_raw(DOI=d)) for d in ids])
        for d in ids:
            cached.get_by_doi(d)
        crossref = self.client(CrossrefClient, [response({})], cache_only=True)
        oa = self.oa([response(batch([work(d) for d in ids]))], refresh=True)
        source = self.root / "input.jsonl"
        write_jsonl(source, [source_record(uid=f"WOS:{i}", doi=d) for i, d in enumerate(ids)])
        with patch("scripts.enrich_abstracts.CrossrefClient", return_value=crossref) as cr_ctor, \
             patch("scripts.enrich_abstracts.OpenAlexClient", return_value=oa) as oa_ctor, \
             patch("scripts.enrich_abstracts.SemanticScholarClient") as s2_ctor, redirect_stdout(io.StringIO()):
            report = enrich_file(source, "output.jsonl", root=self.root, refresh_providers=("openalex",), cache_only_providers=("crossref",))
        self.assertFalse(cr_ctor.call_args.kwargs["refresh"])
        self.assertTrue(oa_ctor.call_args.kwargs["refresh"])
        self.assertEqual(report["actual_http_request_count"], 1)
        self.assertEqual(report["provider_statistics"]["crossref"]["cache_hits"], 10)
        self.assertEqual(report["provider_statistics"]["semantic_scholar"]["status"], "skipped")
        self.assertEqual(report["found_by_openalex"], 10)
        self.assertEqual(report["unresolved"], 0)
        s2_ctor.assert_not_called()
        crossref.session.get.assert_not_called()
        output = (self.root / "output.jsonl").read_text()
        for secret in known_secrets(self.root):
            self.assertNotIn(secret, output)
