"""Offline Semantic Scholar authenticated batch tests."""

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from scripts.enrich_abstracts import enrich_file, enrich_record
from scripts.providers import SemanticScholarClient
from scripts.providers.base_client import ProviderError
try:
    from test_v03 import OfflineCase, TEXT, TITLE, providers, source_record, response
except ModuleNotFoundError:
    from tests.test_v03 import OfflineCase, TEXT, TITLE, providers, source_record, response


def doi(index):
    return f"10.3000/saline{index}"


def paper(value, abstract=TEXT, **extra):
    result = {"paperId": "S2-" + value.rsplit("/", 1)[-1], "title": TITLE,
              "year": 2025, "abstract": abstract,
              "externalIds": {"DOI": value.upper()}, "url": "https://semanticscholar.org/paper/x",
              "isOpenAccess": True, "openAccessPdf": {"url": "https://example.org/a.pdf"}}
    result.update(extra)
    return result


class S2BatchCase(OfflineCase):
    def s2(self, replies, **changes):
        session = Mock(headers={})
        session.get.side_effect = changes.pop("get_replies", [response(paper("10.3000/saline0"))])
        session.post.side_effect = replies
        return SemanticScholarClient(root=self.root, session=session, sleep=Mock(),
                                     min_interval=0, **changes)


