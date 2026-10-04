"""Offline tests for deterministic batching, blind ingestion and benchmark gates."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evidence import batch_prepare
from scripts.evidence.audit import audit_evidence
from scripts.evidence.batch_ingest import build_identity_sidecar, read_batch_responses
from scripts.evidence.benchmark import match_gold_records, _path_errors
from scripts.evidence.metrics import calculate_benchmark, finding_metrics, item_metrics, quality_gate
from scripts.extractors.base import build_payload, empty_record, payload_key
from scripts.extractors.schema_validator import EvidenceValidationError, EvidenceValidator
from scripts.pipeline_utils import write_json, write_jsonl


TEXT="A field study tested rice under saline soil using flooded irrigation. Grain yield increased by 12%."
ITERATION_0_PROMPT_SHA="67a9abde8a7867a205f5cb10d440d9ad23d5b74e4f92a76d1616fb914bbcdef2"


def record(i=1,abstract=TEXT,**changes):
    row={"uid":f"WOS:{i:03d}","doi":f"10.1000/{i}","title":f"Paper {i}","source_title":"Journal",
         "publish_year":2025,"authors":[],"author_keywords":[],"document_types":["Article"],"abstract":abstract,
         "matched_queries":["Q1"],"provenance_history":[]}
    row.update(changes); return row


def valid(row):
    result=empty_record(row); return result


class BatchProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.source=self.root/'canonical.jsonl'

    def prepare(self,rows,**kwargs):
        write_jsonl(self.source,rows)
        out=self.root/'batch'
        return batch_prepare.prepare_batches(self.source,out,root=self.root,**kwargs),out

    def test_deterministic_split_and_record_limit(self):
        rows=[record(i) for i in range(19)]; manifest,out=self.prepare(rows,max_records_per_batch=8,max_input_chars=60000)
        self.assertEqual([b['request_count'] for b in manifest['batches']],[8,8,3])
        self.assertEqual([r['uid'] for b in manifest['batches'] for r in b['requests']],[r['uid'] for r in rows])
        self.assertTrue(all(b['request_count']<=8 for b in manifest['batches']))

    def test_character_limit_and_long_single_record(self):
        rows=[record(1,abstract='a'*15),record(2,abstract='b'*15),record(3,abstract='c'*70)]
        manifest,out=self.prepare(rows,max_records_per_batch=8,max_input_chars=20)
        self.assertEqual([b['request_count'] for b in manifest['batches']],[1,1,1])
        self.assertEqual([b['abstract_chars'] for b in manifest['batches']],[15,15,70])

    def test_worker_request_whitelist_and_no_gold_or_secrets(self):
        row=record(1,api_key='sentinel-secret',previous_evidence={'crop':['rice']},gold_answer={'x':1},fulltext='secret')
        manifest,out=self.prepare([row]); req_path=self.root/manifest['batches'][0]['requests'][0]['request_file']
        request=json.loads(req_path.read_text())
        allowed=set(build_payload(row))|{'payload_sha256','schema_version','prompt_sha256','schema_sha256','canonical_input_sha256','batch_id','request_sha256'}
        self.assertEqual(set(request),allowed)
        self.assertNotIn('sentinel-secret',req_path.read_text()); self.assertFalse((out/'gold').exists())

    def test_resume_skips_validated_response(self):
        manifest,out=self.prepare([record(1)])
        item=manifest['batches'][0]['requests'][0]; request=json.loads((self.root/item['request_file']).read_text())
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        envelope.update(model_label=None,response=empty_record(record(1)))
        write_json(self.root/item['response_file'],envelope)
        resumed=batch_prepare.prepare_batches(self.source,out,root=self.root,resume=True)
        self.assertEqual(resumed['validated_responses_reused'],1)
        self.assertEqual(resumed['batches'][0]['requests'][0]['status'],'validated_response_reused')

    def test_invalid_response_not_accepted(self):
        manifest,out=self.prepare([record(1)])
        item=manifest['batches'][0]['requests'][0]; request=json.loads((self.root/item['request_file']).read_text())
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        bad=empty_record(record(1)); bad['evidence']['surprise']='invalid'
        envelope.update(response=bad,model_label=None); write_json(self.root/item['response_file'],envelope)
        resumed=batch_prepare.prepare_batches(self.source,out,root=self.root,resume=True)
        self.assertEqual(resumed['validated_responses_reused'],0)
        canonical=[record(1)]; accepted,validation,_=read_batch_responses(out,canonical,self.root)
        self.assertNotIn('WOS:001',accepted); self.assertFalse(validation['WOS:001']['accepted'])

    def test_nonempty_inference_response_rejected(self):
        rows=[record(1)]; manifest,out=self.prepare(rows); item=manifest['batches'][0]['requests'][0]
        request=json.loads((self.root/item['request_file']).read_text())
        response=empty_record(rows[0]); response['inference']['possible_gap']=[{'kind':'inference','statement':'unsupported gap','evidence_anchors':['/evidence/methods/0']}]
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        envelope.update(response=response,model_label=None); write_json(self.root/item['response_file'],envelope)
        extracted,validation,_=read_batch_responses(out,rows,self.root)
        self.assertNotIn('WOS:001',extracted)
        self.assertTrue(validation['WOS:001']['schema_valid'])
        self.assertFalse(validation['WOS:001']['response_contract_valid'])

    def test_valid_response_ingests_with_full_provenance(self):
        rows=[record(1)]; manifest,out=self.prepare(rows); item=manifest['batches'][0]['requests'][0]
        request=json.loads((self.root/item['request_file']).read_text())
        response=empty_record(rows[0]); response['screening']['status']='maybe'
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        envelope.update(response=response,model_label=None)
        write_json(self.root/item['response_file'],envelope)
        extracted,validation,audits=read_batch_responses(out,rows,self.root)
        self.assertTrue(validation['WOS:001']['accepted'])
        provenance=extracted['WOS:001']['extraction_provenance']
        self.assertEqual(provenance['worker_protocol_version'],'0.6')
        self.assertEqual(provenance['model_label'],None)
        self.assertEqual(len(provenance['request_sha256']),64)

    def test_prompt_change_invalidates_response(self):
        manifest,out=self.prepare([record(1)])
        item=manifest['batches'][0]['requests'][0]; request=json.loads((self.root/item['request_file']).read_text())
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        envelope.update(response=empty_record(record(1)),model_label=None); write_json(self.root/item['response_file'],envelope)
        prompt=self.root/'prompt.md'; prompt.write_text('changed prompt')
        with patch.object(batch_prepare,'PROMPT_PATH',prompt):
            changed=batch_prepare.prepare_batches(self.source,out,root=self.root,resume=True)
        self.assertNotEqual(changed['prompt_sha256'],manifest['prompt_sha256'])
        self.assertEqual(changed['validated_responses_reused'],0)

    def test_finding_uses_own_support_without_evidence_support_pointer(self):
        row=record(1); response=empty_record(row); span='Grain yield increased by 12%.'
        start=row['abstract'].index(span)
        response['evidence']['findings']=[{'source':'abstract','evidence_text':span,'start':start,
                                            'end':start+len(span),'claim':span,'certainty':'explicit'}]
        self.assertTrue(EvidenceValidator().validate(response,row))

    def test_finding_pointer_in_evidence_support_is_rejected_as_orphan(self):
        row=record(1); response=empty_record(row); span='Grain yield increased by 12%.'
        start=row['abstract'].index(span)
        response['evidence']['findings']=[{'source':'abstract','evidence_text':span,'start':start,
                                            'end':start+len(span),'claim':span,'certainty':'explicit'}]
        response['evidence_support']['/evidence/findings/0']={
            'source':'abstract','evidence_text':span,'start':start,'end':start+len(span)}
        with self.assertRaisesRegex(EvidenceValidationError,'missing or orphan anchors'):
            EvidenceValidator().validate(response,row)

    def test_author_interpretation_uses_own_anchor_without_evidence_support_pointer(self):
        row=record(1); response=empty_record(row); span='A field study tested rice under saline soil using flooded irrigation.'
        start=row['abstract'].index(span)
        response['evidence']['author_interpretations']=[{
            'text':span,
            'claim_type':'other','source':'abstract',
            'anchor':{'source':'abstract','evidence_text':span,'start':start,'end':start+len(span)}}]
        self.assertTrue(EvidenceValidator().validate(response,row))

    def test_author_interpretation_pointer_in_evidence_support_is_rejected_as_orphan(self):
        row=record(1); response=empty_record(row); span='A field study tested rice under saline soil using flooded irrigation.'
        start=row['abstract'].index(span)
        response['evidence']['author_interpretations']=[{
            'text':span,
            'claim_type':'other','source':'abstract',
            'anchor':{'source':'abstract','evidence_text':span,'start':start,'end':start+len(span)}}]
        response['evidence_support']['/evidence/author_interpretations/0']={
            'source':'abstract','evidence_text':span,'start':start,'end':start+len(span)}
        with self.assertRaisesRegex(EvidenceValidationError,'missing or orphan anchors'):
            EvidenceValidator().validate(response,row)

    def test_prompt_explains_self_supported_finding_contract_and_new_hash(self):
        prompt=batch_prepare.PROMPT_PATH.read_text(encoding='utf-8')
        prompt_hash=hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        self.assertIn('Do NOT create any `evidence_support` entry',prompt)
        self.assertIn('`/evidence/findings/`',prompt)
        self.assertNotEqual(prompt_hash,ITERATION_0_PROMPT_SHA)

    def test_iteration_zero_prompt_hash_response_is_stale(self):
        manifest,out=self.prepare([record(1)])
        item=manifest['batches'][0]['requests'][0]
        request=json.loads((self.root/item['request_file']).read_text())
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        envelope['prompt_sha256']=ITERATION_0_PROMPT_SHA
        envelope.update(response=empty_record(record(1)),model_label=None)
        write_json(self.root/item['response_file'],envelope)
        resumed=batch_prepare.prepare_batches(self.source,out,root=self.root,resume=True)
        self.assertNotEqual(resumed['prompt_sha256'],ITERATION_0_PROMPT_SHA)
        self.assertEqual(resumed['validated_responses_reused'],0)

    def test_schema_change_invalidates_response(self):
        manifest,out=self.prepare([record(1)])
        item=manifest['batches'][0]['requests'][0]; request=json.loads((self.root/item['request_file']).read_text())
        envelope={k:request[k] for k in ('payload_sha256','request_sha256','prompt_sha256','schema_sha256','canonical_input_sha256')}
        envelope.update(response=empty_record(record(1)),model_label=None); write_json(self.root/item['response_file'],envelope)
        schema=self.root/'schema.json'; schema.write_text('{}')
        with patch.object(batch_prepare,'SCHEMA_PATH',schema):
            changed=batch_prepare.prepare_batches(self.source,out,root=self.root,resume=True)
        self.assertNotEqual(changed['schema_sha256'],manifest['schema_sha256'])
        self.assertEqual(changed['validated_responses_reused'],0)

    def test_identity_sidecar_preserves_276_and_conflicts_skip_extraction(self):
        rows=[record(i) for i in range(276)]; rows[0]['abstract']=None; rows[1]['doi']='10.1000/conflict'
        sidecar=build_identity_sidecar(rows,{},unresolved_classification={'10.1000/1':'all_sources_no_abstract'},
                                       conflicts=['10.1000/conflict'])
        self.assertEqual(len(sidecar),276)
        self.assertEqual(sidecar[0]['pipeline_status'],'needs_fulltext')
        self.assertEqual(sidecar[1]['pipeline_status'],'needs_manual_resolution')
        self.assertEqual(sidecar[2]['pipeline_status'],'queued')

    def test_full_corpus_91_unresolved_never_enters_worker_and_sidecar_aligns(self):
        rows=[record(i) for i in range(276)]
        for i in range(88): rows[i]['abstract']=None
        conflicts=['10.1000/200','10.1000/201','10.1000/202']
        for doi in conflicts:
            next(r for r in rows if r['doi']==doi)['abstract']=None
        gold={r['uid']:empty_record(r) for r in rows[88:95]}
        unresolved={r['doi']:'all_sources_no_abstract' for r in rows[:88]}
        sidecar=build_identity_sidecar(rows,{},unresolved,gold,conflicts)
        self.assertEqual(len(sidecar),276)
        from collections import Counter
        counts=Counter(r['pipeline_status'] for r in sidecar)
        self.assertEqual(counts['needs_fulltext'],88)
        self.assertEqual(counts['needs_manual_resolution'],3)
        self.assertEqual(counts['gold_reviewed'],7)
        self.assertEqual(counts['queued'],178)
        manifest,_=self.prepare(rows,exclude_dois=conflicts)
        self.assertEqual(manifest['worker']['worker_request_count'],185)

    def test_no_abstract_and_conflict_are_not_batch_requests(self):
        rows=[record(1,abstract=None),record(2)]
        manifest,out=self.prepare(rows,uids={'WOS:001','WOS:002'})
        self.assertEqual(manifest['worker']['worker_request_count'],1)
        self.assertEqual(manifest['batches'][0]['requests'][0]['uid'],'WOS:002')

    def test_conflict_doi_excluded_from_batch_requests(self):
        rows=[record(1),record(2)]
        manifest,out=self.prepare(rows,exclude_dois=['https://doi.org/10.1000/2'])
        self.assertEqual(manifest['worker']['worker_request_count'],1)
        self.assertEqual(manifest['excluded_dois_count'],1)

    def test_canonical_input_is_immutable(self):
        rows=[record(1)]; write_jsonl(self.source,rows); before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        batch_prepare.prepare_batches(self.source,self.root/'batch',root=self.root)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),before)

    def test_gold_identity_matching_uses_uid_doi_and_title(self):
        canonical=[record(1)]; gold=[empty_record(canonical[0])]
        self.assertEqual(list(match_gold_records(canonical,gold)),['WOS:001'])
        bad=copy.deepcopy(gold[0]); bad['doi']='10.1000/wrong'
        with self.assertRaises(ValueError): match_gold_records(canonical,[bad])


class MetricAuditTests(unittest.TestCase):
    def test_normalized_precision_recall(self):
        m=item_metrics([' Rice  straw ','Biochar'],['rice straw','Gypsum'])
        self.assertEqual((m['tp'],m['fp'],m['fn']),(1,1,1))
        self.assertAlmostEqual(m['precision'],0.5); self.assertAlmostEqual(m['recall'],0.5)

    def test_finding_metrics_exact_and_overlap(self):
        gold={'evidence':{'findings':[{'evidence_text':'A grain yield increased by 12 percent.'},{'evidence_text':'Soil pH increased.'}]}}
        pred={'evidence':{'findings':[{'evidence_text':'A grain yield increased by 12 percent.'},{'evidence_text':'Soil pH increased'}]}}
        m=finding_metrics(gold,pred)
        self.assertEqual(m['exact_evidence_text_match'],1); self.assertEqual(m['overlap_match'],2)
        self.assertEqual(m['precision'],1); self.assertEqual(m['recall'],1)

    def test_quality_gate_pass_and_fail(self):
        base={'structural_validity':{'schema_valid_rate':1,'grounding_valid_rate':1,'identifier_match_rate':1,'response_contract_valid_rate':1},
              'unsupported_field_count':0,'unsupported_finding_count':0,'invalid_offset_count':0,'orphan_anchor_count':0,
              'field_micro':{'f1':0.95},'finding_metrics':{'precision':0.97,'recall':0.8},'missing_responses':0}
        self.assertEqual(quality_gate(base)['status'],'PASS')
        base['finding_metrics']['precision']=0.9
        self.assertEqual(quality_gate(base)['status'],'FAIL')

    def test_audit_support_too_short_review_future_and_association(self):
        row=record(1,abstract='Recent studies in this review suggest rice. Future studies should investigate it. Factors associated with yield are described.')
        evidence=empty_record(row)
        evidence['evidence']['treatments']['irrigation']=['rice','should investigate it','associated with yield']
        spans=['rice','should investigate it','associated with yield']
        for i,span in enumerate(spans):
            start=row['abstract'].index(span)
            evidence['evidence_support'][f'/evidence/treatments/irrigation/{i}']={'source':'abstract','evidence_text':span,'start':start,'end':start+len(span)}
        flags=audit_evidence(evidence,row)
        names={x['flag'] for x in flags}
        self.assertIn('support_too_short',names); self.assertIn('review_language',names)
        self.assertIn('future_recommendation_language',names)
        self.assertIn('association_vs_treatment_risk',names)

    def test_metrics_emit_error_taxonomy_categories(self):
        from scripts.evidence.metrics import compare_record
        gold=empty_record(record(1)); predicted=empty_record(record(1)); predicted['evidence']['study_system']['crop']=['wheat']
        fields,findings,errors=compare_record(gold,predicted,False,'offset')
        self.assertTrue(errors['false_positive_fact']); self.assertTrue(errors['wrong_crop']); self.assertTrue(errors['offset_error'])

    def test_unsupported_evidence_and_offsets_are_counted(self):
        row=record(1); predicted=empty_record(row)
        predicted['evidence']['study_system']['crop']=['wheat']
        predicted['evidence_support']['/evidence/study_system/crop/0']={'source':'abstract','evidence_text':'wheat','start':0,'end':5}
        unsupported,unsupported_findings,offsets,orphans=_path_errors(predicted,row)
        self.assertEqual(unsupported,1); self.assertEqual(unsupported_findings,0)
        self.assertEqual(offsets,1); self.assertEqual(orphans,0)


if __name__=='__main__': unittest.main()
