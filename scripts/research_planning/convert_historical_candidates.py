"""Convert historical per-paper evidence without a checked-reference input.

Only labelled numbers and reported effects are converted; unresolved records are
emitted with reasons. No model, network, retrieval or checked_evidence input.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

NUM = r'[-+]?\d+(?:\.\d+)?'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def read_selected(path, selected_dois=None):
    output = []
    with Path(path).open(encoding='utf8') as f:
        for line, text in enumerate(f, 1):
            r = json.loads(text)
            if selected_dois is None or str(r.get('doi', '')).lower() in selected_dois:
                output.append((line, r))
    return output


def evidence_loc(r):
    ev = r.get('evidence') or []
    if isinstance(ev, dict) and isinstance(ev.get('record_metadata'), dict):
        ev = ev['record_metadata'].get('evidence') or ev
    return ev


def field_audit(line, r):
    field, value = r.get('field', ''), str(r.get('value', ''))
    ev = r.get('evidence')
    pointer = isinstance(ev, dict) and 'record_metadata' in ev and ev['record_metadata'].get('review_status') == '已覆盖'
    if pointer:
        state, target, why = 'pointer_only', 'source_locator', '本导出行仅为覆盖清单和位置提示；现有解析未从历史回复恢复该字段的完整事实，需人工核查。'
    elif field in ('新核查 · 土壤 EC 与 pH 显著性', '新核查 · 真菌多样性'):
        state, target, why = 'partial_numeric', 'labelled_measurements', '包含标签、均值、误差、字母及来源位置；只转换可识别指标。EC单位在该候选文本中缺失。' if '土壤' in field else '包含标签、均值、误差和字母；阈值依表注核对，数值仍为候选。'
    elif field in ('主要结果', '结果数值及单位'):
        state, target, why = 'partial_numeric', 'candidate_measurement_and_uncertainty', '有分组均值、误差和字母；与数值候选匹配后可补充误差，不重复计为独立结果。'
    elif any(x in field.lower() for x in ('internal_conflict', 'contradiction', '冲突', '显著性差异')):
        state, target, why = 'qualitative_only', 'source_conflicts', '保留原文冲突及比较范围不明，不转换为处理效应。'
    elif any(x in field.lower() for x in ('conclusion', '结论', 'main_results', '主要数值', 'results', '显著性')):
        state, target, why = 'qualitative_only', 'reported_claim_or_significance', '保留作者或模型陈述；缺少完整配对数值、独立比较范围或不能自动当作作者事实。'
    else:
        state, target, why = 'metadata_candidate', 'study_or_design_metadata', '可作为带来源的原始设计/方法候选；自由文本不自动等同结构化事实或人工审核。'
    if state == 'metadata_candidate':
        lower = field.lower()
        if any(x in lower for x in ('replicate','重复')):
            why = '保留原始重复层次；仅单位及数量同时明确时程序提取，取样或技术重复不充当独立实验单位。'
        elif any(x in lower for x in ('treatment','control','处理','对照')):
            why = '保留处理或对照原文；可明确解析的代码和水平进入实验记录，其余自由文本待核。'
        elif any(x in lower for x in ('design','设计')):
            why = '保留实验设计候选；随机化、区组和因素层次需要结合原始方法核查。'
        elif any(x in lower for x in ('method','indicator','measure','指标','方法')):
            why = '保留测量指标或方法原文；不是带处理与对照的数值比较。'
        elif any(x in lower for x in ('object','location','conditions','对象','条件')):
            why = '保留研究对象或环境条件候选；缺少的盐度、时间或具体条件不推断。'
        else:
            why = '保留原字段文本；无法仅凭字段名形成带处理和对照的数值比较。'
    if field == 'results' and '摘要' in value:
        why = '摘要只提供一个处理的数值，缺少对应对照；且已有摘要与表格冲突，不能生成组间比较。'
    if field == '主要数值':
        why = '只报告百分比且候选未明确具体参考组，不能反推组均值或对照。'
    if not ev:
        why += ' 原候选未提供原文定位。'
    return {'line': line, 'doi': r.get('doi'), 'field': field, 'value': r.get('value'),
            'source_status': r.get('status'), 'record_class': r.get('record_class'),
            'conversion': state, 'destination': target, 'reason': why,
            'source_sha256': r.get('source_sha256'), 'evidence': evidence_loc(r)}


def fields_by_name(records):
    grouped=defaultdict(list)
    for _, r in records: grouped[r['field']].append(r)
    # Conflicting same-name fields can belong to different experiments. Retain
    # them in field_audit, but do not arbitrarily assign one to a comparison.
    return {k:v[0] for k,v in grouped.items() if len(v)==1}


def add_comparison(out, paper, result_id, outcome, treatment, comparator, group_a, group_b,
                   metric_type, evidence, significance, warnings=None):
    out.append({'comparison_id': result_id, 'doi': paper['doi'],
        'experiment_id': paper['experiment']['experiment_id'], 'outcome': outcome,
        'treatment': {'label': treatment, 'levels': group_levels(paper, treatment), **group_a},
        'comparator': {'label': comparator, 'levels': group_levels(paper, comparator), **group_b},
        'metric_type': metric_type, 'observation_time': evidence.get('observation_time'), 'significance_reported': significance,
        'experimental_unit': paper['experiment']['experimental_unit'],
        'independent_replicates': paper['experiment']['independent_replicates'],
        'sampling_replicates': paper['experiment']['sampling_replicates'],
        'record_state': 'candidate_not_human_gold', 'evidence': evidence,
        'warnings': warnings or []})


def group_levels(paper, label):
    levels = paper['experiment']['factor_levels']
    tokens = [label] if label in levels else (re.findall(r'[SN]\d+', label) if re.fullmatch(r'(?:[SN]\d+)+', label) else [])
    if not tokens and len(label) == 4 and label[:2] in levels and label[2:] in levels:
        tokens = [label[:2], label[2:]]
    return [{'code': t, 'description': levels.get(t)} for t in tokens]


def experiment(doi, fs):
    def value(*names):
        for name in names:
            if name not in fs: continue
            record=fs[name]; ev=record.get('evidence')
            if isinstance(ev,dict) and isinstance(ev.get('record_metadata'),dict) and ev['record_metadata'].get('review_status') == '已覆盖':
                continue  # A coverage pointer is not the study fact it points to.
            return record.get('value')
        return None
    design = value('study_design', '试验设计', 'design', '设计处理', '研究对象与设计', '主要设计')
    treatment = value('treatments_levels', '处理', 'treatments', '设计处理', '处理与对照') or ''
    rep_names = ('independent_replicates', '独立试验重复', 'replicates', '试验重复')
    rep = value(*rep_names)
    rep_field = next((name for name in rep_names if name in fs), None)
    sample = value('sampling_replicates', '采样或技术重复', '采样重复')
    unit, n = None, None
    if rep and re.search(r'three biological soil columns per factorial', str(rep), re.I): unit,n = 'soil_column',3
    elif rep and re.search(r'每处理\s*3\s*个独立盆', str(rep)): unit,n = 'pot',3
    elif rep and re.search(r'每处理\s*3\s*块小区', str(rep)): unit,n = 'field_plot',3
    elif rep and re.search(r'每处理\s*3\s*个重复.*土柱为处理单位', str(rep)): unit,n = 'soil_column',3
    # Do not treat the coverage-only wording "每处理5次" as an identified unit.
    elif rep and re.search(r'区组/主区需分层', str(rep)): unit,n = 'main_plot_for_irrigation; subplot_for_inoculation',3
    levels = {}
    if 'IS为' in treatment:
        for code, desc in re.findall(r'(IS|IF|CN|CW)(?:为)?([^，。；、]+)', treatment): levels[code] = desc
    elif re.search(r'\bS1\b', treatment):
        pattern = re.compile(r'\b(S[123]|N[123])\s+(.+?)(?=;\s*(?:S[123]|N[123])\b|\.\s*(?:Straw|Total N)|$)')
        for m in pattern.finditer(treatment): levels[m.group(1)] = m.group(2).strip()
    elif 'CK旱地' in treatment:
        for label, desc in re.findall(r'(CK|BW|W)([^；。]+)', treatment): levels[label] = desc
    elif re.search(r'kg\s*N/ha',treatment,re.I):
        dose=re.search(r'([0-9、，,\s]+)\s*kg\s*N/ha',treatment,re.I)
        if dose:
            for amount in re.findall(r'\d+',dose.group(1)):
                levels['N'+amount]=amount+' kg N/ha'
        control=re.search(r'(\d+)氮为对照',treatment)
        if control:levels['对照']=control.group(1)+' kg N/ha'
    return {'experiment_id': None, 'experiment_identity': 'unresolved_not_assumed_one_per_paper', 'design_candidate': design,
        'research_object_candidate': value('research_object','研究对象及条件','研究对象','study_object','研究对象与设计'),
        'conditions_candidate': value('location_conditions'),
        'treatments_candidate': treatment or None, 'factor_levels': levels,
        'control_candidate': value('controls','对照及比较基准','control','对照'),
        'experimental_unit': {'value': unit, 'source_field': rep_field, 'status': 'candidate' if unit else 'unknown'},
        'independent_replicates': {'value': n, 'original': rep, 'status': 'candidate' if n else 'unknown'},
        'sampling_replicates': {'original': sample, 'status': 'candidate' if sample else 'unknown'},
        'measurement_indicators_candidate': value('measurement_indicators','测量指标及方法','measures'),
        'measurement_methods_candidate': value('measurement_methods','测量指标及方法','methods','测量方法'),
        'record_state': 'candidate_not_human_gold'}


def pair_coverage(evidence, labels):
    # Row names in metadata are location hints; only a quoted excerpt containing
    # both labels demonstrates that the candidate quote covers both rows.
    chunks = evidence if isinstance(evidence, list) else [evidence]
    text = ' '.join(str(x.get('quote') or x.get('excerpt') or x.get('text') or '')
                    for x in chunks if isinstance(x, dict)).upper()
    return all(re.search(r'(?<![A-Z0-9])'+re.escape(x.upper())+r'(?![A-Z0-9])', text) for x in labels)


def parse_numeric_result(r, paper):
    idx = r['index']; doi = r['doi']; outcome = r['indicator']; ev = evidence_loc(r)
    source = {'source_file': 'results.jsonl', 'record_index': idx, 'source_sha256': r.get('source_sha256'), 'anchor': ev,
              'observation_time':r.get('time'), 'reporting_origin':r.get('reporting_origin'),
              'candidate_limitations':r.get('limitations')}
    records, failure = [], []
    value, t, c, unit = r.get('value'), str(r.get('treatment') or ''), str(r.get('comparator') or ''), r.get('unit')
    # JSON mapping with two fully named groups and an explicit ordered comparison.
    if isinstance(value, dict) and len(value) == 2 and re.fullmatch(r'[A-Za-z0-9]+\s+vs\s+[A-Za-z0-9]+', c, re.I):
        labels = re.split(r'\s+vs\s+', c, flags=re.I)
        if all(k in value and isinstance(value[k], (int,float)) for k in labels):
            aa, bb = ({'value': value[k], 'unit': unit, 'error': None, 'error_type': 'unknown'} for k in labels)
            # Enrich only from labelled per-paper field, never checked reference.
            for f in paper['raw_fields']:
                if f.get('field') not in ('主要结果', '结果数值及单位'): continue
                text = str(f.get('value', ''))
                for label, g in zip(labels, (aa,bb)):
                    match = re.search(r'\b'+re.escape(label)+r'\s+('+NUM+r')\s*±\s*('+NUM+r')\s*([a-z]+)',text,re.I)
                    if match and float(match.group(1)) == g['value']:
                        g.update(error=float(match.group(2)), error_type='SD' if 'SD' in text else 'unknown', significance_letters=match.group(3))
            warnings = ['候选表注对比较范围措辞需核对']
            add_comparison(records,paper,f'{doi}#result{idx}:0',outcome,*labels,aa,bb,'group_values',source,r.get('significance'),warnings)
        else: failure.append('比较标签与数值字典不一致或数值缺失')
    # Multiple labelled treatment means versus one control, positional pairing only
    # when the number of labels exactly matches the number of mean±error entries.
    elif isinstance(value,str) and ' versus ' in value and '±' in value:
        left,right = value.split(' versus ',1)
        labels = [x.strip() for x in t.split(';') if x.strip()]
        comps = [x.strip() for x in c.split(';') if x.strip()]
        pair_re = re.compile(r'('+NUM+r')\s*±\s*('+NUM+r')')
        avals,bvals = pair_re.findall(left),pair_re.findall(right)
        if len(labels)==len(avals) and len(comps)==len(bvals)==1:
            foot = json.dumps(ev,ensure_ascii=False).lower()
            error_type = 'SE' if 'standard error' in foot else 'SD' if 'standard deviation' in foot else 'unknown'
            for k,(label,(mean,error)) in enumerate(zip(labels,avals)):
                a={'value':float(mean),'unit':unit,'error':float(error),'error_type':error_type}
                b={'value':float(bvals[0][0]),'unit':unit,'error':float(bvals[0][1]),'error_type':error_type}
                warnings=['多个因素同时变化，不可归因于单一因素']
                if not pair_coverage(ev,[label,comps[0]]): warnings.append('原候选引句未完整覆盖两个表格行，需定向核原PDF')
                add_comparison(records,paper,f'{doi}#result{idx}:{k}',outcome,label,comps[0],a,b,'group_values',source,r.get('significance'),warnings)
        else: failure.append('处理标签与均值数量无法一一对应')
    # A reported difference is not a pair of group means.
    elif isinstance(value,str) and re.fullmatch(NUM,value.strip()) and t and c and ';' not in c:
        add_comparison(records,paper,f'{doi}#result{idx}:0',outcome,t,c,
                       {'value':None,'unit':None},{'value':None,'unit':None},
                       'reported_relative_effect' if unit == '%' else 'reported_difference',source,r.get('significance'),['组均值未提供；不得把差值当组均值','单因素主效应的另一因素汇总范围需核对'])
        records[-1]['reported_effect']={'value':float(value),'unit':unit}
    elif isinstance(value,str) and 'respectively' in value.lower():
        labels = [x.strip() for x in c.split(';') if x.strip()]
        vals = re.findall(r'('+NUM+r')\s*%',value)
        if t and len(labels)==len(vals) and len(vals)>1:
            for k,(label,v) in enumerate(zip(labels,vals)):
                add_comparison(records,paper,f'{doi}#result{idx}:{k}',outcome,t,label,
                    {'value':None,'unit':None},{'value':None,'unit':None},'reported_relative_effect',source,r.get('significance'),
                    ['百分比为作者报告的效应，组均值未知','主效应跨另一因素的汇总范围需核对'])
                records[-1]['reported_effect']={'value':float(v),'unit':'%'}
        else: failure.append('多对照与百分比无法按顺序一一对应')
    elif isinstance(value,str) and 'author-reported relative increase' in value:
        match=re.fullmatch(r'\s*('+NUM+r')\s*%;\s*author-reported relative increase\s*('+NUM+r')%\s*',value,re.I)
        if match and t and c:
            add_comparison(records,paper,f'{doi}#result{idx}:0',outcome,t,c,
                {'value':float(match.group(1)),'unit':'%'},{'value':None,'unit':'%'},
                'reported_relative_effect',source,r.get('significance'),['对照组绝对值未报告；不得反推为原文报告值'])
            records[-1]['reported_effect']={'value':float(match.group(2)),'unit':'%'}
        else: failure.append('相对效应格式或标签无法识别')
    elif isinstance(value,dict) and len(value)==1:
        k=next(iter(value)); match=re.fullmatch(r'([A-Za-z0-9]+)_vs_([A-Za-z0-9]+)',k,re.I)
        if match and isinstance(value[k],(int,float)):
            add_comparison(records,paper,f'{doi}#result{idx}:0',outcome,*match.groups(),
                {'value':None,'unit':None},{'value':None,'unit':None},'reported_relative_effect',source,r.get('significance'),
                ['该相对效应与同论文组均值可能为同一结果，不可当成独立研究'])
            records[-1]['reported_effect']={'value':value[k],'unit':unit}
        else: failure.append('无法解析作者报告效应或其处理标签')
    else: failure.append('缺少可识别的处理标签、成对数值或支持的数值格式')
    for row in records:
        if not unit: row['warnings'].append('单位未报告')
        if not ev: row['warnings'].append('原文位置缺失')
    return records, failure


def parse_field_pairs(paper, field):
    text = str(field['value']); ev = evidence_loc(field); output=[]
    source = {'source_file': 'fields.jsonl','field':field['field'],'source_sha256':field.get('source_sha256'),'anchor':ev}
    for indicator in ('pH','ACE','Shannon'):
        match = re.search(r'(?:^|[。；])\s*'+indicator+r'：([^。；]+)',text,re.I)
        if not match: continue
        data = {}
        for m in re.finditer(r'\b(BW|CK|W)\s+('+NUM+r')\s*±\s*('+NUM+r')\s*([a-z]+)',match.group(1),re.I):
            data[m.group(1).upper()]={'value':float(m.group(2)),'error':float(m.group(3)),
                'error_type':'SD' if 'SD' in text else 'unknown','unit':'pH' if indicator=='pH' else 'index',
                'significance_letters':m.group(4).lower()}
        if 'CK' not in data: continue
        for label in ('W','BW'):
            if label not in data: continue
            letters_a, letters_b = data[label]['significance_letters'],data['CK']['significance_letters']
            conclusion='letters_overlap_no_significant_difference_reported' if set(letters_a)&set(letters_b) else 'letters_disjoint_reported_different'
            add_comparison(output,paper,f"{paper['doi']}#field:{field['field']}:{indicator}:{label}-CK",
                'soil_ph' if indicator=='pH' else 'fungal_'+indicator.lower(),label,'CK',data[label],data['CK'],
                'group_values',source,conclusion,
                ['水田、旱地及肥料可能同时变化，不能单因子归因','作者摘要与逐项字母比较范围存在歧义'])
    return output


def parse_nitrogen_fields(paper, field):
    """Handle explicitly named N levels in the historical Chinese candidate format.

    This is a format adapter, not a claim that all N-level papers share one design.
    """
    text=str(field.get('value') or ''); name=field.get('field',''); out=[]
    source={'source_file':'fields.jsonl','field':name,'source_sha256':field.get('source_sha256'),
            'anchor':evidence_loc(field), 'location_status':{'model_reported':True,'program_parsed':True,'pdf_verified':False}}
    if '籽粒产量' in text and '±' in text and '对照' in text:
        unit_match=re.search(r'籽粒产量[（(]([^）)]+)[）)]',text)
        unit=unit_match.group(1) if unit_match else None
        groups={}
        for m in re.finditer(r'(对照|N\d+)\s*('+NUM+r')\s*±\s*('+NUM+r')\s*([a-z]+)',text,re.I):
            groups[m.group(1)]={'value':float(m.group(2)),'error':float(m.group(3)),
                'error_type':'SD' if '±SD' in text else 'unknown','unit':unit,'significance_letters':m.group(4).lower()}
        if '对照' in groups:
            for label in groups:
                if label=='对照':continue
                sig='letters_overlap_no_significant_difference_reported' if set(groups[label]['significance_letters']) & set(groups['对照']['significance_letters']) else 'letters_disjoint_reported_different'
                add_comparison(out,paper,f"{paper['doi']}#field:{name}:{label}-对照",'rice_grain_yield',
                    label,'对照',groups[label],groups['对照'],'group_values',source,sig,
                    ['氮水平比较，不是水分制度比较','共同对照不得计为独立实验','来源位置尚待核查'])
    m=re.search(r'(N\d+)与(N\d+)、(N\d+)相比，N2O排放低('+NUM+r')%和('+NUM+r')%',text,re.I)
    if m:
        treatment,c1,c2,v1,v2=m.groups()
        for control,v in ((c1,v1),(c2,v2)):
            add_comparison(out,paper,f"{paper['doi']}#field:{name}:{treatment}-{control}",'n2o_unspecified_period',
                treatment,control,{'value':None,'unit':None},{'value':None,'unit':None},
                'reported_relative_effect',source,'unknown',
                ['作者报告相对差异，组均值和误差未提供','氮处理比较，不是水分制度比较','同一试验或数据集是否独立尚待核实'])
            out[-1]['reported_effect']={'value':-float(v),'unit':'%','interpretation':'reported_lower'}
    return out


def parse_salinity_response(paper, response, response_rel, response_hash, review):
    """Legacy user-response greenhouse table; L is a reference, not a control."""
    section=re.search(r'###\s*6\.1\s*温室气体累积排放量(.*?)(?=\n###\s|\n##\s|\Z)',response,re.S)
    if not section: return [], ['历史回复未找到受支持的温室气体比较表'], response_hash
    block=section.group(1)
    if not re.search(r'多项结果以L处理为比较对象',response):
        return [], ['未发现明确的相对比较基准；不把L自动当作对照'],response_hash
    locations=re.search(r'来源：([^\n]+)',block)
    location=locations.group(1).strip() if locations else None
    levels={}
    for line in response.splitlines():
        m=re.match(r'^\|\s*([LMH])\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|',line)
        if m:levels[m.group(1)]='; '.join(x.strip() for x in m.groups()[1:])
    if not all(x in levels for x in ('L','M','H')):
        return [], ['缺少可对齐的三组环境条件'],response_hash
    ex=paper['experiment'];ex['factor_levels']=levels
    reps=re.search(r'\|\s*试验容器重复\s*\|\s*(\d+)个/处理',response)
    ex['experimental_unit']={'value':'mesocosm' if reps else None,'source_field':'historic_response','status':'candidate' if reps else 'unknown'}
    ex['independent_replicates']={'value':int(reps.group(1)) if reps else None,
                                  'original':reps.group(0) if reps else None,'status':'candidate' if reps else 'unknown'}
    ex['treatments_candidate']='盐度与垦殖历史并存的土壤组；非水分制度干预'
    ex['control_candidate']=None
    outcome_names={'CH₄':'cumulative_ch4','CO₂':'cumulative_co2','N₂O':'cumulative_n2o'}
    sup=str.maketrans({'ᵃ':'a','ᵇ':'b','ᶜ':'c'})
    out=[]
    for line in block.splitlines():
        cells=[x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells)!=4 or '累积' not in cells[0]:continue
        gas=next((k for k in outcome_names if k in cells[0]),None)
        um=re.search(r'[（(]([^）)]+)[）)]',cells[0])
        if not gas or not um:continue
        vals=[]
        for cell in cells[1:]:
            m=re.fullmatch(r'('+NUM+r')([ᵃᵇᶜ]+)',cell)
            if not m: break
            vals.append({'value':float(m.group(1)),'unit':um.group(1),'error':None,
                         'error_type':'unknown','significance_letters':m.group(2).translate(sup)})
        if len(vals)!=3:continue
        for idx,label in ((1,'M'),(2,'H')):
            src={'source_file':response_rel,'response_sha256':response_hash,'pdf_sha256':paper.get('pdf_sha256'),
                 'location':{'model_reported':location,'program_parsed':None,'pdf_verified':None},
                 'source_state':'user_pasted_model_response_not_gold',
                 'sharing_restriction':'not_push_github' if review.get('not_push_github') else 'unknown',
                 'row_text':line}
            sig='letters_overlap' if set(vals[idx]['significance_letters']) & set(vals[0]['significance_letters']) else 'letters_disjoint'
            add_comparison(out,paper,f"{paper['doi']}#response:{outcome_names[gas]}:{label}-L",outcome_names[gas],
                label,'L',vals[idx],vals[0],'group_values',src,sig,
                ['L是描述性比较基准，作者未明确设为对照','盐度、pH与垦殖年限共同变化，不能归因单一因素',
                 '土壤来自混合田块，容器重复不等于独立田块','模型转述数据未由本次程序核对原PDF'])
            out[-1]['comparator_role']='descriptive_reference_not_author_control'
            out[-1]['contrast_role']='soil_origin_and_reclamation_groups_not_water_regime'
    return out,([] if out else ['历史回复中没有可解析且带组标签的数值表']),response_hash


def parse_historic_response(paper, original_paper, corpus_root):
    """Read and hash-check the existing user-pasted response, not the benchmark."""
    response_rel = original_paper.get('historic_response_file')
    review_rel = original_paper.get('source_file')
    if not response_rel:
        return [], [], None
    if not corpus_root:
        return [], ['未提供历史语料根目录，无法读取原始模型回复'], None
    root = Path(corpus_root).resolve()
    response_path, review_path = (root / response_rel).resolve(), (root / review_rel).resolve()
    if root not in response_path.parents or root not in review_path.parents:
        return [], ['历史响应路径不在语料根目录内'], None
    if not response_path.is_file() or not review_path.is_file():
        return [], ['历史模型回复或记录元数据缺失'], None
    response_hash = sha(response_path)
    try:
        review = json.loads(review_path.read_text(encoding='utf8'))
    except (ValueError, UnicodeError):
        return [], ['已记录现有历史回复哈希；未发现可核验的原始回复哈希及受支持的表格格式，保留字段候选'], response_hash
    if isinstance(review.get('papers'),list):
        entries=[item for item in review['papers'] if str(item.get('doi','')).lower()==paper['doi'].lower()]
        if len(entries)==1 and entries[0].get('response_sha256')==response_hash:
            return parse_salinity_response(paper,response_path.read_text(encoding='utf8'),response_rel,response_hash,review)
        return [], ['历史响应的DOI或哈希不能匹配元数据'], None
    if not review.get('user_pasted_response_sha256'):
        return [], ['已记录现有历史回复哈希；未发现可核验的原始回复哈希及受支持的表格格式，保留字段候选'], response_hash
    if response_hash != review['user_pasted_response_sha256']:
        return [], ['历史模型回复哈希与先前记录不一致'], None
    response = response_path.read_text(encoding='utf8')
    levels = {}
    for line in response.splitlines():
        match = re.match(r'^\|\s*(T\d+|CK)\s*\|\s*([^|]+)\|\s*([^|]+)\|',line)
        if match and match.group(1) not in levels:
            levels[match.group(1)] = match.group(2).strip() + ' / ' + match.group(3).strip()
    section = re.search(r'###\s*6\.5\s*水稻产量(.*?)(?=\n###\s|\n##\s|\Z)',response,re.S)
    if not section: return [], ['历史回复缺少可识别的产量章节'], response_hash
    block=section.group(1); rows={}
    loc = re.search(r'第\s*(\d+)\s*页[^\n]*?Table\s*(\d+)', block, re.I)
    model_reported_location = loc.group(0) if loc else None
    parsed_location = {'physical_page':int(loc.group(1)), 'table':'Table '+loc.group(2)} if loc else None
    unit_match = re.search(r'(?:Yield|产量)\s*[（(]([^）)]+)[）)]', block, re.I)
    reported_unit = unit_match.group(1).strip() if unit_match else None
    for line in block.splitlines():
        m = re.match(r'^\|\s*(T\d+|CK)\s*\|\s*([\d,]+(?:\.\d+)?)\s*±\s*([\d,]+(?:\.\d+)?)\s*\|\s*([a-z]+)\s*\|\s*([^|]*)\|',line,re.I)
        if m:
            rows[m.group(1)]={'value':float(m.group(2).replace(',','')),'error':float(m.group(3).replace(',','')),
                'error_type':'unknown','unit':reported_unit,'significance_letters':m.group(4).lower(),
                'row_text':line}
    controls = re.findall(r'^\|\s*(T\d+|CK)\s*\|[^\n]*\|\s*对照\s*\|',block,re.M)
    if len(controls)!=1 or controls[0] not in rows:
        return [], ['历史回复未明确指定唯一对照行或结果缺失'], response_hash
    control = controls[0]
    if not all(label in levels for label in rows):
        return [], ['历史回复中的结果组无法全部关联处理定义'], response_hash
    ex=paper['experiment']; ex['factor_levels']=levels
    design = re.search(r'^\|\s*试验设计\s*\|\s*([^|]+)\|',response,re.M)
    if design: ex['design_candidate'] = design.group(1).strip()
    if 'Each treatment was repeated five times' in response and '每个处理重复5次' in response:
        ex['experimental_unit']={'value':'field_plot','source_field':'historic_response/design', 'status':'candidate'}
        ex['independent_replicates']={'value':5,'original':'Each treatment was repeated five times.', 'status':'candidate'}
        ex['sampling_replicates']={'original':'Samples were collected in triplicate; 具体层级不明','status':'candidate'}
    ex['treatments_candidate']='灌溉水源及其组合方式；原历史回复的处理表'
    ex['control_candidate']=control + ': ' + levels[control]
    output=[]
    for label, data in rows.items():
        if label==control: continue
        a,b=dict(data),dict(rows[control]); a.pop('row_text');b.pop('row_text')
        source={'source_file':response_rel,'response_sha256':response_hash,'pdf_sha256':paper['pdf_sha256'],
            'location': {'model_reported':model_reported_location,'program_parsed':parsed_location,'pdf_verified':None},
            'column': 'Yield',
            'row_texts':[rows[label]['row_text'],rows[control]['row_text']],
            'source_state':'user_pasted_model_response_not_gold'}
        sig='letters_overlap' if set(a['significance_letters']) & set(b['significance_letters']) else 'letters_disjoint'
        add_comparison(output,paper,f"{paper['doi']}#historic_response:yield:{label}-{control}",
            'rice_grain_yield',label,control,a,b,'group_values',source,sig,
            ['误差类型在表注中未明确','水源组合处理不是干湿交替',
             '讨论中未显著影响产量的比较基准不清，表格字母与文字的适用范围待核'])
    return output,[],response_hash


def comparability(a,b):
    def outcome_key(x):
        name = x.lower().replace('_', ' ')
        return 'candidate_grain_yield_per_hill' if name in ('grain yield', 'rice grain yield', 'yield per hill') else name
    same_outcome = outcome_key(a['outcome']) == outcome_key(b['outcome'])
    reasons=['研究处理因素、时间、土壤条件与设计须逐项核查','关联研究和误差结构未知，不能据方向断言矛盾']
    if a['treatment'].get('unit') != b['treatment'].get('unit'):
        reasons.append('原报告单位不同或写法不同，未经核实不能定量对齐')
    return {'comparison_ids':[a['comparison_id'],b['comparison_id']],
        'side_by_side': 'allowed_with_conditions' if same_outcome else 'separate_indicators_only',
        'quantitative_effect_comparison':'unknown_requires_common_estimand_and_condition_review',
        'statistical_pooling':'not_permitted_without_design_and_variance_review',
        'reasons':reasons}


def run(history_dir, corpus_root=None, selected_dois=None):
    root=Path(history_dir)
    names=('papers','fields','results','issues','historic_claims','supplement_files')
    if not all((root/(name+'.jsonl')).is_file() for name in names):
        raise FileNotFoundError('历史导出缺少必要 JSONL，拒绝输出伪装成完整审计的结果')
    data={name:read_selected(root/(name+'.jsonl'), selected_dois) for name in names}
    papers=[]
    for line,r in data['papers']:
        doi=r['doi']; fs=[x for x in data['fields'] if x[1].get('doi')==doi]
        p={'doi':doi,'title':r.get('title'),'source_url':'https://doi.org/'+doi,
           'pdf_sha256':r.get('raw_sha256'),'pdf_path':r.get('raw_file'),
           'experiment':experiment(doi,fields_by_name(fs)),
           'raw_fields':[x[1] for x in fs], 'source_paper_line':line}
        if corpus_root and p['pdf_path']:
            base=Path(corpus_root).resolve(); pdf=(base/p['pdf_path']).resolve()
            if base in pdf.parents and pdf.is_file():
                current=sha(pdf)
                p['pdf_integrity']={'status':'sha_matches' if current==p['pdf_sha256'] else 'sha_mismatch', 'actual_sha256':current}
            else: p['pdf_integrity']={'status':'missing_or_outside_corpus'}
        else: p['pdf_integrity']={'status':'not_checked'}
        papers.append(p)
    by={p['doi']:p for p in papers}; comparisons=[]; numeric_audit=[]
    for line,r in data['results']:
        produced,failures=parse_numeric_result(r,by[r['doi']]);comparisons.extend(produced)
        numeric_audit.append({'line':line,'doi':r['doi'],'index':r['index'],'indicator':r['indicator'],
            'conversion':('not_converted' if not produced else 'partial' if any(x['metric_type'] != 'group_values' or any('引句未完整' in w for w in x['warnings']) for x in produced) else 'group_values_candidate'),
            'produced_ids':[x['comparison_id'] for x in produced],
            'reason':failures or list(dict.fromkeys(w for x in produced for w in x['warnings'])) or ['处理、对照及均值来自原候选；不自动升级来源状态。'],
            'original_value':r['value'],'original_treatment':r['treatment'],'original_comparator':r['comparator'],
            'source_file':'results.jsonl','source_sha256':r.get('source_sha256'),
            'record_class':r.get('record_class'),'evidence':evidence_loc(r)})
    for line,r in data['fields']:
        if r['field'] in ('新核查 · 土壤 EC 与 pH 显著性','新核查 · 真菌多样性'):
            comparisons.extend(parse_field_pairs(by[r['doi']],r))
        comparisons.extend(parse_nitrogen_fields(by[r['doi']],r))
    # Keep the field audit separate; do not serialize all raw history twice.
    response_failures=[]; response_sources={}; recovered_dois=set()
    for _, original in data['papers']:
        paper=by[original['doi']]
        produced, failures, response_hash = parse_historic_response(paper, original, corpus_root)
        comparisons.extend(produced)
        if failures: response_failures.append({'doi':original['doi'],'reasons':failures})
        if response_hash: response_sources[original['doi']]={'sha256':response_hash,'comparisons':len(produced),
            'integrity':'prior_hash_matched' if produced else 'current_hash_only_not_independently_verified'}
        if produced: recovered_dois.add(original['doi'])
    # Every comparison gets the final context; response-derived design may have
    # resolved its own experimental unit after earlier candidate parsing.
    for c in comparisons:
        ex=by[c['doi']]['experiment']
        c['experimental_unit']=dict(ex['experimental_unit']); c['independent_replicates']=dict(ex['independent_replicates'])
        c['sampling_replicates']=dict(ex['sampling_replicates'])
        c['context_attribution']='explicit_experiment' if c.get('experiment_id') else 'paper_level_candidate_experiment_scope_unresolved'
        for key in ('experimental_unit','independent_replicates','sampling_replicates'):
            c[key]['association_status']=c['context_attribution']
    for p in papers: p.pop('raw_fields')
    audit=[field_audit(line,r) for line,r in data['fields']]
    recovered_reasons={
        'design':'原回复的设计表提供随机区组及小区信息；来源仍是历史模型回复。',
        'treatments':'原回复的处理表提供水源代码与定义；混合比例和交替周期仍需核原文。',
        'control':'原回复处理表和结果表将CK指定为黄河水对照。',
        'replicates':'原回复含每处理5次的英文引句；程序记录为田间小区候选。',
        'sampling_replicates':'原回复记录triplicate但其空间或分析层级不明确，不计作独立重复。',
        'yield_results':'原回复提供结果表的各处理值和字母；讨论比较基准仍有歧义。',
        'yield_table':'原回复中可按处理标签解析均值、误差和字母；误差类型未报告。',
        'significance':'原回复结果表提供字母；与讨论文字可能涉及不同比较基准，不能消解冲突。',
    }
    recovered_fields=set(recovered_reasons)
    for row in audit:
        if row['doi'] in recovered_dois and row['field'] in recovered_fields and row['conversion']=='pointer_only':
            row['conversion']='recovered_from_historic_response'
            row['reason']=recovered_reasons[row['field']] + ' 哈希与既有记录一致，仍须原文核查。'
            row['response_sha256']=response_sources[row['doi']]['sha256']

    pairs=[comparability(a,b) for a,b in itertools.combinations(comparisons,2)
           if a['doi']!=b['doi'] and a['metric_type']=='group_values' and b['metric_type']=='group_values']
    return {'version':'1.0','input_sources':{name:{'sha256':sha(root/(name+'.jsonl'))} for name in names},
        'papers':papers,'field_audit':audit,'response_sources':response_sources,'response_failures':response_failures,
        'numeric_audit':numeric_audit,'comparisons':comparisons,'pairwise_levels':pairs,
        'summary':{'papers':len(papers),'fields':len(data['fields']),'numeric_candidates':len(data['results']),
                   'field_conversion_counts':dict(Counter(x['conversion'] for x in audit)),
                   'comparisons':len(comparisons),'gold_upgrades':0}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--corpus-root',type=Path,help='Root containing referenced historic response files')
    selection=p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--selection',type=Path,help='JSON containing previous_regression_dois and new_title_selected')
    selection.add_argument('--doi',action='append',help='Select DOI; repeat for multiple papers')
    a=p.parse_args()
    if a.selection:
        config=json.loads(a.selection.read_text(encoding='utf8'))
        selected=set(x.lower() for x in config.get('previous_regression_dois',[])) | set(x['doi'].lower() for x in config.get('new_title_selected',[])) | set(x.lower() for x in config.get('dois',[]))
    else: selected=set(x.lower() for x in a.doi)
    if not selected: p.error('At least one externally specified DOI is required')
    d=run(a.history_dir,a.corpus_root,selected);
    d['selection']={'source':str(a.selection) if a.selection else 'command_line', 'requested_dois':sorted(selected),
                    'missing_dois':sorted(selected-{x['doi'].lower() for x in d['papers']})};a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(d['summary'],ensure_ascii=False))

if __name__=='__main__':main()
