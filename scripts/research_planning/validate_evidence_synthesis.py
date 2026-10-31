"""Compare an already-generated run with the held-out fixed reference.

This is a consistency benchmark, not independent source or human Gold review.
It is intentionally separate from the synthesis process.
"""
import argparse
import json
from pathlib import Path

EXPECTED = {
    'Y23': ('water_regime', 'significant_reported', ['lower', 'lower']),
    'Y26': ('water_regime', 'significant_reported', ['lower']),
    'Y22': ('water_regime', 'significant_reported', ['lower']),
    'N18': ('water_regime', 'not_significant_reported', ['higher', 'higher']),
    'N22': ('water_regime', 'significant_reported', []),
    'C18': ('water_regime', 'significant_reported', ['lower', 'lower']),
    'SOC26': ('water_regime', 'significant_reported', ['lower']),
    'PH24': ('water_and_land_use_confounded', 'not_significant_reported', ['lower']),
    'Y24Q': ('water_source', 'source_conflict_unresolved', ['lower']),
    'Y26F': ('fertilizer', 'significant_reported', ['higher']),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checked', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    ref = json.loads(args.checked.read_text())
    normalized = json.loads((args.output_dir / 'normalized_evidence.json').read_text())
    result = json.loads((args.output_dir / 'synthesis.json').read_text())
    by = {c['comparison_id']: c for c in normalized['comparisons']}
    checks = []
    for c in ref['comparisons']:
        actual = by.get(c['comparison_id'])
        if actual is None:
            checks.append({'id': c['comparison_id'], 'passed': False, 'failure': 'missing'}); continue
        expected_values = {k: v for k, v in c['numeric_values'].items() if k != 'relative_change_percent_calculated'}
        expected_factor, expected_sig, expected_dirs = EXPECTED[c['comparison_id']]
        tests = {'source_values_match': actual['numeric_values'] == expected_values,
                 'factor': actual['factor'] == expected_factor,
                 'significance_scope': actual['significance'] == expected_sig,
                 'directions': [x['direction'] for x in actual['direction_records']] == expected_dirs,
                 'source_location': actual['raw_evidence'][0]['source_location'] == c['source_location']}
        checks.append({'id': c['comparison_id'], 'passed': all(tests.values()), 'checks': tests,
                       'direction': [x['direction'] for x in actual['direction_records']],
                       'value_origin': actual['value_origin'], 'factor': actual['factor'],
                       'significance': actual['significance'], 'conflicts': actual['conflicts']})
    cross = []
    for item in ref['cross_study_comparisons']:
        ids = set(item['comparison_ids'])
        matching_findings = [x for x in result['findings'] if set(x['supports']) == ids]
        matching_pairs = [x for x in result['pairwise_comparability'] if set(x['comparison_ids']) == ids]
        cross.append({'title': item['title'], 'ids': sorted(ids),
                      'matching_qualitative_finding': bool(matching_findings),
                      'matching_pair_refused': bool(matching_pairs and matching_pairs[0]['numeric_comparison'] == 'refused'),
                      'possible_opposite': any(set(x['comparison_ids']) == ids for x in result['possible_contradictions'])})
    benchmark = {'scope': 'Consistency with pre-existing checked reference; not independent scientific validation.',
                 'per_comparison': checks, 'cross_study': cross,
                 'passed_per_comparison': sum(x['passed'] for x in checks), 'total': len(checks)}
    (args.output_dir / 'benchmark.json').write_text(json.dumps(benchmark, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'passed': benchmark['passed_per_comparison'], 'total': benchmark['total'], 'cross_study': cross}, ensure_ascii=False))
    if not all(x['passed'] for x in checks): raise SystemExit(1)


if __name__ == '__main__': main()
