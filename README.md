# A 部分：杯子抓取仿真与统一执行

本目录实现小组项目的 **A 部分**：在 PyBullet 中建立杯子和 Panda 机械臂场景，筛选可行抓取，并用同一套控制器执行 B/C 选出的抓取 ID。候选生成与 M0/M1/M2 由 B 负责，LLM 选择和批量评估由 C 负责。

目前是 **pilot 版本**。2026-10-15 修复了抬升和倒水动作的 IK 越限问题，控制器版本为 `bounded_ik_v1`。G02/G03 在 seed 0–9 的交接与倒水准备任务中均为 10/10 成功，共 40 个 pilot episode，另一次相同条件复跑结果完全一致。详见 [回归结果](results/ik_fix_20261015/summary.json)。这些是控制器回归数据，正式选择器实验尚未开始。

## 1. 先跑通一次视频

在项目根目录打开 **Miniforge Prompt**。首次安装环境：

```bat
cd /d D:\SEM1\CS5478\project\A\grasp_part_a
conda env create -f environment.yml
conda activate grasp-a
```

若 `grasp-a` 已存在，执行 `conda env update -f environment.yml` 补齐依赖，再激活环境。然后运行：

```bat
python -c "import pybullet, pybullet_data, imageio, PIL; print('PyBullet OK')"
python -m grasp_a validate
python -m grasp_a filter --seed 0 --out results\local\filter_seed0.json
python -m grasp_a demo --task handover_prep --seed 0 --visualize --media-dir results\local\visuals --out results\local\handover_seed0.json
```

最后一条命令应选中 **G02**，输出 `success: true`。视频在 `results\local\visuals\handover_prep_seed0_G02.mp4`；结果 JSON 的 `media` 字段也会给出路径。把个人重跑结果放在 `results/local/`，避免覆盖参考数据。已有成功记录见 [结果 JSON](results/demo_handover_seed0.json) 和 [视频](results/visuals/handover_prep_seed0_G02.mp4)。

也可以在 Miniforge Prompt 运行 `setup_and_run_windows.cmd`。它会检查或创建环境，筛选候选、渲染场景，并录制同一条演示；若现有环境缺包，按提示运行 `conda env update -f environment.yml`。

## 2. 目录与阅读顺序

| 路径 | 内容 |
|---|---|
| `README.md` | 项目入口、安装和使用 |
| [`interface.md`](interface.md) | A/B/C 的候选、筛选和结果协议 |
| [`docs/EVALUATION_INTERFACE.md`](docs/EVALUATION_INTERFACE.md) | C 的详细评估接入定义、字段、失败与统计口径 |
| `examples/evaluate_one_trial.py` | C 可替换 selector 的可运行示例，含空列表/非法输出/异常处理 |
| [`PILOT.md`](PILOT.md) | 单次视频的简短命令 |
| `environment.yml`、`requirements.txt` | Conda 与 pip 依赖 |
| `setup_and_run_windows.cmd` | Windows 一键 pilot |
| `candidates.example.json`、`candidates.pilot.json` | 默认 G01/G02 候选与单独的 G03 校准候选 |
| `assets/mug.urdf` | 简化杯子模型 |
| `grasp_a/contracts.py` | `Pose`、`Candidate`、任务 ID 与输入校验 |
| `grasp_a/simulation.py` | 场景、IK/FK、碰撞筛选、统一执行 |
| `grasp_a/collision_rules.py` | Panda 固定连杆组的自碰撞规则 |
| `grasp_a/visualization.py` | PyBullet 视频帧和截图 |
| `grasp_a/__main__.py` | `python -m grasp_a` 命令入口 |
| [`results/README.md`](results/README.md) | 当前结果与历史调试数据索引 |
| `tests/`、`scripts/find_pilot_candidate.py` | 接口检查与可行性网格搜索工具 |

建议先运行第 1 节，再读第 3–5 节。与 B/C 联调时看 [interface.md](interface.md)，查看已有数据时看 [results/README.md](results/README.md)。

## 3. 安装环境

Windows 使用 `environment.yml`：Python 3.11、`pybullet`、`numpy`、`pillow`、`imageio`、`imageio-ffmpeg`。PyBullet 和 `pybullet_data` 提供机械臂模型与物理仿真，其余依赖用于图片和视频。在 Miniforge Prompt 中用 Conda 安装即可，不需要再执行 `pip install -r requirements.txt`。

