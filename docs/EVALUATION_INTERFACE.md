# C 评估接入接口定义 v0.2

核对日期：2026-10-15。适用控制器：`bounded_ik_v1`（修复提交 `cd552f1`）。
本文按当前代码定义输入、返回值和统计口径，供 C 编写批量评估。
文档版本 v0.2 不改变 A 的函数签名；A 原始字典没有 `schema_version`。
[调用示例](../examples/evaluate_one_trial.py) 另外提供 `evaluation_trial_v0.1` 包装记录。

当前可做 pilot 联调。正式实验前需要冻结共同任务目标、保持判定、候选与评分、环境和测试 seed；第 8 节明确尚未满足的评估条件。

## 1. 调用顺序与责任

在项目根目录激活 `grasp-a`。A 不需要 API key，不调用 LLM。

```python
from grasp_a.contracts import Candidate, Pose, TASKS, load_candidates
from grasp_a.simulation import CONTROLLER_VERSION, get_feasible_candidates, run_episode
```

```text
load_candidates(path: str | Path) -> list[Candidate]
get_feasible_candidates(seed: int, candidates: list[Candidate]) -> dict
run_episode(task_id: str, seed: int, selected_id: str,
            candidates: list[Candidate], gui: bool = False,
            media_dir: str | Path | None = None) -> dict
```

读取完整候选 → 按 seed 筛选 → 把公开候选交给选择器 → 校验 ID → A 在新世界执行 → C 保存完整记录。
执行函数需要完整 `list[Candidate]`，不能把五字段公开字典传作 `candidates`。

| 归属 | 内容 |
|---|---|
| A | 场景、筛选、统一控制器、物理结果和轨迹 |
| B | 候选位姿/标签、geometry_score、M0/M1/M2、独立可接受集合 |
| C | M3/A1、LLM 修复与回退、缓存、方法标签、批量日志与统计 |

## 2. 坐标、单位与 Candidate

`task_id` 为 `"pour_prep"` 或 `"handover_prep"`；`seed` 为整数。
当前 seed 只生成杯子初始 x/y（约 ±0.008 m）及偏航（约 ±0.08 rad）扰动，
尚未实现额外控制器随机扰动。文件中的相对抓取位姿本身没有随机扰动。
随机选择器应使用独立 RNG，以便自身按 seed 重现。LLM 的生成设置与缓存由 C 固定，
不能把环境 seed 当成模型一定支持的解码 seed。0–9 已用于控制器 pilot，正式评估 seed 需另行冻结并注明划分。

```json
{"position": [0.0, 0.0, 0.012],
 "quaternion": [0.0, 0.70710678, 0.0, 0.70710678]}
```

上述 `Pose` 的位置用米，四元数顺序 `[x,y,z,w]`。候选位姿相对杯身中心，把手在本地 +Y。
A 用 `T_world_mug * T_mug_tool` 转为 `panda_grasptarget` 末端目标。
轨迹、IK 诊断、目标和最终杯子位姿都使用世界坐标。
读取时检查数值有限、四元数范数与 1 的差不超过 0.01，并归一化。

| Candidate 字段 | 类型 | 约定 |
|---|---|---|
| `id` | string | 完整列表中唯一、稳定 |
| `region` | string | 手工部位标签，如 body/handle；当前无强制枚举 |
| `approach` | string | 接近方向描述；当前无强制词表 |
| `gripper_width` | number | 两指总开口，米；筛选范围 0.005–0.08 |
| `geometry_score` | number | B 提供的有限分数，越高越优；A 不计算或校准 |
| `grasp_pose` | Pose | 杯子坐标系下的抓取末端目标 |
| `pregrasp_pose` | Pose | 同一坐标系下的预抓取目标 |

执行器固定先张开至总开口约 0.08 m，再命令闭合；`gripper_width` 用于筛选，不是精确执行开口。
用 `load_candidates` 读取 JSON 数组以检查重复 ID；文件、JSON、缺字段或非法数值导致的异常是输入错误，不能计作物理失败。
默认 G01/G02 中只有 G02 可行；单独的 `candidates.pilot.json` 提供 G03。
当前评分均为 pilot 输入，G03 的 0 分是占位；正式 M1 需要 B 冻结定义。

## 3. FilterResult

