# 杯子任务感知抓取：仿真与多模型评估

本目录包含小组项目的 **A 部分和 C 部分**：A 在 PyBullet 中建立杯子和 Panda 场景、筛选抓取并统一执行；C 提供可扩展的 LLM 选择、一次输出修复、几何参考回退、缓存和批量评估。候选生成、正式评分及 M0/M1/M2 仍由 B 负责。C 的安装、账号接入和多模型配置见 [docs/PART_C.md](docs/PART_C.md)。

目前是 **pilot 版本**。2026-10-16 升级为 `observable_primitives_v2`：控制器不再读取运行时杯子真实位姿或接触数来决策，使用标定先验/外部位姿估计与关节反馈；物体真值留给终端评分。新增可选的逐步控制接口、位姿估计误差测试、固定共同目标与完整两秒保持检查。

新版 G02/G03、两个任务、seed 0–9 的基线回归为 **33/40 成功**；额外 ±3 mm/±2° 和 ±8 mm/±5° 估计扰动下均为 15/40，见 [本版回归](results/observable_v2_20261016/summary.json)。这表明当前控制器仍对估计误差敏感。旧 `bounded_ik_v1` 的 40/40 使用不同观测与评分规则，不能与新版混算。以上均非正式选择器对比；本项目尚无视觉感知或真机验证。

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

最后一条命令应选中 **G02**，seed 0 输出 `success: true`。视频在 `results\local\visuals\handover_prep_seed0_G02.mp4`；结果 JSON 的 `media` 字段也会给出路径。把个人重跑结果放在 `results/local/`，避免覆盖参考数据。新版录像和失败记录见 [结果索引](results/README.md)。旧版参考视频仍保留，但不作为新版回归证据。

也可以在 Miniforge Prompt 运行 `setup_and_run_windows.cmd`。它会检查或创建环境，筛选候选、渲染场景，并录制同一条演示；若现有环境缺包，按提示运行 `conda env update -f environment.yml`。

## 2. 目录与阅读顺序

| 路径 | 内容 |
|---|---|
| `README.md` | 项目入口、安装和使用 |
| [`interface.md`](interface.md) | A/B/C 的候选、筛选和结果协议 |
| [`docs/EVALUATION_INTERFACE.md`](docs/EVALUATION_INTERFACE.md) | C 的详细评估接入定义、字段、失败与统计口径 |
| `examples/evaluate_one_trial.py` | C 可替换 selector 的可运行示例，含空列表/非法输出/异常处理 |
| `examples/incremental_policy.py` | 可选的逐步末端/夹爪策略，不是 LLM 或正式 baseline |
| [`PILOT.md`](PILOT.md) | 单次视频的简短命令 |
| `environment.yml`、`requirements.txt` | Conda 与 pip 依赖 |
| `setup_and_run_windows.cmd` | Windows 一键 pilot |
| `candidates.example.json`、`candidates.pilot.json` | 默认 G01/G02 候选与单独的 G03 校准候选 |
| `assets/mug.urdf` | 简化杯子模型 |
| `grasp_a/contracts.py` | `Pose`、`Candidate`、任务 ID 与输入校验 |
| `grasp_a/simulation.py` | 场景、IK/FK、碰撞筛选、统一执行 |
| `grasp_a/control.py` | 位姿估计配置、受限 primitive、只含控制观测的会话接口 |
| `grasp_a/evaluation.py` | 独立的物体真值评分，不把真值反馈给策略 |
| `grasp_a/collision_rules.py` | Panda 固定连杆组的自碰撞规则 |
| `grasp_a/visualization.py` | PyBullet 视频帧和截图 |
| `grasp_a/__main__.py` | `python -m grasp_a` 命令入口 |
| `grasp_c/`、[`docs/PART_C.md`](docs/PART_C.md) | 多服务/多模型选择器、缓存、批量运行和统计 |
| `configs/c.*.json`、`requirements-c.txt` | C 的运行配置、观测误差设置与依赖 |
| [`results/README.md`](results/README.md) | 当前结果与历史调试数据索引 |
| `tests/`、`scripts/find_pilot_candidate.py` | 接口检查与可行性网格搜索工具 |
| `scripts/check_observable_control.py` | 120 次控制器/估计噪声回归，保留失败样本 |

建议先运行第 1 节，再读第 3–5 节。与 B/C 联调时看 [interface.md](interface.md)，查看已有数据时看 [results/README.md](results/README.md)。

## 3. 安装环境

