import json
import tempfile
import unittest
from pathlib import Path

from scripts.research_planning.connected import run


class ConnectedProjectTest(unittest.TestCase):
    def test_local_candidate_resumes_without_mistaking_missing_fulltext_for_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            records = base / 'records.jsonl'
            records.write_text(json.dumps({'doi': '10.1234/example', 'title': 'Biochar and soil carbon'}) + '\n')
            config = base / 'config.json'
            config.write_text(json.dumps({'records': str(records), 'terms': ['biochar'], 'fulltexts': []}))
            project = base / 'project'
            first = run(project, 'biochar and soil carbon', config)
            self.assertEqual(first['status'], 'awaiting_confirmation')
            paper = first['papers']['10.1234/example']
            self.assertEqual(paper['screening']['status'], 'title_candidate_pending_review')
            self.assertEqual(paper['fulltext']['status'], 'not_available')
            self.assertEqual(paper['independent_gold']['status'], 'not_assessed')
            candidates = (project / 'local_candidates.jsonl').read_bytes()
            second = run(project, 'biochar and soil carbon', config)
            self.assertEqual(second['status'], 'awaiting_confirmation')
            self.assertEqual(candidates, (project / 'local_candidates.jsonl').read_bytes())
            self.assertEqual(second['wos']['status'], 'authorization_unavailable')

    def test_rejects_changed_configuration_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            records = base / 'records.jsonl'
            records.write_text('')
            config = base / 'config.json'
            config.write_text(json.dumps({'records': str(records), 'terms': ['soil'], 'fulltexts': []}))
            run(base / 'project', 'soil', config)
            config.write_text(json.dumps({'records': str(records), 'terms': ['rice'], 'fulltexts': []}))
            with self.assertRaisesRegex(ValueError, '输入配置已改变'):
                run(base / 'project', 'soil', config)
