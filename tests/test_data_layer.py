import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from scripts.normalize_record import dedup_key, deduplicate_records, normalize_doi, normalize_record
from scripts.wos_client import WosApiError, WosStarterClient
from scripts.wos_search import ROOT, run_search


def api_response(status, body=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body or {}).encode()
    response.headers.update(headers or {})
    return response


class SchemaTests(unittest.TestCase):
    def test_real_response_and_missing_values(self):
        hit = json.loads((ROOT / "tests/fixtures/starter_record.json").read_text())
        before = copy.deepcopy(hit)
        record = normalize_record(hit, "TS=irrigation", retrieved_at="2026-10-02T00:00:00+00:00")
        self.assertEqual(record["page_count"], 39)
        self.assertEqual(record["publication_date_raw"], "DEC 31")
        self.assertEqual(record["times_cited_wos"], 0)
        self.assertEqual(record["authors"][0]["display_name"], "Li, Longyu")
        self.assertIsNone(record["abstract"])
        self.assertIsNone(record["abstract_source"])
        self.assertIsNone(record["abstract_retrieved_at"])
        self.assertEqual(hit, before)
        sparse = normalize_record({"citations": [{"db": "MEDLINE", "count": 100}]})
        self.assertIsNone(sparse["times_cited_wos"])
        self.assertIsNone(sparse["doi"])
        self.assertEqual(sparse["authors"], [])

    def test_doi_normalization_and_identity_aliases(self):
        for value in [" DOI: 10.123/ABC ", "https://doi.org/10.123/ABC", "http://dx.doi.org/10.123/ABC"]:
            self.assertEqual(normalize_doi(value), "10.123/abc")
        records = [
            {"uid": "WOS:1", "doi": None, "title": "First title"},
            {"uid": "WOS:2", "doi": "10.123/abc", "title": "Other copy"},
            {"uid": "WOS:1", "doi": "10.123/abc", "title": "Bridge"},
        ]
        unique = deduplicate_records(records)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]["title"], "First title")
        self.assertEqual(unique[0]["doi"], "10.123/abc")
        self.assertIsNone(records[0]["doi"])

    def test_title_fallback_and_unidentifiable_records(self):
        records = [
            {"title": "Rice--water", "publish_year": 2024},
            {"title": "RICE water", "publish_year": 2024},
            {"title": "Rice water", "publish_year": 2025},
            {}, {}, {"title": "Missing year"}, {"title": "Missing year"},
            {"doi": "10.1/a", "title": "Rice water", "publish_year": 2024},
            {"doi": "10.1/b", "title": "Rice water", "publish_year": 2024},
        ]
        self.assertEqual(len(deduplicate_records(records)), 8)
        self.assertIsNone(dedup_key({}))