class SemanticScholarBatchTests(S2BatchCase):
    def test_key_uses_x_api_key_header(self):
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "s2-test-key"}):
            client = self.s2([response([paper(doi(0))])])
        self.assertEqual(client.session.headers["x-api-key"], "s2-test-key")

    def test_key_never_enters_url(self):
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "s2-test-key"}):
            client = self.s2([response([paper(doi(0))])])
            client.get_many_by_doi([doi(0)])
        url = client.session.post.call_args.args[0]
        self.assertNotIn("s2-test-key", url)

    def test_ten_dois_one_batch_post(self):
        ids = [doi(i) for i in range(10)]
        client = self.s2([response([paper(value) for value in ids])])
        values = client.get_many_by_doi(ids)
        self.assertEqual(client.stats["requests"], 1)
        self.assertEqual(client.stats["batches"], 1)
        self.assertEqual(list(values), ids)

    def test_hundred_dois_one_batch_post(self):
        ids = [doi(i) for i in range(100)]
        client = self.s2([response([paper(value) for value in ids])])
        client.get_many_by_doi(ids)
        self.assertEqual(client.session.post.call_count, 1)
        self.assertEqual(len(client.session.post.call_args.kwargs["json"]["ids"]), 100)

    def test_one_hundred_and_one_dois_two_batches(self):
        ids = [doi(i) for i in range(101)]
        client = self.s2([response([paper(value) for value in ids[:100]]), response([paper(ids[100])])])
        client.get_many_by_doi(ids)
        self.assertEqual(client.session.post.call_count, 2)
        self.assertEqual(client.stats["requests"], 2)

    def test_five_hundred_dois_are_accepted(self):
        ids = [doi(i) for i in range(500)]
        client = self.s2([response([paper(value) for value in ids])], chunk_size=500)
        values = client.get_many_by_doi(ids)
        self.assertEqual(len(values), 500)
        self.assertEqual(client.session.post.call_count, 1)

    def test_more_than_five_hundred_is_chunked_without_oversized_body(self):
        ids = [doi(i) for i in range(501)]
        client = self.s2([response([paper(value) for value in ids[:500]]), response([paper(ids[500])])], chunk_size=500)
        client.get_many_by_doi(ids)
        sizes = [len(call.kwargs["json"]["ids"]) for call in client.session.post.call_args_list]
        self.assertEqual(sizes, [500, 1])

    def test_body_uses_doi_prefix_and_fields_are_query_params(self):
        client = self.s2([response([paper(doi(0))])])
        client.get_many_by_doi([doi(0)])
        kwargs = client.session.post.call_args.kwargs
        self.assertEqual(kwargs["json"], {"ids": ["DOI:" + doi(0)]})
        self.assertIn("isOpenAccess", kwargs["params"]["fields"])
        self.assertNotIn("fields", json.dumps(kwargs["json"]))

    def test_duplicate_dois_are_deduplicated_in_stable_order(self):
        ids = [doi(2), doi(1), doi(2), doi(0), doi(1)]
        client = self.s2([response([paper(doi(2)), paper(doi(1)), paper(doi(0))])])
        self.assertEqual(list(client.get_many_by_doi(ids)), [doi(2), doi(1), doi(0)])

    def test_partial_response_maps_null_and_missing_records(self):
        ids = [doi(0), doi(1), doi(2)]
        client = self.s2([response([paper(ids[0]), None])])
        values = client.get_many_by_doi(ids)
        self.assertEqual(values[ids[0]]["doi"], ids[0])
        self.assertIsNone(values[ids[1]])
        self.assertIsNone(values[ids[2]])
        self.assertEqual(client.stats["not_found"], 2)

    def test_null_result_is_stable_not_found_cache(self):
        client = self.s2([response([None])])
        self.assertIsNone(client.get_many_by_doi([doi(0)])[doi(0)])
        entry = json.loads(next(self.root.joinpath("data/cache/semantic_scholar").glob("*.json")).read_text())
        self.assertEqual(entry["status"], "not_found")

    def test_record_without_abstract_is_no_abstract(self):
        client = self.s2([response([paper(doi(0), abstract=None)])])
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsNotNone(value)
        self.assertIsNone(value["abstract"])
        self.assertEqual(client.stats["no_abstract"], 1)
        self.assertEqual(json.loads(next(self.root.joinpath("data/cache/semantic_scholar").glob("*.json")).read_text())["status"], "no_abstract")

    def test_doi_mismatch_is_error_without_success_cache(self):
        client = self.s2([response([paper("10.3000/wrong")])])
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertIsInstance(value, ProviderError)
        self.assertEqual(value.code, "doi_mismatch")
        self.assertFalse(list(self.root.joinpath("data/cache/semantic_scholar").glob("*.json")))

    def test_429_retries_and_does_not_negative_cache(self):
        client = self.s2([response([], 429, {"Retry-After": "0"}), response([paper(doi(0))])], retries=1)
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertEqual(value["doi"], doi(0))
        self.assertEqual(client.stats["requests"], 2)
        entries = [json.loads(p.read_text()) for p in self.root.joinpath("data/cache/semantic_scholar").glob("*.json")]
        self.assertEqual([entry["status"] for entry in entries], ["success"])

    def test_429_final_is_transient_and_uncached(self):
        client = self.s2([response([], 429, {"Retry-After": "0"})], retries=0)
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertEqual(value.code, "rate_limited")
        self.assertEqual(client.stats["rate_limited"], 1)
        self.assertFalse(list(self.root.joinpath("data/cache/semantic_scholar").glob("*.json")))

    def test_5xx_is_transient_and_uncached(self):
        client = self.s2([response([], 503, {})], retries=0)
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertEqual(value.code, "server_error")
        self.assertEqual(client.stats["transient_errors"], 1)
        self.assertFalse(list(self.root.joinpath("data/cache/semantic_scholar").glob("*.json")))

    def test_timeout_is_transient_and_uncached(self):
        client = self.s2([requests.Timeout("timeout")], retries=0)
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertEqual(value.code, "network_error")
        self.assertEqual(client.stats["transient_errors"], 1)
        self.assertFalse(list(self.root.joinpath("data/cache/semantic_scholar").glob("*.json")))

    def test_success_cache_replay_makes_no_post(self):
        first = self.s2([response([paper(doi(0))])])
        expected = first.get_many_by_doi([doi(0)])[doi(0)]
        second = self.s2([], cache_only=True)
        actual = second.get_many_by_doi([doi(0)])[doi(0)]
        self.assertEqual(actual["abstract"], expected["abstract"])
        second.session.post.assert_not_called()
        self.assertEqual(second.stats["cache_hits"], 1)

    def test_cached_doi_is_removed_from_batch(self):
        first = self.s2([response([paper(doi(0))])])
        first.get_many_by_doi([doi(0)])
        second = self.s2([response([paper(doi(1))])])
        values = second.get_many_by_doi([doi(0), doi(1)])
        self.assertEqual(values[doi(0)]["doi"], doi(0))
        self.assertEqual(second.session.post.call_args.kwargs["json"]["ids"], ["DOI:" + doi(1)])

    def test_minimum_interval_is_at_least_105_seconds(self):
        self.assertGreaterEqual(self.s2([]).min_interval, 1.05)

    def test_open_access_metadata_is_preserved(self):
        value = self.s2([response([paper(doi(0))])]).get_many_by_doi([doi(0)])[doi(0)]
        self.assertTrue(value["is_open_access"])
        self.assertEqual(value["open_access_pdf"]["url"], "https://example.org/a.pdf")

    def test_key_is_absent_from_cache_and_batch_archive(self):
        secret = "s2-test-secret"
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": secret}):
            client = self.s2([response([paper(doi(0))])])
            client.get_many_by_doi([doi(0)])
        text = "\n".join(path.read_text() for path in self.root.joinpath("data/cache/semantic_scholar").rglob("*.json"))
        self.assertNotIn(secret, text)

    def test_stats_report_found_and_no_abstract(self):
        client = self.s2([response([paper(doi(0)), paper(doi(1), abstract=None)])])
        client.get_many_by_doi([doi(0), doi(1)])
        self.assertEqual(client.stats["found"], 2)
        self.assertEqual(client.stats["no_abstract"], 1)
        self.assertEqual(client.stats["requests"], 1)


