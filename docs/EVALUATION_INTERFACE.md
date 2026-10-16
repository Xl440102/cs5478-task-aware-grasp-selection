# C 评估接入接口定义 v0.3

更新：2026-10-16。控制器 `observable_primitives_v2`；评分 `fixed_target_hold_v2`。
旧 `bounded_ik_v1` 使用运行时物体真值和最后一帧评分，**不能与本版混算**。
本版仍是已知模型、人工语义标注的仿真 pilot，不是视觉感知系统或真实机器人验证。

## 1. 两条实验轨道

- 主实验：B/C 选择已有抓取 ID，A 对所有方法使用同一固定控制器。原来的位置参数不变。
- 可选增量控制：策略逐次发出末端移动、夹爪或等待命令。单独标记 `policy_mode=incremental`，不能混入 M0/M1/M2/M3/A1 的 ID-only 对比。
- A 不调用 LLM；B 负责候选/标签/几何分数与非 LLM 基线；C 负责 LLM、缓存、输出校验、评估统计。

```python
from grasp_a.contracts import load_candidates
from grasp_a.control import ObservationConfig, Primitive
from grasp_a.simulation import get_feasible_candidates, run_episode, run_primitive_episode

candidates = load_candidates("candidates.example.json")
config = ObservationConfig()
filtered = get_feasible_candidates(0, candidates, observation_config=config)
# selector 只能看到 filtered["feasible_candidates"]，不能看到其他诊断或真值。
if filtered["status"] == "ok":
    selected_id = filtered["feasible_candidates"][0]["id"]  # 仅 SMOKE 演示
    episode = run_episode("pour_prep", 0, selected_id, candidates,
                          observation_config=config)
```

## 2. Candidate、坐标和 ObservationConfig

`task_id`：`pour_prep` / `handover_prep`；seed 为整数。
位置单位米，四元数顺序 xyzw。候选位姿在杯身中心坐标系，把手本地 +Y；
世界末端是 Panda 的 `panda_grasptarget`，不是手掌或单指坐标系。

| Candidate 字段 | 类型 | 定义 |
|---|---|---|
| id | string | 同一列表内唯一、稳定 |
| region / approach | string | B 人工标注的部位、接近方向；不宣称由视觉识别 |
| gripper_width | number | 总开口米，筛选允许 0.005–0.08；固定策略先开至 0.08 再闭合 |
| geometry_score | number | B 预先定义的有限分数，A 仅透传 |
| grasp_pose / pregrasp_pose | Pose | `{"position":[x,y,z],"quaternion":[x,y,z,w]}`，杯子坐标系 |

`load_candidates` 校验有限数值、四元数和重复 ID；读取时将四元数归一化。
默认 G01/G02 仍只有一条可行候选；G03 在单独的 pilot 文件。分数是占位，不代表正式 M1。
所有方法必须使用同一有序完整候选列表，不能只给执行器五字段公开字典。

```python
config = ObservationConfig(
    # object_pose=Pose(...), source="external_estimate",  # 接入外部估计器时提供
    position_noise_m=0.003,
    yaw_noise_deg=2.0,
    noise_seed=1000,
)
```

| 配置字段 | 默认值 | 定义 |
|---|---|---|
| object_pose | [0.55,0,0.645], identity quaternion | 标定先验，不是按 scene seed 查询的真实初始位姿 |
| source | calibrated_prior | 仅允许 calibrated_prior / external_estimate |
| position_noise_m | 0 | 对输入估计的世界 xyz 各加均匀 ±误差；非标准差 |
| yaw_noise_deg | 0 | 对输入估计左乘世界 Z 轴均匀 ±误差 |
| noise_seed | 0 | 仅控制上述扰动，不使用全局 RNG |

`external_estimate` 只是调用方提供估计的来源标记，A 不运行也不验证外部感知算法。
噪声加在先验/外部估计上，**不从真实物体位姿生成伪视觉观测**。
scene seed 仍使真实杯子 x/y 变化 ±8 mm、偏航变化 ±0.08 rad；默认控制器不知道这些变化。
因此 baseline 即使额外噪声为零，也有先验与实际物体之间的误差。
三维噪声包含 z，可能使估计模型略低于或高于桌面；这是误差压力测试，不能当作真实传感器标定结果。

筛选和执行必须使用相同 config；各方法在同一条件下共享 noise_seed。
任务评分目标独立于 scene seed、候选和观测噪声，不能随抓取结果调整。

## 3. 控制观测与评分真值边界

控制器只使用初始物体位姿估计、已知模型、候选位姿、关节编码器和末端 FK。
抓取后物体相对夹爪的关系来自候选的名义变换，不读取真实抓取偏差。
不会依据真实接触数、真实物体高度进行 no_grasp/lost_grasp 提前分支；
因此失败抓取也可能继续尝试动作，最终由评分器判失败。

