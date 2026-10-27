"""Explicit separation of portable regressions and historical experiment audits.

Historical cases keep their original assertions. They are never rewritten to
accept regenerated data or silently counted as passing on a fresh clone.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_CASES = {
    "test_adjudicate_benchmark.AdjudicationHelpersTests.test_build_packets_emits_complete_source_linked_cases_offline": "data/processed/saline_paddy_v056_full_enriched_20261003.jsonl",
    "test_evaluation_calibration_v061.CalibrationSafetyTests.test_candidate_generation_does_not_overwrite_original_gold": "data/evidence/20261003_192326_169425_evidence_57015bbc_evidence.jsonl",
    "test_finalize_holdout_gold.FinalizeHoldoutGoldTests.test_user_decisions_apply_to_new_gold_and_leave_sources_immutable": "data/evidence_benchmarks/v06_holdout20_gold_work/holdout_gold_candidate/candidate_gold.jsonl",
    "test_v07_contract_v2.V07ContractV2CandidateTests.test_contract_v2_is_a_separate_candidate_and_v1_is_unchanged": "data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json",
    "test_v07_contract_v2.V07ContractV2CandidateTests.test_dev40_abstracts_not_used_in_phase_1a": "data/evidence_benchmarks/v07_dev40/selection_manifest.json",
    "test_v07_contract_v2.V07ContractV2CandidateTests.test_future_holdout_remains_unread_unannotated_and_unextracted": "data/evidence_benchmarks/v07_future_holdout30/selection_manifest.json",
    "test_v07_contract_v2.V07ContractV2CandidateTests.test_v06_frozen_source_hashes_and_all_historical_responses_unchanged": "data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json",
    "test_v07_dev40_baseline.Dev40BaselineTests.test_approved_gold_controller_hash_and_future_holdout_flags_are_intact": "data/evidence_batches/v07_dev40_baseline/controller_integrity_manifest.json",
    "test_v07_dev40_baseline.Dev40BaselineTests.test_prepared_bundle_is_exact_dev40_and_has_no_gold_contract_or_holdout": "data/evidence_batches/v07_dev40_baseline/batch_001/batch_manifest.json",
    "test_v07_dev40_baseline.Dev40BaselineTests.test_response_hashes_are_saved_for_every_immutable_worker_response": "data/evidence_batches/v07_dev40_baseline/worker_response_sha256.json",
    "test_v07_dev40_baseline.Dev40BaselineTests.test_v2_validation_and_baseline_report_are_complete_and_dev_only": "data/reports/v07_dev40_baseline_errors.json",
    "test_v07_dev40_final_gold_v21.V07FinalGoldV21Tests.test_final_gold_freeze_status_and_dev_identity": "data/evidence_benchmarks/v07_dev40/identities.json",
    "test_v07_dev40_i1.Dev40PromptIteration1Tests.test_official_validation_and_paired_metrics_are_development_only": "data/evidence_batches/v07_dev40_i1/validation_v2.json",
    "test_v07_dev40_i1.Dev40PromptIteration1Tests.test_worker_bundle_contains_only_dev40_requests_and_protocol_inputs": "data/evidence_batches/v07_dev40_i1/batch_001/batch_manifest.json",
    "test_v07_dev40_i2.Dev40PromptIteration2Tests.test_i2_additions_cover_authorized_recall_and_regression_rules": "data/reports/v07_i2_change_plan.md",
    "test_v07_dev40_i2.Dev40PromptIteration2Tests.test_validation_metrics_and_selection_are_development_only": "data/evidence_batches/v07_dev40_i2/validation_v2.json",
    "test_v07_dev40_i2.Dev40PromptIteration2Tests.test_worker_bundle_is_dev40_only_and_excludes_analysis_inputs": "data/evidence_batches/v07_dev40_i2/batch_001/batch_manifest.json",
    "test_v07_final_freeze.": "data/evidence_benchmarks/v07_final_system_freeze_manifest.json",
    "test_v07_future_holdout30_gold.": "data/evidence_benchmarks/v07_final_system_freeze_manifest.json",
    "test_v07_gold_workflow.V07GoldWorkflowTests.test_future_holdout_boundary_and_v06_freeze_remain_unchanged": "data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json",
    "test_v07_phase0.V07Phase0Tests.test_frozen_v06_hashes_are_unchanged": "data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl",
    "test_v07_phase0.V07Phase0Tests.test_postmortem_does_not_rewrite_frozen_benchmark_or_responses": "data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl",
    "test_v07_phase0.V07Phase0Tests.test_future_dataset_identity_schema_has_no_abstract_field_when_frozen": "data/evidence_benchmarks/v07_future_holdout30/identities.json",
    # These tests would pass vacuously with an empty, missing output directory.
    "test_v07_dev40_i1.Dev40PromptIteration1Tests.test_api_credentials_do_not_appear_in_worker_bundle_or_log": "data/evidence_batches/v07_dev40_i1/batch_001/batch_manifest.json",
    "test_v07_dev40_i2.Dev40PromptIteration2Tests.test_api_keys_are_not_written_to_i2_outputs": "data/evidence_batches/v07_dev40_i2/batch_001/batch_manifest.json",
}

XLSX_CASES = {
    "test_v02.FileAndExportTests.test_xlsx_has_three_sheets_typed_values_and_no_key",
    "test_v03.OutputTests.test_excel_optional_abstracts_sheet_and_compact_records",
    "test_v04.PipelineTests.test_xlsx_evidence_export_and_csv_compatibility",
}


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def historical_requirement(test_id):
    for prefix, artifact in HISTORICAL_CASES.items():
        if test_id == prefix or (prefix.endswith(".") and test_id.startswith(prefix)):
            return artifact
    return None


def select_tests(suite, profile="portable", without_xlsx=False):
    selected, excluded = [], []
    for case in flatten(suite):
        test_id = case.id()
        artifact = historical_requirement(test_id)
        is_historical = artifact is not None
        reason = None
        if profile == "portable" and is_historical:
            reason = "historical experiment audit"
        elif profile == "historical" and not is_historical:
            reason = "portable regression"
        elif without_xlsx and test_id in XLSX_CASES:
            reason = "XLSX integration explicitly disabled"
        if reason:
            excluded.append({"test": test_id, "reason": reason, "artifact": artifact})
        else:
            selected.append(case)
    return unittest.TestSuite(selected), excluded


def missing_historical_artifacts(suite, root=ROOT):
    missing = set()
    for case in flatten(suite):
        artifact = historical_requirement(case.id())
        if artifact and not (Path(root) / artifact).is_file():
            missing.add(artifact)
    return sorted(missing)
