import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / 'docs/evidence_field_contract_v2_candidate.md'
CONTRACT_V1 = ROOT / 'docs/evidence_field_contract_v1.md'
CASES = ROOT / 'tests/fixtures/v07_contract_v2_ontology_cases.json'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def uid_of_response(path):
    return json.loads(path.read_text(encoding='utf-8'))['response']['uid']


class V07ContractV2CandidateTests(unittest.TestCase):
    def test_v06_frozen_source_hashes_and_all_historical_responses_unchanged(self):
        frozen_path = ROOT / 'data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json'
        frozen = json.loads(frozen_path.read_text(encoding='utf-8'))
        hashes = frozen['hashes']
        tracked = {
            'prompt': ROOT / 'prompts/evidence_extraction.md',
            'schema': ROOT / 'schemas/evidence_matrix.schema.json',
            'field_contract': CONTRACT_V1,
            'metric_v1': ROOT / 'scripts/evidence/metrics.py',
            'metric_v2': ROOT / 'scripts/evidence/metrics_v2.py',
            'final_gold': ROOT / 'data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl',
        }
        for name, path in tracked.items():
            with self.subTest(name=name):
                self.assertEqual(sha(path), hashes[name])
        self.assertEqual(sha(ROOT / 'scripts/extractors/schema_validator.py'),
                         '58735927376d007f5bf004b9bf62cb5dc6acb56cc33a60ab0cb7f856740e95a1')
        self.assertEqual(sha(ROOT / 'scripts/evidence/metrics.py'),
                         '4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a')
        self.assertEqual(sha(ROOT / 'data/reports/v06_holdout20_frozen_v1/v06_gold_benchmark_20261004_235122.json'),
                         '6eeab417be4403307d485cb42c9def8948e0b9c88c4c7992ea0fdd2993d49d3d')
        self.assertEqual(sha(ROOT / 'data/reports/v06_holdout20_frozen_v2/v06_holdout20_metric_v2_20261004.json'),
                         '1a9bae7f59e1de64ac55875bdf26d575b4f7e78795896929d8fa10218082c769')
        responses = ROOT / 'data/evidence_batches/v06_holdout20_eval_20261004/batch_001/responses'
        seen = {}
        for path in responses.glob('*.json'):
            uid = uid_of_response(path)
            seen[uid] = sha(path)
        self.assertEqual(seen, frozen['response_sha256'])
        self.assertEqual(len(seen), 20)

    def test_contract_v2_is_a_separate_candidate_and_v1_is_unchanged(self):
        self.assertTrue(CANDIDATE.is_file())
        self.assertNotEqual(CANDIDATE.resolve(), CONTRACT_V1.resolve())
        frozen = json.loads((ROOT / 'data/evidence_batches/v06_holdout20_eval_20261004/frozen_hash_manifest.json').read_text())
        self.assertEqual(sha(CONTRACT_V1), frozen['hashes']['field_contract'])
        text = CANDIDATE.read_text(encoding='utf-8')
        self.assertIn('Status:** candidate for human review; not approved', text)
        self.assertIn('Evidence Matrix v0.4, unchanged', text)

    def test_formal_contract_approval_and_frozen_sha_precede_dev40_reading(self):
        formal = ROOT / 'docs/evidence_field_contract_v2.md'
        manifest_path = ROOT / 'data/evidence_benchmarks/v07_dev40/contract_freeze_manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertTrue(formal.is_file())
        self.assertEqual(manifest['approval_status'], 'HUMAN_APPROVED')
        self.assertTrue(manifest['approval_before_dev40_reading'])
        self.assertEqual(manifest['contract_v2_sha256'], sha(formal))
        self.assertEqual(manifest['human_review_items'], 10)
        self.assertEqual(manifest['accepted'], 9)
        self.assertEqual(manifest['modified_then_accepted'], 1)
        self.assertFalse(manifest['future_holdout_abstracts_accessed'])

    def test_c10_human_modification_has_priority_and_deduplication_rules(self):
        text = (ROOT / 'docs/evidence_field_contract_v2.md').read_text(encoding='utf-8')
        for rule in ('what the study found', 'explicit interpretive layer',
                     'Cue phrases are evidence to assess, never sufficient by themselves',
                     'NO_MECHANICAL_DUPLICATION = true', 'measurement list',
                     'semantic layers'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)
        manifest = json.loads((ROOT / 'data/evidence_benchmarks/v07_dev40/contract_freeze_manifest.json').read_text())
        self.assertEqual(manifest['human_decisions']['C10'], 'MODIFY_THEN_ACCEPT')

    def test_findings_rule_is_verbatim_v1_copy(self):
        old = next(line for line in CONTRACT_V1.read_text().splitlines() if line.startswith('| `findings` |'))
        for path in (CANDIDATE, ROOT / 'docs/evidence_field_contract_v2.md'):
            new = next(line for line in path.read_text().splitlines() if line.startswith('| `findings` |'))
            self.assertEqual(old, new)
            self.assertIn('PRESERVE_BY_DEFAULT = true', path.read_text())

    def test_ontology_replay_cases_are_deterministic_and_complete(self):
        rows = json.loads(CASES.read_text(encoding='utf-8'))
        self.assertEqual(rows, json.loads(CASES.read_text(encoding='utf-8')))
        ids = [r['id'] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        required = {
            'soil_actual_descriptor','soil_land_use_only','soil_pot_target_unlinked',
            'leaf_antioxidant','plant_na_k','plant_stress_genes','soil_urease_unattributed',
            'microbial_community_assembly','microbial_network','soil_quality_index',
            'carbon_fractions','plant_chlorophyll_fluorescence','field_scale','named_model_only',
            'background_salinity','actual_studied_salinity','soil_chemistry',
            'fertilizer_and_amendment','greenhouse_gas','findings_preserved'
        }
        self.assertEqual(set(ids), required)
        outcomes = {r['id']: r['expected'] for r in rows}
        self.assertEqual(outcomes['soil_actual_descriptor'], 'study_system.soil_type')
        self.assertEqual(outcomes['soil_land_use_only'], 'exclude:study_system.soil_type')
        self.assertEqual(outcomes['field_scale'], 'study_system.experimental_scale=field')
        self.assertEqual(outcomes['named_model_only'], 'methods;exclude:experimental_scale=model')
        self.assertTrue(all(r.get('source') for r in rows))

    def test_candidate_covers_each_replay_case_and_priority_boundary(self):
        text = CANDIDATE.read_text(encoding='utf-8').casefold()
        for token in ('other_is_last_resort = true', 'sampled soil', 'pot substrate', 'chlorophyll fluorescence',
                      'community assembly', 'network connectivity', 'aromatic-c', 'field-scale experiment',
                      'treatment-induced salinity', 'named model is a method', 'author_interpretations',
                      'mechanisms_explicit'):
            with self.subTest(token=token):
                self.assertIn(token.casefold(), text)
        self.assertIn('measurements.plant_growth', text)
        self.assertIn('measurements.microbial', text)
        self.assertIn('measurements.carbon', text)
        self.assertIn('measurements.soil_chemical', text)
        self.assertIn('treatments.fertilization', text)
        self.assertIn('treatments.amendments', text)

    def test_review_packet_and_decision_table_have_required_scope(self):
        packet = (ROOT / 'data/reports/v07_contract_v2_human_review.md').read_text(encoding='utf-8')
        decisions = (ROOT / 'data/reports/v07_contract_v2_decisions.md').read_text(encoding='utf-8')
        ids = re.findall(r'^## #C(\d{2})', packet, flags=re.M)
        self.assertEqual(len(ids), 10)
        self.assertEqual(ids, [f'{i:02d}' for i in range(1, 11)])
        for phrase in ('soil_type','salinity_context','plant_growth','OTHER_IS_LAST_RESORT','microbial',
                       'carbon','soil_chemical','fertilization','experimental_scale','author_interpretations'):
            self.assertIn(phrase, packet)
            self.assertIn(phrase, decisions)
        self.assertEqual(packet.count('**Recommended:**'), 10)
        self.assertTrue(all(x in packet for x in ('ACCEPT','REJECT','MODIFY')))

    def test_future_holdout_remains_unread_unannotated_and_unextracted(self):
        manifest = json.loads((ROOT / 'data/evidence_benchmarks/v07_future_holdout30/selection_manifest.json').read_text())
        self.assertTrue(manifest['V07_FUTURE_HOLDOUT_FROZEN_BEFORE_TUNING'])
        self.assertFalse(manifest['future_holdout_abstracts_accessed'])
        self.assertFalse(manifest['gold_created'])
        self.assertFalse(manifest['extraction_run'])
        self.assertFalse((ROOT / 'data/evidence_batches/v07_future_holdout30').exists())
        self.assertFalse((ROOT / 'data/evidence_benchmarks/v07_future_holdout30_gold').exists())

    def test_dev40_abstracts_not_used_in_phase_1a(self):
        manifest = json.loads((ROOT / 'data/evidence_benchmarks/v07_dev40/selection_manifest.json').read_text())
        self.assertFalse(manifest['abstracts_materialized'])
        self.assertFalse((ROOT / 'data/evidence_batches/v07_dev40').exists())
        self.assertFalse((ROOT / 'data/evidence_benchmarks/v07_dev40_gold').exists())
        # The synthetic replay fixture is the only ontology test input; no abstracts are present in it.
        self.assertNotIn('abstract', CASES.read_text(encoding='utf-8').casefold())


if __name__ == '__main__':
    unittest.main()
