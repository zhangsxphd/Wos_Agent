"""Offline contract, grounding, cache, immutable input and export tests."""
import copy
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock

from scripts.build_evidence_matrix import build_matrix
from scripts.evidence_export import EVIDENCE_COLUMNS, INFERENCE_COLUMNS, evidence_tables
from scripts.export_records import export_records, read_xlsx_tables
from scripts.extractors import CachedExtractor, ManualExtractor, OpenAIExtractor, ExtractorError, build_payload, empty_record, payload_key
from scripts.extractors.schema_validator import EvidenceValidator, EvidenceValidationError, SCHEMA_PATH
from scripts.pipeline_utils import read_jsonl, write_jsonl


TEXT = "A pot experiment tested rice with flooded irrigation. Grain yield increased by 12%."


def source(**changes):
    return dict({"uid": "WOS:test", "doi": "10.1000/evidence", "title": "Saline soil study",
                 "abstract": TEXT, "authors": [], "source_title": "Test Journal", "publish_year": 2025,
                 "document_types": ["Article"], "matched_queries": ["Q1"], "query_match_count": 1,
                 "provenance_history": []}, **changes)


def support(text, record):
    start = record["abstract"].index(text)
    return {"source": "abstract", "evidence_text": text, "start": start, "end": start + len(text)}


