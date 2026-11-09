# 结果文件索引

本目录保存 pilot 和早期调试输出，**不是正式实验数据集**。比较不同方法或 seed
之前，必须固定候选文件、`grasp_a/simulation.py` 中的待机位姿与控制参数。
建议把个人重跑结果写到 `results/local/`，以免覆盖下面的参考文件。

## 2026 年 11 月 6 日 / 11 月 9 日 C 多模型与 Prompt v1/v2 真实评估

- [`c_evaluation_multimodel_20261109/`](c_evaluation_multimodel_20261109/)：汇总第三轮留出集 seed 10–29 上 `gpt-5.6-luna`（`v1`）与 `gemini-3.8-flash`（`v1` 与 `v2`）共 6 组 LLM 方法（360 条密封 trial）与五种基线（M0–M5）的完整对比，并接入 [`oracle_20261103/r3_pbf_v3p_wide_strict_v2/`](oracle_20261103/r3_pbf_v3p_wide_strict_v2/) 全量 458 条默认 PBF 流体表。包含 [`summary.json`](c_evaluation_multimodel_20261109/summary.json)、[`report.md`](c_evaluation_multimodel_20261109/report.md)、[`method_results.csv`](c_evaluation_multimodel_20261109/method_results.csv)、[`success_rates.png`](c_evaluation_multimodel_20261109/success_rates.png) 与 [`validation.json`](c_evaluation_multimodel_20261109/validation.json)。
- [`c_evaluation_20261106/`](c_evaluation_20261106/)：保存初始 `gpt-5.6-luna`（`v1`）120 条真实 M3/A1 选择、冻结运行清单、逐条原始响应及 73 条去重本机默认 PBF 完整 episode（另附接入全量 PBF 表的 [`summary_with_pbf_table.json`](c_evaluation_20261106/summary_with_pbf_table.json) 与 [`report_with_pbf_table.md`](c_evaluation_20261106/report_with_pbf_table.md)）。
- 中文结论、Prompt `v1`/`v2` 消融分析与局限见 [C 评估报告](../docs/PART_C_EVALUATION_20261106.md)。历史 oracle 表只读，模型不接收结果标签。物理异常、模型回退和缺失样本分别保留，倒水代理不与真实流体合并。

## 2026 年 10 月 24 日 A/B/C 集成验证

[`c_ab_integration_20261024/validation.json`](c_ab_integration_20261024/validation.json)
保存三种完整任务、seed 0、B 随机/实验几何和 C 的 M3/A1 共 12 条验证摘要。
六次 Codex 登录调用均为新回答，无修复、缓存或几何回退；M3/A1 和几何参考各 3/3
物理成功，随机基线 2/3 成功，倒水失败原因为 `tilt_ik_failed`。全部 12 条均保留，
完成后的恢复验证新增模型、筛选和物理调用均为 0。旧准备任务与 B 的另 4 条试跑
也均成功，两种轨道分别统计。

