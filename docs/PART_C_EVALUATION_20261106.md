# C 评估补全：多模型（GPT & Gemini）与 Prompt v1/v2 对比评估（2026-11-06 / 2026-11-09）

在第三轮冻结候选与场景上完成真实 LLM 选择器（M3 含任务描述 vs A1 去任务描述消融）的系统评估，涵盖两个模型系列（`gpt-5.6-luna` 与 `gemini-3.8-flash`）以及两个提示词版本（初始 `prompt_version="v1"` 与消解零分歧义及物理约束的 `prompt_version="v2"`），并同步对接第三轮全量真实粒子流体表（`r3_pbf_v3p_wide_strict_v2`，30 个 seed 共 458 条可行倒水 episode）。

核心结论分两层：
1. **在初始 `prompt_version="v1"` 下，单纯拼接简短任务句并未稳定超越启发式基线**：GPT（`gpt-5.6-luna`）在搬运/交接/倒水（全量 PBF）上 M3 为 15/20、4/20、13/20（A1 为 17/20、5/20、13/20）；Gemini（`gemini-3.8-flash`）在搬运上体现出显著的任务区分（M3 19/20 vs A1 9/20，精确检验 $p=0.0020$），但在交接（M3 5/20 vs A1 1/20）和倒水代理（9/20 vs 9/20）上仍受限于 `geometry_score=0.0`（杯沿/把手未评分）被模型当作零质量以及 `mouth=hand over the opening` 标签对顶抓/杯沿的误导。
2. **在消解表示歧义与编码运动学约束的 `prompt_version="v2"` 下，带任务信息的 `M3_GEMINI_V2` 在全部三项任务上大幅提升并显著优于无任务消融 `A1_GEMINI_V2`**：搬运达到 **19/20（95%）**、交接达到 **20/20（100%，追平 Oracle 上界）**、倒水代理达到 **18/20（90%）**、倒水全量真实流体 PBF 达到 **16/20（80%，并列最高）**。其中交接任务上 `M3_GEMINI_V2`（20/20，全选杯沿）相对 `A1_GEMINI_V2`（4/20，因缺少交接走廊约束回退至侧抓）净胜 16 个 seed（**+80 个百分点**，精确符号检验 $p=3.05\times 10^{-5}$，Holm 校正后 $p=0.0023$）；倒水代理上净胜 14 个 seed（**+70 个百分点**，精确检验 $p=1.22\times 10^{-4}$，Holm 校正后 $p=0.0089$），倒水全量 PBF 上提高 **+25 个百分点**（16/20 vs 11/20）。这表明 LLM 抓取选择的性能瓶颈不在于 JSON 结构化推理能力，而在于**静态几何分数的语义消歧（Unscored vs Zero-Quality）与下游操纵走廊/倾倒运动学约束的显式表达**。

## 实验设置与可复现性

- 代码基点：基于 `main` 最新合入版本 `4829fe9`（整合 `6403ba0`、`ac0780b` 与上游全量 PBF 表 `90d4c60`）。
- 复用只读的第三轮冻结数据：`results/oracle_20261103/r3_v3p_wide_strict_v2/`（搬运、交接与代理倒水全量表）及 `results/oracle_20261103/r3_pbf_v3p_wide_strict_v2/`（30 个 seed 全部 458 条可行倒水候选的默认 256 粒子 PBF 流体表），共 116 个 `multifamily_v3p` 候选，`semantic_v1` 公开标签，`wide_strict_v2` 场景。
- 评价集为留出 seed 10–29（共 20 个 seed），三种完整序列任务（`transport_place`、`handover_transfer`、`pour_water`），每个方法/任务/seed 一次模型采样。M4 使用的训练 seed 0–9 与此不重叠；鉴于 seed 10–29 的分布特征已在第三轮实验中被分析，本实验属于留出集上的回顾性对比与提示词消融研究。
- 评估涵盖两类真实 LLM 模型与两个提示词版本（共 6 组 LLM 方法，360 条 trial）：
  - `M3_CODEX` / `A1_CODEX`：请求 `gpt-5.6-luna`（`reasoning_effort=low`），使用 `prompt_version="v1"`。
  - `M3_GEMINI` / `A1_GEMINI`：请求 `gemini-3.8-flash`（`thinking_level=low`），使用 `prompt_version="v1"`。
  - `M3_GEMINI_V2` / `A1_GEMINI_V2`：请求 `gemini-3.8-flash`（`thinking_level=low`），使用改进后的 `prompt_version="v2"`。
  - 所有方法中，M3 传入 `task_id` 与 `task_intent`；A1 移除显式任务 ID 与任务描述，其余系统提示词、五字段候选（`id/region/approach/gripper_width/geometry_score`）与输出 schema 完全一致。
