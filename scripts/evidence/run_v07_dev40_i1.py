"""Prepare, validate, and compare prompt iteration 1 on Dev40 only.

The worker bundle is deliberately self-contained. Corpus abstracts are decoded
only for UIDs in the frozen Dev40 registry; Gold is read only by the evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator
from scripts.evidence.freeze_v07_datasets import metadata_only_row
from scripts.evidence.build_v07_dev40_gold_bundle import raw_top_level_values
from scripts.evidence.metrics import KEY_FIELDS, calculate_benchmark
from scripts.evidence.metrics_v2 import corpus_metrics_v2, finding_metrics_v2
from scripts.extractors.base import INFERENCE_FIELDS, build_payload
from scripts.extractors.schema_validator_v2 import EvidenceValidator, factual_leaves

PROMPT_SHA256 = "9c8a8200c09a2ac83d99f5222823f529b54a459c9b7abca78428bf715a68c86c"
HISTORICAL_PROMPT_SHA256 = "f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1"
CONTRACT_V21_SHA256 = "75fc4b301b598250d159de99c916959999297ecaf512a23a914f74991ae1ffad"
FROZEN_INPUT_HASHES = {
    "schema": "33e289f1b2911024f00748ac2580ab7d2926369e7f4ac2ef48b28da8573dae36",
    "validator_v2": "cfb9877271f948541c2a7fd2596d1aac0c347402106a99cc435f6c24fc041680",
    "metric_v1": "4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a",
    "metric_v2": "54262802b4970429c1a218272145e229f44b9553f0dc5ba7cfceb070dfad4720",
    "contract_v1": "9823606637e1c61a19546b720d052368cbe7a9c85825a1ce2ae41c3194208dab",
}
CORPUS = ROOT / "data/processed/saline_paddy_v056_full_enriched_20261003.jsonl"
IDENTITIES = ROOT / "data/evidence_benchmarks/v07_dev40/identities.json"
GOLD = ROOT / "data/evidence_benchmarks/v07_dev40_gold_final/candidate_gold.jsonl"
PROMPT = ROOT / "prompts/evidence_extraction_v07_i1.md"
SCHEMA = ROOT / "schemas/evidence_matrix.schema.json"
OUTPUT = ROOT / "data/evidence_batches/v07_dev40_i1"
REPORTS = ROOT / "data/reports"
REQUIRED_FIELDS = tuple(KEY_FIELDS)


def sha_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def audit_prompt() -> dict:
    """Audit the iteration prompt against the frozen base and allowed scope."""
    historical = ROOT / "prompts/evidence_extraction.md"
    prompt_bytes = PROMPT.read_bytes()
    historical_bytes = historical.read_bytes()
    if sha_file(historical) != HISTORICAL_PROMPT_SHA256:
        raise ValueError("historical_prompt_hash_changed")
    text = prompt_bytes.decode("utf-8")
    old = historical_bytes.decode("utf-8")
    # The complete self-contained base remains; only the allowed soil/salinity/
    # scale examples in that base may be generalized for the no-record-examples rule.
    allowed_example_edits = {
        "A named model such as\n  HYDRUS-3D or logistic regression is a method, not by itself a study scale.":
            "A named model is a method, not by itself a study scale.",
        "stated specificity (for example, saline-affected paddy fields and\n  saline-affected upland fields); do not replace them with a broader paraphrase\n  such as `saline-affected farmland`.":
            "stated specificity; do not replace a specific supported descriptor with a\n  broader paraphrase.",
        "do not treat a generic application sentence such as a material\n  being used `in saline–alkali soils` as review scope.":
            "do not treat a generic application statement as review scope.",
    }
    for before, after in allowed_example_edits.items():
        if before not in old or after not in text:
            raise ValueError("allowed_example_generalization_missing")
    normalized_old = old
    for before, after in allowed_example_edits.items():
        normalized_old = normalized_old.replace(before, after)
    if not text.startswith(normalized_old):
        raise ValueError("prompt_changed_outside_allowed_base_edits")
    start = "- Findings:"
    end = "- Reviews:"
    def section(source: str) -> str:
        a = source.index(start)
        b = source.index(end, a)
        return source[a:b]
    if section(text) != section(old):
        raise ValueError("findings_prompt_rule_changed")
    addition = text[len(normalized_old):]
    if "Prompt Iteration 1 field-routing additions" not in addition:
        raise ValueError("iteration1_additions_missing")
    if any(token in addition for token in ("WOS:", "10.", "uid:", "doi:")):
        raise ValueError("record_specific_identity_found_in_prompt_addition")
    if any(token.casefold() in addition.casefold() for token in (
        "gold answer", "historical prediction", "holdout abstract", "holdout gold",
        "saline-affected paddy fields", "saline-affected upland fields", "HYDRUS-3D")):
        raise ValueError("paper_specific_or_prohibited_example_found_in_addition")
    frozen = {"contract_v21": sha_file(ROOT / "docs/evidence_field_contract_v2_1.md"),
              "validator_v2": sha_file(ROOT / "scripts/extractors/schema_validator_v2.py"),
              "schema": sha_file(SCHEMA),
              "metric_v1": sha_file(ROOT / "scripts/evidence/metrics.py"),
              "metric_v2": sha_file(ROOT / "scripts/evidence/metrics_v2.py"),
              "gold": sha_file(GOLD)}
    expected = {"contract_v21": CONTRACT_V21_SHA256, **FROZEN_INPUT_HASHES,
                "gold": "4879b4103f6a8620e028679e9a5d1dc2321057e53f7bdf57b9ca1c4f908308b7"}
    expected.pop("contract_v1", None)
    if frozen != expected:
        raise ValueError("frozen_contract_validator_schema_metric_or_gold_changed")
    return {"historical_prompt_sha256": HISTORICAL_PROMPT_SHA256,
            "iteration1_prompt_sha256": sha_bytes(prompt_bytes),
            "findings_rule_unchanged": True, "contract_v21_unchanged": True,
            "validator_v2_unchanged": True, "schema_unchanged": True,
            "metric_v1_unchanged": True, "metric_v2_unchanged": True,
            "dev40_gold_unchanged": True, "future_holdout_accessed": False,
            "record_specific_prompt_examples": False}


def audit_gold_provenance() -> dict:
    manifest_path = ROOT / "data/evidence_benchmarks/v07_dev40_gold_final/gold_freeze_manifest.json"
    decision_path = ROOT / "data/evidence_benchmarks/v07_dev40_gold_final/human_decision_log.jsonl"
    frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
    decisions = read_jsonl(decision_path)
    r01_r18 = [row for row in decisions if row.get("decision") == "HUMAN_ADJUDICATED_R01_R18"]
    r19 = [row for row in decisions if row.get("field") == "study_system.experimental_scale"
           and row.get("decision") == "ACCEPT"]
    if not (frozen.get("gold_status") == "human_approved_development_gold"
            and frozen.get("human_review_complete") is True
            and frozen.get("r01_r18_human_adjudicated") is True
            and frozen.get("r19_scale_human_adjudicated") is True
            and frozen.get("unresolved_count") == 0
            and len(r01_r18) == 21 and len(r19) == 6
            and all(row.get("human_reviewed") is True and row.get("approved_by") == "human"
                    for row in decisions)):
        raise ValueError("dev40_human_review_provenance_not_confirmed")
    return {"gold_status": frozen["gold_status"], "decision_rows": len(decisions),
            "r01_r18_adjudications": len(r01_r18), "r19_scale_adjudications": len(r19),
            "all_rows_human_approved": True, "unresolved_count": 0,
            "gold_content_modified": False}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_response_hashes(batch_dir: Path, known_hashes: dict[str, str] | None = None) -> dict[str, str]:
    current = {}
    manifest = json.loads((Path(batch_dir) / "batch_manifest.json").read_text(encoding="utf-8"))
    for request in manifest["requests"]:
        path = Path(batch_dir) / request["response_file"]
        if path.is_file():
            current[request["uid"]] = sha_file(path)
    if known_hashes is not None and current != known_hashes:
        raise ValueError("worker_response_sha256_changed")
    return current


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def dev_uids(path: Path = IDENTITIES) -> set[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("identities", data.get("uids"))
    rows = [r if isinstance(r, dict) else {"uid": str(r)} for r in data]
    ids = [str(r["uid"]) for r in rows]
    if len(ids) != 40 or len(set(ids)) != 40:
        raise ValueError("dev40_registry_must_contain_40_unique_uids")
    return set(ids)


def extract_dev_inputs(corpus: Path, uids: set[str]) -> dict[str, dict]:
    """Decode abstract/authors/keywords only after metadata identifies a Dev40 UID."""
    selected = {}
    wanted = {"abstract", "authors", "author_keywords"}
    with Path(corpus).open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            metadata, has_abstract = metadata_only_row(line)
            uid = str(metadata["uid"])
            if uid not in uids:
                continue
            raw = raw_top_level_values(line, wanted)
            if not has_abstract or "abstract" not in raw:
                raise ValueError(f"dev40_abstract_missing:{uid}")
            extra = {k: json.loads(raw[k].decode("utf-8")) for k in ("abstract", "authors", "author_keywords") if k in raw}
            row = {**metadata, **extra}
            if not isinstance(row["abstract"], str) or not row["abstract"].strip():
                raise ValueError(f"dev40_abstract_empty:{uid}")
            selected[uid] = row
    if set(selected) != uids:
        raise ValueError(f"dev40_corpus_match:{len(selected)}/{len(uids)}")
    return selected


def _worker_checker(request_count: int) -> str:
    """Standalone worker checker, containing no validator/Gold/benchmark files."""
    return '''#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
from jsonschema import Draft202012Validator
root=Path.cwd(); m=json.loads((root/"batch_manifest.json").read_text())
schema=json.loads((root/"schema/evidence_matrix.schema.json").read_text())
v=Draft202012Validator(schema); inf=("mechanistic_interpretation","connection_to_user_research","possible_gap","transferable_idea","needs_fulltext_for")
def h(b): return hashlib.sha256(b).hexdigest()
assert len(m["requests"]) == COUNT == m["request_count"]
assert h((root/"prompt/evidence_extraction.md").read_bytes()) == m["prompt_sha256"]
assert h((root/"schema/evidence_matrix.schema.json").read_bytes()) == m["schema_sha256"]
for x in m["requests"]:
 q=json.loads((root/x["request_file"]).read_text()); p=root/x["response_file"]
 assert p.is_file()
 e=json.loads(p.read_text()); r=e["response"]
 for k in ("payload_sha256","request_sha256","prompt_sha256","schema_sha256","canonical_input_sha256"): assert e[k] == q[k]
 assert not list(v.iter_errors(r)) and all(r[k] == q[k] for k in ("uid","doi","title"))
 assert r["screening"]["status"] == "maybe" and all(r["inference"][k] == [] for k in inf)
print("BLIND_WORKER_DEV40_SUCCESS requests=COUNT responses=COUNT protocol_valid=COUNT inference_empty=COUNT")
'''.replace("COUNT", str(request_count))


def _worker_task(count: int) -> str:
    return f'''# Prompt Iteration 1 Dev40 worker task

Process all {count} requests listed in `batch_manifest.json`, independently and using only the files in this isolated directory. Read `prompt/evidence_extraction.md` and `schema/evidence_matrix.schema.json` unchanged. For each request, use its abstract and metadata only; create exactly one response envelope at the manifest's `response_file` path. Copy all five hashes from the request exactly and copy uid/doi/title. Follow the extraction prompt without adding any rules, use `screening.status="maybe"`, and leave every inference array empty. Do not inspect outside this directory, access Gold/benchmark/holdout files, use prior model responses, browse, or infer unsupported facts. Run `python3 ./validate_worker_outputs.py` once after writing all responses. If validation passes, report exactly `BLIND_WORKER_DEV40_SUCCESS requests={count} responses={count} protocol_valid={count} inference_empty={count}`. Do not modify the prompt, schema, requests, or manifest.
'''


def prepare(output: Path = OUTPUT, corpus: Path = CORPUS, identities: Path = IDENTITIES,
            prompt: Path = PROMPT, schema: Path = SCHEMA) -> dict:
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing_to_overwrite_nonempty_output:{output}")
    prompt_audit = audit_prompt()
    provenance_audit = audit_gold_provenance()
    if sha_file(prompt) != PROMPT_SHA256:
        raise ValueError("iteration1_prompt_sha_mismatch")
    if sha_file(ROOT / "docs/evidence_field_contract_v2_1.md") != CONTRACT_V21_SHA256:
        raise ValueError("frozen_contract_v21_sha_mismatch")
    current_frozen = {
        "schema": sha_file(schema),
        "validator_v2": sha_file(ROOT / "scripts/extractors/schema_validator_v2.py"),
        "metric_v1": sha_file(ROOT / "scripts/evidence/metrics.py"),
        "metric_v2": sha_file(ROOT / "scripts/evidence/metrics_v2.py"),
        "contract_v1": sha_file(ROOT / "docs/evidence_field_contract_v2.md"),
    }
    if current_frozen != FROZEN_INPUT_HASHES:
        raise ValueError("frozen_contract_schema_validator_or_metric_hash_mismatch")
    uids = dev_uids(identities)
    records = extract_dev_inputs(corpus, uids)
    batch = output / "batch_001"
    for name in ("requests", "responses", "prompt", "schema"):
        (batch / name).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(prompt, batch / "prompt/evidence_extraction.md")
    shutil.copyfile(schema, batch / "schema/evidence_matrix.schema.json")
    prompt_hash, schema_hash = sha_file(prompt), sha_file(schema)
    source_hash = sha_file(corpus)
    reqs = []
    for uid in sorted(uids):
        payload = build_payload(records[uid])
        payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = sha_bytes(payload_bytes)
        request = {**payload, "payload_sha256": payload_hash, "request_sha256": "",
                   "prompt_sha256": prompt_hash, "schema_sha256": schema_hash,
                   "canonical_input_sha256": source_hash}
        unsigned = dict(request)
        unsigned.pop("request_sha256")
        request["request_sha256"] = sha_bytes(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        filename = f"{payload_hash}.json"
        (batch / "requests" / filename).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reqs.append({"uid": uid, "doi": payload.get("doi"), "payload_sha256": payload_hash,
                     "request_sha256": request["request_sha256"], "request_file": f"requests/{filename}",
                     "response_file": f"responses/{filename}"})
    manifest = {"batch_id": "v07_dev40_i1", "evaluation_role": "development_set_only_not_independent_performance",
                "record_count": 40, "request_count": 40, "uids": sorted(uids), "requests": reqs,
                "prompt_sha256": prompt_hash, "schema_sha256": schema_hash,
                "canonical_input_sha256": source_hash,
                "contract_v21_sha256": CONTRACT_V21_SHA256,
                "validator": "scripts.extractors.schema_validator_v2.EvidenceValidator",
                "metric_versions": ["strict_metric_v1", "calibrated_metric_v2"],
                "contains_gold": False, "contains_contract_v21": False,
                "contains_validator": False, "contains_future_holdout": False,
                "future_holdout_abstracts_accessed": False}
    write_json(batch / "batch_manifest.json", manifest)
    write_json(batch / "run_manifest.json", {k: manifest[k] for k in ("batch_id", "evaluation_role", "record_count", "prompt_sha256", "schema_sha256", "canonical_input_sha256")})
    (batch / "TASK.md").write_text(_worker_task(40), encoding="utf-8")
    (batch / "WORKER_INSTRUCTIONS.md").write_text("Use only this directory. Never inspect Gold, benchmark or holdout data. Do not browse or infer. All inference arrays stay empty.\n", encoding="utf-8")
    checker = _worker_checker(40)
    (batch / "validate_worker_outputs.py").write_text(checker, encoding="utf-8")
    (batch / "validate_worker_outputs.py").chmod(0o755)
    hashes = {str(p.relative_to(batch)): sha_file(p) for p in sorted(batch.rglob("*")) if p.is_file()}
    write_json(output / "worker_input_manifest.json", {"files": hashes, "bundle_sha256": sha_bytes(json.dumps(hashes, sort_keys=True).encode()),
                                                         "record_count": 40, "prompt_sha256": prompt_hash,
                                                         "schema_sha256": schema_hash, "contains_gold": False,
                                                         "contains_contract_v21": False, "contains_holdout": False})
    write_json(output / "controller_integrity_manifest.json", {"approved_dev40_gold_sha256": sha_file(GOLD),
                                                                 "contract_v21_sha256": CONTRACT_V21_SHA256,
                                                                 "prompt_sha256": prompt_hash,
                                                                 "future_holdout_abstracts_accessed": False,
                                                                 "future_holdout_gold_created": False,
                                                                 "future_holdout_extraction_run": False,
                                                                 "future_holdout_identities_accessed": False})
    write_json(output / "prompt_static_audit.json", prompt_audit)
    write_json(output / "gold_provenance_audit.json", provenance_audit)
    # Independent path audit before the worker starts.
    allowed_roots = {"batch_manifest.json", "run_manifest.json", "TASK.md", "WORKER_INSTRUCTIONS.md",
                     "validate_worker_outputs.py", "prompt", "schema", "requests", "responses"}
    if {p.name for p in batch.iterdir()} != allowed_roots:
        raise ValueError("worker_bundle_allowlist_violation")
    if len(list((batch / "requests").glob("*.json"))) != 40 or list((batch / "responses").glob("*.json")):
        raise ValueError("worker_bundle_request_response_count")
    banned = ("gold", "contract_v2", "holdout", "candidate_gold", "abstracts.jsonl")
    if any(any(term in str(p.relative_to(batch)).casefold() for term in banned) for p in batch.rglob("*")):
        raise ValueError("worker_bundle_contains_forbidden_path")
    return manifest


def validate_and_measure(batch_dir: Path = OUTPUT / "batch_001", output: Path = OUTPUT,
                         reports: Path = REPORTS) -> dict:
    batch_dir, output, reports = map(lambda p: Path(p).resolve(), (batch_dir, output, reports))
    input_manifest = json.loads((output / "worker_input_manifest.json").read_text(encoding="utf-8"))
    current_hashes = {str(p.relative_to(batch_dir)): sha_file(p) for p in sorted(batch_dir.rglob("*")) if p.is_file() and p.parent.name != "responses"}
    if current_hashes != input_manifest["files"]:
        raise ValueError("worker_modified_frozen_prompt_schema_requests_or_bundle_instructions")
    controller_manifest = json.loads((output / "controller_integrity_manifest.json").read_text(encoding="utf-8"))
    for key in ("future_holdout_abstracts_accessed", "future_holdout_gold_created", "future_holdout_extraction_run", "future_holdout_identities_accessed"):
        controller_manifest.setdefault(key, False)
    write_json(output / "controller_integrity_manifest.json", controller_manifest)
    if sha_file(GOLD) != controller_manifest["approved_dev40_gold_sha256"]:
        raise ValueError("approved_dev40_gold_changed_during_iteration1")
    manifest = json.loads((batch_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    records = extract_dev_inputs(CORPUS, set(manifest["uids"]))
    gold_rows = read_jsonl(GOLD)
    gold = {r["uid"]: r for r in gold_rows}
    if len(gold) != 40 or set(gold) != set(manifest["uids"]):
        raise ValueError("approved_dev40_gold_identity_mismatch")
    schema_validator = Draft202012Validator(json.loads((batch_dir / "schema/evidence_matrix.schema.json").read_text(encoding="utf-8")))
    grounding = EvidenceValidator(batch_dir / "schema/evidence_matrix.schema.json")
    predictions, validation, response_hashes = {}, {}, {}
    missing, extra, duplicate = [], [], []
    seen = set()
    expected_paths = {x["response_file"] for x in manifest["requests"]}
    actual_paths = {str(p.relative_to(batch_dir)) for p in (batch_dir / "responses").glob("*.json")}
    extra = sorted(actual_paths - expected_paths)
    for item in manifest["requests"]:
        uid = item["uid"]
        path = batch_dir / item["response_file"]
        if not path.is_file():
            missing.append(uid)
            continue
        response_hashes[uid] = sha_file(path)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        request = json.loads((batch_dir / item["request_file"]).read_text(encoding="utf-8"))
        response = envelope.get("response")
        errs = list(schema_validator.iter_errors(response)) if isinstance(response, dict) else ["response_not_object"]
        schema_valid = not errs
        identifier_match = bool(isinstance(response, dict) and all(response.get(k) == records[uid].get(k) for k in ("uid", "doi", "title")))
        hash_valid = all(envelope.get(k) == request.get(k) for k in ("payload_sha256", "request_sha256", "prompt_sha256", "schema_sha256", "canonical_input_sha256"))
        response_contract = bool(isinstance(response, dict) and response.get("screening", {}).get("status") == "maybe"
                                 and all(response.get("inference", {}).get(k) == [] for k in INFERENCE_FIELDS)
                                 and hash_valid and identifier_match)
        grounding_error = None
        if schema_valid and identifier_match:
            try:
                grounding.validate(response, records[uid])
            except Exception as exc:  # validator emits fixed safe messages
                grounding_error = str(exc)
        is_grounded = grounding_error is None and schema_valid and identifier_match
        support = response.get("evidence_support", {}) if isinstance(response, dict) else {}
        expected_support = {pointer for pointer, _ in factual_leaves(response.get("evidence", {}))} if isinstance(response, dict) else set()
        orphan_count = len(set(support) - expected_support)
        missing_support = len(expected_support - set(support))
        invalid_offsets = 0
        abstract = records[uid]["abstract"]
        anchors = list(support.values())
        if isinstance(response, dict):
            anchors += list(response.get("evidence", {}).get("findings", []))
            anchors += [i.get("anchor", {}) for i in response.get("evidence", {}).get("author_interpretations", [])]
        for anchor in anchors:
            start, end, quote = anchor.get("start"), anchor.get("end"), anchor.get("evidence_text", "")
            if anchor.get("source") != "abstract" or type(start) is not int or type(end) is not int or not (0 <= start < end <= len(abstract)) or abstract[start:end] != quote:
                invalid_offsets += 1
        # Required finding claims must be exact substrings of their anchored abstract quote.
        findings = response.get("evidence", {}).get("findings", []) if isinstance(response, dict) else []
        unsupported_findings = sum(f.get("source") != "abstract" or f.get("claim") not in f.get("evidence_text", "") for f in findings)
        unsupported_fields = missing_support + orphan_count
        if grounding_error and any(term in grounding_error.casefold() for term in ("support", "source span", "explicitly supported", "evidence value")):
            unsupported_fields += 1
        accepted = bool(schema_valid and is_grounded and identifier_match and response_contract and not invalid_offsets and not orphan_count)
        predictions[uid] = response if isinstance(response, dict) else None
        validation[uid] = {"schema_valid": schema_valid, "grounding_valid": is_grounded,
                           "identifier_match": identifier_match, "response_contract_valid": response_contract,
                           "grounding_error": grounding_error,
                           "accepted": accepted, "error_kind": "offset" if invalid_offsets else "schema" if not schema_valid else "grounding" if not is_grounded else None,
                           "unsupported_field_count": unsupported_fields, "unsupported_finding_count": unsupported_findings,
                           "invalid_offset_count": invalid_offsets, "orphan_anchor_count": orphan_count}
        if uid in seen:
            duplicate.append(uid)
        seen.add(uid)
    if missing or extra or duplicate or len(predictions) != 40:
        raise ValueError(f"response_set_mismatch:missing={len(missing)},extra={len(extra)},duplicate={len(duplicate)}")

    # Prove the worker's response files stayed byte-identical after first hashing.
    initial_hashes_path = output / "worker_response_sha256.json"
    if initial_hashes_path.exists():
        prior = json.loads(initial_hashes_path.read_text(encoding="utf-8"))
        verify_response_hashes(batch_dir, prior)
    else:
        write_json(initial_hashes_path, response_hashes)

    v1 = calculate_benchmark(gold, predictions, validation)
    v2papers = [(uid, gold[uid], predictions[uid], records[uid]["abstract"]) for uid in sorted(gold)]
    v2raw = corpus_metrics_v2(v2papers)
    v2fields = {}
    for pointer in REQUIRED_FIELDS:
        if pointer == "/evidence/findings":
            continue
        counts = Counter()
        for uid in gold:
            metric = v2raw["per_record"][uid]["fields"]["field_metrics"].get(pointer)
            if metric:
                counts.update({k: metric[k] for k in ("exact_tp", "boundary_tp", "fp", "fn", "gold_count", "predicted_count")})
        tp = counts["exact_tp"] + counts["boundary_tp"]
        p = tp / (tp + counts["fp"]) if tp + counts["fp"] else (1.0 if not counts["gold_count"] else 0.0)
        r = tp / (tp + counts["fn"]) if tp + counts["fn"] else 1.0
        v2fields[pointer] = {**dict(counts), "precision": p, "recall": r, "f1": 2*p*r/(p+r) if p+r else 0.0}
    v2 = {"evaluation_role": "development_set_only_not_independent_performance", "records": 40,
          "fields": v2raw["fields"], "field_metrics": v2fields, "findings": v2raw["findings"], "per_record": v2raw["per_record"]}
    fp, fr = v2["findings"]["precision"], v2["findings"]["recall"]
    v2["findings"]["f1"] = 2 * fp * fr / (fp + fr) if fp + fr else 0.0
    primary_errors, cross_category, soil_fn, plant_route, salinity_diagnostics = analyze_errors(gold, predictions, records, v2raw)
    v1_finding = v1["finding_metrics"]
    f1_findings = {"precision": v1_finding["precision"], "recall": v1_finding["recall"], "f1": v1_finding["f1"]}
    sal = v2fields["/evidence/study_system/salinity_context"]
    sal_v1 = v1["field_metrics"]["/evidence/study_system/salinity_context"]
    validation_summary = {"responses": len(validation),
                          "schema_valid": sum(x["schema_valid"] for x in validation.values()),
                          "grounding_valid": sum(x["grounding_valid"] for x in validation.values()),
                          "identifier_valid": sum(x["identifier_match"] for x in validation.values()),
                          "response_contract_valid": sum(x["response_contract_valid"] for x in validation.values()),
                          "unsupported_fields": sum(x["unsupported_field_count"] for x in validation.values()),
                          "unsupported_findings": sum(x["unsupported_finding_count"] for x in validation.values()),
                          "invalid_offsets": sum(x["invalid_offset_count"] for x in validation.values()),
                          "orphan_anchors": sum(x["orphan_anchor_count"] for x in validation.values()),
                          "rejected_responses": sum(not x["accepted"] for x in validation.values()),
                          "grounding_errors": [{"uid": uid, "error": row["grounding_error"]} for uid, row in validation.items() if row["grounding_error"]],
                          "experimental_scale_validator_v2": {
                              "scale_values_predicted": sum(1 for response in predictions.values()
                                                             if response and response.get("evidence", {}).get("study_system", {}).get("experimental_scale") not in (None, "", "unknown")),
                              "scale_support_rejected": sum(1 for row in validation.values()
                                                             if row["grounding_error"] and "experimental scale" in row["grounding_error"].casefold()),
                              "scale_values_grounded": sum(1 for response in predictions.values()
                                                            if response and response.get("evidence", {}).get("study_system", {}).get("experimental_scale") not in (None, "", "unknown")) -
                                                        sum(1 for row in validation.values()
                                                            if row["grounding_error"] and "experimental scale" in row["grounding_error"].casefold()),
                              "responses_accepted_by_validator_v2": sum(1 for row in validation.values() if row["accepted"]),
                              "rejected_uids": [uid for uid, row in validation.items()
                                                if row["grounding_error"] and "experimental scale" in row["grounding_error"].casefold()]}}
    report = {"DEV40_I1_COMPLETE": True,
              "evaluation_role": "development_set_only_not_independent_performance", "record_count": 40,
              "future_holdout_status": {"abstracts_accessed": False, "gold_created": False, "extraction_run": False, "identities_accessed": False},
              "prompt_sha256": manifest["prompt_sha256"], "contract_v21_sha256": CONTRACT_V21_SHA256,
              "validator": "Validator v2", "validation": validation_summary, "v1": v1, "v2": v2,
              "diagnostics": {"primary_error_classes": primary_errors, "cross_category_confusion": cross_category,
                              "soil_type_false_negative_categories": soil_fn, "plant_growth_to_other_routing": plant_route,
                              "salinity_context_diagnostics": salinity_diagnostics,
                              "salinity_context_v1_exact_counts": sal_v1,
                              "salinity_context_v2_counts": sal,
                              "salinity_boundary_recovery": sal["boundary_tp"],
                              "finding_precision_below_0_95_risk_only": f1_findings["precision"] < .95}}
    reports.mkdir(parents=True, exist_ok=True)
    write_json(reports / "v07_dev40_i1_errors.json", report)
    write_error_markdown(reports / "v07_dev40_i1_errors.md", report, primary_errors, cross_category, soil_fn, plant_route)
    write_json(output / "validation_v2.json", {**validation_summary,
              "identity_errors": sum(not x["identifier_match"] for x in validation.values()), "per_record": validation})
    write_json(output / "metric_v1.json", v1)
    write_json(output / "metric_v2.json", v2)
    write_json(output / "error_analysis.json", report["diagnostics"])
    report["paired_comparison"] = paired_comparison(report)
    write_json(output / "paired_comparison.json", report["paired_comparison"])
    return report


def paired_comparison(report: dict) -> dict:
    baseline_dir = ROOT / "data/evidence_batches/v07_dev40_baseline"
    base_v1 = json.loads((baseline_dir / "metric_v1.json").read_text(encoding="utf-8"))
    base_v2 = json.loads((baseline_dir / "metric_v2.json").read_text(encoding="utf-8"))
    base_errors = json.loads((baseline_dir / "error_analysis.json").read_text(encoding="utf-8"))
    current_v1, current_v2 = report["v1"], report["v2"]
    current_val = report["validation"]
    requested = {
        "soil_type": "/evidence/study_system/soil_type",
        "plant_growth": "/evidence/measurements/plant_growth",
        "other": "/evidence/measurements/other",
        "salinity_context": "/evidence/study_system/salinity_context",
        "experimental_scale": "/evidence/study_system/experimental_scale",
        "microbial": "/evidence/measurements/microbial",
        "carbon": "/evidence/measurements/carbon",
        "soil_chemical": "/evidence/measurements/soil_chemical",
        "fertilization": "/evidence/treatments/fertilization",
        "amendments": "/evidence/treatments/amendments",
        "methods": "/evidence/methods",
    }
    fields = {}
    for label, pointer in requested.items():
        before = base_v2["field_metrics"][pointer]
        after = current_v2["field_metrics"][pointer]
        fields[label] = {
            "baseline_f1": before["f1"], "iteration1_f1": after["f1"],
            "f1_delta": after["f1"] - before["f1"],
            "baseline_fp": before["fp"], "iteration1_fp": after["fp"],
            "fp_delta": after["fp"] - before["fp"],
            "baseline_fn": before["fn"], "iteration1_fn": after["fn"],
            "fn_delta": after["fn"] - before["fn"],
            "baseline_precision": before["precision"], "iteration1_precision": after["precision"],
            "baseline_recall": before["recall"], "iteration1_recall": after["recall"],
            "gold_count": after["gold_count"], "predicted_count": after["predicted_count"],
            "tp": after["exact_tp"] + after["boundary_tp"],
        }
    def simple(metric: dict) -> dict:
        return {k: metric[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn") if k in metric}
    base_routes = base_errors["plant_growth_to_other_routing"]["span_match_counts"]
    next_routes = report["diagnostics"]["plant_growth_to_other_routing"]["span_match_counts"]
    route_pairs = {"same_span": ("exact_span", 3), "overlap_span": ("partial_span", 34),
                   "semantic_related": ("semantic_only", 11)}
    routes = {}
    for label, (key, expected) in route_pairs.items():
        before = base_routes[key]
        if before != expected:
            raise ValueError(f"baseline_route_anchor_mismatch:{label}")
        after = next_routes.get(key, 0)
        routes[label] = {"baseline": before, "iteration1": after, "delta": after - before,
                         "reduction_percent": (before - after) / before * 100}
    base_find_v2, next_find_v2 = base_v2["findings"], current_v2["findings"]
    precision_below = next_find_v2["precision"] < .95
    finding_f1_not_improved = next_find_v2["f1"] < base_find_v2["f1"]
    finding_regression = precision_below or finding_f1_not_improved
    base_overall, next_overall = base_v2["fields"], current_v2["fields"]
    sal_base, sal_next = fields["salinity_context"], report["diagnostics"]["salinity_context_diagnostics"]["counts"]
    validation_clean = current_val["grounding_valid"] == 40 and current_val["rejected_responses"] == 0
    other_fp_down = fields["other"]["fp_delta"] < 0
    major_fields_up = fields["soil_type"]["f1_delta"] > 0 and fields["plant_growth"]["f1_delta"] > 0
    salinity_precision_not_lower = sal_base["iteration1_precision"] >= sal_base["baseline_precision"]
    overall_up = next_overall["f1"] > base_overall["f1"]
    findings_safe = not finding_regression
    if overall_up and major_fields_up and other_fp_down and validation_clean and salinity_precision_not_lower and findings_safe:
        recommendation = "KEEP"
    elif next_overall["f1"] < base_overall["f1"] and fields["soil_type"]["f1_delta"] <= 0 and fields["plant_growth"]["f1_delta"] <= 0:
        recommendation = "REVERT"
    else:
        recommendation = "REVISE"
    return {
        "evaluation_role": "development_set_only_not_independent_performance",
        "baseline_source": "data/evidence_batches/v07_dev40_baseline",
        "metric_versions_unchanged": ["strict_metric_v1", "calibrated_metric_v2"],
        "overall_v1": {"baseline": simple(base_v1["field_micro"]), "iteration1": simple(current_v1["field_micro"])},
        "overall_v2": {"baseline": simple(base_overall), "iteration1": simple(next_overall)},
        "fields_v2": fields,
        "plant_growth_to_other_routing": routes,
        "soil_type_counts": {k: fields["soil_type"][k] for k in ("gold_count", "predicted_count", "tp", "iteration1_fp", "iteration1_fn", "baseline_precision", "iteration1_precision", "baseline_recall", "iteration1_recall", "baseline_f1", "iteration1_f1")},
        "salinity_context": {"boundary_matches": {"baseline": base_v2["field_metrics"][requested["salinity_context"]]["boundary_tp"], "iteration1": current_v2["field_metrics"][requested["salinity_context"]]["boundary_tp"]},
                             "bare_descriptor_fp": {"baseline": base_errors["salinity_context_diagnostics"]["counts"].get("bare descriptor FP", 0), "iteration1": sal_next.get("bare descriptor FP", 0)},
                             "background_application_fp": {"baseline": base_errors["salinity_context_diagnostics"]["counts"].get("background/application FP", 0), "iteration1": sal_next.get("background/application FP", 0)},
                             "actual_system_linked_tp": {"baseline": base_errors["salinity_context_diagnostics"]["counts"].get("actual_system_linked_tp", 0), "iteration1": sal_next.get("actual_system_linked_tp", 0)},
                             "precision": {"baseline": fields["salinity_context"]["baseline_precision"], "iteration1": fields["salinity_context"]["iteration1_precision"]},
                             "recall": {"baseline": fields["salinity_context"]["baseline_recall"], "iteration1": fields["salinity_context"]["iteration1_recall"]}},
        "findings": {"baseline": base_find_v2, "iteration1": next_find_v2,
                     "precision_below_0_95": precision_below,
                     "f1_delta": next_find_v2["f1"] - base_find_v2["f1"],
                     "finding_regression": finding_regression},
        "top_error_classes": report["diagnostics"]["primary_error_classes"]["top_10"],
        "validation": current_val,
        "recommendation": recommendation,
        "recommendation_factors": {"overall_v2_f1_improved": overall_up, "soil_type_and_plant_growth_f1_improved": major_fields_up,
                                   "other_fp_decreased": other_fp_down, "grounding_40_of_40_and_zero_rejected": validation_clean,
                                   "salinity_precision_not_lower": salinity_precision_not_lower, "findings_no_regression": findings_safe},
    }


def analyze_errors(gold, predictions, records, v2raw):
    classes, cross, soil = Counter(), Counter(), Counter()
    plant_route, plant_physiology = Counter(), Counter()
    salinity = Counter()
    mismatch_rows = []
    routes = {
        "/evidence/measurements/plant_growth": "/evidence/measurements/other",
        "/evidence/measurements/microbial": "/evidence/measurements/other",
        "/evidence/measurements/carbon": "/evidence/measurements/other",
        "/evidence/measurements/soil_chemical": "/evidence/measurements/other",
        "/evidence/treatments/fertilization": "/evidence/treatments/amendments",
        "/evidence/treatments/amendments": "/evidence/treatments/fertilization",
        "/evidence/study_system/soil_type": "/evidence/study_system/salinity_context",
        "/evidence/study_system/salinity_context": "/evidence/study_system/soil_type",
    }
    for uid in gold:
        g, p, abstract = gold[uid], predictions[uid], records[uid]["abstract"]
        from scripts.evidence.metrics_v2 import _field_items, one_to_one_match
        for pointer, metric in v2raw["per_record"][uid]["fields"]["field_metrics"].items():
            gs, ps = _field_items(g, pointer), _field_items(p, pointer)
            _, unmatched_p, unmatched_g = one_to_one_match(gs, ps, abstract, scale=pointer.endswith("experimental_scale"))
            if pointer == "/evidence/study_system/salinity_context":
                salinity["boundary_only_mismatch"] += metric["boundary_tp"]
                salinity["actual_system_linked_tp"] += metric["exact_tp"] + metric["boundary_tp"]
                for pair in one_to_one_match(gs, ps, abstract)[0]:
                    source = ps[pair["prediction_index"]].get("support") or {}
                    text = str(source.get("evidence_text", "")).casefold()
                    if any(term in text for term in ("induced", "irrigated with", "saline water", "salt stress", "salt treatment")):
                        salinity["treatment-induced salinity"] += 1
            for kind, indices, rows in (("FP", unmatched_p, ps), ("FN", unmatched_g, gs)):
                for ix in indices:
                    item = rows[ix]
                    value, support = item.get("value"), item.get("support")
                    routed_to = routes.get(pointer)
                    routed_item = None
                    if kind == "FN" and routed_to:
                        candidates = _field_items(p, routed_to)
                        routed_item = next((x for x in candidates if x.get("support") and support and _overlap(support, x["support"])), None)
                    elif kind == "FP":
                        for source, target in routes.items():
                            if target == pointer:
                                candidates = _field_items(g, source)
                                routed_item = next((x for x in candidates if x.get("support") and support and _overlap(support, x["support"])), None)
                                if routed_item:
                                    routed_to = source
                                    break
                    primary = _error_class(pointer, kind, value, support, abstract, g, routed_to if routed_item else None)
                    classes[primary] += 1
                    entry = {"uid": uid, "doi": g.get("doi"), "field": pointer,
                             "Gold": value if kind == "FN" else (routed_item.get("value") if routed_item else None),
                             "Prediction": value if kind == "FP" else (routed_item.get("value") if routed_item else None),
                             "Gold_support": support if kind == "FN" else (routed_item.get("support") if routed_item else None),
                             "Prediction_support": support if kind == "FP" else (routed_item.get("support") if routed_item else None),
                             "abstract_context": _context(abstract, support or (routed_item or {}).get("support")),
                             "primary_error": primary,
                             "secondary_error": "same_span_cross_category_route" if routed_item else None}
                    mismatch_rows.append(entry)
                    if pointer.endswith("/soil_type") and kind == "FN":
                        soil[_soil_fn_class(value, support, abstract, g, p)] += 1
                    if pointer.endswith("/plant_growth") and kind == "FN":
                        route_kind = _find_other_route(item, p, abstract)
                        if route_kind:
                            plant_route[route_kind] += 1
                            plant_physiology[_physiology(value, support, abstract)] += 1
                    if pointer.endswith("/salinity_context"):
                        category = _salinity_class(kind, support, abstract, g)
                        salinity[category] += 1

        # Cross-category confusion: unmatched Gold item span appears in a different predicted category.
        for source in REQUIRED_FIELDS:
            source_gold = _field_items(g, source)
            source_pred = _field_items(p, source)
            _, _, missed_source = one_to_one_match(source_gold, source_pred, abstract,
                                                   scale=source.endswith("experimental_scale"))
            if not missed_source:
                continue
            target = routes.get(source)
            if not target:
                continue
            target_values = _field_items(p, target)
            for source_index in missed_source:
                ss = source_gold[source_index].get("support")
                if ss and any(x.get("support") and _overlap(ss, x["support"]) for x in target_values):
                    cross[f"{source} -> {target}"] += 1
        finding_metric = finding_metrics_v2(g, p, abstract)
        for kind, items in (("FP", finding_metric["unmatched_predictions"]), ("FN", finding_metric["unmatched_gold_items"])):
            for item in items:
                duplicate_layer = False
                if kind == "FP":
                    quote = str(item.get("evidence_text", ""))
                    evidence = p.get("evidence", {})
                    duplicate_layer = any(quote == str(x) for x in evidence.get("mechanisms_explicit", [])) or any(
                        quote == str(x.get("text", "")) or quote == str((x.get("anchor") or {}).get("evidence_text", ""))
                        for x in evidence.get("author_interpretations", []))
                primary = ("INTERPRETATION_DUPLICATION" if duplicate_layer else "TRUE_OVEREXTRACTION") if kind == "FP" else "TRUE_OMISSION"
                classes[primary] += 1
                mismatch_rows.append({"uid": uid, "doi": g.get("doi"), "field": "/evidence/findings",
                                      "Gold": None if kind == "FP" else item,
                                      "Prediction": item if kind == "FP" else None,
                                      "Gold_support": None if kind == "FP" else {"source": item.get("source"), "evidence_text": item.get("evidence_text"), "start": item.get("start"), "end": item.get("end")},
                                      "Prediction_support": {"source": item.get("source"), "evidence_text": item.get("evidence_text"), "start": item.get("start"), "end": item.get("end")} if kind == "FP" else None,
                                      "abstract_context": _context(abstract, {"start": item.get("start"), "end": item.get("end")}),
                                      "primary_error": primary, "secondary_error": None})
    field_totals = Counter()
    for pointer in REQUIRED_FIELDS:
        field_totals[pointer] = sum(v2raw["per_record"][uid]["fields"]["field_metrics"][pointer]["fp"] +
                                    v2raw["per_record"][uid]["fields"]["field_metrics"][pointer]["fn"] for uid in gold)
    # Route counts are emitted even when zero, to make directional comparisons explicit.
    for source, target in routes.items():
        cross.setdefault(f"{source} -> {target}", 0)
    for label in ("TRUE_OMISSION", "TRUE_OVEREXTRACTION", "WRONG_CATEGORY", "BOUNDARY_ONLY",
                  "SOIL_TYPE_ABSTENTION", "SALINITY_LINK_ERROR", "PLANT_TO_OTHER", "OTHER_OVERUSE",
                  "MICROBIAL_ROUTING", "CARBON_ROUTING", "SOIL_CHEM_ROUTING",
                  "FERTILIZATION_AMENDMENT_ROUTING", "TREATMENT_ROLE_ERROR", "REVIEW_ATTRIBUTION",
                  "SCALE_ERROR", "INTERPRETATION_DUPLICATION", "OTHER"):
        classes.setdefault(label, 0)
    classes["BOUNDARY_ONLY"] = sum(metric["boundary_tp"] for uid in gold
                                   for metric in v2raw["per_record"][uid]["fields"]["field_metrics"].values())
    for label in ("exact_span", "partial_span", "semantic_only"):
        plant_route.setdefault(label, 0)
    for label in ("photosynthesis", "pigments", "gas_exchange", "plant_ions", "antioxidant", "ros",
                  "osmolyte", "gene_expression", "morphology", "other_physiology"):
        plant_physiology.setdefault(label, 0)
    for label in ("bare descriptor FP", "actual_system_linked_tp", "background/application FP",
                  "review_scope_errors", "treatment-induced salinity", "boundary_only_mismatch"):
        salinity.setdefault(label, 0)
    return ({"counts": dict(classes.most_common()), "top_10": classes.most_common(10),
             "top_fields_by_fp_plus_fn": field_totals.most_common(), "unmatched_items": mismatch_rows},
            {"counts": dict(cross.most_common()), "required_routes": {f"{s} -> {t}": cross[f"{s} -> {t}"] for s, t in routes.items()},
             "top_10": cross.most_common(10)},
            {"counts": dict(soil), "total": sum(soil.values())},
            {"span_match_counts": dict(plant_route), "physiology_class_counts": dict(plant_physiology),
             "total_routed": sum(plant_route.values())},
            {"counts": dict(salinity), "total": sum(salinity.values())})


def _field_items_by_path(record, pointer):
    from scripts.evidence.metrics_v2 import _field_items
    return [(x.get("value"), x.get("support")) for x in _field_items(record, pointer)]


def _overlap(a, b):
    if type(a.get("start")) is not int or type(a.get("end")) is not int or type(b.get("start")) is not int or type(b.get("end")) is not int:
        return False
    return max(a["start"], b["start"]) < min(a["end"], b["end"])


def _context(abstract, support, width=180):
    if not support or type(support.get("start")) is not int:
        return ""
    start = max(0, support["start"] - width // 2)
    end = min(len(abstract), support.get("end", start) + width // 2)
    return abstract[start:end].replace("\n", " ")


def _error_class(pointer, kind, value, support, abstract, gold, routed_from):
    if routed_from:
        if kind == "FN":
            if pointer.endswith("/measurements/plant_growth") and routed_from.endswith("/measurements/other"):
                return "PLANT_TO_OTHER"
            if pointer.endswith("/measurements/microbial") and routed_from.endswith("/measurements/other"):
                return "MICROBIAL_ROUTING"
            if pointer.endswith("/measurements/carbon") and routed_from.endswith("/measurements/other"):
                return "CARBON_ROUTING"
            if pointer.endswith("/measurements/soil_chemical") and routed_from.endswith("/measurements/other"):
                return "SOIL_CHEM_ROUTING"
        if pointer.endswith("/measurements/other"):
            if routed_from.endswith("/plant_growth"):
                return "PLANT_TO_OTHER"
            if routed_from.endswith("/microbial"):
                return "MICROBIAL_ROUTING"
            if routed_from.endswith("/carbon"):
                return "CARBON_ROUTING"
            if routed_from.endswith("/soil_chemical"):
                return "SOIL_CHEM_ROUTING"
        if "fertilization" in pointer or "amendments" in pointer:
            return "FERTILIZATION_AMENDMENT_ROUTING"
        if "soil_type" in pointer or "salinity_context" in pointer:
            return "SALINITY_LINK_ERROR"
        return "WRONG_CATEGORY"
    if pointer.endswith("experimental_scale"):
        return "SCALE_ERROR"
    if pointer.endswith("salinity_context"):
        return "SALINITY_LINK_ERROR"
    if pointer.endswith("soil_type") and kind == "FN":
        return "SOIL_TYPE_ABSTENTION"
    if pointer.endswith("measurements/other") and kind == "FP":
        return "OTHER_OVERUSE"
    if pointer.endswith("measurements/microbial"):
        return "MICROBIAL_ROUTING"
    if pointer.endswith("measurements/carbon"):
        return "CARBON_ROUTING"
    if pointer.endswith("measurements/soil_chemical"):
        return "SOIL_CHEM_ROUTING"
    if "treatments" in pointer and "review" in " ".join(gold.get("document_types") or []).casefold() and kind == "FP":
        return "REVIEW_ATTRIBUTION"
    if "fertilization" in pointer or "amendments" in pointer:
        return "TREATMENT_ROLE_ERROR"
    if kind == "FP":
        return "TRUE_OVEREXTRACTION"
    if kind == "FN":
        return "TRUE_OMISSION"
    return "OTHER"


def _soil_fn_class(value, support, abstract, gold, pred):
    text = (_context(abstract, support, 500) + " " + str(value or "")).casefold()
    if "review" in " ".join(gold.get("document_types", [])).casefold() or "review" in text:
        return "review scope"
    if any(x in text for x in ("paddy field", "upland field", "saline-alkali land")):
        return "land-use ambiguity"
    if any(x in text for x in ("pot", "substrate", "experimental soil", "soil sample", "soil samples", "collected from", "field soil", "soil was collected")):
        return "explicit studied-soil descriptor"
    if any(x in text for x in ("soil", "site", "field", "substrate")):
        return "actual-system linkage missing"
    return "worker abstention"


def _find_other_route(gold_item, prediction, abstract):
    g = gold_item.get("support") or {}
    from scripts.evidence.metrics_v2 import _field_items
    for pred in _field_items(prediction, "/evidence/measurements/other"):
        s = pred.get("support") or {}
        if type(g.get("start")) is not int or type(s.get("start")) is not int:
            continue
        if (g["start"], g["end"]) == (s["start"], s["end"]):
            return "exact_span"
        if _overlap(g, s):
            return "partial_span"
        a = set(str(gold_item.get("value", "")).casefold().split())
        b = set(str(pred.get("value", "")).casefold().split())
        if a & b:
            return "semantic_only"
    return None


def _physiology(value, support, abstract):
    text = (str(value or "") + " " + (support or {}).get("evidence_text", "")).casefold()
    for label, terms in (("photosynthesis", ("photosynth",)), ("pigments", ("chlorophyll", "pigment")),
                         ("gas_exchange", ("gas exchange",)), ("plant_ions", ("na+", "k+", "na/k", "sodium", "potassium")),
                         ("antioxidant", ("antioxidant", "superoxide dismutase", "catalase", "peroxidase")),
                         ("ros", ("reactive oxygen", "hydrogen peroxide", "ros")),
                         ("osmolyte", ("proline", "osmolyte", "soluble sugar")),
                         ("gene_expression", ("gene expression", "transcript", "mrna")),
                         ("morphology", ("root length", "shoot length", "biomass", "plant height"))):
        if any(term in text for term in terms):
            return label
    return "other_physiology"


def _salinity_class(kind, support, abstract, gold):
    text = ((support or {}).get("evidence_text", "") + " " + _context(abstract, support, 360)).casefold()
    dtype = " ".join(gold.get("document_types") or []).casefold()
    if "review" in dtype and kind == "FP":
        return "review_scope_errors"
    if any(x in text for x in ("application", "potential", "background", "in general")):
        return "background/application FP"
    if kind == "FP":
        return "bare descriptor FP"
    if any(x in text for x in ("induced", "irrigated with", "saline water", "salt stress", "salt treatment")):
        return "treatment-induced salinity"
    return "other/ambiguous"


def write_error_markdown(path, report, primary, cross, soil, plant):
    v1, v2 = report["v1"], report["v2"]
    lines = ["# v0.7 Dev40 Frozen-Prompt Baseline Error Analysis", "",
             "**Evaluation scope:** development set only; this is not independent performance. The 30-paper future holdout was not used.", "",
             f"Prompt SHA256: `{report['prompt_sha256']}`  ", f"Contract v2.1 SHA256: `{report['contract_v21_sha256']}`  ",
             "Validator: v2", "", "## Overall metrics", "",
             f"Strict v1 field micro P/R/F1: {v1['field_micro']['precision']:.4f} / {v1['field_micro']['recall']:.4f} / {v1['field_micro']['f1']:.4f}",
             f"Strict v1 finding P/R/F1: {v1['finding_metrics']['precision']:.4f} / {v1['finding_metrics']['recall']:.4f} / {v1['finding_metrics']['f1']:.4f}",
             f"Calibrated v2 field P/R/F1: {v2['fields']['precision']:.4f} / {v2['fields']['recall']:.4f} / {v2['fields']['f1']:.4f}",
             f"Calibrated v2 finding P/R/F1: {v2['findings']['precision']:.4f} / {v2['findings']['recall']:.4f} / {v2['findings']['f1']:.4f}", "",
             "## Per-field v2 metrics", "", "| Field | Exact TP | Boundary TP | FP | FN | P | R | F1 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for pointer, m in report["v2"]["field_metrics"].items():
        lines.append(f"| `{pointer}` | {m['exact_tp']} | {m['boundary_tp']} | {m['fp']} | {m['fn']} | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} |")
    lines += ["", "## Error diagnostics", "", "### Top error classes", "",
              "Counts are unmatched-side records; the cross-category matrix below counts each routed Gold span once.", "",
              "| Class | Count |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in primary["top_10"]]
    lines += ["", "### Top error fields by FP + FN", "", "| Field | FP + FN |", "|---|---:|"]
    lines += [f"| `{k}` | {v} |" for k, v in primary["top_fields_by_fp_plus_fn"][:10]]
    lines += ["", "### Cross-category confusion", "", "| Source field → predicted field | Count |", "|---|---:|"]
    lines += [f"| `{k}` | {v} |" for k, v in cross["required_routes"].items()]
    lines += ["", "### Soil type", "", f"Metric v2: `{json.dumps(v2['field_metrics']['/evidence/study_system/soil_type'], ensure_ascii=False)}`"]
    lines += ["", "### Soil type false-negative categories", "", "| Category | Count |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in soil["counts"].items()]
    lines += ["", "### plant_growth → other span matching", "", "| Measure | Count |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in plant["span_match_counts"].items()]
    lines += ["", "Physiology classes:", ""]
    lines += [f"- {k}: {v}" for k, v in plant["physiology_class_counts"].items()]
    lines += ["", "### Salinity context", "", f"v1 exact counts: `{json.dumps(report['diagnostics']['salinity_context_v1_exact_counts'], ensure_ascii=False)}`",
              f"v2 exact/boundary/FP/FN: `{json.dumps(report['diagnostics']['salinity_context_v2_counts'], ensure_ascii=False)}`",
              f"Boundary recovery: {report['diagnostics']['salinity_boundary_recovery']}",
              f"Error classes: `{json.dumps(report['diagnostics']['salinity_context_diagnostics'], ensure_ascii=False)}`", "",
              f"Validator v2 summary: `{json.dumps(report['validation'], ensure_ascii=False)}`", "",
              "### Finding comparison (development diagnostic only)", "",
              f"Iteration 1 strict-v1 finding P/R/F1: {report['v1']['finding_metrics']}",
              "Finding regression is assessed in the paired development-only report.", "",
              "Detailed UID-level mismatches and cited abstract contexts are in `v07_dev40_i1_errors.json`.", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def run_worker(bundle: Path, *, codex: str, log_path: Path) -> str:
    """Launch exactly one fresh ephemeral worker, return its captured final line."""
    env = {k: v for k, v in __import__("os").environ.items()
           if not any(token in k.upper() for token in ("API_KEY", "APIKEY", "SECRET", "TOKEN", "OPENAI_API_KEY", "ANTHROPIC"))}
    command = [codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
               "--sandbox", "workspace-write", "--cd", str(bundle),
               "--disable", "apps", "--disable", "browser_use", "--disable", "browser_use_external",
               "--disable", "computer_use", "--disable", "in_app_browser", "--disable", "memories",
               "--disable", "plugins", "--disable", "skill_mcp_dependency_install",
               "-c", "sandbox_workspace_write.network_access=false",
               "-c", "web_search=\"disabled\"",
               "-c", "memories.use_memories=false", "-c", "memories.generate_memories=false",
               "Follow TASK.md exactly. Read TASK.md, WORKER_INSTRUCTIONS.md, and the manifest, then process all listed requests and run the supplied checker once."]
    result = subprocess.run(command, cwd=bundle, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=60*60, check=False)
    # Store only process output needed for audit; worker should not print secrets.
    Path(log_path).write_text(result.stdout, encoding="utf-8")
    expected = "BLIND_WORKER_DEV40_SUCCESS requests=40 responses=40 protocol_valid=40 inference_empty=40"
    if result.returncode != 0 or expected not in result.stdout:
        raise RuntimeError("fresh_worker_failed_once; stopping_without_retry")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, default=OUTPUT)
    w = sub.add_parser("worker"); w.add_argument("--batch-dir", type=Path, default=OUTPUT / "batch_001"); w.add_argument("--codex", default="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
    e = sub.add_parser("evaluate"); e.add_argument("--batch-dir", type=Path, default=OUTPUT / "batch_001"); e.add_argument("--output", type=Path, default=OUTPUT); e.add_argument("--reports", type=Path, default=REPORTS)
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare(args.output); print(json.dumps({"record_count": 40, "prompt_sha256": manifest["prompt_sha256"], "schema_sha256": manifest["schema_sha256"], "worker_bundle": str(args.output / "batch_001"), "future_holdout_abstracts_accessed": False}, indent=2)); return 0
    if args.command == "worker":
        marker = run_worker(args.batch_dir, codex=args.codex, log_path=args.batch_dir.parent / "worker_run.log"); print(marker); return 0
    result = validate_and_measure(args.batch_dir, args.output, args.reports)
    print(json.dumps({"evaluation_role": result["evaluation_role"], "records": 40, "prompt_sha256": result["prompt_sha256"],
                      "v1_field_micro": result["v1"]["field_micro"], "v1_findings": result["v1"]["finding_metrics"],
                      "v2_fields": result["v2"]["fields"], "v2_findings": result["v2"]["findings"],
                      "errors_json": str(args.reports / "v07_dev40_i1_errors.json"), "errors_markdown": str(args.reports / "v07_dev40_i1_errors.md")}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
