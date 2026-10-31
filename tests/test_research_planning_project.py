import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_planning.project import initialize, local_search, import_evidence, search


class ResearchProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = initialize(self.root / 'project', 'Test topic')

    def tearDown(self):
        self.temp.cleanup()

    def test_local_search_is_explicit_candidate_recall(self):
        path = self.root / 'records.jsonl'
        path.write_text('\n'.join(json.dumps(x) for x in [
            {'title': 'Water irrigation', 'uid': '1'}, {'title': 'Other', 'uid': '2'}]) + '\n')
        result = local_search(self.project, path, ['IRRIGAT'])
        self.assertEqual((result['input_count'], result['candidate_count']), (2, 1))
        row = json.loads((self.project / 'local_candidates.jsonl').read_text())
        self.assertEqual(row['screening_status'], 'unreviewed')
        with self.assertRaises(FileExistsError):
            local_search(self.project, path, ['other'])

    def test_import_is_atomic_and_requires_valid_sources(self):
        payload = self.root / 'evidence.json'
        source = dict(id='S1', title='Study', url='https://example.org', source_type='paper', access='unknown', notes='unknown')
        keys = ('id','source_id','claim','location','evidence_type','treatment','control_group','experimental_unit','replicates','soil','measurement','result','significance','limitations')
        observation = dict.fromkeys(keys, 'unknown')
        observation.update(id='E1', source_id='missing', evidence_type='reported_fact')
        payload.write_text(json.dumps({'sources': [source], 'observations': [observation]}))
        with self.assertRaises(sqlite3.IntegrityError):
            import_evidence(self.project, payload)
        with sqlite3.connect(self.project / 'evidence.sqlite') as db:
            self.assertEqual(db.execute('select count(*) from sources').fetchone()[0], 0)
        observation['source_id'] = 'S1'
        payload.write_text(json.dumps({'sources': [source], 'observations': [observation]}))
        self.assertEqual(import_evidence(self.project, payload), (1, 1))

    def test_existing_manifest_stops_search_before_network(self):
        (self.project / 'wos_search_manifest.json').write_text('{}')
        with patch('scripts.research_planning.project.run_plan') as run:
            with self.assertRaises(FileExistsError):
                search(self.project, 'plan.yaml')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
