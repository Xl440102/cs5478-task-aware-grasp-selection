# A 部分完整任务定义与接入

本扩展把“杯子到达准备位姿”与“完成后续物理任务”分开。新增搬运放置、夹爪交接和液体倒水三个完整小序列；成功必须同时满足动作完成、阶段结果、无掉落、无非法接触和最终实际任务结果。三个任务使用同一候选格式，但不能与旧准备任务的成功率混算。

## 任务和场景

| 任务 ID | 初始状态和 A 点 | B 点与最终目标 |
| --- | --- | --- |
| `transport_place` | 空的带把手开口杯，桌面标称中心 A = (0.55, 0, 0.645) m | 桌面 B = (0.55, 0.16, 0.645) m；杯子直立，已松手，桌面独立承重，机械臂撤离 |
| `handover_transfer` | 同上，空杯 | 交接中心 (0.55, 0.16, 0.80) m；接收夹爪抓住把手，供体松手撤离，接收夹爪独立抬升 0.05 m |
| `pour_water` | 开口源杯内 0.100 kg 数值液体，密度参数 1000 kg/m³ | 另一只开口接水杯位于桌面 B；源杯在 B 附近抬高、倾倒、回正，最终目标杯内留存 ≥90%，两杯外液体 ≤5% |

坐标统一为世界系：米、秒、千克；四元数为 `(x,y,z,w)`。桌面高 0.60 m。源杯外半径 35 mm、解析液体内半径 32 mm、高 90 mm、底厚 3 mm，杯子质量 0.28 kg；接水杯内半径 55 mm、外半径 58 mm、高 90 mm、质量 0.30 kg。刚体壁使用多段薄盒近似圆环，顶部没有封口。

机器定义中的 `point_B` 是统一的桌面区域锚点；交接实际工作高度见 `handoff_pose`，倒水源杯工作中心见 `pouring_pose`，不要把桌面锚点当成交接末端高度。

A 的真实初始 x/y 各有 ±8 mm 扰动，偏航有 ±0.08 rad 扰动，seed 固定场景。控制器默认仅获得标称 A 位姿，可替换为外部初始位姿估计；不会读真实杯子位姿修正抓取。B 是公开标定的固定工作区域，不随成功结果调整。`SequenceConfig.target_xy` 可以共同改动三个任务的 B 坐标，比较不同方法时必须冻结配置。

交接接收端是有 Y 接近滑轨、Z 抬升滑轨和上下双指的简化物理夹爪，不是人手或完整第二机械臂。上下指用夹爪内部的齿轮约束同步；杯子没有与任何机器人绑定。指尖通过实际接触与摩擦转移支撑。

本轮保持已知、配合式接收器这一受控实验条件；不包含随机接取时机、未知人手运动或基于传感反馈的释放控制，不能据此宣称已完成真实人机交接。

## 阶段推进规则

每一步只有前一步**控制动作完成**才开始；评分失败不会触发隐藏重抓、重规划或提前结束倒水。下面的“阶段结果”用于独立评分，不是提供给控制器的真值反馈。IK、编码器到位、动作超时和总时限可以中止执行。

移动先执行预定轨迹，再用末端 FK 连续检查：位置误差 ≤20 mm、姿态误差 ≤10°，持续 0.10 s；额外等待最多 2 s，并受该阶段时限约束。仅执行完预定运动时间不算到位。IK 规划的末端残差另外限制在 8 mm / 3°。末端单个 primitive 平移 ≤0.25 m，长移动由执行器分段；每次动作 ≤2 s。夹爪采用限力电机，不传送或刚性附着物体。

三任务均先经过以下步骤。超时单位为模拟秒，所有阶段另受整段 25 s 上限限制。