Windows 使用 `environment.yml`：Python 3.11、`pybullet`、`numpy`、`pillow`、`imageio`、`imageio-ffmpeg`，以及 C 的 `openai`、`google-genai`、`python-dotenv`。PyBullet 和 `pybullet_data` 提供机械臂模型与物理仿真，其余依赖用于图片、视频和模型调用。在 Miniforge Prompt 中用 Conda 安装即可，不需要再执行 `pip install -r requirements.txt`。

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

C 开发评估程序请先读 [详细评估定义 v0.3](docs/EVALUATION_INTERFACE.md)，并运行
`python -m examples.evaluate_one_trial --task pour_prep --seed 0` 验证联调。
包装记录会区分完成、无可行候选、非法选择、选择器异常和执行异常；当前控制器的
固定共同目标、全过程保持评分、dropped 标记及新版字段也在该文档中明确说明。

候选文件是 JSON 数组，每个 `Candidate` 有以下字段：

| 字段 | 含义 |
|---|---|
| `id` | 同一候选集内唯一、稳定的字符串 ID |
| `grasp_pose`、`pregrasp_pose` | 杯子坐标系下的目标位姿：`position: [x,y,z]`（米），`quaternion: [x,y,z,w]` |
| `region`、`approach` | 手工标注的部位和接近方向描述 |
| `gripper_width` | 两指总开口，单位米 |
| `geometry_score` | B 预先定义的几何评分；A 仅透传 |

杯子坐标原点在杯身中心，把手位于本地 **+Y** 方向。A 用**初始位姿估计**把候选转换到世界坐标系，不读取按 seed 扰动后的真实位姿来校正抓取。`task_id` 只能是 `pour_prep` 或 `handover_prep`。所有方法共享 scene seed、候选、控制器及位姿估计/噪声配置。

`get_feasible_candidates(seed, candidates)` 返回 `seed`、`status`、`checks` 和 `feasible_candidates`。`checks` 含每条候选的通过状态、拒绝原因和碰撞详情；`feasible_candidates` 只含选择器可见的 `id`、`region`、`approach`、`gripper_width`、`geometry_score`。列表为空时 `status` 为 `no_feasible_candidate`，应记录并跳过该 seed。

`run_episode(task_id, seed, selected_id, candidates)` 创建新场景、重新筛选所选 ID，然后使用统一控制器执行。B/C 只能返回当次可行列表中的已有 ID。无效 ID 报错；已知但不可行的 ID 返回 `failure_reason: "infeasible"`。

两函数均新增可选关键字 `observation_config=ObservationConfig(...)`，默认使用固定标定先验 `[0.55, 0, 0.645]`、单位方向，而非精确物体位姿。可提供外部估计，或添加可复现的均匀位置/偏航扰动；筛选和执行必须传同一配置。FilterResult 保存完整候选指纹、观测配置及 `selection_space` 审计警告；默认只有一条可行候选会显式提示，不能据此证明任务选择有效。

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

`EpisodeResult` 的 `schema_version=episode_v0.3`，含成功/失败、检测掉落/碰撞、真实终端误差、固定目标、轨迹、IK 诊断、`primitive_log`、观测配置与保持区间指标。提前失败也返回目标和真实终端误差。物体真值只供终端评估，不得把完整 result/trajectory 反馈给执行中的 agent。录像另有 `media`。完整字段及与旧版的区别见 [详细协议](docs/EVALUATION_INTERFACE.md)。

### 可选：让策略逐步操作

`run_primitive_episode(..., policy)` 允许策略依据关节角、末端 FK、夹爪开口与上一步动作状态，逐次返回 `move_tcp` / `gripper` / `wait`；可改变路径、时长、开口、重试或停止，不提供 `pickup/place` 宏动作。默认最多 32 次动作，单次不超过 2 秒；末端目标和移动距离有限制，但不是碰撞安全保证。策略停止后仅等待两秒评分，不自动补齐任务。

```bat
python -m examples.incremental_policy --task pour_prep --id G02 --retreat-m .10 --clearance-m .04
```

这是独立的增量控制轨道，不能与固定执行器下的抓取 ID 选择实验混算。原 ID-only 接口仍是主实验，以隔离“抓取选择”的贡献。它不是通用自主机器人 agent。

### 位姿估计误差测试

```bat
python -m grasp_a run --task pour_prep --seed 0 --id G02 --pose-noise-m .003 --yaw-noise-deg 2 --observation-seed 1000 --out results\local\noisy_episode.json
python -m scripts.check_observable_control --out results\local\observable_regression
```

`--observed-pose estimate.json` 可读取外部估计的 Pose JSON；本项目不会自动运行视觉估计。噪声加在先验/输入估计上，不从模拟器真值生成观测；零额外噪声也不等于精确已知物体状态。

## 6. 仿真检查与任务

