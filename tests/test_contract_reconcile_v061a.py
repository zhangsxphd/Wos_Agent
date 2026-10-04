"""Offline v0.6.1a contract reconciliation regressions."""

import unittest
import json
import tempfile
from pathlib import Path

from scripts.evidence.contract_reconcile_v061a import (
    DEV_REQUEST_MANIFEST,
    _load_development_requests,
    _remove_value,
    downstream_provenance,
    resolve_scale_and_methods,
)


class ExperimentalScaleContractTests(unittest.TestCase):
    def test_pot_experiment_with_logistic_regression_uses_pot_scale(self):
        result = resolve_scale_and_methods("A pot experiment measured rice traits using a logistic regression model.")
        self.assertEqual(result["experimental_scale"], "pot")
        self.assertTrue(any("logistic regression" in m.casefold() for m in result["methods"]))

    def test_field_study_with_hydrus_uses_field_scale(self):
        result = resolve_scale_and_methods("A field study using the HYDRUS-3D model monitored salt transport.")
        self.assertEqual(result["experimental_scale"], "field")
        self.assertTrue(any("hydrus-3d" in m.casefold() for m in result["methods"]))

    def test_logistic_regression_models_alone_do_not_define_model_scale(self):
        result = resolve_scale_and_methods("Binary logistic regression models were developed to estimate suitability.")
        self.assertNotEqual(result["experimental_scale"], "model")
        self.assertEqual(result["experimental_scale"], "unknown")

    def test_explicit_modeling_study_uses_model_scale(self):
        result = resolve_scale_and_methods("A modeling study was conducted to quantify regional water demand.")
        self.assertEqual(result["experimental_scale"], "model")

    def test_coupled_model_scenario_design_is_model_study(self):
        result = resolve_scale_and_methods(
            "By coupling PLUS model-InVEST model-crop coefficient method, eight different scenarios were designed."
        )
        self.assertEqual(result["experimental_scale"], "model")

    def test_spatial_logistic_analysis_without_study_level_model_claim_is_not_scale(self):
        result = resolve_scale_and_methods(
            "Long-term climate records, land-use data, and logistic regression-based spatial analysis were used. "
            "Binary logistic regression models were developed to quantify land-use suitability."
        )
        self.assertEqual(result["experimental_scale"], "unknown")
        self.assertTrue(any("logistic regression" in m.casefold() for m in result["methods"]))

    def test_field_scale_takes_precedence_over_model_method(self):
        result = resolve_scale_and_methods("A field-based monitoring study used a logistic regression model.")
        self.assertEqual(result["experimental_scale"], "field")
        self.assertTrue(any("logistic regression" in m.casefold() for m in result["methods"]))

    def test_two_physical_scales_are_marked_ambiguous(self):
        result = resolve_scale_and_methods("Field and pot experiments were conducted in parallel.")
        self.assertEqual(result["experimental_scale"], "unknown")
        self.assertIn("multi_scale_ambiguity", result["flags"])


class ReviewProvenanceTests(unittest.TestCase):
    def test_review_treatment_is_not_author_operated(self):
        result = downstream_provenance(["Review"], "/evidence/treatments/irrigation/0")
        self.assertEqual(result["paper_role"], "review_scope")
        self.assertEqual(result["treatment_attribution"], "review_derived")
        self.assertFalse(result["author_operated_treatment"])

    def test_review_system_value_is_scope_not_empirical_observation(self):
        result = downstream_provenance(["Review"], "/evidence/study_system/soil_type/0")
        self.assertEqual(result["system_value_role"], "scope_descriptor")
        self.assertFalse(result["empirical_system_observation"])


class CandidateDeltaTests(unittest.TestCase):
    def test_removing_value_reindexes_support_without_orphan_anchors(self):
        record = {"uid": "U1", "evidence": {"methods": ["input data", "field study", "HYDRUS-3D"]},
                  "evidence_support": {
                      "/evidence/methods/0": {"evidence_text": "input data"},
                      "/evidence/methods/1": {"evidence_text": "field study"},
                      "/evidence/methods/2": {"evidence_text": "HYDRUS-3D"}}}
        _remove_value(record, "/evidence/methods", "field study")
        self.assertEqual(record["evidence"]["methods"], ["input data", "HYDRUS-3D"])
        self.assertEqual(set(record["evidence_support"]), {"/evidence/methods/0", "/evidence/methods/1"})
        self.assertEqual(record["evidence_support"]["/evidence/methods/1"]["evidence_text"], "HYDRUS-3D")

    def test_development_loader_reads_only_manifested_request_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / DEV_REQUEST_MANIFEST
            manifest_path.parent.mkdir(parents=True)
            requests = []
            for uid in ("DEV1", "DEV2"):
                request_path = manifest_path.parent / f"{uid}.json"
                request_path.write_text(json.dumps({"uid": uid, "abstract": f"abstract {uid}",
                                                    "document_types": ["Article"]}))
                requests.append({"uid": uid, "request_file": str(request_path.relative_to(root))})
            manifest_path.write_text(json.dumps({"requests": requests}))
            result = _load_development_requests(root, {"DEV1", "DEV2"})
            self.assertEqual(set(result), {"DEV1", "DEV2"})
            with self.assertRaises(ValueError):
                _load_development_requests(root, {"DEV1", "HOLDOUT"})


if __name__ == "__main__":
    unittest.main()
