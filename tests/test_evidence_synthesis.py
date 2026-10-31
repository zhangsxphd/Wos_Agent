import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('evidence_synthesis', ROOT / 'scripts/research_planning/evidence_synthesis.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
REFERENCE = ROOT / 'research_projects/saline_paddy_irrigation/v09_recovery_20261031/checked_evidence.json'


class EvidenceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.ref = json.loads(REFERENCE.read_text())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = {'doi': '10.3390/su18052185', 'index': 0, 'indicator': 'grain_yield',
               'value': {'ISCW': 7.18, 'IFCW': 18.27}, 'unit': 'g/hill',
               'record_class': 'candidate_pending_review', 'evidence': {'excerpt': 'table excerpt'}}
        (Path(self.tmp.name) / 'results.jsonl').write_text(json.dumps(raw) + '\n')

    def build(self, ref=None):
        return mod.prepare(ref or self.ref, self.tmp.name)

    def test_ignores_prebuilt_synthesis_and_single_paper_result_prose(self):
        altered = copy.deepcopy(self.ref)
        altered['cross_study_comparisons'] = [{'interpretation': 'completely invented'}]
        for c in altered['comparisons']:
            c['result'] = 'invented opposite direction'
            if 'relative_change_percent_calculated' in c['numeric_values']:
                c['numeric_values']['relative_change_percent_calculated'] = 999
        self.assertEqual(mod.synthesize(self.build()), mod.synthesize(self.build(altered)))

    def test_original_candidate_feeds_result_without_upgrade(self):
        c = next(c for c in self.build()['comparisons'] if c['comparison_id'] == 'Y26')
        self.assertEqual(c['value_origin'], 'historic_numeric_candidate')
        self.assertEqual(c['candidate_links'][0]['status'], 'candidate_pending_review')
        self.assertNotIn('human_gold', c['candidate_links'][0]['status'])
        self.assertEqual(c['direction_records'][0]['within_comparison_percent_change'], -60.7)

    def test_candidate_mismatch_stops_cross_study_claim(self):
        p = Path(self.tmp.name) / 'results.jsonl'
        r = json.loads(p.read_text()); r['value']['ISCW'] = 30
        p.write_text(json.dumps(r) + '\n')
        n = self.build(); c = next(c for c in n['comparisons'] if c['comparison_id'] == 'Y26')
        self.assertIn('historic_candidate_disagrees_with_source_check', c['conflicts'])
        self.assertNotIn('within_comparison_percent_change', c['direction_records'][0])
        self.assertTrue(all('Y26' not in x['supports'] for x in mod.synthesize(n)['findings']))

    def test_opposite_values_change_program_result(self):
        ref = copy.deepcopy(self.ref)
        c = next(c for c in ref['comparisons'] if c['comparison_id'] == 'Y23')
        for k in ('2017', '2018'):
            a = c['numeric_values'][k]
            a['intervention'], a['comparator'] = a['comparator'], a['intervention']
        groups = mod.synthesize(self.build(ref))['findings']
        self.assertFalse(any('Y23' in g['supports'] for g in groups))

    def test_ns_unit_conflict_and_unrelated_factors(self):
        n = self.build(); by = {c['comparison_id']: c for c in n['comparisons']}
        self.assertEqual(by['N18']['significance'], 'not_significant_reported')
        self.assertIn('unit_conflict_unresolved', by['N18']['conflicts'])
        self.assertTrue(all('within_comparison_percent_change' not in r for r in by['N18']['direction_records']))
        self.assertEqual(by['Y24Q']['factor'], 'water_source')
        self.assertEqual(by['Y26F']['factor'], 'fertilizer')
        self.assertTrue(by['N22']['summary_only'])
        self.assertFalse(mod.synthesize(n)['possible_contradictions'])

    def test_candidate_only_has_no_invented_cross_study_finding(self):
        n = self.build()
        n['comparisons'] = [c for c in n['comparisons'] if c['value_origin'] == 'historic_numeric_candidate']
        self.assertEqual(len(n['comparisons']), 1)
        self.assertEqual(mod.synthesize(n)['findings'], [])

    def test_article_conflict_is_not_silently_in_common_finding(self):
        n = self.build()
        c = next(c for c in n['comparisons'] if c['comparison_id'] == 'Y22')
        self.assertIn('possible_abstract_table_conflict_scope_unresolved', c['conflicts'])
        finding = mod.synthesize(n)['findings'][0]
        self.assertEqual(finding['supports'], ['Y23', 'Y26'])
        self.assertTrue(any(x['comparison_id'] == 'Y22' for x in finding['counterevidence_or_unresolved']))

    def test_matched_context_possible_opposition_and_shared_experiment_guard(self):
        n = self.build(); a = next(c for c in n['comparisons'] if c['comparison_id'] == 'Y26')
        b = copy.deepcopy(a); b['comparison_id'] = 'SYNTHETIC'; b['study_id'] = 'SYNTHETIC'
        b['direction_records'][0]['direction'] = 'higher'
        b['direction_records'][0]['within_comparison_percent_change'] = 10
        context = {k: 'same' for k in ('population','environment','intervention_level', 'comparator_level',
                    'time_window','measurement_method','experimental_unit','independent_replicates','statistical_method')}
        context['source_status'] = 'source_checked'
        a['comparison_context'] = context; b['comparison_context'] = context
        p = copy.deepcopy(next(p for p in n['papers'] if p['study_id'] == 'P2026'))
        p['study_id'] = 'SYNTHETIC'; p['source_url'] = 'synthetic'; p['doi'] = '10.1234/synthetic'
        n['papers'].append(p); n['comparisons'].append(b)
        s = mod.synthesize(n)
        self.assertTrue(any('SYNTHETIC' in x['comparison_ids'] for x in s['possible_contradictions']))
        p['experiment_metadata'] = {'site': 'A', 'years': [2022]}
        next(p for p in n['papers'] if p['study_id'] == 'P2026')['experiment_metadata'] = {'site': 'A', 'years': [2022]}
        s = mod.synthesize(n)
        self.assertTrue(s['possible_shared_experiments'])
        self.assertFalse(any('SYNTHETIC' in x['comparison_ids'] for x in s['possible_contradictions']))


if __name__ == '__main__': unittest.main()
