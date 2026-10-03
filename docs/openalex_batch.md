# OpenAlex 批量 DOI 修复

当前真实执行仅使用 Crossref 和 OpenAlex。Semantic Scholar 未创建客户端，逐条 `provider_results.semantic_scholar.status=skipped`；其请求、缓存和错误计数均为 0，不改变缺失状态和 OpenAlex 的独立覆盖统计。原有注入 Mock/client dictionary 的低层测试接口保留兼容；所有原 77 项测试仍完全离线运行，不发 Semantic Scholar 请求。

OpenAlex Key 只在项目 `.env`（600）中保存。客户端使用 Authorization Bearer，从不把 Key 放到查询参数、数据文件或错误正文。批量请求：

```text
GET https://api.openalex.org/works
filter=doi:https://doi.org/<normalized DOI1>|https://doi.org/<normalized DOI2>|...
per_page=100
select=id,doi,title,publication_year,abstract_inverted_index
```

最多 100 个规范化且去重后的 DOI 一批，缓存命中先排除。`get_many_by_doi` 返回 normalized DOI 到统一记录/None/ProviderError 的字典；结果顺序不参与匹配，原 WoS DOI 和其他书目字段不改动。10/100/101 个未缓存 DOI 在无重试的成功条件下分别请求 1/1/2 次。`get_by_doi` 的旧单记录接口仍可用，标准 enrichment 文件执行默认走批量接口。

缓存仍是 `data/cache/openalex/<DOI SHA256>.json`、cache_version=1，兼容原 `ok`/`not_found` 条目，新增 `no_abstract`。每批完整成功响应保存在 `data/cache/openalex/batches/<run_id>.json`，各 DOI 缓存保留原 Work object、指向批次原始响应的路径及响应限额头。

- 有记录、有摘要：缓存 `ok`，lookup `found`；仍须经过既有 DOI、题名、年份和文本校验，才能计入有效摘要覆盖率。
- HTTP 200 有记录、摘要为空或索引无法无损重建：`no_abstract`，保留元数据和原始 index/警告。
- HTTP 404，或完整精确 DOI 查询结果明确没有此 DOI：`not_found`。结果声明还有未返回项目时，遗漏 DOI 为 `incomplete_batch_response`，不写负缓存。
- 429/timeout/5xx/连接失败：transient error，保留已有缓存，不生成新负缓存；下一次运行可以重试。单批失败不破坏已完成的其他批次。
- 响应结构异常、意外 DOI、不明确缺失：报错，不将缺失解释为无记录。

abstract_inverted_index 按整数位置排序重建；重复或缺失位置不猜词。多个有效来源明显冲突时，继续保留所有候选、标记 conflict，沿用 v0.3 canonical 摘要规则。

只记录 `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Credits-Used`、`X-RateLimit-Reset`。每次收到 HTTP 响应的尝试，其状态及四个 header 进入报告；未获得响应的连接/超时尝试计入 requests，没有响应头可记录。源站缺失的 header 保持 null，不凭猜测补值。响应 Key 反射在写入前拒绝。

```bash
cd ~/wos-research-agent
source .venv/bin/activate
python tests/run_offline.py -v
python scripts/enrich_abstracts.py data/processed/saline_paddy_v03_smoke_input_10_20261003.jsonl \
  --output data/processed/my_openalex_batch.jsonl \
  --refresh-provider openalex --cache-only-provider crossref
python scripts/enrich_abstracts.py data/processed/saline_paddy_v03_smoke_input_10_20261003.jsonl \
  --output data/processed/my_openalex_cache_replay.jsonl --cache-only
```

覆盖报告 `found_by_crossref`/`found_by_openalex` 统计通过元数据和摘要校验的候选，abstract_found_total 统计最终非空摘要，unresolved=输入数-最终摘要数。`openalex_coverage` 单独列 DOI 分母、有效摘要、覆盖率及 lookup 状态；Semantic Scholar skipped 不进入这一判断。actual_http_request_count 包含重试，OpenAlex rate-limit headers 保存于 openalex_rate_limit_headers。

接口依据：[官方 DOI OR filter/100 上限](https://help.openalex.org/api/filtering/)、[select 字段](https://help.openalex.org/api/selecting-fields/)、[Bearer 鉴权](https://help.openalex.org/api/authentication/)。
