# 逐篇证据与跨论文综合：v1 数据约定

入口：`scripts/research_planning/evidence_synthesis.py`。它读取历史 100 篇导出的 JSONL 和既有 `checked_evidence.json` 中的 `studies`、`comparisons`；入口在整理证据前删除 `cross_study_comparisons`，计算也不读取单篇的 `result` 叙述或预先算好的降幅。比较脚本是独立的验收步骤，生成综合后才读取既有跨论文参照。它们不调用模型、检索或全文解析器。

输出结构以 `schemas/scientific_evidence_bridge_v1.schema.json` 描述。输出 `normalized_evidence.json`：`papers[] → experiments[] → treatments[]`，由 `comparisons[]` 的 `study_id` 和 `experiment_id` 关联测量及 `raw_evidence[]`。历史 `papers/fields/results/issues/historic_claims/supplement_files` 按 DOI 匹配后原样保存在每篇 `historic_records`；历史候选的原文摘录、页码、段落、表行列、哈希、字段状态、核查状态均不重写。`input_files` 保留每个 JSONL 的 SHA-256 或 missing。`source_checked_by_prior_assistant_not_human_gold` 指此前助手对原文的核查记录，不是人工 Gold。没有转录原句、文件哈希或作者假设的项目显式标记未知。历史记录中作者事实、作者解释与系统候选的原有分类保留；无法安全识别的限制标为 `limitations_unclassified`，不能当成作者明确报告的局限。独立方法学评价另列，不能冒充作者事实。

每条比较保存原始处理与对照、结局、时期、单位、误差、显著性原文、来源位置及冲突。能够把原有数字候选中两个明确标签与单篇处理名称、指标和单位对应时，直接以该候选的数字生成方向，并分别保留其 Candidate 状态及已核查值。两者不同立即标记冲突并停止把该条纳入共同发现。匹配不到候选时，使用既有单篇原文核查的数值；这不代表已从全部历史自由文本中自动抽取。平均通量、稻季累计量、跨处理平均值不能互换。单位冲突、不确定的文字表格冲突、未匹配的比较基准都不得自动修正。非显著不等于零效应。

自动综合按结局和水制度比较生成限定的共同方向。水源、肥料以及水管理与土地利用混杂分别保留，不进入水制度效应组。不同论文的同方向仅是各自对照内的描述，不证明相同效应或机制，也不等于效应量可合并。年度/田块分层不会计为独立论文，同 DOI 不重复计数；存在相同 DOI 或明确相同地点及年份时标记可能共享实验并拒绝独立比较。

定量比较要求两个不同论文具备来源已核查且全部明确匹配的 `comparison_context`：`population`、`environment`、`intervention_level`、`comparator_level`、`time_window`、`measurement_method`、`experimental_unit`、`independent_replicates`、`statistical_method`，以及 `source_status: source_checked`；还要求结局、处理类型、设计和单位一致，且无来源冲突、无跨处理摘要、无未对齐分层。当前参照不满足这些要求。允许的也只是展示各研究内部的效应，不计算合并估计。相同条件下两个显著比较方向相反时才标为**可能**相反，仍需人工复核；统计显著与否本身不能判为相反结果。

本版有意保守：它不能普遍解析自由文本、表格、所有实验层次和来源位置，也不能自动确认机制、统计功效、共享原始试验或创新性。`source_checked` 元数据不会被程序独立验证；未来使用者须核对原文并保证比较上下文的赋值真实。它不是系统性综述，也不是完整决策系统。