筛选与 task 无关。每个 seed 用同一完整候选和参数筛选，再把同样的公开列表复制给各方法。
函数建立并关闭 DIRECT 世界。

| 顶层字段 | 类型 | 含义 |
|---|---|---|
| `controller_version` | string | 当前为 bounded_ik_v1 |
| `seed` | int | 输入 seed |
| `status` | ok / no_feasible_candidate | 公开列表是否非空 |
| `feasible_candidates` | list[dict] | 可行公开候选，保留输入顺序 |
| `checks` | list[Feasibility] | 每条输入候选的检查记录，包括被拒绝项 |

**选择器仅收到五字段**，不应额外收到位姿、失败原因、轨迹、任务真值标签。当前公开接口也没有净空或接触评分：

```json
{"id": "G02", "region": "body", "approach": "from_robot_side_high",
 "gripper_width": 0.078, "geometry_score": 0.66}
```

| Feasibility 字段 | 类型 | 含义 |
|---|---|---|
| `candidate_id` | string | 候选 ID |
| `feasible` | bool | 所有必需检查是否通过 |
| `reasons` | list[string] | 通过时为空，失败可能有多个原因 |
| `pregrasp_position_error_m` | number/null | 预抓取 IK 的 FK 位置误差 |
| `grasp_position_error_m` | number/null | 抓取 IK 的 FK 位置误差 |
| `min_table_clearance_m` | number/null | **预抓取配置处**最小桌面距离，不是全路径最小值 |
| `collision_details` | list[dict] | 被拒绝路径上的碰撞证据 |

宽度失败提前返回，其余数值字段可能为 null。0.10 m 内没查到桌面点时净空记 0.10；
轻微负值可能仍在 1 mm 容差内。当前不返回 FK 角度误差字段，但仍检查角度容差。

`reasons` 包括 `gripper_width_out_of_range`、`pregrasp_ik_or_fk_error`、
`grasp_ik_or_fk_error`、`home_to_pregrasp:<types>`、`approach:<types>`。
冒号后的类型以逗号分隔，不要写死完整字符串。
类型为 `robot_table / robot_floor / robot_mug_nonfinger / robot_self`；
`robot_mug_nonfinger` 是现有标签，在禁止手指接触的阶段也可能指手指与杯子碰撞。

每个 collision_details 有 `type: string`、`robot_link: string`、
`other_link: string`、`penetration_m: number`、
`path: home_to_pregrasp|approach`、`sample: int`。
`sample` 为 0–20 的插值采样序号，不是物理时间步；每段只保留首次拒绝点的证据。
自碰撞的 other_link 是连杆名称，其他物体通常记录索引字符串 `"-1"`。

每段检查 21 个关节插值点，穿入超过 0.001 m 判作碰撞；抓取终点允许预期手指接触。
筛选通过不保证后续抬升/任务成功，也不等于连续路径无碰撞或精确力闭合。

## 4. 执行参数、异常与录像

```python
result = run_episode("pour_prep", 0, "G02", full_candidates,
                     gui=False, media_dir=None)
```

每次执行新建独立世界，不复用前一种方法的物理状态。所选 ID 必须来自当次可行列表，A 会再检查。

| 情况 | A 的行为 | C 的处理 |
|---|---|---|
| task_id 非法 | ValueError | 配置错误，停止修正 |
| ID 没有恰好匹配一次 | ValueError | 选择器/候选协议错误 |
| ID 存在但不可行 | success=false，failure_reason=infeasible | 若先前允许该 ID，检查配置或输入不一致 |
| 筛选列表为空 | 不应调用执行函数 | 同 seed 所有方法一起记录跳过 |
| 仿真、依赖、录像或文件异常 | 可能抛出 | 记录执行异常，不伪造掉落或碰撞 |

批量运行用 `gui=False`。`media_dir=None` 不录像，指定目录会增加开销。
原始视频名只有任务、seed、ID，**没有方法名**，需为不同方法提供不同目录以防覆盖。
`media` 路径是生成机器上的绝对路径，换机器后按相对位置查找。

## 5. EpisodeResult 字段与失败分类

Python None 序列化为 JSON null，tuple 为数组；缺失值不能补零。

