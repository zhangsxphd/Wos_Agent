import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality.literature_triage import build, classify, signals
from scripts.pipeline_utils import write_jsonl
from scripts.quality.snapshot import context


class LiteratureTriageTests(unittest.TestCase):
    def setUp(self):
        self.record={'uid':'U1','doi':'10.1234/a','title':'Rice on saline soil',
                     'abstract':'A study.','abstract_enrichment':{'provider_results':{
                         'semantic_scholar':{'status':'error','error_code':'rate_limited','http_status':429}}}}
        self.quality={'uid':'U1','doi':'10.1234/a','abstract_state':'sourced_not_individually_verified',
                      'evidence_admission':'technical_ready','issues':['provider_error_or_parse_error']}

    def test_rate_limit_is_provider_incident_not_technical_or_human_content_error(self):
        row=classify(self.record,self.quality)
        self.assertEqual(row['content_issues'],[]);self.assertEqual(row['identity_issues'],[])
        self.assertEqual(row['provider_incidents'][0]['error_code'],'rate_limited')
        self.assertEqual(row['review_decision'],'');self.assertEqual(row['human_review_status'],'pending')

    def test_missing_abstract_is_never_automatic_exclusion(self):
        quality=dict(self.quality,abstract_state='missing',evidence_admission='not_available')
        record={**self.record,'abstract':None,'title':'An uninformative title'}
        row=classify(record,quality)
        self.assertEqual(row['automatic_suggestion'],'uncertain_insufficient_scope_evidence')
        self.assertIn('abstract_missing',row['completeness_issues'])
        self.assertEqual(row['review_decision'],'')

    def test_multiple_issues_stay_on_one_uid_and_input_is_unchanged(self):
        before=copy.deepcopy(self.record)
        quality=dict(self.quality,issues=['provider_error_or_parse_error','abstract_layout_review','publication_year_outside_query_range'])
        row=classify(self.record,quality)
        self.assertEqual(row['content_issues'],['abstract_layout_review'])
        self.assertIn('publication_year_rule_review',row['scientific_review_items'])
        self.assertEqual(self.record,before)

    def test_unmatched_doi_is_rejected(self):
        with self.assertRaises(ValueError):classify(self.record,{**self.quality,'doi':'10.1234/other'})

    def test_signals_capture_sources_without_concluding_study_design(self):
        result=signals({'title':'Rice in saline soil','abstract':'Microbial communities'})
        self.assertEqual(result['rice_paddy'],['title'])
        self.assertEqual(result['microbial'],['abstract'])

    def test_report_distinguishes_request_count_from_paper_incidents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'canonical.jsonl';write_jsonl(source,[self.record])
            inventory=root/'inventory.jsonl';write_jsonl(inventory,[{'uid':'U1','doi':'10.1234/a'}])
            quality=root/'quality';quality.mkdir();write_jsonl(quality/'quality_rows.jsonl',[{**self.quality,'observed_file_sha256':{'raw_file':None,'parsed_file':None}}])
            (quality/'quality_summary.json').write_text(json.dumps({'inputs':{'canonical_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'inventory_sha256':hashlib.sha256(inventory.read_bytes()).hexdigest()},
                'snapshot_integrity':{'version':'quality-snapshot-v2','quality_rows_sha256':hashlib.sha256((quality/'quality_rows.jsonl').read_bytes()).hexdigest(),'validation_context':context()}}))
            provider=root/'provider.json';provider.write_text(json.dumps({'provider_statistics':{'semantic_scholar':{'requests':3}}}))
            first=build(source,quality,root/'out1',inventory=inventory,provider_report=provider,root=root)
            second=build(source,quality,root/'out2',inventory=inventory,provider_report=provider,root=root)
            self.assertEqual(first['paper_counts']['provider_incident'],1)
            self.assertEqual(first['provider_http_requests_from_linked_report']['semantic_scholar'],3)
            self.assertEqual(first['paper_counts']['technical_identity_or_content_review'],0)
            self.assertEqual(first['paper_counts'],second['paper_counts'])


if __name__=='__main__':unittest.main()