- 模型不接触任何执行结果、oracle 标签、真实位姿、流体状态或失败原因。输入候选已按当前任务运动学可行性筛选，因此 A1 严格代表“移除显式任务描述”的消融。
- 物理成功只计数值有效的 `completed/execution_failed`；置信区间为按 seed 计算的 95% Wilson 区间；方法间使用配对精确符号检验，并对全部列出比较执行 Holm 多重检验校正。

配置文件：[c.r3.codex.json](../configs/c.r3.codex.json)、[c.r3.codex.v2.json](../configs/c.r3.codex.v2.json)、[c.r3.gemini.json](../configs/c.r3.gemini.json)、[c.r3.gemini.v2.json](../configs/c.r3.gemini.v2.json) 与多模型汇总配置 [c.r3.multimodel.json](../configs/c.r3.multimodel.json)。
运行记录与审计产物：
- 初始 GPT 单模型目录：[results/c_evaluation_20261106/](../results/c_evaluation_20261106/)（含 [manifest.json](../results/c_evaluation_20261106/manifest.json)、[summary.json](../results/c_evaluation_20261106/summary.json)、[summary_with_pbf_table.json](../results/c_evaluation_20261106/summary_with_pbf_table.json) 与 [validation.json](../results/c_evaluation_20261106/validation.json)）。
- 完整多模型（GPT `v1` + Gemini `v1/v2`）目录：[results/c_evaluation_multimodel_20261109/](../results/c_evaluation_multimodel_20261109/)（含 360 条密封 [trials/](../results/c_evaluation_multimodel_20261109/trials/)、[manifest.json](../results/c_evaluation_multimodel_20261109/manifest.json)、[summary.json](../results/c_evaluation_multimodel_20261109/summary.json)、[report.md](../results/c_evaluation_multimodel_20261109/report.md)、[method_results.csv](../results/c_evaluation_multimodel_20261109/method_results.csv) 与 [validation.json](../results/c_evaluation_multimodel_20261109/validation.json)）。

## 方法比较（留出集 seed 10–29）

每格为 `成功数/有效分母`（计划均为 20 个 seed）；完整置信区间与逐对检验见 [多模型机器报告](../results/c_evaluation_multimodel_20261109/report.md)。

