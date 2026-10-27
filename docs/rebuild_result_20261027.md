# 2026-10-27 重建完成记录

本轮按用户选择完成文献池、摘要和导出；没有启动子代理标注、重新建立 30 篇 Gold 或运行正式质量评测。旧实验保持其历史状态，今天的数据是新的独立运行记录。

## 真实检索与文献池

使用现有 `queries/saline_paddy_research.yaml`，时间范围 2022–2026，先完成每式 5 条的小规模验证，再以每页 50 条、每式 500 条/10 页保护上限执行本轮检索。

| Query | 初始命中 | 实际采集 | 页数 | 截断 |
|---|---:|---:|---:|---|
| Q1_core_water | 47 | 47 | 1 | false |
| Q2_water_regime | 17 | 17 | 1 | false |
| Q3_carbon_microbe | 205 | 205 | 5 | false |
| Q4_amendment | 153 | 153 | 4 | false |

422 条跨检索命中记录合并为 **280 篇唯一文献**，全部有 DOI。4 个 query 本轮均正常完成且未截断。这里的完整仅指本次 API 返回的这 4 个检索式结果；实时索引和检索策略变化会改变候选池。

原始页及完整计划清单位于 `data/raw/plans/20261027_122126_216983_saline_paddy_research_c7d58563/`；标准化分页结果位于对应 `data/processed/plans/`。合并结果保留 matched_queries、provenance_history 和原始响应路径。

## 摘要与来源

| 项目 | 本轮结果 |
|---|---:|
| 唯一文献 | 280 |
| 最终可用摘要 | 157（56.07%） |
| 暂缺摘要 | 122 |
| 来源冲突，摘要保留为空 | 1 |
| 最终 Crossref 来源 | 114 |
| 最终 OpenAlex 来源 | 43 |

来源候选经过 DOI、题名、年份与文本校验；没有将空结果补写为推测摘要。Semantic Scholar 的 3 个逻辑批次共 9 次 HTTP 尝试均遇到 429，本轮未提供可用摘要。Crossref 280 次请求及 OpenAlex 3 次批量请求成功完成；完整请求/缓存/错误记录见 coverage report。

冲突文献：`WOS:001380615900001`，DOI `10.1002/ldr.5442`。Crossref 与 OpenAlex 摘要相似度为 0.591793，未达到现有 0.90 一致性阈值，canonical 摘要保持为空。候选内容和来源保留在 JSONL/缓存，待后续核查；本轮未擅自选边。

随后完成一次完全离线回放：**实际 HTTP 请求为 0**，280 篇的 UID、DOI、摘要和最终来源与在线运行逐条一致。

## 已交付文件

- `data/exports/rebuild_20261027.xlsx`：Records、Searches、Summary、Abstracts 四个工作表。
- `data/exports/rebuild_20261027.csv`：280 篇书目及摘要，UTF-8 BOM。
- `data/exports/rebuild_20261027_attention.csv`：123 篇待补摘要/来源冲突清单。
- `data/processed/rebuild_20261027_merged.jsonl`：新检索合并后的机器可读记录。
- `data/processed/rebuild_20261027_enriched.jsonl`：本轮带摘要、来源候选和校验状态的主数据。
- `data/processed/rebuild_20261027_cache_replay.jsonl`：离线回放输出。
- `data/reports/20261027_122910_381579_abstracts_b1209a7c_abstract_coverage.json`：本轮摘要覆盖与来源报告。
- `data/reports/rebuild_export_verification_20261027.json`：导出及缓存回放验证。
- `data/recovery/dev40_rebuilt_20261027/`：从 Git 保留材料恢复的 40 篇开发数据、参考证据、审阅包、空预测请求包和导出。
- `data/recovery/dev40_rebuilt_20261027.zip`：40 篇开发材料备份及同级 SHA256 校验文件。
- `data/recovery/rebuild_snapshot_20261027.zip`：本轮文献数据、原始响应、缓存、导出、代码与测试报告的完整备份；同级 `.zip.sha256` 为校验值。

已读回 Excel，确认 Records 和 Abstracts 各 281 行（含表头），Searches 5 行（含表头），CSV 280 篇。备份不包含真实 `.env` 或 API Key。

## 工程修复与验证

新增 `scripts/rebuild_workspace.py`，可从已提交的真实 Dev40 摘要与 AI 辅助参考重建工作区。原作者信息、query 和获取时间等未保存内容保持缺失。参考通过 Validator v2，保留原始字节和 provenance；没有将其当作新的预测或独立 Gold。

新增 `scripts/evidence/final_evaluation.py`，将最终提示词、Validator v2、Schema 和 Metric v1/v2 显式绑定。支持准备、响应验证、人工审阅包和评分；缺失响应、输入/提示词变动、过期 envelope 或不受支持证据均阻止完整评分。开发运行始终标为开发诊断。新的 holdout 路径要求恰好 30 篇、与已知 Dev40 不重叠，且事先完成全部审核和哈希绑定；本轮未执行该路径。

导出增加可选 `--evidence-validator 2`，使 v0.7 参考能通过相应校验后导出；默认继续保留旧版行为。`.env.example` 补充 Semantic Scholar 变量，新增精确依赖版本和 GitHub Actions 配置。远程 CI 尚未触发，本轮没有提交或推送。

| 验证 | 结果 |
|---|---|
| 本地完整 portable 回归（含真实 XLSX） | **355/355 通过，无跳过** |
| 不依赖 Codex XLSX 的 CI 同款命令 | **352/352 通过**，明确排除 3 项 XLSX 集成测试 |
| 历史审计 | 36 项单列；当前缺少 19 个前置文件，明确返回 `PREREQUISITES_MISSING` / 退出码 2 |
| 原始冻结组件 | **6/6 哈希保持一致** |
| 导出读回 | 280 篇 / 4 个 query / 4 个工作表核对通过 |
| 缓存回放 | 0 HTTP，摘要及来源逐条一致 |

历史断言未删除，也没有将丢失的旧实验计为通过。355 项普通回归验证的是当前代码及已保存材料，不能替代原版独立评测。测试选择与结果分别位于 `data/reports/rebuild_portable_tests.json`、`rebuild_ci_tests.json` 和 `rebuild_historical_preflight.json`。

后续可先使用本轮 Excel 筛选文献、核查冲突条目及补齐缺失摘要。待用户决定继续标注时，再建立新的评测样本和人工审核记录；原来丢失的 Holdout30 不能冒用为已恢复或已通过。
