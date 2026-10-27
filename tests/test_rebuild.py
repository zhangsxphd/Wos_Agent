"""Portable end-to-end regressions for recovery and frozen v0.7 evaluation."""

import copy
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from scripts.evidence.final_evaluation import (FROZEN, digest, evaluate_run, file_hash, prepare_run,
    review_packet, validate_run, verify_frozen, verify_review)
from scripts.extractors.base import empty_record
from scripts.pipeline_utils import ROOT, read_jsonl, write_jsonl
from scripts.rebuild_workspace import rebuild
from suite_profiles import flatten, missing_historical_artifacts, select_tests


def source(n=1):
    return {"uid": f"SYNTHETIC:{n}", "doi": f"10.9999/synthetic-{n}", "title": f"Synthetic paper {n}",
            "source_title": "Synthetic journal", "publish_year": 2026,
            "abstract": "A field experiment grew rice. Rice yield increased by 10%."}


def reference(record):
    result = empty_record(record)
    result["evidence"]["study_system"]["crop"] = ["rice"]
    s = record["abstract"].index("rice")
    result["evidence_support"]["/evidence/study_system/crop/0"] = {
        "source": "abstract", "evidence_text": "rice", "start": s, "end": s + 4}
    text = "Rice yield increased by 10%."
    s = record["abstract"].index(text)
    result["evidence"]["findings"] = [{"claim": text, "evidence_text": text, "source": "abstract",
        "start": s, "end": s + len(text), "certainty": "explicit"}]
    return result


class FrozenRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.records = [source(1), source(2)]
        self.refs = [reference(r) for r in self.records]
        self.corpus = self.root / "records.jsonl"
        self.ref = self.root / "reference.jsonl"
        write_jsonl(self.corpus, self.records)
        write_jsonl(self.ref, self.refs)
        self.run = self.root / "run"

    def prepare(self):
        return prepare_run(self.corpus, self.run, reference_file=self.ref)

    def respond(self):
        bundle = json.loads((self.run / "worker/manifest.json").read_text())
        by_uid = {r["uid"]: r for r in self.refs}
        for item in bundle["requests"]:
            req = json.loads((self.run / "worker" / item["request_file"]).read_text())
            env = {k: req[k] for k in ("payload_sha256", "request_sha256", "prompt_sha256", "schema_sha256", "canonical_input_sha256")}
            env.update(model_label=None, response=by_uid[item["uid"]])
            (self.run / "worker" / item["response_file"]).write_text(json.dumps(env))

    def test_frozen_components_still_match_original_protocol(self):
        self.assertEqual(verify_frozen(), FROZEN)

    def test_missing_responses_are_visible_and_never_scored(self):
        self.prepare()
        result = evaluate_run(self.run, self.ref)
        self.assertEqual(result["validation"]["missing"], 2)
        self.assertIsNone(result["metrics"])
        self.assertEqual(result["quality_gate"], "NOT_EVALUATED_INCOMPLETE")

    def test_complete_synthetic_run_is_a_development_diagnostic(self):
        self.prepare()
        self.respond()
        result = evaluate_run(self.run, self.ref)
        self.assertEqual(result["metrics"]["v2"]["fields"]["f1"], 1)
        self.assertEqual(result["quality_gate"], "NOT_APPLICABLE_DEVELOPMENT")
        self.assertFalse(result["independent_performance_claim"])
        worker = self.run / "worker"
        self.assertFalse(any("reference" in p.name for p in worker.rglob("*")))

    def test_bad_offsets_are_rejected_before_scoring(self):
        self.prepare()
        self.respond()
        path = next((self.run / "worker/responses").glob("*.json"))
        env = json.loads(path.read_text())
        env["response"]["evidence_support"]["/evidence/study_system/crop/0"]["start"] = 0
        path.write_text(json.dumps(env))
        result = evaluate_run(self.run, self.ref)
        self.assertEqual(result["validation"]["rejected"], 1)
        self.assertIsNone(result["metrics"])

    def test_stale_response_and_unexpected_files_prevent_complete_validation(self):
        self.prepare()
        self.respond()
        path = next((self.run / "worker/responses").glob("*.json"))
        env = json.loads(path.read_text()); env["request_sha256"] = "stale"
        path.write_text(json.dumps(env))
        (path.parent / "unexpected.json").write_text("{}")
        result = validate_run(self.run)[3]
        self.assertFalse(result["complete_valid"])
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["unexpected_response_files"], ["unexpected.json"])

    def test_changed_source_or_worker_prompt_is_detected(self):
        self.prepare()
        (self.run / "worker/prompt.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "worker_component_hash_mismatch"):
            validate_run(self.run)

    def test_replacement_reference_is_rejected_even_if_schema_valid(self):
        self.prepare(); self.respond()
        self.ref.write_text(self.ref.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "reference_not_frozen_before_run"):
            evaluate_run(self.run, self.ref)

    def test_duplicate_ids_and_normalized_dois_do_not_publish_a_run(self):
        changed = copy.deepcopy(self.records)
        changed[1]["doi"] = "https://doi.org/" + changed[0]["doi"].upper()
        self.corpus.write_text("\n".join(map(json.dumps, changed)))
        with self.assertRaisesRegex(ValueError, "duplicate_doi"):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_review_templates_are_pending_and_bound_to_exact_text(self):
        packet = self.root / "review"
        review_packet(self.corpus, self.ref, packet)
        log = packet / "decisions.template.jsonl"
        with self.assertRaisesRegex(ValueError, "owner_review_required"):
            verify_review(self.records, self.refs, log)
        decisions = read_jsonl(log)
        for entry in decisions:
            entry.update(decision="ACCEPT", reviewer="synthetic test only", reviewed_at="2026-01-01T00:00:00+00:00")
        log.write_text("\n".join(map(json.dumps, decisions)))
        self.assertTrue(verify_review(self.records, self.refs, log))
        changed = copy.deepcopy(self.records); changed[0]["abstract"] += " Changed."
        with self.assertRaisesRegex(ValueError, "owner_review_required_or_stale"):
            verify_review(changed, self.refs, log)

    def test_new_holdout_requires_exact_30_and_prior_owner_review(self):
        with self.assertRaisesRegex(ValueError, "requires_30"):
            prepare_run(self.corpus, self.run, role="new_holdout")
        rows = [source(i) for i in range(30)]
        self.corpus.write_text("\n".join(map(json.dumps, rows)))
        with self.assertRaisesRegex(ValueError, "reviewed_reference_required"):
            prepare_run(self.corpus, self.run, role="new_holdout")
        self.assertFalse(self.run.exists())

    def prepare_synthetic_holdout(self):
        self.records = [source(i) for i in range(30)]
        self.refs = [reference(r) for r in self.records]
        self.corpus.write_text("\n".join(map(json.dumps, self.records)))
        self.ref.write_text("\n".join(map(json.dumps, self.refs)))
        self.log = self.root / "synthetic_review.jsonl"
        write_jsonl(self.log, [{"uid": r["uid"], "canonical_record_sha256": digest(r),
            "reference_record_sha256": digest(g), "decision": "ACCEPT", "reviewer": "synthetic test only",
            "reviewed_at": "2026-01-01T00:00:00+00:00"} for r, g in zip(self.records, self.refs)])
        prepare_run(self.corpus, self.run, "new_holdout", self.ref, self.log)

    def test_synthetic_reviewed_holdout_uses_v2_gate_and_reports_overlap_limit(self):
        self.prepare_synthetic_holdout(); self.respond()
        result = evaluate_run(self.run, self.ref, self.log)
        self.assertEqual(result["quality_gate"], "PASS")
        self.assertEqual(result["metrics"]["v2"]["fields"]["f1"], 1)
        self.assertFalse(result["independent_performance_claim"])
        manifest = json.loads((self.run / "manifest.json").read_text())
        self.assertEqual(manifest["historical_holdout_overlap"], "unknown_original_registry_lost")

    def test_grounded_but_missing_findings_fail_final_recall_gate(self):
        self.prepare_synthetic_holdout(); self.respond()
        for path in (self.run / "worker/responses").glob("*.json"):
            body = json.loads(path.read_text()); body["response"]["evidence"]["findings"] = []
            path.write_text(json.dumps(body))
        result = evaluate_run(self.run, self.ref, self.log)
        self.assertTrue(result["validation"]["complete_valid"])
        self.assertEqual(result["quality_gate"], "FAIL")
        self.assertFalse(result["gate_checks"]["findings_recall"])

    def test_holdout_rejects_development_doi_aliases(self):
        rows = [source(i) for i in range(30)]
        known = read_jsonl(ROOT / "data/evidence_benchmarks/v07_dev40_gold_work/abstracts.jsonl")[0]
        rows[0]["doi"] = "https://doi.org/" + known["doi"].upper()
        self.corpus.write_text("\n".join(map(json.dumps, rows)))
        with self.assertRaisesRegex(ValueError, "overlaps_known_development"):
            prepare_run(self.corpus, self.run, "new_holdout")

    def test_tampered_request_cannot_reuse_an_old_response(self):
        self.prepare(); self.respond()
        path = next((self.run / "worker/requests").glob("*.json"))
        request = json.loads(path.read_text()); request["abstract"] = "Replaced abstract."
        path.write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError, "request_hash_or_payload_mismatch"):
            validate_run(self.run)


