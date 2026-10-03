"""Offline v0.3 tests. All HTTP is mocked and real sockets are disabled."""

import copy
import csv
import io
import json
import os
import socket
import tempfile
import unittest
import uuid
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import requests

from scripts.enrich_abstracts import (POLICY, PROTECTED_FIELDS, abstract_quality, enrich_file,
                                     enrich_record, validate_candidate)
from scripts.export_records import ABSTRACT_COLUMNS, export_records, read_xlsx_tables
from scripts.pipeline_utils import SECRET_ENV_NAMES, assert_safe, known_secrets, redact, write_jsonl
from scripts.providers import CrossrefClient, SemanticScholarClient, OpenAlexClient, PROVIDER_NAMES
from scripts.providers.base_client import ProviderError, cache_key, canonical_doi
from scripts.providers.crossref_client import clean_markup
from scripts.providers.openalex_client import reconstruct_abstract


DOI = "10.1000/rice"
TITLE = "Water management and microbial carbon in saline paddy soils"
TEXT = ("Field measurements evaluated water management and microbial carbon in saline paddy soils. "
        "Replicated rice plots received contrasting irrigation regimes during two growing seasons. "
        "Soil measurements and crop observations documented changes in salinity, carbon dynamics and water use. "
        "These observations support further field evaluation under comparable soil and climate conditions.")
OTHER = ("A molecular spectroscopy experiment characterized the crystalline structure of industrial catalysts. "
         "Laboratory reactors operated at high temperatures with different gas pressures and metal concentrations. "
         "The measured spectra indicated distinct lattice configurations and electrochemical properties. "
         "The results concern catalyst fabrication and performance in fuel conversion systems.")


def source_record(**changes):
    record = {"uid": "WOS:fixture", "doi": DOI, "title": TITLE, "publish_year": 2025,
              "authors": [{"display_name": "Example Author"}], "source_title": "Example Journal",
              "issn": "1234-5678", "eissn": None, "times_cited_wos": 0,
              "matched_queries": ["Q1", "Q3"], "query_match_count": 2,
              "provenance_history": [{"query_id": "Q1", "query": "TS=rice", "database": "WOS"}],
              "abstract": None, "abstract_source": None, "abstract_retrieved_at": None}
    return dict(record, **changes)


def candidate(provider="crossref", **changes):
    return dict({"provider": provider, "provider_id": "example-id", "doi": DOI, "title": TITLE,
                 "year": 2025, "abstract": TEXT, "url": "https://example.org/paper",
                 "retrieved_at": "2026-10-03T00:00:00+00:00", "raw": {}}, **changes)


def response(raw=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = raw
    return result


def crossref_raw(**changes):
    message = {"DOI": DOI, "title": [TITLE], "issued": {"date-parts": [[2025]]},
               "abstract": f"<jats:p>{TEXT}</jats:p>", "URL": "https://doi.org/" + DOI}
    return {"message": dict(message, **changes)}


class FakeProvider:
    def __init__(self, value):
        self.value = value
        self.calls = []
        self.stats = {"requests": 0, "cache_hits": 0, "not_found": 0, "errors": 0}
        self.error_counts, self.last_lookup = {}, {}

    def get_by_doi(self, doi):
        self.calls.append(doi)
        self.stats["requests"] += 1
        if isinstance(self.value, Exception):
            self.stats["errors"] += 1
            self.error_counts[self.value.code] = self.error_counts.get(self.value.code, 0) + 1
            raise self.value
        if self.value is None:
            self.stats["not_found"] += 1
        return copy.deepcopy(self.value)


def providers(values=None):
    values = values or {}
    return {name: FakeProvider(values.get(name)) for name in PROVIDER_NAMES}


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden in tests"))
        self.network_guard.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.network_guard.stop)

    def client(self, cls=CrossrefClient, replies=None, **changes):
        session = Mock(headers={})
        session.get.side_effect = replies or [response(crossref_raw())]
        client = cls(root=self.root, session=session, sleep=Mock(), min_interval=0, **changes)
        return client


