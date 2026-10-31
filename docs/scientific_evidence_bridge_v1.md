# 逐篇候选到跨论文综合

正式入口是 `convert_historical_candidates.py --selection … --history-dir … --output …`，再运行 `evidence_synthesis.py --converted … --selection … --output-dir …`。DOI 与主题纳入标准由外部文件提供。转换和综合均不读取 `checked_evidence.json`；旧的 `--checked` 入口保留为历史兼容，不是正式综合。转换后可用 `check_candidate_conversion.py` 单独做既有参照检查。

转换器保留原候选的处理、对照、组均值、单位、误差、显著性、实验单位、重复、原文锚点、状态和失败原因。尚不能识别实验或数据集时，`experiment_id` 为 null，论文级设计信息与比较之间的关联为待核状态；不能根据 DOI 数量推断实验数量或独立性。已有中英文历史模型回复和字段的解析规则是格式适配器，不保证适用于其他格式。自动解析页码、表格位置只能标为 `program_parsed`；模型转述和实际 PDF 核查分别记录。可选 `--source-audit` 接收单独的、哈希匹配的 PDF 核查记录；这仍不是人工 Gold。

综合层适配到 `schemas/scientific_evidence_bridge_v1.schema.json`：未知实验 ID 允许为 null；`raw_evidence` 保留原始锚点与其状态。主要问题的候选必须同时符合预定研究对象、干预、结局与设计，其他相关结果分为间接背景。比较级别分为有条件并列、效应定义及基准一致时的待核定量比较，以及本轮不实现的统计合并。环境或尺度不同可以并列，不能只因为同向或显著性产生共同发现。共享组和重复表述标记为可能相关，不计为独立研究；不同 DOI 不证明数据独立。

当前适配器并不泛化解析所有 PDF、模型文本和实验设计；定位、时段、比较基准、实验独立性、效应定义或来源核验不足时保持未知。`comparison_context` 的来源状态本身须经外部核对，系统不会由此自动认定真实或原创。完整结果可能受历史语料的分享限制，运行时应遵守原始语料元数据中的限制。
