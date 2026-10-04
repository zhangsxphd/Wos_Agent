# WoS Starter 可追溯检索

当前版本 v0.5.5，包含可追溯检索、合并去重、摘要补充、来源锚定的 Evidence Matrix、OA 全文解析，以及 Elsevier 机构授权全文 API fallback。v0.5 基线曾通过 182 项离线测试；v0.5.5 新增测试需在合并前继续离线复核。

首次安装：

```bash
git clone git@github.com:zhangsxphd/Wos_Agent.git wos-research-agent
cd wos-research-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

在本地 `.env` 中填写自己的 API Key，脚本按项目路径读取。公开仓库只包含代码、配置模板、文档及测试；`data/` 下的原始响应、缓存、全文、清单和导出结果由本地运行生成，不提交。文档中的真实验证路径是开发时的本地记录，新检索会生成自己的运行路径。

CSV 导出使用 Python 标准库。XLSX 导出及相关离线测试需要 Codex 的 `@oai/artifact-tool` Node.js 运行环境；安装 Python 依赖不会安装该运行环境。自定义位置可通过 `WOS_ARTIFACT_NODE` 和 `WOS_ARTIFACT_MODULES` 设置，详见下文导出说明。

首次检索：

```bash
cd ~/wos-research-agent
source .venv/bin/activate
pip install -r requirements.txt
python scripts/wos_search.py \
  --query 'TS=("saline-alkali" OR "saline soil" OR "salt-affected soil") AND TS=(rice OR paddy) AND TS=(irrigation OR "water management") AND PY=(2022-2026)' \
  --max-records 200 \
  --name saline_paddy_irrigation
```

每次检索创建独立目录 `data/raw/<运行名>/`，先写入完整的 `page_0001.json` 等原始响应，再生成 `data/processed/<运行名>.jsonl`。原始页保留原字段和值，不用标准化结果覆盖原始数据。运行名使用上海时区和微秒，记录中的检索时间使用带时区的 UTC ISO 8601。

`manifest.json` 记录检索式、数据库、排序、每页检索时间、初始命中数、采集数量、去重数量、请求尝试次数、停止原因和输出路径。路径相对于项目根目录。失败或中断会保存已取得的原始页、部分 JSONL 及状态。`status=completed` 表示本次程序运行完成；`collection_complete=true` 才表示按该检索式采集到了完整结果集。实时索引可能变化，重复运行不保证返回完全相同的数据。

原有单次检索 `wos_search.py` 保持默认页大小 50、最多输出 200 条唯一记录，页预算为 `ceil(max-records / limit)`，默认最多请求 4 个结果页，重试另计。如果页间重复使唯一记录不足 200，达到页预算即停止；需要继续填满时可显式增加 `--max-pages`。这些限制控制本次检索规模，不代表账户其他程序的每日总用量。下面的 v0.2 检索计划默认没有这两个上限。

客户端请求间隔至少 0.25 秒，对连接错误、429 和 5xx 最多尝试 4 次；401/403 等错误直接返回。支持秒数及 HTTP 日期格式的 `Retry-After`。要求等待超过 60 秒时保存失败状态并退出，之后重新检索。鉴权信息只进入官方 HTTPS 请求头，不写入清单和正常输出。

标准化使用已观察到的 Starter 结构，缺失标量为 `null`，缺失列表为空列表。`publishMonth` 保存为 `publication_date_raw`；`pages.count` 只保存为页数；文章号单独保存。仅使用 `citations` 中 `db=WOS` 的被引次数，0 不等于缺失。摘要预留 `abstract`、`abstract_source`、`abstract_retrieved_at` 三个字段，当前均为 `null`。未返回的机构、基金、参考文献正文不生成，不根据题名猜摘要。

去重优先使用规范化 DOI，再使用 UID；同一 UID 有无 DOI 的副本可匹配。只有缺少 DOI 和 UID 且题名、年份均存在时，才使用规范化题名加年份。没有可靠标识的记录分别保留。重复版本保留首次出现的非空字段，缺失字段可由实际副本补齐，冲突原值可从原始页核查。

运行离线验证（不调用 API）：

```bash
.venv/bin/python tests/run_offline.py -v
```

## v0.2 多检索计划

`queries/saline_paddy_research.yaml` 定义四个命名检索，分别覆盖核心水管理、水分制度、碳与微生物、改良措施。每个 query 必须有 `id`、`description`、`query`、`database`、`sort`、布尔型 `enabled`。可以禁用单个 query，或为单个 query 设置可选的 `max_records`、`max_pages`；缺省及 `null` 都表示没有上限。ID 不区分大小写地唯一，YAML 重复键和不安全对象标签会被拒绝。

安装新增 YAML 依赖后，运行完整检索：

```bash
cd ~/wos-research-agent
source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_search_plan.py queries/saline_paddy_research.yaml
```

执行器顺序运行已启用的 query，并共用原有 `WosStarterClient`，保持原有限流和重试行为。默认每页 50 条，翻页至 API 报告的最后一页，没有隐含 200 条上限。实时索引发生变化或 API 返回异常空页、重复页时，清单会明确标记不完整或失败。

小规模验证可以显式设置上限：

```bash
python scripts/run_search_plan.py queries/saline_paddy_research.yaml \
  --limit 5 --max-records 5 --max-pages 1
