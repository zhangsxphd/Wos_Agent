# Semantic Scholar DOI 批量补全（v0.5.6）

v0.5.6 恢复 Semantic Scholar 的认证 DOI 批量查询。客户端只使用本地 `.env` 中的 `SEMANTIC_SCHOLAR_API_KEY`，通过 `x-api-key` header 发送，不把凭据写入 URL、缓存、报告、日志或导出文件。

## 请求与限流

请求入口为 `POST https://api.semanticscholar.org/graph/v1/paper/batch`。请求参数选择：

```text
paperId,title,year,abstract,externalIds,url,isOpenAccess,openAccessPdf
```

请求体使用规范化且去重后的 DOI：

```json
{"ids": ["DOI:10.xxxx/example"]}
```

默认每批 100 个 DOI，允许的最大批大小为 500，超过批大小自动分块；批次严格串行，默认间隔 1.10 秒，客户端强制不低于 1.05 秒。响应按 `externalIds.DOI` 精确映射回规范化 DOI。返回 DOI 不匹配时记录 `doi_mismatch`，不写成功缓存。

429 使用 `Retry-After` 和有界指数退避；429、5xx、timeout、连接错误都是 transient error，不生成 `not_found` 负缓存，下次允许重试。`null` 结果是 `not_found`；有记录但 `abstract=null` 是 `no_abstract`。每个 DOI 的稳定缓存位于 `data/cache/semantic_scholar/`，完整批次原始响应另存 `batches/`。

报告只记录 `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Credits-Used`、`X-RateLimit-Reset`，缺失值保持 `null`，绝不记录 API Key。

## 使用

刷新 Semantic Scholar、复用 Crossref/OpenAlex 缓存：

```bash
python scripts/enrich_abstracts.py input.jsonl \
  --output data/processed/s2_enriched.jsonl \
  --refresh-provider semantic_scholar \
  --cache-only-provider crossref \
  --cache-only-provider openalex
```

完全离线回放：

```bash
python scripts/enrich_abstracts.py input.jsonl \
  --output data/processed/s2_cache_replay.jsonl --cache-only
```

Crossref > Semantic Scholar > OpenAlex 的 canonical 优先级保持不变。Semantic Scholar 只提供摘要和 OA 元数据，不下载 PDF、不抓取 publisher 页面，也不改变 fulltext 字段。

## 10 DOI 真实验证

原有 10 条 DOI 样本完成一次逻辑批量查询：首个请求收到 429，随后重试成功，因此实际 HTTP 请求数为 2、逻辑批次数为 1；找到 10 条论文，其中 5 条无摘要，5 条有摘要候选，1 条通过 canonical 校验并成为最终 Semantic Scholar 来源。最终 canonical 摘要覆盖率从 7/10 提升到 8/10。随后 cache-only 回放实际 HTTP 请求数为 0，Semantic Scholar cache hits 为 10，canonical 摘要和来源字段逐条一致。
