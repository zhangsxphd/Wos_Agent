import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.export_records import RECORD_COLUMNS, SEARCH_COLUMNS, export_records, read_xlsx_tables
from scripts.merge_searches import merge_records, merge_searches, prepare_record, summarize_records
from scripts.normalize_record import normalize_record
from scripts.pipeline_utils import read_jsonl, write_jsonl
from scripts.run_search_plan import load_plan, run_plan
from scripts.wos_client import WosApiError


TEST_KEY = "unit-test-secret-0123456789"


def query(qid, enabled=True, **kwargs):
    result = {"id": qid, "description": f"Description {qid}", "query": f"TS={qid}", "database": "WOS", "sort": "PY+D", "enabled": enabled}
    result.update(kwargs)
    return result


def hit(index, **kwargs):
    record = {"uid": f"WOS:{index}", "title": f"Paper {index}", "source": {"publishYear": 2026, "sourceTitle": "Example Journal"}}
    record.update(kwargs)
    return record


def paged(total, page, limit):
    return {"metadata": {"total": total, "page": page, "limit": limit}, "hits": [hit(i) for i in range((page - 1) * limit, min(page * limit, total))]}


class FakeClient:
    api_key = TEST_KEY

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.request_count = 0

    def search_documents(self, **kwargs):
        self.calls.append(kwargs)
        self.request_count += 1
        return self.handler(kwargs["query"], kwargs["page"], kwargs["limit"])

    def close(self):
        pass


