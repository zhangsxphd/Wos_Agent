import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence import run_v07_dev40_baseline as baseline


ROOT = Path(__file__).resolve().parents[1]


class Dev40BaselineTests(unittest.TestCase):
    def test_historical_prompt_hash_and_frozen_inputs_unchanged(self):
        self.assertEqual(baseline.sha_file(ROOT / "prompts/evidence_extraction.md"), baseline.PROMPT_SHA256)
        actual = {
            "schema": baseline.sha_file(ROOT / "schemas/evidence_matrix.schema.json"),
            "validator_v2": baseline.sha_file(ROOT / "scripts/extractors/schema_validator_v2.py"),
            "metric_v1": baseline.sha_file(ROOT / "scripts/evidence/metrics.py"),
            "metric_v2": baseline.sha_file(ROOT / "scripts/evidence/metrics_v2.py"),
            "contract_v1": baseline.sha_file(ROOT / "docs/evidence_field_contract_v2.md"),
        }
        self.assertEqual(actual, baseline.FROZEN_INPUT_HASHES)
        self.assertEqual(baseline.sha_file(ROOT / "docs/evidence_field_contract_v2_1.md"), baseline.CONTRACT_V21_SHA256)

    def test_validator_v2_is_the_only_grounding_validator(self):
        self.assertEqual(baseline.EvidenceValidator.__module__, "scripts.extractors.schema_validator_v2")
        self.assertIn("Validator v2", baseline.validate_and_measure.__code__.co_consts)

    def test_worker_task_and_checker_are_dev40_only(self):
        task = baseline._worker_task(40).casefold()
        checker = baseline._worker_checker(40).casefold()
        self.assertIn("process all 40 requests", task)
        self.assertIn("blind_worker_dev40_success", checker)
        self.assertNotIn("gold_freeze_manifest", task + checker)
        self.assertNotIn("candidate_gold.jsonl", task + checker)
        self.assertNotIn("evidence_field_contract_v2_1.md", task + checker)
        self.assertNotIn("future_holdout30", task + checker)
        self.assertNotIn("v06_holdout20", task + checker)
        self.assertNotIn("future_holdout30", Path(baseline.__file__).read_text(encoding="utf-8"))

    def test_worker_checker_requires_40_responses_and_empty_inference(self):
        checker = baseline._worker_checker(40)
        self.assertIn('== 40 == m["request_count"]', checker)
        self.assertIn('r["inference"][k] == []', checker)
        self.assertIn('r["screening"]["status"] == "maybe"', checker)

    def test_response_hash_manifest_detects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "responses").mkdir()
            payload = root / "responses/a.json"
            payload.write_text('{"response":1}\n', encoding="utf-8")
            manifest = {"requests": [{"uid": "WOS:1", "response_file": "responses/a.json"}]}
            (root / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            before = baseline.verify_response_hashes(root)
            self.assertEqual(len(before["WOS:1"]), 64)
            payload.write_text('{"response":2}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "worker_response_sha256_changed"):
                baseline.verify_response_hashes(root, before)

    def test_worker_instructions_exclude_gold_contract_and_holdout_files(self):
        source = Path(baseline.__file__).read_text(encoding="utf-8")
        self.assertIn("contains_gold\": False", source)
        self.assertIn("contains_contract_v21\": False", source)
        self.assertIn("contains_holdout\": False", source)
        self.assertIn("future_holdout_abstracts_accessed\": False", source)
        self.assertIn("only after metadata identifies a Dev40 UID", baseline.extract_dev_inputs.__doc__)

    def test_prepared_bundle_is_exact_dev40_and_has_no_gold_contract_or_holdout(self):
        batch = baseline.OUTPUT / "batch_001"
        manifest = json.loads((batch / "batch_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["uids"]), baseline.dev_uids())
        self.assertEqual(len(manifest["requests"]), 40)
        self.assertEqual(len(list((batch / "requests").glob("*.json"))), 40)
        self.assertEqual(len(list((batch / "responses").glob("*.json"))), 40)
        forbidden_names = ("gold", "contract_v2", "future_holdout", "holdout20", "ontology_review")
        relative_files = [str(p.relative_to(batch)).casefold() for p in batch.rglob("*")]
        self.assertFalse(any(token in name for name in relative_files for token in forbidden_names))
        self.assertFalse(manifest["contains_gold"])
        self.assertFalse(manifest["contains_contract_v21"])
        self.assertFalse(manifest["contains_future_holdout"])
        self.assertFalse(manifest["future_holdout_abstracts_accessed"])
        for row in manifest["requests"]:
            request = json.loads((batch / row["request_file"]).read_text(encoding="utf-8"))
            self.assertEqual(set(request), {"uid", "doi", "title", "journal", "year", "authors", "keywords", "document_types", "abstract",
                                              "payload_sha256", "request_sha256", "prompt_sha256", "schema_sha256", "canonical_input_sha256"})

    def test_response_hashes_are_saved_for_every_immutable_worker_response(self):
        batch = baseline.OUTPUT / "batch_001"
        saved = json.loads((baseline.OUTPUT / "worker_response_sha256.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 40)
        self.assertEqual(baseline.verify_response_hashes(batch, saved), saved)

    def test_v2_validation_and_baseline_report_are_complete_and_dev_only(self):
        report = json.loads((ROOT / "data/reports/v07_dev40_baseline_errors.json").read_text(encoding="utf-8"))
        self.assertTrue(report["DEV40_BASELINE_COMPLETE"])
        self.assertEqual(report["evaluation_role"], "development_set_only_not_independent_performance")
        self.assertEqual(report["validation"]["responses"], 40)
        self.assertEqual(report["validation"]["schema_valid"], 40)
        self.assertEqual(report["validation"]["identifier_valid"], 40)
        self.assertEqual(report["validation"]["response_contract_valid"], 40)
        self.assertEqual(report["validation"]["experimental_scale_validator_v2"]["rejected_uids"], [
            "WOS:000794189500001", "WOS:000899201500001", "WOS:000973038200001", "WOS:001332147700001", "WOS:001745283700001"
        ])
        self.assertEqual(report["future_holdout_status"], {"abstracts_accessed": False, "gold_created": False,
                                                           "extraction_run": False, "identities_accessed": False})

    def test_approved_gold_controller_hash_and_future_holdout_flags_are_intact(self):
        manifest = json.loads((baseline.OUTPUT / "controller_integrity_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline.sha_file(baseline.GOLD), manifest["approved_dev40_gold_sha256"])
        self.assertEqual(manifest["contract_v21_sha256"], baseline.CONTRACT_V21_SHA256)
        self.assertFalse(manifest["future_holdout_abstracts_accessed"])
        self.assertFalse(manifest["future_holdout_gold_created"])
        self.assertFalse(manifest["future_holdout_extraction_run"])
        self.assertFalse(manifest["future_holdout_identities_accessed"])
        self.assertEqual(baseline.sha_file(ROOT / "data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl"),
                         "fb58a888e06ee0efb7cba5de9d533b91582fb412697af2bf051ffb361fdf65c4")

    def test_historical_finding_rule_is_frozen_by_prompt_hash(self):
        prompt = (ROOT / "prompts/evidence_extraction.md").read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), baseline.PROMPT_SHA256)
        self.assertTrue(any(term in prompt.casefold() for term in ("findings", "finding")))
        self.assertEqual(baseline.sha_file(ROOT / "scripts/evidence/metrics.py"), baseline.FROZEN_INPUT_HASHES["metric_v1"])

    def test_eval_scope_is_development_only(self):
        self.assertEqual(baseline.V06_HOLDOUT_FINDING_REFERENCE, {"precision": 1.0, "recall": 0.9394, "f1": 0.9688})
        self.assertEqual(baseline.dev_uids.__module__, "scripts.evidence.run_v07_dev40_baseline")


if __name__ == "__main__":
    unittest.main()