class ClientTests(unittest.TestCase):
    def make_client(self):
        client = WosStarterClient(api_key="test-secret")
        self.addCleanup(client.close)
        return client

    def test_retry_rate_limit_and_request_parameters(self):
        client = self.make_client()
        client.session.get = Mock(side_effect=[api_response(429, headers={"Retry-After": "0"}), api_response(200, {"hits": []})])
        with patch("scripts.wos_client.time.sleep") as sleep, patch("scripts.wos_client.time.monotonic", return_value=100):
            client.search_documents("TS=rice", limit=5, sort_field="PY+D")
        self.assertEqual(client.request_count, 2)
        sleep.assert_any_call(0.25)
        params = client.session.get.call_args.kwargs
        self.assertEqual(params["params"]["sortField"], "PY+D")
        self.assertFalse(params["allow_redirects"])
        self.assertEqual(client.session.headers["X-ApiKey"], "test-secret")

    def test_auth_error_redacted_without_retry(self):
        client = self.make_client()
        client.session.get = Mock(return_value=api_response(403, {"error": "test-secret"}))
        with self.assertRaises(WosApiError) as error:
            client.search_documents("TS=rice")
        self.assertNotIn("test-secret", str(error.exception))
        self.assertIn("[REDACTED]", str(error.exception))
        self.assertEqual(client.request_count, 1)

    def test_retry_header_formats_and_long_wait(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
        self.assertTrue(28 <= WosStarterClient._retry_delay(api_response(429, headers={"Retry-After": future}), 0) <= 30)
        self.assertEqual(WosStarterClient._retry_delay(api_response(429, headers={"Retry-After": "bad"}), 2), 4)
        client = self.make_client()
        client.session.get = Mock(return_value=api_response(429, headers={"Retry-After": "3600"}))
        with patch("scripts.wos_client.time.sleep") as sleep, self.assertRaises(WosApiError):
            client.search_documents("TS=rice")
        sleep.assert_not_called()

    def test_transient_connection_error_and_invalid_json(self):
        client = self.make_client()
        client.session.get = Mock(side_effect=[requests.Timeout("timeout"), api_response(200, {"hits": []})])
        with patch("scripts.wos_client.time.sleep"):
            self.assertEqual(client.search_documents("TS=rice"), {"hits": []})
        invalid = api_response(200)
        invalid._content = b"not json"
        client.session.get = Mock(return_value=invalid)
        with patch("scripts.wos_client.time.sleep"), self.assertRaisesRegex(WosApiError, "invalid JSON"):
            client.search_documents("TS=rice")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_quiet(self, client, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_search("TS=rice", root=self.root, client=client, **kwargs)

    def test_archive_pagination_and_duplicate_doi(self):
        client = Mock()
        client.request_count = 2
        pages = [
            {"metadata": {"total": 3, "page": 1, "limit": 2}, "hits": [{"uid": "WOS:1", "identifiers": {"doi": "DOI:10.1/A"}}, {"uid": "WOS:2"}]},
            {"metadata": {"total": 3, "page": 2, "limit": 2}, "hits": [{"uid": "WOS:3", "identifiers": {"doi": "https://doi.org/10.1/a"}}]},
        ]
        original = copy.deepcopy(pages)
        client.search_documents.side_effect = pages
        manifest = self.run_quiet(client, limit=2, max_records=4)
        self.assertEqual(manifest["records_collected"], 2)
        self.assertEqual(manifest["duplicates_removed"], 1)
        self.assertTrue(manifest["collection_complete"])
        raw_dir = self.root / manifest["raw_directory"]
        self.assertEqual(json.loads((raw_dir / "page_0001.json").read_text()), original[0])
        self.assertEqual(pages, original)
        records = [json.loads(line) for line in (self.root / manifest["processed_file"]).read_text().splitlines()]
        self.assertEqual(records[0]["provenance"]["query"], "TS=rice")

    def test_page_budget_and_collision_safe_names(self):
        client = Mock()
        client.request_count = 1
        client.search_documents.return_value = {"metadata": {"total": 100, "page": 1, "limit": 2}, "hits": [{"uid": "WOS:1"}, {"uid": "WOS:1"}]}
        first = self.run_quiet(client, limit=2, max_records=2, name="../../unsafe")
        second = self.run_quiet(client, limit=2, max_records=2, name="../../unsafe")
        self.assertEqual(first["stop_reason"], "page_cap")
        self.assertFalse(first["collection_complete"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotIn("..", first["raw_directory"])

    def test_failure_preserves_manifest_and_partial_records(self):
        client = Mock(spec=["search_documents", "request_count"])
        client.request_count = 2
        client.search_documents.side_effect = [
            {"metadata": {"total": 2, "page": 1, "limit": 1}, "hits": [{"uid": "WOS:1"}]},
            WosApiError("WoS API error 503"),
        ]
        with self.assertRaises(WosApiError):
            self.run_quiet(client, limit=1, max_records=2)
        path = next((self.root / "data/raw").glob("*/manifest.json"))
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["pages_fetched"], 1)
        self.assertEqual(len((self.root / manifest["processed_file"]).read_text().splitlines()), 1)
        self.assertTrue((path.parent / "page_0001.json").exists())

    def test_empty_results_and_record_cap(self):
        client = Mock()
        client.request_count = 1
        client.search_documents.return_value = {"metadata": {"total": 0, "page": 1, "limit": 50}, "hits": []}
        empty = self.run_quiet(client)
        self.assertTrue(empty["collection_complete"])
        self.assertEqual((self.root / empty["processed_file"]).read_text(), "")
        client.search_documents.return_value = {"metadata": {"total": 100, "page": 1, "limit": 50}, "hits": [{"uid": f"WOS:{i}"} for i in range(50)]}
        capped = self.run_quiet(client, max_records=1)
        self.assertEqual(capped["records_collected"], 1)
        self.assertEqual(capped["records_omitted_by_cap"], 49)
        self.assertEqual(capped["stop_reason"], "record_cap")


if __name__ == "__main__":
    unittest.main()