| 顺序与阶段 | 入口 | 动作与控制退出条件 | 阶段结果 | 时限 |
| --- | --- | --- | --- | --- |
| 1 `pregrasp` | 全新场景，选中候选通过初始可行性检查 | 张开供体夹爪到 80 mm；到估计 pregrasp，连续 FK 到位 | 无非法碰撞 | 3 s |
| 2 `approach` | pregrasp 完成 | 沿分段轨迹到候选 grasp，连续 FK 到位 | 允许正常手指接触杯子，不允许非手指撞杯 | 3 s |
| 3 `close` | approach 完成 | 以每指 35 N 上限向闭合位置驱动，固定 0.75 s | 供体手指与杯子形成接触 | 1 s |
| 4 `lift` | close 动作完成 | 根据末端 FK 向上抬 0.14 m，连续 FK 到位 | 实际杯子中心 ≥0.735 m，且有供体手指接触 | 4 s |
| 5 `transport` | lift 控制完成 | 将标称杯子移到任务工作位姿，连续 FK 到位 | 实际杯子距工作位姿 ≤30 mm，直立误差 ≤5° | 4 s |

`transport` 目标：放置和交接为 `(Bx, By, carry_height)`；倒水为 `(Bx, By + sign(angle)×0.045, carry_height)`。默认 `carry_height=0.80 m`、`angle=-110°`，所以源杯中心位于接水杯的 −Y 侧 45 mm，绕世界 X 负向倾倒时杯口朝 +Y。上述目标通过候选局部抓取变换换算成末端目标，不使用抓取后的实际物体变换。

### 搬运放置的后续步骤

| 阶段 | 入口 | 动作与退出 | 阶段结果 | 时限 |
| --- | --- | --- | --- | --- |
| `lower` | transport 完成 | 标称杯子中心降低到 `(Bx,By,0.642)`，连续 FK 到位；比理想桌面中心低 3 mm，靠接触与限力电机形成支撑 | 真实杯底接触桌面 | 4 s |
| `release` | lower 完成 | 供体张开到 80 mm，固定 0.5 s | 桌面接触维持 | 1 s |
| `retreat` | release 完成 | 沿原接近方向反向移动 0.10 m，同时上抬 0.16 m；连续 FK 到位 | 供体与杯子分离，所有供体连杆距杯子 ≥80 mm | 4 s |
| `settle` | retreat 完成 | 保持全部最终电机目标，固定 1 s | 持续监测掉落和非法接触，不提前计稳定窗口 | 1 s |
| `terminal` | settle 完成 | 固定观察 1 s | 每个物理步都满足下面的放置终态 | 1 s |

放置终态：实际杯子 XY 距 B ≤20 mm，中心高度距 0.645 m ≤10 mm，直立误差 ≤5°，线速度 ≤0.02 m/s，角速度 ≤5°/s；桌面有受力接触，供体无受力接触且最小间距 ≥80 mm。只在 B 上方悬停不成功。

### 杯子交接的后续步骤

| 阶段 | 入口 | 动作与退出 | 阶段结果 | 时限 |
| --- | --- | --- | --- | --- |
| `receiver_approach` | transport 完成 | 接收夹爪张开，上下指总开口约 80 mm；Y 滑轨从 +120 mm 移至 0，误差 <2 mm 持续 0.10 s | 无接收端与供体碰撞，无掌部撞杯 | 2 s |
| `receiver_close` | receiver_approach 完成 | 两个接收手指以每指 15 N 上限闭合，固定 0.75 s | 至少一指已接触把手；尚不据此宣布支撑转移成功 | 1 s |
| `donor_release` | receiver_close 动作完成 | 供体张开，固定 0.5 s | 接收端两指都与杯子有受力接触 | 1 s |
| `retreat` | donor_release 完成 | 同放置的反向 0.10 m + 上抬 0.16 m，连续 FK 到位 | 供体分离，间距 ≥80 mm | 4 s |
| `receiver_lift` | retreat 完成 | 接收端 Z 滑轨上升 50 mm，编码器误差 <2 mm 持续 0.10 s | 杯子实际比 retreat 结束时上升 ≥35 mm，两指仍接触 | 2 s |
| `settle` | receiver_lift 完成 | 保持最终电机目标，固定 1 s | 监测掉落与非法接触 | 1 s |
| `terminal` | settle 完成 | 固定观察 1 s | 每步都满足交接终态 | 1 s |

