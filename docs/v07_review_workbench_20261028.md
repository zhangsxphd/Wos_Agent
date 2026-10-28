# v0.7 人工审核包与小批量 Pilot（2026-10-28）

从 `codex/v07-literature-quality` 的 `0bc4e176a6e7ead5a3cd2c920be8b440469a6340` 独立开发。本轮不修改原始文献、历史解析、Gold 或冻结组件；本地生成目录按 Git 忽略，包含可能受机构授权限制的论文，不能公开提交。

## 使用

打开本地 `data/reviews/v07_review_round1/index.html`，按同目录“人工审核操作说明”操作。包包括 46 条 Springer 摘要、4 个 PDF 身份项目、统一年份规则和 280 个初筛条目。优先核对 7 篇摘要；自动线索不是人工纳入结论。页面通过浏览器本地存储保存中间状态，并可下载包含全部待定及已决定项目的 JSONL。审核人完成后，以新目录导入：

```bash
.venv/bin/python -m scripts.review.import_decisions \
  --manifest data/reviews/v07_review_round1/review_manifest.json \
  --decisions /path/to/wos_review_decisions.jsonl \
  --output data/reviews/imports/round1_001
```

导入同时核对全部源快照、每条任务对象和审核输入、原文及预览哈希；验证 UID、DOI、类型、选择、审核人和带时区时间，结果另存，不改变技术准入或 Canonical。输出目录必须不存在。CSV 模板也支持导入，但需填写状态、决定、审核人和时间。没有原文不能批准摘要或确认全文身份。人工确认身份不代表解析或科学内容通过。

## 数据与结果

重新质量核验共 280 条：156 篇 `technical_ready`、1 篇 `needs_review`、123 篇无可用全文。三份原来不一致的 Frontiers 解析以 `pdfplumber-page-v2` 独立重建并按原文和解析哈希、首页 DOI、环境和重解析核对；PDF 阅读顺序仍需人工确认。`10.32604/phyton.2022.018396` 严格 pypdf 读取失败、pdfplumber 未读出页面，未放松规则。独立的助手首页观察不等于人工审批。

原首页字符相似度使用 `SequenceMatcher` 默认 autojunk，不适合这些较长文本。现在以至多 12000 个词及标点 token、关闭 autojunk 计算，前四篇新比值约为 0.955、0.603、0.619、0.824；这些只是定位提示，不代表摘要正确。对照原页，`s00344-022-10786-z` 摘要是单栏通栏，`s11104-023-06371-2` 摘要横跨两栏；另外的差异仍需要逐篇原文核对。14 条旧出版商候选没有归档原始响应和获取时间，只能作为候选。

年份：两篇正式刊期 2027 年 1 月；其中 `10.1016/j.biortech.2026.135921` 的 PubMed 记录有 2026-09-22 ahead-of-print 日期，另一篇首次在线日期未确认。Crossref 创建时间不是发表日期。统一规则尚待研究者选择。

`data/pilot/v07_pilot_round1/` 保留 3 篇原始研究候选、27 行空白参考模板和来源定位提示；有需要时可复制行记录多条结果。41 个请求均为历史的按页请求，逐条检查请求哈希及锚点；检查发现按页拆分没有自动跨页上下文，图表完整性和逐句边界尚待人工核对。已排除已知 Dev40，历史 Holdout 登记缺失，不能称为独立留出集。没有模型调用、真实费用、人工答案或准确率。本项目现有按页请求不是完整的全文模型执行器；在人工参考完成并明确启动后，应先确定适用的全文 Worker、提示词版本和调用记录，再小批量执行，不使用历史摘要提示词假装已验证全文流程。

## 可选版面环境

Python 3.12.14，新建虚拟环境后安装 `requirements-pdf-layout.lock.txt`。记录 pdfplumber 0.11.9、pdfminer.six 20251230、pypdf 6.21.2 及探测输入哈希；新隔离环境已使用真实小 PDF 验证。普通 CI 不需要 pdfplumber，相关真实小文件测试在无可选依赖时跳过。解析或验证代码、解析器环境、canonical、清单、质量行、当前原文与解析文件任一变化都使 v2 质量快照失效，须重新核验。

```bash
python3 -m venv /tmp/wos-pdf-layout
/tmp/wos-pdf-layout/bin/pip install --index-url https://pypi.org/simple -r requirements-pdf-layout.lock.txt
```

离线常规测试不访问校园网或实时 API；真实文献检查、浏览器操作测试和模型表现不能由 mock 测试代替。

## 本轮测试

修改前 portable 基线 439 项通过。最终 `--without-xlsx` 离线套件 447 项通过、无跳过；另有 36 项历史材料依赖测试与 3 项可选 XLSX 测试被明确排除。真实小 PDF 用于可选解析器测试；网络、模型调用和论文语义正确性不在此测试范围。六个冻结组件哈希验证通过。浏览器验收在隔离临时会话中检查了原文预览、填写、下载和导入；测试决定只存在临时目录，没有写入真实人工审核。
