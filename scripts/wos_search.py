"""Archive a bounded, repeatable WoS search, then normalize and deduplicate."""

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__:
    from .normalize_record import deduplicate_records, normalize_record
    from .wos_client import BASE_URL, WosApiError, WosStarterClient
else:
    from normalize_record import deduplicate_records, normalize_record
    from wos_client import BASE_URL, WosApiError, WosStarterClient


ROOT = Path(__file__).resolve().parent.parent


def slugify(text, max_length=60):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_length] or "wos_search"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_manifest(path, manifest):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_search(
    query, db="WOS", limit=50, max_records=200, sort_field="PY+D",
    name=None, root=ROOT, client=None, max_pages=None,
):
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 50 or max_records < 1:
        raise ValueError("limit must be 1-50; max-records must be positive")
    if max_pages is None:
        max_pages = math.ceil(max_records / limit)
    if max_pages < 1:
        raise ValueError("max-pages must be positive")
    root = Path(root).resolve()
    owns_client = client is None
    client = client or WosStarterClient()
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    run_id = f"{timestamp}_{slugify(name or query)}"
    raw_dir = root / "data" / "raw" / run_id
    output_file = root / "data" / "processed" / f"{run_id}.jsonl"
    raw_dir.mkdir(parents=True, exist_ok=False)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    manifest = {
        "schema_version": 1, "run_id": run_id, "status": "running",
        "provider": "Clarivate", "api": "Web of Science Starter API",
        "endpoint": f"{BASE_URL}/documents", "database": db, "query": query,
        "sort": sort_field, "detail": "full", "retrieved_at": utc_now(),
        "filename_timezone": "Asia/Shanghai", "total_hits": None,
        "records_collected": 0, "raw_records_fetched": 0, "duplicates_removed": 0,
        "unique_records_seen": 0, "records_omitted_by_cap": 0, "pages_fetched": 0,
        "page_size": limit, "max_records": max_records, "max_pages": max_pages,
        "stop_reason": None, "collection_complete": False, "warnings": [], "pages": [],
        "raw_directory": str(raw_dir.relative_to(root)),
        "processed_file": str(output_file.relative_to(root)),
        "manifest_file": str(manifest_path.relative_to(root)),
    }
    candidates = []
    records = []
    write_manifest(manifest_path, manifest)
    try:
        for page in range(1, max_pages + 1):
            response = client.search_documents(
                query=query, db=db, limit=limit, page=page, detail="full", sort_field=sort_field
            )
            retrieved_at = utc_now()
            raw_path = raw_dir / f"page_{page:04d}.json"
            with raw_path.open("x", encoding="utf-8") as stream:
                json.dump(response, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            manifest["pages_fetched"] += 1
            manifest["pages"].append({"page": page, "raw_file": raw_path.name, "retrieved_at": retrieved_at})
            metadata = response.get("metadata") or {}
            total = metadata.get("total")
            hits = response.get("hits")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise WosApiError("Response metadata.total is missing or invalid; raw page retained")
            if not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
                raise WosApiError("Response hits must be a list of objects; raw page retained")
            if metadata.get("page", page) != page or metadata.get("limit", limit) != limit:
                raise WosApiError("API pagination differs from requested page/limit; raw page retained")
            if manifest["total_hits"] is None:
                manifest["total_hits"] = total
                print(f"WoS total hits: {total:,}")
            elif total != manifest["total_hits"]:
                warning = "WoS total changed during pagination; inspect the per-page totals."
                if warning not in manifest["warnings"]:
                    manifest["warnings"].append(warning)
            manifest["pages"][-1].update({"reported_total": total, "records_returned": len(hits)})
            manifest["raw_records_fetched"] += len(hits)
            candidates.extend(normalize_record(hit, query, db, retrieved_at) for hit in hits)
            unique = deduplicate_records(candidates)
            records = unique[:max_records]
            manifest.update({
                "records_collected": len(records), "unique_records_seen": len(unique),
                "duplicates_removed": len(candidates) - len(unique),
                "records_omitted_by_cap": max(0, len(unique) - len(records)),
            })
            print(f"Page {page}: {len(hits)} hits | {len(records)} unique records collected")
            write_manifest(manifest_path, manifest)
            if total == 0 or page * limit >= total:
                manifest["stop_reason"] = "total_exhausted"
                break
            if not hits:
                manifest["stop_reason"] = "empty_page"
                manifest["warnings"].append("API returned an empty page before its reported total.")
                break
            if len(records) >= max_records:
                manifest["stop_reason"] = "record_cap"
                break
        else:
            manifest["stop_reason"] = "page_cap"
        manifest["status"] = "completed"
        manifest["collection_complete"] = (
            manifest["stop_reason"] == "total_exhausted"
            and manifest["records_omitted_by_cap"] == 0
            and not manifest["warnings"]
        )
    except (Exception, KeyboardInterrupt) as exc:
        manifest["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        manifest["stop_reason"] = manifest["status"]
        manifest["error"] = client.redact(exc) if hasattr(client, "redact") else str(exc)
        raise
    finally:
        with output_file.open("x", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        manifest["completed_at"] = utc_now()
        manifest["requests_attempted"] = getattr(client, "request_count", None)
        write_manifest(manifest_path, manifest)
        if owns_client:
            client.close()
    print("\nSearch completed.")
    print(f"Total WoS hits: {manifest['total_hits']:,}")
    print(f"Collected: {manifest['records_collected']:,}")
    print(f"Stop reason: {manifest['stop_reason']}")
    print(f"Raw data: {raw_dir}")
    print(f"Processed data: {output_file}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="WoS Advanced Search query")
    parser.add_argument("--db", default="WOS")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--max-records", type=int, default=200, help="Maximum unique output records")
    parser.add_argument("--max-pages", type=int, help="Page budget; default ceil(max-records / limit)")
    parser.add_argument("--sort", default="PY+D", help="WoS sortField")
    parser.add_argument("--name")
    args = parser.parse_args()
    try:
        run_search(args.query, args.db, args.limit, args.max_records, args.sort, args.name, max_pages=args.max_pages)
    except (WosApiError, ValueError, OSError) as exc:
        print(f"Search failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Search interrupted; inspect the saved manifest and partial output.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
