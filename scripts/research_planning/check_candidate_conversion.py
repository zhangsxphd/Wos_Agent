"""Post-conversion reference check; never supplies values to the converter."""
import argparse
import json
import re
from pathlib import Path


def tokens(text):
    # Extract literal treatment codes from the single-paper reference label.
    return re.findall(r'ISCW|IFCW|S\d|N\d|T\d|CK|(?<![A-Z])W', text or '')


def normalized_label(text):
    codes=tokens(text)
    if any(x.startswith('S') and x[1:].isdigit() for x in codes):
        return ''.join(x for x in codes if re.fullmatch(r'[SN]\d',x))
    return codes[-1] if codes else None


def canonical_unit(unit):
    s=str(unit or '').lower().replace(' ', '').replace('−','-')
    if s in ('kg/hm²','kg/hm2','kg/ha'): return 'kg/ha'
    if s in ('g/hill','ghill-1','g/hill（均值±sd）','g/hill（均值±se）'): return 'g/hill'
    if s.startswith('ph'): return 'pH'
    return s


def main():
    p=argparse.ArgumentParser();p.add_argument('--converted',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); converted=json.loads(a.converted.read_text());ref=json.loads(a.reference.read_text())
    studies={x['study_id']:x for x in ref['studies']}; checks=[]
    for r in ref['comparisons']:
        expected_a,expected_b=normalized_label(r['intervention']),normalized_label(r['comparator'])
        doi=studies[r['study_id']]['doi'].lower()
        matched=[c for c in converted['comparisons'] if c['doi'].lower()==doi and c['metric_type']=='group_values'
            and c['treatment']['label']==expected_a and c['comparator']['label']==expected_b]
        values=r.get('numeric_values',{}); matched=[c for c in matched if isinstance(values.get('intervention'),(int,float)) and isinstance(values.get('comparator'),(int,float))]
        # The same labels can occur for several outcomes; require outcome alignment.
        aliases={'grain_yield':'rice_grain_yield','Yield per hill':'rice_grain_yield'}
        matched=[c for c in matched if aliases.get(c['outcome'],c['outcome'])==r['outcome']]
        item={'reference_id':r['comparison_id'],'doi':doi,'treatment_label':expected_a,'comparator_label':expected_b}
        if not matched:
            available=[c for c in converted['comparisons'] if c['doi'].lower()==doi and c['metric_type']=='group_values'
                       and aliases.get(c['outcome'],c['outcome'])==r['outcome']]
            if not any(p['doi'].lower()==doi for p in converted['papers']):
                reason='该论文不在本次五篇历史导出关联范围。'
            elif any(c['treatment']['label']==expected_a and c['comparator']['label']!=expected_b for c in available):
                reason='相同处理的历史候选使用不同对照；不能用它替代参照的对照。'
            else:
                reason='历史候选未提供相同指标、处理和对照的成对均值；不反向填充值。'
            item.update(status='not_comparable_no_matching_candidate',reason=reason)
        elif len(matched)>1:
            item.update(status='ambiguous_multiple_matches',reason='需人工区分结果，不自动选取。')
        else:
            c=matched[0]; tests={'intervention_value':c['treatment']['value']==values['intervention'],
                'comparator_value':c['comparator']['value']==values['comparator'],
                'unit':canonical_unit(c['treatment'].get('unit'))==canonical_unit(values.get('unit'))}
            if 'intervention_error' in values: tests['intervention_error']=c['treatment'].get('error')==values['intervention_error']
            if 'comparator_error' in values: tests['comparator_error']=c['comparator'].get('error')==values['comparator_error']
            if values.get('error_type') not in (None,'unknown'):
                tests['error_type']=c['treatment'].get('error_type')==values['error_type']
            item.update(status='match' if all(tests.values()) else 'mismatch',tests=tests,comparison_id=c['comparison_id'],
                        reference_significance=r.get('significance'),candidate_significance=c.get('significance_reported'),
                        note='仅比较共同记录的数值；单位、显著性和来源冲突另行核查，不构成人工Gold。')
        checks.append(item)
    result={'method':'Reference read only after independent historical-candidate conversion.',
            'checks':checks,'matched':sum(c['status']=='match' for c in checks),
            'not_comparable':sum(c['status']=='not_comparable_no_matching_candidate' for c in checks),
            'mismatches':sum(c['status']=='mismatch' for c in checks)}
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); print(json.dumps({k:result[k] for k in ('matched','not_comparable','mismatches')}))

if __name__=='__main__':main()
