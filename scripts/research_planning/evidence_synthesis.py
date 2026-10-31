"""Conservative bridge from historic per-paper candidates to comparative evidence.

No model calls. Source-checked single-paper observations supplement, but never
upgrade, earlier model candidates. Prewritten cross-study narrative is ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def doi(value):
    return str(value or '').strip().lower().removeprefix('https://doi.org/')


def rows(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def factor(intervention, comparator, role=''):
    text = f'{intervention} {comparator}'.lower()
    # A change in fertilizer or water source is not a water-regime experiment.
    if 'not_water_contrast' in role or re.search(r'尿素|fertilizer|fertiliser', text):
        return 'fertilizer'
    if 'water_quality' in role or re.search(r'沟水|河水|water source', text):
        return 'water_source'
    if 'confounded' in role:
        return 'water_and_land_use_confounded'
    if re.search(r'awd|灌溉|滴灌|淹水|浅湿|flood|irrigat', text):
        return 'water_regime'
    return 'unknown'


def water_contrast(intervention, comparator):
    a, b = intervention.lower(), comparator.lower()
    if ('awd' in a or '间歇' in a) and ('cf' in b or '淹水' in b):
        return 'intermittent_vs_flooded'
    if '浅湿' in a and '淹水' in b:
        return 'shallow_wet_vs_flooded'
    if '滴灌' in a and '滴灌' in b:
        return 'deficit_vs_full_drip'
    return 'unknown'


def study_type(fields):
    t = ' '.join(str(fields.get(k, '')) for k in ('研究对象', '试验设计')).lower()
    if re.search(r'综述|review|meta.analysis', t): return 'review_or_meta'
    if re.search(r'模拟|simulation', t) and not re.search(r'盆栽|田间|稻田', t): return 'simulation'
    if re.search(r'盆栽|土柱| pot', t): return 'pot_experiment'
    if re.search(r'田间|稻田|小区|田块', t): return 'field_experiment'
    if re.search(r'观察|observational', t): return 'observational'
    return 'unknown'


def significance(text):
    # Interpret only the comparison-specific clause; retain other contexts intact.
    first = (text or '').split('；')[0]
    if '未显著' in first or '未显示显著' in first or '未检出显著' in first or re.search(r'(?<![A-Za-z])NS(?![A-Za-z])', first, re.I):
        return 'not_significant_reported'
    if re.search(r'显著|p\s*<|不同字母|不同小写字母|分别标|无交集|\*\*', first, re.I):
        return 'significant_reported'
    return 'unknown'


def sections(source):
    """Only admit per-paper measurements. No reference synthesis/result prose."""
    return source.get('studies', []), source.get('comparisons', [])


def prepare(reference, history_dir):
    selected, measurements = sections(reference)
    ids = {doi(s.get('doi')): s['study_id'] for s in selected}
    historic = defaultdict(lambda: defaultdict(list))
    file_info = {}
    for name in ('papers', 'fields', 'results', 'issues', 'historic_claims', 'supplement_files'):
        path = Path(history_dir) / f'{name}.jsonl'
        if not path.exists():
            file_info[name] = {'status': 'missing'}
            continue
        file_info[name] = {'status': 'loaded', 'sha256': digest(path)}
        for record in rows(path):
            if doi(record.get('doi')) in ids:
                historic[ids[doi(record['doi'])]][name].append(record)
    papers = []
    for study in selected:
        sid = study['study_id']
        previous = dict(historic[sid])
        raw_sha = next((p.get('raw_sha256') for p in previous.get('papers', []) if p.get('raw_sha256')), None)
        fields = study.get('fields', {})
        papers.append({
            'study_id': sid, 'doi': study.get('doi'), 'source_url': study.get('source_url'),
            'study_type': study_type(fields), 'evidence_role': study.get('evidence_role', 'unknown'),
            'source_access': study.get('source_access'), 'source_location': study.get('location'),
            'pdf_sha256': raw_sha, 'experiment_metadata': study.get('experiment_metadata', {}), 'source_state': 'source_checked_by_prior_assistant_not_human_gold',
            'experiments': [{'experiment_id': sid + ':E1', 'status': 'partially_structured',
                'object': fields.get('研究对象'), 'conditions': fields.get('环境及盐度'),
                'question_and_hypothesis': fields.get('研究问题'), 'design': fields.get('试验设计'),
                'experimental_unit_and_independent_replicates': fields.get('独立重复'),
                'sampling_replicates': fields.get('采样重复'), 'outcomes': fields.get('指标'),
                'methods_and_statistics': fields.get('方法'),
                'treatments': [{'role': 'intervention', 'description': fields.get('处理')},
                               {'role': 'comparator', 'description': fields.get('对照')}],
                # Prior notes can mix author limitations and independent concerns.
                'limitations_unclassified': fields.get('限制'),
                'author_hypothesis': 'not_separately_verified',
                'author_mechanism': 'not_separately_verified',
                'independent_methodological_review': fields.get('独立重复'),
                'original_author_conclusions': 'not_separately_verified'}],
            # Preserve entire candidate rows, including quote, offsets, table, row,
            # model provenance and original adjudication. No scientific promotion.
            'historic_records': previous,
        })
    paper_by_id = {p['study_id']: p for p in papers}
    comparisons = []
    for c in measurements:
        p = paper_by_id.get(c['study_id'])
        if not p: continue
        values = {k: v for k, v in c.get('numeric_values', {}).items()
                  if k != 'relative_change_percent_calculated'}
        evidence = {
            'source_url': p['source_url'], 'source_location': c.get('source_location'),
            'pdf_sha256': p['pdf_sha256'], 'exact_quote': None,
            'quote_state': 'not_transcribed_in_prior_source_check',
            'source_state': c.get('verification_status', 'unknown'),
        }
        outcome = c.get('outcome')
        comp = {
            'comparison_id': c['comparison_id'], 'study_id': c['study_id'],
            'experiment_id': c['study_id'] + ':E1', 'outcome': outcome,
            'intervention': c.get('intervention', ''), 'comparator': c.get('comparator', ''),
            'factor': factor(c.get('intervention', ''), c.get('comparator', ''), p['evidence_role']),
            'contrast': water_contrast(c.get('intervention', ''), c.get('comparator', '')),
            'observation_period': c.get('observation_period'), 'numeric_values': values,
            'reported_unit': c.get('unit'), 'significance_original': c.get('significance'),
            'significance': significance(c.get('significance')),
            'limitations': c.get('limitations'), 'raw_evidence': [evidence],
            'record_state': 'source_checked_by_prior_assistant_not_human_gold',
            'comparison_context': c.get('comparison_context', {}),
            'direction_records': [], 'conflicts': [], 'candidate_links': [],
        }
        unit = str(values.get('unit', c.get('unit', ''))).lower()
        if 'unresolved' in unit or '冲突' in unit:
            comp['conflicts'].append('unit_conflict_unresolved')
        study_limit = p['experiments'][0].get('limitations_unclassified') or ''
        if re.search(r'摘要.*(?:不一致|不全一致|冲突|歧义)', study_limit):
            comp['conflicts'].append('possible_abstract_table_conflict_scope_unresolved')
        if re.search(r'讨论.*未显著|文字与表格|比较基准.*不明', (c.get('significance') or '') + (c.get('limitations') or '')):
            comp['conflicts'].append('possible_text_table_conflict_scope_unresolved')
            comp['significance'] = 'source_conflict_unresolved'
        pairs = []
        if isinstance(values.get('intervention'), (int, float)) and isinstance(values.get('comparator'), (int, float)):
            pairs.append(('overall', values))
        else:
            pairs.extend((k, v) for k, v in values.items() if isinstance(v, dict)
                         and isinstance(v.get('intervention'), (int, float))
                         and isinstance(v.get('comparator'), (int, float)))
        for stratum, v in pairs:
            a, b = v['intervention'], v['comparator']
            # Record the signs of tabulated numbers, but do not quantify disputed units.
            diff = 'higher' if a > b else 'lower' if a < b else 'equal'
            record = {'stratum': stratum, 'intervention': a, 'comparator': b,
                      'direction': diff, 'unit': values.get('unit'),
                      'independent_study': False if stratum != 'overall' else None}
            if b != 0 and not comp['conflicts'] and outcome != 'soil_ph':
                record['within_comparison_percent_change'] = round((a / b - 1) * 100, 2)
            comp['direction_records'].append(record)
        if 'author_reported_mean_relative_change_percent' in values:
            comp['summary_only'] = True
        # A historic raw numeric result can supply the observation itself when its
        # group labels, outcome and units match explicitly. Preserve the separate
        # source-check record; never silently promote the historic candidate.
        for raw in p['historic_records'].get('results', []):
            rv = raw.get('value')
            if not isinstance(rv, dict) or len(rv) != 2 or not pairs: continue
            aliases = {'grain_yield': 'rice_grain_yield', 'rice_grain_yield': 'rice_grain_yield'}
            if aliases.get(str(raw.get('indicator', '')).lower()) != outcome: continue
            labels = list(rv)
            if (all(re.search(r'(?<![A-Za-z0-9])' + re.escape(k) + r'(?![A-Za-z0-9])',
                              comp['intervention'] if i == 0 else comp['comparator'], re.I)
                    for i, k in enumerate(labels)) and raw.get('unit') == values.get('unit')
                and all(isinstance(rv[k], (int, float)) for k in labels)):
                comp['candidate_links'].append({'index': raw.get('index'), 'status': raw.get('record_class'),
                    'evidence': raw.get('evidence'), 'source_sha256': raw.get('source_sha256')})
                comp['source_checked_values'] = dict(values)
                comp['value_origin'] = 'historic_numeric_candidate'
                candidate_a, candidate_b = rv[labels[0]], rv[labels[1]]
                comp['numeric_values'] = dict(values, intervention=candidate_a, comparator=candidate_b)
                if (candidate_a, candidate_b) != (pairs[0][1]['intervention'], pairs[0][1]['comparator']):
                    comp['conflicts'].append('historic_candidate_disagrees_with_source_check')
                direction = 'higher' if candidate_a > candidate_b else 'lower' if candidate_a < candidate_b else 'equal'
                r = {'stratum': 'overall', 'intervention': candidate_a, 'comparator': candidate_b,
                     'direction': direction, 'unit': raw.get('unit'), 'independent_study': None}
                if candidate_b and not comp['conflicts']:
                    r['within_comparison_percent_change'] = round((candidate_a / candidate_b - 1) * 100, 2)
                comp['direction_records'] = [r]
                ev = raw.get('evidence') or {}
                if isinstance(ev, dict) and ev.get('excerpt'):
                    evidence['exact_quote'] = ev['excerpt']
                    evidence['quote_state'] = 'historic_candidate_excerpt_needs_independent_review'
                break
        comp.setdefault('value_origin', 'prior_source_checked_single_paper_observation')
        comparisons.append(comp)
    return {'version': '1.0', 'input_files': file_info, 'papers': papers, 'comparisons': comparisons}


def possible_shared(a, b):
    """Flag, never assert, overlap only with explicit common site and year."""
    x, y = a.get('experiment_metadata', {}), b.get('experiment_metadata', {})
    if doi(a.get('doi')) and doi(a.get('doi')) == doi(b.get('doi')):
        return True
    return bool(x.get('site') and x.get('site') == y.get('site')
                and set(x.get('years', [])) & set(y.get('years', [])))


def assess_pair(a, b, papers):
    pa, pb = papers[a['study_id']], papers[b['study_id']]
    reasons = []
    if a['factor'] != b['factor']: reasons.append('intervention_factor_differs')
    if a['contrast'] != b['contrast'] or a['contrast'] == 'unknown': reasons.append('water_regime_definition_differs_or_unknown')
    if pa['study_type'] != pb['study_type'] or pa['study_type'] == 'unknown': reasons.append('study_design_differs_or_unknown')
    if a.get('summary_only') or b.get('summary_only'): reasons.append('one_result_is_across_treatments_summary')
    if possible_shared(pa, pb): reasons.append('possible_shared_experiment')
    if a['conflicts'] or b['conflicts']: reasons.append('source_conflict_unresolved')
    if not a['numeric_values'].get('unit') or a['numeric_values'].get('unit') != b['numeric_values'].get('unit'):
        reasons.append('measurement_units_differ_or_unknown')
    # Matching topic labels do not establish equivalent populations, interventions,
    # or measurement windows. Require explicit source-checked context in BOTH records.
    fields = ('population', 'environment', 'intervention_level', 'comparator_level',
              'time_window', 'measurement_method', 'experimental_unit',
              'independent_replicates', 'statistical_method')
    ca, cb = a.get('comparison_context', {}), b.get('comparison_context', {})
    for key in fields:
        if not ca.get(key) or not cb.get(key) or ca[key] != cb[key]:
            reasons.append(key + '_differs_or_unknown')
    if ca.get('source_status') != 'source_checked' or cb.get('source_status') != 'source_checked':
        reasons.append('comparison_context_not_source_checked')
    can_compare = not reasons and len(a['direction_records']) == 1 and len(b['direction_records']) == 1
    if not a['direction_records'] or not b['direction_records']:
        reasons.append('paired_numeric_observations_missing')
    if len(a['direction_records']) > 1 or len(b['direction_records']) > 1:
        reasons.append('multiple_strata_need_explicit_alignment')
    result = {'comparison_ids': [a['comparison_id'], b['comparison_id']],
              'numeric_comparison': 'allowed_within_study_effects_only' if can_compare else 'refused',
              'numeric_pooling': 'refused', 'reasons': list(dict.fromkeys(reasons))}
    if can_compare:
        result['within_study_effects_percent'] = [a['direction_records'][0].get('within_comparison_percent_change'),
                                                   b['direction_records'][0].get('within_comparison_percent_change')]
    return result


def synthesize(normalized):
    ps = {p['study_id']: p for p in normalized['papers']}
    cs = normalized['comparisons']
    assessments = [assess_pair(a, b, ps) for a, b in itertools.combinations(cs, 2)
                   if a['study_id'] != b['study_id'] and a['outcome'] == b['outcome']]
    groups = defaultdict(list)
    for c in cs:
        if c['factor'] == 'water_regime' and c['contrast'] != 'unknown' and not c['conflicts'] and not c.get('summary_only') and c['direction_records']:
            dirs = {r['direction'] for r in c['direction_records']}
            if len(dirs) == 1:
                groups[(c['outcome'], next(iter(dirs)))].append(c)
    findings = []
    for (outcome, direction), members in sorted(groups.items()):
        # DOI duplicates cannot inflate the number of supporting papers.
        deduplicated = {}
        for c in members:
            key = doi(ps[c['study_id']].get('doi')) or c['study_id']
            deduplicated.setdefault(key, c)
        members = list(deduplicated.values())
        if len(members) < 2: continue
        # Broad screening group only, not a claim of comparable treatment effects.
        others = [x for x in cs if x['outcome'] == outcome and x['factor'] == 'water_regime'
                  and x not in members]
        findings.append({'kind': 'qualitative_same_direction', 'outcome': outcome, 'direction': direction,
            'supports': [c['comparison_id'] for c in members],
            'sources': [ps[c['study_id']]['source_url'] for c in members],
            'counterevidence_or_unresolved': [{'comparison_id': c['comparison_id'],
                'significance': c['significance_original'], 'conflicts': c['conflicts'],
                'limitations': c['limitations'], 'source': ps[c['study_id']]['source_url']} for c in others],
            'within_study_other_conditions': [{'comparison_id': c['comparison_id'],
                'text': '；'.join((c.get('significance_original') or '').split('；')[1:])}
                for c in members if '；' in (c.get('significance_original') or '')],
            'conditions': [{'comparison_id': c['comparison_id'], 'contrast': c['contrast'],
                'study_type': ps[c['study_id']]['study_type'],
                'significance': c['significance_original'],
                'error_information': {k: v for k, v in c['numeric_values'].items() if 'error' in k},
                'statistical_power': 'not_established',
                'environment': ps[c['study_id']]['experiments'][0]['conditions'],
                'independent_units': ps[c['study_id']]['experiments'][0]['experimental_unit_and_independent_replicates']} for c in members],
            'uncertainty': '仅各论文内部观测方向相同；处理、条件、单位与独立性可能不同，不能合并效应或推断因果交互；未完成反例检索，统计功效未知。',
            'numeric_pooling': 'refused'})
    gaps = []
    outcomes = {c['outcome'] for c in cs}
    for outcome in sorted(outcomes):
        selected = [c for c in cs if c['outcome'] == outcome]
        if len({c['study_id'] for c in selected}) < 2:
            gaps.append({'outcome': outcome, 'kind': 'only_one_study_in_available_evidence',
                         'support': [c['comparison_id'] for c in selected],
                         'uncertainty': '仅指本次可用记录，不代表整个文献不存在其他研究。'})
    by_id = {c['comparison_id']: c for c in cs}
    disagreements = []
    for pair in assessments:
        a, b = (by_id[x] for x in pair['comparison_ids'])
        if a['factor'] != 'water_regime' or b['factor'] != 'water_regime': continue
        if pair['numeric_comparison'] == 'refused':
            disagreements.append({'comparison_ids': pair['comparison_ids'], 'outcome': a['outcome'],
                'kind': 'different_conditions_or_insufficient_information_not_established_opposition',
                'sources': [ps[a['study_id']]['source_url'], ps[b['study_id']]['source_url']],
                'counterevidence': '同条件反例尚未确认', 'reasons': pair['reasons'],
                'conditions': [ps[c['study_id']]['experiments'][0]['conditions'] for c in (a, b)],
                'significance': [a['significance_original'], b['significance_original']],
                'uncertainty': '可能影响差异的条件不等于已证实原因；不显著不能判作相反结果。'})
    shared = [{'study_ids': [a['study_id'], b['study_id']], 'status': 'possible_only'}
              for a, b in itertools.combinations(normalized['papers'], 2) if possible_shared(a, b)]
    contradictions = []
    for pair in assessments:
        if pair['numeric_comparison'] != 'allowed_within_study_effects_only': continue
        a, b = (by_id[x] for x in pair['comparison_ids'])
        if (a['significance'] == b['significance'] == 'significant_reported'
            and {a['direction_records'][0]['direction'], b['direction_records'][0]['direction']} == {'higher', 'lower'}):
            contradictions.append({'comparison_ids': pair['comparison_ids'],
                'kind': 'possible_opposite_results_requires_independent_review',
                'sources': [ps[a['study_id']]['source_url'], ps[b['study_id']]['source_url']],
                'counterevidence': '其他条件匹配的研究尚未完整检索',
                'conditions': a['comparison_context'],
                'uncertainty': '显著性、误差和原文处理归属仍需人工核查；方向相反不证明差异显著。'})
    return {'findings': findings, 'pairwise_comparability': assessments,
            'gaps': gaps, 'disagreements': disagreements, 'possible_shared_experiments': shared, 'possible_contradictions': contradictions,
            'contradiction_status': ('存在待独立核查的可能相反结果。' if contradictions else '未发现满足同条件、同指标、相反方向及可核查比较的充分证据；不显著不是相反方向。'),
            'mechanisms': '缺少经来源核实的机制链，暂不能确认。',
            'shared_experiment_status': '论文间是否共享原始实验未能从结构化数据确认；年份或样本不得自动视为独立论文。',
            'novelty': 'unknown_not_assessed'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--checked', type=Path, required=True)
    ap.add_argument('--history-dir', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--candidate-values-only', action='store_true',
                    help='Use only numeric comparisons linked to historical candidate values')
    args = ap.parse_args()
    reference = json.loads(args.checked.read_text(encoding='utf-8'))
    # Deliberately discard the benchmark before creating the algorithm's input.
    reference.pop('cross_study_comparisons', None)
    normalized = prepare(reference, args.history_dir)
    if args.candidate_values_only:
        normalized['comparisons'] = [c for c in normalized['comparisons']
                                     if c['value_origin'] == 'historic_numeric_candidate']
    result = synthesize(normalized)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, obj in [('normalized_evidence.json', normalized), ('synthesis.json', result)]:
        (args.output_dir / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'papers': len(normalized['papers']), 'comparisons': len(normalized['comparisons']),
                      'findings': len(result['findings']), 'pairwise': len(result['pairwise_comparability']),
                      'historic_counts': dict(Counter(k for p in normalized['papers'] for k, v in p['historic_records'].items() for _ in v))}, ensure_ascii=False))


if __name__ == '__main__': main()
