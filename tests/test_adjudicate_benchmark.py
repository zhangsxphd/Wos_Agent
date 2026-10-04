"""Focused offline tests for source-linked v0.6 adjudication packets."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.evidence.adjudicate_benchmark import anchor_for, build_packets, field_values, match_findings, sentence_for


ROOT = Path(__file__).resolve().parents[1]


class AdjudicationHelpersTests(unittest.TestCase):
    def test_build_packets_emits_complete_source_linked_cases_offline(self):
        with TemporaryDirectory() as tmp:
            result = build_packets(
                ROOT,
                Path("data/evidence_batches/v06_gold_blind_iteration1_20261004/batch_001"),
                Path("data/processed/saline_paddy_v056_full_enriched_20261003.jsonl"),
                Path("data/evidence/20261003_192326_169425_evidence_57015bbc_evidence.jsonl"),
                Path("data/reports/v06_gold_benchmark_20261004_123933.json"),
                Path(tmp) / "reports",
                Path(tmp) / "reviewers",
            )
            self.assertEqual(result["field_mismatch_count"], 75)
            self.assertEqual(result["finding_mismatch_count"], 12)
            import json
            packet = json.loads((Path(tmp) / "reports/v06_field_discrepancies_20261004.json").read_text())
            self.assertEqual(len(packet["field_mismatches"]), 75)
            self.assertTrue(all(row["abstract_context"] for row in packet["field_mismatches"]))
            for role in "AB":
                reviewer = json.loads((Path(tmp) / f"reviewers/reviewer_{role}/reviewer_input.json").read_text())
                self.assertEqual(len(reviewer["cases"]), 108)
                self.assertTrue(all("field" in row for row in reviewer["cases"] if row["case_type"] == "field"))

    def test_field_values_treat_unknown_scale_as_empty(self):
        row = {"evidence": {"study_system": {"experimental_scale": "unknown", "crop": ["rice"]}}}
        self.assertEqual(field_values(row, "/evidence/study_system/experimental_scale"), [])
        self.assertEqual(field_values(row, "/evidence/study_system/crop"), ["rice"])

    def test_anchor_for_preserves_source_quote_and_offsets(self):
        row = {"evidence_support": {"/evidence/methods/0": {
            "source": "abstract", "evidence_text": "field trial", "start": 4, "end": 15}}}
        self.assertEqual(anchor_for(row, "/evidence/methods", 0), {
            "source": "abstract", "evidence_text": "field trial", "start": 4, "end": 15})

    def test_sentence_for_returns_original_abstract_context(self):
        abstract = "First sentence. A rice field trial tested irrigation. Last sentence."
        start = abstract.index("rice field")
        self.assertEqual(sentence_for(abstract, {"start": start, "end": start + len("rice field")}),
                         "A rice field trial tested irrigation.")

    def test_context_builder_accepts_self_supported_finding_anchor(self):
        from scripts.evidence.adjudicate_benchmark import add_contexts
        abstract = "A review summarized prior studies. Rice yield increased by 10%."
        start = abstract.index("Rice yield")
        item = {"source":"abstract","evidence_text":"Rice yield increased by 10%.",
                "start":start,"end":start+len("Rice yield increased by 10%.")}
        self.assertEqual(add_contexts(abstract,item),["Rice yield increased by 10%."])

    def test_finding_matching_is_exact_then_bidirectional_substring_only(self):
        gold = [{"evidence_text": "Yield rose by 10 percent."}, {"evidence_text": "Soil pH increased."}]
        predicted = [{"evidence_text": "soil pH increased."}, {"evidence_text": "Yield rose"},
                     {"evidence_text": "pH increased"}]
        pairs, pred_only, gold_only = match_findings(gold, predicted)
        self.assertEqual(pairs, [{"gold_index": 1, "prediction_index": 0, "exact": True},
                                 {"gold_index": 0, "prediction_index": 1, "exact": False}])
        self.assertEqual(pred_only, [2])
        self.assertEqual(gold_only, [])

    def test_finding_matching_does_not_add_semantic_synonyms(self):
        pairs, pred_only, gold_only = match_findings(
            [{"evidence_text": "Yield increased."}], [{"evidence_text": "Production rose."}])
        self.assertEqual(pairs, [])
        self.assertEqual(pred_only, [0])
        self.assertEqual(gold_only, [0])


if __name__ == "__main__":
    unittest.main()