class TemporaryProject(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"WOS_STARTER_API_KEY": TEST_KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def plan_file(self, queries):
        path = self.root / "plan.yaml"
        path.write_text(yaml.safe_dump({"version": 1, "name": "test_plan", "queries": queries}), encoding="utf-8")
        return path

    def quiet(self, function, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(*args, **kwargs)

    def assert_no_secrets(self):
        for path in self.root.rglob("*"):
            if not path.is_file() or path.name == ".env":
                continue
            if path.suffix == ".xlsx":
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        self.assertNotIn(TEST_KEY, archive.read(name).decode("utf-8", errors="ignore"))
            else:
                self.assertNotIn(TEST_KEY, path.read_text(encoding="utf-8-sig"))


class PlanTests(TemporaryProject):
    def test_yaml_reads_all_required_fields(self):
        path = self.plan_file([query("Q1"), query("Q2", enabled=False)])
        plan = load_plan(path)
        self.assertEqual([q["id"] for q in plan["queries"]], ["Q1", "Q2"])
        self.assertFalse(plan["queries"][1]["enabled"])

    def test_yaml_rejects_invalid_flags_ids_caps_and_duplicate_keys(self):
        cases = [[query("Q1", enabled="true")], [query("Q1"), query("q1")], [query("Q1", max_records=0)], [query("../../Q1")]]
        for queries in cases:
            with self.subTest(queries=queries), self.assertRaises(ValueError):
                load_plan(self.plan_file(queries))
        path = self.root / "duplicate.yaml"
        path.write_text("queries: []\nqueries: []\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_plan(path)
        path.write_text("!!python/object/apply:os.system ['false']")
        with self.assertRaises(ValueError):
            load_plan(path)

    def test_enabled_queries_execute_sequentially_with_own_archives(self):
        path = self.plan_file([query("Q1"), query("Q2", enabled=False), query("Q3")])
        client = FakeClient(lambda q, p, limit: paged(3, p, limit))
        manifest = self.quiet(run_plan, path, root=self.root, client=client, page_size=2)
        self.assertEqual([call["query"] for call in client.calls], ["TS=Q1", "TS=Q1", "TS=Q3", "TS=Q3"])
        self.assertEqual(manifest["skipped_queries"], ["Q2"])
        self.assertEqual(len(manifest["queries"]), 2)
        for result in manifest["queries"]:
            own = json.loads((self.root / result["manifest_file"]).read_text())
            self.assertFalse(own["truncated"])
            self.assertEqual(own["collected"], 3)
            records = read_jsonl(self.root / result["processed_file"])
            self.assertEqual(records[0]["matched_queries"], [result["query_id"]])
            self.assertEqual(records[0]["provenance_history"][0]["query"], own["exact_query"])
            self.assertEqual(len(list((self.root / own["raw_directory"]).glob("page_*.json"))), 2)
        self.assert_no_secrets()

    def test_default_collects_more_than_200_records(self):
        client = FakeClient(lambda q, p, limit: paged(205, p, limit))
        manifest = self.quiet(run_plan, self.plan_file([query("Q1")]), root=self.root, client=client)
        result = manifest["queries"][0]
        self.assertEqual(result["collected"], 205)
        self.assertEqual(result["pages_fetched"], 5)
        self.assertFalse(result["truncated"])
        own = json.loads((self.root / result["manifest_file"]).read_text())
        self.assertIsNone(own["max_records"])
        self.assertIsNone(own["max_pages"])

    def test_explicit_caps_are_marked_even_if_last_page_was_fetched(self):
        path = self.plan_file([query("Q1", max_records=3)])
        client = FakeClient(lambda q, p, limit: paged(10, p, limit))
        manifest = self.quiet(run_plan, path, root=self.root, client=client, max_records=5)
        result = manifest["queries"][0]
        self.assertEqual(result["total_hits"], 10)
        self.assertEqual(result["collected"], 3)
        self.assertTrue(result["truncated"])
        own = json.loads((self.root / result["manifest_file"]).read_text())
        self.assertEqual(own["max_records"], 3)
        self.assertEqual(own["raw_records_fetched"], 10)
        self.assertEqual(own["records_omitted_by_cap"], 7)

    def test_page_cap_and_empty_results(self):
        client = FakeClient(lambda q, p, limit: paged(205 if q == "TS=Q1" else 0, p, limit))
        manifest = self.quiet(run_plan, self.plan_file([query("Q1"), query("Q0")]), root=self.root, client=client, max_pages=2)
        self.assertEqual(manifest["queries"][0]["collected"], 100)
        self.assertTrue(manifest["queries"][0]["truncated"])
        self.assertEqual(manifest["queries"][1]["collected"], 0)
        self.assertFalse(manifest["queries"][1]["truncated"])

    def test_error_isolation_preserves_partial_records_and_redacts_key(self):
        def handler(q, page, limit):
            if q == "TS=Qbad" and page == 2:
                raise WosApiError(f"Error echoing {TEST_KEY}")
            return paged(2 if q == "TS=Qbad" else 1, page, limit)
        client = FakeClient(handler)
        manifest = self.quiet(run_plan, self.plan_file([query("Qbad"), query("Qgood")]), root=self.root, client=client, page_size=1)
        self.assertEqual(manifest["status"], "completed_with_errors")
        bad, good = manifest["queries"]
        self.assertEqual(bad["collected"], 1)
        self.assertEqual(bad["status"], "failed")
        self.assertEqual(good["status"], "completed")
        self.assertTrue((self.root / bad["processed_file"]).is_file())
        self.assert_no_secrets()

    def test_repeated_run_never_overwrites_existing_raw(self):
        path = self.plan_file([query("Q1")])
        client = FakeClient(lambda q, p, limit: paged(1, p, limit))
        first = self.quiet(run_plan, path, root=self.root, client=client)
        original = (self.root / first["queries"][0]["manifest_file"]).read_bytes()
        second = self.quiet(run_plan, path, root=self.root, client=client)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual((self.root / first["queries"][0]["manifest_file"]).read_bytes(), original)

    def test_secret_in_raw_response_is_rejected_before_archiving(self):
        def handler(q, p, limit):
            data = paged(1, p, limit)
            if q == "TS=Qbad":
                data["hits"][0]["title"] = TEST_KEY
            return data
        manifest = self.quiet(run_plan, self.plan_file([query("Qbad"), query("Qgood")]), root=self.root, client=FakeClient(handler))
        self.assertEqual(manifest["queries"][0]["status"], "failed")
        self.assertEqual(manifest["queries"][1]["status"], "completed")
        self.assert_no_secrets()


class MergeTests(unittest.TestCase):
    def record(self, qid, **fields):
        return dict(fields, provenance={"query_id": qid, "query": f"TS={qid}", "retrieved_at": f"2026-10-02T00:00:0{qid[-1]}+00:00", "database": "WOS"})

    def test_doi_dedup_and_matched_queries(self):
        records = [self.record("Q1", uid="WOS:1", doi="https://doi.org/10.1/ABC"), self.record("Q3", uid="WOS:3", doi="DOI:10.1/abc")]
        merged = merge_records(records)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_queries"], ["Q1", "Q3"])
        self.assertEqual(merged[0]["query_match_count"], 2)
        self.assertEqual(merged[0]["doi"], "10.1/abc")
        self.assertEqual([p["query"] for p in merged[0]["provenance_history"]], ["TS=Q1", "TS=Q3"])

    def test_uid_fallback_when_doi_is_missing(self):
        merged = merge_records([self.record("Q1", uid="WOS:1", doi=None), self.record("Q2", uid="wos:1", doi="10.1/abc")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["doi"], "10.1/abc")
        self.assertEqual(merged[0]["query_match_count"], 2)

    def test_title_year_fallback_and_no_false_merge_of_strong_ids(self):
        records = [self.record("Q1", title="Rice-water!", publish_year=2026), self.record("Q2", title="RICE water", publish_year=2026), self.record("Q3", title="Rice water", publish_year=2025), self.record("Q4", title="Rice water", publish_year=2026, doi="10.1/different")]
        self.assertEqual(len(merge_records(records)), 3)
        self.assertEqual(len(merge_records([{}, {}])), 2)

    def test_transitive_merges_preserve_all_history_and_original_inputs(self):
        records = [self.record("Q1", uid="WOS:1", doi=None), self.record("Q2", uid="WOS:2", doi="10.1/a"), self.record("Q3", uid="WOS:1", doi="10.1/a")]
        original = copy.deepcopy(records)
        merged = merge_records(records)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_queries"], ["Q1", "Q2", "Q3"])
        self.assertEqual(len(merged[0]["provenance_history"]), 3)
        self.assertEqual(merge_records(merged), merged)
        self.assertEqual(records, original)

    def test_same_query_multiple_runs_preserve_history_without_count_inflation(self):
        first = self.record("Q1", uid="WOS:1")
        second = copy.deepcopy(first)
        second["provenance"]["retrieved_at"] = "2026-10-03T00:00:00+00:00"
        merged = merge_records([first, second, first])[0]
        self.assertEqual(merged["query_match_count"], 1)
        self.assertEqual(len(merged["provenance_history"]), 2)

    def test_legacy_input_reserves_null_abstract_without_guessing(self):
        record = {"title": "A tempting title", "uid": "WOS:1", "provenance": {"query": "TS=rice", "database": "WOS"}}
        upgraded = prepare_record(record)
        self.assertIsNone(upgraded["abstract"])
        self.assertIsNone(upgraded["abstract_source"])
        self.assertIsNone(upgraded["abstract_retrieved_at"])
        self.assertEqual(upgraded["provenance"], record["provenance"])
        self.assertTrue(upgraded["matched_queries"][0].startswith("legacy_"))

    def test_summary_counts_and_overlap(self):
        records = merge_records([self.record("Q1", uid="WOS:1", publish_year=2026, source_title="J"), self.record("Q2", uid="WOS:1"), self.record("Q2", uid="WOS:2", publish_year=2025, source_title="K")])
        summary = summarize_records(records)
        self.assertEqual(summary["unique_records"], 2)
        self.assertEqual(summary["records_by_query"], [{"query_id": "Q1", "records": 1}, {"query_id": "Q2", "records": 2}])
        self.assertEqual(summary["overlap"]["matrix"], [[1, 1], [1, 2]])


class FileAndExportTests(TemporaryProject):
    def input_file(self, name="input.jsonl"):
        first = normalize_record(hit(1, identifiers={"doi": "10.1/test"}, citations=[{"db": "WOS", "count": 0}]), "TS=rice", retrieved_at="2026-10-02T00:00:00+00:00")
        first["provenance"]["query_id"] = "Q1"
        second = copy.deepcopy(first)
        second["provenance"].update(query_id="Q2", query="TS=paddy")
        records = merge_records([first, second])
        records[0] = prepare_record(records[0], source_file="fixture.jsonl")
        path = self.root / name
        write_jsonl(path, records)
        return path

    def test_merge_files_and_preserve_input_bytes(self):
        first = self.input_file("first.jsonl")
        second = self.input_file("second.jsonl")
        original = first.read_bytes()
        manifest = self.quiet(merge_searches, [first, second], "merged.jsonl", root=self.root)
        self.assertEqual(manifest["unique_records"], 1)
        self.assertEqual(manifest["duplicates_removed"], 1)
        self.assertEqual(first.read_bytes(), original)
        with self.assertRaises(FileExistsError):
            self.quiet(merge_searches, [first], "merged.jsonl", root=self.root)

    def test_csv_export_preserves_zero_and_header_fields(self):
        input_path = self.input_file()
        digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
        self.quiet(export_records, input_path, "records", formats=("csv",), root=self.root)
        with (self.root / "records.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            row = next(reader)
            self.assertEqual(reader.fieldnames, RECORD_COLUMNS)
            self.assertEqual(row["times_cited_wos"], "0")
            self.assertEqual(row["abstract"], "")
            self.assertIn("Q1", row["matched_queries"])
        self.assertEqual(hashlib.sha256(input_path.read_bytes()).hexdigest(), digest)
        self.assert_no_secrets()

    def test_csv_formula_text_is_literal_and_secret_input_is_blocked(self):
        input_path = self.input_file()
        records = read_jsonl(input_path)
        records[0]["title"] = "=HYPERLINK(\"example\")"
        input_path.write_text(json.dumps(records[0]) + "\n")
        self.quiet(export_records, input_path, "literal", formats=("csv",), root=self.root)
        with (self.root / "literal.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertTrue(next(csv.DictReader(stream))["title"].startswith("'="))
        records[0]["title"] = TEST_KEY
        input_path.write_text(json.dumps(records[0]) + "\n")
        with self.assertRaisesRegex(ValueError, "API key"):
            self.quiet(export_records, input_path, "blocked", formats=("csv",), root=self.root)
        self.assertFalse((self.root / "blocked.csv").exists())

    def test_xlsx_has_three_sheets_typed_values_and_no_key(self):
        input_path = self.input_file()
        searches = [{"query_id": qid, "description": "Search", "exact_query": "TS=rice", "retrieved_at": "2026-10-02T00:00:00+00:00", "total_hits": 10, "collected": 1, "pages": 1, "truncated": True, "status": "completed", "stop_reason": "record_cap"} for qid in ["Q1", "Q2"]]
        self.quiet(export_records, input_path, "workbook", searches=searches, root=self.root)
        sheets = read_xlsx_tables(self.root / "workbook.xlsx")
        self.assertEqual(list(sheets), ["Records", "Searches", "Summary"])
        self.assertEqual(len(sheets["Records"]), 2)
        self.assertEqual(len(sheets["Searches"]), 3)
        from scripts.export_records import XLSX_RECORD_COLUMNS
        self.assertEqual(list(sheets["Records"][0].values()), XLSX_RECORD_COLUMNS)
        self.assertEqual(sheets["Records"][1]["C2"], 2026)
        self.assertEqual(sheets["Records"][1]["K2"], 0)
        self.assertTrue(sheets["Searches"][1]["H2"])
        self.assertEqual(next(row["B4"] for row in sheets["Summary"] if "B4" in row), 1)
        self.assert_no_secrets()

    def test_failed_xlsx_export_does_not_leave_final_partial_files(self):
        input_path = self.input_file()
        with patch("scripts.export_records.build_xlsx", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                self.quiet(export_records, input_path, "failed_export", root=self.root)
        self.assertFalse((self.root / "failed_export.csv").exists())
        self.assertFalse((self.root / "failed_export.xlsx").exists())
        self.assertFalse((self.root / "failed_export.export.json").exists())

    def test_plan_merge_export_pipeline_and_search_manifest_rows(self):
        client = FakeClient(lambda q, p, limit: paged(2, p, limit))
        plan = self.quiet(run_plan, self.plan_file([query("Q1"), query("Q2")]), root=self.root, client=client)
        inputs = [self.root / result["processed_file"] for result in plan["queries"]]
        manifest = self.quiet(merge_searches, inputs, "merged.jsonl", root=self.root)
        self.assertEqual(len(manifest["searches"]), 2)
        self.assertEqual(manifest["unique_records"], 2)
        merged = read_jsonl(self.root / "merged.jsonl")
        self.assertEqual(merged[0]["matched_queries"], ["Q1", "Q2"])
        self.assertEqual(len(merged[0]["provenance_history"]), 2)
        exported = self.quiet(export_records, "merged.jsonl", "merged_export", formats=("csv",), root=self.root)
        self.assertEqual(exported["searches"], 2)
        self.assert_no_secrets()


if __name__ == "__main__":
    unittest.main()
