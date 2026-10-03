"""Run enabled YAML queries sequentially; collect all pages unless explicitly capped."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

if __package__:
    from .merge_searches import merge_records, prepare_record
    from .normalize_record import normalize_record
    from .pipeline_utils import ROOT, assert_safe, display_path, known_secrets, new_run_id, project_path, redact, utc_now, write_json, write_jsonl
    from .wos_client import BASE_URL, WosApiError, WosStarterClient
else:
    from merge_searches import merge_records, prepare_record
    from normalize_record import normalize_record
    from pipeline_utils import ROOT, assert_safe, display_path, known_secrets, new_run_id, project_path, redact, utc_now, write_json, write_jsonl
    from wos_client import BASE_URL, WosApiError, WosStarterClient


class UniqueSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in mapping:
                    raise ValueError(f"Duplicate YAML mapping key at line {key_node.start_mark.line + 1}")
                mapping[key] = self.construct_object(value_node, deep=deep)
            except TypeError:
                raise ValueError("YAML mapping keys must be scalar values") from None
        return mapping


def positive_cap(value, name):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive integer or null")
    return value


def load_plan(path):
    try:
        data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueSafeLoader)
    except yaml.YAMLError:
        raise ValueError("Invalid or unsafe YAML search plan") from None
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError("Plan must be a mapping containing a queries list")
    if type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
        raise ValueError("Only search plan version 1 is supported")
    ids = set()
    for query in data["queries"]:
        if not isinstance(query, dict):
            raise ValueError("Every query must be a mapping")
        for field in ("id", "description", "query", "database", "sort"):
            if not isinstance(query.get(field), str) or not query[field].strip():
                raise ValueError(f"Every query requires a nonempty {field}")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", query["id"]):
            raise ValueError("Query IDs must start with a letter and contain only letters, digits, _ or -")
        if query["id"].casefold() in ids:
            raise ValueError("Query IDs must be unique, including case")
        ids.add(query["id"].casefold())
        if type(query.get("enabled")) is not bool:
            raise ValueError("Query enabled must be a YAML boolean")
        positive_cap(query.get("max_records"), "query max_records")
        positive_cap(query.get("max_pages"), "query max_pages")
    return data


def effective_cap(command_cap, query_cap):
    values = [value for value in (command_cap, query_cap) if value is not None]
    return min(values) if values else None


def run_query(query, run_id, index, client, root, page_size, max_records=None, max_pages=None):
    qid = query["id"]
    raw_dir = root / "data/raw/plans" / run_id / f"{index:02d}_{qid}"
    raw_dir.mkdir(parents=True, exist_ok=False)
    output = root / "data/processed/plans" / run_id / f"{qid}.jsonl"
    manifest_path = raw_dir / "manifest.json"
    secrets = known_secrets(root, extra=(getattr(client, "api_key", None),))
    record_cap = effective_cap(max_records, query.get("max_records"))
    page_cap = effective_cap(max_pages, query.get("max_pages"))
    request_start = client.request_count
    manifest = {
        "schema_version": 2, "run_id": run_id, "query_id": qid,
        "description": query["description"], "query": query["query"], "exact_query": query["query"],
        "database": query["database"], "sort": query["sort"], "detail": "full",
        "endpoint": f"{BASE_URL}/documents", "status": "running", "retrieved_at": utc_now(),
        "total_hits": None, "collected": 0, "records_collected": 0,
        "raw_records_fetched": 0, "unique_records_seen": 0, "duplicates_removed": 0,
        "records_omitted_by_cap": 0, "pages_fetched": 0, "pages": [],
        "page_size": page_size, "max_records": record_cap, "max_pages": page_cap,
        "truncated": True, "collection_complete": False, "stop_reason": None,
        "warnings": [], "raw_directory": display_path(raw_dir, root),
        "processed_file": display_path(output, root), "manifest_file": display_path(manifest_path, root),
    }
    write_json(manifest_path, manifest, secrets)
    records, candidates, seen_pages = [], [], set()
    page = 1
    try:
        while True:
            response = client.search_documents(query=query["query"], db=query["database"], limit=page_size, page=page, detail="full", sort_field=query["sort"])
            retrieved_at = utc_now()
            raw_file = raw_dir / f"page_{page:04d}.json"
            write_json(raw_file, response, secrets)
            manifest["pages_fetched"] += 1
            manifest["pages"].append({"page": page, "raw_file": raw_file.name, "retrieved_at": retrieved_at})
            metadata, hits = response.get("metadata") or {}, response.get("hits")
            total = metadata.get("total")
            if type(total) is not int or total < 0 or not isinstance(hits, list) or any(not isinstance(h, dict) for h in hits):
                raise WosApiError("Invalid total/hits metadata; raw page retained")
            if metadata.get("page", page) != page or metadata.get("limit", page_size) != page_size:
                raise WosApiError("API pagination differs from the requested page/limit")
            manifest["pages"][-1].update(reported_total=total, records_returned=len(hits))
            manifest["raw_records_fetched"] += len(hits)
            if manifest["total_hits"] is None:
                manifest["total_hits"] = total
            elif total != manifest["total_hits"] and not manifest["warnings"]:
                manifest["warnings"].append("WoS total changed during pagination; result completeness is uncertain.")
            fingerprint = hashlib.sha256(json.dumps(hits, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if hits and fingerprint in seen_pages:
                raise WosApiError("API repeated a previously fetched page; raw page retained")
            seen_pages.add(fingerprint)
            if not hits and total > (page - 1) * page_size:
                raise WosApiError("API returned an empty page before its reported total")
            for hit in hits:
                record = normalize_record(hit, query["query"], query["database"], retrieved_at)
                record["provenance"].update(
                    query_id=qid, description=query["description"], run_id=run_id,
                    raw_file=display_path(raw_file, root), search_manifest_file=display_path(manifest_path, root),
                )
                candidates.append(prepare_record(record))
            unique = merge_records(candidates)
            records = unique if record_cap is None else unique[:record_cap]
            manifest.update(
                collected=len(records), records_collected=len(records), unique_records_seen=len(unique),
                duplicates_removed=len(candidates) - len(unique), records_omitted_by_cap=len(unique) - len(records),
            )
            write_json(manifest_path, manifest, secrets, overwrite=True)
            if total == 0 or page * page_size >= total:
                manifest["stop_reason"] = "total_exhausted"
                break
            if record_cap is not None and len(records) >= record_cap:
                manifest["stop_reason"] = "record_cap"
                break
            if page_cap is not None and page >= page_cap:
                manifest["stop_reason"] = "page_cap"
                break
            page += 1
        manifest["status"] = "completed"
        manifest["collection_complete"] = manifest["stop_reason"] == "total_exhausted" and not manifest["records_omitted_by_cap"] and not manifest["warnings"]
        manifest["truncated"] = not manifest["collection_complete"]
    except (Exception, KeyboardInterrupt) as exc:
        manifest.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", stop_reason="interrupted" if isinstance(exc, KeyboardInterrupt) else "error", error=redact(exc, secrets), truncated=True)
    finally:
        write_jsonl(output, records, secrets)
        manifest.update(completed_at=utc_now(), requests_attempted=client.request_count - request_start)
        write_json(manifest_path, manifest, secrets, overwrite=True)
    print(f"{qid}: total_hits={manifest['total_hits']} collected={manifest['collected']} pages={manifest['pages_fetched']} truncated={str(manifest['truncated']).lower()} status={manifest['status']}")
    return manifest


def run_plan(plan_path, root=ROOT, client=None, page_size=50, max_records=None, max_pages=None):
    root = Path(root).resolve()
    plan_path = project_path(plan_path, root)
    secrets = known_secrets(root, extra=(getattr(client, "api_key", None),))
    assert_safe(plan_path.read_text(encoding="utf-8"), secrets)
    plan = load_plan(plan_path)
    if type(page_size) is not int or not 1 <= page_size <= 50:
        raise ValueError("limit must be 1-50")
    positive_cap(max_records, "max-records")
    positive_cap(max_pages, "max-pages")
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(plan.get("name") or plan_path.stem))[:60]
    run_id = new_run_id(name or "search_plan")
    plan_manifest_path = root / "data/raw/plans" / run_id / "plan_manifest.json"
    manifest = {
        "schema_version": 2, "run_id": run_id, "status": "running", "retrieved_at": utc_now(),
        "plan_file": display_path(plan_path, root), "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "plan_snapshot": plan, "page_size": page_size, "max_records": max_records, "max_pages": max_pages,
        "queries": [], "skipped_queries": [q["id"] for q in plan["queries"] if not q["enabled"]],
        "manifest_file": display_path(plan_manifest_path, root),
    }
    write_json(plan_manifest_path, manifest, secrets)
    owns_client = client is None
    try:
        client = client or WosStarterClient()
        for index, query in enumerate(plan["queries"], 1):
            if not query["enabled"]:
                continue
            result = run_query(query, run_id, index, client, root, page_size, max_records, max_pages)
            manifest["queries"].append({key: result[key] for key in ("query_id", "description", "exact_query", "retrieved_at", "total_hits", "collected", "pages_fetched", "truncated", "status", "processed_file", "manifest_file")})
            write_json(plan_manifest_path, manifest, secrets, overwrite=True)
            if result["status"] == "interrupted":
                manifest["status"] = "interrupted"
                break
        else:
            manifest["status"] = "completed_with_errors" if any(q["status"] == "failed" for q in manifest["queries"]) else "completed"
    except Exception as exc:
        manifest.update(status="failed", error=redact(exc, known_secrets(root)))
    finally:
        manifest["completed_at"] = utc_now()
        manifest["truncated"] = manifest["status"] != "completed" or any(q["truncated"] for q in manifest["queries"])
        write_json(plan_manifest_path, manifest, secrets, overwrite=True)
        if owns_client and client is not None:
            client.close()
    print(f"Plan manifest: {plan_manifest_path}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--max-records", type=int, help="Explicit per-query unique record cap; default unlimited")
    parser.add_argument("--max-pages", type=int, help="Explicit per-query page cap; default unlimited")
    args = parser.parse_args()
    try:
        manifest = run_plan(args.plan, page_size=args.limit, max_records=args.max_records, max_pages=args.max_pages)
        return 130 if manifest["status"] == "interrupted" else (0 if manifest["status"] == "completed" else 1)
    except (ValueError, OSError) as exc:
        print(f"Search plan failed: {redact(exc, known_secrets())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
