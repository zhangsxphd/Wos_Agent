"""Export canonical JSONL to CSV/XLSX, with an optional full-text Abstracts sheet."""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

if __package__:
    from .merge_searches import collect_searches, merge_records, prepare_record, search_row, summarize_records
    from .pipeline_utils import ROOT, SECRET_ENV_NAMES, assert_safe, display_path, known_secrets, project_path, read_jsonl, redact, utc_now, write_json
else:
    from merge_searches import collect_searches, merge_records, prepare_record, search_row, summarize_records
    from pipeline_utils import ROOT, SECRET_ENV_NAMES, assert_safe, display_path, known_secrets, project_path, read_jsonl, redact, utc_now, write_json


RECORD_COLUMNS = [
    "uid", "title", "publish_year", "source_title", "document_types", "authors",
    "doi", "issn", "eissn", "author_keywords", "times_cited_wos", "matched_queries",
    "query_match_count", "record_url", "abstract", "abstract_source", "abstract_retrieved_at",
    "abstract_available", "abstract_status",
]
XLSX_RECORD_COLUMNS = RECORD_COLUMNS[:14] + ["abstract_available", "abstract_source", "abstract_status", "abstract_retrieved_at"]
ABSTRACT_COLUMNS = ["uid", "doi", "title", "abstract", "abstract_source", "abstract_status"]
SEARCH_COLUMNS = ["query_id", "description", "exact_query", "retrieved_at", "total_hits", "collected", "pages", "truncated", "status", "stop_reason", "retrieved_at_timezone"]