class ProviderTests(OfflineCase):
    def test_crossref_jats_cleaning_entities_and_subscripts(self):
        value = "<jats:sec><jats:title>Background</jats:title><jats:p>CO<sub>2</sub> &amp; water.</jats:p><p>Rice.</p><script>hidden</script></jats:sec>"
        self.assertEqual(clean_markup(value), "Background CO2 & water. Rice.")
        self.assertIsNone(clean_markup(None))

    def test_crossref_parsing_preserves_raw(self):
        raw = crossref_raw()
        client = self.client(replies=[response(raw)])
        result = client.get_by_doi(DOI)
        self.assertEqual(result["abstract"], TEXT)
        self.assertEqual(result["raw"], raw)
        self.assertEqual(result["provider_id"], DOI)
        self.assertEqual(result["year"], 2025)
        self.assertIn("User-Agent", client.session.headers)
        self.assertFalse(client.session.get.call_args.kwargs["allow_redirects"])

    def test_crossref_missing_abstract_stays_null(self):
        client = self.client(replies=[response(crossref_raw(abstract=None))])
        self.assertIsNone(client.get_by_doi(DOI)["abstract"])

    def test_semantic_scholar_doi_fields_and_parsing_without_key(self):
        raw = {"paperId": "paper-id", "title": TITLE, "year": 2025, "abstract": TEXT,
               "externalIds": {"DOI": DOI.upper()}, "url": "https://example.org/paper"}
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": ""}):
            client = self.client(SemanticScholarClient, [response(raw)])
        result = client.get_by_doi(DOI)
        self.assertEqual(result["doi"], DOI)
        self.assertEqual(result["abstract"], TEXT)
        args, kwargs = client.session.get.call_args
        self.assertTrue(args[0].endswith(quote("DOI:" + DOI, safe="")))
        self.assertEqual(set(kwargs["params"]["fields"].split(",")), {"paperId", "title", "year", "abstract", "externalIds", "url"})
        self.assertNotIn("x-api-key", client.session.headers)

    def test_openalex_inverted_index_order_and_repeated_words(self):
        self.assertEqual(reconstruct_abstract({"rice": [2, 0], "water": [1]}), "rice water rice")
        self.assertIsNone(reconstruct_abstract(None))

    def test_openalex_invalid_index_never_invents_words(self):
        for index in ({"rice": [0, 2]}, {"rice": [0], "water": [0]}, {"rice": [-1]}, {"rice": [True]}):
            with self.subTest(index=index), self.assertRaises(ValueError):
                reconstruct_abstract(index)

    def test_openalex_parsing_preserves_work_id_and_doi(self):
        words = {}
        for position, word in enumerate(TEXT.split()):
            words.setdefault(word, []).append(position)
        raw = {"id": "https://openalex.org/W123", "doi": "https://doi.org/" + DOI,
               "title": TITLE, "publication_year": 2025, "abstract_inverted_index": words}
        client = self.client(OpenAlexClient, [response(raw)])
        result = client.get_by_doi(DOI)
        self.assertEqual(result["provider_id"], raw["id"])
        self.assertEqual(result["abstract"], TEXT)
        self.assertEqual(result["raw"], raw)

    def test_malformed_openalex_abstract_keeps_metadata_and_warning(self):
        client = self.client(OpenAlexClient, [response({"id": "W123", "doi": DOI,
                           "abstract_inverted_index": {"lost": [1]}})])
        result = client.get_by_doi(DOI)
        self.assertIsNone(result["abstract"])
        self.assertEqual(result["parse_warning"], "invalid_inverted_index")

    def test_doi_normalization_and_stable_hash(self):
        variants = (DOI, DOI.upper(), " DOI:" + DOI + " ", "https://dx.doi.org/" + DOI.upper())
        self.assertEqual({cache_key(doi) for doi in variants}, {cache_key(DOI)})
        self.assertEqual(canonical_doi(variants[-1]), DOI)
        with self.assertRaises(ValueError):
            canonical_doi("not-a-doi")

    def test_success_cache_hit_does_not_make_network_request(self):
        first = self.client()
        expected = first.get_by_doi(DOI)
        second = self.client(cache_only=True)
        self.assertEqual(second.get_by_doi(DOI.upper()), expected)
        second.session.get.assert_not_called()
        self.assertEqual(second.stats["cache_hits"], 1)
        cache = json.loads(next(second.cache_dir.glob("*.json")).read_text())
        self.assertEqual(cache["raw"], crossref_raw())

    def test_404_negative_cache_and_raw_response(self):
        client = self.client(replies=[response({"message": "not found"}, 404)])
        self.assertIsNone(client.get_by_doi(DOI))
        self.assertIsNone(client.get_by_doi(DOI))
        self.assertEqual(client.session.get.call_count, 1)
        self.assertEqual(client.stats["not_found"], 2)
        cache = json.loads(next(client.cache_dir.glob("*.json")).read_text())
        self.assertEqual(cache["status"], "not_found")
        self.assertEqual(cache["raw"], {"message": "not found"})

    def test_200_empty_record_negative_cache(self):
        client = self.client(SemanticScholarClient, [response({})])
        self.assertIsNone(client.get_by_doi(DOI))
        self.assertIsNone(client.get_by_doi(DOI))
        self.assertEqual(client.session.get.call_count, 1)

    def test_refresh_bypasses_positive_and_negative_cache(self):
        for first_response in (response(crossref_raw()), response({}, 404)):
            with self.subTest(status=first_response.status_code):
                for path in (self.root / "data/cache/crossref").glob("*.json"):
                    path.unlink()
                self.client(replies=[first_response]).get_by_doi(DOI)
                refreshed = self.client(refresh=True)
                self.assertEqual(refreshed.get_by_doi(DOI)["abstract"], TEXT)
                self.assertEqual(refreshed.stats["requests"], 1)

    def test_cache_only_missing_does_not_make_network_request(self):
        client = self.client(cache_only=True)
        with self.assertRaisesRegex(ProviderError, "cache_miss"):
            client.get_by_doi(DOI)
        client.session.get.assert_not_called()

    def test_429_and_5xx_backoff_then_success(self):
        client = self.client(replies=[response({}, 429, {"Retry-After": "3"}), response({}, 503), response(crossref_raw())])
        self.assertEqual(client.get_by_doi(DOI)["abstract"], TEXT)
        self.assertEqual(client.stats["requests"], 3)
        self.assertTrue(any(call.args == (3.0,) for call in client.sleep.call_args_list))
        self.assertTrue(any(call.args == (2,) for call in client.sleep.call_args_list))

    def test_network_timeout_retries_and_errors_are_fixed_codes(self):
        client = self.client(replies=[requests.Timeout("sensitive request details"), response(crossref_raw())])
        self.assertEqual(client.get_by_doi(DOI)["abstract"], TEXT)
        self.assertEqual(client.stats["requests"], 2)

    def test_rate_limit_failure_not_negative_cached(self):
        client = self.client(replies=[response({}, 429)] * 3)
        with self.assertRaisesRegex(ProviderError, "rate_limited"):
            client.get_by_doi(DOI)
        self.assertEqual(client.stats["errors"], 1)
        self.assertEqual(list(client.cache_dir.glob("*.json")), [])

    def test_long_retry_after_stops_without_long_sleep(self):
        client = self.client(replies=[response({}, 429, {"Retry-After": "120"})])
        with self.assertRaisesRegex(ProviderError, "retry_after_exceeds_60s"):
            client.get_by_doi(DOI)
        client.sleep.assert_not_called()

    def test_long_retry_after_cooldown_applies_to_next_doi(self):
        client = self.client(replies=[response({}, 429, {"Retry-After": "120"})])
        client.clock = Mock(return_value=0)
        with self.assertRaises(ProviderError):
            client.get_by_doi(DOI)
        with self.assertRaisesRegex(ProviderError, "provider_cooldown"):
            client.get_by_doi("10.1000/next")
        self.assertEqual(client.stats["requests"], 1)

    def test_invalid_provider_schema_keeps_raw_cache_and_reports_error(self):
        client = self.client(SemanticScholarClient, [response({"paperId": "id", "title": ["invalid"]})])
        with self.assertRaisesRegex(ProviderError, "invalid_schema"):
            client.get_by_doi(DOI)
        self.assertEqual(len(list(client.cache_dir.glob("*.json"))), 1)

    def test_optional_keys_read_from_project_dotenv_without_log_or_cache_leak(self):
        secret = uuid.uuid4().hex
        (self.root / ".env").write_text("OPENALEX_API_KEY=" + secret + "\n")
        environment = os.environ.copy()
        environment.pop("OPENALEX_API_KEY", None)
        with patch.dict(os.environ, environment, clear=True):
            client = self.client(OpenAlexClient, [response({"id": "W123", "doi": DOI})])
            client.get_by_doi(DOI)
        self.assertEqual(client.session.headers["Authorization"], "Bearer " + secret)
        self.assertNotIn(secret, next(client.cache_dir.glob("*.json")).read_text())

    def test_request_rate_limit_between_completed_calls(self):
        client = self.client(replies=[response(crossref_raw()), response(crossref_raw(DOI="10.1000/other"))])
        client.min_interval = 1
        client.clock = Mock(return_value=0)
        client.get_by_doi(DOI)
        client.get_by_doi("10.1000/other")
        client.sleep.assert_called_with(1)

    def test_all_credentials_auth_and_echo_guard(self):
        secrets = {name: "sentinel/" + uuid.uuid4().hex for name in SECRET_ENV_NAMES}
        with patch.dict(os.environ, secrets):
            for cls in (CrossrefClient, SemanticScholarClient, OpenAlexClient):
                for secret in secrets.values():
                    client = self.client(cls, [response({"echo": quote(secret, safe="")})])
                    with self.assertRaises(ProviderError) as error:
                        client.get_by_doi(DOI)
                    self.assertNotIn(secret, str(error.exception))
                    self.assertEqual(list(client.cache_dir.glob("*.json")), [])
            s2 = self.client(SemanticScholarClient)
            oa = self.client(OpenAlexClient)
            self.assertEqual(s2.session.headers["x-api-key"], secrets["SEMANTIC_SCHOLAR_API_KEY"])
            self.assertEqual(oa.session.headers["Authorization"], "Bearer " + secrets["OPENALEX_API_KEY"])
            self.assertEqual(oa.request_spec(DOI)[1], {})

    def test_error_response_and_exception_never_leak_credential_to_log(self):
        secret = uuid.uuid4().hex
        with patch.dict(os.environ, {"OPENALEX_API_KEY": secret}):
            for failure in (response({"error": secret}, 401), requests.ConnectionError("https://example?api_key=" + secret)):
                client = self.client(OpenAlexClient, [failure], retries=0)
                captured = io.StringIO()
                with redirect_stdout(captured), self.assertRaises(ProviderError) as error:
                    client.get_by_doi(DOI)
                self.assertNotIn(secret, captured.getvalue() + str(error.exception))
                self.assertEqual(list(client.cache_dir.glob("*.json")), [])


