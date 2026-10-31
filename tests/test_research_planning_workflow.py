import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.pipeline_utils import read_jsonl, write_jsonl
from scripts.research_planning.workflow import CodexWorker, PLAN_SCHEMA, run_workflow
from scripts.wos_client import WosApiError


class FakeWos:
    api_key = 'fixture-secret-value'
    request_count = 0

    def search_documents(self, **kwargs):
        self.request_count += 1
        return {'metadata': {'total': 1, 'page': kwargs['page'], 'limit': kwargs['limit']},
                'hits': [{'uid': 'WOS:1', 'title': 'Irrigation and soil', 'types': ['Article'],
                          'source': {'publishYear': 2020}, 'identifiers': {'doi': '10.1000/example'},
                          'links': {'record': 'https://example.org/record'}}]}


class FakeWorker:
    def __init__(self, corrupt=None):
        self.corrupt = corrupt
        self.stages = []

    def ask(self, stage, payload, schema):
        self.stages.append(stage)
        if stage == 'plan':
            return {'research_question': 'Can irrigation affect soil?', 'assumptions': ['test context'],
                    'queries': [{'description': 'literature', 'query': 'TS=(irrigation)'}],
                    'novelty_queries': [{'description': 'novelty', 'query': 'TS=(soil)'}]}
        if stage == 'screen':
            return {'decisions': [{'source_id': 'S0001', 'status': 'candidate_include',
                    'reason': 'topic overlap', 'quote': 'Irrigation'}]}
        if stage == 'evidence':
            quote = 'invented result' if self.corrupt == 'quote' else 'Water increased yield.'
            return {'observations': [{'source_id': 'S0001', 'quote': quote,
                    'claim': 'Yield increased in the study.', 'limitations': 'Abstract only.'}],
                    'uncertainties': ['No full text.']}
        if stage == 'decision':
            ref = 'S9999' if self.corrupt == 'citation' else 'S0001'
            return {'synthesis': [{'text': 'A candidate observation', 'source_ids': [ref]}],
                    'contradictions': [], 'novelty_assessment': [{'text': 'Related record needs review',
                    'source_ids': ['N0001']}], 'candidate_questions': ['Does it replicate?'],
                    'experimental_design': ['Compare treatments with independent plots.'],
                    'resources_and_constraints': ['unknown'], 'limitations': ['No patent search.']}
        if stage == 'review':
            return {'critical_findings': ['Verify design.'], 'required_checks': ['Check full text.'],
                    'recommendation': 'Do not start trial yet.', 'limitations': ['Model review only.']}
        raise AssertionError(stage)


