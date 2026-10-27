# 数据丢失后的重建与继续运行

2026-10-27：旧的未提交资料已无法找回。本轮保留原版 v0.6/v0.7 冻结文档和 6 个核心组件的原始哈希；新数据和新运行不写到丢失的旧实验路径，不伪造原始检索时间、历史预测、Gold 审核决定或通过状态。

## 恢复已有 40 篇开发材料

```bash
.venv/bin/python scripts/rebuild_workspace.py --output data/recovery/my_dev40
```

在 Codex 内置 Artifact 环境中可添加 `--with-xlsx`，同时输出参考证据工作表。默认只需 Python 依赖，输出 CSV。`--init-env` 仅在没有 `.env` 时建立空模板；已有凭据文件不会被覆盖。

输入来自仓库已跟踪的 `v07_dev40_gold_work/abstracts.jsonl` 和 `v07_dev40_gold_final/candidate_gold.jsonl`。40 篇摘要与参考均通过 Validator v2。重建目录包含：

- `records.jsonl`：书目与摘要。原作者、query、获取时间等未保存信息保持缺失。
- `reference.jsonl`：原有 AI 辅助开发参考原始字节；`reference_normalized.jsonl` 供导出使用。
- `owner_review/review.md`：逐篇原文、候选字段、证据片段和位置；所有新审核决定均为待审核。
- `extraction_run/worker/`：只含请求、冻结 final prompt、Schema 和空的 responses；不含参考答案。
- `manifest.json`、`validation.json`、`source_snapshots/`：来源、状态、哈希与恢复快照。
- 同级 `.zip` 和 `.zip.sha256`：包含完整数据包的备份，不含 `.env`。

这是恢复的开发材料。没有新预测时没有提取准确率，也不能作为新独立留出集。

## 重做真实检索

API Key 只写入本地 `.env`，权限设为 600；不要放入命令行参数、配置 YAML 或版本库。模板包括 WoS、OpenAlex 和 Semantic Scholar 的变量，只有已配置的来源才具备相应认证。

先做有界验证，再根据命中数确定完整采集规模：

```bash
.venv/bin/python scripts/run_search_plan.py queries/saline_paddy_research.yaml --limit 5 --max-records 5 --max-pages 1
.venv/bin/python scripts/run_search_plan.py queries/saline_paddy_research.yaml --limit 50 --max-records 500 --max-pages 10
```

按实际新生成的 plan manifest 路径合并，再补充摘要与导出。命令中的占位符必须替换为本轮输出：

```bash
.venv/bin/python scripts/merge_searches.py --plan-manifest data/raw/plans/ACTUAL_RUN_ID/plan_manifest.json --output data/processed/my_rebuilt_merged.jsonl
.venv/bin/python scripts/enrich_abstracts.py data/processed/my_rebuilt_merged.jsonl --output data/processed/my_rebuilt_enriched.jsonl
.venv/bin/python scripts/export_records.py data/processed/my_rebuilt_enriched.jsonl --output-prefix data/exports/my_rebuilt --include-abstracts
```

无 Excel 运行时使用 `--format csv`。每个新输出使用尚不存在的路径；已有文件不被覆盖。完整采集标志和来源错误以本轮 manifest/coverage report 为准。

## 新的冻结评测入口

`scripts/evidence/final_evaluation.py` 显式使用最终提示词、Validator v2、Metric v1/v2；不会修改历史 `batch_prepare`、`benchmark` 或冻结校验器。

```bash
.venv/bin/python -m scripts.evidence.final_evaluation prepare input.jsonl --reference reference.jsonl --output data/evidence_runs/my_dev_run
.venv/bin/python -m scripts.evidence.final_evaluation validate data/evidence_runs/my_dev_run
.venv/bin/python -m scripts.evidence.final_evaluation evaluate data/evidence_runs/my_dev_run --reference reference.jsonl --output data/reports/my_dev_metrics.json
```

默认 role 为 development，分数只作开发诊断，quality_gate 为 `NOT_APPLICABLE_DEVELOPMENT`。这个入口准备和校验请求，不自动调用模型；worker 按其独立目录内的 TASK.md 返回 envelope 后才有实际预测。缺失、过期、不受支持或额外响应均阻止完整评分。请求绑定原文、提示词、Schema 和输入哈希；参考必须在 prepare 时冻结。

新的 `--role new_holdout` 要求恰好 30 篇，与已知 Dev40 的 UID/规范化 DOI 不重叠，并且在生成目标提取请求前提供所有记录的审核日志。使用 `review` 子命令生成待审核包：

```bash
.venv/bin/python -m scripts.evidence.final_evaluation review new_input.jsonl --reference candidate.jsonl --output data/review/my_new_holdout
```

审核人逐篇修改/确认参考后重新生成与最终内容匹配的日志，记录 `ACCEPT`、真实审核人和带时区时间；日志同时绑定摘要记录和参考记录哈希。`prepare` 和 `evaluate` 都检查这些绑定。模板不会自动批准。最终质量门保持字段 v2 micro-F1≥0.80、findings v1 precision≥0.95/recall≥0.85、结构与 grounding 全部有效；开发集永远不报告该门通过。

旧的 Holdout30 registry 已丢失，新数据与旧 holdout 的重叠无法完整核实。因此新的结果必须披露为重建评测，不能接续旧冻结实验，也不能声称已证明与所有旧评测数据独立。

## 测试与交付

```bash
.venv/bin/python tests/run_offline.py -v --report data/reports/portable_tests.json
.venv/bin/python tests/run_offline.py --without-xlsx
.venv/bin/python tests/run_offline.py --suite historical
.venv/bin/python tests/run_offline.py --suite all
```

默认 portable 保留普通断言和仓库已保存材料的回归。历史审计测试的精确列表在 `tests/suite_profiles.py`，排除项写入 JSON 测试报告；`historical`/`all` 缺少历史输入时明确失败并返回 2。原有断言未删除，也没有把丢失数据转换成测试通过。GitHub Actions 只执行不依赖额外 Codex Excel 运行时的 portable 套件；本地完整 portable 继续覆盖实际 XLSX。

本次依赖版本基于 Python 3.12 环境固定在 `requirements.lock.txt`，一般安装仍可使用 `requirements.txt`。重建脚本、测试和配置可以随代码交付；真实运行数据保存在本地 `data/`，应另外保存 ZIP 及校验值。
