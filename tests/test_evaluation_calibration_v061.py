"""Offline regression tests for v0.6.1 evaluation calibration."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence.audit_v2 import audit_evidence_v2
from scripts.evidence.calibrate_v061 import build_candidate
from scripts.evidence.freeze_holdout import stratified_select
from scripts.evidence.metrics import item_metrics
from scripts.evidence.metrics_v2 import (
    BOUNDARY_EQUIVALENT,
    EXACT_NORMALIZED,
    boundary_equivalent,
    corpus_metrics_v2,
    finding_metrics_v2,
    field_metrics_v2,
    one_to_one_match,
)


class MetricV2Tests(unittest.TestCase):
    def item(self, value, abstract, start, end):
        quote = abstract[start:end]
        return {"value": value, "support": {"source": "abstract", "evidence_text": quote,
                                               "start": start, "end": end}}

    def record(self, evidence, support):
        return {"uid": "U1", "evidence": evidence, "evidence_support": support}

    def test_exact_v2_field_match(self):
        abstract = "Soil pH was measured."
        gold = self.record({"measurements": {"soil_chemical": ["Soil pH"]}},
                           {"/evidence/measurements/soil_chemical/0": {"source": "abstract", "evidence_text": "Soil pH", "start": 0, "end": 7}})
        pred = self.record({"measurements": {"soil_chemical": ["soil   pH"]}},
                           {"/evidence/measurements/soil_chemical/0": {"source": "abstract", "evidence_text": "soil pH", "start": 0, "end": 7}})
        result = field_metrics_v2(gold, pred, abstract)
        field = result["field_metrics"]["/evidence/measurements/soil_chemical"]
        self.assertEqual(field["exact_tp"], 1)
        self.assertEqual(field["boundary_tp"], 0)

    def test_boundary_equivalent_same_span_match(self):
        abstract = "Saline paddy soil was sampled."
        long = self.item("saline paddy soil", abstract, 0, 18)
        short = self.item("saline soil", abstract, 0, 18)
        self.assertTrue(boundary_equivalent(long, short, abstract))
        pairs, pred_only, gold_only = one_to_one_match([long], [short], abstract)
        self.assertEqual(pairs[0]["level"], BOUNDARY_EQUIVALENT)
        self.assertEqual((pred_only, gold_only), ([], []))

    def test_unrelated_same_field_text_does_not_match(self):
        abstract = "Soil pH increased. Rice yield declined."
        a = self.item("soil pH", abstract, 0, 17)
        b = self.item("rice yield", abstract, 19, 39)
        self.assertFalse(boundary_equivalent(a, b, abstract))
        self.assertEqual(one_to_one_match([a], [b], abstract)[0], [])

    def test_cross_field_never_matches(self):
        abstract = "Rice was sampled."
        anchor = {"source": "abstract", "evidence_text": "Rice", "start": 0, "end": 4}
        gold = self.record({"study_system": {"crop": ["Rice"], "soil_type": []}},
                           {"/evidence/study_system/crop/0": anchor})
        pred = self.record({"study_system": {"crop": [], "soil_type": ["Rice"]}},
                           {"/evidence/study_system/soil_type/0": anchor})
        result = field_metrics_v2(gold, pred, abstract)
        self.assertEqual(result["exact_tp"] + result["boundary_tp"], 0)
        self.assertEqual((result["fp"], result["fn"]), (1, 1))

    def test_experimental_scale_enum_is_exact_only(self):
        abstract = "A field-based pot experiment was conducted."
        g = self.item("pot", abstract, 0, len(abstract))
        p = self.item("field", abstract, 0, len(abstract))
        pairs, pred_only, gold_only = one_to_one_match([g], [p], abstract, scale=True)
        self.assertEqual(pairs, [])
        self.assertEqual((pred_only, gold_only), ([0], [0]))

    def test_matching_is_one_to_one(self):
        abstract = "Soil pH was measured."
        item = self.item("soil pH", abstract, 0, 7)
        pairs, pred_only, gold_only = one_to_one_match([item], [item, item], abstract)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["level"], EXACT_NORMALIZED)
        self.assertEqual(pred_only, [1])
        self.assertEqual(gold_only, [])

    def test_finding_boundary_match(self):
        abstract = "Soil salinity decreased significantly during irrigation."
        full = {"source": "abstract", "evidence_text": abstract, "start": 0, "end": len(abstract),
                "claim": abstract}
        short_text = "Soil salinity decreased"
        short = {"source": "abstract", "evidence_text": short_text, "start": 0, "end": len(short_text),
                 "claim": short_text}
        result = finding_metrics_v2({"evidence": {"findings": [full]}},
                                    {"evidence": {"findings": [short]}}, abstract)
        self.assertEqual(result["exact_match"], 0)
        self.assertEqual(result["boundary_match"], 1)
        self.assertEqual((result["unmatched_prediction"], result["unmatched_gold"]), (0, 0))

    def test_duplicate_finding_is_not_double_matched(self):
        abstract = "Yield increased by ten percent."
        finding = {"source": "abstract", "evidence_text": abstract, "start": 0, "end": len(abstract),
                   "claim": abstract}
        result = finding_metrics_v2({"evidence": {"findings": [finding]}},
                                    {"evidence": {"findings": [finding, finding]}}, abstract)
        self.assertEqual(result["exact_match"], 1)
        self.assertEqual(result["unmatched_prediction"], 1)

    def test_v1_item_metric_behavior_remains_strict_and_unchanged(self):
        old = item_metrics(["saline paddy soil"], ["saline soil"])
        self.assertEqual((old["tp"], old["fp"], old["fn"]), (0, 1, 1))


class AuditV2Tests(unittest.TestCase):
    def test_review_context_is_not_review_as_experiment_error(self):
        abstract = "This review discusses rice irrigation."
        start = abstract.index("rice irrigation")
        evidence = {"evidence": {"treatments": {"irrigation": ["flooded"]}},
                    "evidence_support": {"/evidence/treatments/irrigation/0": {
                        "source": "abstract", "evidence_text": "rice irrigation", "start": start,
                        "end": start + len("rice irrigation")}}}
        flags = audit_evidence_v2(evidence, {"abstract": abstract, "document_types": ["Review"]})
        self.assertTrue(any(x["flag"] == "review_context_flag" for x in flags))
        self.assertFalse(any(x["flag"] == "review_as_experiment_error" for x in flags))

    def test_review_prior_study_measurement_stays_context_without_attribution(self):
        abstract = "Recent studies applied flooded irrigation to rice."
        start = abstract.index("flooded irrigation")
        evidence = {"evidence": {"treatments": {"irrigation": ["flooded irrigation"]}},
                    "evidence_support": {"/evidence/treatments/irrigation/0": {
                        "source": "abstract", "evidence_text": "flooded irrigation", "start": start,
                        "end": start + len("flooded irrigation")}}}
        flags = audit_evidence_v2(evidence, {"abstract": abstract, "document_types": ["Review"]})
        self.assertEqual([x["flag"] for x in flags], ["review_context_flag"])

    def test_review_error_requires_explicit_conflicting_attribution(self):
        abstract = "Recent studies measured soil pH after irrigation."
        start = abstract.index("soil pH")
        evidence = {"evidence": {"measurements": {"soil_chemical": ["soil pH"]}},
                    "evidence_support": {"/evidence/measurements/soil_chemical/0": {
                        "source": "abstract", "evidence_text": "soil pH", "start": start,
                        "end": start + len("soil pH"), "attributed_to": "review_author"}}}
        flags = audit_evidence_v2(evidence, {"abstract": abstract, "document_types": ["Review"]})
        self.assertEqual([x["flag"] for x in flags], ["review_context_flag", "review_as_experiment_error"])


class CalibrationSafetyTests(unittest.TestCase):
    def test_holdout_selection_is_seeded_stratified_and_excludes_development(self):
        rows = []
        for i in range(185):
            rows.append({"uid": f"U{i:03d}", "doi": f"10.1234/{i}", "title": f"Title {i}",
                         "abstract": f"private abstract {i}", "publish_year": 2020 + i % 5,
                         "document_types": ["Article" if i % 7 else "Review"],
                         "source_title": f"Journal {i % 23}",
                         "matched_queries": [f"Q{i % 4}"], "query_match_count": 1})
        dev = {f"U{i:03d}" for i in range(7)}
        first, strata = stratified_select(rows, dev)
        second, strata2 = stratified_select(rows, dev)
        self.assertEqual(len(first), 20)
        self.assertEqual(first, second)
        self.assertEqual(strata, strata2)
        self.assertFalse({row["uid"] for row in first} & dev)
        self.assertTrue(all("abstract" not in row for row in first))

    def test_candidate_generation_does_not_overwrite_original_gold(self):
        root = Path(__file__).resolve().parents[1]
        gold_path = root / "data/evidence/20261003_192326_169425_evidence_57015bbc_evidence.jsonl"
        before = hashlib.sha256(gold_path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            result = build_candidate(root, Path(temp) / "candidate", Path(temp) / "human_review.md")
            candidate_path = Path(result["candidate_directory"]) / "candidate_gold.jsonl"
            manifest = json.loads((Path(result["candidate_directory"]) / "candidate_manifest.json").read_text())
            candidate_records = {row["uid"]: row for row in map(json.loads, candidate_path.read_text().splitlines())}
            self.assertEqual(len(candidate_records), 7)
            self.assertFalse(manifest["candidate_changes_are_human_approved"])
            self.assertGreater(result["human_review_items"], 0)
            self.assertLessEqual(result["human_review_items"], 25)
            moved = candidate_records["WOS:001631731300001"]["evidence"]["treatments"]
            self.assertIn("two levels (1% and 3% by weight)", moved["amendments"])
            self.assertNotIn("two levels (1% and 3% by weight)", moved["other_treatments"])
            change_rows = [json.loads(line) for line in (Path(result["candidate_directory"]) / "change_log.jsonl").read_text().splitlines()]
            self.assertTrue(next(row for row in change_rows if row["case_id"] == "F029")["proposal_applied_to_candidate_file"])
        after = hashlib.sha256(gold_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
