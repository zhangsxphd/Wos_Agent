"""Validate Codex batch responses and build an identity-preserving sidecar."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if not __package__: sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from scripts.evidence.audit import audit_evidence
from scripts.evidence.batch_prepare import sha256_bytes, canonical_json
from scripts.extractors.base import build_payload, empty_record, payload_key
from scripts.extractors.schema_validator import EvidenceValidator, EvidenceValidationError, SCHEMA_PATH
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT, assert_safe, display_path, known_secrets, project_path, read_jsonl, write_jsonl, write_json


def read_batch_responses(batch_dir, canonical_records, root=ROOT):
    root=Path(root).resolve(); batch_dir=project_path(batch_dir,root)
    manifest=json.loads((batch_dir/'manifest.json').read_text(encoding='utf-8'))
    records_by_uid={r.get('uid'):r for r in canonical_records}; results={}; validation={}; audits={}
    validator=EvidenceValidator(); expected_schema=sha256_bytes(SCHEMA_PATH.read_bytes())
    for batch in manifest['batches']:
        bm_path=project_path(batch['batch_manifest'],root)
        bm=json.loads(bm_path.read_text(encoding='utf-8'))
        for request_meta in bm['requests']:
            uid=request_meta['uid']; record=records_by_uid.get(uid)
            response_path=project_path(request_meta['response_file'],root)
            request_path=project_path(request_meta['request_file'],root)
            val={"schema_valid":False,"grounding_valid":False,"identifier_match":False,"accepted":False,
                 "response_contract_valid":False,
                 "unsupported_field_count":0,"unsupported_finding_count":0,"invalid_offset_count":0,"orphan_anchor_count":0,
                 "response_present":response_path.is_file(),"response_file":display_path(response_path,root)}
            if record is None:
                validation[uid]=val; continue
            if not response_path.is_file(): validation[uid]=val; continue
            try:
                request=json.loads(request_path.read_text(encoding='utf-8'))
                request_digest=request.pop('request_sha256',None)
                if request_digest!=request_meta['request_sha256'] or sha256_bytes(canonical_json(request))!=request_digest:
                    raise ValueError('request_hash_mismatch')
                if request_meta['payload_sha256']!=payload_key(record) or any(request.get(k)!=v for k,v in build_payload(record).items()):
                    raise ValueError('canonical_payload_mismatch')
                envelope=json.loads(response_path.read_text(encoding='utf-8'))
                assert_safe(envelope,known_secrets(root))
                if envelope.get('payload_sha256')!=request_meta['payload_sha256'] or envelope.get('request_sha256')!=request_meta['request_sha256']:
                    raise ValueError('request_hash_mismatch')
                if envelope.get('prompt_sha256')!=manifest['prompt_sha256'] or envelope.get('schema_sha256')!=expected_schema or envelope.get('canonical_input_sha256')!=manifest['canonical_input_sha256']:
                    raise ValueError('stale_worker_response')
                response=envelope.get('response')
                val['schema_valid']=validator.validator.is_valid(response)
                if not val['schema_valid']: raise EvidenceValidationError('schema_error')
                inference=response.get('inference',{})
                val['response_contract_valid']=(response.get('screening',{}).get('status')=='maybe' and
                                                all(not values for values in inference.values()))
                if not val['response_contract_valid']: raise EvidenceValidationError('response_contract_error')
                val['identifier_match']=all(response.get(k)==record.get(k) for k in ('uid','doi','title'))
                if not val['identifier_match']: raise EvidenceValidationError('identifier_mismatch')
                validator.validate(response,record); val['grounding_valid']=True; val['accepted']=True
                normalized=validator.normalize(response,record)
                metadata={"worker":"codex","worker_protocol_version":"0.6","batch_id":batch['batch_id'],
                          "request_sha256":request_meta['request_sha256'],"payload_sha256":request_meta['payload_sha256'],
                          "response_sha256":sha256_bytes(response_path.read_bytes()),"prompt_sha256":manifest['prompt_sha256'],
                          "schema_sha256":manifest['schema_sha256'],"canonical_input_sha256":manifest['canonical_input_sha256'],
                          "processed_at":datetime.now(timezone.utc).isoformat(),"model_label":envelope.get('model_label')}
                results[uid]={"status":"extracted","evidence":normalized,"extraction_provenance":metadata,
                              "response_file":display_path(response_path,root)}
                audits[uid]=audit_evidence(normalized,record)
            except EvidenceValidationError as exc:
                val['error_kind']='offset' if 'offset' in str(exc).casefold() else 'schema'
            except (OSError,ValueError,TypeError,KeyError):
                val['error_kind']='schema'
            validation[uid]=val
    return results,validation,audits


def build_identity_sidecar(canonical_records, extracted, unresolved_classification=None, gold_evidence=None,
                           conflicts=(), rejected_uids=(), root=ROOT):
    unresolved_classification=unresolved_classification or {}; gold_evidence=gold_evidence or {}
    conflicts={normalize_doi(x) for x in conflicts}; rejected_uids=set(rejected_uids); rows=[]
    for record in canonical_records:
        uid=record.get('uid'); doi=normalize_doi(record.get('doi'))
        if doi in conflicts:
            status='needs_manual_resolution'; content=empty_record(record,'needs_fulltext','Abstract sources conflict; manual abstract resolution is required.')
            availability='conflict'; provenance={"pipeline_version":"0.6","source":"canonical conflict inventory"}
        elif uid in extracted:
            status='extracted'; content=extracted[uid]['evidence']; availability='abstract_available'; provenance=extracted[uid]['extraction_provenance']
        elif uid in rejected_uids:
            status='rejected'; content=empty_record(record,'needs_fulltext','Worker response failed validation; manual review required.')
            availability='abstract_available'; provenance={"pipeline_version":"0.6","worker":"codex","model_label":None}
        elif uid in gold_evidence and record.get('abstract'):
            status='gold_reviewed'; content=gold_evidence[uid]; availability='abstract_available'
            provenance={"pipeline_version":"0.6","worker":"human_reviewed_v0.4_gold","model_label":None}
        elif not record.get('abstract'):
            availability=unresolved_classification.get(doi,'all_sources_no_abstract')
            status='needs_fulltext'; content=empty_record(record,'needs_fulltext','No canonical abstract available; no extraction performed.')
            provenance={"pipeline_version":"0.6","worker":"none","model_label":None}
        else:
            status='queued'; content=empty_record(record)
            provenance={"pipeline_version":"0.6","worker":"none","model_label":None}
            availability='abstract_available'
        rows.append({"uid":uid,"doi":record.get('doi'),"title":record.get('title'),"pipeline_status":status,
                     "source_availability_status":availability,"evidence_record":content,"extraction_provenance":provenance})
    return rows


def ingest(batch_dir, canonical_input, output, root=ROOT, unresolved_file=None, gold_evidence_file=None, conflicts=()):
    root=Path(root).resolve(); source=project_path(canonical_input,root); target=project_path(output,root)
    if target.exists(): raise FileExistsError('Evidence sidecar output exists; choose a new path')
    records=read_jsonl(source); extracted,validation,audits=read_batch_responses(batch_dir,records,root)
    unresolved={}
    if unresolved_file:
        body=json.loads(project_path(unresolved_file,root).read_text())
        unresolved={x.get('doi'):x.get('reason') for x in body.get('records',[])}
    gold={}
    if gold_evidence_file:
        gold={x.get('uid'):x for x in read_jsonl(project_path(gold_evidence_file,root))
              if x.get('screening',{}).get('status')=='maybe'}
    rejected={uid for uid,value in validation.items() if value.get('response_present') and not value.get('accepted')}
    sidecar=build_identity_sidecar(records,extracted,unresolved,gold,conflicts,rejected,root)
    secrets=known_secrets(root); assert_safe(sidecar,secrets); write_jsonl(target,sidecar,secrets)
    manifest={"pipeline_version":"0.6","schema_version":"0.4","canonical_input":display_path(source,root),
              "canonical_input_sha256":sha256_bytes(source.read_bytes()),"sidecar":display_path(target,root),
              "identities":len(sidecar),"statuses":{s:sum(r['pipeline_status']==s for r in sidecar) for s in
              ('extracted','gold_reviewed','needs_fulltext','needs_manual_resolution','queued','rejected')},
              "accepted_worker_responses":len(extracted),"rejected_worker_responses":len(rejected),
              "validation_results":validation,"audit_flags":audits}
    write_json(target.with_suffix('.manifest.json'),manifest,secrets)
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('batch_dir');p.add_argument('--canonical-input',required=True);p.add_argument('--output',required=True)
    p.add_argument('--unresolved-file');p.add_argument('--gold-evidence-file');p.add_argument('--conflict-doi',action='append',default=[]);a=p.parse_args()
    try:
        m=ingest(a.batch_dir,a.canonical_input,a.output,unresolved_file=a.unresolved_file,gold_evidence_file=a.gold_evidence_file,conflicts=a.conflict_doi)
        print(json.dumps({"sidecar":m['sidecar'],"identities":m['identities'],"statuses":m['statuses']}))
    except (ValueError,OSError,TypeError,KeyError) as exc: print(f'Batch ingest failed: {exc}',file=sys.stderr); return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
