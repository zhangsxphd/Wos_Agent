"""Freeze metadata-only v0.7 development and future holdout identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = 20261005
CORPUS = Path("data/processed/saline_paddy_v056_full_enriched_20261003.jsonl")
OLD_DEV = Path("data/evidence_benchmarks/gold_v04/identities.json")
OLD_HISTORICAL = Path("data/evidence_benchmarks/v06_holdout20/identities.json")
DEV_DIR = Path("data/evidence_benchmarks/v07_dev40")
FUTURE_DIR = Path("data/evidence_benchmarks/v07_future_holdout30")
METADATA_FIELDS = ("uid", "doi", "title", "publish_year", "document_types", "source_title",
                   "matched_queries", "query_match_count")


def _skip_ws(data: bytes, i: int) -> int:
    while i < len(data) and data[i] in b" \t\r\n":
        i += 1
    return i


def _skip_string(data: bytes, i: int) -> int:
    if data[i] != 34:
        raise ValueError("expected_json_string")
    i += 1
    while i < len(data):
        if data[i] == 92:
            i += 2
        elif data[i] == 34:
            return i + 1
        else:
            i += 1
    raise ValueError("unterminated_json_string")


def _skip_value(data: bytes, i: int) -> int:
    i = _skip_ws(data, i)
    if i >= len(data):
        raise ValueError("missing_json_value")
    if data[i] == 34:
        return _skip_string(data, i)
    if data[i] in (123, 91):
        opening = data[i]
        closing = 125 if opening == 123 else 93
        i += 1
        while True:
            i = _skip_ws(data, i)
            if i >= len(data):
                raise ValueError("unterminated_json_container")
            if data[i] == closing:
                return i + 1
            if data[i] in (44, 58):
                i += 1
            elif data[i] == 34:
                i = _skip_string(data, i)
            else:
                i = _skip_value(data, i)
    while i < len(data) and data[i] not in b",}] \t\r\n":
        i += 1
    return i


def metadata_only_row(line: bytes) -> tuple[dict, bool]:
    """Parse allowlisted top-level metadata without decoding abstract contents."""
    data = line.strip()
    if not data:
        raise ValueError("empty_jsonl_record")
    i = _skip_ws(data, 0)
    if data[i] != 123:
        raise ValueError("expected_json_object")
    i += 1
    values = {}
    has_abstract = False
    while True:
        i = _skip_ws(data, i)
        if i >= len(data):
            raise ValueError("unterminated_json_object")
        if data[i] == 125:
            break
        key_end = _skip_string(data, i)
        key = json.loads(data[i:key_end].decode("utf-8"))
        i = _skip_ws(data, key_end)
        if data[i] != 58:
            raise ValueError("expected_json_colon")
        value_start = _skip_ws(data, i + 1)
        value_end = _skip_value(data, value_start)
        raw = data[value_start:value_end]
        if key == "abstract":
            # Only distinguish null/empty from present text; do not decode or retain it.
            has_abstract = raw not in (b"null", b'""')
        elif key in METADATA_FIELDS:
            values[key] = json.loads(raw.decode("utf-8"))
        i = _skip_ws(data, value_end)
        if i < len(data) and data[i] == 44:
            i += 1
    missing = set(METADATA_FIELDS) - set(values)
    if missing:
        raise ValueError("metadata_fields_missing:" + ",".join(sorted(missing)))
    return values, has_abstract


def read_metadata_corpus(path: Path):
    records = []
    total = 0
    abstract_bearing = 0
    with Path(path).open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            total += 1
            row, has_abstract = metadata_only_row(line)
            if has_abstract:
                abstract_bearing += 1
                records.append(row)
    return records, {"total_records": total, "abstract_bearing_records": abstract_bearing}


def _uids(path: Path):
    body = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(body, dict):
        body = body.get("identities", body.get("uids"))
    if not isinstance(body, list):
        raise ValueError("identity_registry_shape")
    return {row.get("uid") if isinstance(row, dict) else str(row) for row in body}


def _features(row):
    return {
        "year": [str(row.get("publish_year") or "unknown")],
        "document_type": row.get("document_types") or ["unknown"],
        "query_membership": row.get("matched_queries") or ["unknown"],
    }


def stratified_select(records, sample_size: int, seed: int):
    if sample_size <= 0 or sample_size > len(records):
        raise ValueError("invalid_sample_size")
    rng = random.Random(seed)
    tie_break = {row["uid"]: rng.random() for row in records}
    groups = ("year", "document_type", "query_membership")
    population = {group: Counter() for group in groups}
    for row in records:
        for group in groups:
            population[group].update(_features(row)[group])
    targets = {group: {value: count * sample_size / len(records)
                       for value, count in population[group].items()} for group in groups}
    selected, remaining = [], {row["uid"]: row for row in records}
    observed = {group: Counter() for group in groups}
    journals = set()
    while len(selected) < sample_size:
        ranked = []
        for uid, row in remaining.items():
            score = 0.0
            features = _features(row)
            for group in groups:
                for value in features[group]:
                    target = targets[group][value]
                    score += max(0.0, target - observed[group][value]) / max(target, 1.0)
            score /= len(groups)
            journal = (row.get("source_title") or "unknown").casefold()
            score += 0.35 if journal not in journals else 0.0
            ranked.append((score, tie_break[uid], uid))
        _, _, chosen_uid = max(ranked)
        row = remaining.pop(chosen_uid)
        selected.append(row)
        for group in groups:
            observed[group].update(_features(row)[group])
        journals.add((row.get("source_title") or "unknown").casefold())
    selected.sort(key=lambda row: (row.get("publish_year") or 0,
                                   (row.get("source_title") or "").casefold(), row["uid"]))
    strata = {group: dict(counter) for group, counter in observed.items()}
    return selected, strata


def _sha_bytes(value: bytes):
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_datasets(root: Path = ROOT, corpus: Path = CORPUS, seed: int = SEED):
    root = Path(root).resolve()
    corpus_path = (root / corpus).resolve()
    dev_dir, future_dir = root / DEV_DIR, root / FUTURE_DIR
    if dev_dir.exists() or future_dir.exists():
        raise FileExistsError("Refusing to overwrite a v0.7 frozen dataset directory")
    records, counts = read_metadata_corpus(corpus_path)
    if counts["abstract_bearing_records"] != 185:
        raise ValueError("expected_185_abstract_bearing_metadata_records")
    old_dev, old_historical = _uids(root / OLD_DEV), _uids(root / OLD_HISTORICAL)
    old_overlap = old_dev & old_historical
    if old_overlap:
        raise ValueError("old_development_historical_overlap")
    excluded = old_dev | old_historical
    eligible = sorted((row for row in records if row["uid"] not in excluded), key=lambda r: r["uid"])
    if len(eligible) != 158:
        raise ValueError("expected_158_eligible_metadata_records")
    dev, dev_strata = stratified_select(eligible, 40, seed)
    dev_uids = {row["uid"] for row in dev}
    future_population = [row for row in eligible if row["uid"] not in dev_uids]
    future, future_strata = stratified_select(future_population, 30, seed)
    future_uids = {row["uid"] for row in future}
    if len(dev_uids) != 40 or len(future_uids) != 30 or dev_uids & future_uids:
        raise ValueError("v07_dataset_identity_invariant")
    metadata_rows = sorted(records, key=lambda r: r["uid"])
    metadata_digest = _sha_bytes(json.dumps(metadata_rows, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8"))
    source_hash = _sha_file(corpus_path)

    def build(output_dir, selected, role, strata, population_count, excluded_for_role):
        output_dir.mkdir(parents=True)
        identities = output_dir / "identities.json"
        manifest_path = output_dir / "selection_manifest.json"
        identities.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "dataset_role": role, "seed": seed, "sample_size": len(selected),
            "eligible_population_count": population_count,
            "excluded_identity_count": len(excluded_for_role), "excluded_uids": sorted(excluded_for_role),
            "old_development_count": len(old_dev), "historical_holdout_count": len(old_historical),
            "selection_algorithm": "seeded greedy marginal-deficit balancing across publication year, document type, and query membership, with a journal-diversity bonus and seeded random tie-breaks",
            "selection_dimensions": ["publish_year", "document_types", "matched_queries", "journal_diversity"],
            "selected_strata": strata, "selected_unique_journals": len({(r.get("source_title") or "unknown").casefold() for r in selected}),
            "source_corpus_sha256": source_hash, "eligible_metadata_sha256": metadata_digest,
            "identities_sha256": _sha_bytes(identities.read_bytes()),
            "abstracts_materialized": False,
        }
        if role == "v07_future_independent_holdout":
            manifest["V07_FUTURE_HOLDOUT_FROZEN_BEFORE_TUNING"] = True
            manifest["future_holdout_abstracts_accessed"] = False
            manifest["gold_created"] = False
            manifest["extraction_run"] = False
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    dev_manifest = build(dev_dir, dev, "v07_development", dev_strata, len(eligible), excluded)
    future_excluded = excluded | dev_uids
    future_manifest = build(future_dir, future, "v07_future_independent_holdout", future_strata, len(future_population), future_excluded)
    return {"counts": counts, "eligible_population": len(eligible), "old_development_overlap": len(dev_uids & old_dev),
            "historical_holdout_overlap": len(future_uids & old_historical), "dev_future_overlap": len(dev_uids & future_uids),
            "development": dev_manifest, "future_holdout": future_manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(freeze_datasets(args.root, args.corpus, args.seed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
