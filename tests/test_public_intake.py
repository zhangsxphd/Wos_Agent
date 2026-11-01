import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_planning.connected import run
from scripts.research_planning.public_intake import public_run


class PublicIntakeTest(unittest.TestCase):
    def test_json_actor_does_not_prove_human_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); records=base/'records.jsonl'; records.write_text('')
            cfg=base/'config.json'; cfg.write_text(json.dumps({'records':str(records),'terms':['rice'],'fulltexts':[]}))
            project=base/'project'; run(project,'rice',cfg)
            review=base/'review.json'; review.write_text(json.dumps({'decisions':[{'doi':'10.1234/example','decision':'confirm','actor':'user'}]}))
            confirmation=base/'confirmation.json'; confirmation.write_text(json.dumps({'papers':[]}))
            with patch('sys.stdin.isatty',return_value=False):
                with self.assertRaisesRegex(ValueError,'actor'):
                    run(project,'rice',cfg,confirmation,review)
            self.assertEqual(json.loads((project/'run_manifest.json').read_text())['status'],'awaiting_confirmation')

    def test_frozen_public_search_resumes_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); project=base/'project'; settings=base/'settings.json'
            settings.write_text(json.dumps({'query':'rice','terms':['rice'],'inclusion_criteria':['candidate'],'rows':1}))
            raw={'message':{'total-results':1,'items':[{'DOI':'10.1234/example','title':['rice soil carbon']} ]}}
            with patch('scripts.providers.crossref_client.CrossrefClient.search_works',return_value=raw) as search:
                first=public_run(project,'rice',settings)
                second=public_run(project,'rice',settings)
                self.assertEqual(search.call_count,1)
            self.assertEqual(first['status'],'awaiting_confirmation')
            self.assertEqual(second['analysis']['status'],'interim_no_confirmed_comparisons')
            self.assertEqual(json.loads((project/'analysis/synthesis.json').read_text())['findings'],[])