def fake_enrich(source, destination, root, **kwargs):
    rows = read_jsonl(source)
    for row in rows:
        row['abstract'] = 'Water increased yield.'
        row['abstract_source'] = 'crossref'
    write_jsonl(destination, rows)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_end_to_end_keeps_candidate_evidence_and_separate_novelty_search(self):
        worker = FakeWorker()
        project = self.root / 'project'
        with patch('scripts.research_planning.workflow.enrich_file', side_effect=fake_enrich):
            state = run_workflow(project, 'Irrigation and soil', worker, FakeWos())
        self.assertEqual(state['status'], 'candidate_pending_review')
        self.assertEqual(worker.stages, ['plan', 'screen', 'evidence', 'decision', 'review'])
        self.assertEqual(state['evidence_observations'], 1)
        self.assertTrue((project / 'literature_search.json').is_file())
        self.assertTrue((project / 'novelty_search.json').is_file())
        report = (project / 'research_report.md').read_text()
        self.assertIn('[S0001]', report)
        self.assertIn('[N0001]', report)
        self.assertIn('不得据此认定创新性', report)
        with sqlite3.connect(project / 'candidate_evidence.sqlite') as db:
            self.assertEqual(db.execute('select source_id, status from observations').fetchall(),
                             [('S0001', 'candidate_pending_review')])
        with self.assertRaises(FileExistsError):
            run_workflow(project, 'Irrigation and soil', worker, FakeWos())

    def test_rejects_invented_quotes_and_persists_failure(self):
        project = self.root / 'bad_quote'
        with patch('scripts.research_planning.workflow.enrich_file', side_effect=fake_enrich):
            with self.assertRaisesRegex(ValueError, 'quote'):
                run_workflow(project, 'Test', FakeWorker('quote'), FakeWos())
        state = json.loads((project / 'workflow_status.json').read_text())
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['stage'], 'evidence')
        self.assertFalse((project / 'research_report.md').exists())

    def test_rejects_unknown_source_citations(self):
        project = self.root / 'bad_citation'
        with patch('scripts.research_planning.workflow.enrich_file', side_effect=fake_enrich):
            with self.assertRaisesRegex(ValueError, 'citations'):
                run_workflow(project, 'Test', FakeWorker('citation'), FakeWos())
        self.assertEqual(json.loads((project / 'workflow_status.json').read_text())['stage'], 'decision')
        self.assertFalse((project / 'research_report.md').exists())

    def test_missing_abstract_preserves_title_grounded_decision(self):
        project = self.root / 'missing_abstract'
        class MissingWorker(FakeWorker):
            def ask(self, stage, payload, schema):
                result = super().ask(stage, payload, schema)
                if stage == 'decision': result['synthesis'] = []
                return result
        run_workflow(project, 'Test', MissingWorker(), FakeWos(), enrich=False)
        decision = json.loads((project / 'screening.json').read_text())['decisions'][0]
        self.assertEqual(decision['status'], 'candidate_include')
        self.assertEqual(decision['model_decision']['status'], 'candidate_include')
        self.assertEqual(decision['information_sufficiency'], 'title_only')

    def test_failed_search_is_recorded_without_creating_a_report(self):
        class FailedWos(FakeWos):
            def search_documents(self, **kwargs):
                raise WosApiError('fixture network failure')
        project = self.root / 'failed_search'
        with self.assertRaisesRegex(RuntimeError, 'search incomplete'):
            run_workflow(project, 'Test', FakeWorker(), FailedWos())
        state = json.loads((project / 'workflow_status.json').read_text())
        self.assertEqual((state['status'], state['stage']), ('failed', 'literature_search'))
        self.assertFalse((project / 'research_report.md').exists())

    def test_model_adapter_uses_readonly_session_without_api_credentials(self):
        def fake_run(command, **kwargs):
            self.assertIn('read-only', command)
            self.assertIn('--ephemeral', command)
            self.assertNotIn('WOS_STARTER_API_KEY', kwargs['env'])
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            self.assertIn('untrusted data', kwargs['input'])
            output = Path(command[command.index('--output-last-message') + 1])
            output.write_text(json.dumps(FakeWorker().ask('plan', {}, PLAN_SCHEMA)))
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.dict(os.environ, {'WOS_STARTER_API_KEY': 'secret', 'OPENAI_API_KEY': 'secret', 'GH_TOKEN': 'secret'}), \
             patch('scripts.research_planning.workflow.subprocess.run', side_effect=fake_run):
            result = CodexWorker().ask('plan', {'topic': 'test'}, PLAN_SCHEMA)
        self.assertEqual(result['queries'][0]['query'], 'TS=(irrigation)')

    def test_reuses_existing_search_without_network_and_marks_novelty_unknown(self):
        source = self.root / 'existing.jsonl'
        source.write_text(json.dumps({'uid': 'WOS:1', 'doi': '10.1000/example',
                                      'title': 'Irrigation and soil', 'abstract': 'Water increased yield.'}) + '\n')
        manifest = self.root / 'existing.manifest.json'
        manifest.write_text(json.dumps({'unique_records': 1, 'searches': [{'truncated': False}]}))
        class ReuseWorker(FakeWorker):
            def ask(self, stage, payload, schema):
                result = super().ask(stage, payload, schema)
                if stage == 'decision':
                    result['novelty_assessment'] = []
                return result
        project = self.root / 'reuse'
        state = run_workflow(project, 'Irrigation and soil', ReuseWorker(), None,
                             existing_records=source, existing_manifest=manifest, skip_novelty=True)
        self.assertEqual(state['status'], 'candidate_pending_review')
        self.assertTrue(state['novelty_truncated'])
        self.assertEqual(json.loads((project / 'novelty_search.json').read_text())['status'], 'not_run')
        self.assertEqual(json.loads((project / 'literature_search.json').read_text())['reuse']['network_calls'], 0)

    def test_reuse_rejects_manifest_count_mismatch(self):
        source = self.root / 'existing.jsonl'
        source.write_text('{}\n')
        manifest = self.root / 'existing.manifest.json'
        manifest.write_text(json.dumps({'unique_records': 2, 'searches': []}))
        with self.assertRaisesRegex(ValueError, 'count differs'):
            run_workflow(self.root / 'bad_reuse', 'Test', FakeWorker(), None,
                         existing_records=source, existing_manifest=manifest, skip_novelty=True)

    def test_model_usage_limit_is_reported_instead_of_generic_login_error(self):
        failure = subprocess.CompletedProcess(['codex'], 1, '', 'ERROR: You’ve hit your usage limit.')
        with patch('scripts.research_planning.workflow.subprocess.run', return_value=failure):
            with self.assertRaisesRegex(RuntimeError, 'usage limit reached'):
                CodexWorker().ask('plan', {'topic': 'test'}, PLAN_SCHEMA)

    def test_cached_abstract_requires_nonempty_matching_doi(self):
        source = self.root / 'source.jsonl'
        source.write_text(json.dumps({'uid': 'WOS:1', 'doi': None, 'title': 'Irrigation and soil'}) + '\n')
        pool = self.root / 'pool.jsonl'
        pool.write_text(json.dumps({'uid': 'WOS:1', 'doi': None, 'abstract': 'Water increased yield.'}) + '\n')
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'unique_records': 1, 'searches': [{'truncated': False}]}))
        class NoAbstractWorker(FakeWorker):
            def ask(self, stage, payload, schema):
                result = super().ask(stage, payload, schema)
                if stage == 'decision':
                    result['synthesis'] = []
                    result['novelty_assessment'] = []
                return result
        project = self.root / 'missing_doi'
        run_workflow(project, 'Test', NoAbstractWorker(), None, existing_records=source,
                     existing_manifest=manifest, existing_pool=pool, skip_novelty=True)
        self.assertIsNone(json.loads((project / 'sources.json').read_text())[0]['abstract'])
        self.assertEqual(json.loads((project / 'screening.json').read_text())['decisions'][0]['status'], 'candidate_include')


if __name__ == '__main__':
    unittest.main()