| 方法 | 模型与版本 | 搬运 (`transport_place`) | 交接 (`handover_transfer`) | 倒水代理 (`proxy_margin`) | 倒水 PBF（上游全量表 `r3_pbf`） | 倒水 PBF（本机复核 `pbf/`） | 三任务合计（搬运+交接+PBF 全量表） |
|---|---|---:|---:|---:|---:|---:|---:|
| M0 随机 | 几何无关基线 | 15/20 (75%) | 11/20 (55%) | 13/20 (65%) | **16/20 (80%)** | 16/20 (80%) | 42/60 (70.0%) |
| M1 静态 v2 | 静态杯身余量 | 17/20 (85%) | 5/20 (25%) | 12/20 (60%) | 13/20 (65%) | 15/20 (75%) | 35/60 (58.3%) |
| M2 任务风险 | 手工风险加权 | 19/20 (95%) | 11/20 (55%) | 13/20 (65%) | 15/20 (75%) | 15/20 (75%) | 45/60 (75.0%) |
| M4 族×任务查表 | Seed 0–9 经验先验 | 18/20 (90%) | 17/20 (85%) | 14/20 (70%) | 12/20 (60%) | 13/20 (65%) | 47/60 (78.3%) |
| M5 部件启发式 | 论文式部件表 | **20/20 (100%)** | 5/20 (25%) | 12/20 (60%) | 13/20 (65%) | 15/20 (75%) | 38/60 (63.3%) |
| `M3_CODEX` | `gpt-5.6-luna` (`v1` + 任务) | 15/20 (75%) | 4/20 (20%) | 7/20 (35%) | 13/20 (65%) | 14/20 (70%) | 32/60 (53.3%) |
| `A1_CODEX` | `gpt-5.6-luna` (`v1` 去任务) | 17/20 (85%) | 5/20 (25%) | 12/20 (60%) | 13/20 (65%) | 15/20 (75%) | 35/60 (58.3%) |
| `M3_GEMINI` | `gemini-3.8-flash` (`v1` + 任务) | 19/20 (95%) | 5/20 (25%) | 9/20 (45%) | 14/20 (70%) | 15/20 (75%) | 38/60 (63.3%) |
| `A1_GEMINI` | `gemini-3.8-flash` (`v1` 去任务) | 9/20 (45%) | 1/20 (5%) | 9/20 (45%) | 14/20 (70%) | 15/20 (75%) | 24/60 (40.0%) |
| **`M3_GEMINI_V2`** | **`gemini-3.8-flash` (`v2` + 任务)** | **19/20 (95%)** | **20/20 (100%)** | **18/20 (90%)** | **16/20 (80%)** | **17/20 (85%)** | **55/60 (91.7%)** |
| `A1_GEMINI_V2` | `gemini-3.8-flash` (`v2` 去任务) | 18/20 (90%) | 4/20 (20%) | 4/20 (20%) | 11/20 (55%) | 13/20 (65%) | 33/60 (55.0%) |
| 均匀随机期望 | 可行集均值 | 15.8/20 (79%) | 10.4/20 (52%) | 9.6/20 (48%) | 12.3/20 (61%) | — | 38.5/60 (64.2%) |
| Oracle 上界 | 事后最优 | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) | — | 60/60 (100.0%) |

![多模型任务成功率与 Wilson 95% 区间](../results/c_evaluation_multimodel_20261109/success_rates.png)

### 关键配对统计（M3 vs A1 任务消融 & Prompt v2 vs v1 对比）

