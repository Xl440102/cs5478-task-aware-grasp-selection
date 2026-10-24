# 完整任务的 A B C 接口

本接口用于 `transport_place`、`handover_transfer`、`pour_water` 三个完整物理任务。B/C 选择已有抓取 ID，A 使用统一控制器执行并独立评分。任务逐步定义见 [TASK_SEQUENCES.md](TASK_SEQUENCES.md)，两套视频见 [演示索引](../results/sequence_v1_20261017/README.md)。

旧 `pour_prep / handover_prep` 继续使用 `grasp_a.simulation` 和 [旧评估协议](EVALUATION_INTERFACE.md)。不要把新任务 ID 传入旧接口，或将两个轨道的成功率、缓存混算。

## 版本和输入

| 项目 | 当前值 |
| --- | --- |
| 场景 | `hollow_cup_sequences_v1` |
| 控制器 | `staged_sequences_v1` |
| 评分 | `physical_outcomes_v1` |
| 筛选结果 | `sequence_filter_v1` |
| 执行结果 | `sequence_episode_v1` |
| 视频标记 | `task_landmarks_v1`，纯显示，不属于物理评分配置 |

候选沿用 `grasp_a.contracts.Candidate`。`grasp_pose / pregrasp_pose` 在源杯局部坐标系；位置单位 m，四元数顺序 xyzw；`gripper_width` 为总开口，不是单指位移。输入列表的 ID 应唯一，`selected_id` 必须恰好匹配其中一条。

选择器仅接收公开任务说明及五个候选字段：`id, region, approach, gripper_width, geometry_score`。完整位姿可供 A/B 内部计算，但不要将终端物理结果、接触、液体质量或任务适用性答案送入选择器。新空心杯场景需重新筛选，不能复用旧实心模型的可行 ID 缓存。

## Python 调用

```python
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from grasp_a.sequence import get_sequence_candidates, run_sequence

task_spec(task_id, config=None)

get_sequence_candidates(
    task_id, seed, candidates, *, config=None, observation_config=None
)

run_sequence(
    task_id, seed, selected_id, candidates, *,
    config=None, observation_config=None,
    gui=False, media_dir=None, show_landmarks=True
)
```

| 参数 | 说明 |
| --- | --- |
| `task_id` | 上述三个新任务之一 |
| `seed` | 场景 seed；各方法使用同一 seed 列表 |
| `candidates` | 完整有序的 `list[Candidate]`，筛选和执行使用同一列表 |
| `config` | `SequenceConfig`；省略为默认值；比较前冻结目标、时限、阈值与流体分辨率 |
| `observation_config` | 既有 `ObservationConfig`；标定先验或外部初始估计，不是运行时精确杯子位姿 |
| `selected_id` | 选择器选出的可行 ID；调用方应先校验它属于本次筛选的公开列表 |
| `gui` | 是否显示本地 PyBullet 窗口；不等于录像开关 |
| `media_dir` | `None` 不录制；提供路径则输出 MP4、初始 PNG、最终 PNG |
| `show_landmarks` | 布尔值，默认 `True`；只控制 A/B 显示，不改变动作、评分或液体显示 |

`SequenceConfig` 的完整默认值和每步入口、动作、退出、时限、允许接触可通过 `task_spec()` 获得，也可不启动物理仿真导出：

```bat
python -m grasp_a task-spec --task handover_transfer --out results\local\handover_spec.json
```

最小调用示例：

```python
from grasp_a.contracts import load_candidates
from grasp_a.sequence import get_sequence_candidates, run_sequence
from grasp_a.sequence_contracts import SequenceConfig

task_id, seed = "handover_transfer", 0
candidates = load_candidates("candidates.example.json")
config = SequenceConfig()
filtered = get_sequence_candidates(task_id, seed, candidates, config=config)
if filtered["status"] != "ok":
    raise SystemExit("no_feasible_candidate: skip selection/execution")

public_candidates = filtered["feasible_candidates"]
selected_id = public_candidates[0]["id"]  # 仅 SMOKE；正式实验由 B/C 选择器替换
allowed = {candidate["id"] for candidate in public_candidates}
if selected_id not in allowed:
    raise ValueError("selector output is not an allowed ID")

result = run_sequence(task_id, seed, selected_id, candidates, config=config)
assert result["candidate_sha256"] == filtered["candidate_sha256"]
assert result["task_spec_sha256"] == filtered["task_spec_sha256"]
print(result["status"], result["success"], result["failure_reason"])
```

## 筛选结果

`get_sequence_candidates` 返回 `status=ok / no_feasible_candidate`、`feasible_candidates`、`checks`，以及版本、seed、任务定义、观测配置、候选与任务指纹。`feasible_candidates` 每条只有五个公开选择字段。

筛选只覆盖初始开口、IK/FK 和采样接近路径检查，不保证放置、交接或倒水成功，也不应通过执行试跑来筛掉任务失败候选。空列表时跳过选择和执行，单独统计覆盖率；非法 ID 是选择器输出问题，不是物理失败。

初始路径检查目前沿用供体自碰撞、桌面／地面和源杯检查；额外接水杯、接收器相关接触在执行评分中检查。因此可行列表也不是完整场景、完整动作序列的无碰撞保证。

## 执行结果和判据

