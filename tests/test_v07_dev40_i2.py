import json
import re
import unittest
from pathlib import Path

from scripts.evidence import run_v07_dev40_i2 as iteration2


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/evidence_batches/v07_dev40_i2"
BATCH = OUT / "batch_001"


class Dev40PromptIteration2Tests(unittest.TestCase):
    def test_i2_prompt_is_append_only_child_of_i1_and_findings_are_frozen(self):
        audit = iteration2.audit_prompt()
        self.assertEqual(audit["parent_prompt_sha256"],
                         "9c8a8200c09a2ac83d99f5222823f529b54a459c9b7abca78428bf715a68c86c")
        self.assertEqual(audit["iteration2_prompt_sha256"], iteration2.PROMPT_SHA256)
        self.assertTrue(audit["append_only_child"])
        self.assertTrue(audit["findings_rule_unchanged"])
        parent = (ROOT / "prompts/evidence_extraction_v07_i1.md").read_bytes()
        child = iteration2.PROMPT.read_bytes()
        self.assertTrue(child.startswith(parent))

    def test_i2_additions_cover_authorized_recall_and_regression_rules(self):
        text = iteration2.PROMPT.read_text(encoding="utf-8")
        required = ("OTHER_SHOULD_BE_AVOIDED", "Micronaire", "PFP_N", "floodwater EC",
                    "quaternary-N", "autochthonous", "crop-residue decomposition",
                    "measurement protocol", "strain-produced", "carbon mineralization",
                    "soil EC/conductivity", "other_treatments", "Validator v2")
        for phrase in required:
            self.assertIn(phrase, text)
        self.assertTrue((ROOT / "data/reports/v07_i2_change_plan.md").is_file())

    def test_i2_static_diff_audit_has_no_record_specific_answers(self):
        audit = iteration2.audit_prompt()
        self.assertFalse(audit["record_specific_answer_injection"])
        text = iteration2.PROMPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"WOS:\d{10,}", text))
        self.assertIsNone(re.search(r"\b10\.\d{4,9}/\S+", text))
        diff = (ROOT / "data/reports/v07_i1_to_i2_prompt_diff.md").read_text(encoding="utf-8")
        for section in ("## Added", "## Modified", "## Unchanged", "**Static audit:** passed"):
            self.assertIn(section, diff)

    def test_frozen_contract_validator_schema_metrics_gold_and_provenance(self):
        audit = iteration2.audit_prompt()
        for key in ("contract_v21_unchanged", "validator_v2_unchanged", "schema_unchanged",
                    "metric_v1_unchanged", "metric_v2_unchanged", "dev40_gold_unchanged"):
            self.assertTrue(audit[key], key)
        provenance = iteration2.audit_gold_provenance()
        self.assertEqual(provenance["gold_status"], "human_approved_development_gold")
        self.assertEqual(provenance["decision_rows"], 27)
        self.assertTrue(provenance["all_rows_human_approved"])

    def test_worker_bundle_is_dev40_only_and_excludes_analysis_inputs(self):
        manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["record_count"], 40)
        self.assertEqual(len(manifest["requests"]), 40)
        self.assertEqual(len(list((BATCH / "requests").glob("*.json"))), 40)
        self.assertEqual(len(list((BATCH / "responses").glob("*.json"))), 40)
        self.assertFalse(manifest["contains_gold"])
        self.assertFalse(manifest["contains_contract_v21"])
        self.assertFalse(manifest["contains_validator"])
        self.assertFalse(manifest["contains_future_holdout"])
        self.assertEqual(iteration2.sha_file(BATCH / "prompt/evidence_extraction.md"), iteration2.PROMPT_SHA256)
        names = [str(p.relative_to(BATCH)).casefold() for p in BATCH.rglob("*")]
        for forbidden in ("gold", "contract_v2", "error_report", "benchmark", "human_decision", "holdout", "profile", "change_plan", "prompt_diff"):
            self.assertFalse(any(forbidden in name for name in names), forbidden)

    def test_api_keys_are_not_written_to_i2_outputs(self):
        blobs = [p.read_bytes() for p in OUT.rglob("*") if p.is_file()]
        text = b"\n".join(blobs)
        self.assertIsNone(re.search(rb"s2k-[A-Za-z0-9]{20,}", text))
        self.assertNotIn(b"OPENALEX_API_KEY", text)
        self.assertNotIn(b"ELSEVIER_API_KEY", text)

    def test_validation_metrics_and_selection_are_development_only(self):
        validation = json.loads((OUT / "validation_v2.json").read_text(encoding="utf-8"))
        paired = json.loads((OUT / "paired_comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(validation["responses"], 40)
        self.assertEqual(validation["schema_valid"], 40)
        self.assertEqual(validation["identifier_valid"], 40)
        self.assertEqual(validation["response_contract_valid"], 40)
        self.assertGreaterEqual(validation["grounding_valid"], 0)
        self.assertEqual(validation["invalid_offsets"], 0)
        self.assertEqual(validation["orphan_anchors"], 0)
        self.assertIn(paired["recommendation"], {"KEEP", "REVISE", "REVERT"})
        self.assertEqual(paired["evaluation_role"], "development_set_only_not_independent_performance")
        self.assertEqual(paired["overall_v1"]["iteration2"]["f1"], json.loads((OUT / "metric_v1.json").read_text())["field_micro"]["f1"])
        self.assertEqual(paired["overall_v2"]["iteration2"]["f1"], json.loads((OUT / "metric_v2.json").read_text())["fields"]["f1"])
        self.assertFalse(paired["future_holdout_accessed"])
        self.assertEqual(set(paired["fields_v2"]), {"soil_type", "salinity_context", "experimental_scale", "plant_growth", "other", "microbial", "carbon", "soil_chemical", "fertilization", "amendments", "other_treatments", "methods"})
        integrity = json.loads((OUT / "controller_integrity_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(integrity["future_holdout_abstracts_accessed"])
        self.assertFalse(integrity["future_holdout_gold_created"])
        self.assertFalse(integrity["future_holdout_extraction_run"])


if __name__ == "__main__":
    unittest.main()