交接终态：杯子 XY 距 B ≤20 mm，中心高度 ≥`carry_height + receiver_lift −0.015`（默认 0.835 m），直立和速度满足放置同样阈值；接收两指均有受力接触，供体无受力接触且间距 ≥80 mm，杯子不接触桌面。接收指的源杯接触点必须位于杯子局部 +Y 把手区域，而不是暗中夹杯身。杯子仍由供体托着、落到桌面、只碰了一下接收端都不成功。

### 液体倒水的后续步骤

| 阶段 | 入口 | 动作与退出 | 阶段结果 | 时限 |
| --- | --- | --- | --- | --- |
| `tilt` | transport 完成 | 绕源杯标称中心、世界 X 轴转至 −110°，2 s 轨迹后连续 FK 到位 | 无掉杯或源杯撞目标杯；液体靠流体物理从开口流出 | 5 s |
| `pour_dwell` | tilt 完成 | 固定保持倾角 2 s，不读取液体质量决定何时停止 | 源杯保持真实抓持，目标杯不被机械臂碰撞 | 2 s |
| `return_upright` | pour_dwell 完成 | 2 s 轨迹回到同一工作中心和直立姿态，再连续 FK 到位 | 无掉落、碰撞 | 5 s |
| `settle` | return_upright 完成 | 保持最终电机目标，固定 1 s | 继续物理仿真和接触监测，允许液体自由落下、回落 | 1 s |
| `terminal` | settle 完成 | 固定观察 1 s | 每步都满足液体、源杯和接水杯终态 | 1 s |

液体终态同时要求：

- `transfer_ratio = m_target / m0 ≥0.90`。只有当前实际处在目标杯腔内、低于杯口的液体才计入；从上方经过或曾经入杯又弹出不计最终留存。
- `spill_ratio = m_outside / m0 ≤0.05`。源杯和目标杯之外，包括仍在空中、桌面和地面的液体，都保守计入 outside。全程不删除液体、不往目标杯补液。
- 源杯仍由供体抓持，回到规定工作中心 30 mm 内，直立误差 ≤5°，线速度 ≤0.02 m/s，角速度 ≤5°/s。
- 接水杯直立和速度也满足同样阈值，距初始标定中心的位移 ≤10 mm；不能靠把接水杯撞到水流下方成功。
- 液体状态有限，质量账误差 <1e−9 kg，整段已评分过程最大 SPH 密度压缩误差 ≤10%。数值无效与物理任务失败分开记录。

默认 100 g 水对应至少 90 g 留在接水杯、最多 5 g 在两杯外；剩余部分可以仍留在源杯。只是不漏水但一滴没倒出，依然失败。倒出去了但没进接水杯，也失败。

## 接触和掉落

每个 1/240 s 物理步检查接触和终态条件；轨迹常规保存 10 Hz，最终窗口每步保存。抬起后无任何供体手指、接收手指或桌面支撑接触持续超过 0.10 s，锁存为掉落失败。接触需有非零法向力，不能以一个几何近邻点当作支撑。

杯子与桌面只在初始接近/闭爪、尚未真正抬离桌面的 lift 前段，以及放置的 lower/release/retreat/settle/terminal 阶段允许。交接后杯子落到桌面不能冒充接收成功。供体手指与杯子正常接触允许，非手指撞杯不允许；接收掌部撞杯、供体与接收端接触、机器人撞接水杯、源杯撞接水杯、杯子碰地面均失败。非预期机器人自接触也失败，URDF 中同一固定刚性组件和相邻关节的设计接触排除。跨物体接触穿透 >1 mm 或法向力 >0.2 N 都会记录，避免把浅穿透的大力碰撞漏掉。液体接触容器和桌面是物理行为，液体撒漏单独按质量评分。

## 液体实现及适用范围

