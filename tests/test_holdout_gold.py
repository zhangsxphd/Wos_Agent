import json
import tempfile
import unittest
from pathlib import Path

from scripts.extractors.schema_validator import EvidenceValidator
from scripts.evidence.holdout_gold import (
    FROZEN_PROMPT_SHA, _category, agreement_summary, assemble_candidate,
    compare_pair, disagreement_items, prepare, sha, validate_registry,
)

ROOT = Path(__file__).resolve().parents[1]


class HoldoutGoldTests(unittest.TestCase):
    def _registry(self, root, overlap=False):
        base = root / "data/evidence_benchmarks/v06_holdout20"
        base.mkdir(parents=True)
        (base / "identities.json").write_text(json.dumps([
            {"uid": f"H{i:02}", "doi": f"10.1/{i}"} for i in range(20)
        ]))
        dev = root / "data/evidence_benchmarks/gold_v04/identities.json"
        dev.parent.mkdir(parents=True)
        dev.write_text(json.dumps(["H00" if overlap else f"D{i}" for i in range(7)]))
        (base / "selection_manifest.json").write_text(json.dumps({
            "sample_size": 20, "HOLDOUT_FROZEN_BEFORE_PROMPT_ITERATION_2": True,
            "holdout_abstracts_included": False, "holdout_evidence_created": False,
            "holdout_responses_created": False,
        }))
        return base

    def test_frozen_registry_has_exact_twenty_and_zero_development_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self._registry(root)
            self.assertEqual(len(validate_registry(root)), 20)

    def test_registry_rejects_development_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._registry(root, overlap=True)
            with self.assertRaisesRegex(ValueError, "development_overlap"):
                validate_registry(root)

    def test_prepare_creates_prompt_blind_isolated_annotator_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self._registry(root)
            (root / "prompts").mkdir(); (root / "prompts/evidence_extraction.md").write_bytes(
                (ROOT / "prompts/evidence_extraction.md").read_bytes())
            (root / "schemas").mkdir(); (root / "schemas/evidence_matrix.schema.json").write_text("{}")
            (root / "docs").mkdir(); (root / "docs/evidence_field_contract_v1.md").write_text("contract")
            (root / "data/processed").mkdir(parents=True)
            records = [{"uid": f"H{i:02}", "doi": f"10.1/{i}", "title": f"title {i}",
                        "source_title": "journal", "publish_year": 2025,
                        "document_types": ["Article"], "abstract": f"Abstract for paper {i}."}
                       for i in range(20)]
            (root / "data/processed/saline_paddy_v056_full_enriched_20261003.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in records))
            out = root / "data/evidence_benchmarks/v06_holdout20_gold_work"
            result = prepare(root, out)
            self.assertEqual(result["record_count"], 20)
            a, b = out / "annotator_A", out / "annotator_B"
            self.assertEqual((a / "input.jsonl").read_bytes(), (b / "input.jsonl").read_bytes())
            for bundle in (a, b):
                names = {p.name for p in bundle.iterdir()}
                self.assertNotIn("evidence_extraction.md", names)
                self.assertNotIn("responses", names)
                manifest = json.loads((bundle / "bundle_manifest.json").read_text())
                self.assertFalse(manifest["extraction_prompt_included"])
                self.assertFalse(manifest["model_predictions_included"])
            (a / "annotations.jsonl").write_text("A-only")
            self.assertFalse((b / "annotations.jsonl").exists())

    def test_frozen_prompt_sha_constant_matches_current_file(self):
        self.assertEqual(sha((ROOT / "prompts/evidence_extraction.md").read_bytes()), FROZEN_PROMPT_SHA)

    def test_field_category_normalization(self):
        self.assertEqual(_category("/evidence/study_system/crop/0"), "/evidence/study_system")
        self.assertEqual(_category("/evidence/findings/2"), "/evidence/findings")

    def test_deterministic_agreement_counts_exact_boundary_and_presence(self):
        def ann(crop, quote, scale="unknown", finding=None):
            return {"evidence": {
                "study_system": {"crop": [crop], "experimental_scale": scale},
                "findings": ([{"claim": finding, "evidence_text": finding, "source": "abstract",
                               "start": 0, "end": len(finding), "certainty": "explicit"}] if finding else []),
                "treatments": {}, "measurements": {}, "methods": [],
                "mechanisms_explicit": [], "limitations_explicit": [], "author_interpretations": [],
            }, "evidence_support": {
                "/evidence/study_system/crop/0": {"evidence_text": quote},
                **({"/evidence/study_system/experimental_scale": {"evidence_text": "field study"}} if scale != "unknown" else {}),
            }}
        a = ann("rice was grown in field plots", "rice was grown in field plots", "field", "yield increased")
        b = ann("rice was grown", "rice was grown", "field", "yield increased")
        stats, a_only, b_only, categories, _agreements = compare_pair(a, b)
        self.assertEqual(stats["/evidence/study_system"]["boundary_equivalent"], 1)
        self.assertEqual(stats["/evidence/study_system"]["exact"], 1)
        self.assertEqual(stats["/evidence/findings"]["exact"], 1)
        self.assertEqual((a_only, b_only, categories), ([], [], []))
        self.assertEqual(compare_pair(a, b), compare_pair(a, b))

    def test_disagreement_generation_is_deterministic_and_contains_only_mismatches(self):
        def ann(crop):
            return {"uid": "H0", "doi": "10.1/0", "evidence": {
                "study_system": {"crop": [crop], "soil_type": [], "salinity_context": [],
                                 "location": None, "experimental_scale": "unknown"},
                "treatments": {}, "measurements": {}, "methods": [], "findings": [],
                "mechanisms_explicit": [], "limitations_explicit": [], "author_interpretations": [],
            }, "evidence_support": {"/evidence/study_system/crop/0": {
                "source": "abstract", "evidence_text": crop, "start": 0, "end": len(crop)}}}
        inp = [{"uid": "H0", "doi": "10.1/0", "abstract": "Rice was measured. Barley was measured."}]
        a, b = ann("Rice"), ann("Barley")
        contract = "| `study_system.crop` | crop rule |"
        one = disagreement_items([a], [b], inp, contract)
        self.assertEqual(one, disagreement_items([a], [b], inp, contract))
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["annotator_A"][0]["value"], "Rice")
        self.assertEqual(one[0]["annotator_B"][0]["value"], "Barley")
        self.assertEqual(agreement_summary([a], [b])["/evidence/study_system"]["A_only"], 1)

    def test_candidate_preserves_identity_and_contains_no_prediction_provenance(self):
        abstract = "Rice was grown."
        support = {"source": "abstract", "evidence_text": "Rice", "start": 0, "end": 4}
        ann = {"schema_version": "0.4", "uid": "H0", "doi": "10.1/0", "title": "Title",
               "screening": {"status": "maybe", "reason": "Gold annotation only", "confidence": "low"},
               "evidence": {"study_system": {"crop": ["Rice"], "soil_type": [], "salinity_context": [],
                    "location": None, "experimental_scale": "unknown"},
                    "treatments": {k: [] for k in ("irrigation", "water_regime", "amendments", "fertilization", "biological_treatments", "other_treatments")},
                    "measurements": {k: [] for k in ("soil_physical", "soil_chemical", "carbon", "nitrogen", "microbial", "greenhouse_gases", "plant_growth", "yield", "water_use", "other")},
                    "methods": [], "findings": [], "mechanisms_explicit": [], "limitations_explicit": [], "author_interpretations": []},
               "evidence_support": {"/evidence/study_system/crop/0": support},
               "inference": {k: [] for k in ("mechanistic_interpretation", "connection_to_user_research", "possible_gap", "transferable_idea", "needs_fulltext_for")}}
        source = {"uid": "H0", "doi": "10.1/0", "title": "Title", "abstract": abstract}
        candidate, audit, review = assemble_candidate([ann], [ann], [source], [], [])
        self.assertEqual((candidate[0]["uid"], candidate[0]["doi"]), ("H0", "10.1/0"))
        self.assertNotIn("extraction_provenance", candidate[0])
        self.assertNotIn("model_prediction", candidate[0])
        self.assertEqual((audit, review), ([], []))

    def test_candidate_excludes_agreed_fact_rejected_by_frozen_validator_for_human_review(self):
        # Reuse a valid record template, then make both annotators agree on a
        # field-scale -> field mapping that the unchanged validator rejects.
        abstract = "A field-scale comparison was reported."
        support = {"source": "abstract", "evidence_text": "field-scale", "start": 2, "end": 13}
        ann = {"schema_version": "0.4", "uid": "H0", "doi": "10.1/0", "title": "Title",
               "screening": {"status": "maybe", "reason": "Gold annotation only", "confidence": "low"},
               "evidence": {"study_system": {"crop": [], "soil_type": [], "salinity_context": [],
                    "location": None, "experimental_scale": "field"},
                    "treatments": {k: [] for k in ("irrigation", "water_regime", "amendments", "fertilization", "biological_treatments", "other_treatments")},
                    "measurements": {k: [] for k in ("soil_physical", "soil_chemical", "carbon", "nitrogen", "microbial", "greenhouse_gases", "plant_growth", "yield", "water_use", "other")},
                    "methods": [], "findings": [], "mechanisms_explicit": [], "limitations_explicit": [], "author_interpretations": []},
               "evidence_support": {"/evidence/study_system/experimental_scale": support},
               "inference": {k: [] for k in ("mechanistic_interpretation", "connection_to_user_research", "possible_gap", "transferable_idea", "needs_fulltext_for")}}
        source = {"uid": "H0", "doi": "10.1/0", "title": "Title", "abstract": abstract}
        candidate, _audit, review = assemble_candidate([ann], [ann], [source], [], [], ROOT / "schemas/evidence_matrix.schema.json")
        self.assertEqual(candidate[0]["evidence"]["study_system"]["experimental_scale"], "unknown")
        self.assertEqual(len(review), 1)
        EvidenceValidator().validate(candidate[0], source)


if __name__ == "__main__":
    unittest.main()