class SemanticScholarIntegrationTests(S2BatchCase):
    def test_enrich_record_uses_s2_candidate_after_crossref_missing(self):
        s2 = self.s2([response([paper("10.1000/rice")])])
        clients = providers({"crossref": None, "openalex": None})
        clients["semantic_scholar"] = s2
        result = enrich_record(source_record(), clients,
                               batch_results={"semantic_scholar": {"10.1000/rice": s2.parse(paper("10.1000/rice"), "now")}})
        self.assertEqual(result["abstract_source"], "semantic_scholar")

    def test_protected_wos_fields_survive_s2_batch_enrichment(self):
        original = source_record()
        clients = providers({"crossref": None, "openalex": None})
        s2 = self.s2([response([paper("10.1000/rice")])])
        clients["semantic_scholar"] = s2
        result = enrich_record(original, clients,
                               batch_results={"semantic_scholar": {"10.1000/rice": s2.parse(paper("10.1000/rice"), "now")}})
        for field in ("uid", "title", "authors", "publish_year", "doi", "matched_queries", "provenance_history"):
            self.assertEqual(result[field], original[field])

    def test_selective_refresh_semantic_scholar_is_allowed(self):
        source = self.root / "input.jsonl"
        from scripts.pipeline_utils import write_jsonl
        write_jsonl(source, [source_record()])
        with patch("scripts.enrich_abstracts.CrossrefClient") as crossref, \
             patch("scripts.enrich_abstracts.OpenAlexClient") as openalex, \
             patch("scripts.enrich_abstracts.SemanticScholarClient") as semantic:
            crossref.return_value = providers()["crossref"]
            openalex.return_value = providers()["openalex"]
            semantic.return_value = self.s2([response([paper("10.1000/rice")])])
            with redirect_stdout(io.StringIO()):
                report = enrich_file(source, "output.jsonl", root=self.root,
                                     refresh_providers=("semantic_scholar",),
                                     cache_only_providers=("crossref", "openalex"))
        self.assertEqual(report["semantic_scholar_lookup_mode"], "batch")
        self.assertEqual(report["provider_statistics"]["semantic_scholar"]["batches"], 1)

    def test_cache_only_s2_does_not_post(self):
        client = self.s2([], cache_only=True)
        value = client.get_many_by_doi([doi(0)])[doi(0)]
        self.assertEqual(value.code, "cache_miss")
        client.session.post.assert_not_called()
