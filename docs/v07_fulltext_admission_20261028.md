# 全文身份与证据准入专项审查（2026-10-28）

基准为 GitHub `codex/v07-reliability-audit` 的 `f4d408855078fa90534bde3944f006b09a988e14`；本次在独立本地分支 `codex/v07-fulltext-reliability` 开发。原始文献池、全文、旧解析、缓存、历史 Gold 与冻结组件保持不变；只在新目录生成审核结果。技术准入不代表人工核验或科学结论正确。

## 实际流程与验证边界

`WoS canonical → resolver / 浏览器导入 → raw PDF/TEI → parser JSON → prepare_sections / manual extraction → validate_fulltext → combine → screening`。摘要提取/导出是独立路径。原始文件的 `available` 只说明已获取；现在只有 `scripts/fulltext/admission.py::inspect_fulltext` 从当前文件重新确认记录 UID/DOI、原文哈希、格式、文章身份、解析器及重新解析结果一致时，才允许准备全文 Worker 请求或使用全文证据。`validate_fulltext` 继续验证段落、偏移、原文和部分关联/因果提示；它本身不验证文件身份，也不全面判断科学语义。

入口核查：`screening_profile.classify` 和 `screen_file`、`fulltext.evidence.prepare_sections`、`combine`、`build_fulltext_evidence` 共享准入；`recover_pdf_abstracts` 在从 PDF 产生新摘要前复用准入。`validate_fulltext` 和 `anchor_text` 是纯文本校验原语，独立调用不能据此声称原文件可信。`build_evidence_matrix` / `evidence_export` 使用已有摘要 evidence schema，不自动消费 `fulltext_evidence`；`resolve_fulltexts` 和 `acquire_fulltexts` 的 `available` 仍是获取状态，不能作为证据准入凭证。`consolidate_fulltexts` 是候选清单构建，不是准入捷径。直接绕过项目正式入口或擅自读取文件的外部程序不在此保护范围内。

## 已复现并修复