`observe()` / `ControlSession.observe()` 的字段：

| 字段 | 含义 |
|---|---|
| tool_pose | 当前末端 FK 世界位姿 |
| joint_positions | 7 个关节角，弧度 |
| gripper_width | 两指编码器开口之和；不能证明抓住了指定物体 |
| initial_object_pose_estimate | 冻结的初始估计，不是实时物体跟踪 |
| observation_source | calibrated_prior / external_estimate |
| elapsed_s / actions_executed | 已模拟时间和已尝试动作数 |
| last_action | 初始为 null；之后有 kind、status，移动另有末端位置/方向误差 |

`last_action.status=executed` 表示已执行电机控制，不保证物体到位；
`ik_failed` 表示未移动，没有自动重试或自动换分支策略。
末端误差来自 FK，不是杯子真值。

物体真值、接触点、碰撞、成功标签仅用于 `EpisodeEvaluator` 的记录和终端评分，
不进入上述观测、policy context 或动作反馈。轨迹/result 仍含真值，**只能在 episode 结束后用于评估**。
这是工程 API 边界，不是 Python 安全沙箱；策略不得访问私有 scene、PyBullet 全局接口或其他进程中的仿真状态。

仍保留的理想化条件：已知杯子/桌面/机器人模型、固定环境、人工语义标注、精确机器人运动学。
可行性检查把已知杯子模型放在估计位姿进行离线碰撞探测，随后恢复全部物理状态；
这不是实测碰撞反馈，也不证明真实场景或执行路径安全。不能宣称“完全没有 privileged assumptions”。

## 4. FilterResult

```text
get_feasible_candidates(seed, candidates, *, observation_config=None) -> dict
```

| 字段 | 类型 | 定义 |
|---|---|---|
| controller_version | string | observable_primitives_v2 |
| seed | int | 场景 seed |
| candidate_sha256 | string | 有序完整候选 SHA-256，含位姿和分数 |
| observation_config | object | 完整配置 |
| initial_object_pose_estimate | Pose | 实际使用的带扰动估计 |
| status | string | ok / no_feasible_candidate |
| feasible_candidates | list | 只含 id、region、approach、gripper_width、geometry_score |
| checks | list | 每条候选的 Feasibility |
| selection_space | object | feasible_count、regions、approaches、warnings；仅供审计，不给选择器 |

warning：`fewer_than_two_feasible_candidates`、`single_or_no_semantic_region`、
`task_diversity_not_validated`。多条候选、多个标签不等于任务适用性确有差异，需要 B 独立验证。

Feasibility 必有：
candidate_id、feasible、reasons、pregrasp_position_error_m、grasp_position_error_m、
min_table_clearance_m、collision_details。数值在宽度失败时可为 null。
min_table_clearance_m 仅预抓取配置下的桌面最小距离，0.10 m 内没点时记 0.10。

筛选检查宽度、预抓取/抓取 IK/FK、关节限位和 home→pregrasp→grasp 两段关节路径，
每段 21 个采样点，穿入超过 1 mm 判碰撞。只有抓取终点允许预期手指接触。
reasons 包括 gripper_width_out_of_range、pregrasp_ik_or_fk_error、
grasp_ik_or_fk_error、home_to_pregrasp:<types>、approach:<types>。
碰撞类型：robot_table、robot_floor、robot_mug_nonfinger、robot_self。
collision_details 含 type、robot_link、other_link、penetration_m、path、sample（0–20）。
通过筛选不保证夹持稳定、后续路径可行或连续无碰撞。

## 5. 固定控制器和 EpisodeResult

```text
run_episode(task_id, seed, selected_id, candidates, gui=False, media_dir=None,
            *, observation_config=None) -> dict
```

新建世界、重查候选、预抓取180步、接近120步、闭合180步、抬升200步、
任务移动200步、保持480步。倒水额外执行 task_clearance/task_rotate 各200步：
向机器人侧约10 cm并增加约4 cm高度，在该区域旋转后回到共同目标。
抬升、任务移动使用分段 TCP 路径；预抓取/接近仍使用与筛选对应的关节插值。
路径以约1.5 cm / 5°分段求 IK，但这不是实际路径误差容差或连续碰撞保证。
每步为1/240秒；交接约5.67秒，倒水约7.33秒。

