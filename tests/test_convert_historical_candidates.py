import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('candidate_conversion',ROOT/'scripts/research_planning/convert_historical_candidates.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def field(name,value): return {'field':name,'value':value,'evidence':[{'page':4}],'status':'candidate_pending_review'}


def paper():
    fs=[field('独立试验重复','作者报告每处理3个独立盆'),field('处理','ISCN、ISCW、IFCN、IFCW；IS为浅湿灌、IF为淹灌，CN无藻、CW加藻。'),
        field('主要结果','ISCW 9.11 ± 0.25 c，对 IFCW 13.25 ± 0.72 a，单位 g/穴；均值±SD，n=3。')]
    return {'doi':'10.1234/test','experiment':m.experiment('10.1234/test',{f['field']:f for f in fs}),'raw_fields':fs}


class CandidateConversionTests(unittest.TestCase):
    def test_named_values_and_replicates_are_derived_from_candidate(self):
        p=paper();r={'doi':p['doi'],'index':0,'indicator':'grain_yield','treatment':'see comparison',
            'comparator':'ISCW vs IFCW','value':{'ISCW':9.11,'IFCW':13.25},'unit':'g/hill',
            'significance':'c vs a','evidence':{'physical_page':14,'excerpt':'ISCW IFCW'}}
        items,fail=m.parse_numeric_result(r,p);self.assertFalse(fail);c=items[0]
        self.assertEqual(c['treatment']['value'],9.11);self.assertEqual(c['comparator']['error'],0.72)
        self.assertEqual(c['treatment']['levels'][0]['description'],'浅湿灌')
        self.assertEqual(c['experimental_unit']['value'],'pot');self.assertEqual(c['independent_replicates']['value'],3)
        self.assertEqual(c['record_state'],'candidate_not_human_gold')
        r['value']['ISCW']=9.12
        changed,_=m.parse_numeric_result(r,p)
        self.assertEqual(changed[0]['treatment']['value'],9.12)
        self.assertIsNone(changed[0]['treatment']['error'])

    def test_unmatched_labels_rejected_and_means_not_invented(self):
        p=paper();r={'doi':p['doi'],'index':1,'indicator':'test','treatment':'A;B','comparator':'C',
                     'value':'2 ± 1 versus 1 ± 0.1','unit':'g','evidence':[]}
        rows,why=m.parse_numeric_result(r,p);self.assertEqual(rows,[]);self.assertTrue(why)
        r.update(treatment='A',value='+2.5',comparator='C',unit='%')
        rows,_=m.parse_numeric_result(r,p);self.assertEqual(rows[0]['metric_type'],'reported_relative_effect')
        self.assertIsNone(rows[0]['treatment']['value']);self.assertIsNone(rows[0]['comparator']['value'])

    def test_group_mean_rows_order_and_quote_coverage(self):
        p=paper();r={'doi':p['doi'],'index':5,'indicator':'Yield per hill','treatment':'S2N2; S2N3',
                     'comparator':'S1N1','value':'18.6 ± 4.8; 20.5 ± 2.1 versus 10.4 ± 0.3',
                     'unit':'g/hill','significance':'p<0.05',
                     'evidence':[{'quote':'S2N2 18.6 ± 4.8 a','row':'S2N2; S2N3; S1N1','footnote':'standard error'}]}
        out,why=m.parse_numeric_result(r,p);self.assertFalse(why);self.assertEqual(len(out),2)
        self.assertEqual(out[1]['treatment']['label'],'S2N3');self.assertEqual(out[1]['comparator']['value'],10.4)
        self.assertEqual(out[0]['treatment']['error_type'],'SE')
        self.assertTrue(any('引句未完整' in x for x in out[0]['warnings']))
        self.assertTrue(any('多个因素' in x for x in out[0]['warnings']))

    def test_letters_and_field_groups_are_not_guessed(self):
        f=field('试验重复','每处理3块小区');design=field('设计处理','CK旱地；W水田；BW水田施肥。')
        p={'doi':'10.1234/f','experiment':m.experiment('10.1234/f',{x['field']:x for x in (f,design)})}
        data=field('新核查 · 真菌多样性','ACE：W 10±1 a、BW 9±1 ab、CK 5±1 b。均值±SD。')
        out=m.parse_field_pairs(p,data);self.assertEqual(len(out),2)
        self.assertEqual(out[1]['significance_reported'],'letters_overlap_no_significant_difference_reported')
        self.assertEqual(out[1]['treatment']['levels'][0]['code'],'BW')
        data['value']='ACE：W 10±1 a、BW 9±1 ab。';self.assertEqual(m.parse_field_pairs(p,data),[])

    def test_pasted_response_requires_hash_and_recovers_source_table(self):
        text=('| T1 | Ditch water | 沟水 | p3 |\n| CK | River water | 河水 | p3 |\n'
              '每个处理重复5次 Each treatment was repeated five times\n'
              '### 6.5 水稻产量\n| CK | 100 ± 5 | a | 对照 |\n| T1 | 90 ± 4 | b | 10% |\n')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'answer.txt').write_text(text);h=hashlib.sha256((root/'answer.txt').read_bytes()).hexdigest()
            (root/'review.json').write_text(json.dumps({'user_pasted_response_sha256':h}))
            p=paper();p['pdf_sha256']='sourcehash';p['raw_fields']=[]
            origin={'historic_response_file':'answer.txt','source_file':'review.json'}
            out,err,_=m.parse_historic_response(p,origin,root)
            self.assertFalse(err);self.assertEqual(out[0]['treatment']['value'],90)
            self.assertEqual(out[0]['independent_replicates']['value'],5)
            (root/'answer.txt').write_text(text+'modified')
            out,err,_=m.parse_historic_response(p,origin,root);self.assertFalse(out);self.assertTrue(err)

    def test_source_location_is_parsed_not_invented(self):
        text=('| T1 | Ditch water | 沟水 | p3 |\n| CK | River water | 河水 | p3 |\n'
              '### 6.5 水稻产量\nPDF第27页，Table 19，Yield (kg/hm²)\n'
              '| CK | 100 ± 5 | a | 对照 |\n| T1 | 90 ± 4 | b | 10% |\n')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'answer.txt').write_text(text)
            h=hashlib.sha256((root/'answer.txt').read_bytes()).hexdigest()
            (root/'review.json').write_text(json.dumps({'user_pasted_response_sha256':h}))
            p=paper();p['pdf_sha256']='hash';p['raw_fields']=[]
            result,err,_=m.parse_historic_response(p,{'historic_response_file':'answer.txt','source_file':'review.json'},root)
            self.assertFalse(err)
            self.assertEqual(result[0]['evidence']['location']['program_parsed']['physical_page'],27)
            self.assertEqual(result[0]['evidence']['location']['program_parsed']['table'],'Table 19')
            self.assertIsNone(result[0]['evidence']['location']['pdf_verified'])

    def test_two_studies_convert_without_any_reference_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            d1='10.3390/su18052185';d2='10.3390/agriculture16161786'
            papers=[{'doi':d1,'raw_sha256':'hash1'}, {'doi':d2,'raw_sha256':'hash2'}]
            f1=field('独立试验重复','作者报告每处理3个独立盆');f1['doi']=d1
            f2=field('independent_replicates','Three biological soil columns per factorial combination');f2['doi']=d2
            r1={'doi':d1,'index':0,'indicator':'grain_yield','treatment':'see comparison','comparator':'A vs B',
                'value':{'A':9,'B':3},'unit':'g/hill','evidence':{'physical_page':2}}
            r2={'doi':d2,'index':0,'indicator':'Yield per hill','treatment':'X','comparator':'Y',
                'value':'12 ± 2 versus 4 ± 1','unit':'g/hill','evidence':[{'quote':'X Y','footnote':'standard error'}]}
            for name,rows in {'papers':papers,'fields':[f1,f2],'results':[r1,r2],
                              'issues':[],'historic_claims':[],'supplement_files':[]}.items():
                (root/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
            result=m.run(root)
            self.assertEqual(len(result['comparisons']),2)
            self.assertEqual({c['doi'] for c in result['comparisons']},{d1,d2})
            self.assertEqual(result['comparisons'][0]['treatment']['value'],9)
            self.assertFalse((root/'checked_evidence.json').exists())
            r1['value']['A']=19
            (root/'results.jsonl').write_text(json.dumps(r1)+'\n'+json.dumps(r2)+'\n')
            self.assertEqual(m.run(root)['comparisons'][0]['treatment']['value'],19)

    def test_coverage_pointer_is_not_promoted_to_design_fact(self):
        pointer={'field':'study_object','value':'地点、品种与条件，PDF 第2—3页',
                 'evidence':{'record_metadata':{'review_status':'已覆盖'}}}
        ex=m.experiment('10.1234/x',{'study_object':pointer})
        self.assertIsNone(ex['research_object_candidate'])

    def test_nitrogen_reported_effect_keeps_period_and_significance_unknown(self):
        p=paper(); f=field('主要结果','作者报告N200与N160、N240相比，N2O排放低34.7%和78.9%；具体数值及测量误差须查表。')
        rows=m.parse_nitrogen_fields(p,f)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['outcome'],'n2o_unspecified_period')
        self.assertEqual(rows[1]['comparator']['label'],'N240')
        self.assertEqual(rows[1]['reported_effect']['value'],-78.9)
        self.assertEqual(rows[0]['significance_reported'],'unknown')
        self.assertIsNone(rows[0]['experiment_id'])

    def test_salinity_response_uses_reference_not_formal_control(self):
        text=('| L | light | 1 | 2 | 3 |\n| M | moderate | 2 | 3 | 4 |\n| H | heavy | 3 | 4 | 5 |\n'
              '多项结果以L处理为比较对象\n| 试验容器重复 | 4个/处理 |\n'
              '### 6.1 温室气体累积排放量\n来源：PDF第22页 Figure 7。\n'
              '| 累积N₂O排放量（mg m⁻²） | 1ᵃ | 2ᵃᵇ | 3ᵇ |\n')
        p=paper();p['pdf_sha256']='hash'
        out,err,_=m.parse_salinity_response(p,text,'old.txt','sha',{'not_push_github':True})
        self.assertFalse(err);self.assertEqual(len(out),2)
        self.assertEqual(out[0]['comparator_role'],'descriptive_reference_not_author_control')
        self.assertEqual(out[0]['independent_replicates']['value'],4)
        self.assertEqual(out[0]['evidence']['sharing_restriction'],'not_push_github')
        self.assertIsNone(out[0]['evidence']['location']['pdf_verified'])

    def test_three_comparability_levels(self):
        a={'outcome':'grain_yield','comparison_id':'A','treatment':{'unit':'g/hill'}};b={'outcome':'Yield per hill','comparison_id':'B','treatment':{'unit':'g/hill'}}
        result=m.comparability(a,b);self.assertEqual(result['side_by_side'],'allowed_with_conditions')
        self.assertIn('requires',result['quantitative_effect_comparison'])
        self.assertIn('not_permitted',result['statistical_pooling'])
        b['outcome']='soil_ph';self.assertEqual(m.comparability(a,b)['side_by_side'],'separate_indicators_only')

if __name__=='__main__':unittest.main()
