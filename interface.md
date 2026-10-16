# Part A Interface v0.3

当前控制器：`observable_primitives_v2`，评分：`fixed_target_hold_v2`。
C 请以 [完整评估协议](docs/EVALUATION_INTERFACE.md) 为准；本页是快速入口。
旧 v0.1/v0.2 和 `bounded_ik_v1` 结果保留追溯，不与新版混算。

## Candidate input

JSON 数组，每条有稳定唯一 id、region、approach、gripper_width、geometry_score、
grasp_pose、pregrasp_pose。位姿在杯子坐标系，单位米、四元数xyzw；把手本地+Y。
region/approach是人工标注，geometry_score由B预先定义。
A只向ID选择器公开 id、region、approach、gripper_width、geometry_score；
不公开执行真值、任务适用性标签、完整位姿或评价结果。

## ID-only 主实验

```python
from grasp_a.contracts import load_candidates
from grasp_a.control import ObservationConfig
from grasp_a.simulation import get_feasible_candidates, run_episode

candidates = load_candidates("candidates.example.json")
config = ObservationConfig()  # 固定标定先验，不是模拟器精确物体位姿
filtered = get_feasible_candidates(0, candidates, observation_config=config)
if filtered["status"] == "ok":
    selected_id = filtered["feasible_candidates"][0]["id"]  # SMOKE；由B/C替换
    result = run_episode("handover_prep", 0, selected_id, candidates,
                         observation_config=config)
```

`get_feasible_candidates(seed, candidates, *, observation_config=None)` 返回
controller_version、seed、candidate_sha256、observation_config、
initial_object_pose_estimate、status、feasible_candidates、checks、selection_space。
status为ok或no_feasible_candidate；空列表时跳过选择与执行并报告覆盖率。
selection_space警告用于审计，不是额外的选择器输入。

`run_episode(task_id, seed, selected_id, candidates, gui=False, media_dir=None,
*, observation_config=None)` 保留原位置参数。task_id仅pour_prep/handover_prep。
所有方法共享相同观测配置、noise_seed、完整候选和固定控制器。
旧函数调用仍有效；旧缓存/返回字典和成功语义不能沿用。

控制使用初始估计、候选名义变换、关节编码器和末端FK；
不根据运行时真实杯子位姿、接触数或高度决定动作。
筛选用已知模型放在估计位姿进行离线几何探测，随后完整恢复物理状态。
这仍是已知模型假设，不是纯视觉系统或实机安全保证。

目标杯子中心固定为世界[0.55,0,0.785] m，
方向分别为世界Y+35°、世界Z−90°。
成功要求完整两秒保持的每个物理步均夹持且误差≤4 cm/15°，
且无检测掉落/意外碰撞。真值仅用于终端评价，不能反馈给执行中的策略。

结果含schema_version=episode_v0.3、controller_version、evaluation_version、
身份、观测配置、feasibility、success、failure_reason、dropped、collision、
collision_types、grasp_maintained、目标/最终位姿、误差、hold_steps、
hold_duration_s、hold_pose_within_tolerance、trajectory、ik_diagnostics、
primitive_log、policy_mode。录像另有media。
提前失败也返回目标和真实终端误差；详细失败枚举见完整协议。

## 估计和噪声

默认先验：[0.55,0,0.645]、单位方向，与scene seed无关。
真实杯子仍按seed扰动；控制器并不知道该扰动。
可提供`ObservationConfig(object_pose=Pose(...), source="external_estimate")`，
或设置position_noise_m、yaw_noise_deg、noise_seed。
扰动为输入估计上的均匀±误差，不从物体真值生成观测。
A没有实现视觉估计，外部位姿应由调用方提供。

## 可选增量控制轨道

`Primitive("move_tcp", target=Pose(...), duration_s=.5, path="cartesian"|"joint")`
控制世界TCP目标；`Primitive("gripper", width=.06)`控制总开口；
`Primitive("wait", duration_s=.2)`等待。没有pickup/place宏动作。

`run_primitive_episode(..., policy, observation_config=None, max_actions=32)`
调用policy(context, observation)，返回一条Primitive或None。
观测只有机器人本体反馈、冻结的初始估计及上次动作状态。
策略可选路径、开口、时长、重试或停止；停止后只等待两秒评分，不自动抓取。
此轨道标记incremental，不能混入固定控制器的抓取选择对比。
限制、字段和边界见 [完整定义第7节](docs/EVALUATION_INTERFACE.md#7-增量-primitive-接口)。

```bat
python -m examples.incremental_policy --task pour_prep --id G02
python -m examples.evaluate_one_trial --task pour_prep --seed 0
python -m scripts.check_observable_control --out results/local/observable_regression
```

C的包装记录升级为evaluation_trial_v0.2，并严格匹配筛选的完整候选指纹和观测配置。
默认候选只有一个可行ID；G03也是相近杯身抓取，不能据此声称LLM任务选择有效。
B仍需提供有实际任务差异的可行候选与独立标签。
