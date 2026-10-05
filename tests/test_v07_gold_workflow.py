import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.evidence.compare_v07_gold_annotators import compare
from scripts.evidence.validate_v07_gold_annotator import read_jsonl, validate_file

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'data/evidence_benchmarks/v07_dev40_gold_work'
RESULTS = ROOT / 'data/evidence_benchmarks/v07_dev40_gold_work_results'
CANDIDATE = ROOT / 'data/evidence_benchmarks/v07_dev40_gold_candidate'


class V07GoldWorkflowTests(unittest.TestCase):
    def test_annotation_bundle_is_allowlisted_and_manifested(self):
        allowed = {'abstracts.jsonl', 'evidence_matrix.schema.json',
                   'evidence_field_contract_v2.md', 'GOLD_ANNOTATION_INSTRUCTIONS.md'}
        self.assertEqual({p.name for p in WORK.iterdir()}, allowed)
        manifest = json.loads((ROOT / 'data/evidence_benchmarks/v07_dev40_gold_work_manifest.json').read_text())
        self.assertEqual(set(manifest['files']), allowed)
        self.assertFalse(manifest['contains_extraction_prompt'])
        self.assertFalse(manifest['contains_v06_worker_outputs'])
        self.assertFalse(manifest['contains_future_holdout_identities'])
        self.assertFalse(manifest['future_holdout_abstracts_accessed'])
        self.assertEqual(len(read_jsonl(WORK / 'abstracts.jsonl')), 40)

    def test_annotation_protocol_requires_literal_claim_and_nested_anchors(self):
        text = (WORK / 'GOLD_ANNOTATION_INSTRUCTIONS.md').read_text()
        self.assertIn('claim` must itself be a verbatim substring', text)
        self.assertIn('Do not add entries for `/evidence/findings/*`', text)
        self.assertIn('`/evidence/author_interpretations/*`', text)

    def test_two_annotator_outputs_are_complete_and_independently_validated(self):
        for who, expected_grounded in (('A', 34), ('B', 28)):
            with self.subTest(annotator=who):
                report = json.loads((RESULTS / f'annotator_{who}/validation.json').read_text())
                rows = read_jsonl(RESULTS / f'annotator_{who}/annotation.jsonl')
                self.assertEqual(len(rows), 40)
                self.assertEqual(len({r['uid'] for r in rows}), 40)
                self.assertEqual(report['schema_valid'], 40)
                self.assertEqual(report['grounding_valid'], expected_grounded)
                self.assertEqual(report['invalid_offset_count'], 0)
                self.assertEqual(report['orphan_anchor_count'], 0)

    def test_ab_comparison_reports_source_boundary_and_scale_disagreements(self):
        report = json.loads((RESULTS / 'ab_comparison.json').read_text())
        self.assertEqual(report['record_count'], 40)
        self.assertEqual(report['fields'], {'exact': 494, 'boundary_equivalent': 60,
                                             'a_only': 64, 'b_only': 66})
        self.assertEqual(report['findings'], {'exact': 149, 'boundary': 15,
                                               'a_only': 8, 'b_only': 6})
        self.assertEqual(report['category_disagreement_count'], 6)
        self.assertEqual(report['experimental_scale_exact_enum_only']['disagreement'], 1)

    def test_adjudicator_decisions_cover_each_disagreement_once(self):
        items = json.loads((RESULTS / 'adjudicator_C/disagreements.json').read_text())['items']
        rows = read_jsonl(RESULTS / 'adjudicator_C/adjudications.jsonl')
        self.assertEqual(len(items), 168)
        self.assertEqual(len(rows), 168)
        self.assertEqual({x['id'] for x in items}, {x['id'] for x in rows})
        self.assertEqual(sum(x['decision'] == 'NEEDS_HUMAN_REVIEW' for x in rows), 41)
        self.assertTrue(all(x['decision'] in {'A','B','MODIFY','NEEDS_HUMAN_REVIEW'} for x in rows))

    def test_candidate_has_exact_dev_identities_and_passes_schema_and_grounding(self):
        rows = read_jsonl(CANDIDATE / 'candidate_gold.jsonl')
        inputs = read_jsonl(WORK / 'abstracts.jsonl')
        self.assertEqual({x['uid'] for x in rows}, {x['uid'] for x in inputs})
        report = validate_file(CANDIDATE / 'candidate_gold.jsonl', WORK / 'abstracts.jsonl',
                               ROOT / 'schemas/evidence_matrix.schema.json')
        self.assertEqual(report['schema_valid'], 40)
        self.assertEqual(report['grounding_valid'], 40)
        self.assertEqual(report['invalid_offset_count'], 0)
        self.assertEqual(report['orphan_anchor_count'], 0)
        self.assertEqual(report['missing_support_count'], 0)
        freeze = json.loads((CANDIDATE / 'candidate_manifest.json').read_text())
        self.assertEqual(freeze['status'], 'development_gold_candidate')
        self.assertFalse(freeze['human_approved'])

    def test_candidate_contains_no_prediction_provenance_and_review_is_compact(self):
        forbidden = {'prediction', 'prompt_sha', 'extractor_output', 'model_response'}
        for row in read_jsonl(CANDIDATE / 'candidate_gold.jsonl'):
            self.assertFalse(forbidden & set(row))
            self.assertTrue(all(not values for values in row['inference'].values()))
        packet = (ROOT / 'data/reports/v07_dev40_gold_human_review.md').read_text()
        self.assertLessEqual(len(re.findall(r'^## G\d+', packet, flags=re.M)), 25)
        self.assertIn('WOS:001332147700001', packet)
        self.assertIn('NEEDS_HUMAN_REVIEW', packet)

    def test_future_holdout_boundary_and_v06_freeze_remain_unchanged(self):
        workflow = json.loads((RESULTS / 'workflow_manifest.json').read_text())
        self.assertTrue(workflow['dev40_abstracts_accessed'])
        self.assertFalse(workflow['future_holdout_abstracts_accessed'])
        self.assertFalse(workflow['future_holdout_gold_created'])
        self.assertFalse(workflow['future_holdout_extraction_run'])
        self.assertFalse(workflow['prompt_modified'])
        self.assertFalse(workflow['extractor_run'])
        paths = {'prompts/evidence_extraction.md':'prompt', 'schemas/evidence_matrix.schema.json':'schema',
                 'docs/evidence_field_contract_v1.md':'field_contract', 'scripts/evidence/metrics.py':'metric_v1',
                 'scripts/evidence/metrics_v2.py':'metric_v2',
                 'data/processed/saline_paddy_v056_full_enriched_20261003.jsonl':'canonical_input',
                 'data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl':'final_gold'}
        frozen = json.loads((ROOT / 'data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json').read_text())
        for path, key in paths.items():
            import hashlib
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), frozen['hashes'][key])
        import hashlib
        self.assertEqual(hashlib.sha256((ROOT / 'scripts/extractors/schema_validator.py').read_bytes()).hexdigest(),
                         '58735927376d007f5bf004b9bf62cb5dc6acb56cc33a60ab0cb7f856740e95a1')

    def test_contract_consistency_audit_flags_recurrent_gaps_without_editing_contract(self):
        packet = (ROOT / 'data/reports/v07_dev40_contract_consistency_audit.md').read_text()
        self.assertIn('CONTRACT_V2_AMENDMENT_NEEDED', packet)
        self.assertIn('WOS:000899201500001', packet)
        self.assertIn('WOS:001298590200001', packet)
        self.assertIn('Contract v2 remains frozen and was not changed', packet)
        manifest = json.loads((CANDIDATE / 'candidate_manifest.json').read_text())
        self.assertFalse(manifest['contract_modified_after_freeze'])

    def test_deterministic_comparator_keeps_boundary_equivalence_separate(self):
        abstract = 'Rice yield increased under saline field conditions.'
        start = abstract.index('Rice yield')
        support = {'source':'abstract','evidence_text':abstract,'start':0,'end':len(abstract)}
        a = {'evidence':{'study_system':{'crop':['Rice']},'treatments':{},'measurements':{'yield':['Rice yield']},'methods':[],'findings':[]},
             'evidence_support':{'/evidence/study_system/crop/0':{'source':'abstract','evidence_text':'Rice','start':0,'end':4},
                                 '/evidence/measurements/yield/0':{'source':'abstract','evidence_text':'Rice yield','start':start,'end':start+10}}}
        b = {'evidence':{'study_system':{'crop':['rice']},'treatments':{},'measurements':{'yield':['yield']},'methods':[],'findings':[]},
             'evidence_support':{'/evidence/study_system/crop/0':{'source':'abstract','evidence_text':'Rice','start':0,'end':4},
                                 '/evidence/measurements/yield/0':{'source':'abstract','evidence_text':abstract,'start':0,'end':len(abstract)}}}
        report = compare_from_pair(a,b,abstract)
        self.assertGreaterEqual(report['fields']['exact_tp'], 1)
        self.assertGreaterEqual(report['boundary_equivalent'], 1)


def compare_from_pair(a,b,abstract):
    # Keep the regression focused on the deterministic metric implementation.
    from scripts.evidence.metrics_v2 import field_metrics_v2
    raw=field_metrics_v2(a,b,abstract)
    return {'fields':{k:raw[k] for k in ('exact_tp','boundary_tp','fp','fn')},
            'boundary_equivalent':raw['field_metrics']['/evidence/measurements/yield']['boundary_tp']}


if __name__ == '__main__':
    unittest.main()
