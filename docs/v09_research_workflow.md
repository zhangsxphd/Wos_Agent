# v0.9 主题驱动科研决策：候选流程

这是可运行的初步流程，不是完整文献综述或可靠科研结论。输入主题后，流程生成 WoS 检索式，运行检索、去重和 Crossref 摘要补全，按题录和摘要生成筛选候选及带原文引句的候选观察，再进行一轮独立的 WoS 相关研究检索，生成问题与方案草稿并开启单独的模型评审会话。

```bash
python -m scripts.research_planning.workflow outputs/research_projects/my_v09_run \
  --topic '研究主题' --wos-env /path/to/local/.env
```

需要项目 Python 依赖、可用的 WoS Starter 凭据和已用 ChatGPT 登录的 Codex CLI。`--wos-env` 可省略；此时使用环境变量或当前仓库 `.env`。输出目录必须不存在，流程不会覆盖先前运行；凭据不应作为主题或命令输出提供。默认每个查询最多取 10 条且仅 1 页，可用 `--max-records`、`--max-pages` 调整。检索完成不等于检索完整；每轮的查询、采集和截断记录保存在项目目录中。

`sources.json`、`screening.json`、`evidence_candidates.json` 和 `candidate_evidence.sqlite` 保留来源、候选筛选和原文摘要引句。仅当引句能在对应摘要中匹配时才保存候选观察。题名或摘要支持的原始筛选判断、规则调整、证据用途和信息充分程度分别保存；缺摘要不再统一覆盖判断，候选仍需人工核查。`novelty_search.json` 和 `novelty_sources.json` 保存第二轮检索；`research_report.md` 是带来源标识的候选报告，`workflow_status.json` 记录阶段和失败。模型生成的草稿以及本地可追溯的引句匹配都需要科学复核。

目前尚未自动完成全文和补充材料的获取与准入、12 类全文字段提取、系统专利/产品查新、资源验证或人工独立评审；同一模型的单独会话也不等同于独立同行评审。查新只覆盖本次 WoS 查询，不能证明创新性；默认上限通常会导致截断。候选筛选的排除不应被视为正式纳排决定，研究方案不应直接作为正式试验依据。原有全文准入与提取模块尚待安全接入，旧的 100 篇 Pilot 不会自动重跑。

## 复用已有检索并限制外部调用

以下模式读取已有去重JSONL及其检索清单，按UID和DOI完全一致复用已有摘要，保留输入哈希；不会重做原检索、补全摘要或调用WoS查新。缺摘要不再一律改写；可以按有来源的题名作临时纳入或排除，可能具有间接用途的研究单列并保守待核。模型调用使用ChatGPT登录的Codex CLI，按批处理，并在`model_calls/`保留请求、结构、响应和调用状态；模型不会访问传入的本地凭据。`--skip-novelty`的查新状态为`not_run`，不能将人工网页探索视为完整查新。

```bash
python -m scripts.research_planning.workflow outputs/research_projects/my_reuse_run \
  --topic '研究主题' --existing-records /path/to/merged.jsonl \
  --existing-manifest /path/to/merged.manifest.json \
  --existing-pool /path/to/cached_abstracts.jsonl --skip-novelty
```

流程完成表示生成候选材料，不代表全文证据或独立审查完成。旧输出中`independent_review.json`是模型单独会话的历史文件名；它不是独立事实验证或人类同行评议。绝大多数源记录没有摘要时，候选筛选将保留大量待核，不能说已完成有效人工初筛。中断在筛选阶段时可使用下方恢复命令复用已完成批次；模型复核不是独立事实验证。


## 筛选断点恢复与有限全文综合

`recovery` 在独立目录核验原始书目哈希、已完成批次的输入ID/内容、提示词哈希、模型标识、原始响应和调用日志；仅以原始模型输出重新应用规则，不重新调用已完成的批次。默认不重试失败记录，不获取全文或调用WoS。缺失的全文与模型失败分别留在清单中。已经核验的全文证据可在少量记录仍待核时继续生成有限研究综合。

```bash
python -m scripts.research_planning.recovery /path/to/interrupted_run /path/to/new_checkpoint \
  --corpus-root /path/to/old_workspace --records /path/to/original.jsonl \
  --pool /path/to/old_enriched.jsonl --fulltext-manifest /path/to/fulltexts.jsonl \
  --expected-calls /path/to/original_model_calls.json \
  --confirmed-evidence /path/to/source_checked_evidence.json
```

重复同一命令复用来源审计和已完成结果；如果要显式尝试尚未完成的模型记录，额外传 `--retry-missing`，执行前仍应确认ChatGPT额度和使用授权。失败的模型调用及手工兜底分开记录；历史版本或来源哈希改变时会拒绝续跑。当前恢复入口针对筛选阶段及经核对证据的有限综合；尚不支持把任意全文下载、未生成的模型报告或整个流程从任意步骤自动续跑。证据文件及示例见 [本轮结构化证据](../research_projects/saline_paddy_irrigation/v09_recovery_20261031/checked_evidence.json)。