请先 `conda activate grasp-a` 再运行；仅直接调用环境里的 `python.exe` 可能缺少 Conda 数值库的 DLL 路径。NumPy 也用于备用 IK 的有界数值求解。

Linux/macOS 可在 Python 3.11 虚拟环境中使用 `requirements.txt`，前提是该平台能安装 PyBullet：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`validate` 只检查候选 JSON，不启动仿真；以导入检查和实际运行结果判断 PyBullet 与视频依赖是否可用。

## 4. 命令行使用

在项目根目录运行 `python -m grasp_a <命令>`。不指定候选文件时读取 `candidates.example.json`。

| 命令 | 作用 |
|---|---|
| `validate` | 校验候选 JSON、列出 ID；不启动仿真 |
| `filter` | 检查全部候选，输出可行列表与拒绝原因 |
| `run` | 在新场景中执行指定候选 ID |
| `demo` | 选列表中第一个可行 ID，跑通流水线 |
| `scene` | 保存初始场景 PNG 和静态环绕视频 |

`demo` 只是冒烟演示，不代表 M0/M1/M2/M3。要执行 B/C 给出的 ID，用 `run`：

```bat
python -m grasp_a run --task handover_prep --seed 0 --id G02 --visualize --media-dir results\local\visuals --out results\local\G02_handover_seed0.json
```

`run` 可加 `--gui` 打开交互式 PyBullet 窗口；默认 DIRECT 模式也能录 MP4。指定其他候选文件时，**`--candidates` 必须放在子命令之前**：

```bat
python -m grasp_a --candidates candidates.pilot.json filter --seed 0 --out results\local\G03_filter_seed0.json
```

`scene` 生成的环绕视频只展示初始场景；真正的抓取视频需要 `run` 或 `demo` 的 `--visualize`，并可用 `--media-dir` 指定输出目录。

开发时可运行 `python -m unittest discover -s tests -v` 检查输入协议和 IK 回归。`tests/test_simulation_ik.py` 会复现原来的抬升/倒水失败，检查关节限位、求解后的状态恢复及不可达目标的拒绝；未安装 PyBullet 时跳过这部分测试。`python -m scripts.find_pilot_candidate --seed 0 --out results\local\pilot_search.json` 只搜索能通过可行性筛选的试验位姿，不执行抓取，也不替代 B 提供的正式候选集。

## 5. A/B/C 统一接口

C 开发评估程序请先读 [详细评估定义 v0.2](docs/EVALUATION_INTERFACE.md)，并运行
`python -m examples.evaluate_one_trial --task pour_prep --seed 0` 验证联调。
包装记录会区分完成、无可行候选、非法选择、选择器异常和执行异常；当前控制器的
最后一帧成功判定、目标随实际抓取变化及 dropped 标记含义也在该文档中明确说明。

候选文件是 JSON 数组，每个 `Candidate` 有以下字段：

| 字段 | 含义 |
|---|---|
| `id` | 同一候选集内唯一、稳定的字符串 ID |
| `grasp_pose`、`pregrasp_pose` | 杯子坐标系下的目标位姿：`position: [x,y,z]`（米），`quaternion: [x,y,z,w]` |
| `region`、`approach` | 手工标注的部位和接近方向描述 |
| `gripper_width` | 两指总开口，单位米 |
| `geometry_score` | B 预先定义的几何评分；A 仅透传 |

杯子坐标原点在杯身中心，把手位于本地 **+Y** 方向。A 按 seed 建立杯子初始位姿，再把候选转换到世界坐标系。`task_id` 只能是 `pour_prep` 或 `handover_prep`。同一 seed、候选文件和控制参数要供所有方法共同使用。

`get_feasible_candidates(seed, candidates)` 返回 `seed`、`status`、`checks` 和 `feasible_candidates`。`checks` 含每条候选的通过状态、拒绝原因和碰撞详情；`feasible_candidates` 只含选择器可见的 `id`、`region`、`approach`、`gripper_width`、`geometry_score`。列表为空时 `status` 为 `no_feasible_candidate`，应记录并跳过该 seed。

