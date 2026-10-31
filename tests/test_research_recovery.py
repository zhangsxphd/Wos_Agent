import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_planning.recovery import recover_screening, continue_with_confirmed
from scripts.research_planning.workflow import _validate_screen, CodexWorker


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.run = self.root/'run'; self.out = self.root/'out'; self.run.mkdir()
        self.sources = [{'source_id':'S0001','uid':'WOS:1','doi':'10.1/one','title':'Rice irrigation','abstract':None},
                        {'source_id':'S0002','uid':'WOS:2','doi':'10.1/two','title':'Other rice','abstract':None}]
        put(self.run/'sources.json', self.sources)
        self.records=self.root/'records.jsonl';self.records.write_text('{}\n')
        self.pool=self.root/'pool.jsonl';self.pool.write_text('{}\n')
        self.fulltext=self.root/'fulltext.jsonl';self.fulltext.write_text('{}\n')
        put(self.run/'literature_search.json', {'reuse':{'sha256':hashlib.sha256(self.records.read_bytes()).hexdigest()}})
        put(self.run/'workflow_status.json', {'topic':'topic','status':'failed'})
        for i, source in enumerate(self.sources, 1):
            folder=self.run/'model_calls'/f'{i:03d}_screen'
            put(folder/'input.json', {'topic':'topic','sources':[source]});put(folder/'schema.json', {})
            put(folder/'call.json', {'returncode':0 if i == 1 else 1,'stderr':'user\nscreen prompt\nINPUT DATA:\n' + ('usage limit' if i==2 else '')})
            if i==1:put(folder/'response.txt', {'decisions':[{'source_id':'S0001','status':'candidate_include','reason':'relevant','quote':'Rice'}]})
        put(self.run/'screening_with_manual_fallback.json', {'decisions':[{'source_id':'S0002','status':'uncertain','reason':'failed','decision_origin':'fallback'}]})
        self.audit={s['source_id']:{'fulltext':{'admission':'not_available'}} for s in self.sources}
        self.args=(self.run,self.out,self.root,self.records,self.pool,self.fulltext)

    def tearDown(self): self.temp.cleanup()

    def test_title_grounded_judgment_and_indirect_role_are_separate(self):
        source={'source_id':'S','title':'盐碱稻田秸秆','abstract':None}
        row={'source_id':'S','status':'candidate_exclude','reason':'秸秆和氮肥，未比较灌溉','quote':'秸秆'}
        _validate_screen([row],[source])
        self.assertEqual(row['model_decision']['status'],'candidate_exclude')
        self.assertEqual(row['status'],'uncertain')
        self.assertEqual(row['evidence_use'],'agronomic_context')
        self.assertEqual(row['information_sufficiency'],'title_only')

    def test_rice_mechanism_is_retained_but_clearly_unrelated_can_be_excluded(self):
        sources=[{'source_id':'A','title':'Methane production in paddy soil','abstract':None},
                 {'source_id':'B','title':'Cotton yield under saline water','abstract':None}]
        rows=[{'source_id':'A','status':'candidate_exclude','reason':'未体现盐碱灌溉','quote':'Methane'},
              {'source_id':'B','status':'candidate_exclude','reason':'明确为棉花，非稻田','quote':'Cotton'}]
        _validate_screen(rows,sources,'盐碱稻田灌溉')
        self.assertEqual((rows[0]['status'],rows[0]['evidence_use']),('uncertain','mechanism_transfer'))
        self.assertEqual(rows[1]['status'],'candidate_exclude')

    def test_available_water_yield_study_is_not_discarded_for_lacking_gas(self):
        source={'source_id':'A','title':'Rice irrigation','abstract':'Some abstract',
                'fulltext':{'admission':'technical_pass'}}
        row={'source_id':'A','status':'candidate_exclude',
             'reason':'盐土水稻全量与亏缺灌溉比较，报告产量，未报告温室气体', 'quote':'Rice'}
        _validate_screen([row],[source],'盐碱稻田灌溉')
        self.assertEqual(row['model_decision']['status'],'candidate_exclude')
        self.assertEqual((row['status'],row['evidence_use']),('uncertain','agronomic_context'))
        self.assertEqual(row['next_action'],'prioritize_existing_fulltext')

    def test_resume_failure_is_saved_and_only_missing_id_retried(self):
        class Worker:
            def __init__(self, fail):self.fail=fail;self.ids=[]
            def ask(self,stage,payload,schema):
                self.ids.extend(s['source_id'] for s in payload['sources'])
                if self.fail:raise RuntimeError('usage limit')
                return {'decisions':[{'source_id':'S0002','status':'candidate_exclude','reason':'out of scope','quote':'Other'}]}
        first=Worker(True)
        with patch('scripts.research_planning.recovery._source_audit',return_value=self.audit) as audited:
            cp=recover_screening(*self.args,retry_missing=True,worker=first)
            self.assertEqual(cp['pending_model_ids'],['S0002']);self.assertEqual(first.ids,['S0002'])
            self.assertEqual(cp['pending_fulltext_count'],2)
            second=Worker(False);cp=recover_screening(*self.args,retry_missing=True,worker=second)
            self.assertEqual(second.ids,['S0002']);self.assertEqual(cp['pending_model_ids'],[])
            third=Worker(False);recover_screening(*self.args,retry_missing=True,worker=third)
            self.assertEqual(third.ids,[]);self.assertEqual(audited.call_count,1)
        rows=json.loads((self.out/'screening_recovered.json').read_text())['decisions']
        self.assertEqual(rows[0]['decision_origin'],'historical_model')
        self.assertEqual(rows[1]['decision_origin'],'retry_model')

    def test_resume_rejects_changed_inputs(self):
        with patch('scripts.research_planning.recovery._source_audit',return_value=self.audit):
            recover_screening(*self.args)
            self.pool.write_text('changed')
            with self.assertRaisesRegex(ValueError,'inputs changed'):recover_screening(*self.args)

    def test_worker_numbers_continue_after_failure(self):
        folder=self.root/'audit';(folder/'001_screen').mkdir(parents=True)
        self.assertEqual(CodexWorker(audit_dir=folder).call_number,1)

    def test_limited_analysis_can_continue_with_pending_screen_and_is_idempotent(self):
        evidence=self.root/'evidence.json'
        fields={'outcome':'rice_grain_yield','intervention':'AWD','comparator':'CF','result':'1 vs 2',
                'unit':'t/ha','significance':'unknown','observation_period':'season',
                'source_location':'Table 1','limitations':'different designs','verification_status':'source_checked'}
        put(evidence,{'studies':[{'study_id':'A','source_url':'https://example.org/a','source_access':'fulltext'},
                                 {'study_id':'B','source_url':'https://example.org/b','source_access':'fulltext'}],
                      'comparisons':[dict(fields,comparison_id='a',study_id='A'),dict(fields,comparison_id='b',study_id='B')],
                      'cross_study_comparisons':[{'title':'Yield','comparison_ids':['a','b'],'interpretation':'Limited.',
                                                  'comparability_limitations':'Different contexts.'}],
                      'limitations':['Incomplete.']})
        with patch('scripts.research_planning.recovery._source_audit',return_value=self.audit):
            recover_screening(*self.args)
        receipt=continue_with_confirmed(self.out,evidence)
        self.assertEqual(receipt['pending_model_ids'],['S0002'])
        self.assertEqual(receipt['cross_study_comparisons'],1)
        self.assertEqual(continue_with_confirmed(self.out,evidence),receipt)
        (self.out/'limited_synthesis.md').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'changed'):continue_with_confirmed(self.out,evidence)

    def test_different_outcomes_cannot_be_automatically_grouped(self):
        with patch('scripts.research_planning.recovery._source_audit',return_value=self.audit):
            recover_screening(*self.args)
        fields={'intervention':'A','comparator':'B','result':'1','unit':'x','significance':'unknown',
                'observation_period':'season','source_location':'p1','limitations':'limited','verification_status':'source_checked'}
        file=self.root/'bad.json';put(file,{'studies':[{'study_id':'A','source_url':'https://example.org/a','source_access':'yes'},
                                                       {'study_id':'B','source_url':'https://example.org/b','source_access':'yes'}],
            'comparisons':[dict(fields,comparison_id='a',study_id='A',outcome='yield'),dict(fields,comparison_id='b',study_id='B',outcome='n2o')],
            'cross_study_comparisons':[{'title':'bad','comparison_ids':['a','b'],'interpretation':'bad','comparability_limitations':'bad'}], 'limitations':[]})
        with self.assertRaisesRegex(ValueError,'one common outcome'):continue_with_confirmed(self.out,file)

if __name__ == '__main__': unittest.main()
