import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const recordsSheet = workbook.worksheets.add("Records");
const searchesSheet = workbook.worksheets.add("Searches");
const summarySheet = workbook.worksheets.add("Summary");
const abstractsSheet = payload.include_abstracts ? workbook.worksheets.add("Abstracts") : null;
const evidenceSheet = payload.include_evidence ? workbook.worksheets.add("Evidence") : null;
const inferenceSheet = payload.include_evidence ? workbook.worksheets.add("Research_Inference") : null;
const navy = "#24415E";

function columnName(index) {
  let name = "";
  while (index > 0) {
    index--;
    name = String.fromCharCode(65 + index % 26) + name;
    index = Math.floor(index / 26);
  }
  return name;
}

function writeTable(sheet, headers, data, widths, name) {
  const rows = [headers, ...data];
  const range = sheet.getRangeByIndexes(0, 0, rows.length, headers.length);
  range.values = rows;
  range.format.font = { name: "Arial", size: 11, color: "#1F2937" };
  range.format.verticalAlignment = "center";
  range.format.rowHeight = 22;
  sheet.showGridLines = false;
  headers.forEach((_, index) => {
    sheet.getRangeByIndexes(0, index, rows.length, 1).format.columnWidth = widths[index] ?? 22;
  });
  if (data.length) {
    sheet.tables.add(`A1:${columnName(headers.length)}${rows.length}`, true, name).style = "TableStyleMedium2";
    const body = sheet.getRangeByIndexes(1, 0, data.length, headers.length);
    body.format.wrapText = true;
    body.format.verticalAlignment = "top";
    body.format.autofitRows();
  }
  const header = sheet.getRangeByIndexes(0, 0, 1, headers.length);
  header.format.fill = navy;
  header.format.font = { name: "Arial", size: 11, bold: true, color: "#FFFFFF" };
  header.format.wrapText = true;
  header.format.horizontalAlignment = "center";
  header.format.rowHeight = 34;
  sheet.freezePanes.freezeRows(1);
  return rows.length;
}

const abstractTimeColumn = payload.record_columns.indexOf("abstract_retrieved_at");
const recordData = payload.records.map(row => row.map((value, index) =>
  index === abstractTimeColumn && value ? new Date(value) : value));
writeTable(recordsSheet, payload.record_columns, recordData,
  [27, 70, 16, 46, 22, 52, 43, 14, 14, 45, 22, 40, 22, 55, 22, 25, 25, 25], "RecordsTable");
if (abstractsSheet) {
  writeTable(abstractsSheet, payload.abstract_columns, payload.abstracts,
    [27, 43, 70, 120, 25, 25], "AbstractsTable");
  abstractsSheet.freezePanes.freezeColumns(2);
}
if (evidenceSheet) {
  writeTable(evidenceSheet, payload.evidence_columns, payload.evidence,
    [27, 43, 70, 22, 45, 22, 45, 30, 60, 40, 40, 42, 40, 40, 45, 65, 130, 90, 70, 22], "EvidenceTable");
  evidenceSheet.freezePanes.freezeColumns(3);
  evidenceSheet.getCell(0, payload.evidence_columns.length + 1).values = [["Source: original abstracts; quotes and offsets are preserved in Evidence JSONL. Empty = not reported. Completeness is not paper quality."]];
  evidenceSheet.getRangeByIndexes(0, payload.evidence_columns.length + 1, 1, 1).format.columnWidth = 110;
  writeTable(inferenceSheet, payload.inference_columns, payload.inference,
    [27, 70, 85, 85, 85, 85], "ResearchInferenceTable");
  inferenceSheet.freezePanes.freezeColumns(2);
  inferenceSheet.tabColor = "#704A85";
  inferenceSheet.getCell(0, payload.inference_columns.length + 1).values = [["Interpretation only; every statement requires evidence anchors. Empty = no second-stage inference supplied."]];
  inferenceSheet.getRangeByIndexes(0, payload.inference_columns.length + 1, 1, 1).format.columnWidth = 100;
}
recordsSheet.freezePanes.freezeColumns(2);
if (payload.records.length && abstractTimeColumn >= 0) {
  const abstractTimes = recordsSheet.getRangeByIndexes(1, abstractTimeColumn, payload.records.length, 1);
  abstractTimes.setNumberFormat('yyyy-mm-dd hh:mm:ss" UTC"');
  recordsSheet.getRangeByIndexes(0, abstractTimeColumn, payload.records.length + 1, 1).format.columnWidth = 32;
}
for (const column of [2, 10, 12]) {
  if (payload.records.length) recordsSheet.getRangeByIndexes(1, column, payload.records.length, 1).setNumberFormat("0");
}
const searchData = payload.searches.map(row => row.map((value, index) =>
  index === 3 && value ? new Date(value) : value));
writeTable(searchesSheet, payload.search_columns, searchData,
  [27, 48, 90, 23, 17, 15, 12, 14, 24, 22, 22], "SearchesTable");