def literal_text(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def record_row(record, columns=RECORD_COLUMNS):
    row = dict(record)
    row["abstract_available"] = bool(record.get("abstract"))
    row["abstract_status"] = (record.get("abstract_enrichment") or {}).get("status", "not_enriched")
    for field in ("document_types", "author_keywords", "matched_queries"):
        row[field] = "; ".join(record.get(field) or [])
    row["authors"] = "; ".join(record.get("author_names") or [a["display_name"] for a in record.get("authors", []) if a.get("display_name")])
    return [literal_text(row.get(field)) for field in columns]


def artifact_runtime():
    dependency_root = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
    node = Path(os.getenv("WOS_ARTIFACT_NODE", str(dependency_root / "node/bin/node")))
    modules = Path(os.getenv("WOS_ARTIFACT_MODULES", str(dependency_root / "node/node_modules")))
    if not node.is_file() or not (modules / "@oai/artifact-tool/package.json").is_file():
        raise RuntimeError("Codex spreadsheet runtime is unavailable; configure WOS_ARTIFACT_NODE and WOS_ARTIFACT_MODULES, or export CSV")
    return node, modules


def read_xlsx_tables(path):
    """Independent readback of saved cell values; standard-library XML only."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        links = {node.attrib["Id"]: node.attrib["Target"] for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        sheets = {}
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            target = links[sheet.attrib[f"{{{rel_ns}}}id"]]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            xml = ET.fromstring(archive.read(target))
            rows = []
            for row in xml.findall("m:sheetData/m:row", ns):
                cells = {}
                for cell in row.findall("m:c", ns):
                    kind = cell.get("t")
                    value = cell.findtext("m:v", default="", namespaces=ns)
                    if kind == "s":
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(cell.find("m:is", ns).itertext())
                    elif kind == "b":
                        value = value == "1"
                    elif value and kind not in {"str", "e", "d"}:
                        value = float(value) if "." in value or "E" in value.upper() else int(value)
                    else:
                        value = value or None
                    cells[cell.attrib["r"]] = value
                rows.append(cells)
            sheets[sheet.attrib["name"]] = rows
        return sheets


def build_xlsx(payload, output, secrets=(), preview_dir=None):
    node, modules = artifact_runtime()
    with tempfile.TemporaryDirectory(prefix="wos-xlsx-") as temporary:
        temp_dir = Path(temporary)
        (temp_dir / "node_modules").symlink_to(modules, target_is_directory=True)
        builder = temp_dir / "export_workbook.mjs"
        shutil.copyfile(Path(__file__).with_name("export_workbook.mjs"), builder)
        payload_path = temp_dir / "payload.json"
        write_json(payload_path, payload, secrets)
        environment = os.environ.copy()
        for name in SECRET_ENV_NAMES:
            environment.pop(name, None)
        command = [str(node), str(builder), str(payload_path), str(output)]
        if preview_dir:
            command.append(str(preview_dir))
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError("XLSX export failed: " + redact(result.stderr[-1500:], secrets))
    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            assert_safe(archive.read(name).decode("utf-8", errors="ignore"), secrets)
    tables = read_xlsx_tables(output)
    expected_sheets = ["Records", "Searches", "Summary"] + (["Abstracts"] if payload.get("include_abstracts") else [])
    if payload.get("include_evidence"):
        expected_sheets += ["Evidence", "Research_Inference"]
    if list(tables) != expected_sheets:
        raise RuntimeError("XLSX sheet names or order differ from the requested structure")
    if len(tables["Records"]) != len(payload["records"]) + 1 or len(tables["Searches"]) != len(payload["searches"]) + 1:
        raise RuntimeError("XLSX record/search counts differ from canonical inputs")
    if payload.get("include_abstracts") and len(tables["Abstracts"]) != len(payload["abstracts"]) + 1:
        raise RuntimeError("XLSX abstract count differs from canonical input")
    if payload.get("include_evidence") and any(len(tables[name]) != len(payload["records"]) + 1 for name in ("Evidence", "Research_Inference")):
        raise RuntimeError("XLSX evidence/inference counts differ from canonical input")


def export_records(input_file, output_prefix, formats=("csv", "xlsx"), searches=None, root=ROOT, preview_dir=None, include_abstracts=False, evidence_file=None):
    root = Path(root).resolve()
    input_file, prefix = project_path(input_file, root), project_path(output_prefix, root)
    secrets = known_secrets(root)
    records = read_jsonl(input_file)
    assert_safe(records, secrets)
    canonical_records = records
    assert_safe(str(prefix), secrets)
    records = [prepare_record(record) for record in records]
    if len(merge_records(records)) != len(records):
        raise ValueError("Input JSONL contains duplicates; merge it before exporting")
    if not formats or any(value not in {"csv", "xlsx"} for value in formats):
        raise ValueError("Export formats must be csv and/or xlsx")
    if searches is None:
        searches = collect_searches([input_file], [records], root)
    summary = summarize_records(records, searches)
    payload = {
        "record_columns": XLSX_RECORD_COLUMNS, "records": [record_row(record, XLSX_RECORD_COLUMNS) for record in records],
        "include_abstracts": include_abstracts, "abstract_columns": ABSTRACT_COLUMNS,
        "abstracts": [record_row(record, ABSTRACT_COLUMNS) for record in records] if include_abstracts else [],
        "search_columns": SEARCH_COLUMNS,
        "searches": [[literal_text(search.get(field)) if field != "retrieved_at_timezone" else "UTC" for field in SEARCH_COLUMNS] for search in searches],
        "summary": summary,
        "partial_queries": [s["query_id"] for s in searches if s.get("truncated") or s.get("status") not in {None, "completed"}],
    }
    if evidence_file is not None:
        if __package__:
            from .evidence_export import EVIDENCE_COLUMNS, INFERENCE_COLUMNS, evidence_tables
        else:
            # Standalone CLI needs the project parent for package imports.
            sys.path.insert(0, str(ROOT))
            from scripts.evidence_export import EVIDENCE_COLUMNS, INFERENCE_COLUMNS, evidence_tables
        evidence_file = project_path(evidence_file, root)
        assert_safe(str(evidence_file), secrets)
        matrix = read_jsonl(evidence_file)
        assert_safe(matrix, secrets)
        evidence_rows, inference_rows = evidence_tables(canonical_records, matrix)
        payload.update(include_evidence=True, evidence_columns=EVIDENCE_COLUMNS,
                       evidence=[[literal_text(value) for value in row] for row in evidence_rows],
                       inference_columns=INFERENCE_COLUMNS,
                       inference=[[literal_text(value) for value in row] for row in inference_rows])
    assert_safe(payload, secrets)
    if "xlsx" in formats and include_abstracts and any(len(record.get("abstract") or "") > 32767 for record in records):
        raise ValueError("An abstract exceeds the Excel cell limit; keep the full text in canonical JSONL/CSV")
    if "xlsx" in formats and any(isinstance(value, str) and len(value) > 32767
                                 for name in ("evidence", "inference") for row in payload.get(name, []) for value in row):
        raise ValueError("Evidence or inference exceeds the Excel cell limit; keep complete content in Evidence JSONL")
    outputs = {fmt: Path(str(prefix) + "." + fmt) for fmt in formats}
    manifest_path = Path(str(prefix) + ".export.json")
    if any(path.exists() for path in [*outputs.values(), manifest_path]):
        raise FileExistsError("Export output already exists; choose a new output prefix")
    if "xlsx" in formats:
        artifact_runtime()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wos-export-", dir=prefix.parent) as temporary:
        staged = {fmt: Path(temporary) / f"records.{fmt}" for fmt in formats}
        if "csv" in formats:
            with staged["csv"].open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(RECORD_COLUMNS)
                writer.writerows(record_row(record) for record in records)
        if "xlsx" in formats:
            build_xlsx(payload, staged["xlsx"], secrets, preview_dir)
        for fmt in formats:
            os.link(staged[fmt], outputs[fmt])
    manifest = {
        "schema_version": 3, "exported_at": utc_now(), "canonical_input": display_path(input_file, root),
        "include_abstracts": include_abstracts,
        "evidence_input": display_path(evidence_file, root) if evidence_file else None,
        "records": len(records), "searches": len(searches), "summary": summary,
        "outputs": {fmt: display_path(path, root) for fmt, path in outputs.items()},
    }
    write_json(manifest_path, manifest, secrets)
    print(f"Exported {len(records)} records and {len(searches)} searches")
    for path in outputs.values():
        print(path)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--format", choices=["csv", "xlsx", "both"], default="both")
    parser.add_argument("--search-manifests", nargs="+")
    parser.add_argument("--preview-dir", help=argparse.SUPPRESS)
    parser.add_argument("--include-abstracts", action="store_true", help="Add a separate full-text Abstracts sheet to XLSX")
    parser.add_argument("--evidence", help="Validated Evidence Matrix JSONL; adds Evidence and Research_Inference sheets")
    args = parser.parse_args()
    try:
        searches = [search_row(json.loads(project_path(path).read_text(encoding="utf-8")), source_manifest=path) for path in args.search_manifests] if args.search_manifests else None
        formats = ("csv", "xlsx") if args.format == "both" else (args.format,)
        export_records(args.input, args.output_prefix, formats, searches, preview_dir=args.preview_dir, include_abstracts=args.include_abstracts, evidence_file=args.evidence)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Export failed: {redact(exc, known_secrets())}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