| 字段 | 类型 | 必有 | 当前含义 |
|---|---|---|---|
| `controller_version` | string | 是 | 控制器标识 |
| `seed / task_id / selected_id` | int/string/string | 是 | 执行身份 |
| `feasibility` | Feasibility | 是 | 选中候选的重新检查 |
| `success` | bool | 是 | 第 8 节的当前 pilot 判定 |
| `failure_reason` | string/null | 是 | 成功为 null，失败见下表 |
| `dropped` | bool | 是 | 遗留失败标记，不能直接解释为真实掉落事件 |
| `collision` | bool | 是 | 记录到意外碰撞；不可行时也可能含筛选碰撞 |
| `collision_types` | list[string] | 是 | 碰撞类型去重排序列表 |
| `grasp_maintained` | bool | 是 | 最终保持代理；提前返回一律 false |
| `position_error_m` | number/null | 是 | 最终杯子中心到目标的欧氏距离，米 |
| `orientation_error_deg` | number/null | 是 | 最终与目标四元数的最短角差，度 |
| `target_mug_pose` | Pose | 否 | 仅执行到最终评估分支时返回 |
| `final_mug_pose` | Pose | 是 | 返回时杯子的世界位姿 |
| `trajectory` | list[TrajectorySample] | 是 | 已执行阶段采样；筛选失败时为空 |
| `ik_diagnostics` | list[IKDiagnostic] | 是 | 所选候选的筛选及执行 IK 诊断 |
| `media` | object | 否 | 录像时含 video / scene_image / final_image 三个路径 |

提前失败的误差为 null、target_mug_pose 缺失。末端 IK 目标不能替代杯子目标。
collision=false 只表示采样未记录碰撞，不是连续无碰撞保证。

| failure_reason | 触发条件 | 统计注意 |
|---|---|---|
| `infeasible` | 重新筛选不通过 | 未执行物理抓取 |
| `no_grasp` | 闭合后手指接触点为零 | 当前 dropped=true，但实际是没抓住 |
| `lift_ik_failed` | 抬升目标无通过限位/FK 的解 | 规划失败，不等于掉落 |
| `task_ik_failed` | 任务目标无通过限位/FK 的解 | 同上；grasp_maintained=false 也不证明此时失去接触 |
| `lost_grasp` | 抬升或最终检查时杯子过低/无手指接触 | 抓取丢失代理，可能未形成稳定抓取 |
| `unintended_collision` | 完整执行有碰撞且最终仍保持 | 独立查看 collision |
| `target_pose_error` | 最终保持、无碰撞，但位姿超差 | 仅完整执行分支 |

最终主失败原因优先级：失去抓取 → 碰撞 → 位姿超差。
同一次执行可有多种现象，不能仅凭 failure_reason 计算碰撞率。
dropped 和 grasp_maintained 都不是全过程事件传感器，建议称“控制器报告的掉落/保持标记”。

## 6. 轨迹和 IK 诊断

| TrajectorySample 字段 | 类型 | 含义 |
|---|---|---|
| `stage` | string | pregrasp / grasp / close / lift / task_motion / hold |
| `step` | int | **当前阶段内**零基步数，换阶段重置 |
| `mug_pose / tool_pose` | Pose | 世界坐标的杯子与末端位姿 |
| `finger_contacts` | int | 手指接触点数，不是手指数或夹紧力 |

步长 1/240 秒，通常每 24 步和阶段末采样。阶段内时间可用 `(step+1)/240`，列表索引不是物理步。
六个阶段分别为 180/120/180/200/200/480 个物理步；完整运动约 5.67 秒，保持为 2 秒。

| IKDiagnostic 字段 | 类型 | 含义 |
|---|---|---|
| `stage` | string | filter_pregrasp / filter_grasp / pregrasp / grasp / lift / task_motion；直接调用可为 unspecified |
| `target_pose` | Pose | 末端世界目标，与杯子目标不同 |
| `movable_dofs` | int | Panda 当前为 9，含两指 |
| `success` | bool | 此次求解是否通过校验 |
| `attempts` | list[dict] | 局部和必要时的有界备用尝试 |
| `solver` | string，可缺失 | 成功时为 local 或 bounded |
| `joint_positions` | list[7 numbers]，可缺失 | 成功时 panda_joint1–7 的弧度值 |