| 对比组 | 任务 | 胜/负（20 个配对 seed） | 成功率差（百分点） | 精确符号检验 $p$ | Holm 校正 $p$ |
|---|---|---:|---:|---:|---:|
| `M3_CODEX` vs `A1_CODEX` (`v1`) | 搬运 / 交接 / 倒水代理 / 倒水 PBF 表 | 0/2 / 2/3 / 1/6 / 2/2 | -10 / -5 / -25 / 0 | 0.5000 / 1.0000 / 0.1250 / 1.0000 | 1.0000 |
| `M3_GEMINI` vs `A1_GEMINI` (`v1`) | 搬运 (`transport_place`) | **10/0** | **+50** | **0.0020** | 0.1406 |
| `M3_GEMINI` vs `A1_GEMINI` (`v1`) | 交接 / 倒水代理 / 倒水 PBF 表 | 4/0 / 0/0 / 0/0 | +20 / 0 / 0 | 0.1250 / 1.0000 / 1.0000 | 1.0000 |
| **`M3_GEMINI_V2` vs `A1_GEMINI_V2` (`v2`)** | 搬运 (`transport_place`) | 1/0 | +5 | 1.0000 | 1.0000 |
| **`M3_GEMINI_V2` vs `A1_GEMINI_V2` (`v2`)** | **交接 (`handover_transfer`)** | **16/0** | **+80** | **$3.05\times 10^{-5}$** | **0.0023** |
| **`M3_GEMINI_V2` vs `A1_GEMINI_V2` (`v2`)** | **倒水代理 (`pour_water`)** | **14/0** | **+70** | **$1.22\times 10^{-4}$** | **0.0089** |
| **`M3_GEMINI_V2` vs `A1_GEMINI_V2` (`v2`)** | 倒水 PBF（全量表 `r3_pbf`） | 7/2 | +25 | 0.1797 | 1.0000 |
| **`M3_GEMINI_V2` vs `M3_GEMINI` (`v2` vs `v1`)** | **交接 (`handover_transfer`)** | **15/0** | **+75** | **$6.10\times 10^{-5}$** | **0.0045** |
| **`M3_GEMINI_V2` vs `M3_GEMINI` (`v2` vs `v1`)** | **倒水代理 / 倒水 PBF 表** | **10/1** / 5/3 | **+45** / +10 | **0.0117** / 0.7266 | 0.7969 / 1.0000 |
| **`M3_GEMINI_V2` vs `M0_RANDOM`** | **交接 (`handover_transfer`)** | **9/0** | **+45** | **0.0039** | 0.2773 |

## 模型输出审计与抓取族转移分析

360 条模型选择（6 组方法 × 3 任务 × 20 seed）全部取得合法最终 JSON 排列，无几何回退（`fallback_to_geometry = 0`）、无选择器异常（`selector_error = 0`）。其中 `M3_CODEX` 在交接 seed 18 触发 1 次格式修复后通过，其余 359/360 条首次生成即通过完整排列校验。

| 指标 | `M3_CODEX` (`v1`) | `A1_CODEX` (`v1`) | `M3_GEMINI` (`v1`) | `A1_GEMINI` (`v1`) | `M3_GEMINI_V2` (`v2`) | `A1_GEMINI_V2` (`v2`) |
|---|---:|---:|---:|---:|---:|---:|
| 最终有效选择 | 60/60 | 60/60 | 60/60 | 60/60 | 60/60 | 60/60 |
| 首次有效 | 59/60 | 60/60 | 60/60 | 60/60 | 60/60 | 60/60 |
| 格式修复 / 几何回退 | 1 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 搬运选中抓取族 | 顶抓 18、侧抓 2 | 顶抓 20 | 顶抓 20 | 侧抓 20 | **把手 19、杯沿 1** | 把手 19、侧抓 1 |
| 交接选中抓取族 | 侧抓 18、顶抓 2 | 侧抓 13、顶抓 7 | 侧抓 13、顶抓 7 | 侧抓 20 | **杯沿 20** | 侧抓 20 |
| 倒水选中抓取族 | 侧抓 20 | 侧抓 12、顶抓 8 | 侧抓 20 | 侧抓 20 | **顶抓 20** | 侧抓 20 |

### 1. 为什么 `prompt_version="v1"` 下 LLM 没有稳定超越基线？

对 `v1` 模型输出与 seed 10–29 全量物理表的交叉分析揭示了三个具体症结：