def valid(record=None):
    record = record or source()
    result = empty_record(record)
    e = result["evidence"]
    e["study_system"]["crop"] = ["rice"]
    e["study_system"]["experimental_scale"] = "pot"
    e["treatments"]["irrigation"] = ["flooded irrigation"]
    e["methods"] = ["pot experiment"]
    for pointer, text in (("study_system/crop/0", "rice"), ("study_system/experimental_scale", "A pot experiment"),
                          ("treatments/irrigation/0", "flooded irrigation"), ("methods/0", "pot experiment")):
        result["evidence_support"]["/evidence/" + pointer] = support(text, record)
    claim = "Grain yield increased by 12%."
    e["findings"] = [dict(support(claim, record), claim=claim, certainty="explicit")]
    return result


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.validator, self.record, self.row = EvidenceValidator(), source(), valid()

    def reject(self, row):
        with self.assertRaises(EvidenceValidationError):
            self.validator.validate(row, self.record)

    def test_valid_schema_and_grounding(self):
        self.assertIs(self.validator.validate(self.row, self.record), self.row)

    def test_null_abstract_needs_fulltext(self):
        record = source(abstract=None)
        row = self.validator.normalize(empty_record(record), record)
        self.assertEqual(row["screening"]["status"], "needs_fulltext")
        self.assertFalse(row["completeness"]["has_abstract"])
        self.assertFalse(any(row["inference"].values()))

    def test_missing_crop_and_scale_remain_unknown(self):
        row = self.validator.normalize(empty_record(self.record), self.record)
        self.assertEqual(row["evidence"]["study_system"]["crop"], [])
        self.assertIsNone(row["evidence"]["study_system"]["location"])
        self.assertEqual(row["evidence"]["study_system"]["experimental_scale"], "unknown")

    def test_no_abstract_cannot_contain_content_facts(self):
        record = source(abstract=None)
        row = empty_record(record)
        row["evidence"]["study_system"]["crop"] = ["rice"]
        with self.assertRaises(EvidenceValidationError):
            self.validator.validate(row, record)

    def test_orphan_support_rejected(self):
        self.row["evidence_support"]["/evidence/methods/3"] = support("rice", self.record)
        self.reject(self.row)

    def test_nonexistent_finding_anchor_rejected(self):
        self.row["inference"]["possible_gap"] = [{"kind": "inference", "statement": "A gap", "evidence_anchors": ["/evidence/findings/7"]}]
        self.reject(self.row)

    def test_title_cannot_fill_crop(self):
        self.row["evidence"]["study_system"]["crop"] = ["wheat"]
        self.row["evidence_support"]["/evidence/study_system/crop/0"] = {"source": "metadata", "evidence_text": "wheat", "metadata_path": "/title"}
        self.reject(self.row)

    def test_hallucinated_evidence_text_rejected(self):
        self.row["evidence"]["findings"][0]["evidence_text"] = "SOC increased by 50%."
        self.reject(self.row)

    def test_unsupported_numeric_claim_rejected(self):
        self.row["evidence"]["findings"][0]["claim"] = "Grain yield increased by 99%."
        self.reject(self.row)

    def test_unsupported_numeric_field_rejected(self):
        self.row["evidence"]["treatments"]["irrigation"][0] = "99 mm"
        self.reject(self.row)

    def test_wrong_quote_offsets_rejected(self):
        self.row["evidence"]["findings"][0]["start"] += 1
        self.reject(self.row)

    def test_invalid_schema_rejected(self):
        self.row["evidence"]["surprise"] = "unrequested fact"
        self.reject(self.row)

    def test_missing_support_rejected(self):
        self.row["evidence_support"].pop("/evidence/study_system/crop/0")
        self.reject(self.row)

    def test_wrong_experimental_scale_rejected(self):
        self.row["evidence"]["study_system"]["experimental_scale"] = "field"
        self.reject(self.row)

    def test_identity_changes_rejected(self):
        self.row["doi"] = "10.1000/different"
        self.reject(self.row)

    def test_inference_separate_and_anchored(self):
        before = copy.deepcopy(self.row["evidence"])
        self.row["inference"]["possible_gap"] = [{"kind": "inference", "statement": "This abstract does not establish generality beyond this pot study.", "evidence_anchors": ["/evidence/methods/0", "/evidence/findings/0"]}]
        self.validator.validate(self.row, self.record)
        self.assertEqual(before, self.row["evidence"])

    def test_inference_requires_anchor(self):
        self.row["inference"]["possible_gap"] = [{"kind": "inference", "statement": "A gap", "evidence_anchors": []}]
        self.reject(self.row)

    def test_inference_empty_anchor_rejected(self):
        self.row["inference"]["possible_gap"] = [{"kind": "inference", "statement": "A gap", "evidence_anchors": ["/evidence/study_system/location"]}]
        self.reject(self.row)

    def test_inference_title_anchor_rejected(self):
        self.row["inference"]["possible_gap"] = [{"kind": "inference", "statement": "A gap", "evidence_anchors": ["/title"]}]
        self.reject(self.row)

    def test_completeness_is_objective(self):
        row = self.validator.normalize(self.row, self.record)
        self.assertEqual(row["evidence_completeness"], "high")
        self.assertFalse(row["completeness"]["needs_fulltext"])
        row["evidence"]["methods"] = []
        row["evidence_support"].pop("/evidence/methods/0")
        row.pop("completeness")
        row.pop("evidence_completeness")
        normalized = self.validator.normalize(row, self.record)
        self.assertEqual(normalized["evidence_completeness"], "medium")
        self.assertTrue(normalized["completeness"]["needs_fulltext"])

    def test_payload_excludes_user_context_and_secrets(self):
        payload = build_payload(source(api_key="fixture-secret", user_research="personal", inference={"guess": "x"}))
        self.assertEqual(set(payload), {"uid", "doi", "title", "journal", "year", "authors", "keywords", "abstract", "document_types"})
        self.assertNotIn("fixture-secret", json.dumps(payload))

    def test_reserved_openai_is_offline(self):
        with self.assertRaises(ExtractorError):
            OpenAIExtractor().extract(self.record)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record, self.row = source(), valid()
        self.input = self.root / "input.jsonl"
        write_jsonl(self.input, [self.record])
        self.manual = self.root / "manual"
        self.manual.mkdir()
        (self.manual / (payload_key(self.record) + ".json")).write_text(json.dumps(self.row))

    def build(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return build_matrix(self.input, root=self.root, **kwargs)

    def test_manual_fixture_workflow_and_raw_retention(self):
        result = self.build(manual_dir=self.manual)
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["rejected"], 0)
        raw = self.root / result["results"][0]["raw_response_file"]
        self.assertEqual(json.loads(raw.read_text()), self.row)

    def test_prepare_only_whitelisted_payload(self):
        result = self.build(prepare_only=True)
        self.assertEqual(result["status"], "prepared")
        payload = json.loads(Path(result["requests"][0]["request_file"]).read_text())
        self.assertEqual(payload, build_payload(self.record))

    def test_no_abstract_does_not_call_extractor(self):
        self.input.unlink()
        write_jsonl(self.input, [source(abstract=None)])
        extractor = Mock()
        extractor.stats = {}
        extractor.schema_sha256 = extractor.prompt_sha256 = None
        result = self.build(extractor=extractor)
        extractor.extract.assert_not_called()
        self.assertEqual(result["screening_counts"]["needs_fulltext"], 1)
        self.assertIsNone(result["results"][0]["raw_response_file"])

    def test_canonical_immutable_and_overwrite_refused(self):
        before = self.input.read_bytes()
        self.build(manual_dir=self.manual)
        self.assertEqual(before, self.input.read_bytes())
        with self.assertRaises(FileExistsError):
            self.build(output=self.input, manual_dir=self.manual)

    def test_bad_record_does_not_destroy_completed_evidence(self):
        second = source(uid="WOS:second", doi="10.1000/second")
        self.input.unlink()
        write_jsonl(self.input, [self.record, second])
        invalid = valid(second)
        invalid["evidence"]["findings"][0]["claim"] = "Yield increased by 99%."
        (self.manual / (payload_key(second) + ".json")).write_text(json.dumps(invalid))
        result = self.build(manual_dir=self.manual)
        rows = read_jsonl(self.root / result["evidence_file"])
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(len(rows[0]["evidence"]["findings"]), 1)
        self.assertEqual(rows[1]["screening"]["status"], "needs_fulltext")
        self.assertTrue((self.root / result["results"][1]["raw_response_file"]).is_file())

    def test_cache_hit_avoids_manual_extraction(self):
        fallback = Mock(name="manual")
        fallback.name = "manual"
        fallback.extract.return_value = self.row
        cache = CachedExtractor(fallback=fallback, root=self.root)
        self.assertEqual(cache.extract(self.record), self.row)
        cache.extract(self.record)
        fallback.extract.assert_called_once()
        self.assertEqual(cache.stats, {"cache_hits": 1, "extractions": 1})

    def test_input_prompt_and_schema_invalidate_cache(self):
        prompt = self.root / "prompt.md"
        prompt.write_text("source-only")
        schema = self.root / "schema.json"
        schema.write_bytes(SCHEMA_PATH.read_bytes())
        cache = CachedExtractor(root=self.root, prompt_path=prompt, schema_path=schema)
        first = cache.cache_key(self.record)
        self.assertNotEqual(first, cache.cache_key(source(abstract=TEXT + " More context.")))
        prompt.write_text("source-only v2")
        self.assertNotEqual(first, CachedExtractor(root=self.root, prompt_path=prompt, schema_path=schema).cache_key(self.record))
        changed = json.loads(schema.read_text())
        changed["description"] = "Changed contract"
        schema.write_text(json.dumps(changed))
        self.assertNotEqual(first, CachedExtractor(root=self.root, prompt_path=prompt, schema_path=schema).cache_key(self.record))

    def test_invalid_response_is_not_cached(self):
        invalid = copy.deepcopy(self.row)
        invalid["evidence"]["findings"][0]["claim"] = "99%"
        fallback = Mock()
        fallback.name = "manual"
        fallback.extract.return_value = invalid
        cache = CachedExtractor(fallback=fallback, root=self.root)
        with self.assertRaises(EvidenceValidationError):
            cache.extract(self.record)
        self.assertFalse(list((self.root / "data/cache").rglob("*.json")))

    def test_api_key_rejected_from_cache_raw_output_and_log(self):
        secret = "fixture-key-never-export"
        (self.root / ".env").write_text("OPENALEX_API_KEY=" + secret)
        bad = copy.deepcopy(self.row)
        bad["screening"]["reason"] = secret
        (self.manual / (payload_key(self.record) + ".json")).write_text(json.dumps(bad))
        log = io.StringIO()
        with redirect_stdout(log):
            result = build_matrix(self.input, root=self.root, manual_dir=self.manual)
        self.assertEqual(result["rejected"], 1)
        self.assertIsNone(result["results"][0]["raw_response_file"])
        self.assertNotIn(secret, log.getvalue())
        for path in (self.root / "data").rglob("*"):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes())

    def test_cache_only_replay(self):
        self.build(manual_dir=self.manual)
        replay = self.build(cache_only=True)
        self.assertEqual(replay["extraction_stats"]["cache_hits"], 1)
        self.assertEqual(replay["rejected"], 0)

    def test_evidence_and_inference_export_separate(self):
        row = copy.deepcopy(self.row)
        row["inference"]["transferable_idea"] = [{"kind": "inference", "statement": "Compare this pot design in a future field test.", "evidence_anchors": ["/evidence/methods/0"]}]
        evidence, inference = evidence_tables([self.record], [row])
        self.assertEqual(evidence[0][EVIDENCE_COLUMNS.index("main_findings")], "Grain yield increased by 12%.")
        self.assertNotIn("future field test", str(evidence))
        self.assertIn("[inference]", inference[0][INFERENCE_COLUMNS.index("transferable_idea")])
        self.assertIn("/evidence/methods/0", str(inference))

    def test_missing_or_duplicate_evidence_export_refused(self):
        with self.assertRaises(ValueError):
            evidence_tables([self.record], [])
        with self.assertRaises(ValueError):
            evidence_tables([self.record], [self.row, self.row])

    def test_xlsx_evidence_export_and_csv_compatibility(self):
        matrix = self.root / "evidence.jsonl"
        write_jsonl(matrix, [self.row])
        before = self.input.read_bytes()
        with redirect_stdout(io.StringIO()):
            export_records(self.input, self.root / "out", root=self.root, evidence_file=matrix)
        tables = read_xlsx_tables(self.root / "out.xlsx")
        self.assertEqual(set(tables), {"Records", "Searches", "Summary", "Evidence", "Research_Inference"})
        self.assertEqual([tables["Evidence"][0].get(chr(65 + n) + "1", "") for n in range(20)], EVIDENCE_COLUMNS)
        self.assertEqual([tables["Research_Inference"][0].get(chr(65 + n) + "1", "") for n in range(6)], INFERENCE_COLUMNS)
        self.assertEqual(tables["Evidence"][1]["Q2"], "Grain yield increased by 12%.")
        self.assertEqual(before, self.input.read_bytes())
        with (self.root / "out.csv").open(encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.reader(stream))
        self.assertEqual(len(csv_rows), 2)
        self.assertNotIn("main_findings", csv_rows[0])

    def test_xlsx_overlong_evidence_refused_without_truncation(self):
        text = "A" * 33000
        record = source(abstract=text)
        row = empty_record(record)
        row["evidence"]["findings"] = [dict(support(text, record), claim=text, certainty="explicit")]
        self.input.unlink()
        write_jsonl(self.input, [record])
        matrix = self.root / "long_evidence.jsonl"
        write_jsonl(matrix, [row])
        with self.assertRaises(ValueError):
            export_records(self.input, self.root / "too_long", root=self.root, evidence_file=matrix)
        self.assertFalse((self.root / "too_long.xlsx").exists())
