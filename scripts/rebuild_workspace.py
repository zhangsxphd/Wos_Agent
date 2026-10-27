"""Rebuild a portable Dev40 workspace from versioned surviving source files."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evidence.final_evaluation import file_hash, prepare_run, review_packet, validate_reference, verify_frozen
from scripts.export_records import artifact_runtime, export_records
from scripts.extractors.schema_validator_v2 import EvidenceValidator
from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, new_run_id, read_jsonl, redact, utc_now, write_json, write_jsonl


ABSTRACTS = "data/evidence_benchmarks/v07_dev40_gold_work/abstracts.jsonl"
REFERENCE = "data/evidence_benchmarks/v07_dev40_gold_final/candidate_gold.jsonl"
SOURCE_MANIFEST = "data/evidence_benchmarks/v07_dev40_gold_final/gold_freeze_manifest.json"


def recover_records(rows, source_sha):
    records = []
    for row in rows:
        records.append({"uid": row["uid"], "doi": row.get("doi"), "title": row.get("title"),
                        "source_title": row.get("journal"), "publish_year": row.get("year"),
                        "document_types": row.get("document_types") or [], "authors": [], "author_names": [],
                        "author_keywords": [], "matched_queries": [], "query_match_count": 0,
                        "abstract": row["abstract"], "abstract_source": "versioned_dev40_snapshot_original_provider_unknown",
                        "abstract_retrieved_at": None,
                        "provenance": {"recovery_source_file": ABSTRACTS, "recovery_source_sha256": source_sha,
                                       "original_query": None, "original_retrieved_at": None,
                                       "historical_retrieval_metadata_available": False}})
    return records


def rebuild(output=None, root=ROOT, with_xlsx=False, init_env=False):
    root = Path(root).resolve()
    frozen = verify_frozen(root)
    sources = {name: root / name for name in (ABSTRACTS, REFERENCE, SOURCE_MANIFEST)}
    source_hashes = {name: file_hash(path) for name, path in sources.items()}
    abstracts = read_jsonl(sources[ABSTRACTS])
    references = read_jsonl(sources[REFERENCE])
    if len(abstracts) != 40 or len(references) != 40:
        raise ValueError("surviving_dev40_must_have_40_records")
    secrets = known_secrets(root)
    records = recover_records(abstracts, source_hashes[ABSTRACTS])
    validate_reference(records, references)
    assert_safe([records, references], secrets)
    if with_xlsx:
        artifact_runtime()  # Check capability before creating output.
    target = Path(output).resolve() if output else root / "data/recovery" / new_run_id("recovered_dev40")
    archive = target.with_name(target.name + ".zip")
    checksum = archive.with_suffix(".zip.sha256")
    if target.exists() or archive.exists() or checksum.exists():
        raise FileExistsError("rebuild_requires_new_output_path")
    target.mkdir(parents=True)
    try:
        for name, path in sources.items():
            destination = target / "source_snapshots" / Path(name).name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, destination)
        canonical = target / "records.jsonl"
        reference = target / "reference.jsonl"
        write_jsonl(canonical, records, secrets)
        # Keep surviving reference bytes and provenance separate from predictions.
        shutil.copyfile(sources[REFERENCE], reference)
        validator = EvidenceValidator()
        normalized = [validator.normalize(g, r) for g, r in zip(references, records)]
        evidence = target / "reference_normalized.jsonl"
        write_jsonl(evidence, normalized, secrets)
        write_json(target / "records.manifest.json", {"status": "recovered_versioned_development_snapshot",
                   "records": 40, "searches": [], "collection_complete": False,
                   "original_query_metadata_available": False, "source_sha256": source_hashes[ABSTRACTS]}, secrets)
        review_packet(canonical, reference, target / "owner_review", root)
        prepared = prepare_run(canonical, target / "extraction_run", reference_file=reference, root=root)
        formats = ("csv", "xlsx") if with_xlsx else ("csv",)
        export_records(canonical, target / "exports/recovered_dev40", formats=formats, root=root,
                       searches=[], include_abstracts=True, evidence_file=evidence, evidence_validator_version=2)
        write_json(target / "validation.json", {"records": 40, "schema_valid": 40, "grounding_valid": 40,
                   "validator": "EvidenceValidator v2", "target_predictions_created": False,
                   "quality_gate": "NOT_EVALUATED", "reference_status": "ai_assisted_development_reference",
                   "owner_review_complete": False}, secrets)
        (target / "START_HERE.md").write_text(
            "# 已恢复的 40 篇开发数据\n\n"
            "40 篇真实摘要及已有证据参考已从 Git 版本化文件恢复，均通过 Validator v2。\n\n"
            "- records.jsonl：重建的书目与摘要；原 query、原获取时间和未知作者信息保持缺失。\n"
            "- reference.jsonl：原有 AI 辅助开发参考，保留原始字节；不是新预测，也不是新独立留出集。\n"
            "- reference_normalized.jsonl：用于导出的规范化参考副本。\n"
            "- owner_review/review.md：40 篇逐篇审阅包；decisions.template.jsonl 的决定仍待人工填写。\n"
            "- extraction_run/worker：40 个冻结 final prompt 请求，responses 为空，待实际提取。\n"
            "- exports：书目 CSV 与可选 Excel，展示的是恢复的开发参考。\n"
            "- manifest.json：来源、冻结哈希、文件校验值和丢失实验状态。\n\n"
            "旧的 Holdout30、历史 Worker 输出和全量检索池没有恢复。当前没有新的提取准确率或生产通过结论。\n",
            encoding="utf-8")
        if any(file_hash(sources[name]) != old_hash for name, old_hash in source_hashes.items()):
            raise ValueError("recovery_source_changed_during_run")
        manifest = {"format": "wos-recovery-1", "created_at": utc_now(), "records": 40,
                    "source_hashes": source_hashes, "frozen_components": frozen,
                    "dataset_role": "recovered_development_only", "historical_experiment_restored": False,
                    "lost_artifacts": ["original_full_corpus", "historical_worker_predictions", "original_holdout30_registry",
                                       "original_holdout30_candidate_and_review", "original_final_system_freeze_manifest"],
                    "owner_review_complete": False, "target_predictions_created": False, "quality_gate": "NOT_EVALUATED",
                    "worker_requests": prepared["records"], "export_formats": list(formats),
                    "files": {str(p.relative_to(target)): file_hash(p) for p in sorted(target.rglob("*")) if p.is_file()}}
        write_json(target / "manifest.json", manifest, secrets)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    bundle.write(path, arcname=str(Path(target.name) / path.relative_to(target)))
        checksum.write_text(file_hash(archive) + "  " + archive.name + "\n", encoding="utf-8")
    except Exception:
        # Only this newly created run and its own archive are removed on failure.
        shutil.rmtree(target)
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
        raise
    if init_env and not (root / ".env").exists():
        with (root / ".env").open("x", encoding="utf-8") as stream:
            os.chmod(root / ".env", 0o600)
            stream.write((root / ".env.example").read_text(encoding="utf-8"))
    return {"output": str(target), "archive": str(archive), "archive_sha256": file_hash(archive),
            "records": 40, "worker_requests": 40, "reference_valid": 40,
            "quality_gate": "NOT_EVALUATED", "historical_experiment_restored": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--with-xlsx", action="store_true")
    parser.add_argument("--init-env", action="store_true", help="Create a blank private .env only if absent")
    args = parser.parse_args()
    try:
        print(json.dumps(rebuild(args.output, with_xlsx=args.with_xlsx, init_env=args.init_env), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print("Rebuild failed: " + redact(exc, known_secrets()), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
