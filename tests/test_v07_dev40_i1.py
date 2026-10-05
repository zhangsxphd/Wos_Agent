import json
import re
import unittest
from pathlib import Path

from scripts.evidence import run_v07_dev40_i1 as iteration1


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/evidence_batches/v07_dev40_i1"
BATCH = OUT / "batch_001"


class Dev40PromptIteration1Tests(unittest.TestCase):
    def test_prompt_is_derived_from_historical_prompt_and_findings_are_unchanged(self):
        audit = iteration1.audit_prompt()
        self.assertEqual(audit["historical_prompt_sha256"],
                         "f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1")
        self.assertEqual(audit["iteration1_prompt_sha256"], iteration1.PROMPT_SHA256)
        self.assertTrue(audit["findings_rule_unchanged"])
        old = (ROOT / "prompts/evidence_extraction.md").read_text(encoding="utf-8")
        new = iteration1.PROMPT.read_text(encoding="utf-8")
        def findings(text):
            start = text.index("- Findings:")
            end = text.index("- Reviews:", start)
            return text[start:end]
        self.assertEqual(findings(new), findings(old))
        self.assertIn("OTHER_IS_LAST_RESORT = true", new)
        self.assertIn("measurements.plant_growth", new)
        self.assertIn("Prompt Iteration 1 field-routing additions", new)

    def test_prompt_has_no_dev40_identity_or_paper_specific_field_examples(self):
        text = iteration1.PROMPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"WOS:\d{10,}", text))
        self.assertIsNone(re.search(r"\b10\.\d{4,9}/\S+", text))
        for phrase in ("HYDRUS-3D", "saline-affected paddy fields",
                       "saline-affected upland fields", "in saline–alkali soils"):
            self.assertNotIn(phrase.casefold(), text.casefold())

    def test_frozen_contract_validator_schema_metrics_and_gold_are_unchanged(self):
        audit = iteration1.audit_prompt()
        self.assertTrue(audit["contract_v21_unchanged"])
        self.assertTrue(audit["validator_v2_unchanged"])
        self.assertTrue(audit["schema_unchanged"])
        self.assertTrue(audit["metric_v1_unchanged"])
        self.assertTrue(audit["metric_v2_unchanged"])
        self.assertTrue(audit["dev40_gold_unchanged"])
        self.assertEqual(iteration1.sha_file(iteration1.PROMPT), iteration1.PROMPT_SHA256)

    def test_gold_provenance_has_human_adjudication_for_r01_to_r19(self):
        audit = iteration1.audit_gold_provenance()
        self.assertEqual(audit["gold_status"], "ai_assisted_development_reference")
        self.assertEqual(audit["r01_r18_decision_rows"], 21)
        self.assertEqual(audit["r19_scale_decision_rows"], 6)
        self.assertFalse(audit["all_gold_independently_human_annotated"])
        self.assertFalse(audit["gold_content_modified"])

    def test_worker_bundle_contains_only_dev40_requests_and_protocol_inputs(self):
        manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["record_count"], 40)
        self.assertEqual(len(manifest["requests"]), 40)
        self.assertEqual(len(list((BATCH / "requests").glob("*.json"))), 40)
        self.assertEqual(len(list((BATCH / "responses").glob("*.json"))), 40)
        self.assertFalse(manifest["contains_gold"])
        self.assertFalse(manifest["contains_contract_v21"])
        self.assertFalse(manifest["contains_validator"])
        self.assertFalse(manifest["contains_future_holdout"])
        self.assertFalse(manifest["future_holdout_abstracts_accessed"])
        names = [str(path.relative_to(BATCH)).casefold() for path in BATCH.rglob("*")]
        for forbidden in ("gold", "contract_v2", "benchmark", "human_decision", "holdout", "profile"):
            self.assertFalse(any(forbidden in name for name in names), forbidden)
        self.assertEqual(iteration1.sha_file(BATCH / "prompt/evidence_extraction.md"), iteration1.PROMPT_SHA256)

    def test_api_credentials_do_not_appear_in_worker_bundle_or_log(self):
        scan_root = OUT
        blobs = [p.read_bytes() for p in scan_root.rglob("*") if p.is_file()]
        text = b"\n".join(blobs)
        self.assertIsNone(re.search(rb"s2k-[A-Za-z0-9]{20,}", text))
        self.assertNotIn(b"OPENALEX_API_KEY", text)
        self.assertNotIn(b"ELSEVIER_API_KEY", text)

    def test_official_validation_and_paired_metrics_are_development_only(self):
        validation = json.loads((OUT / "validation_v2.json").read_text(encoding="utf-8"))
        paired = json.loads((OUT / "paired_comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(validation["responses"], 40)
        self.assertEqual(validation["schema_valid"], 40)
        self.assertEqual(validation["identifier_valid"], 40)
        self.assertEqual(validation["response_contract_valid"], 40)
        self.assertEqual(validation["invalid_offsets"], 0)
        self.assertEqual(validation["orphan_anchors"], 0)
        self.assertEqual(validation["grounding_valid"], 38)
        self.assertEqual(validation["rejected_responses"], 2)
        self.assertEqual(paired["evaluation_role"], "development_set_only_not_independent_performance")
        self.assertEqual(paired["soil_type_counts"]["tp"], 21)
        self.assertEqual(paired["soil_type_counts"]["iteration1_fn"], 2)
        self.assertEqual(paired["findings"]["finding_regression"], False)
        self.assertEqual(paired["recommendation"], "REVISE")
        self.assertEqual(paired["plant_growth_to_other_routing"]["overlap_span"]["iteration1"], 0)
        integrity = json.loads((OUT / "controller_integrity_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(integrity["future_holdout_abstracts_accessed"])
        self.assertFalse(integrity["future_holdout_gold_created"])
        self.assertFalse(integrity["future_holdout_extraction_run"])


if __name__ == "__main__":
    unittest.main()
