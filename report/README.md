# CS5478 final report

主文件是 `neurips_2026.tex`，最终阅读/提交文件是 `final_report.pdf`。报告使用仓库提供的 NeurIPS 2026 样式；采用非匿名 preprint 模式，只将页脚改为课程信息，没有改动 `.sty`、正文边距、字号或行距。它不是 NeurIPS 投稿或录用论文。

老师的要求是 **正文 5–8 页，参考文献不计页数**。本报告将参考文献另起一页；标题、摘要、图表、贡献说明和所有实质讨论均计入正文，没有借用“附录不计页数”的会议惯例。`checklist.tex` 是原始模板随附的会议投稿检查表，不是课程已要求的内容，因此保留原文件但不编入报告。

本次编译验收：**正文 7 页 + 参考文献 1 页，共 8 页**，US Letter（按总页数计也不超过 8 页）。已逐页渲染检查；无未解析引用、文字溢出或异常分页，13 个字体均已嵌入且没有 Type 3 字体。使用 pdflatex 时需安装 `cm-super`，否则等宽字体会退化为 Type 3 位图。记录见 `generated/pdf_validation.json`。后续改动内容后，应重新编译核验页数。

## 编译与复核

在项目根目录运行：

```bash
make -C report
```

支持 Tectonic 或安装完整宏包的 `pdflatex`。使用后者时自动运行两次以解析交叉引用；参考文献已写入主文件，无需 BibTeX。上传 Overleaf 时上传整个 `report` 目录，选择 `neurips_2026.tex` 为主文件即可；图和生成后的表格均已包含，编译不依赖 Python 或上层结果目录。

要重新核对仓库原始证据并生成表格、图和审计记录：

```bash
python3 report/prepare_results.py
make -C report
```

Python 脚本需要项目已有的 `matplotlib`/`numpy`；读取上层结果与规则实现，不调用模型、不重新运行仿真、不改写冻结结果。`generated/evidence_audit.json` 记录核验结果。PDF 可用 Poppler 检查：

```bash
pdfinfo report/final_report.pdf
pdftotext -layout report/final_report.pdf -
pdffonts report/final_report.pdf
```

## 证据来源与解释边界

| 内容 | 仓库来源 |
| --- | --- |
| 完整任务、控制器与成功判据 | `grasp_a/sequence_contracts.py`、`sequence.py`、`sequence_evaluation.py` |
| PBF 液体近似与边界 | `grasp_a/fluid.py` |
| 候选与语义 | `grasp_b/multifamily.py`、`semantics.py` |
| 实际参评 M1 Static v2 / M2 | `grasp_b/risk.py`、`motion.py`、`grasp_c/oracle.py` |
| 模型输入、三个提示词版本 | `grasp_c/prompts.py`、`tasks.py`；实际调用以 trial 中保存的 messages 为准 |
| R3 刚体与代理执行 | `results/oracle_20261103/r3_v3p_wide_strict_v2/` |
| 主结果的全量 PBF 表 | `results/oracle_20261103/r3_pbf_v3p_wide_strict_v2/` |
| 720 条 LLM trial、98 条去重 PBF 复核、170 组比较 | `results/c_evaluation_multimodel_20261109/` |
| M6 回顾性规则对照 | `grasp_c/rules.py`、`results/oracle_20261103/m6_prompt_rules.md` |
| R4 碰撞修复后敏感性分析 | `results/oracle_20261103/r4_v3p_wide_strict_v3/`、`r4_pbf_v3p_wide_strict_v3/` |
| 任务截图（图 2） | `figures/task_*.png`：取自演示稿，为 `results/sequence_v1_20261017/videos/with_landmarks/*_seed0_G02*` 演示的末帧，非基准 episode |
| 作者与 A/B/C 分工 | `presentation/PRESENTATION_SCRIPT_AND_TEAM_SPLIT.md` |

## 本次审核修正的主要问题

- 原目录只有 NeurIPS 示例论文，包括虚构作者、排版说明和无关参考文献；现已替换为英文项目报告，补齐研究问题、相关工作、方法、实验、分析、局限和结论。
- 课程要求覆盖会议示例中的“9 页”和附录豁免规则；以编译后的正文实际页数为准。
- 用全量 PBF 表作为主倒水结果，代理、本机复核和 R4 独立展示，避免混合不同实验口径。
- 补入 M6：规则本身达到 54/60，不能把 v2 的提升归结为 LLM 的独立推理优势；v2 和 M6 都应标为看过评估结果后的回顾性方法。
- seed 10–29 仅与 M4 训练分开，并非从未看过的盲测集；A1 仅移除明确任务文本，可行候选集仍带任务信息。
- 首次有效响应是 **712/720**，修复 **8** 次，回退 **2** 次；回退属于修复 trial，不应相加计算独立事件。部分旧说明写成 711。
- 统计以 `summary.json` 的逐 seed 记录重算，保留 **170** 组比较的 Holm 校正族，不沿用旧文档中其他汇总版本的校正数值。GPT mechanics handover 对 A1 是 **3 胜 / 0 负，p=0.25**；Gemini mechanics handover 对 A1 的 Holm p 是 **0.15625**，不可称为校正后显著。
- 精确均匀随机期望重新按场景平均：放置约 **15.9/20**、交接 **10.4/20**、PBF 倒水 **12.3/20**，三任务约 **38.6/60**，避免复制旧说明中的舍入误差。
- 保留 R3 的碰撞缺陷及 R4 修复结果，禁止把 R3 的完整 LLM 实验当成已在 R4 重做。
- M1/M2、M4 与语言模型的信息访问不同；M5 是项目内 surrogate，六维 wrench 扩展未纳入正式比较。

此次不新造数据、不重新调用模型、不改写源代码或历史实验，也不宣称已完成新 seed、真机或跨物体验证。作者姓名与分工沿用现有演示稿；学号未有可核实记录，未编造。