1. **`geometry_score = 0.0` 语义歧义（未评分 vs 零质量）**：在 `multifamily_v3p` 中，B 的静态模型 `static_load_margin_v2` 仅支持圆柱杯身候选（`region="body"`，分数 `0.59–0.75`），所有 8 个杯沿（`rim`）候选和 28 个把手（`handle`）候选均被置为 `geometry_score = 0.0`（代表静态杯身公式不适用，而非几何质量差）。但 `v1` 系统提示词声明 `The geometry_score is a higher-is-better geometric prior`，导致 `M3_CODEX`、`A1_CODEX`、`M3_GEMINI` 和 `A1_GEMINI` 在全部 240 次 `v1` 调用中均将 `rim` 和 `handle` 排在最末（0 次选中）。而在真实物理表中，搬运任务上 `rim` 成功率为 **98.4%（124/126）**、`handle` 为 **92.8%（233/251）**；交接任务上 `rim` 成功率为 **88.7%（110/124，其中 `closing=across handle axis` 为 33/33 = 100%）**。
2. **交接任务的水平走廊干涉**：`v1` 的 `handover_transfer` 提示词只要求“留出把手（`leaves the handle accessible`）”。`M3_CODEX`（18/20 次）与 `M3_GEMINI`（13/20 次）因排斥 `0.0` 分的 `rim` 且看到 `top` 带有 `mouth=hand over the opening`，大量选择了水平侧抓杯身中部/上部（`part=body mid/high, from=side`）。然而接收夹爪沿 `+Y` 水平滑轨接近把手时，供体水平侧抓的手指/手腕占据水平走廊，导致整体 `side` 族交接成功率仅 **21.5%（51/237，其中 `body mid` 为 0/24）**。相反，从上方垂直捏持杯沿（`region="rim", from=above, closing=across handle axis, handle=left free`）完全让出水平交接走廊并向 `+Z` 垂直撤离。
3. **倒水任务的 `mouth=hand over the opening` 标签误导与倾倒运动学**：`semantic_v1` 将所有 `top` 和 `rim` 抓取标注为 `mouth=hand over the opening (blocks pouring)`，配合 `v1` 提示词 `leaves the opening unobstructed`，迫使 `M3_CODEX` 和 `M3_GEMINI` 20/20 全选侧抓。但在仿真器中，110° 大角度倾倒的主要失败源是俯仰侧抓与中下部侧抓的预抓取/倾倒 IK 与到位超时，而高分 `body top`（`geometry_score >= 0.70`，PBF 下 83%–89% 成功）、水平高位侧抓（`part=body high, from=side, horizontal, side=beside the handle`，PBF 下 88% 成功）与跨把手轴 `rim` 抓取（PBF 下 88% 成功）并不会阻碍粒子流体倾倒且运动学可达性远高于俯仰侧抓。

### 2. `prompt_version="v2"` 为什么能实现显著跃升与任务自适应？

在 [`grasp_c/prompts.py`](../grasp_c/prompts.py) 与 [`grasp_c/tasks.py`](../grasp_c/tasks.py) 中实现的 `prompt_version="v2"` 做了两项针对性修正：
- **消除零分歧义（`SYSTEM_PROMPT_V2`）**：明确说明 `geometry_score > 0` 仅对 `region='body'` 有效，而 `region='rim'` 与 `region='handle'` 的 `geometry_score=0.0` 代表静态圆柱公式未评分（`unscored (N/A)`），要求模型优先根据任务需求匹配 `region` 与 `approach` 语义，仅在同类 `body` 候选内部用 `geometry_score` 排序。
- **对齐三项任务的运动学与几何约束（`SEQUENCE_INTENT_V2`）**：明确描述搬运下放退刀的桌面避障、交接中 `+Y` 水平接收走廊净空（优先上方垂直 `rim` 跨把手轴抓取）以及 110° 倾倒的连续腕部可达性（澄清通用 `mouth` 标签在仿真中不挡水流，优先高分 `body top` 或把手旁水平高位侧抓）。