| 必有字段 | 类型/定义 |
|---|---|
| schema_version | episode_v0.3 |
| controller_version / evaluation_version | observable_primitives_v2 / fixed_target_hold_v2 |
| seed / task_id / selected_id | 输入身份 |
| policy_mode | fixed 或 incremental |
| observation_config / initial_object_pose_estimate | 配置与控制估计 |
| feasibility | 重新检查结果 |
| success / failure_reason | bool / string或null |
| dropped | 首次抬升形成后，在评分采样点发现手指接触归零；不是无抓取的同义词 |
| collision / collision_types | 碰撞标记和去重类型；包括筛选碰撞 |
| grasp_maintained | 最终保持、保持区间每步保持且无已检测掉落；提前失败时也如实计算，不等于任务成功 |
| position_error_m / orientation_error_deg | 返回时真实杯子相对固定目标的误差；提前失败也有数值 |
| target_mug_pose / final_mug_pose | 固定目标和终端真实位姿；两者均必有 |
| hold_steps / hold_duration_s | 实际评分保持步数/秒数；提前终止通常为0 |
| hold_pose_within_tolerance | 完整保持且每步目标误差合格 |
| trajectory / ik_diagnostics / primitive_log | 特权评分轨迹、IK诊断、动作及可观测反馈日志 |

录像时另有 media，含 video、scene_image、final_image 的本机绝对路径。
不同方法用不同 media_dir，文件名不含方法名，避免覆盖。

failure_reason：
infeasible、pregrasp_ik_failed、grasp_ik_failed、lift_ik_failed、task_ik_failed、
lost_grasp、unintended_collision、hold_incomplete、target_pose_error、
action_budget_exceeded（仅增量轨道）。
不再以真值接触门限生成 no_grasp。规划失败不等于掉落；必须分别统计 dropped/collision。
终端评分原因优先级：失去保持→碰撞→保持不足→目标误差；显式规划/预算失败覆盖主原因。

task/ID非法、重复匹配、无效动作、文件、仿真或录像异常可能抛出；
不是物理失败，C 不得伪造掉落/碰撞。普通执行异常由包装示例记录。

## 6. 固定目标、保持与碰撞评分

杯子中心世界目标固定为 **[0.55, 0, 0.785] m**。
pour_prep 为世界Y轴+35°，handover_prep 为世界Z轴−90°（均从标定单位姿态出发）。
不再使用实际抬升后 x/y 或实际闭合后姿态定义目标。

保持区间480个物理步逐步检查：
```text
held = finger_contacts > 0 and mug_z > 0.69 m
pose_ok = position_error <= 0.04 m and orientation_error <= 15 degrees
success = full_2s_hold and every_hold_step_held and every_hold_step_pose_ok
          and final_held and not detected_drop and not collision
```

其他阶段物体/接触约10 Hz采样，碰撞在约10 Hz和阶段末采样；
保持的物体位姿/接触逐步评分，但日志仍约10 Hz。
杯子第一次被抬起后再次触桌会记录 mug_table_after_lift；
初始放在桌面不算碰撞。短暂且未采样的事件仍可能漏检，不能称连续安全验证。
任务动作结束紧接保持，不设置按真值自适应的等待期；第一步超差也会失败。
这些标准比旧版“最后一帧正确”更严格。

trajectory 字段：stage、step（当前动作内零基步）、physics_step（episode累计步数）、
action_index（primitive_log零基索引）、mug_pose、tool_pose、finger_contacts。
同 stage 可以有多次动作，使用 action_index/physics_step 对齐。
阶段增加 task_clearance、task_rotate、policy；不要只接受旧六阶段枚举。

ik_diagnostics 可在同阶段出现多个 TCP waypoint：
stage、target_pose、movable_dofs=9、success、attempts；
成功另有 solver=local|bounded 和7个 joint_positions。
attempt 包含 method、reason，通常还有 raw_joint_limit_violations、
position_error_m、orientation_error_deg。
预抓取/抓取/移动IK验收为0.025 m、15°，不等于实际电机跟踪误差保证。
bounded 是数值IK回退，和LLM修复/回退M1无关。

## 7. 增量 primitive 接口

```python
Primitive("move_tcp", target=Pose(...), duration_s=.5, path="cartesian")
Primitive("move_tcp", target=Pose(...), duration_s=.5, path="joint")
Primitive("gripper", width=.06, duration_s=.5)
Primitive("wait", duration_s=.2)
```

move_tcp：世界绝对TCP目标；默认分段Cartesian规划，joint为关节插值。
单动作平移≤0.25 m；目标盒 x∈[0.20,0.90]、y∈[-0.45,0.45]、z∈[0.60,1.20] m。
总开口∈[0,0.08] m；时长∈[1/240,2]秒。
限制不等于碰撞安全，不能直接用于真机。没有 pickup/place、物体瞬移或刚性附着。

```text
run_primitive_episode(task_id, seed, selected_id, candidates, policy,
                      *, observation_config=None, max_actions=32) -> dict
policy(context, observation) -> Primitive | None
```

