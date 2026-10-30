// Build a source-linked, filterable workbook from a frozen 100-paper export.
// Run with the bundled Node runtime after linking its node_modules beside a
// temporary copy of this file. Never rewrites an existing output.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [inputArg, outputArg, previewArg, ...options] = process.argv.slice(2);
if (!inputArg || !outputArg || !previewArg) throw new Error('usage: node export_scientific_workbook.mjs EXPORT_DIR OUTPUT_XLSX PREVIEW_DIR [--allow-incomplete]');
const input = path.resolve(inputArg), output = path.resolve(outputArg), preview = path.resolve(previewArg);
try { await fs.access(output); throw new Error('output_already_exists'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
const summary = JSON.parse(await fs.readFile(path.join(input, 'summary.json'), 'utf8'));
if (summary.papers_total !== 100 || !summary.candidate_not_gold || summary.accuracy_calculated) throw new Error('invalid_export_scope_or_status');
if (summary.new75_unfinished && !options.includes('--allow-incomplete')) throw new Error('batch_not_complete');
const parse = async n => (await fs.readFile(path.join(input, n + '.jsonl'), 'utf8')).split('\n').filter(Boolean).map(JSON.parse);
const specs = [
  ['papers', '论文', 'Papers'], ['fields', '核心字段', 'Fields'], ['results', '定量结果', 'Results'],
  ['issues', '审核问题', 'Issues'], ['historic_claims', '历史详细记录', 'HistoricClaims'], ['supplement_requests', '待下载附件', 'MissingSupplements'],
  ['supplements', '补充观察', 'SupplementObservations'], ['supplement_files', '附件来源', 'SupplementFiles']
];
const names = {
  doi:'DOI', uid:'UID', title:'论文题目', extraction_status:'提取状态', model_source:'模型来源', source_file:'来源记录',
  raw_sha256:'原始文件 SHA256', raw_file:'原始文件', human_relevance:'相关性审核', holdout_overlap:'历史留出集标识',
  supplement_status:'补充材料状态', field:'核心字段', value:'记录值', status:'状态', record_class:'记录类别',
  evidence:'原文证据及位置', source_sha256:'来源 SHA256', index:'结果序号', indicator:'指标', treatment:'处理组', comparator:'比较对象',
  time:'时间', unit:'单位', significance:'显著性', significance_letters:'显著性字母', reporting_origin:'报告来源', limitations:'限制',
  source:'来源', severity:'优先级', type:'标记类型', path:'结果位置', detail:'标记详情', topic:'主题', record_kind:'记录类型',
  observation:'观察记录', created_utc:'记录时间 UTC', pmcid:'PMC ID', license:'许可', filename:'文件名', local_file:'本地文件',
  sha256:'SHA256', md5_matches_pmc:'PMC MD5 核验', bytes:'字节数', source_url:'来源地址', identity:'身份说明',
  historic_response_file:'历史完整回答', source_file_sha256:'历史记录文件 SHA256', raw_pdf_sha256:'原始 PDF SHA256', original_record:'原始详细记录',
  publisher_link:'出版商地址', source_pdf:'原文 PDF', source_page:'原文页码', source_excerpt:'原文材料说明', model_note:'模型备注', note:'范围限制'
};
const workbook = Workbook.create();
const intro = workbook.worksheets.add('说明');
intro.getRange('B2').setNumberFormat('@');
intro.getRange('A1:B12').values = [
  ['Wos_Agent 全文科研信息', '说明'],
  ['生成时间 UTC', summary.created_at],
  ['论文总数', summary.papers_total],
  ['历史候选', summary.historical20],
  ['既有 5 篇候选', summary.prior_five],
  ['本批已完成模型提取', summary.new75_completed],
  ['本批尚未完成', summary.new75_unfinished],
  ['字段 / 定量结果', `${summary.fields} / ${summary.results}`],
  ['自动标记', '仅为核查线索，不代表已证实错误。'],
  ['审核状态', '新提取和补充观察均为 Candidate，不是人工 Gold；不计算准确率。'],
  ['补充材料', '新取得附件未自动进入历史模型输入；来源及待核状态见各表。'],
  ['不可导出字符', '原始文本中的 Excel/XML 禁止字符以 [U+XXXX] 明示；原文及 JSONL 不变。']
];
intro.getRange('A1:B1').format = { fill:'#174C64', font:{name:'Arial',bold:true,color:'#FFFFFF',size:11} };
intro.getRange('A:A').format.columnWidth = 27;
intro.getRange('B:B').format.columnWidth = 85;
intro.getRange('A1:B12').format.rowHeight = 25;
intro.getRange('B1:B12').format.wrapText = true;
intro.freezePanes.freezeRows(1);
await fs.mkdir(preview, {recursive:true});
const counts = {}, createdSheets = new Set(['说明']);
let escapedCharacters = 0;
const safeForXml = text => text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, c => {
  escapedCharacters += 1;
  return `[U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4,'0')}]`;
});
for (const [stem, sheetName, tableName] of specs) {
  const rows = await parse(stem);
  if (stem === 'issues') rows.sort((a,b) => ({high:0, medium:1, low:2}[a.severity] ?? 3) - ({high:0, medium:1, low:2}[b.severity] ?? 3));
  let keys = [...new Set(rows.flatMap(Object.keys))];
  if (stem === 'papers') keys = ['doi','title','uid','extraction_status','supplement_status',...keys.filter(k=>!['doi','title','uid','extraction_status','supplement_status'].includes(k))];
  if (!keys.length) continue;
  const display = v => {
    const value = v === undefined || v === null ? '' : (typeof v === 'object' ? JSON.stringify(v) : v);
    return typeof value === 'string' ? safeForXml(value) : value;
  };
  const matrix = [keys.map(k => names[k] || k), ...rows.map(row => keys.map(k => display(row[k])))];
  for (const row of matrix) for (const cell of row) if (typeof cell === 'string' && cell.length > 32767) throw new Error(`excel_cell_exceeds_limit:${stem}`);
  const sheet = workbook.worksheets.add(sheetName);
  createdSheets.add(sheetName);
  const range = sheet.getRangeByIndexes(0, 0, matrix.length, keys.length);
  // Preserve ISO timestamps and other source strings rather than letting the
  // spreadsheet engine reinterpret them as dates or numbers.
  for (let i=0;i<keys.length;i++) {
    if (rows.every(row => row[keys[i]] == null || typeof row[keys[i]] !== 'number' && typeof row[keys[i]] !== 'boolean')) {
      sheet.getRangeByIndexes(0,i,matrix.length,1).setNumberFormat('@');
    }
  }
  range.values = matrix;
  range.format.font = {name:'Arial',size:10};
  range.format.verticalAlignment = 'center';
  range.format.wrapText = true;
  const header = sheet.getRangeByIndexes(0,0,1,keys.length);
  header.format = {fill:'#174C64',font:{name:'Arial',bold:true,color:'#FFFFFF',size:10}};
  for (let i=0;i<keys.length;i++) {
    const width = ['title','value','evidence','detail','observation','limitations','original_record','source_excerpt','model_note','note'].includes(keys[i]) ? 48 : ['doi','model_source','source_file','local_file','source_url','raw_file','historic_response_file','publisher_link','source_pdf'].includes(keys[i]) ? 32 : 20;
    sheet.getRangeByIndexes(0,i,matrix.length,1).format.columnWidth = width;
  }
  range.format.autofitRows();
  sheet.freezePanes.freezeRows(1);
  const table = sheet.tables.add(range.address, true, tableName);
  table.showFilterButton = true;
  sheet.showGridLines = false;
  counts[stem] = rows.length;
}
workbook.recalculate();
for (const [sheetName,file] of [['说明','readme'],...specs.map(x=>[x[1],x[0]])]) {
  if (!createdSheets.has(sheetName)) continue;
  try {
    const img = await workbook.render({sheetName,range:sheetName==='说明'?'A1:B12':'A1:F7',scale:1,format:'png'});
    await fs.writeFile(path.join(preview,file+'.png'),new Uint8Array(await img.arrayBuffer()));
  } catch(e) { throw new Error(`render_failed:${sheetName}:${e.message}`); }
}
const inspect = await workbook.inspect({kind:'table',range:'论文!A1:F4',include:'values',tableMaxRows:4,tableMaxCols:6});
await fs.writeFile(path.join(preview,'inspect.ndjson'),inspect.ndjson || '');
const file = await SpreadsheetFile.exportXlsx(workbook);
await fs.mkdir(path.dirname(output),{recursive:true});
await file.save(output);
console.log(JSON.stringify({output, counts, escaped_xml_characters:escapedCharacters, unfinished:summary.new75_unfinished, candidate_not_gold:true}));
