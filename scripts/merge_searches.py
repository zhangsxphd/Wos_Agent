"""Merge canonical JSONL records while preserving every query provenance."""

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

if __package__:
    from .normalize_record import normalize_doi, normalize_title
    from .pipeline_utils import ROOT, assert_safe, display_path, known_secrets, project_path, read_jsonl, redact, utc_now, write_json, write_jsonl
else:
    from normalize_record import normalize_doi, normalize_title
    from pipeline_utils import ROOT, assert_safe, display_path, known_secrets, project_path, read_jsonl, redact, utc_now, write_json, write_jsonl


def legacy_query_id(provenance):
    value = json.dumps([provenance.get("database"), provenance.get("query")], ensure_ascii=False)
    return "legacy_" + hashlib.sha256(value.encode()).hexdigest()[:12]


def unique_history(history):
    result, seen = [], set()
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("provenance_history entries must be objects")
        key = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(copy.deepcopy(entry))
    return result


def prepare_record(record, query_id=None, source_file=None):
    record = copy.deepcopy(record)
    record["doi"] = normalize_doi(record.get("doi"))
    for field in ("abstract", "abstract_source", "abstract_retrieved_at"):
        record.setdefault(field, None)
    matched = record.get("matched_queries") or []
    if not isinstance(matched, list) or any(not isinstance(q, str) or not q for q in matched):
        raise ValueError("matched_queries must be a list of nonempty query IDs")
    history = unique_history(record.get("provenance_history") or [])
    provenance = record.get("provenance") or {}
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be an object")
    own_id = provenance.get("query_id")
    if query_id and own_id and query_id != own_id:
        raise ValueError("Explicit query ID conflicts with record provenance")
    own_id = query_id or own_id or (matched[0] if len(matched) == 1 else None)
    if not own_id and provenance.get("query"):
        own_id = legacy_query_id(provenance)
    entry = copy.deepcopy(provenance)
    if own_id:
        entry["query_id"] = own_id
    if entry:
        existing = next((h for h in history if all(h.get(k) == v for k, v in entry.items())), None)
        if existing is not None:
            if source_file:
                existing.setdefault("processed_source_file", source_file)
        else:
            if source_file:
                entry.setdefault("processed_source_file", source_file)
            history.append(entry)
    ids = list(dict.fromkeys(matched + [h["query_id"] for h in history if h.get("query_id")]))
    record.update(matched_queries=ids, query_match_count=len(ids), provenance_history=unique_history(history))
    return record


def identity_keys(record):
    keys = []
    doi = normalize_doi(record.get("doi"))
    uid = (record.get("uid") or "").strip().upper()
    if doi:
        keys.append(f"doi:{doi}")
    if uid:
        keys.append(f"uid:{uid}")
    if keys:
        return keys
    title, year = normalize_title(record.get("title")), record.get("publish_year")
    return [f"titleyear:{title}|{year}"] if title and year is not None else []