```

这些上限作用于每个 query；命令行与 YAML 同时设置时采用较小值。`max_records` 限制唯一输出条数，原始响应仍按完整页保存。上限导致未取完结果，或完整原始页中只有部分记录进入输出时，`truncated=true`。`total_hits` 是首次 API 命中数量，`collected` 是实际唯一输出数量。达到上限不是程序错误，但不是完整检索。

目录按每次执行分开，重复运行不会覆盖历史数据：

```text
data/raw/plans/<run_id>/
  plan_manifest.json
  01_Q1_core_water/
    page_0001.json
    manifest.json
  ...
data/processed/plans/<run_id>/
  Q1_core_water.jsonl
  ...
```

计划清单保存 YAML 快照和文件 SHA256，单个 query 清单保存完整检索参数、分页时间和状态。单个 query 的 HTTP/数据错误会保存安全的部分结果，其余 query 继续执行。CLI 对存在失败的计划返回非零退出码；成功检索的数据保持有效。`status=completed` 表示正常执行，`truncated=false` 才表示本次结果采集完整。返回内容如果包含已知 API Key，会在写入文件前被拒绝。

## 合并与保留来源

将终端打印的计划清单路径代入以下命令，输出名需使用尚不存在的新名称：

```bash
python scripts/merge_searches.py \
  --plan-manifest "data/raw/plans/<run_id>/plan_manifest.json" \
  --output data/processed/saline_paddy_merged.jsonl
```

也可直接合并多个 JSONL：

```bash
python scripts/merge_searches.py data/processed/first.jsonl data/processed/second.jsonl \
  --output data/processed/merged.jsonl
```

对于没有命名 query 的旧版 JSONL，可用 `--query-ids Q0_initial Q1_core_water` 按输入顺序提供标签；未提供时，根据旧记录的数据库和原检索式生成稳定的 `legacy_*` ID。既有检索式和检索时间保留，不改写旧文件。使用计划清单合并时，会包含已保存的失败部分结果，并在导出 Searches 中保留其失败状态。

去重优先级和原有逻辑一致：规范化 DOI、UID，以及仅对缺少强标识的记录使用题名加年份。合并增加 `matched_queries`、`query_match_count`、`provenance_history`。同一 query 的多次采集保留不同时间/来源记录，但不会重复增加 query 数量。重复来源的非空字段保留首次出现值，实际副本可补齐空字段。原始页始终保留。没有摘要的旧版记录升级后，三个摘要字段仍为 `null`。

合并输出附带同名 `.manifest.json`，保存输入文件、去重数量、查询清单和可独立核查的统计。JSONL 是标准的机器可读数据源，合并和导出均不覆盖输入。

## CSV 与 Excel 导出

```bash
python scripts/export_records.py data/processed/saline_paddy_merged.jsonl \
  --output-prefix data/exports/saline_paddy