if (searchData.length) searchesSheet.getRangeByIndexes(1, 3, searchData.length, 1).setNumberFormat("yyyy-mm-dd hh:mm:ss");
const sourceCol = payload.search_columns.length + 1;
searchesSheet.getCell(0, sourceCol).values = [["Source: Clarivate Web of Science Starter API"]];
searchesSheet.getRangeByIndexes(0, sourceCol, 1, 1).format.columnWidth = 58;

summarySheet.showGridLines = false;
summarySheet.tabColor = navy;
summarySheet.getRange("A2").values = [["WoS research candidate pool"]];
summarySheet.getRange("A2").format.font = { name: "Arial", size: 15, bold: true, color: navy };
summarySheet.getRange("A4:B4").values = [["unique records", payload.summary.unique_records]];
summarySheet.getRange("A5:B5").values = [["partially collected queries", payload.partial_queries.length]];
if (payload.partial_queries.length) summarySheet.getRange("D4").values = [["Partial query results; see Searches.truncated."]];
summarySheet.getRange("A4:H5").format.font = { name: "Arial", size: 11, color: "#1F2937" };

function summaryBlock(row, col, headers, data) {
  const range = summarySheet.getRangeByIndexes(row, col, data.length + 1, headers.length);
  range.values = [headers, ...data];
  range.format.font = { name: "Arial", size: 11, color: "#1F2937" };
  range.format.rowHeight = 23;
  const header = summarySheet.getRangeByIndexes(row, col, 1, headers.length);
  header.format.fill = navy;
  header.format.font = { name: "Arial", size: 11, bold: true, color: "#FFFFFF" };
  header.format.horizontalAlignment = "center";
}
summaryBlock(7, 0, ["publish_year", "records"], payload.summary.records_per_year.map(r => [r.year === "Unknown" ? r.year : Number(r.year), r.records]));
summaryBlock(7, 3, ["source_title", "records"], payload.summary.records_per_journal.map(r => [r.journal, r.records]));
summaryBlock(7, 6, ["query_id", "records"], payload.summary.records_by_query.map(r => [r.query_id, r.records]));
const overlapRow = 10 + Math.max(payload.summary.records_per_year.length, payload.summary.records_per_journal.length, payload.summary.records_by_query.length);
summarySheet.getCell(overlapRow - 1, 0).values = [["Overlap between queries (shared unique records)"]];
const ids = payload.summary.overlap.query_ids;
summaryBlock(overlapRow, 0, ["query_id", ...ids], ids.map((id, index) => [id, ...payload.summary.overlap.matrix[index]]));
const lastRow = overlapRow + ids.length + 1;
const used = summarySheet.getRangeByIndexes(0, 0, lastRow, Math.max(8, ids.length + 1));
used.format.verticalAlignment = "center";
for (const [column, width] of [[0, 30], [1, 26], [2, 26], [3, 60], [4, 26], [5, 4], [6, 30], [7, 15]]) {
  summarySheet.getRangeByIndexes(0, column, lastRow, 1).format.columnWidth = width;
}
summarySheet.getRange("A2").format.font = { name: "Arial", size: 15, bold: true, color: navy };
if (payload.summary.records_per_journal.length) summarySheet.getRangeByIndexes(8, 3, payload.summary.records_per_journal.length, 1).format.wrapText = true;
if (ids.length) {
  summarySheet.getRangeByIndexes(overlapRow, 0, 1, ids.length + 1).format.wrapText = true;
  summarySheet.getRangeByIndexes(overlapRow, 0, 1, ids.length + 1).format.rowHeight = 44;
}
used.format.autofitRows();

workbook.recalculate();
const sheetNames = ["Records", "Searches", "Summary", ...(abstractsSheet ? ["Abstracts"] : []), ...(evidenceSheet ? ["Evidence", "Research_Inference"] : [])];
for (const name of sheetNames) {
  await workbook.inspect({ kind: "table", range: `${name}!A1:H10`, include: "values,formulas", tableMaxRows: 10, tableMaxCols: 8, maxChars: 1500 });
}
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 20 }, maxChars: 1500 });
if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const previews = [["Records", "A1:F6"], ["Records", "N1:R6"], ["Searches", "A1:D6"], ["Summary", `A1:H${lastRow}`]];
  if (abstractsSheet) previews.push(["Abstracts", `C1:F${Math.min(3, payload.abstracts.length + 1)}`]);
  if (evidenceSheet) {
    previews.push(["Evidence", `A1:F${Math.min(7, payload.evidence.length + 1)}`]);
    previews.push(["Evidence", `P1:T${Math.min(3, payload.evidence.length + 1)}`]);
    previews.push(["Research_Inference", `A1:F${Math.min(6, payload.inference.length + 1)}`]);
  }
  for (const [name, range] of previews) {
    const preview = await workbook.render({ sheetName: name, range, scale: 1, format: "png" });
    const filename = name === "Records" && range.startsWith("N") ? "records_abstract_status" : name === "Evidence" && range.startsWith("P") ? "evidence_findings" : name.toLowerCase();
    await fs.writeFile(path.join(previewDir, `${filename}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ sheets: sheetNames, records: payload.records.length, searches: payload.searches.length, errorScan: errors.ndjson }));