摘要包含实际候选快照、原始回答、任务/代码/完整 trial 指纹和数值证据。完整运行
记录在本机 `results/local/c_sequence_b_codex_20261024/`。这只是单 seed 集成检查，
不能证明 LLM 优于几何参考；B 的几何评分仍为实验版本，M2 尚未实现。重跑步骤见
[C 文档第 11 节](../docs/PART_C.md#11-完整序列与-b-基线接入)。

## 2026 年 10 月 17 日完整任务序列

新增 `staged_sequences_v1 / physical_outcomes_v1`，场景为开口杯、物理接收夹爪和 PBF 数值液体。完整定义和复现方式见 [任务序列说明](../docs/TASK_SEQUENCES.md)。本轨道与下面准备任务分别统计。

三个任务的有／无 A/B 标记版共六条视频及起止截图已纳入 Git，见 [视频索引](sequence_v1_20261017/README.md)。

`sequence_v1_20261017/summary.json` 保存 G02、三个任务、seed 0–2 的逐项物理证据、失败样本、任务定义及液体分辨率检查。原始稠密日志保留在本机 `results/local/sequence_v1/final/`；演示视频与截图另存到上面的可追踪目录。组员可通过 `scripts/validate_sequences.py` 复现。它是执行器开发回归，不是不同选择方法的正式对比或真实水验证。

## 2026 年 10 月 16 日可观测控制 v2 准备任务

`observable_v2_20261016/` 对应 `observable_primitives_v2`，评分 `fixed_target_hold_v2`。
控制使用标定先验而非运行时物体真值；世界共同目标固定，保持区间逐物理步检查。

| 文件/目录 | 内容 |
|---|---|
| `observable_v2_20261016/summary.json` | G02/G03、两个任务、seed0–9、3种观测条件，共120次，含版本/资产哈希及失败原因 |
| `observable_v2_20261016/baseline/` | 无额外估计扰动：33/40成功；仍有真实场景与先验之间的偏差 |
| `observable_v2_20261016/mild/` | 先验额外加均匀±3 mm xyz、±2°偏航：15/40成功 |
| `observable_v2_20261016/stress/` | 先验额外加均匀±8 mm xyz、±5°偏航：15/40成功 |
| `observable_v2_20261016/*/filter_seed*.json` | 同条件筛选、观测配置、选择空间警告 |
| `observable_v2_20261016/visuals/` | 新版 seed0 G02 倒水与交接录像和起止截图 |

各任务/候选/seed完整episode日志均保存，包括失败，不选择性剔除。
噪声不是从真实物体位姿生成的视觉估计，也不代表实测传感器分布；相同成功数不证明噪声强度无影响。
这些是控制器开发回归，不是M0–M3/A1对比。0–9已用于调试，不能充当未见测试集。
可用 `python -m scripts.check_observable_control --out results/local/observable_regression` 重跑。
本版改变了观测、任务路径与成功规则，不与下面旧版40/40合并比较。

`interface_v02_smoke/pour_seed0.json` 是旧 v0.2 C 接口示例的一次真实包装记录，
方法标签为 SMOKE，包含候选 SHA-256、选择输出和完整 EpisodeResult；
它是接口验收样本，不是 M0/M1/M2/M3/A1 对比数据。

## 2026 年 10 月 15 日 IK 修复回归

`ik_fix_20261015/` 对应控制器 `bounded_ik_v1`，与旧控制器结果分开保存：

| 文件 | 含义 |
|---|---|
| `ik_fix_20261015/summary.json` | seed 0–9，G02/G03，两个任务共 40 次全部成功；相同条件复跑完全一致 |
| `ik_fix_20261015/handover_G02_seed1.json` | 原抬升失败 seed 1 的修复后完整日志 |
| `ik_fix_20261015/pour_G02_seed0.json` | 原倒水 IK 失败 seed 0 的修复后日志与媒体路径 |
| `ik_fix_20261015/visuals/pour_prep_seed0_G02.mp4` | 修复后的真实倒水准备视频 |

新日志的 `ik_diagnostics` 保存了原始越限关节、备用解和 FK 复核结果；
`controller_version` 用于区分控制器。40 次回归不是选择器正式对比实验。

## 2026 年 10 月 6 日旧控制器的 seed 0 参考结果

| 文件 | 候选来源 | 含义 |
|---|---|---|
| `filter_example_new_home_seed0.json` | `candidates.example.json` | G02 可行；G01 接近时撞桌 |
| `demo_handover_seed0.json` | `candidates.example.json` | `demo` 选中 G02，`handover_prep` 成功，含视频路径和轨迹 |
| `visuals/handover_prep_seed0_G02.mp4` | G02 | 实际抓取、抬升、转向、保持的视频 |
| `visuals/handover_prep_seed0_G02_scene.png`、`visuals/handover_prep_seed0_G02_final.png` | G02 | 对应视频的起始/结束画面 |
| `G02_handover_new_home_seed0.json` | G02 | 同任务的一次无视频运行，用于调试 |

`candidates.pilot.json` 中还有一条单独的 G03 候选：
`G03_filter_seed0.json`、`G03_handover_seed0.json` 和
`visuals/handover_prep_seed0_G03*` 是它在 seed 0 的成功 pilot 输出。
G03 的 `geometry_score` 只是占位值，不能用于正式 M1 排名。

## 历史调试输出

`filter_seed0.json`、`filter_seed0_pilot_20261006.json`、`demo.json`、
`demo_seed0.json`、`demo_seed0_pilot_20261006.json` 和 `G01_*.json`
来自旧待机位姿或早期代码，不能与上面的当前结果直接比较。
`visuals/handover_prep_seed0_G01*` 是失败案例视频。
`scene_seed0.png` 和 `scene_orbit_seed0.mp4` 只是旧场景的静态截图与镜头环绕，
不表示抓取执行成功。历史文件保留用于追溯问题，没有移动或删除。

## 重跑时注意

在项目根目录运行 README 中的命令。`filter` 返回全部候选的检查记录和
`feasible_candidates`；`run`/`demo` 返回一次执行结果。视频文件由
`--visualize` 生成；JSON 的 `media` 路径是生成机器上的绝对路径，组员换机器后
应按同名文件在本目录中查找。`demo` 只选第一个可行候选，不代表 M0/M1/M2/M3。
