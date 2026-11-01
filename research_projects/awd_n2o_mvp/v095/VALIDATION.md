# v0.9.5 受控项目验收

主题为普通稻田 AWD 与持续淹水的生育季累计 N₂O。只选择此前保存的两条题录，不代表完整检索。WoS 未获本轮授权，状态为 `authorization_unavailable`。未调用 WoS 或付费模型。原始全文沿用已保存的期刊公开文件；不会将仅有题录的论文判为不相关。

从仓库根目录启动（首次运行不带末尾两个确认参数，将暂停并生成候选草稿；下面的完整命令使用本次保存的用户确认）：

```sh
python -m scripts.research_planning.project run outputs/v095_real --topic '普通稻田 AWD 相对持续淹水如何影响生育季累计 N₂O 排放？' --config research_projects/awd_n2o_mvp/v095/input_config.json --confirmation outputs/v095_real/user_confirmation.json --review outputs/v095_real/user_review.json
```

运行依赖本地已有 `outputs/v092/fresh/wu.pdf`、`wu_table1.xlsx`、`outputs/v092/sources/nature2018.pdf` 与本次项目的确认文件；这些原始文件未随仓库发布。首次运行在各自项目中生成 `intake/manifest_draft.json`，需要按实际候选编辑并建立确认记录。不是从摘要生成数值，也不是无需人工的自动筛选。完成后再次运行证实候选及分析结果按哈希复用；输入或确认文件变化需要重新核查。

- 两条题录关键词召回、DOI 去重；两个 PDF 首页面 DOI 匹配并通过已有技术准入；Wu PDF、表格及另一篇 PDF 均实际运行既有候选提取。
- Wu 的本轮草稿沿用 27 项此前对实验描述、位置等的人工配置；用户本轮单独确认 Table 1 的 AWD 15U C11 与 CF 15U C6、均值及误差、每处理 3 个盆。数据由原 XLSX 读取，不是从既有结果 JSON 复制。用户确认是人工核查，不是独立 Gold。
- [Wu et al. (2022)](https://doi.org/10.5194/soil-8-645-2022) Table 1, C11/C6（PDF 物理页 7）：4.48±0.12 对 4.02±0.30 kg N₂O/ha；程序计算相对变化约 +11.44%。仅支持该处理条件下的单项观察，不能据此推广。
- 新接入 [2018 年研究](https://doi.org/10.1038/s41598-018-35939-7) 使用其实际公开全文从头生成候选，未为其编写完整 manifest。助手定向阅读 PDF 第 8 页 Methods：2015 年 CSW 对照未独立重复且其值未报告，2016 年改为 AWD；因此未形成合格配对，待人工进一步核查。这一判断不是独立人工 Gold。
- 仅 Wu 一项比较进入综合，程序正确输出跨研究证据不足；未判断普遍效应或研究空白。需继续人工核实设计细节及第二项可比研究。
- `validation/` 为实际运行结果快照，包含规范化证据、综合、报告、运行清单、待办和用户确认记录。为避免公布机器绝对路径，带 `.snapshot.json` 的文件仅将路径前缀替换为 `<REPO_ROOT>`，因此它们是审阅副本而非可执行输入；本地 `outputs/v095_real/` 保留未改动的运行原件和候选哈希。
