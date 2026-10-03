"""Prepare deterministic, context-isolated request batches for a Codex worker."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.extractors.base import build_payload, payload_key
from scripts.extractors.schema_validator import EvidenceValidator, EvidenceValidationError, SCHEMA_PATH
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT, assert_safe, display_path, known_secrets, new_run_id, project_path, read_jsonl, write_json

PROMPT_PATH = ROOT / "prompts/evidence_extraction.md"
PROTOCOL_VERSION = "0.6"


def sha256_bytes(data): return hashlib.sha256(data).hexdigest()
def canonical_json(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validated_response(path, request, validator, record):
    if not path.is_file(): return False
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if any(envelope.get(k) != request.get(k) for k in ("payload_sha256", "request_sha256", "prompt_sha256", "schema_sha256", "canonical_input_sha256")):
            return False
        response = envelope.get("response")
        validator.validate(response, record)
        if response.get("screening", {}).get("status") != "maybe" or any(response.get("inference", {}).values()):
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError, EvidenceValidationError):
        return False


def prepare_batches(input_file, output_dir=None, root=ROOT, max_records_per_batch=8,
                    max_input_chars=60000, uids=None, exclude_dois=(), resume=False):
    root = Path(root).resolve()
    if max_records_per_batch < 1 or max_input_chars < 1: raise ValueError("Batch limits must be positive")
    source = project_path(input_file, root).resolve()
    records = read_jsonl(source)
    input_hash = sha256_bytes(source.read_bytes())
    prompt_hash = sha256_bytes(PROMPT_PATH.read_bytes())
    schema_hash = sha256_bytes(SCHEMA_PATH.read_bytes())
    prompt_text = PROMPT_PATH.read_text(encoding="utf-8")
    validator = EvidenceValidator()
    secrets = known_secrets(root)
    selected_uids = set(uids) if uids is not None else None
    excluded={normalize_doi(x) for x in exclude_dois if normalize_doi(x)}
    selected = []
    for record in records:
        if selected_uids is not None and record.get("uid") not in selected_uids: continue
        if normalize_doi(record.get("doi")) in excluded: continue
        if not record.get("abstract"): continue
        selected.append(record)
    if selected_uids is not None:
        missing = selected_uids - {r.get("uid") for r in records}
        if missing: raise ValueError("Requested identities missing or without abstract")
    run_id = Path(output_dir).name if output_dir else new_run_id("evidence_batch")
    run_dir = project_path(output_dir, root).resolve() if output_dir else root / "data/evidence_batches" / run_id
    if run_dir.exists() and not resume: raise FileExistsError("Batch output directory exists; use --resume")
    run_dir.mkdir(parents=True, exist_ok=True)
    batches, pending = [], []
    char_count = 0
    for record in selected:
        length = len(record.get("abstract") or "")
        if pending and (len(pending) >= max_records_per_batch or char_count + length > max_input_chars):
            batches.append(pending); pending=[]; char_count=0
        pending.append(record); char_count += length
        if len(pending) >= max_records_per_batch:
            batches.append(pending); pending=[]; char_count=0
    if pending: batches.append(pending)
    manifest_batches=[]; total_requests=0; skipped_valid=0
    for batch_index, batch_records in enumerate(batches, 1):
        batch_id=f"batch_{batch_index:03d}"
        batch_dir=run_dir/batch_id; requests_dir=batch_dir/"requests"; responses_dir=batch_dir/"responses"
        requests_dir.mkdir(parents=True, exist_ok=True); responses_dir.mkdir(parents=True, exist_ok=True)
        request_rows=[]; abstract_chars=0
        for record in batch_records:
            payload=build_payload(record); psha=payload_key(record); abstract_chars+=len(payload.get("abstract") or "")
            request={**payload,"payload_sha256":psha,"schema_version":"0.4","prompt_sha256":prompt_hash,
                     "schema_sha256":schema_hash,"canonical_input_sha256":input_hash,"batch_id":batch_id}
            request["request_sha256"]=sha256_bytes(canonical_json(request))
            request_path=requests_dir/(psha+".json")
            if not request_path.exists(): write_json(request_path,request,secrets)
            elif json.loads(request_path.read_text(encoding="utf-8")) != request:
                if not resume:
                    raise ValueError("Existing request differs; use --resume to invalidate stale prompt/schema responses")
                write_json(request_path,request,secrets,overwrite=True)
            response_path=responses_dir/(psha+".json")
            is_valid=validated_response(response_path,request,validator,record)
            if resume and is_valid: skipped_valid+=1
            request_rows.append({"uid":record.get("uid"),"doi":record.get("doi"),"payload_sha256":psha,
                                 "request_sha256":request["request_sha256"],"request_file":display_path(request_path,root),
                                 "response_file":display_path(response_path,root),"response_valid":is_valid,
                                 "status":"validated_response_reused" if is_valid else "pending"})
        batch_manifest={"batch_id":batch_id,"schema_version":"0.4","pipeline_version":"0.6",
                        "canonical_input_sha256":input_hash,"prompt_sha256":prompt_hash,"schema_sha256":schema_hash,
                        "request_count":len(request_rows),"abstract_chars":abstract_chars,"requests":request_rows}
        write_json(batch_dir/"batch_manifest.json",batch_manifest,secrets,overwrite=(batch_dir/"batch_manifest.json").exists())
        manifest_batches.append({"batch_id":batch_id,"request_count":len(request_rows),"abstract_chars":abstract_chars,
                                 "batch_manifest":display_path(batch_dir/"batch_manifest.json",root),"requests":request_rows})
        total_requests+=len(request_rows)
    worker_info={"protocol_version":PROTOCOL_VERSION,"input_count":len(records),"worker_request_count":total_requests,
                 "batch_count":len(batches),"max_records_per_batch":max_records_per_batch,"max_input_chars":max_input_chars,
                 "canonical_input":display_path(source,root),"canonical_input_sha256":input_hash,
                 "prompt_file":display_path(run_dir/"prompts/evidence_extraction.md",root),"prompt_sha256":prompt_hash,
                 "schema_file":display_path(run_dir/"schemas/evidence_matrix.schema.json",root),"schema_sha256":schema_hash,
                 "instructions":"Worker context may contain only the batch manifest, this batch's requests, prompt, and schema. Never expose benchmark gold or other papers."}
    manifest={"schema_version":1,"pipeline_version":"0.6","run_id":run_id,"status":"prepared",
              "canonical_input":display_path(source,root),"canonical_input_sha256":input_hash,
              "prompt_sha256":prompt_hash,"schema_sha256":schema_hash,"created_at":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
              "worker":worker_info,"batches":manifest_batches,"validated_responses_reused":skipped_valid,
              "excluded_dois_count":sum(normalize_doi(r.get('doi')) in excluded for r in records),
              "gold_or_previous_evidence_included":False}
    assert_safe(manifest,secrets)
    write_json(run_dir/"manifest.json",manifest,secrets,overwrite=(run_dir/"manifest.json").exists())
    prompt_copy=run_dir/"prompts/evidence_extraction.md"; prompt_copy.parent.mkdir(parents=True,exist_ok=True)
    if prompt_copy.exists() and not resume: raise FileExistsError("Prompt snapshot exists; use --resume")
    prompt_copy.write_text(prompt_text,encoding="utf-8")
    write_json(run_dir/"schemas/evidence_matrix.schema.json",json.loads(SCHEMA_PATH.read_text()),secrets,
               overwrite=(run_dir/"schemas/evidence_matrix.schema.json").exists())
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("input")
    parser.add_argument("--output-dir"); parser.add_argument("--max-records-per-batch",type=int,default=8)
    parser.add_argument("--max-input-chars",type=int,default=60000); parser.add_argument("--uids-file")
    parser.add_argument("--exclude-doi",action="append",default=[])
    parser.add_argument("--resume",action="store_true"); args=parser.parse_args()
    try:
        uids=json.loads(project_path(args.uids_file).read_text()) if args.uids_file else None
        manifest=prepare_batches(args.input,args.output_dir,max_records_per_batch=args.max_records_per_batch,
                                 max_input_chars=args.max_input_chars,uids=uids,exclude_dois=args.exclude_doi,resume=args.resume)
        print(json.dumps({"run_id":manifest['run_id'],"batches":len(manifest['batches']),
                          "requests":manifest['worker']['worker_request_count'],"directory":manifest['run_id']}))
    except (ValueError,OSError,TypeError) as exc:
        print(f"Batch preparation failed: {exc}",file=sys.stderr); return 1
    return 0


if __name__=="__main__": raise SystemExit(main())