def merge_records(records):
    records = [prepare_record(record) for record in records]
    parents = list(range(len(records)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners = {}
    for index, record in enumerate(records):
        for key in identity_keys(record):
            if key in owners:
                left, right = find(index), find(owners[key])
                parents[max(left, right)] = min(left, right)
            else:
                owners[key] = index
    groups = {}
    for index, record in enumerate(records):
        root = find(index)
        if root not in groups:
            groups[root] = copy.deepcopy(record)
            continue
        kept = groups[root]
        for field, value in record.items():
            if field in {"matched_queries", "query_match_count", "provenance_history"}:
                continue
            if (kept.get(field) is None or kept.get(field) == [] or kept.get(field) == "") and value is not None:
                kept[field] = copy.deepcopy(value)
        kept["matched_queries"] = list(dict.fromkeys(kept["matched_queries"] + record["matched_queries"]))
        kept["query_match_count"] = len(kept["matched_queries"])
        kept["provenance_history"] = unique_history(kept["provenance_history"] + record["provenance_history"])
    return list(groups.values())


def search_row(manifest, query_id=None, source_manifest=None):
    return {
        "query_id": query_id or manifest.get("query_id") or legacy_query_id(manifest),
        "description": manifest.get("description"),
        "exact_query": manifest.get("exact_query", manifest.get("query")),
        "database": manifest.get("database"),
        "retrieved_at": manifest.get("retrieved_at"),
        "total_hits": manifest.get("total_hits"),
        "collected": manifest.get("collected", manifest.get("records_collected")),
        "pages": manifest.get("pages_fetched"),
        "truncated": manifest.get("truncated", not manifest.get("collection_complete", False)),
        "status": manifest.get("status"),
        "stop_reason": manifest.get("stop_reason"),
        "manifest_file": source_manifest,
    }


def summarize_records(records, searches=()):
    years = Counter(str(r["publish_year"]) if r.get("publish_year") is not None else "Unknown" for r in records)
    journals = Counter(r.get("source_title") or "Unknown" for r in records)
    queries = list(dict.fromkeys([s["query_id"] for s in searches] + [q for r in records for q in r.get("matched_queries", [])]))
    query_counts = {q: sum(q in r.get("matched_queries", []) for r in records) for q in queries}
    overlap = [[sum(a in r.get("matched_queries", []) and b in r.get("matched_queries", []) for r in records) for b in queries] for a in queries]
    return {
        "unique_records": len(records),
        "records_per_year": [{"year": year, "records": count} for year, count in sorted(years.items(), key=lambda item: (item[0] == "Unknown", item[0]))],
        "records_per_journal": [{"journal": journal, "records": count} for journal, count in sorted(journals.items(), key=lambda item: (-item[1], item[0]))],
        "records_by_query": [{"query_id": q, "records": query_counts[q]} for q in queries],
        "overlap": {"query_ids": queries, "matrix": overlap},
    }


def collect_searches(input_files, records_by_file, root=ROOT, query_ids=None):
    searches, seen = [], set()
    for index, path in enumerate(input_files):
        paths = []
        sidecar = path.with_suffix(".manifest.json")
        if sidecar.is_file():
            searches.extend(json.loads(sidecar.read_text(encoding="utf-8")).get("searches", []))
        legacy = Path(root) / "data/raw" / path.stem / "manifest.json"
        if legacy.is_file():
            paths.append(legacy)
        for record in records_by_file[index]:
            for entry in record.get("provenance_history", []) + [record.get("provenance") or {}]:
                if entry.get("search_manifest_file"):
                    paths.append(project_path(entry["search_manifest_file"], root))
        for manifest_path in paths:
            if manifest_path in seen:
                continue
            seen.add(manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            searches.append(search_row(manifest, query_ids[index] if query_ids else None, display_path(manifest_path, root)))
    return unique_history(searches)


def merge_searches(input_files, output_file, query_ids=None, searches=None, root=ROOT):
    root = Path(root).resolve()
    input_files = [project_path(path, root).resolve() for path in input_files]
    output_file = project_path(output_file, root).resolve()
    sidecar = output_file.with_suffix(".manifest.json")
    if output_file.suffix != ".jsonl":
        raise ValueError("Canonical merged output must use .jsonl")
    if output_file.exists() or sidecar.exists():
        raise FileExistsError("Merge output already exists; choose a new output name")
    if query_ids and len(query_ids) != len(input_files):
        raise ValueError("Provide one query ID per input file")
    secrets = known_secrets(root)
    raw_records = [read_jsonl(path) for path in input_files]
    assert_safe(raw_records, secrets)
    prepared = []
    for index, records in enumerate(raw_records):
        prepared.extend(prepare_record(record, query_ids[index] if query_ids else None, display_path(input_files[index], root)) for record in records)
    merged = merge_records(prepared)
    searches = searches if searches is not None else collect_searches(input_files, raw_records, root, query_ids)
    manifest = {
        "schema_version": 2, "status": "completed", "merged_at": utc_now(),
        "input_files": [display_path(path, root) for path in input_files],
        "input_records": len(prepared), "unique_records": len(merged),
        "duplicates_removed": len(prepared) - len(merged),
        "processed_file": display_path(output_file, root), "searches": searches,
        "summary": summarize_records(merged, searches),
    }
    assert_safe(manifest, secrets)
    write_jsonl(output_file, merged, secrets)
    write_json(sidecar, manifest, secrets)
    print(f"Merged: {len(prepared)} input records -> {len(merged)} unique records")
    print(f"Canonical JSONL: {output_file}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*")
    parser.add_argument("--output", required=True)
    parser.add_argument("--query-ids", nargs="+")
    parser.add_argument("--plan-manifest")
    args = parser.parse_args()
    try:
        inputs, searches = args.inputs, None
        if args.plan_manifest:
            if inputs or args.query_ids:
                raise ValueError("Use either inputs/query-ids or --plan-manifest")
            plan = json.loads(project_path(args.plan_manifest).read_text(encoding="utf-8"))
            entries = plan["queries"]
            inputs = [entry["processed_file"] for entry in entries]
            searches = [search_row(json.loads(project_path(entry["manifest_file"]).read_text(encoding="utf-8")), source_manifest=entry["manifest_file"]) for entry in entries]
        if not inputs:
            raise ValueError("Provide JSONL inputs or a plan manifest")
        merge_searches(inputs, args.output, args.query_ids, searches)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Merge failed: {redact(exc, known_secrets())}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