```

默认输出 UTF-8 BOM CSV 和 XLSX，并保存 `.export.json` 清单。`--format csv` 可以只导出 CSV，`--format xlsx` 可以只导出 Excel。若输入未去重，程序会要求先合并，不会在导出时静默删行。

Excel 默认三个工作表，可用 v0.3 的 `--include-abstracts` 增加摘要工作表：

- **Records**：要求的书目字段、命中 query，以及摘要是否存在、来源、状态和获取时间；完整摘要移入可选的 Abstracts。作者和列表字段以分号分隔，缺失值为空，被引次数 0 保持为 0。CSV 保留原有完整摘要列，并增加是否存在与状态列。
- **Searches**：query ID、描述、完整检索式、检索时间、命中数、采集数、页数、截断标志，并增加状态和停止原因。Excel 检索时间使用日期单元格，时区单独标为 UTC；JSONL 保留原始带时区 ISO 8601 时间。
- **Summary**：当前导出文献池的唯一记录数、年份分布、期刊分布、各 query 命中数及重叠矩阵；矩阵对角线为该 query 在导出池的记录数，其他单元格为两个 query 的共同记录数。存在截断时，明确提示统计范围只是已采集的候选池。

所有统计来自当前 JSONL，CSV/XLSX 是导出快照。Excel 使用 Codex 自带的 Artifact Tool 运行时，不需要额外 Python Excel 库。默认查找 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/`；在其他环境中可设置 `WOS_ARTIFACT_NODE` 和 `WOS_ARTIFACT_MODULES`，否则仍可使用 CSV。导出过程不向 Node 子进程传递 API Key，保存文件后会读回检查工作表、行数及敏感信息。失败的导出不会留下最终 CSV/XLSX 半成品。

v0.2 没有论文评分、自动排除、PDF 下载或摘要推测步骤。

## v0.3 DOI 多来源摘要补全

```bash
python scripts/enrich_abstracts.py data/processed/saline_paddy_merged.jsonl \
  --output data/processed/saline_paddy_enriched.jsonl
python scripts/export_records.py data/processed/saline_paddy_enriched.jsonl \
  --output-prefix data/exports/saline_paddy_enriched --include-abstracts
```

WoS 原始书目字段和 query provenance 保持原值。当前默认启用 Crossref、Semantic Scholar 和 OpenAlex；Semantic Scholar 使用 DOI 批量请求，默认每批 100 个 DOI，批次间至少间隔 1.10 秒，并支持 `--refresh-provider semantic_scholar`。Crossref 保持现有缓存行为；OpenAlex 默认先收集规范化 DOI，再排除缓存命中，以最多 100 个 DOI 一批查询 `/works` 并按响应 DOI 映射回记录。没有 DOI 时不请求。摘要候选、校验、来源故障及选择理由写入 `abstract_enrichment`，原始来源响应另存 `data/cache/<provider>/<DOI SHA256>.json`；OpenAlex 和 Semantic Scholar 批次完整响应另存同目录的 `batches/`。输出文件必须使用新名称。

OpenAlex Key 仅在本地 `.env` 设置 `OPENALEX_API_KEY`；Semantic Scholar Key 仅在本地 `.env` 设置 `SEMANTIC_SCHOLAR_API_KEY`。OpenAlex 使用 Authorization Bearer，Semantic Scholar 使用 `x-api-key` header；凭据不进入请求 URL、缓存、报告或日志。已有 WoS Key 不会发给外部来源。Semantic Scholar 默认批次间隔 1.10 秒（客户端强制不低于 1.05 秒），超时 30 秒，429/5xx/连接错误执行有界重试；429、5xx、timeout 和连接错误不生成负缓存。可使用 `--cache-only-provider semantic_scholar` 做离线回放。

OpenAlex `200` 返回有记录但没有摘要时标记 `no_abstract`，与没有记录的 `not_found` 分开。仅 `404` 或完整精确 DOI 查询结果明确没有记录时负缓存；429、timeout、5xx 和异常/不完整响应不把缺失 DOI 写为负缓存，下次仍可重试。既有 v0.3 缓存保持兼容。

仅刷新 OpenAlex、强制 Crossref 仅读缓存的 10 条验证命令：