attempt 必有 `method: local|bounded` 和 `reason`。
reason 为 invalid_solver_output / joint_limit / fk_tolerance / accepted；
正常数值输出另含 `raw_joint_limit_violations: list[string]`、
`position_error_m`、`orientation_error_deg`。
预抓取/抓取容差 0.025 m、15°；抬升/任务容差 0.035 m、15°。
优化中的更小收敛目标不是正式任务成功容差。
**IK 的 bounded 备用求解和 LLM 的一次修复/回退 M1 必须分别统计。**

## 7. 选择器协议与评估包装记录

可直接调用 A，也可使用 [evaluate_trial](../examples/evaluate_one_trial.py)。
它不实现 M0–M3/A1，不调用 LLM，不自动执行修复和 M1 回退；C 在自己的 selector 内完成这些策略。

```text
select(task_id: str, public_candidates: list[dict], seed: int) -> str | dict
```

基线可返回 `"G02"`；C 可返回结构化结果：

```json
{
  "selected_id": "G02",
  "ranking": ["G02"],
  "metadata": {
    "model_id": "填写实际模型",
    "prompt_version": "v1",
    "repair_attempts": 0,
    "fallback_to_m1": false,
    "cache_hit": false
  }
}
```

ranking 可省略或为 null；提供时必须是当前全部可行 ID 的完整排列，首项等于 selected_id。
原始无效回答与排名放 metadata，不能作为最终有效排名提交。
metadata 必须为可 JSON 序列化的字典；C 自行保存模型参数、原始回答、校验和缓存信息，不放 API key。
示例拒绝非法输出，不替 C 自动修复。

```text
evaluate_trial(task_id, seed, method_id, full_candidates, selector,
               filtered=None, media_dir=None) -> dict
```

method_id 建议 M0/M1/M2/M3/A1，允许字母、数字、下划线、连字符。
filtered 可复用同 seed 的筛选字典，但必须来自**完全相同的候选位姿、场景、代码和环境**。
示例能检查 seed、控制器、ID 和公开字段，公开字段不能证明位姿未变，禁止跨候选文件复用缓存。
选择器接收公开列表深拷贝；媒体自动分配到 method/task/seed 子目录。

| 包装记录字段 | 含义 |
|---|---|
| `schema_version` | evaluation_trial_v0.1 |
| `evaluation_scope` | 当前固定 pilot |
| `controller_version / method_id / task_id / seed` | 试验身份 |
| `candidate_sha256` | 有序完整候选的规范化 JSON SHA-256，含位姿；不涵盖代码/杯子资产 |
| `status` | 下表五种状态 |
| `selected_id` | 合法选择时为 ID，其余 null |
| `selection` | 合法时含 ID、ranking、metadata、latency_s；非法/异常时含耗时与调试信息；未调用为 null |
| `filter` | 完整筛选副本 |
| `episode` | A 原始结果；未执行为 null |
| `error` | 无错误为 null，否则含 type / message |

| status | 物理执行 | 有效物理统计样本 |
|---|---|---|
| completed | 是 | 是；episode.success=false 也要保留 |
| no_feasible_candidate | 否 | 否，所有方法同 seed 一起跳过并报告覆盖率 |
| invalid_selection | 否 | 否，报告最终选择无效率 |
| selector_error | 否 | 否，报告选择器异常 |
| execution_error | 不一定完成 | 否，报告运行/协议异常，保留已得到的 episode |

无效输入、筛选不匹配或筛选本身异常向外抛出，C 在调度层记录并检查配置。
选择与执行的普通异常被包装记录；用户中断不捕获。
latency_s 是 selector 调用的墙钟时间，可能包括内部修复/缓存，不含仿真，复跑时不保证相等。
示例 CLI 退出码 0 表示完成或合法跳过，不表示物理成功，必须读取 JSON。

## 8. 当前成功定义与指标口径

程序运行两秒保持后，用**最后一帧**计算：

```text
held = finger_contacts > 0 AND final_mug_z > 0.69 m
success = held AND NOT collision
          AND position_error_m <= 0.04
          AND orientation_error_deg <= 15
```

尚未检查保持区间内每一步都在目标容差内。
当前杯子目标为 `[实际抬升后 x, 实际抬升后 y, 闭合后杯子 z+0.14]`；
方向为闭合后实际杯子方向左乘世界 Y 轴 +35°（倒水）或世界 Z 轴 −90°（交接）。
因此不同抓法的目标可能略有不同。正式评估要由 A/B 固定每个 seed 的共同目标并升级判定版本，不能把当前 pilot 宣称为已完成共同固定目标与全程保持判定的 benchmark。