`grasp_a/fluid.py` 实现 Position Based Fluids：等质量流体计算样本通过 SPH 密度约束产生压力响应，并使用 XSPH 邻域黏性扩散。它们不是 PyBullet 刚体小球，没有逐粒碰撞形状、弹珠质量或刚体摩擦。杯子边界的动量交换以力和力矩反馈到真实刚体杯子。算法参考 [Macklin 与 Müller 的 Position Based Fluids 论文](https://mmacklin.com/pbf_sig_preprint.pdf)。

默认 256 个流体样本、每刚体步 2 个流体子步（480 Hz）、8 次密度约束迭代。开始执行前额外固定静置 0.5 s，要求初始杯外比例 ≤1%、压缩误差 ≤10%；此初始化单独报告，不计 25 s 动作预算。流体样本数为 `fluid_particles`，这个字段指液体的数值离散分辨率，不是颗粒物替代水。

这是低分辨率的数值液体，不是经过实验标定的高精度水 CFD：圆柱解析边界不含把手的流固作用，未标定表面张力、润湿和真实水温对应的黏度，未求解空气相和湍流；自由表面密度与小液滴行为存在分辨率误差。视频里的蓝色球面仅用于显示流体样本，没有碰撞或动力学，不是被操纵的刚体小球。256 样本时每个样本约占 0.391 g，质量比例以约 0.391% 为最小步长。正式物理结论还需要分辨率/步长敏感性检查和真实液体验证，不应由本轮三个 seed 推断真实倒水成功率。

流体单独测试使用相同的预定倾倒轨迹、固定容器运动，隔离机器人影响：128 / 256 / 512 样本的最终目标留存分别为 88.28% / 95.31% / 96.68%；256 样本将流体步长从 1/480 s 减半到 1/960 s 时，此测试的最终留存仍为 95.31%。四项静置检查都保留全部初始液体，质量账误差均 <1e−9 kg，最大压缩误差均 <10%。这说明低分辨率会改变质量结果，不能将默认 256 样本当作已经证明网格收敛；这些是流体数值测试，不是机器人任务成功率。可用 `python scripts/validate_liquid.py` 复现。

## 安装与运行

沿用仓库 `grasp-a` Conda 环境，NumPy 提供 CPU 流体求解，PyBullet 提供机器人和刚体物理；本扩展没有新增 CUDA、外部液体服务或付费依赖。安装与激活：

```bat
conda env update -f environment.yml
conda activate grasp-a
```

在项目根目录运行：

```bat
python -m grasp_a task-spec --task pour_water --out results\local\pour_spec.json
python -m grasp_a sequence-filter --task pour_water --seed 0 --out results\local\pour_filter.json
python -m grasp_a sequence --task transport_place --seed 0 --id G02 --visualize --out results\local\place.json
python -m grasp_a sequence --task handover_transfer --seed 0 --id G02 --visualize --out results\local\handover.json
python -m grasp_a sequence --task pour_water --seed 0 --id G02 --visualize --out results\local\water.json
```

JSON 的 `media.video` 指向 MP4，默认录像目录为 `results/local/sequences/media`。液体计算比空杯慢，CPU 上可能需要数分钟；25 s 指模拟时间，不是程序墙钟时间。`--config` 可读取只含 `SequenceConfig` 覆盖字段的 JSON。修改阈值或分辨率必须在方法比较前共同冻结，不要为了通过某条结果而改评分门槛。

录像默认带 A/B 标记，`--landmarks / --no-landmarks` 切换显示，Python 对应 `show_landmarks=True / False`。新视频分别带 `_ab / _plain` 后缀，可以放在同一媒体目录。两套已录制视频见 [演示索引](../results/sequence_v1_20261017/README.md)，详细函数签名与输出字段见 [完整任务接口](SEQUENCE_INTERFACE.md)。

## B 和 C 的接口

候选仍为 `Candidate`，抓取/预抓取仍在杯子局部坐标系，公开选择元数据仍只有 `id / region / approach / gripper_width / geometry_score`。A 不生成正确答案标签，也不把物体真值、液体质量或接触数送给选择器。

```python
from grasp_a.contracts import load_candidates
from grasp_a.sequence import get_sequence_candidates, run_sequence
from grasp_a.sequence_contracts import SequenceConfig, task_spec

candidates = load_candidates("candidates.example.json")
config = SequenceConfig()
filtered = get_sequence_candidates("pour_water", 0, candidates, config=config)
public_candidates = filtered["feasible_candidates"]
# B/C 根据公开任务说明和 public_candidates 选出已有 ID。
selected_id = "G02"  # 仅为例子；不能在正式评估里固定为已知成功 ID。
result = run_sequence("pour_water", 0, selected_id, candidates, config=config)
```

| 输出字段 | 含义 |
| --- | --- |
| `schema_version` | `sequence_filter_v1` 或 `sequence_episode_v1` |
| `controller_version` / `evaluation_version` | `staged_sequences_v1` / `physical_outcomes_v1` |
| `scene_version` | `hollow_cup_sequences_v1` |
| `candidate_sha256` | 完整有序候选含位姿的指纹，保持现有算法 |
| `task_spec_sha256` | 配置与逐阶段定义的指纹；改动作、阈值或流体分辨率就会变化 |
| `task_spec` | 完整机器可读定义，含场景目标、每步入口/动作/退出/评分/时限/允许接触 |
| `stages` | 每步控制是否完成、起止时间和只含控制反馈的动作记录；未执行的步骤也保留 |
| `stage_evaluation` | 独立评分证据、各步物理检查；不能转交选择器作为运行时提示 |
| `success_checks` / `terminal_checks` | 整段判据与全终态窗口逐项 AND 的检查结果 |
| `final_evidence` | 真值评分：支撑方、杯子状态、液体质量账等 |
| `liquid_model` / `liquid_initial_check` | 求解版本、分辨率、适用限制、初始化保水检查 |
| `trajectory` / `collision_events` | 诊断轨迹和带时间、阶段的碰撞事件 |

`status=completed` 表示控制序列完成，不代表任务成功；任务结果看 `success`。`rejected` 是初始抓取不可行，`execution_failed` 是 IK/到位/时间失败，`simulation_error` 是求解发散或液体初始化不合格，`simulation_invalid` 是整段流体密度有效性不合格。数值无效结果不能当作某个选择器的真实物理失败混进成功率，应单独报数量和原因。

C 现有 `grasp_c` 批量入口仍仅接受 `pour_prep / handover_prep`。接入新任务需要显式添加新的适配轨道、任务说明和结果字段映射；不要把新 ID 传入旧 `run_episode`，也不要悄悄放宽旧任务白名单。缓存至少绑定 task ID、seed、候选指纹、任务定义指纹、场景/控制/评分版本和观测配置。所有方法共享同一输入和动作序列，只允许选择已有候选 ID。`sequence-filter` 只检查初始抓取可行性，不保证后续任务成功，不能用执行结果过滤候选。

B 的杯身/把手候选可继续使用，但新空心模型需要重新筛选；旧实心模型的几何特征不能直接当作空心杯的接触力学真值。交接把手必须留给接收夹爪，是否需要新增把手抓取候选应作为选择空间设计讨论，而不是因为某个候选在演示中成功就给它任务标签。目前 G02 的重复运行是执行器 smoke test，不是任务感知选择优于几何基线的证据。

## 测试与结果

G02 在 seed 0–2 的本轮执行器回归：放置 3/3、交接 3/3、倒水 2/3。倒水三条最终留存分别为 98.44%、97.66%、93.75%，杯外分别为 1.56%、2.34%、6.25%；seed 2 因杯外比例超过 5% 而失败，原始失败日志保留。参考定义、逐项物理证据和数值检查见 [结果汇总](../results/sequence_v1_20261017/summary.json)。这些 seed 已用于开发，不能当作未见测试集。

```bat
python -m unittest discover -s tests -v
python scripts/validate_sequences.py --tasks transport_place handover_transfer pour_water --seeds 0 1 2 --out-dir results\local\sequence_validation
```

验证脚本逐条保存原始结果与汇总，保留失败样本；`--samples` 和 `--substeps` 可以用于液体数值敏感性检查。`scripts/probe_sequence_motion.py` 明确禁用液体，只供开发时排查转腕碰撞，不能当作倒水成功结果。

录像采用相机深度测试的液体显示，不往 PyBullet 世界中添加显示小球。默认配置 seed 0 的录制与不录制版本已检查过：最终物理证据和完整评分轨迹一致。