场景为固定桌面、简化实心杯子、Panda 7 自由度机械臂和双指夹爪。桌面高度 0.60 m，物理步长 1/240 秒。seed 扰动杯子初始位置约 ±8 mm、偏航角约 ±0.08 rad；每次 `run_episode` 都建立新世界。

筛选依次检查夹爪总开口 0.005–0.08 m、预抓取和抓取位姿的 IK/FK 误差及关节限位、两段路径的离散碰撞。路径每段取 21 个关节插值点；穿入深度超过 1 mm 视为碰撞。抓取终点允许手指与杯子接触，其他意外接触仍拒绝。当前版本未实现精确力闭合或接触对抗评分；离散采样也不能证明采样点之间绝无碰撞。

统一执行顺序：预抓取 → 接近 → 闭合夹爪 → 抬升约 14 cm → 任务动作 → 保持 2 秒。倒水增加向机器人侧移动、旋转、返回的中间目标，减少直接旋转时的近桌面 IK 分支切换。所有 seed/候选/估计条件共用杯子世界目标 `[0.55, 0, 0.785]` m；倒水方向为世界 Y +35°，交接为世界 Z −90°。保持的每个物理步都须夹持且位置误差≤4 cm、方向误差≤15°；还须没有检测到掉落和意外碰撞。评分比旧版最后一帧判定严格。

杯子模型没有液体、视觉感知或真人交接。已知模型、人工部位标注和机器人运动学仍是理想化假设；筛选在**估计位姿的已知模型**上进行，不保证实际执行安全。保留之前修复的 9 自由度有界 IK；新观测和固定目标下仍可能规划失败或位姿超差，所有失败均保留在本版回归中，不放宽关节限位或成功阈值。

新版倒水视频可用 `python -m grasp_a run --task pour_prep --seed 0 --id G02 --visualize --media-dir results\local\visuals --out results\local\G02_pour_seed0.json` 重现；请检查结果中的 `controller_version`。旧 `ik_fix_20261015/` 的视频仅用于追溯 IK 修复。

## 7. 结果与后续工作

[results/README.md](results/README.md) 区分新版回归和旧控制器的历史文件。旧结果均保留，不能与新版参数、观测和成功规则混算。

正式比较前，组员还需补齐约 8 条有实质差异的候选、定义 `geometry_score` 和独立任务适用性、冻结目标/评分/环境，并使用未参与调试的匹配 seed。多条相近杯身抓取不等于任务选择空间充分。B 的正式候选与 M0/M1/M2 仍未包含。C 的 M3、暂定 A1 和批量评估已实现并对齐新版 A，当前仍为 pilot。


## 8. C：用各自 API key 跑 GPT / Gemini 选择

C 默认通过官方 SDK 调用 GPT 和 Gemini，分别读取 `OPENAI_API_KEY` 和
`GEMINI_API_KEY`。小组成员只需在本机配置自己的 key；一个配置可同时比较多个模型。
完整说明见 [C 接入文档](docs/PART_C.md)。

```bash
python -m pip install -r requirements-c.txt
cp .env.example .env
# 用本地编辑器在 .env 中填写自己的 key；也可以使用终端已导出的环境变量
python -m examples.llm_smoke --out results/local/openai_smoke.json
python -m examples.llm_smoke --provider gemini --out results/local/gemini_smoke.json
python -m grasp_c validate --config configs/c.api.json
python -m grasp_c run --config configs/c.api.json --out results/local/c_api
python -m grasp_c summarize results/local/c_api
```

Windows PowerShell 复制模板用 `Copy-Item .env.example .env`；Conda 用户先执行
`conda env update -f environment.yml`。`.env` 已被 Git 忽略，终端已导出的变量优先。
关闭缓存的样例会打印真实原始回答与物理结果；再次运行请用新的 `--out` 路径。

只测 GPT 用 `configs/c.openai.json`，只测 Gemini 用 `configs/c.gemini.json`，
同时比较用 `configs/c.api.json`。默认模型为 `gpt-6-luna` 和 `gemini-3.8-flash`，
可改为各自账户可访问的准确模型 ID。API key 缺失会明确报错，不会自动使用登录态。
`configs/c.codex.json` 仍保留为显式可选的 Codex CLI 方式；
`configs/c.mock.json` 可无凭据验证完整物理流水线。C 的 A1 暂定义为去掉任务信息，
几何回退使用 pilot 分数。正式实验前仍需全组冻结候选、评分、任务和消融定义。

C 已对齐 `observable_primitives_v2`：同一次运行的筛选与物理执行使用同一份显式 `observation_config`，记录 `fixed_target_hold_v2` 与 `policy_mode=fixed`。旧 `bounded_ik_v1` 验收保留在独立目录，不与新版结果混算。
