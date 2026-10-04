"""Build a portable, blind worker bundle from one prepared batch."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_bundle(batch_dir: Path, output_dir: Path, iteration: int | None = None) -> Path:
    """Copy one batch and its prompt/schema into a self-contained portable folder."""
    batch_dir = batch_dir.resolve()
    output_dir = output_dir.resolve()
    parent = batch_dir.parent
    batch = _json(batch_dir / "batch_manifest.json")
    run_manifest_path = parent / "manifest.json"
    run_manifest = _json(run_manifest_path) if run_manifest_path.is_file() else {}
    iteration = run_manifest.get("prompt_iteration", 1) if iteration is None else iteration
    if iteration not in (1, 2, 3):
        raise ValueError("Only prompt iterations 1 through 3 are supported by this worker bundle")

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty bundle: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("requests", "responses", "prompt", "schema"):
        (output_dir / name).mkdir(exist_ok=True)

    prompt_source = parent / "prompts" / "evidence_extraction.md"
    schema_source = parent / "schemas" / "evidence_matrix.schema.json"
    if not prompt_source.is_file() or not schema_source.is_file():
        raise FileNotFoundError("The batch parent must contain prompts/ and schemas/ files")
    shutil.copyfile(prompt_source, output_dir / "prompt" / prompt_source.name)
    shutil.copyfile(schema_source, output_dir / "schema" / schema_source.name)

    requests = []
    for item in batch.get("requests", []):
        source = batch_dir / "requests" / Path(item["request_file"]).name
        request = _json(source)
        name = f"{request['payload_sha256']}.json"
        shutil.copyfile(source, output_dir / "requests" / name)
        requests.append({
            "uid": item["uid"],
            "doi": item.get("doi"),
            "payload_sha256": item["payload_sha256"],
            "request_sha256": item["request_sha256"],
            "request_file": f"requests/{name}",
            "response_file": f"responses/{name}",
        })

    manifest = {
        "batch_id": batch["batch_id"],
        "schema_version": batch["schema_version"],
        "pipeline_version": batch["pipeline_version"],
        "canonical_input_sha256": batch["canonical_input_sha256"],
        "prompt_sha256": batch["prompt_sha256"],
        "schema_sha256": batch["schema_sha256"],
        "request_count": len(requests),
        "abstract_chars": batch.get("abstract_chars"),
        "requests": requests,
    }
    _write_json(output_dir / "batch_manifest.json", manifest)
    _write_json(output_dir / "run_manifest.json", {
        "run_id": run_manifest.get("run_id"),
        "canonical_input_sha256": run_manifest.get("canonical_input_sha256", batch["canonical_input_sha256"]),
        "prompt_sha256": batch["prompt_sha256"],
        "schema_sha256": batch["schema_sha256"],
        "prompt_iteration": iteration,
    })
    (output_dir / "WORKER_INSTRUCTIONS.md").write_text(_instructions(iteration), encoding="utf-8")
    (output_dir / "TASK.md").write_text(_task(iteration, len(requests)), encoding="utf-8")
    (output_dir / "validate_worker_outputs.py").write_text(_checker(iteration, len(requests)), encoding="utf-8")
    (output_dir / "validate_worker_outputs.py").chmod(0o755)
    return output_dir


def _instructions(iteration: int = 1) -> str:
    return f"""# Blind Worker Instructions

You are a blind scientific evidence extraction worker. This is prompt iteration {iteration}.

Use this bundle directory as the entire available workspace. Do not access parent directories, host paths, Git repositories, Gold benchmark files, previous Evidence, user profiles, the web, external databases, memory, or any other papers. Treat each request independently. Do not use outside knowledge.

Allowed inputs: `batch_manifest.json`, `requests/*.json`, `prompt/evidence_extraction.md`, and `schema/evidence_matrix.schema.json`. Allowed outputs: `responses/*.json` and the supplied local checker. Do not alter requests, prompt, schema, or manifest.

Follow the prompt and schema exactly. Findings and author interpretations are self-supported. Never add `/evidence/findings/` or `/evidence/author_interpretations/` entries to `evidence_support`. All inference arrays must be empty; `screening.status` must be `maybe`. Do not run a benchmark or look for Gold.
"""


def _task(iteration: int = 1, request_count: int = 7) -> str:
    return f"""# Iteration {iteration} worker task

Process every request listed in `batch_manifest.json`. For each request, independently read only that request, follow `prompt/evidence_extraction.md`, and produce a schema-conforming response envelope. Copy `payload_sha256`, `request_sha256`, `prompt_sha256`, `schema_sha256`, and `canonical_input_sha256` exactly from the request; use `model_label: null` unless you can reliably identify your model. Write exactly one file to the corresponding `responses/<payload_sha256>.json` path.