class SelectionTests(OfflineCase):
    def test_title_sanity_rejects_wrong_title_even_with_exact_doi(self):
        result = validate_candidate(source_record(), candidate(title="Unrelated industrial reactor spectroscopy"))
        self.assertTrue(result["doi_exact_match"])
        self.assertFalse(result["accepted"])
        self.assertIn("title_mismatch_or_missing", result["rejection_reasons"])

    def test_doi_mismatch_and_absent_doi_rejected(self):
        for doi in ("10.1000/wrong", None):
            result = validate_candidate(source_record(), candidate(doi=doi))
            self.assertFalse(result["accepted"])
            self.assertIn("doi_mismatch_or_missing", result["rejection_reasons"])

    def test_year_plus_minus_one_tolerated_and_recorded(self):
        for year, offset in ((2024, -1), (2026, 1)):
            result = validate_candidate(source_record(), candidate(year=year))
            self.assertTrue(result["accepted"])
            self.assertEqual(result["year_difference"], offset)
            self.assertIn("publication_year_differs", result["warnings"])

    def test_year_difference_above_one_rejected_missing_year_warns(self):
        self.assertFalse(validate_candidate(source_record(), candidate(year=2022))["accepted"])
        missing = validate_candidate(source_record(), candidate(year=None))
        self.assertTrue(missing["accepted"])
        self.assertIsNone(missing["year_match"])
        self.assertIn("year_unavailable", missing["warnings"])

    def test_abstract_quality_rejects_empty_short_or_markup(self):
        for text in (None, "", "Tiny abstract", "9" * 300, "<p>" + TEXT + "</p>", TEXT + "\x00"):
            with self.subTest(text_type=type(text).__name__):
                self.assertIsNotNone(abstract_quality(text))
        self.assertIsNone(abstract_quality(TEXT))

    def test_all_three_providers_queried_even_if_first_has_abstract(self):
        clients = providers({name: candidate(name) for name in PROVIDER_NAMES})
        result = enrich_record(source_record(), clients)
        self.assertTrue(all(len(client.calls) == 1 for client in clients.values()))
        self.assertEqual(result["abstract_source"], "crossref")
        self.assertTrue(result["abstract_enrichment"]["corroborated"])
        self.assertEqual(len(result["abstract_enrichment"]["candidates"]), 3)

    def test_near_agreement_is_corroborated_without_rewriting(self):
        changed = TEXT.replace("two growing seasons", "2 growing seasons")
        clients = providers({"crossref": candidate(), "openalex": candidate("openalex", abstract=changed)})
        result = enrich_record(source_record(), clients)
        self.assertTrue(result["abstract_enrichment"]["corroborated"])
        self.assertEqual(result["abstract"], TEXT)

    def test_provider_conflict_keeps_candidates_and_null_canonical(self):
        result = enrich_record(source_record(), providers({"crossref": candidate(), "openalex": candidate("openalex", abstract=OTHER)}))
        self.assertIsNone(result["abstract"])
        self.assertEqual(result["abstract_enrichment"]["status"], "conflict")
        self.assertTrue(result["abstract_enrichment"]["conflict"])
        self.assertEqual(len(result["abstract_enrichment"]["candidates"]), 2)

    def test_conflict_preserves_existing_abstract_explicitly(self):
        previous = source_record(abstract=TEXT, abstract_source="crossref", abstract_retrieved_at="previous")
        result = enrich_record(previous, providers({"crossref": candidate(), "openalex": candidate("openalex", abstract=OTHER)}))
        self.assertEqual(result["abstract"], TEXT)
        self.assertEqual(result["abstract_retrieved_at"], "previous")
        self.assertTrue(result["abstract_enrichment"]["previous_abstract_retained"])
        self.assertEqual(result["abstract_enrichment"]["status"], "conflict")

    def test_priority_uses_semantic_scholar_if_crossref_has_no_abstract(self):
        result = enrich_record(source_record(), providers({"crossref": candidate(abstract=None),
                              "semantic_scholar": candidate("semantic_scholar"), "openalex": candidate("openalex")}))
        self.assertEqual(result["abstract_source"], "semantic_scholar")
        self.assertIn("fixed priority", result["abstract_enrichment"]["selection_reason"])

    def test_missing_doi_skips_all_clients_and_keeps_null(self):
        clients = providers({name: candidate(name) for name in PROVIDER_NAMES})
        result = enrich_record(source_record(doi=None), clients)
        self.assertEqual(result["abstract_enrichment"]["status"], "no_doi")
        self.assertIsNone(result["abstract"])
        self.assertTrue(all(not client.calls for client in clients.values()))

    def test_missing_abstract_preserves_empty_candidate_and_missing_status(self):
        result = enrich_record(source_record(), providers({"crossref": candidate(abstract=None)}))
        self.assertIsNone(result["abstract"])
        self.assertEqual(result["abstract_enrichment"]["status"], "missing")
        self.assertEqual(len(result["abstract_enrichment"]["candidates"]), 1)

    def test_provider_failure_isolated_and_recorded(self):
        clients = providers({"crossref": ProviderError("crossref", "server_error", 503), "openalex": candidate("openalex")})
        result = enrich_record(source_record(), clients)
        self.assertEqual(result["abstract_source"], "openalex")
        self.assertEqual(result["abstract_enrichment"]["provider_results"]["crossref"]["http_status"], 503)

    def test_protected_wos_fields_and_input_unchanged(self):
        original = source_record()
        snapshot = copy.deepcopy(original)
        result = enrich_record(original, providers({"crossref": candidate(year=2024)}))
        self.assertEqual(original, snapshot)
        for field in PROTECTED_FIELDS:
            self.assertEqual(result[field], original[field])