| 问题 | 证据与修复 | 边界 |
|---|---|---|
| 筛选可绕过全文证据验证 | 修改前 `test_fulltext_screening_gate.py` 以非空全文研究对象及缺失 manifest 复现输出成功；现在缺失 manifest、路径、SHA 或身份会拒绝，直接调用 `classify` 也执行同一检查。空全文层仍支持摘要工作流。 | API 内部的低层校验原语不是完整准入。 |
| 旧 `available` TEI 未验证文章身份 | `tei_parser.tei_identity` 只读取 TEI header 的文章 `sourceDesc/biblStruct/idno`、其 `analytic/idno` 或 `publicationStmt/idno`，规范化后完整比较 DOI；参考文献和正文的 DOI 不参与判断。对缺少、错误、多值冲突分别标记。旧缓存每次重新核验。 | DOI 未检出不证明论文错误；标题相似不能自动晋升。限定路径会让其他合法 TEI 格式进入人工复核。[GROBID header 文档](https://grobid.readthedocs.io/en/latest/training/header/) 将 `idno` 用于文章标识符。 |
| 旧解析 JSON 未与当前原文有效绑定 | `inspect_fulltext` 读取并计算原文 SHA、解析 JSON SHA，用当前对应 parser 重新解析并比较完整对象；已有 parser 环境记录也需一致。准备请求再检查当前文件，并把 `parsed_sha256` 和 parser 写入请求；合并与筛选再次检查。新解析结果保存为带原文与解析哈希的新文件，不覆盖旧文件。 | 环境版本变化、另一解析器或旧 JSON 无法复现时待复核；不要仅凭文件名或布尔标记重新认证。重解析比缓存耗时。 |
| 部分下载和历史清单可能把信息不足误认为已验证 | PDF 要求完整 DOI 的首页匹配并进行严格 PDF 读取；TEI 检查头部 DOI 与正文存在。新获取的不可确认文件以 `needs_review` 保留，继续尝试后续来源；单篇准入失败不会准备 Worker 请求，并在批量结果中记录原因。 | PDF 第一页可能引用其他文章 DOI；准确 DOI 只是技术身份线索，复杂排版、OCR、跨页 DOI 或引用需人工核查。严格 PDF 检查可能保守拒绝可修复文档。 |
| 本地路径和解析输入 | 原文和解析路径拒绝绝对路径、`..`、越界符号链接；XML 限制 30 MB、拒绝 DTD/实体与 NUL，gzip 采用有上限读取。拒绝 HTML 等错误内容。 | 不能保证完全防御所有恶意 PDF、解析器漏洞或文件系统竞争；URL DNS 重绑定仍非本轮完整修复。 |
| 来源文本中的指令 | 全文分块请求明确声明论文文本为不可信材料，不能修改任务、schema 或权限。 | 这是指令边界而非数学上的提示注入防护，需要实际模型对抗评估和最小权限执行环境。 |

`identity_check`、`identity_status`、`parse_status`、`status` 是分离信息；只有现场 `inspect_fulltext` 成功才有 `technical_pass`。如果清单带 `parsed_sha256` 和 parser，二者也需匹配。该结果明确 `human_review_status=not_assessed`；不得作为人工审批或科学可靠性的证明。

## 本地实际质量审查

只读输入 `data/processed/fulltext_verified_20261027_v5.jsonl` SHA256 `f07c3908dac7e0950b8f199c8c74b26522203a2e81b009de36c916fd7edb837a`；全文清单 `data/fulltext/runs/consolidated_20261027_final/fulltexts.jsonl` SHA256 `e7d183b7158eaf545da5d918d0e8a5b32dd0bb0263f2230fa0a13a6e0ebac662`。新目录 `data/reports/fulltext_admission_20261028_final/` 保存完整逐条 JSONL、摘要 JSON、全部待办 CSV 和重点清单。数据和机构授权文件仍留在本地。

| 项目 | 本次结果与解释 |
|---|---|
| 候选论文 / 有来源摘要 | 280 / 192；其中 160 为技术上可作为摘要提取候选，尚非科学审核通过；32 条 PDF 摘要需版面审阅，88 条无摘要。 |
| PDF | 历史清单 157 份；新准入下 152 份可技术处理，4 份首页 DOI 缺乏可直接验证的证据、1 份严格解析失败，5 份需复核。152 不是人工批准的科研证据。 |
| TEI | 当前这份 280 条全文清单没有 TEI，因此没有可计算的真实 TEI 准入数；测试中的 TEI 均为人工构造 fixture。 |
| 无可用全文 | 123 条，其中旧清单为 78 unavailable/pending、45 error；不能直接归因于不存在论文。 |
| 现有全文 Evidence 锚点 | 此次未提供已有 Evidence 输入，280 条标为未检查；不将其当作验证通过或失败。`--evidence` 可指定后在逐条审查中调用源文件和锚点检查。 |
| 人工复核 | 32 条摘要版面、14 份有 PDF 但缺可靠摘要、2 条年份问题，按 UID 去重为 48 篇。`abstract_year_review.csv/jsonl` 列出 DOI、原文路径、复核内容和留空的人工决定；没有自动填写人工批准。 |

`quality_summary.json` 的旧 `identity_hash_checked` 是历史诊断口径，不等于新准入。新口径看 `evidence_admission_counts`。`technical_ready` 不检查布局是否适合科研提取，也不意味着模型输出准确；缺失历史材料的行和未传入 Evidence 的锚点标记为未检查。旧 PDF 清单的 157 与新准入 152 使用不同规则，不应视为文件丢失。

## 测试和未完成的验证

基准 portable：404 项通过、36 项历史审计排除；修改后 portable 426 项通过，其中包括 3 项 XLSX 集成测试；`--without-xlsx` 选择 423 项，全部通过。本次测试包括真实小型 TEI/PDF 文件、锚点和文件系统，以及模拟下载与缓存；不代表真实 API、模型性能或出版社全文内容验证。本轮没有真实 API 调用。此前 GitHub Actions 只覆盖基准提交，本地改动未推送，尚无新的远程 CI。历史 36 项审计因 19 个数据文件缺失未执行；六个冻结组件 SHA256 通过。

原 Dev40 的 v1 0.7817、v2 0.8564、findings 0.9532 和 methods 0.5797 是历史开发结果，并非本次重算。参考答案有 AI 辅助且未全部独立人工审阅；历史预测及 holdout 登记缺失，不能确定 methods 错因或旧 holdout 重叠。结构与字符定位验证不能独立验证研究类型、实验对照、数值单位、方向、显著性或因果解释。已有人工参考审核材料位于 `data/recovery/dev40_rebuilt_20261027/owner_review/review.md` 与 `decisions.template.jsonl`；它是开发数据审核，不是新的独立 holdout。新评估应先固定参考和指标、建立独立人工裁决，避免以当前开发集继续调参冒充泛化成绩。

## 剩余风险和下一步

1. 对 48 篇重点清单与 5 份全文按原文人工核对；对双栏、跨页、图表、公式、参考文献混入和解析顺序保留审核记录。不要仅凭 DOI 自动判断文章事实、全部章节和作者主张。
2. PDF 缺失 DOI 可能只是编码或 DOI 位于后页；DOI 冲突、相同 DOI 的勘误/版本、含目标 DOI 的首页引用、错误的出版商链接都可能导致误判。当前没有供用户签署的独立人工准入机制，待复核文件不会自动升级。
3. `pypdf` 和 TEI 解析存在工具和资源边界；其他 parser 与环境变化可能触发误拒绝。没有覆盖所有文件写入并发、OCR、恶意 PDF、DNS 重绑定或网络重定向问题。
4. 逐字段复核方法、处理组、单位、效果方向、显著性和相关/因果；用独立双人审核参考与冻结新指标评价后，再进行受控小批量全文模型提取。优先实现可信引用追踪和跨文献证据比较；暂缓自动创新性评分及大规模智能体提取。

只读复核示例：

```bash
.venv/bin/python -m scripts.quality.audit data/processed/fulltext_verified_20261027_v5.jsonl \
  --fulltexts data/fulltext/runs/consolidated_20261027_final/fulltexts.jsonl \
  --search-manifest data/processed/fulltext_verified_20261027_v5.manifest.json \
  --output data/reports/new_fulltext_audit
```

可另传 `--evidence` 指向现有 Evidence JSONL；输出目录必须不存在。项目不要求实时网络参与普通 CI。