context 含 task_id、完整静态 candidate、估计的 pregrasp_tool_pose/grasp_tool_pose、
固定 target_object_pose，不含真实当前杯子状态。
policy 每次收到新的可观测反馈；可改变接近路径、开口、时长、重试策略或停止。
None 表示停止发动作，系统仅执行标准两秒等待并评分，**不会自动补齐抓取或任务动作**。
超过预算停止；max_actions允许1–64，默认32；动作异常向外抛出。
无真实触觉/实时视觉输入，编码器反馈不能自动解决滑移检测。
策略不能读取完整结果或评价轨迹再决定下一动作。

可运行示例：
```bat
python -m examples.incremental_policy --task pour_prep --id G02 --retreat-m .10 --clearance-m .04
```
该示例是手写策略，不是LLM或新baseline。

## 8. C 的 ID-only 包装记录

`examples.evaluate_one_trial.evaluate_trial` 保留原有 selector 协议：
```text
selector(task_id, public_candidates, seed) -> str | dict
evaluate_trial(task_id, seed, method_id, candidates, selector, *,
               filtered=None, media_dir=None, observation_config=None) -> dict
```

字符串为ID；字典为 selected_id、可选 ranking、可选 metadata。
ranking如提供，必须是全部可行ID的完整无重复排列，首项等于selected_id。
metadata须可JSON序列化，不放API key；C自行保存模型、提示词版本、一次修复、M1回退和缓存信息。
包装器不实现LLM、M0–M3/A1、输出修复或回退。

包装 schema_version 升为 evaluation_trial_v0.2；
字段有 evaluation_scope=pilot、controller_version、method_id、task_id、seed、
candidate_sha256、observation_config、status、selected_id、selection、
filter、episode、error。selection含 latency_s；未调用时为null。
error无错误为null，有错误为type/message。
筛选缓存必须匹配完整候选指纹、控制器、seed和观测配置，否则抛ValueError。
JSON存取前后tuple/list差异不会造成配置不匹配。场景/资产/代码仍需由运行清单冻结。

| status | 含义 |
|---|---|
| completed | 已返回有效物理结果，success=false也必须保留 |
| no_feasible_candidate | 同条件各方法共同记录覆盖率；不调用selector或执行器 |
| invalid_selection | ID、排名或metadata不合法；未执行 |
| selector_error | selector普通异常；未执行 |
| execution_error | 执行或结果一致性检查异常；不能伪造成物理失败 |

包装检查执行身份、controller、可行性和观测配置。
输入错误、筛选不匹配、筛选本身异常向外抛出；用户中断不捕获。
latency_s只计selector调用墙钟时间，包括其内部缓存/修复，不含物理仿真。
CLI退出0表示合法完成或跳过，不代表物理成功。

```bat
python -m examples.evaluate_one_trial --task pour_prep --seed 0 --out results/local/interface_v03_smoke.json
python -m unittest discover -s tests -v
```

## 9. 回归与统计口径

[本版回归汇总](../results/observable_v2_20261016/summary.json)：
G02/G03、2任务、seed0–9，baseline 33/40；
额外±3mm/±2°为15/40，±8mm/±5°为15/40。
噪声加在标定先验上；这些不是实际传感器误差分布，也不是选择器对比。
噪声两组成功数相同不证明性能单调或统计等价；当前样本很小且用于开发。
原始逐次日志和filter一并保存，失败不剔除。

重跑：
```bat
python -m scripts.check_observable_control --out results/local/observable_regression
python -m grasp_a run --task pour_prep --seed 0 --id G02 --pose-noise-m .003 --yaw-noise-deg 2 --observation-seed 1000 --out results/local/noisy_episode.json
```

报告条件成功率时列completed分母；同时报告可行覆盖率、非法选择、selector/execution异常、
碰撞、检测掉落及误差有效n。不能补零或只保留成功seed。
配对比较按task/seed/候选哈希/controller/观测配置/环境共同匹配；
噪声配置不能在筛选与执行之间更换。正式端到端分母及异常处理由全组预先冻结。
M3有效性、一致性、修复/回退率从C的调用日志统计，不能从IK结果推断。

## 10. 正式实验前仍需完成

B提供具有实质差异的可行候选及独立任务适用性定义；多条相近杯身抓取还不够。
保留同一固定控制器做ID-only公平比较，增量策略单独成实验。
冻结代码/资产哈希、完整Conda包版本、候选、目标、成功规则和开发/正式seed；
seed0–9已经用于调试，不得冒充未见测试集。
真实感知、触觉、未知物体、液体或真人交接均未实现。
依赖没有新增；environment.yml尚未锁定全部包，跨环境不保证逐字段复现。