`run_episode(task_id, seed, selected_id, candidates)` 创建新场景、重新筛选所选 ID，然后使用统一控制器执行。B/C 只能返回当次可行列表中的已有 ID。无效 ID 报错；已知但不可行的 ID 返回 `failure_reason: "infeasible"`。

```python
from grasp_a.contracts import load_candidates
from grasp_a.simulation import get_feasible_candidates, run_episode

candidates = load_candidates("candidates.example.json")
filtered = get_feasible_candidates(seed=0, candidates=candidates)
if filtered["status"] == "ok":
    feasible_ids = {item["id"] for item in filtered["feasible_candidates"]}
    selected_id = "G02"  # seed 0 演示值；正式实验由 B/C 选择器返回
    assert selected_id in feasible_ids
    result = run_episode("handover_prep", 0, selected_id, candidates)
else:
    result = None  # 记录 no_feasible_candidate，跳过该 seed
```

`EpisodeResult` 包含 `success`、`failure_reason`、`dropped`、`collision`、`collision_types`、`grasp_maintained`、最终位姿、位姿误差和按阶段采样的 `trajectory`。新增 `controller_version` 与 `ik_diagnostics`，可查看每个阶段的目标、局部/有界求解、原始越限关节及 FK 误差。完整执行还包含目标位姿；提前失败时误差可能为 `null`。录制视频时增加 `media` 路径。完整协议见 [interface.md](interface.md)。

## 6. 仿真检查与任务

场景为固定桌面、简化实心杯子、Panda 7 自由度机械臂和双指夹爪。桌面高度 0.60 m，物理步长 1/240 秒。seed 扰动杯子初始位置约 ±8 mm、偏航角约 ±0.08 rad；每次 `run_episode` 都建立新世界。

筛选依次检查夹爪总开口 0.005–0.08 m、预抓取和抓取位姿的 IK/FK 误差及关节限位、两段路径的离散碰撞。路径每段取 21 个关节插值点；穿入深度超过 1 mm 视为碰撞。抓取终点允许手指与杯子接触，其他意外接触仍拒绝。当前版本未实现精确力闭合或接触对抗评分；离散采样也不能证明采样点之间绝无碰撞。

统一执行顺序：预抓取 → 接近 → 闭合夹爪 → 抬升约 14 cm → 任务动作 → 保持 2 秒。`pour_prep` 的目标是倾斜 35°；`handover_prep` 的目标是转动杯子让把手朝交接方向展示。成功需保持抓取、无意外碰撞，最终位置误差不超过 4 cm、方向误差不超过 15°。这些仍是 pilot 参数，正式实验前应冻结。

杯子模型没有液体、视觉感知或真人交接。当前 G02/G03 的两个任务均通过 seed 0–9 的 pilot 回归，G01 仍因接近时撞桌被拒绝。原来 `lift_ik_failed` / `task_ik_failed` 的典型原因是局部 IK 把第 5 关节推到限位外；新版保留通过 FK/限位校验的局部解，必要时改用包含两指的 9 自由度限位、当前姿态和有界 FK 数值细化。所有关节限位与成功容差继续生效。

倒水视频可用 `python -m grasp_a run --task pour_prep --seed 0 --id G02 --visualize --media-dir results\local\visuals --out results\local\G02_pour_seed0.json` 重现；已保存的 [新视频](results/ik_fix_20261015/visuals/pour_prep_seed0_G02.mp4) 与 [结果 JSON](results/ik_fix_20261015/pour_G02_seed0.json) 对应 `bounded_ik_v1`。

## 7. 结果与后续工作

[results/README.md](results/README.md) 区分当前可复现输出和旧待机位姿下的历史文件。参考视频：[G02 交接准备抓取](results/visuals/handover_prep_seed0_G02.mp4)；对应结果：[demo_handover_seed0.json](results/demo_handover_seed0.json)。旧 `filter_seed0.json`、`demo_seed0.json` 等仍保留，不能与当前参数下的结果混算。

正式比较前，组员还需共同确认杯子与任务目标、补齐约 8 条候选、定义 `geometry_score`、检查至少两个不同抓法能通过筛选，并在固定参数下做匹配 seed 的重复运行。B/C 的选择器与批量评估尚未包含在本目录中。