| 建议 pilot 指标 | 口径 |
|---|---|
| 条件任务成功率 | completed 中 episode.success=true 的比例，列分母 |
| 可行 seed 覆盖率 | 至少一条候选可行的 seed / 计划 seed |
| 端到端成功覆盖率 | 成功包装试验 / 同方法计划可行试验；异常是未产生成功结果，不伪造成物理失败 |
| 碰撞标记比例 | completed 中 collision=true；另外核查误执行 infeasible 的情况 |
| 报告掉落/保持标记比例 | completed 的 dropped/grasp_maintained 比例，说明第 5 节限制 |
| 位置/角度误差 | 仅统计非 null 值，报告有效 n；不补零 |
| 选择异常率 | 分别统计 invalid_selection、selector_error、LLM 修复与回退 |

配对比较使用同 task/seed/候选哈希/控制器及环境的共同有效样本，报告排除数和原因；
不能为不同方法挑选不同 seed。正式分母与异常处理需全组提前冻结。
可接受抓取集合由 B 独立冻结，A 不返回任务标签。
LLM 有效性、一致性、修复与回退率来自 C 的原始调用记录，无法从 IK 或 EpisodeResult 推断。

## 9. 可复制的接入示例

下面可直接运行 SMOKE 接口检查，C 将 methods 换成实际选择器；每个 seed 只筛选一次。

```python
import json
from pathlib import Path
from grasp_a.contracts import TASKS, load_candidates
from grasp_a.simulation import get_feasible_candidates
from examples.evaluate_one_trial import evaluate_trial, smoke_selector

candidates = load_candidates("candidates.example.json")
methods = {"SMOKE": smoke_selector}  # 换成真实 M0/M1/M2/M3/A1 函数
out = Path("results/local/evaluation")
out.mkdir(parents=True, exist_ok=True)
for seed in [0]:
    filtered = get_feasible_candidates(seed, candidates)
    for task_id in TASKS:
        for method_id, selector in methods.items():
            trial = evaluate_trial(task_id, seed, method_id, candidates, selector,
                                   filtered=filtered)
            path = out / f"{method_id}_{task_id}_seed{seed}.json"
            path.write_text(json.dumps(trial, ensure_ascii=False, indent=2,
                                       allow_nan=False), encoding="utf-8")
```

无需修改代码的单次检查：

```bat
conda activate grasp-a
python -m examples.evaluate_one_trial --task pour_prep --seed 0 --out results\local\interface_smoke.json
python -m unittest discover -s tests -v
```

真实包装样本：[pour_seed0.json](../results/interface_v02_smoke/pour_seed0.json)。
A 原始日志：[倒水](../results/ik_fix_20261015/pour_G02_seed0.json)、
[抬升回归](../results/ik_fix_20261015/handover_G02_seed1.json)、
[40 次 IK 回归](../results/ik_fix_20261015/summary.json)。
SMOKE、IK 回归与正式方法对比数据要分开，重复验证同一抓取不能当成不同方法结果。

## 10. 批量评估前的交接清单

全组确认候选文件/哈希、部位和方向词表、geometry_score、独立可接受集合；
controller_version、Git commit、杯子资产、完整 Conda 包版本及 pilot/正式 seed 划分；
任务目标与成功语义是否仍是第 8 节 pilot；
每个方法输出协议、LLM 模型/提示词/生成参数、一次修复和 M1 回退日志；
空列表、非法 ID、异常与 null 误差的统计口径。
每次至少保存完整 filter、selection、episode 和运行清单，避免只存成功率或 ID。

本机修复时实测环境为 Python 3.11.16、PyBullet 3.2.5、NumPy 2.4.6、
Pillow 12.3.0、imageio 2.38.0、imageio-ffmpeg 0.6.0。
`environment.yml` 尚未锁定全部包版本，而 pip 依赖要求 PyBullet >=3.2.6，
因此不同安装方式未必得到相同环境，不能承诺跨环境逐字段一致。正式运行前记录
`conda list --explicit` 和 `git rev-parse HEAD`，再冻结全组使用的版本。
