# v0.9 主题驱动科研决策：候选流程

这是可运行的初步流程，不是完整文献综述或可靠科研结论。输入主题后，流程生成 WoS 检索式，运行检索、去重和 Crossref 摘要补全，按题录和摘要生成筛选候选及带原文引句的候选观察，再进行一轮独立的 WoS 相关研究检索，生成问题与方案草稿并开启单独的模型评审会话。

```bash
python -m scripts.research_planning.workflow outputs/research_projects/my_v09_run \
  --topic '研究主题' --wos-env /path/to/local/.env
```

需要项目 Python 依赖、可用的 WoS Starter 凭据和已用 ChatGPT 登录的 Codex CLI。`--wos-env` 可省略；此时使用环境变量或当前仓库 `.env`。输出目录必须不存在，流程不会覆盖先前运行；凭据不应作为主题或命令输出提供。默认每个查询最多取 10 条且仅 1 页，可用 `--max-records`、`--max-pages` 调整。检索完成不等于检索完整；每轮的查询、采集和截断记录保存在项目目录中。

`sources.json`、`screening.json`、`evidence_candidates.json` 和 `candidate_evidence.sqlite` 保留来源、候选筛选和原文摘要引句。仅当引句能在对应摘要中匹配时才保存候选观察；缺摘要记录自动保留为不确定。`novelty_search.json` 和 `novelty_sources.json` 保存第二轮检索；`research_report.md` 是带来源标识的候选报告，`workflow_status.json` 记录阶段和失败。模型生成的草稿以及本地可追溯的引句匹配都需要科学复核。

目前尚未自动完成全文和补充材料的获取与准入、12 类全文字段提取、系统专利/产品查新、资源验证或人工独立评审；同一模型的单独会话也不等同于独立同行评审。查新只覆盖本次 WoS 查询，不能证明创新性；默认上限通常会导致截断。候选筛选的排除不应被视为正式纳排决定，研究方案不应直接作为正式试验依据。原有全文准入与提取模块尚待安全接入，旧的 100 篇 Pilot 不会自动重跑。

## 复用已有检索并限制外部调用

以下模式读取已有去重JSONL及其检索清单，按UID和DOI完全一致复用已有摘要，保留输入哈希；不会重做原检索、补全摘要或调用WoS查新。记录缺失摘要时筛选结果仍强制设为`uncertain`。模型调用使用ChatGPT登录的Codex CLI，按批处理，并在`model_calls/`保留请求、结构、响应和调用状态；模型不会访问传入的本地凭据。`--skip-novelty`的查新状态为`not_run`，不能将人工网页探索视为完整查新。

```bash
python -m scripts.research_planning.workflow outputs/research_projects/my_reuse_run \
  --topic '研究主题' --existing-records /path/to/merged.jsonl \
  --existing-manifest /path/to/merged.manifest.json \
  --existing-pool /path/to/cached_abstracts.jsonl --skip-novelty
```

流程完成表示生成候选材料，不代表全文证据或独立审查完成。旧输出中`independent_review.json`是模型单独会话的历史文件名；它不是独立事实验证或人类同行评议。绝大多数源记录没有摘要时，候选筛选将保留大量待核，不能说已完成有效人工初筛。遇失败会保存状态和已完成批次，但暂不支持从失败阶段自动续跑。