```bash
python scripts/enrich_abstracts.py data/processed/saline_paddy_v03_smoke_input_10_20261003.jsonl \
  --output data/processed/my_openalex_batch.jsonl \
  --refresh-provider openalex --cache-only-provider crossref
```

`--refresh-provider openalex` 不刷新 Crossref；`--cache-only-provider crossref` 阻止 Crossref 缓存未命中时发请求。全局 `--cache-only` 可验证整个新结果的离线缓存重放。报告记录实际 HTTP 次数和 OpenAlex 四个 RateLimit 响应头（未返回的值保持 null）。

报告位于 `data/reports/<run_id>_abstract_coverage.json`，包含每个来源的有效摘要数量、请求/缓存/未命中/错误计数、最终覆盖率、冲突和逐条结果。导出保留原检索 Searches，Summary 统计当前输入样本，Abstracts 保存完整摘要。没有摘要时保持 `null`。

详细校验阈值、冲突行为、缓存语义和真实验证记录见 [v0.3 说明](docs/v03.md)。

## v0.4 原文证据矩阵

在 enriched JSONL 上建立独立的 Evidence Matrix，默认人工、离线提取。7 篇真实摘要已完成带原文片段和字符位置的验证，3 篇无摘要保持 `needs_fulltext`。本轮未设置正式纳入标准，因此有摘要条目保持 `maybe`，推理层为空。

```bash
python scripts/build_evidence_matrix.py data/processed/saline_paddy_v03_openalex_batch_enriched_20261003.jsonl \
  --manual-dir data/manual/evidence_v04_smoke_20261003/responses
python scripts/export_records.py data/processed/saline_paddy_v03_openalex_batch_enriched_20261003.jsonl \
  --output-prefix data/exports/my_v04_review --include-abstracts \
  --evidence data/evidence/ACTUAL_RUN_ID_evidence.jsonl
```

用实际生成的 Evidence 文件路径替换 `ACTUAL_RUN_ID`。JSONL 保存证据与溯源；XLSX 分开提供 Evidence 和 Research_Inference。缺失值保持 `null`/`unknown`/`[]`，完整度只描述信息是否齐备。现有 API/provider 和检索核心保持原样。

人工模板、缓存、校验约束、复现命令与验证结果见 [v0.4 说明](docs/v04.md)。

## v0.5 OA 全文解析与筛选 Profile

新增 Full-text Resolver，优先获取 OpenAlex TEI XML，再尝试缓存 PDF 和明确的 OA location。全文 raw、parsed、manifest 和按章节审核的证据保存在独立 sidecar，原始 canonical 与 v0.4 Evidence 不覆盖。筛选结果同时包含 eligibility_status 和 evidence_role，保留综述/区域模型的科研用途。

首轮仍用原来的 10 篇：10 个 Work matched，仅 1 篇全文实际取得并解析；总体 needs_fulltext 保持 8→8。182 项离线测试通过。完整使用步骤、限制与逐篇真实结果见 [v0.5 说明](docs/v05.md)。


## v0.5.5 Elsevier 机构授权全文 fallback

v0.5.5 在现有 OA resolver 之后增加 Elsevier Article Retrieval API。它不会替换 OpenAlex，也不会抓取 ScienceDirect 网页。

本地 `.env`：

~~~text
ELSEVIER_API_KEY=
ELSEVIER_INSTTOKEN=
~~~

`ELSEVIER_INSTTOKEN` 仅在机构明确提供时填写；通常 Elsevier 可根据请求所在的订阅机构网络/IP 判断 entitlement。API Key 只通过 `X-ELS-APIKey` header 发送，不进入 URL 或输出文件。

运行：

~~~bash
python scripts/resolve_entitled_fulltexts.py   data/processed/<canonical>.jsonl   --output data/fulltext/runs/<new-sidecar>.jsonl
~~~

路由顺序：

1. OpenAlex OA/缓存全文；
2. Elsevier exact DOI + `view=FULL`；
3. 失败则保留 unavailable/error 和逐路由 provenance。

详细说明见 [v0.5.5 文档](docs/v055.md)。
