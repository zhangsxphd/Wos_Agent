"""Prepare iteration 2 using only the seven manifested development requests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.evidence.contract_reconcile_v061a import DEV_IDS, DEV_REQUEST_MANIFEST, ROOT, _load_development_requests
from scripts.evidence.batch_prepare import canonical_json
from scripts.extractors.schema_validator import SCHEMA_PATH


DEFAULT_OUTPUT = Path("data/evidence_batches/v06_gold_blind_iteration2_20261004")
PROMPT_PATH = Path("prompts/evidence_extraction.md")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload(request: dict) -> dict:
    return {key: request.get(key) for key in (
        "uid", "doi", "title", "journal", "year", "authors", "keywords", "document_types", "abstract")}


def prepare(root: Path = ROOT, output_dir: Path = DEFAULT_OUTPUT, iteration: int = 2) -> dict:
    root = Path(root).resolve()
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    if iteration not in (2, 3):
        raise ValueError("Only development prompt iterations 2 and 3 can be prepared here")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite iteration-{iteration} batch: {output_dir}")

    expected = set(json.loads((root / DEV_IDS).read_text(encoding="utf-8")))
    source_requests = _load_development_requests(root, expected)
    source_manifest_path = root / DEV_REQUEST_MANIFEST
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if set(source_requests) != expected or len(source_requests) != 7:
        raise ValueError("Iteration 2 is restricted to the exact seven-paper development identity set")

    prompt_bytes = (root / PROMPT_PATH).read_bytes()
    schema_bytes = SCHEMA_PATH.read_bytes()
    prompt_hash, schema_hash = _sha(prompt_bytes), _sha(schema_bytes)
    source_payloads = [_payload(source_requests[uid]) for uid in sorted(expected)]
    dev_input_hash = _sha(canonical_json(source_payloads))
    batch_id = "batch_001"
    batch_dir = output_dir / batch_id
    requests_dir = batch_dir / "requests"
    responses_dir = batch_dir / "responses"
    requests_dir.mkdir(parents=True)
    responses_dir.mkdir()
    request_rows = []
    abstract_chars = 0
    for uid in sorted(expected):
        payload = _payload(source_requests[uid])
        abstract_chars += len(payload.get("abstract") or "")
        payload_hash = _sha(canonical_json(payload))
        request = {
            **payload,
            "payload_sha256": payload_hash,
            "schema_version": "0.4",
            "prompt_sha256": prompt_hash,
            "schema_sha256": schema_hash,
            "canonical_input_sha256": dev_input_hash,
            "batch_id": batch_id,
        }
        request["request_sha256"] = _sha(canonical_json(request))
        filename = f"{payload_hash}.json"
        (requests_dir / filename).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rel_request = (batch_dir / "requests" / filename).relative_to(root).as_posix()
        rel_response = (batch_dir / "responses" / filename).relative_to(root).as_posix()
        request_rows.append({
            "uid": uid, "doi": payload["doi"], "payload_sha256": payload_hash,
            "request_sha256": request["request_sha256"], "request_file": rel_request,
            "response_file": rel_response, "response_valid": False, "status": "pending",
        })

    (output_dir / "prompts").mkdir(parents=True)
    (output_dir / "schemas").mkdir()
    (output_dir / "prompts/evidence_extraction.md").write_bytes(prompt_bytes)
    (output_dir / "schemas/evidence_matrix.schema.json").write_bytes(schema_bytes)
    batch_manifest = {
        "batch_id": batch_id, "schema_version": "0.4", "pipeline_version": "0.6",
        "canonical_input_sha256": dev_input_hash, "prompt_sha256": prompt_hash,
        "schema_sha256": schema_hash, "request_count": 7,
        "abstract_chars": abstract_chars, "requests": request_rows,
    }
    (batch_dir / "batch_manifest.json").write_text(json.dumps(batch_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_manifest = {
        "schema_version": 1, "pipeline_version": "0.6", "run_id": output_dir.name,
        "status": "prepared", "canonical_input": "development_request_payloads_only",
        "canonical_input_sha256": dev_input_hash, "prompt_sha256": prompt_hash,
        "schema_sha256": schema_hash, "prompt_iteration": iteration,
        "development_request_manifest_sha256": _sha(source_manifest_path.read_bytes()),
        "worker": {"worker_request_count": 7, "batch_count": 1,
                   "input_scope": "seven development request payloads only",
                   "gold_or_previous_evidence_included": False},
        "batches": [{"batch_id": batch_id, "request_count": 7,
                     "abstract_chars": abstract_chars,
                     "batch_manifest": (batch_dir / "batch_manifest.json").relative_to(root).as_posix(),
                     "requests": request_rows}],
        "gold_or_previous_evidence_included": False,
    }
    (output_dir / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {"output_dir": str(output_dir), "request_count": 7,
              "prompt_sha256": prompt_hash, "schema_sha256": schema_hash,
              "development_input_sha256": dev_input_hash,
              "source_manifest_sha256": run_manifest["development_request_manifest_sha256"],
        "holdout_loaded": False, "iteration": iteration}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iteration", type=int, choices=(2, 3), default=2)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.output_dir, args.iteration), ensure_ascii=False))


if __name__ == "__main__":
    main()