| 字段 | 用法 |
| --- | --- |
| `status` | 执行／数值状态，不等于成功 |
| `success` | 整段动作、阶段结果、无掉落／非法碰撞、完整终态窗口全部通过才为 `True` |
| `failure_reason` | 首个失败类别或带阶段的执行原因；诊断应结合结构化检查，不依赖一份固定字符串枚举 |
| `task_id / seed / selected_id` | 试验身份 |
| `candidate_sha256` | 完整有序候选含位姿的指纹 |
| `task_spec / task_spec_sha256` | 任务定义与其指纹，包含评分阈值及流体配置 |
| `observation_config / initial_object_pose_estimate` | 输入估计与观测来源 |
| `stages` | 每步 `id / control_status / start_s / end_s / feedback`；未执行步骤仍保留 |
| `stage_evaluation` | 每步物理检查、端点证据、采样统计；评分用途，不反馈给控制器 |
| `success_checks / terminal_checks` | 整段判据与最终连续窗口的逐项检查 |
| `final_evidence` | 实际杯子状态、供体／接收端接触、间距、桌面支撑；倒水另含 `water` |
| `trajectory / collision_events` | 诊断数据；接触检查每物理步运行，轨迹常规 10 Hz，终态窗口每步保存 |
| `liquid_model / liquid_initial_check` | 流体算法版本、参数、限制及初始保水检查；空杯任务为 `None` |
| `elapsed_s` | 模拟秒，不是 CPU 运行时间 |
| `media` | 仅录制时出现；含 `video / scene_image / final_image / landmarks_enabled`；开启标记时另含 `annotations` |

| `status` | `success` | 建议统计方式 |
| --- | --- | --- |
| `completed` | `True` 或 `False` | 看实际物理检查；动作走完不代表任务成功 |
| `rejected` | `False` | 初始抓取不可行，单独记录筛选／选择一致性问题 |
| `execution_failed` | `False` | IK、到位或超时等执行失败，保留原因 |
| `simulation_error` | `False` | 初始化不合格或流体发散，单独报告数值错误 |
| `simulation_invalid` | `False` | 整段流体密度有效性不合格，单独报告，不冒充物理失败 |

无效参数和不匹配的 ID 可直接抛 `ValueError`；C 应单独捕获并记录配置／选择问题，不将其默认为成功或把它替换成另一条候选。数值有效覆盖率、执行失败率与任务成功率应分开报告。

三个任务都要求无掉落和非法接触。放置要求松手撤离后桌面独立支撑；交接要求接收端双指支撑、供体分离、独立抬升并稳定；倒水要求最终连续窗口内留存 ≥90%、杯外 ≤5%、两杯状态稳定且流体数值有效。完整定义以 `task_spec` 与 [逐步说明](TASK_SEQUENCES.md) 为准。

## 两种视频版本

标记版使用橙色 A、绿色 B、世界坐标和任务方向箭头。交接视频的绿色 B 表示接收端抬升后的最终杯子中心（默认 z=0.850 m），蓝色 HANDOFF 表示交接位置（z=0.800 m）；机器任务定义的 `point_B` 仍是桌面区域锚点。倒水 B 表示接水杯，蓝色 POUR POSE 表示源杯工作位姿。箭头是任务方向提示，不是机器人规划轨迹。

```bat
python -m grasp_a sequence --task transport_place --seed 0 --id G02 --visualize --landmarks --media-dir results\local\two_versions --out results\local\place_ab.json
python -m grasp_a sequence --task transport_place --seed 0 --id G02 --visualize --no-landmarks --media-dir results\local\two_versions --out results\local\place_plain.json
```

也可调用 `run_sequence(..., media_dir=path, show_landmarks=True / False)`。新生成文件分别以 `_ab.mp4` 和 `_plain.mp4` 结束，PNG 使用相同前缀，因此放进同一目录也不会相互覆盖。相同任务、seed、ID、模式重复运行仍会重写该模式文件，需保留多轮结果时请换目录。关闭标记仍保留阶段／结果标题和液体显示，不会关闭录像或改变液体物理。

仓库保存了三个任务的两套 seed 0 / G02 视频及起止截图，见 [视频索引](../results/sequence_v1_20261017/README.md)；[清单](../results/sequence_v1_20261017/demo_manifest.json) 包含视频 SHA-256 和最终物理证据。两种显示版本逐条核对过完整评分轨迹和最终物理证据一致。录像不是 B/C 的选择输入。

## B 和 C 的迁移边界

C 已通过显式 `track="sequence"` 接入三个完整任务，调用本页的新 A 函数，并独立保存任务定义、筛选、执行状态和数值有效性统计。原配置默认 `track="prep"`，仍只接受两个准备任务；两轨道不能在同一次运行、同一缓存身份或同一成功率中混算。使用 `python -m grasp_c prepare-b --out results/local/c_b_inputs` 导出所有方法共享的 B 评分候选，再运行 `configs/c.sequence.codex.json`；完整命令和统计口径见 [C 接入文档第 11 节](PART_C.md#11-完整序列与-b-基线接入)。

B 的候选结构不变，新任务按 task/seed 在空心杯场景重新筛选，不复用旧实心杯可行列表。B 的几何评分仍基于已知杯身和理想接触近似，当前是实验参考，不是通过空心杯、带水杯验证的正式 M1；M2 任务规则尚未实现。LLM 只接收公开任务意图和五个候选字段，B 内部特征及最终物理证据不进入 prompt。

各方法必须共用完整候选、场景 seed、观测配置、任务配置和版本。缓存至少绑定上述身份、候选指纹和任务定义指纹；是否录像及显示模式也应记录在媒体任务元数据中。不同候选由同一执行器处理，不允许因选择方法不同而换接收策略或重抓规则。

当前交接是已知、配合式接收器的受控物理测试，释放按固定阶段时序进行；没有未知人手轨迹、随机接取时机或传感反馈释放模块。它用于比较抓取是否方便接取，不代表已经完成真实人机交接。液体是带密度／压力约束、黏性与流固耦合的低分辨率 PBF，不是刚体颗粒替代；也不是经真实水标定的高精度 CFD。
