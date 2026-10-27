# Wos_Agent 项目分析与下一步建议

分析日期：2026-10-27（Asia/Shanghai）。仓库：[zhangsxphd/Wos_Agent](https://github.com/zhangsxphd/Wos_Agent)。

**结论：检索、去重、摘要补全和可追溯证据处理已有较完整实现；当前最值得推进的是 v0.7 的独立质量验证与可复现交付。先恢复未提交的评测材料、完成 Gold 人工审核，再按冻结方案评测，随后决定是否扩大到全库。**

## 本次拉取与验证

已将仓库完整克隆到 `/Users/zhangshuxuan/Documents/Project/Wos_Agent`，包含 Git 历史及 6 个远程分支，非浅克隆。当前工作目录切换到最新开发分支 `v0.7-field-reliability`，提交 `1ed0549`；`main` 保留在本地。Git 对象完整性检查通过。

| 远程分支 | 提交 | 已提交文件数 | 定位 |
|---|---|---:|---|
| main | 75bf952 | 61 | 默认分支，检索与数据处理主线，含 v0.5.6 摘要补全 |
| v0.5.5-elsevier-agent | 031c61f | 65 | 独立的 Elsevier 全文获取功能 |
| v0.5.6-semantic-scholar-batch | dbb9982 | 67 | Semantic Scholar 批量补全开发分支 |
| v0.5.6-semantic-scholar-main | 4b01190 | 61 | Semantic Scholar 主线候选分支 |
| v0.6-evidence-extraction | 866987a | 100 | 隔离 Worker、批处理与证据评估 |
| v0.7-field-reliability | 1ed0549 | 169 | 字段可靠性、Dev40、冻结提示词与留出集评测准备 |

v0.7 比 main 多 26 个提交，main 是其祖先。其他功能分支的历史已下载，但未将各分支差异混合到当前工作目录；例如 Elsevier 功能不在当前 v0.7 的文件树中。

GitHub 只能提供已提交内容。`.env`、大部分真实检索数据、缓存、PDF、历史评测材料和最终待审包不在仓库内。v0.7 确实提交了部分 Dev40 材料，但并没有完整提交其运行依赖。

已在项目 `.venv` 中安装 `requirements.txt`。本次使用 Python 3.12.14 和当前 Codex 内置 Node/Artifact 运行环境；具体安装版本保存在本次分析目录中。

| 实测对象 | 结果 | 含义 |
|---|---|---|
| main：`tests/run_offline.py -v` | 209 项，全部通过 | 当前环境可复现主线离线测试，包含导出相关测试 |
| v0.7：同一离线入口 | 运行 362 项；1 failure、21 errors、1 skipped | 本次错误均为 FileNotFoundError；失败断言为报告文件不存在。部分类在 setUpClass 阶段失败，未执行其中的测试体 |
| v0.7 冻结组件 | 6/6 哈希与协议一致 | 最终提示词、Contract v2.1、Validator v2、Schema、Metric v1/v2 均匹配；最终提示词与 I2 字节一致 |
| Git 完整性 | `git fsck --full` 通过 | 已取得的 Git 对象没有检测到完整性错误 |

本次未调用 WoS、摘要来源、全文下载或模型提取接口；未产生新的研究数据或留出集评分。仓库业务源码和冻结组件均未修改。本报告中的既有科学提取指标来自版本化文档，未在当前缺少原始评测材料的目录重新计算。

## 项目结构与成熟度

项目目前是 Python 命令行科研工作流，研究配置集中于盐碱稻田、水管理、碳与微生物等主题。主路径为：检索计划 → 原始响应留档 → 标准化与合并去重 → 多来源摘要补全 → 来源锚定的证据提取 → 全文补充与筛选 → CSV/XLSX 导出。

| 模块 | 主要文件 | 已实现的职责 |
|---|---|---|
| 检索 | `scripts/wos_client.py`、`wos_search.py`、`run_search_plan.py` | 分页、限流、重试、检索计划与失败部分结果留档 |
| 数据规范化 | `scripts/normalize_record.py`、`merge_searches.py` | DOI/UID 去重、缺失值处理、跨 query 来源历史 |
| 摘要补全 | `scripts/enrich_abstracts.py`、`providers/` | Crossref/Semantic Scholar/OpenAlex、DOI 批处理、缓存与覆盖报告 |
| 证据层 | `scripts/build_evidence_matrix.py`、`extractors/`、`schemas/` | 人工/缓存提取、原文片段与字符位置校验、证据与推理分离 |
| 全文层 | `scripts/fulltext/`、`resolve_fulltexts.py`、`build_fulltext_evidence.py` | OA 获取、TEI/PDF 解析、分块提取与摘要/全文独立溯源 |
| 筛选与导出 | `scripts/screening_profile.py`、`export_records.py`、`export_workbook.mjs` | 主题 Profile、证据角色、CSV 和多工作表 XLSX |
| 评估层 | `scripts/evidence/`、`docs/v06*`、`docs/v07*` | 隔离请求包、Gold 标注与裁决、字段/发现指标、冻结与审计 |

优点是原始数据与处理结果分离、默认保留来源和缺失状态、缓存及请求有审计记录、证据须有原文支撑。离线测试明确禁止 Python socket 联网。`scripts/extractors/openai.py` 仍是占位适配器；实际提取走人工/缓存或外部 Codex Worker 协议，不能将其理解为已接通的一键模型 API 服务。

## 关键发现

**P0：新克隆无法完整复现 v0.7。** 多个测试直接依赖被 `.gitignore` 排除的历史运行产物，例如 `data/processed/saline_paddy_v056_full_enriched_20261003.jsonl`、Dev40 与 Holdout30 的 identities/selection manifest、历史 holdout Gold、Worker 响应和 `v07_final_system_freeze_manifest.json`。最终 Holdout30 候选与人工审阅包也缺失。这是实际测试失败原因，不能据此断言全部业务功能失效，也不能仅凭已有文档声称最新版本测试全绿。参见 `tests/test_v07_final_freeze.py`、`tests/test_v07_future_holdout30_gold.py` 及本次日志。

**P0：独立评测仍未闭环。** `docs/v07_future_holdout30_gold_preparation.md` 记录：30 篇候选已有 AI 辅助标注与裁决；27/30 通过 grounding，3 篇仍有不受支持的测量断言；全体 30 篇均待 owner 审核；目标提取和评分尚未运行。这是仓库最后记录的状态，当前无法用缺失的本地材料独立核实其后续进展。

**P1：开发集改进有价值，但不能作为独立验证。** `docs/v07_dev40_final_selection.md` 报告 Dev40 的字段 Metric v2 F1 从 0.7472 提高到 0.8564，I2 拒绝响应为 0，findings precision/recall 为 0.9645/0.9422；methods F1 降至 0.5797。文档已冻结 I2 并停止该轮调参。这些是开发诊断。v0.6 独立评测曾因严格字段 F1=0.5986 未达到当时门槛，`docs/v06_final_assessment.md` 明确标记尚未具备生产提取条件。

**P1：通用批处理入口仍采用 v0.6 组件。** `scripts/evidence/batch_prepare.py:13` 引用 Validator v1，`:17` 读取旧提示词；`batch_ingest.py:15` 使用 Validator v1；`benchmark.py` 调用旧 `quality_gate`，其字段门槛为 v1 F1≥0.90、findings recall≥0.75。v0.7 最终协议要求 Validator v2、Metric v2 和 recall≥0.85。直接执行旧通用命令不会自动变成冻结的 v0.7 最终评测，需要明确的新入口与配置，保留旧版历史结果。

**P2：工程交付说明落后于开发状态。** README 首段仍写 v0.5/182 tests；实际 main 本次为 209 tests，最新分支已到 v0.7。`.env.example` 缺少代码使用的 `SEMANTIC_SCHOLAR_API_KEY`。仓库未包含 `.github` CI 配置和完整依赖锁定；XLSX 还依赖 Codex Artifact 运行时，纯 Python 安装只覆盖部分运行需求。这些问题影响新环境上手与协作。

## 建议的推进顺序与验收条件

1. **先恢复评测资料。** 从原开发机器或备份恢复原始 `data/`，优先恢复 canonical JSONL、冻结 identity/selection 清单、最终系统冻结清单、Holdout30 标注/裁决/候选/校验报告和完整人工审阅包，再恢复历史 Worker 响应与报告。核对已有 SHA256，给恢复材料建立清单与独立备份。缺失的冻结数据应恢复原件；重新随机选样或重新生成答案无法替代原冻结实验。
2. **同时修复新环境的可复现测试。** 将使用合成 fixtures 的常规离线回归与依赖真实冻结产物的历史审计分开。前者在干净克隆中直接运行；后者在执行前明确校验材料是否齐备。为常规回归建立 CI，补充依赖锁定、运行时检查和当前分支说明。验收条件是常规回归不依赖作者私有目录，历史审计对缺失材料给出明确清单。
3. **完成 30 篇 Gold 人工审核。** 恢复材料后，逐篇核对原文、字段分类和支持片段，处理 3 篇 grounding 问题，并保存全部 30 篇的明确决定与最终 Gold 哈希。遵循 `docs/v07_final_evaluation_protocol.md:19` 已规定的审核顺序，维持目标提示词与指标冻结。Dev40 的最新 provenance 声明是 AI 辅助开发参考，不能升级描述为全量独立人工金标准。
4. **补齐并验证最终评测入口。** 新入口显式绑定冻结 final prompt、Contract v2.1、Validator v2、Schema 和 Metric v1/v2；读取审核完成状态并校验哈希。先使用合成数据测试控制逻辑，审核完成后再按原方案执行独立评测。验收条件：结构有效率 100%，不受支持断言/无效位置/孤立锚点/拒绝响应均为 0，Metric v2 字段 micro F1≥0.80，findings precision≥0.95、recall≥0.85。结果失败就如实记录，保持该留出集独立性。
5. **依据评测结果安排试运行和发布。** 通过固定质量门后再制定小规模 pilot，检查人工纠错量、字段误差和导出用途，然后决定是否全库提取及合入 main。Elsevier 全文功能另做差异审查与验证；OA 全文取得率和证据提取正确率应分别衡量。

现在最具体的一项行动是：**找回原环境的 Holdout30 人工审阅包及其配套冻结文件**。资料恢复前，工程侧可先完成干净克隆测试分层和最终评测入口的合成数据验证。

## 本次文件

- [主线测试完整日志](../data/reports/project_analysis_20261027/main_offline.log)
- [v0.7 测试完整日志](../data/reports/project_analysis_20261027/v07_offline.log)
- [全部远程分支及文件清单](../data/reports/project_analysis_20261027/repository_inventory.json)
- [本次安装的依赖版本](../data/reports/project_analysis_20261027/installed_dependencies.txt)

以上日志及清单位于忽略的本地 `data/`，本报告位于 `docs/`。本次未创建提交或推送 GitHub。