class OutputTests(OfflineCase):
    def test_v02_jsonl_compatibility_coverage_and_search_provenance(self):
        source = self.root / "merged.jsonl"
        original = [source_record(), source_record(uid="WOS:no-doi", doi=None)]
        write_jsonl(source, original)
        manifest = {"searches": [{"query_id": "Q1", "exact_query": "TS=rice", "collected": 2, "truncated": False}]}
        source.with_suffix(".manifest.json").write_text(json.dumps(manifest))
        with redirect_stdout(io.StringIO()):
            report = enrich_file(source, "enriched.jsonl", root=self.root, clients=providers({"openalex": candidate("openalex")}))
        self.assertEqual(report["coverage_percent"], 50)
        self.assertEqual(report["records_without_doi"], 1)
        self.assertEqual(report["found_by_openalex"], 1)
        enriched_manifest = json.loads((self.root / "enriched.manifest.json").read_text())
        self.assertEqual(enriched_manifest["searches"][0]["exact_query"], "TS=rice")
        self.assertEqual([json.loads(line) for line in source.read_text().splitlines()], original)
        self.assertEqual(len(list((self.root / "data/reports").glob("*_abstract_coverage.json"))), 1)

    def test_output_no_overwrite(self):
        source = self.root / "input.jsonl"
        write_jsonl(source, [source_record()])
        with self.assertRaises(FileExistsError):
            enrich_file(source, source, root=self.root)

    def test_credentials_cannot_enter_enrichment_output_or_csv(self):
        secret = uuid.uuid4().hex
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": secret}):
            source = self.root / "input.jsonl"
            write_jsonl(source, [source_record()])
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "API key"):
                enrich_file(source, "blocked.jsonl", root=self.root, clients=providers({"crossref": candidate(abstract=TEXT + secret)}))
            self.assertFalse((self.root / "blocked.jsonl").exists())
            source.write_text(json.dumps(source_record(title=secret)) + "\n")
            with self.assertRaisesRegex(ValueError, "API key"):
                export_records(source, "blocked_export", formats=("csv",), root=self.root)
            self.assertFalse((self.root / "blocked_export.csv").exists())

    def test_three_secret_names_redaction_and_encoded_guards(self):
        secrets = {name: "sentinel/" + uuid.uuid4().hex for name in SECRET_ENV_NAMES}
        with patch.dict(os.environ, secrets):
            known = known_secrets(self.root)
            for secret in secrets.values():
                for text in (secret, quote(secret, safe="")):
                    with self.assertRaises(ValueError):
                        assert_safe({"text": text}, known)
                    self.assertNotIn(text, redact(text, known))

    def test_csv_retains_full_abstract_and_adds_status_columns(self):
        record = enrich_record(source_record(), providers({"crossref": candidate()}))
        source = self.root / "input.jsonl"
        write_jsonl(source, [record])
        with redirect_stdout(io.StringIO()):
            export_records(source, "csv", formats=("csv",), root=self.root)
        with (self.root / "csv.csv").open(encoding="utf-8-sig") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["abstract"], TEXT)
        self.assertEqual(row["abstract_source"], "crossref")
        self.assertEqual(row["abstract_status"], "found")
        self.assertEqual(row["abstract_available"], "True")

    def test_excel_optional_abstracts_sheet_and_compact_records(self):
        record = enrich_record(source_record(), providers({"crossref": candidate()}))
        source = self.root / "input.jsonl"
        write_jsonl(source, [record])
        secrets = {name: uuid.uuid4().hex for name in SECRET_ENV_NAMES}
        with patch.dict(os.environ, secrets), redirect_stdout(io.StringIO()):
            export_records(source, "book", formats=("xlsx",), root=self.root, include_abstracts=True)
        sheets = read_xlsx_tables(self.root / "book.xlsx")
        self.assertEqual(list(sheets), ["Records", "Searches", "Summary", "Abstracts"])
        self.assertNotIn("abstract", list(sheets["Records"][0].values()))
        self.assertIn("abstract_available", list(sheets["Records"][0].values()))
        self.assertEqual(list(sheets["Abstracts"][0].values()), ABSTRACT_COLUMNS)
        self.assertEqual(sheets["Abstracts"][1]["D2"], TEXT)
        with zipfile.ZipFile(self.root / "book.xlsx") as archive:
            xml = b"".join(archive.read(name) for name in archive.namelist())
            self.assertIn(b"yyyy-mm-dd hh:mm:ss", archive.read("xl/styles.xml"))
            self.assertIn(b"UTC", archive.read("xl/styles.xml"))
        for secret in secrets.values():
            self.assertNotIn(secret.encode(), xml)


if __name__ == "__main__":
    unittest.main()
