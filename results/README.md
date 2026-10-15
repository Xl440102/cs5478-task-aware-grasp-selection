# 结果文件索引

本目录保存 pilot 和早期调试输出，**不是正式实验数据集**。比较不同方法或 seed
之前，必须固定候选文件、`grasp_a/simulation.py` 中的待机位姿与控制参数。
建议把个人重跑结果写到 `results/local/`，以免覆盖下面的参考文件。

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