对比 `M3_GEMINI_V2` 与 `A1_GEMINI_V2` 的行为可以清晰看到**任务意识（Task-Awareness）的因果作用**：
- **无任务消融 `A1_GEMINI_V2`** 在解除 `geometry_score=0.0` 惩罚后，基于日常先验将“外侧把手横握（`part=handle outer bar`）”排在首位、杯身侧抓排在其次。这使它在搬运任务上取得 **18/20（90%）**；但在交接和倒水任务中，外侧把手候选因占用把手或倾倒 IK 不可行被筛选器过滤，而 `A1_GEMINI_V2` 因**不知道当前任务是交接还是倒水**，一律回退到高 `geometry_score` 的杯身侧抓（`side` 20/20），导致交接仅 **4/20（20%）**、倒水代理仅 **4/20（20%）**、倒水全量 PBF 仅 **11/20（55%）**。
- **含任务描述的 `M3_GEMINI_V2`** 则根据显式任务意图在三个任务上主动切换抓取族：
  - **搬运 (`transport_place`)**：选择 `handle`（19/20）与 `rim`（1/20），成功率 **19/20（95%）**；
  - **交接 (`handover_transfer`)**：100% 切换至上方垂直 `rim` 抓取（20/20），完全避开 `+Y` 接收走廊并垂直撤离，成功率从 `v1` 的 **5/20（25%）** 跃升至 **20/20（100%）**；
  - **倒水 (`pour_water`)**：100% 切换至高分 `body top` 抓取（20/20），避开易卡死在预抓取/倾倒阶段的俯仰与中位侧抓，代理成功率从 **9/20（45%）** 跃升至 **18/20（90%）**，全量 PBF 流体成功率从 **14/20（70%）** 提升至 **16/20（80%）**。

## 倒水复核与全量 PBF 表对账

- **全量 PBF 表（`r3_pbf_v3p_wide_strict_v2`）**：对 30 个 seed 上全部 458 条可行倒水候选均执行了默认 256 粒子、8 次迭代、2 个子步的 PBF 流体仿真，因此所有 11 种方法在留出集 seed 10–29 上均具备完整的 **20/20** 真实流体评分分母与真实流体 Oracle 上界（20/20）。其中 `M3_GEMINI_V2` 与 `M0_RANDOM` 并列第一（**16/20 = 80.0%**），优于 `M2`（15/20）、`M3_GEMINI`/`A1_GEMINI`（14/20）、`M1`/`M5`/`M3_CODEX`/`A1_CODEX`（13/20）、`M4`（12/20）以及无任务消融 `A1_GEMINI_V2`（11/20）。
- **去重 PBF 复核目录（`c_evaluation_multimodel_20261109/pbf/` 与 `c_evaluation_20261106/pbf/`）**：涵盖全部 11 种方法去重后的 89 条 `(seed, candidate_id)` PBF episode（73 条直接复用 `c_evaluation_20261106/pbf/` 本机完整 episode，新增 16 条 Gemini 独有候选补齐密封校验，89/89 数值有效，全部 11 种方法在 `pour_water_pbf` 上均达 20/20 覆盖）。全量 458 条 PBF 表与代理模式的一致率为 **75.8%（347/458）**，其中 83 例为代理判失败而真实流体成功（含 53 例侧抓），证实代理模式对侧抓明显偏悲观，倒水方法对比应以真实流体为准。

## 总结

综合 `v1` 与 `v2` 两轮评估，Part C 形成完整的实验闭环：
1. **`v1` 揭示接口表示瓶颈**：当静态评分对跨形态抓取族（`rim`/`handle`）输出 `0.0` 占位符且语义标签（`mouth=hand over the opening`）与仿真物理不一致时，无论是 `gpt-5.6-luna` 还是 `gemini-3.8-flash` 都无法仅凭简短任务名克服先验误导。
2. **`v2` 验证任务感知抓取选择**：在消除未评分歧义并显式提供任务空间/运动学约束后，`gemini-3.8-flash`（`M3_GEMINI_V2`）能够随任务准确切换抓取族（搬运抓把手 $\rightarrow$ 交接抓杯沿 $\rightarrow$ 倒水抓杯身顶部），在留出集 60 个序列试验中取得 **55/60（91.7%，含 PBF 倒水）** / **57/60（95.0%，含代理倒水）** 的总成功率，超越全部五种基线方法，并在交接（$p=3.05\times 10^{-5}$）和倒水代理（$p=1.22\times 10^{-4}$）上相对无任务消融 `A1_GEMINI_V2` 取得经 Holm 多重校正后依然显著的提升。
