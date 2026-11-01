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
            'experiments': [{'experiment_id': None, 'status': 'identity_unresolved',
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
            'experiment_id': None, 'outcome': outcome,
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
    # Direction alone is not a shared scientific finding.
    findings = []
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
        if (a.get('independence_status') == b.get('independence_status') == 'independent_confirmed'
            and a['significance'] == b['significance'] == 'significant_reported'
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


def _source_provenance(evidence):
    """Preserve model-reported locations separately from parsed/verified ones."""
    loc=evidence.get('location') if isinstance(evidence,dict) else None
    if isinstance(loc,dict):
        return {'model_reported':loc.get('model_reported'), 'program_parsed':loc.get('program_parsed'),
                'pdf_verified':loc.get('pdf_verified'), 'original':evidence}
    anchor=evidence.get('anchor') if isinstance(evidence,dict) else None
    if isinstance(evidence,dict) and evidence.get('reporting_origin')=='manual_figure_estimate':
        return {'model_reported':None,'program_parsed':None,'manual_reported':anchor,'pdf_verified':None,'original':evidence}
    if isinstance(evidence,dict) and evidence.get('reporting_origin') in ('program_pdf_table_extraction','program_source_table_extraction'):
        return {'model_reported':None, 'program_parsed':anchor, 'pdf_verified':None, 'original':evidence}
    return {'model_reported':anchor, 'program_parsed':None, 'pdf_verified':None, 'original':evidence}


def _converted_factor(c):
    if c.get('contrast_role'):
        return 'salinity_reclamation' if any(x in c['contrast_role'] for x in ('salinity','reclamation','soil_origin')) else 'unknown'
    a,b=c.get('treatment',{}),c.get('comparator',{})
    ta,co=str(a.get('label','')),str(b.get('label',''))
    if re.fullmatch(r'N\d+',ta,re.I) and (re.fullmatch(r'N\d+',co,re.I) or co=='对照'):
        return 'nitrogen_input'
    if re.fullmatch(r'S\d+N\d+',ta,re.I) or re.fullmatch(r'S\d+N\d+',co,re.I):
        return 'straw_and_nitrogen'
    texts=' '.join(str(x.get('description') or '') for x in a.get('levels',[])+b.get('levels',[])).lower()
    if any(x in texts for x in ('ditch water','river water','沟水','河水')): return 'water_source'
    if ta in ('W','BW') and co=='CK':return 'water_land_use_confounded'
    if ('浅湿' in texts and '淹灌' in texts) or (('flooded' in texts or 'flooding' in texts) and 'intermittent' in texts):
        return 'water_regime'
    return 'unknown'


def _outcome_family(outcome, unit=None):
    x=str(outcome or '').lower().replace('₂','2').replace('₄','4')
    u=str(unit or '').lower()
    if 'gwp' in x or 'co2eq' in u:
        if ('n2o' in x and 'ch4' in x) or any(k in x for k in ('multi_gas','total_gwp','combined_gwp')):return 'multi_gas_gwp'
        if 'n2o' in x and ('n2o_gwp' in x or 'gwp_n2o' in x):return 'n2o_gwp'
        if 'ch4' in x and ('ch4_gwp' in x or 'gwp_ch4' in x):return 'ch4_gwp'
        return 'unknown_indicator'
    if 'n2o' in x:
        if 'n-n2o' in u or 'n2o-n' in u or 'n2o_n_mass' in x:return 'n2o_n_mass'
        if ('n2o' in u or 'n2o_mass' in x) and ('season' in x or 'cumulative' in x):return 'n2o_mass'
        return 'unknown_indicator'
    if any(k in x for k in ('carbon_mineralization','carbon mineralization','碳矿化','碳转化速率')):return 'carbon_process'
    if any(k in x for k in ('nitrification','denitrification','nitrogen_mineralization','硝化速率','反硝化速率','氮矿化')):return 'nitrogen_process'
    if 'ch4' in x:return 'ch4'
    if 'co2' in x:return 'co2'
    if 'grain_yield' in x or 'yield per hill' in x:return 'rice_yield'
    if x=='soil_ph':return 'soil_ph'
    if 'organic_carbon' in x:return 'soil_carbon_pool'
    if x in ('soil_total_nitrogen','soil_total_n'):return 'soil_nitrogen_pool'
    return 'other'


def adapt_converted(converted, topic_profile):
    """Minimal v1 schema adapter; no checked reference, inferred Gold, or DOE ID."""
    if not topic_profile.get('primary_question') or not topic_profile.get('primary_eligibility'):
        raise ValueError('需要预先给定研究问题及纳入标准')
    if topic_profile.get('population_requirement','saline_rice') not in ('rice','saline_rice'):
        raise ValueError('尚不支持该主题对象的自动纳入；请明确选择 rice 或 saline_rice')
    papers=[]; by={}
    for p in converted.get('papers',[]):
        e=p.get('experiment',{}); sid=doi(p.get('doi'))
        if not sid or sid in by:raise ValueError('缺少或重复的论文标识，不能安全合并')
        text=' '.join(str(x or '') for x in (p.get('title'),e.get('research_object_candidate'),e.get('design_candidate')))
        design=str(e.get('design_candidate') or '')
        if re.search(r'\breview\b|meta.analysis|综述|系统评价',text,re.I):study_kind='review_or_meta'
        elif re.search(r'观察性|observational',design,re.I):study_kind='observational'
        elif re.search(r'盆栽|土柱|容器|mesocosm|soil.column|pot experiment|lysimeter',design,re.I):study_kind='pot_experiment'
        elif re.search(r'田间|田块|小区|field experiment',design,re.I):study_kind='field_experiment'
        else:study_kind='unknown'
        row={'study_id':sid,'doi':p.get('doi'),'title':p.get('title'),'study_type':study_kind,
             'source_url':p.get('source_url'),'pdf_sha256':p.get('pdf_sha256'),
             'source_state':'historical_candidate_not_human_gold',
             'experiments':[{'experiment_id':e.get('experiment_id'),
                 'status':'identity_unresolved' if not e.get('experiment_id') else 'candidate_identity',
                 'treatments':[], 'design_candidate':e.get('design_candidate'),
                 'conditions_candidate':e.get('conditions_candidate'),
                 'research_object_candidate':e.get('research_object_candidate'),
                 'experimental_unit':e.get('experimental_unit'),
                 'independent_replicates':e.get('independent_replicates'),
                 'sampling_replicates':e.get('sampling_replicates')}],
             'independence_status':'unknown', 'historical_context':text}
        papers.append(row);by[sid]=row
    comparisons=[]
    source_issues=defaultdict(list)
    for f in converted.get('field_audit',[]):
        name=str(f.get('field') or '').lower(); status=str(f.get('source_status') or '')
        if (any(w in name for w in ('internal_conflict','contradiction','冲突','显著性差异','误差形式','数值差异'))
            or status in ('原文表述不同','原文两处不同','原文表述冲突','图文疑点','量纲疑点')):
            source_issues[doi(f.get('doi'))].append({'field':f.get('field'),'value':f.get('value'),
                'status':f.get('source_status'),'evidence':f.get('evidence')})
    for c in converted.get('comparisons',[]):
        sid=doi(c.get('doi')); p=by.get(sid)
        if not p:raise ValueError('比较缺少所属论文: '+str(c.get('comparison_id')))
        exp_id=c.get('experiment_id')
        if exp_id and not any(x.get('experiment_id')==exp_id for x in p['experiments']):
            p['experiments'].append({'experiment_id':exp_id,'status':'comparison_declared_context_unverified','treatments':[]})
        a,b=c.get('treatment',{}),c.get('comparator',{})
        factor=_converted_factor(c);family=_outcome_family(c.get('outcome'),a.get('unit'))
        context=p['historical_context'].lower()
        rice=bool(re.search(r'rice|paddy|稻',context))
        positive_context=re.sub(r'(?:salinity|saline|sodicity|sodic)\s+(?:not\s+(?:specified|reported|confirmed|known)|unknown)|盐碱(?:条件)?(?:未报告|未确认|未知)', '', context)
        saline=bool(re.search(r'salin|sodi|盐碱|盐土|盐渍',positive_context))
        population=topic_profile.get('population_requirement','saline_rice')
        eligible_population=rice and (saline or population=='rice')
        if family in ('n2o_mass','n2o_n_mass','n2o_gwp','ch4_gwp','multi_gas_gwp','ch4','carbon_process','nitrogen_process') and factor=='water_regime' and eligible_population and p['study_type'] in ('pot_experiment','field_experiment','observational'):
            eligibility='primary_candidate_requires_source_review'
        elif family in ('n2o_mass','n2o_n_mass','n2o_gwp','ch4_gwp','multi_gas_gwp','ch4','co2','carbon_process','nitrogen_process') and rice and p['study_type'] in ('pot_experiment','field_experiment'):
            eligibility='indirect_context' if saline else 'external_context_salinity_unconfirmed'
        elif family in ('soil_carbon_pool','soil_nitrogen_pool') and factor=='water_regime' and rice and saline:
            eligibility='water_pool_context_not_transformation_rate'
        elif family=='rice_yield' and rice and saline:
            eligibility='regression_context_outside_primary_outcome'
        elif family=='rice_yield' and rice:
            eligibility='external_context_salinity_unconfirmed'
        else:eligibility='outside_topic_or_unresolved'
        values={'intervention':a.get('value'),'comparator':b.get('value'),
                'unit':a.get('unit'),'comparator_unit':b.get('unit'),
                'intervention_error':a.get('error'),'intervention_error_type':a.get('error_type'),
                'comparator_error':b.get('error'),'comparator_error_type':b.get('error_type'),
                'reported_effect':c.get('reported_effect')}
        av,bv=a.get('value'),b.get('value'); direction=[]
        if (isinstance(av,(int,float)) and isinstance(bv,(int,float)) and a.get('unit') and a.get('unit')==b.get('unit')
            and not re.search(r'unresolved|conflict|冲突|未知',str(a.get('unit')),re.I)):
            direction=[{'direction':'higher' if av>bv else 'lower' if av<bv else 'equal',
                        'interpretation':'within_record_arithmetic_only'}]
        raw=c.get('evidence') or {}
        comparisons.append({'comparison_id':c.get('comparison_id'),'study_id':sid,
            'experiment_id':c.get('experiment_id'),'dataset_identity':'unknown' if not c.get('experiment_id') else 'candidate',
            'outcome':c.get('outcome'),'outcome_family':family,'factor':factor,
            'intervention':str(a.get('label') or ''),'comparator':str(b.get('label') or ''),
            'treatment_details':a,'comparator_details':b,'comparator_role':c.get('comparator_role','reported_comparator_role_unconfirmed'),
            'numeric_values':values,'significance':str(c.get('significance_reported') or 'unknown'),
            'independent_replicates':c.get('independent_replicates'), 'experimental_unit':c.get('experimental_unit'),
            'sampling_replicates':c.get('sampling_replicates'), 'context_attribution':c.get('context_attribution','unknown'), 'observation_time':c.get('observation_time'),
            'raw_evidence':[_source_provenance(raw)],'record_state':'candidate_not_human_gold',
            'direction_records':direction,'conflicts':list(c.get('warnings') or []),
            'comparison_context':c.get('comparison_context') or {},'metric_type':c.get('metric_type'),
            'eligibility':eligibility,'source_url':p.get('source_url'), 'source_issues':source_issues.get(sid,[])})
    ids=[c['comparison_id'] for c in comparisons]
    if None in ids or len(ids)!=len(set(ids)):raise ValueError('比较ID缺失或重复')
    return {'version':'1.0','input_files':converted.get('input_sources',{}),'papers':papers,'comparisons':comparisons,
            'independence_reviews':converted.get('independence_reviews',[]),
            'topic_profile':topic_profile,'selection':converted.get('selection'),
            'adapter':'converted_historical_candidates_v1','unmatched_fields':converted.get('field_audit',[])}


def apply_source_checks(normalized, checks):
    """Attach scoped PDF reviews to exact comparisons; never upgrade Candidate."""
    papers={p['study_id']:p for p in normalized['papers']}
    comparisons={c['comparison_id']:c for c in normalized['comparisons']}
    seen=set()
    for item in checks.get('checks',[]):
        sid=doi(item.get('doi')); paper=papers.get(sid); cid=item.get('comparison_id')
        if not cid or item.get('outcomes'):
            raise ValueError('PDF核查必须指定单条comparison_id；不得按论文和结局批量附加')
        comp=comparisons.get(cid)
        if not paper or not comp or comp['study_id']!=sid or not item.get('pdf_sha256') or item['pdf_sha256']!=paper.get('pdf_sha256'):
            raise ValueError('PDF核查记录的比较、DOI或哈希不匹配')
        if cid in seen: raise ValueError('同一比较的PDF核查重复')
        if not item.get('pages') or not item.get('scope') or not item.get('status'):
            raise ValueError('PDF核查必须指明实际页码、具体范围和核查状态')
        seen.add(cid)
        for ev in comp['raw_evidence']:
            ev['pdf_verified']={'status':item['status'],'pages':item['pages'],
                'scope':item['scope'],'pdf_sha256':item['pdf_sha256'],'comparison_id':cid}
    return normalized


def synthesize_converted(normalized):
    """Topic-gated descriptive comparison; never infer common findings from signs."""
    comparisons=normalized['comparisons']; groups=defaultdict(list)
    for c in comparisons:
        if c['eligibility'] in ('primary_candidate_requires_source_review','indirect_context','external_context_salinity_unconfirmed','regression_context_outside_primary_outcome'):
            groups[(c['study_id'],c.get('experiment_id') or '',c.get('comparison_family',c['outcome_family']))].append(c)
    result_pairs=[]
    for (sid_a,exp_a,fam_a),(sid_b,exp_b,fam_b) in itertools.combinations(sorted(groups),2):
        if fam_a!=fam_b:continue
        if sid_a==sid_b and (not exp_a or not exp_b or exp_a==exp_b):continue
        aa,bb=groups[(sid_a,exp_a,fam_a)],groups[(sid_b,exp_b,fam_b)]
        # Single DOI groups may conceal multiple experiments; list records but do
        # not call them one experiment or count them as independent replications.
        factors_a=sorted({c['factor'] for c in aa});factors_b=sorted({c['factor'] for c in bb})
        reasons=[]
        if factors_a!=factors_b:reasons.append('处理因素或比较基准不同')
        if any(c['comparator_role']=='descriptive_reference_not_author_control' for c in aa+bb):
            reasons.append('至少一个比较基准是观察性来源组，不是作者指定对照')
        units={str(c['numeric_values'].get('unit') or 'unknown') for c in aa+bb}
        if len(units)>1:reasons.append('结果数值或报告效应的单位和口径不同或未知')
        paper_by_id={p['study_id']:p for p in normalized['papers']}
        kinds={paper_by_id.get(sid,{}).get('study_type','unknown') for sid in (sid_a,sid_b)}
        if len(kinds)>1: reasons.append('试验尺度或类型不同：'+', '.join(sorted(kinds)))
        if any((c.get('independent_replicates') or {}).get('value')==1 or re.search(r'pseudo.replicat|伪重复',str((c.get('independent_replicates') or {}).get('original') or ''),re.I) for c in aa+bb):
            reasons.append('至少一项研究报告伪重复，处理层独立重复不足或未知')
        reasons.append('测量时间窗口、指标定义和共同效应量未充分确认')
        reasons.append('不同论文是否共享原始实验或数据集未知，不能计为独立重复')
        contexts=[c['comparison_context'] for c in aa+bb]
        keys=('estimand','outcome_definition','treatment_definition','comparator_definition','time_basis','unit')
        same=all(ctx and ctx.get('source_status')=='source_checked' and all(ctx.get(k) for k in keys) for ctx in contexts) and all(
            all(ctx[k]==contexts[0][k] for k in keys) for ctx in contexts[1:])
        metric_complete=all(c['metric_type']=='group_values' and c['numeric_values'].get('intervention') is not None
                            and c['numeric_values'].get('comparator') is not None for c in aa+bb)
        # Whether quantitative comparison is feasible is separate from whether
        # the studies can be pooled or treated as independent.
        conditional='possible_pending_source_and_independence_review' if same and metric_complete and factors_a==factors_b and factors_a!=['unknown'] and not any(c['source_issues'] for c in aa+bb) else 'not_established'
        result_pairs.append({'study_ids':[sid_a,sid_b],'outcome_family':fam_a,
             'comparison_ids':[[c['comparison_id'] for c in aa],[c['comparison_id'] for c in bb]],
             'experiment_ids':[exp_a or None,exp_b or None],
             'A_descriptive':'permitted_with_conditions_and_source_status',
             'B_conditional_quantitative':conditional,'C_meta_analysis':'not_implemented',
             'factor_classes':[factors_a,factors_b],'independence':'unknown',
             'potential_effect_modifiers':['土壤、盐度、灌溉、施氮、时间、试验尺度、采样及统计方法可能不同'],
             'reasons':reasons,'source_conflicts':list(dict.fromkeys(w for c in aa+bb for w in c['conflicts'])),
             'recorded_source_issues':[issue for c in (aa[0],bb[0]) for issue in c['source_issues']],
             'sources':[aa[0]['source_url'],bb[0]['source_url']]})
    findings=[]
    keys=('estimand','outcome_definition','treatment_definition','comparator_definition','time_basis','unit')
    eligible=[c for c in comparisons if c['eligibility']=='primary_candidate_requires_source_review']
    for a,b in itertools.combinations(eligible,2):
        ca,cb=a['comparison_context'],b['comparison_context']
        if a.get('comparison_family',a['outcome_family'])!=b.get('comparison_family',b['outcome_family']) or a['factor']!=b['factor'] or a['factor']=='unknown': continue
        if not all(c.get('source_status')=='source_checked' and all(c.get(k) for k in keys) for c in (ca,cb)): continue
        if any(ca[k]!=cb[k] for k in keys): continue
        if any(c['metric_type']!='group_values' or c['conflicts'] or c['source_issues'] or not c.get('experiment_id') or not any(e.get('pdf_verified') for e in c['raw_evidence']) for c in (a,b)): continue
        datasets={(c['study_id'],c['experiment_id']) for c in (a,b)}
        if len(datasets)!=2: continue
        reviews=normalized.get('independence_reviews',[])
        if not any(r.get('status')=='source_checked_independent' and r.get('evidence') and
                   {(x.get('study_id'),x.get('experiment_id')) for x in r.get('datasets',[])}==datasets for r in reviews): continue
        da=a['direction_records'][0]['direction'] if a['direction_records'] else None
        db=b['direction_records'][0]['direction'] if b['direction_records'] else None
        if da not in ('lower','higher') or da!=db: continue
        matching=[c for c in eligible if c.get('comparison_family',c['outcome_family'])==a.get('comparison_family',a['outcome_family']) and c['comparison_context'].get('source_status')=='source_checked' and all(c['comparison_context'].get(k)==ca[k] for k in keys)]
        if any(not c['direction_records'] or c['direction_records'][0]['direction']!=da or c['conflicts'] or c['source_issues'] for c in matching): continue
        findings.append({'kind':'conditional_descriptive_common_observation', 'direction':da,
                         'comparison_ids':[a['comparison_id'],b['comparison_id']],
                         'sources':[a['source_url'],b['source_url']], 'conditions':ca,
                         'uncertainty':'仅为指定条件下两项比较的描述；不代表显著性、因果效应或普遍规律。'})
    grouped={}
    for finding in findings:
        key=(finding['direction'],json.dumps({k:finding['conditions'].get(k) for k in keys},sort_keys=True,ensure_ascii=False))
        if key not in grouped: grouped[key]=finding
        else:
            for attr in ('comparison_ids','sources'):
                grouped[key][attr]=list(dict.fromkeys(grouped[key][attr]+finding[attr]))
    findings=list(grouped.values())
    shared=defaultdict(set)
    for c in comparisons:
        for side in ('treatment_details','comparator_details'):
            group=c[side].get('experimental_group_id')
            if group and c.get('experiment_id'): shared[(c['study_id'],c['experiment_id'],group)].add(c['comparison_id'])
    dependencies=[{'study_id':k[0],'experiment_id':k[1],'group_id':k[2], 'comparison_ids':sorted(v),
                   'status':'possible_shared_group_or_repeated_representation_do_not_count_as_independent'}
                  for k,v in shared.items() if len(v)>1]
    experiments=defaultdict(list)
    for c in comparisons:
        if c.get('experiment_id'): experiments[(c['study_id'],c['experiment_id'])].append(c['comparison_id'])
    experiment_comparisons=[{'study_id':sid,'experiment_id':eid,'comparison_ids':ids,
                             'status':'comparisons_from_one_declared_experiment_not_independent_studies'}
                            for (sid,eid),ids in experiments.items()]
    direct_records=[c for c in comparisons if c['eligibility']=='primary_candidate_requires_source_review']
    direct=[c['comparison_id'] for c in direct_records]
    observations=[]
    for c in direct_records:
        item={'comparison_id':c['comparison_id'],'study_id':c['study_id'],'outcome':c['outcome'],
              'treatment':c['intervention'],'comparator':c['comparator'], 'values':c['numeric_values'],
              'significance_reported':c['significance'],'source':c['raw_evidence'],
              'record_state':c['record_state'],'independent_replicates':c['independent_replicates'],
              'uncertainties':c['conflicts'], 'interpretation':'single_comparison_observation_not_a_causal_or_common_finding'}
        if c['direction_records']:
            item['observed_direction']=c['direction_records'][0]['direction']
            a,b=c['numeric_values']['intervention'],c['numeric_values']['comparator']
            if b and not any(e.get('manual_reported') for e in c['raw_evidence']):
                item['within_comparison_change_percent']=round(100*(a/b-1),2)
        else:item['observed_direction']='not_calculated_due_to_missing_or_unresolved_units'
        observations.append(item)
    pools=[{'comparison_id':c['comparison_id'],'study_id':c['study_id'],'outcome':c['outcome'],
            'treatment':c['intervention'],'comparator':c['comparator'],'values':c['numeric_values'],
            'significance_reported':c['significance'],'source':c['raw_evidence'],'uncertainties':c['conflicts'],
            'record_state':c['record_state'],'independent_replicates':c['independent_replicates'],
            'interpretation':'measured_pool_concentration_not_transformation_rate'}
           for c in comparisons if c['eligibility']=='water_pool_context_not_transformation_rate']
    mechanism={'status':'not_established_by_supplied_direct_evidence',
        'source_comparison_ids':direct,
        'uncertainty':'本次结构化直接证据未包含经核实的产甲烷/氧化、硝化/反硝化速率或同位素路径证据；不能据此断定其他文献不存在；作者解释不能自动当作实测机制。'}
    return {'topic':normalized['topic_profile']['topic'], 'primary_question':normalized['topic_profile']['primary_question'],
            'primary_evidence_ids':direct, 'direct_observations':observations, 'pool_observations':pools, 'mechanism_assessment':mechanism, 'primary_conclusion':'conditional_observation_requires_review' if findings else 'insufficient_evidence_no_common_finding',
            'indirect_evidence_ids':[c['comparison_id'] for c in comparisons if c['eligibility']=='indirect_context'],
            'external_evidence_ids':[c['comparison_id'] for c in comparisons if c['eligibility']=='external_context_salinity_unconfirmed'],
            'water_pool_context_ids':[c['comparison_id'] for c in comparisons if c['eligibility']=='water_pool_context_not_transformation_rate'],
            'pairwise_comparability':result_pairs, 'dependent_comparisons':dependencies, 'experiment_comparisons':experiment_comparisons,
            'findings':findings, 'independent_experiment_count':'unknown', 'meta_analysis':'not_implemented',
            'limitations':['不同DOI不证明实验独立；同一DOI也可包含多个实验。','来源状态为候选；模型定位和PDF实际核验分开。',
                           '不因同向、显著或不显著生成共同结论或真正矛盾。']}


def main():
    ap=argparse.ArgumentParser()
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--converted',type=Path,help='Formal: output of historical candidate converter')
    source.add_argument('--checked',type=Path,help='Legacy compatibility input, not a formal synthesis')
    ap.add_argument('--selection',type=Path,help='Predeclared topic and inclusion criteria')
    ap.add_argument('--source-audit',type=Path,help='Optional scoped, hash-matched assistant PDF review ledger')
    ap.add_argument('--history-dir',type=Path,help='Required only for legacy compatibility')
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--candidate-values-only',action='store_true',help='Legacy compatibility')
    args=ap.parse_args()
    if args.converted:
        if not args.selection:ap.error('--converted requires --selection')
        payload=json.loads(args.converted.read_text(encoding='utf8'))
        profile=json.loads(args.selection.read_text(encoding='utf8'))
        normalized=adapt_converted(payload,profile)
        if args.source_audit:apply_source_checks(normalized,json.loads(args.source_audit.read_text(encoding='utf8')))
        result=synthesize_converted(normalized)
        result['input_conversion_sha256']=digest(args.converted)
        result['source_audit_sha256']=digest(args.source_audit) if args.source_audit else None
    else:
        if not args.history_dir:ap.error('--checked requires --history-dir')
        reference=json.loads(args.checked.read_text(encoding='utf8'));reference.pop('cross_study_comparisons',None)
        normalized=prepare(reference,args.history_dir)
        if args.candidate_values_only:normalized['comparisons']=[c for c in normalized['comparisons'] if c['value_origin']=='historic_numeric_candidate']
        result=synthesize(normalized);result['legacy_only_not_formal']='legacy reference input'
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for name,obj in [('normalized_evidence.json',normalized),('synthesis.json',result)]:
        (args.output_dir/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'papers':len(normalized['papers']),'comparisons':len(normalized['comparisons']),
                      'findings':len(result['findings']),'pairwise':len(result['pairwise_comparability'])},ensure_ascii=False))

if __name__=='__main__':main()
