# 全文获取与摘要补充记录（2026-10-27）

280 篇去重文献中，已取得并核对 DOI 的全文 PDF 为 **157 篇**，有摘要的文献为 **192 篇**。还缺 123 篇全文和 88 篇摘要。检索结果属于候选文献池，尚未完成研究范围筛选或科学证据标注。

## 可直接使用的文件

- `outputs/fulltext_20261027/fulltext_verified_20261027.xlsx`：Records、Searches、Summary、Abstracts 四张表。280 条记录、4 条检索式、192 篇摘要；摘要与规范 JSONL 逐条一致。
- `outputs/fulltext_20261027/fulltext_verified_20261027.csv`：同一批文献的完整 CSV。
- `outputs/fulltext_20261027/fulltext_inventory_20261027.csv`：280 篇的全文状态、PDF 路径、页码文本路径、来源和文件哈希。
- `outputs/fulltext_20261027/remaining_fulltexts_20261027.csv`：123 篇尚未保存成功的全文。
- `outputs/fulltext_20261027/local_abstract_review_queue_20261027.csv`：14 篇已有全文但摘要需处理跨页或双栏阅读顺序的文献。
- `outputs/fulltext_20261027/scope_review_queue_20261027.csv`：2 篇 WoS 返回的出版年为 2027 的记录；检索式限定 2022–2026，需要核对 early access 年与正式卷期年，未自动删除。
- `data/processed/fulltext_verified_20261027_v5.jsonl`：当前规范数据，保留旧的提供商响应、摘要选择理由与新增原文锚点。
- `data/fulltext/runs/consolidated_20261027_final/fulltexts.jsonl`：当前全文清单。
- `data/fulltext/raw/`：按 SHA256 保存的 PDF；`data/fulltext/parsed/`：按页保存的文本及解析版本。
- `data/recovery/fulltext_snapshot_20261027.zip` 与 SHA256 文件：本阶段备份，排除密钥、浏览器登录信息、虚拟环境和重复的下载收件箱。

## 获取和核对方法

全文来源共 157 篇：公开出版商浏览器下载 67 篇、校园认证浏览器保存 58 篇、开放获取链接及其落地页下载 32 篇。校园认证组包括 Springer 47 篇、Wiley 10 篇及 ScienceDirect 1 篇。

156 篇为下载的 PDF；ScienceDirect 首篇 DOI `10.1016/j.fcr.2026.110607` 的原始下载返回网络错误，但认证会话可阅读全部 15 页，已使用浏览器“打印为 PDF”保存 15 页副本。其清单明确记录 `acquisition_method=browser_print_to_pdf`，不会冒充出版商原始下载字节。机构权限不等于开放许可证，校园下载的许可证字段保留未知。

每份全文均核对原始文件 SHA256、有效 PDF、可提取文字以及首页 DOI。DOI 比较仅规范化空白和排版连字符。3 篇 Frontiers 文献的原解析器输出字体字形名称，改用 pdfplumber 获取可读数字并核对 DOI；旧解析结果完整保留。

原文和原有规范数据没有被覆盖。新版解析另存为 `pypdf-page-v2-fonttools` 或 `pdfplumber-page-v1`，旧摘要字符锚点仍可重现。所有来源内容均保存在独立文件，未调用模型进行证据标注、Gold 生成或评估。

## 摘要补充与质量状态

192 篇摘要来源为 Crossref 115、OpenAlex 43、出版商 PDF 33、出版商 HTML 1。

本阶段新增的 32 个 PDF 摘要取自首页独立 Abstract 和 Keywords 标题之间的原样文本，保留换行、断词和字符起止位置，不进行改写。Excel 的 `abstract_status=selected_pdf_layout_review` 标识这些记录；它们仍需逐篇核对排版，不能视为人工验证过的摘要。另 1 篇 PDF 摘要在先前步骤中按双栏阅读顺序保存了两个明确来源片段。ScienceDirect 摘要来自保存页面中的出版商摘要元素，仅保留公开摘要片段，未归档含登录界面的完整网页。

此前 1 篇 Crossref/OpenAlex 摘要冲突，已与 Wiley PDF 核对并记录解决依据。该结论及所有版本保留在 `data/fulltext/abstract_resolution_20261027.json` 和相邻恢复报告中。

88 篇缺摘要文献中，14 篇已有 PDF；另 74 篇仍需补充来源。剩余 123 篇全文主要包括 Elsevier 96、Wiley 6、Taylor & Francis 6 及其他出版商 15。ScienceDirect 已有学校全文权限，但普通 PDF 下载曾返回网络错误，下载入口还可能保存验证页面，不能把这些页面计为全文。Wiley 的 6 篇补充下载也未取得通过校验的文件；其余出版商状态详见清单。没有据此断言这些文献无法取得，也没有绕过验证或付费访问。

## 下一步顺序

1. 优先处理 14 篇本地 PDF 的跨页、双栏摘要，逐段保存页码与原文锚点。
2. 核对 32 篇新提取摘要的阅读顺序，并检查 2 篇出版年边界记录。
3. 按“盐碱稻田—水管理—碳过程/微生物”的研究范围进行题名和摘要筛选；检索池中包含玉米、小麦及综述等候选，不能直接全部纳入。
4. 根据筛选结果确定需要优先补取的全文，再处理剩余出版商下载与机构授权。避免对未筛选的全部候选进行低效人工下载。
5. 来源与纳入范围确认后，再准备证据候选供逐篇审核。本阶段没有启动此前讨论的 30 篇多代理标注。

## 验证

368 项 portable 离线测试全部通过；36 项依赖丢失历史实验数据的审计测试明确排除，未计为通过。Excel 四张表已渲染检查，独立读取保存文件确认全部摘要与规范数据一致。32 个新增 PDF 摘要的字符锚点逐一重放成功。v0.7 的 6 个冻结组件哈希保持一致，`git diff --check` 通过。

验证结果见 `data/reports/fulltext_final_portable_tests_20261027.json`、`data/reports/fulltext_final_export_validation_20261027.json` 和 `data/reports/fulltext_final_summary_20261027.json`。数据与导出目录已由 Git 忽略；没有提交或推送研究 PDF。