Before reporting success, run `python3 ./validate_worker_outputs.py`. Do not claim success unless it prints the full success marker. If it fails, report only the listed UID, DOI, and validation stage, then stop. Do not inspect Gold, benchmark, previous Evidence, or any file outside this bundle. After successful validation, report exactly `BLIND_WORKER_ITER{iteration}_SUCCESS requests={request_count} responses={request_count} protocol_valid={request_count} inference_empty={request_count}` and exit.
"""


def _checker(iteration: int = 1, request_count: int = 7) -> str:
    return r'''#!/usr/bin/env python3
"""Portable integrity/preflight and response validator for this worker bundle."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

WORKER_ROOT = Path.cwd().resolve()
INFERENCE = ("mechanistic_interpretation", "connection_to_user_research", "possible_gap", "transferable_idea", "needs_fulltext_for")
FORBIDDEN_PATHS = (chr(47) + "workspace", chr(47) + "Users" + chr(47))
SECRET_PATTERNS = (re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b"), re.compile(r"\bs2k-[A-Za-z0-9]{20,}\b"), re.compile(r"(?i)\b(?:api[_-]?key|apikey)\s*[:=]\s*[A-Za-z0-9_-]{16,}"))

def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(obj): return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
def read_json(path): return json.loads(path.read_text(encoding="utf-8"))
def leaves(value, prefix="/evidence"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("findings", "author_interpretations"): continue
            yield from leaves(child, prefix + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, child in enumerate(value): yield from leaves(child, prefix + "/" + str(index))
    elif value not in (None, "", "unknown"):
        yield prefix, value
def quote_ok(span, abstract):
    text = span.get("evidence_text")
    start, end = span.get("start"), span.get("end")
    return span.get("source") == "abstract" and isinstance(text, str) and bool(text) and isinstance(start, int) and isinstance(end, int) and 0 <= start < end and abstract[start:end] == text
def fail(stage):
    print("INVALID stage=" + stage)
    return 1
def integrity(manifest, preflight):
    prompt = WORKER_ROOT / "prompt/evidence_extraction.md"
    schema_path = WORKER_ROOT / "schema/evidence_matrix.schema.json"
    request_dir, response_dir = WORKER_ROOT / "requests", WORKER_ROOT / "responses"
    run_manifest_path = WORKER_ROOT / "run_manifest.json"
    if not all(p.is_file() for p in (WORKER_ROOT / "batch_manifest.json", run_manifest_path, prompt, schema_path)) or not request_dir.is_dir() or not response_dir.is_dir(): return "bundle_structure"
    if digest(prompt.read_bytes()) != manifest.get("prompt_sha256") or digest(schema_path.read_bytes()) != manifest.get("schema_sha256"): return "prompt_or_schema_hash"
    run_manifest = read_json(run_manifest_path)
    if any(run_manifest.get(k) != manifest.get(k) for k in ("prompt_sha256", "schema_sha256", "canonical_input_sha256")): return "run_manifest_hashes"
    requests = manifest.get("requests", [])
    responses = list(response_dir.glob("*.json"))
    if len(requests) != manifest.get("request_count") or len(requests) != EXPECTED_REQUEST_COUNT: return "request_count"
    if preflight and responses: return "preflight_responses_not_empty"
    if not preflight and len(responses) != len(requests): return "response_count"
    checked_files = [WORKER_ROOT / "batch_manifest.json", prompt, schema_path, run_manifest_path, WORKER_ROOT / "TASK.md", WORKER_ROOT / "WORKER_INSTRUCTIONS.md", *request_dir.glob("*.json")]
    if not preflight: checked_files.extend(responses)
    for path in checked_files:
        try: content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError): return "bundle_file_read"
        if any(token in content for token in FORBIDDEN_PATHS) or any(pattern.search(content) for pattern in SECRET_PATTERNS): return "forbidden_path_or_secret"
    seen = set()
    for item in requests:
        req_path = WORKER_ROOT / item.get("request_file", "")
        if not req_path.is_file() or req_path.parent != request_dir: return "request_file"
        req = read_json(req_path)
        if req_path.name != str(req.get("payload_sha256")) + ".json" or req_path.name in seen: return "request_identity"
        seen.add(req_path.name)
        request_hash = req.get("request_sha256")
        unsigned = dict(req); unsigned.pop("request_sha256", None)
        if digest(canonical(unsigned)) != request_hash or request_hash != item.get("request_sha256"): return "request_hash"
        payload = {k: req.get(k) for k in ("uid", "doi", "title", "journal", "year", "authors", "keywords", "document_types", "abstract")}
        if digest(canonical(payload)) != req.get("payload_sha256") or req.get("payload_sha256") != item.get("payload_sha256"): return "payload_hash"
        if any(req.get(k) != manifest.get(k) for k in ("prompt_sha256", "schema_sha256", "canonical_input_sha256")): return "manifest_hashes"
    if len(list(request_dir.glob("*.json"))) != len(requests): return "extra_request_files"
    return None
def preflight():
    manifest = read_json(WORKER_ROOT / "batch_manifest.json")
    problem = integrity(manifest, True)
    if problem: return fail(problem)
    print("BLIND_WORKER_PREFLIGHT_OK requests=REQUEST_COUNT responses=0")
    return 0
def validate():
    manifest = read_json(WORKER_ROOT / "batch_manifest.json")
    problem = integrity(manifest, False)
    if problem: return fail(problem)
    schema = read_json(WORKER_ROOT / "schema/evidence_matrix.schema.json")
    validator = Draft202012Validator(schema)
    for item in manifest["requests"]:
        req = read_json(WORKER_ROOT / item["request_file"])
        response_path = WORKER_ROOT / item["response_file"]
        if response_path.parent != WORKER_ROOT / "responses" or response_path.name != req["payload_sha256"] + ".json" or not response_path.is_file(): return fail("response_file")
        env = read_json(response_path)
        for key in ("payload_sha256", "request_sha256", "prompt_sha256", "schema_sha256", "canonical_input_sha256"):
            if env.get(key) != req.get(key): return fail("envelope_hashes")
        response = env.get("response")
        if list(validator.iter_errors(response)): return fail("json_schema")
        if any(response.get(k) != req.get(k) for k in ("uid", "doi", "title")): return fail("identifier")
        if response.get("screening", {}).get("status") != "maybe" or any(response.get("inference", {}).get(k) != [] for k in INFERENCE): return fail("response_contract")
        support = response.get("evidence_support", {})
        if any(p.startswith("/evidence/findings/") or p.startswith("/evidence/author_interpretations/") for p in support): return fail("forbidden_self_supported_pointer")
        expected = {pointer for pointer, _ in leaves(response.get("evidence", {}))}
        if set(support) != expected: return fail("missing_or_orphan_support")
        abstract = req.get("abstract") or ""
        for pointer, value in leaves(response.get("evidence", {})):
            span = support[pointer]
            if not quote_ok(span, abstract): return fail("factual_grounding_or_offsets")
            if pointer.endswith("/experimental_scale"):
                scale = {"field": r"\bfield (?:study|experiment|soil column experiment|trial)\b|\bfield-based\b|\bin situ monitoring\b", "pot": r"\bpot (?:experiment|study|trial)s?\b", "greenhouse": r"\bgreenhouse\b", "lab": r"\blaboratory\b|\blab (?:experiment|study|trial)\b", "model": r"\bmodels?\b|\bmodelling\b|\bmodeling\b"}
                if value not in scale or not re.search(scale[value], span["evidence_text"], re.I): return fail("factual_grounding_or_offsets")
            elif str(value).casefold() not in span["evidence_text"].casefold(): return fail("factual_grounding_or_offsets")
        for finding in response.get("evidence", {}).get("findings", []):
            if not quote_ok(finding, abstract) or finding.get("claim") not in finding.get("evidence_text", ""): return fail("finding_grounding_or_offsets")
        for interpretation in response.get("evidence", {}).get("author_interpretations", []):
            anchor = interpretation.get("anchor", {})
            if interpretation.get("source") != "abstract" or not quote_ok(anchor, abstract) or interpretation.get("text") not in anchor.get("evidence_text", ""): return fail("author_interpretation_grounding")
    print("BLIND_WORKER_ITER1_SUCCESS requests=REQUEST_COUNT responses=REQUEST_COUNT protocol_valid=REQUEST_COUNT inference_empty=REQUEST_COUNT")
    return 0
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true", help="verify bundle integrity before any response exists")
    args = parser.parse_args()
    return preflight() if args.preflight else validate()
if __name__ == "__main__": raise SystemExit(main())
'''.replace("EXPECTED_REQUEST_COUNT", str(request_count)).replace("REQUEST_COUNT", str(request_count)).replace(
        "BLIND_WORKER_ITER1_SUCCESS", f"BLIND_WORKER_ITER{iteration}_SUCCESS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True, help="Prepared batch directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="New or empty destination bundle directory")
    parser.add_argument("--iteration", type=int, choices=(1, 2, 3), help="Prompt iteration recorded in the bundle")
    args = parser.parse_args()
    result = build_bundle(args.batch_dir, args.output_dir, args.iteration)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
