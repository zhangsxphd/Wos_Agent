import copy
import importlib.util
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('direct_synthesis',ROOT/'scripts/research_planning/evidence_synthesis.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
PROFILE={'topic':'盐碱稻田灌溉和温室气体','primary_question':'灌溉对温室气体?',
         'primary_eligibility':['原始盐碱稻田水分对照，CH4或N2O']}


def example(doi, tx, ctrl, factor='nitrogen', outcome='n2o', exp=None):
    experiment={'experiment_id':exp,'design_candidate':'盐碱稻田土柱试验','research_object_candidate':'盐土水稻',
                'experimental_unit':{'value':'soil_column','status':'candidate'},
                'independent_replicates':{'value':3,'status':'candidate'},'sampling_replicates':{'value':None}}
    paper={'doi':doi,'title':'saline paddy rice experiment','source_url':'https://doi.org/'+doi,'experiment':experiment}
    if factor=='nitrogen':
        a={'label':'N200','value':tx,'unit':'mg/m²','error':2,'error_type':'SD','levels':[]}
        b={'label':'N160','value':ctrl,'unit':'mg/m²','error':3,'error_type':'SD','levels':[]}
    else:
        a={'label':'ISCW','value':tx,'unit':'mg/m²','levels':[{'code':'IS','description':'浅湿灌'},{'code':'CW','description':'加藻'}]}
        b={'label':'IFCW','value':ctrl,'unit':'mg/m²','levels':[{'code':'IF','description':'淹灌'},{'code':'CW','description':'加藻'}]}
    c={'doi':doi,'experiment_id':exp,'comparison_id':doi+':'+str(tx),'outcome':outcome,'treatment':a,'comparator':b,
       'metric_type':'group_values','experimental_unit':experiment['experimental_unit'],
       'independent_replicates':experiment['independent_replicates'],'significance_reported':'not significant',
       'evidence':{'source_file':'candidate','location':{'model_reported':'p7 table2','program_parsed':{'physical_page':7},'pdf_verified':None}},
       'record_state':'candidate_not_human_gold','warnings':[]}
    return paper,c


class DirectSynthesisTests(unittest.TestCase):
    def test_real_interface_has_no_reference_and_preserves_raw_fields(self):
        p,c=example('10.1/a',7,10);result=m.adapt_converted({'papers':[p],'comparisons':[c]},PROFILE)
        got=result['comparisons'][0]
        self.assertEqual(got['numeric_values']['intervention'],7)
        self.assertEqual(got['numeric_values']['comparator'],10)
        self.assertEqual(got['numeric_values']['intervention_error_type'],'SD')
        self.assertEqual(got['independent_replicates']['value'],3)
        self.assertEqual(got['record_state'],'candidate_not_human_gold')
        self.assertIsNone(got['experiment_id']); self.assertEqual(got['dataset_identity'],'unknown')
        loc=got['raw_evidence'][0]
        self.assertEqual(loc['program_parsed']['physical_page'],7);self.assertIsNone(loc['pdf_verified'])

    def test_two_studies_same_direction_does_not_make_a_finding(self):
        p1,c1=example('10.1/a',7,10);p2,c2=example('10.1/b',6,9)
        result=m.synthesize_converted(m.adapt_converted({'papers':[p1,p2],'comparisons':[c1,c2]},PROFILE))
        self.assertEqual(result['findings'],[]);self.assertEqual(len(result['pairwise_comparability']),1)
        pair=result['pairwise_comparability'][0]
        self.assertEqual(pair['independence'],'unknown');self.assertEqual(pair['B_conditional_quantitative'],'not_established')
        self.assertEqual(pair['C_meta_analysis'],'not_implemented')

    def test_conditions_not_scale_alone_control_quantitative_comparison(self):
        p1,c1=example('10.1/a',7,10);p2,c2=example('10.1/b',6,9)
        ctx={k:'matched' for k in ('estimand','outcome_definition','treatment_definition','comparator_definition','time_basis','unit')}
        ctx['source_status']='source_checked';c1['comparison_context']=ctx;c2['comparison_context']=copy.deepcopy(ctx)
        p2['experiment']['design_candidate']='盐碱稻田田间小区试验'
        result=m.synthesize_converted(m.adapt_converted({'papers':[p1,p2],'comparisons':[c1,c2]},PROFILE))
        self.assertEqual(result['pairwise_comparability'][0]['B_conditional_quantitative'],'possible_pending_source_and_independence_review')
        c2['comparison_context']['comparator_definition']='different'
        result=m.synthesize_converted(m.adapt_converted({'papers':[p1,p2],'comparisons':[c1,c2]},PROFILE))
        self.assertEqual(result['pairwise_comparability'][0]['B_conditional_quantitative'],'not_established')

    def test_same_doi_can_contain_two_named_experiments(self):
        p,c1=example('10.1/a',7,10,exp='e1');_,c2=example('10.1/a',6,9,exp='e2')
        result=m.synthesize_converted(m.adapt_converted({'papers':[p],'comparisons':[c1,c2]},PROFILE))
        self.assertEqual(len(result['pairwise_comparability']),1)
        self.assertEqual(result['pairwise_comparability'][0]['independence'],'unknown')
        c1['experiment_id']=None;c2['experiment_id']=None
        result=m.synthesize_converted(m.adapt_converted({'papers':[p],'comparisons':[c1,c2]},PROFILE))
        self.assertEqual(result['pairwise_comparability'],[])

    def test_shared_controls_and_reported_effect_are_not_independent(self):
        p,c1=example('10.1/a',7,10);_,c2=example('10.1/a',6,10)
        c2['metric_type']='reported_relative_effect'
        result=m.synthesize_converted(m.adapt_converted({'papers':[p],'comparisons':[c1,c2]},PROFILE))
        self.assertEqual(len(result['dependent_comparisons']),2)
        self.assertEqual(result['independent_experiment_count'],'unknown')

    def test_pdf_check_requires_matching_hash_and_never_upgrades_candidate(self):
        p,c=example('10.1/a',7,10);p['pdf_sha256']='known'
        n=m.adapt_converted({'papers':[p],'comparisons':[c]},PROFILE)
        checks={'checks':[{'doi':p['doi'],'pdf_sha256':'known','outcomes':['n2o'],
                           'status':'assistant_pdf_visual_review_not_human_gold','pages':[3],'scope':'table only'}]}
        m.apply_source_checks(n,checks)
        self.assertEqual(n['comparisons'][0]['raw_evidence'][0]['pdf_verified']['pages'],[3])
        self.assertEqual(n['comparisons'][0]['record_state'],'candidate_not_human_gold')
        checks['checks'][0]['pdf_sha256']='different'
        with self.assertRaises(ValueError):m.apply_source_checks(n,checks)

    def test_nitrogen_and_water_are_separate_scopes(self):
        p1,c1=example('10.1/a',7,10,'nitrogen');p2,c2=example('10.1/b',6,9,'water')
        n=m.adapt_converted({'papers':[p1,p2],'comparisons':[c1,c2]},PROFILE)
        self.assertEqual([c['factor'] for c in n['comparisons']],['nitrogen_input','water_regime'])
        self.assertEqual([c['eligibility'] for c in n['comparisons']],['indirect_context','primary_candidate_requires_source_review'])
        pair=m.synthesize_converted(n)['pairwise_comparability'][0]
        self.assertEqual(pair['B_conditional_quantitative'],'not_established')

if __name__=='__main__':unittest.main()