class RecoveryIntegrationTests(unittest.TestCase):
    def test_rebuild_restores_real_dev40_and_archives_without_predictions_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            out = Path(tmp) / "recovery"
            result = rebuild(out)
            records = read_jsonl(out / "records.jsonl")
            self.assertEqual(len(records), 40)
            self.assertEqual(result["reference_valid"], 40)
            self.assertEqual(validate_run(out / "extraction_run")[3]["missing"], 40)
            self.assertTrue(all(r["abstract_retrieved_at"] is None for r in records))
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertFalse(manifest["historical_experiment_restored"])
            self.assertFalse(manifest["target_predictions_created"])
            for name, expected in manifest["files"].items():
                self.assertEqual(file_hash(out / name), expected)
            with zipfile.ZipFile(result["archive"]) as archive:
                self.assertFalse(any(name.endswith(".env") for name in archive.namelist()))
            with self.assertRaises(FileExistsError):
                rebuild(out)


class SuiteProfileTests(unittest.TestCase):
    def case(self, test_id):
        case = unittest.FunctionTestCase(lambda: None)
        case.id = lambda: test_id
        return case

    def test_historical_assertions_are_excluded_explicitly_and_preflight_lists_missing_inputs(self):
        historic = self.case("test_v07_final_freeze.V07FinalFreezeTests.test_contract_validator_schema_metrics_frozen")
        ordinary = self.case("test_data_layer.DataTests.test_plain")
        portable, exclusions = select_tests(unittest.TestSuite([historic, ordinary]))
        self.assertEqual([c.id() for c in flatten(portable)], [ordinary.id()])
        self.assertEqual(len(exclusions), 1)
        history, _ = select_tests(unittest.TestSuite([historic, ordinary]), "historical")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(missing_historical_artifacts(history, tmp), ["data/evidence_benchmarks/v07_final_system_freeze_manifest.json"])
