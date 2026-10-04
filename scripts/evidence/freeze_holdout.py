"""Freeze metadata-only stratified holdout identities before prompt iteration."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from scripts.pipeline_utils import read_jsonl, write_json


SEED = 20261004
SAMPLE_SIZE = 20
DEV_IDENTITIES = Path("data/evidence_benchmarks/gold_v04/identities.json")
DEFAULT_CORPUS = Path("data/processed/saline_paddy_v056_full_enriched_20261003.jsonl")
DEFAULT_OUTPUT = Path("data/evidence_benchmarks/v06_holdout20")


def _metadata(record):
    """Return only allowed metadata; abstract and evidence are never retained."""
    return {
        "uid": record.get("uid"), "doi": record.get("doi"), "title": record.get("title"),
        "publish_year": record.get("publish_year"),
        "document_types": sorted(set(record.get("document_types") or [])),
        "source_title": record.get("source_title"),
        "matched_queries": sorted(set(record.get("matched_queries") or [])),
        "query_match_count": record.get("query_match_count"),
    }


def _features(row):
    return {
        "year": [str(row.get("publish_year") or "unknown")],
        "document_type": row.get("document_types") or ["unknown"],
        "query_membership": row.get("matched_queries") or ["unknown"],
    }


def stratified_select(records, dev_uids, seed=SEED, sample_size=SAMPLE_SIZE):
    eligible = [_metadata(row) for row in records
                if row.get("abstract") and row.get("uid") not in dev_uids]
    if len(eligible) != 178:
        raise ValueError(f"Expected 178 abstract-bearing non-development records, found {len(eligible)}")
    if len({row["uid"] for row in eligible}) != len(eligible):
        raise ValueError("Duplicate eligible UID in source corpus")
    if sample_size != SAMPLE_SIZE:
        raise ValueError("This frozen protocol selects exactly 20 holdout identities")

    rng = random.Random(seed)
    tie_break = {row["uid"]: rng.random() for row in eligible}
    groups = ("year", "document_type", "query_membership")
    population = {group: Counter() for group in groups}
    for row in eligible:
        features = _features(row)
        for group in groups:
            population[group].update(features[group])
    targets = {group: {value: count * sample_size / len(eligible)
                       for value, count in population[group].items()} for group in groups}

    selected = []
    selected_uids = set()
    selected_features = {group: Counter() for group in groups}
    journals = set()
    remaining = {row["uid"]: row for row in eligible}
    while len(selected) < sample_size:
        scored = []
        for uid, row in remaining.items():
            features = _features(row)
            deficit_score = 0.0
            for group in groups:
                for value in features[group]:
                    target = targets[group][value]
                    deficit_score += max(0.0, target - selected_features[group][value]) / max(target, 1.0)
            deficit_score /= len(groups)
            journal = (row.get("source_title") or "unknown").casefold()
            diversity_bonus = 0.35 if journal not in journals else 0.0
            scored.append((deficit_score + diversity_bonus, tie_break[uid], uid))
        _, _, chosen_uid = max(scored)
        row = remaining.pop(chosen_uid)
        selected.append(row)
        selected_uids.add(chosen_uid)
        features = _features(row)
        for group in groups:
            selected_features[group].update(features[group])
        journals.add((row.get("source_title") or "unknown").casefold())

    selected.sort(key=lambda row: (row["publish_year"] or 0, (row["source_title"] or "").casefold(), row["uid"]))
    if selected_uids & set(dev_uids):
        raise AssertionError("Development identities leaked into holdout")
    return selected, {group: dict(counter) for group, counter in selected_features.items()}


def freeze_holdout(root=Path("."), corpus=DEFAULT_CORPUS, output=DEFAULT_OUTPUT):
    root = Path(root).resolve()
    corpus_path = root / corpus
    output_path = root / output
    dev_uids = set(json.loads((root / DEV_IDENTITIES).read_text(encoding="utf-8")))
    records = read_jsonl(corpus_path)
    abstract_count = sum(bool(row.get("abstract")) for row in records)
    if abstract_count != 185 or len(dev_uids) != 7:
        raise ValueError(f"Expected 185 abstract-bearing records and 7 development identities; got {abstract_count} and {len(dev_uids)}")
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite frozen holdout directory: {output_path}")
    selected, selected_distribution = stratified_select(records, dev_uids)
    output_path.mkdir(parents=True)
    write_json(output_path / "identities.json", selected)
    manifest = {
        "dataset_role": "hidden_holdout_identity_registry",
        "seed": SEED,
        "sample_size": SAMPLE_SIZE,
        "source_abstract_bearing_records": abstract_count,
        "development_count_excluded": len(dev_uids),
        "eligible_count": abstract_count - len(dev_uids),
        "selection_dimensions": ["publish_year", "document_types", "matched_queries", "journal_diversity"],
        "algorithm": "seeded greedy marginal-stratum-deficit balancing across year, document type, and query membership; prefer previously unrepresented journals; seeded random tie-breaks",
        "selected_strata": selected_distribution,
        "selected_unique_journals": len({(row.get("source_title") or "unknown").casefold() for row in selected}),
        "development_uids_excluded": sorted(dev_uids),
        "holdout_abstracts_included": False,
        "holdout_evidence_created": False,
        "holdout_responses_created": False,
        "HOLDOUT_FROZEN_BEFORE_PROMPT_ITERATION_2": True,
    }
    write_json(output_path / "selection_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(freeze_holdout(args.root, args.corpus, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
