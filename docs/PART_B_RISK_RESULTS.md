# Part B M0/M1/M2 标准化实验

日期：2026-10-24。该报告是当前工作区代码的开发验证，不是论文正式结果，也没有使用 held-out seeds。算法与单位假设见 [Part B v2 定义](PART_B_RISK_V2.md)。忽略目录下的原始报告：`results/local/b_standardization/ranking_seed0_9/report.json`（50 个配对筛选/排序 trial）、`execution_seed0/report.json`（5 个 seed-0 任务执行 trial）、`pour_original/report.json`、`pour_adaptive_v3/report.json` 和 `sensitivity_v2.json`。

## 方法定义与协议

| 方法 | 本轮定义 | 比较定位 |
| --- | --- | --- |
| M0 | 在 A feasibility 返回的五字段记录中使用局部 seeded RNG 均匀随机选取；不计算或排序质量特征 | 正式最低基线 |
| M1 Legacy | 历史 `experimental_geometry_weighted_v0.1`：alignment、symmetry、COM-distance proxy 等权；读取原配置，不重新解释历史分数 | M1 主比较版本 |
| M1 Static v2 | 几何静态负载/接触余量风险的单调分数；不读取 task 或轨迹 | M1 内部改进/消融，不另编号 |
| M2 | 由显式任务上下文驱动的 transport、handover、pouring 风险规则 | Handcrafted physics-informed 启发式，不是学习模型或成功概率 |

每个 seed/task 内，四种方法共享相同资产 profile、原始 80 候选、A feasibility 结果、任务配置与零噪声观测；之后各自排名。两种 profile 分开：prep 使用 solid URDF，sequence 使用 hollow URDF。Seed 0–9 改变 A 场景的杯子 XY（各轴 ±8 mm）和世界 yaw（±0.08 rad），并同时作为 M0 随机种子；观测配置和 noise seed 固定。不同 seed 是不同物理初始位姿，不是对同一个确定性 episode 的重复。实际执行只跑每任务 seed 0；相同方法选中相同 ID 时复用同一 A episode，避免重复执行被算成独立样本。

## Candidate generation 与 A feasibility

Adaptive v2 使用 5 个由物体 body bounds 计算的高度、8 个 45° yaw、2 个水平夹爪 roll，生成 80 个稳定 ID。宽度为 `2r(1+0.10)`，高度避开上下边缘各 8 mm；pregrasp 距离修复为 `max(tool_offset, 3.5r)`。旧加法规则对该杯子给出 175 mm，现为 122.5 mm。Roll 0/180 改变 Panda 姿态，但在理想平行夹爪圆柱接触定义中不形成新 contact family；20 个 family 各含 4 个候选（两种 roll 与等价方位），因此 80 个 ID 不是 80 种接触。

| Profile / tasks | 生成 | 几何接触族 | A feasible | rate | generation（含模型解析） | filtering 平均 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hollow / 三个 sequence tasks | 80 | 20（4 个接触方向 × 5 高度）；4 family 有可行候选 | 6/80 | 7.5% | 4.95 ms | 1.49–1.56 s |
| Solid / 两个 prep tasks | 80 | 20；4 family 有可行候选 | 6/80 | 7.5% | 3.26 ms | 1.48 s |

修复前 adaptive distance 为 175 mm；`transport_place` seed 0 的 A feasibility 为 4/80（5%）。改成 122.5 mm 后同一场景为 6/80，增长来自 pregrasp IK/碰撞更容易通过，生成的候选数和接触家族数不变。10 个 seed 中，各 task 的可行数始终是 6；Sequence 三任务的可行 ID 集一致，Prep 两任务内部一致，但 solid 与 hollow 的 ID/可行集不同。Seed 0 的 sequence IDs 为 `B2_00168d…`、`B2_5c6dca…`、`B2_6f0aee…`、`B2_77b4b2…`、`B2_88f4b0…`、`B2_a378a9…`；Prep 另有 6 个对应 profile ID。

每 trial 74 个拒绝候选的 primary reason 完整分区：pregrasp IK/FK 45、home→pregrasp table 10、home→pregrasp self 8、approach 非手指撞杯 7、home→pregrasp self+table 2、approach table 2。A 同时返回的多标签接触/阶段诊断中还包括 grasp IK/FK 32 次、approach table 12 次、robot self 4 次及 self+table 6 次；多标签计数允许同一候选重复计数。由此可见主要瓶颈是预抓取 IK/FK 与机器人/桌面碰撞约束，不是 B 评分。杯柄抓取未生成；当前 4 个方向、20 family 的数目也暴露了接触多样性仍有限。接近距离的修复已提升覆盖率，但没有降低到可忽略失败过滤的程度。

## 五任务 × 10 seeds 配对排序

每个 task 的候选覆盖率为 10/10 trials，feasibility 为 6/80（7.5%）。Legacy 和 Static v2 在本候选集合里的排名完全一致，说明当前等价几何分数层级占主导；M2 与 M1 的分歧按任务而异。Pairwise agreement 是排名中所有候选对的 concordance（0–1），并非 tie-corrected 统计量。

| Task | M1 Legacy / Static v2 top | M2 top | M1–M2 pairwise agreement | top-1 一致 seeds | M2 raw risk 范围 | M2 排名平均耗时 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `transport_place` | center family，z=0 | 同 M1 | 1.000 | 10/10 | 0.341–0.596 | 30.7 ms |
| `handover_transfer` | center family，z=0 | 从 receiver 方向接近的 side family，z=+11.1 mm | 0.933 | 0/10 | 1.310–1.328 | 75.1 ms |
| `pour_water` | center family，z=0 | 同 M1 | 1.000 | 10/10 | 1.012–2.172 | 59.4 ms |
| `pour_prep` | center family，z=0 | 同 M1 | 0.933 | 10/10 | 0.955–1.823 | 39.7 ms |
| `handover_prep` | center family，z=0 | high family，z=+25.9 mm | 0.333 | 0/10 | 2.936–3.608 | 48.0 ms |

Legacy 几何分数跨任务为 0.808–1.000；Static v2 为 0.636–0.752。二者公式和校准不同，不可直接比较数值。M0 的 top-1 在每个 task 的十个 trial 中覆盖全部 6 个可行 ID；M0 的选择不看分数。M2 在 transport、pouring 的可行集合排序与 M1 相同，所以这组默认物理条件没有产生顶端排序差异；不能据此声称 M2 总优于 M1。Handover 的 top-1 差异来自接收可达空间、把手遮挡和释放/撤离方向；Prep handover 中没有真实 receiver，M2 使用明确标为 hypothetical 的 receiver envelope，其排名只用于假设分析。

实测 selector ranking 耗时：M0 约 0.05 ms、Legacy 约 0.29 ms、Static v2 约 0.90 ms；M2 的任务风险几何遍历额外约 30–75 ms/排名。A filtering 平均约 1.48–1.56 s，远高于 B ranking。Raw report 同时记录候选 ID、contact family、每分数分布、全排序、pairwise concordance 和过滤/排序时长。

## Seed-0 完整执行（每任务每方法 n=1）

| Task | M0 | M1 Legacy | M1 Static v2 | M2 |
| --- | --- | --- | --- | --- |
| `transport_place` | `B2_77b4…` 成功 | `B2_6f0a…` 失败：lower 阶段缺少 table support | 同 Legacy，episode 复用 | 同 Legacy，episode 复用 |
| `handover_transfer` | `B2_77b4…` 成功 | `B2_6f0a…` 成功 | 同 Legacy，episode 复用 | `B2_5c6d…` 成功 |
| `pour_water` | `B2_77b4…` 失败：`tilt_ik_failed` | `B2_6f0a…` 同原因 | 同 Legacy，episode 复用 | 同 Legacy，episode 复用 |
| `pour_prep` | `B2_3a3e…` 成功 | `B2_a4b5…` 成功 | 同 Legacy，episode 复用 | 同 Legacy，episode 复用 |
| `handover_prep` | `B2_3a3e…` 成功 | `B2_a4b5…` 失败：`target_pose_error` | 同 Legacy，episode 复用 | `B2_3a3e…` 成功 |

在此小样本中 M0 4/5、Legacy 2/5、Static v2 2/5、M2 3/5；每个方法每任务 coverage 为 1/1，所以本次端到端与 conditional rate 相等。该 n=1 结果只验证执行链条和暴露任务失败，不支持方法优劣推断。候选差异确实存在：例如 Handover M2 与 M1 选择不同 family 且都成功；`handover_prep` M2 选择高抓取位置，避开 Legacy/Static 的目标姿态失败。

## M2 敏感性与有物理解释的排序差异

所有 sweep 是显式假设参数送入 B 的离线 proxy，不会改变 A 中 URDF、摩擦或 executor。风险数值不是成功概率。

- **Transport：**速度通过估计加速度进入负载，速度本身不代替加速度。固定路径的时间缩放 0.5/1/2/4 时，所测候选风险为 0.333/0.341/0.373/0.502；加速度随时间平方倒数变化。质量 0.5/1/2 倍风险为 0.260/0.341/0.505；摩擦系数 μ=0.3/0.6/1.2 时风险为 0.505/0.341/0.260。COM x 偏移 ±25 mm 把该 sweep 的最低风险从 0.341 推至约 0.852；upright lift 中 COM z 平行重力时不改变风险。默认 6 个 A 可行候选中，改变为 COM z=+35 mm 且速度 6× 的反事实会使 Static v2 以 z=+11.1 mm 为首，而 M2 改为 z=+25.9 mm，来源是加速负载相对旋转轴的力矩变化；这个参数没有注入 A。
- **Handover：**相同 COM、不同 yaw 的两个候选 Static v2 同分（0.7517），M2 区分了 receiver sweep 与 handle access；姿态朝 receiver 的方案具有更大夹爪/把手遮挡和朝接收器撤离惩罚。10-seed 正式排序中 M2 与 M1 的顶端 10/10 不同。真实 `handover_transfer` receiver 来自 A 配置；`handover_prep` 没有 receiver，使用保守假想空间，结论不可外推到真实交接。
- **Pouring：**SequenceConfig 默认倾倒角 −110°，prep 角度为世界 Y +35°。准静态角度 sweep −80/−90/−110/−135° 的最大估计角加速度约 1.723/1.938/2.369/2.907 rad/s²，风险约 0.784/0.783/0.782/0.785，三种高度的排名未变；风险有变化但排名不变。固定假设液体质量 0→0.1→0.2 kg 会提高静态负载项；把 0.2 kg 点质量固定在杯局部 z=−25 mm 时，抓取 z=−20 mm 比 center 的风险更低（0.812 vs 1.110），但 rim-clearance proxy 更差，这些竞争项会影响排名。该模型把水视作固定 COM 的刚体质量，不模拟流出、液面、冲击或 slosh。
- `speed_m_s` 单独变化而 acceleration 不变时，力与力矩不变（有单元测试）；摩擦/质量/速度敏感性覆盖了“risk only”情况，COM + 高速度反事实及 handover yaw 覆盖“risk 与 ranking 同时变化”。这些离线变化没有启动 A executor，因而不能据此声称引发了执行失败。Seed-0 实际 `pour_water` 失败来自 A 的 tilt IK。

## Pouring IK 诊断

原 175 mm pregrasp 版本在 seed 0 有 4 个 initial-grasp feasible 候选，四个完整 sequence 都在 `tilt_ik_failed`。Tilt target 位置约为 x=0.5500 m、y=0.1166 或 0.1191 m、z=0.8083 或 0.8195 m；目标姿态相同倾倒角但受夹爪 roll 变换。IK local 解违反 `panda_joint2`、`panda_joint5` 限位；bounded 解的平移残差约 3.11–3.90 mm、姿态误差约 3.08–3.86°，超过 A 的 3°姿态容差。初始 grasp feasible 因此不等于 tilt reachable。

原 80 个候选中有 15 个通过离线 nominal carry→tilt kinematic path probe，但它不执行整条 sequence，也不验证闭环碰撞/动力学。pregrasp 改为 122.5 mm 后 initial feasible 增至 6；其间候选 `B2_5c6dca94e6db7609` 的完整 seed-0 `run_sequence('pour_water', ...)` 成功，包含 tilt、2 s dwell、return upright、settle 和 terminal；这证明并非所有新可行 grasp 都受同一 IK 限制。当前 M0/M1/M2 seed-0 top choice 仍都失败于 `tilt_ik_failed`，所以 B 的静态/规则风险尚未识别全序列 IK 可达性。建议 A 后续提供只读的逐候选 sequence-level planning/IK feasibility probe（至少报告每阶段 target pose、IK solution/limit/tolerance reason、collision result），作为 separate feasibility stage；不应把初始抓取检查冒充完整任务可行性。本轮没有改 A。

## A 运动数据来源

A 发布任务配置、阶段 endpoint、命令时长和实际执行后的状态/诊断；控制器以 1/240 s stepping，对 Cartesian waypoints 做 ≤15 mm/5° 插值、关节轨迹与到达修正。它没有向 B 暴露预选阶段逐候选时间化末端/关节轨迹、实际速度或加速度。`motion.command_context()` 因而用 A 命令 endpoint/time 做 half-cosine rest-to-rest 估计，标签为 `command_endpoint_half_cosine_estimate`，不是 PyBullet 实测轨迹。Prep 时长按 A 的步数/240Hz 换算；Sequence 阶段使用配置时长，速度/角速度/加速度都是估计值。A 执行后记录的真实观测只用于报告，不回流到排名。液体项仅是固定点质量的 quasi-static/rigid-entrainment proxy。

## 验证结果与限制

- B 专项：**52 passed**。覆盖 M0 seeded determinism、M1 legacy/static contracts、模型组合碰撞形状、生成器缩放与稳定 ID、M2 task-specific differences/敏感性、A feasibility 输入一致、C 五字段隔离、候选校验与报告序列化。
- 全套：**241 tests；237 passed、2 skipped、2 errors**。两项错误均为既有 C env 测试因环境未安装 `python-dotenv`（`ModuleNotFoundError: dotenv`），不在本轮范围；没有安装依赖或修改 C。
- `git diff --check` 通过。完整 10-seed 执行矩阵没有完成：它在无界执行模式耗时过长后被停止；本报告改用全部 50 个 paired ranking/filter trial 加每任务 seed 0 执行，不能把 5 次执行外推为 10-seed 成功率。

已经通过测试验证的是 B API/公式约束、候选稳定性和适配接口；模拟执行只支持上述 seed-0 episode 结果；M2 对质量、摩擦、液体与运动的推断仍属于有明确单位与假设的工程代理模型。当前主要待解问题是 M2/M1 只看到初始 A 可行集合、缺少完整序列可达性；Pouring 排名应先作为 risk heuristic 研究，不能称为倾倒成功率预测。

Baseline 文献对照、M1 特征区分度审计和 P0/P1 协议边界见 [算法说明的研究依据与诊断章节](PART_B_RISK_V2.md#literature-alignment-and-claims-boundary)。本轮按实际职责拆分了风险测试，未更改算法公式或历史结果；M1 Static v2 对当前 upright 可行候选的 load ratio 相同（0.1526），Legacy 与 Static 排名一致，说明本次排序主要区分高度相关几何项，详见算法说明。

## Sequence Execution Oracle（seed 0，事后评价）

新增 oracle 对每个 task 先只运行一次 A 初始候选筛选，得到同一组 6 个 feasible candidates；在任何执行前冻结 M0、M1 Legacy、M1 Static v2、M2 的完整排序。随后对每个 candidate 单独调用完整 `run_sequence()`，每次都以 seed 0、同一 `SequenceConfig`、同一零噪声 `ObservationConfig` 和同一原始候选列表重建 A 场景。单次超时 120 s；18/18 均完成，没有超时。Oracle 结果仅用于下表与 top-k 事后统计，未传回任何排序或风险值。完整逐阶段结果、IK 诊断和配置保存在忽略目录 `results/local/sequence_oracle_seed0/report.json`。

M1 分数越高越好，M2 raw risk 越低越好；排名 1 是首选。`M1 Legacy/Static` 列分别列两种 M1 排名，当前结果二者相同。`M0 rank` 是冻结的 seed-0 随机排列，不是质量排序。成功位置和失败阶段按 A 完整 sequence 结果记录。

### `transport_place`：5/6 完整成功

| Candidate ID | M0 rank | M1 Legacy / Static rank | M1 Legacy score | M2 rank / risk | Oracle execution |
| --- | ---: | ---: | ---: | ---: | --- |
| `B2_6f0aee2a7a935b67` | 4 | 1 / 1 | 1.0000 | 1 / 0.3411 | 失败：`lower`，table support 判定失败 |
| `B2_5c6dca94e6db7609` | 3 | 2 / 2 | 0.9178 | 2 / 0.3993 | 成功 |
| `B2_88f4b090e78630d0` | 2 | 3 / 3 | 0.9178 | 3 / 0.4012 | 成功 |
| `B2_a378a97a3856d2d6` | 6 | 4 / 4 | 0.9178 | 4 / 0.4012 | 成功 |
| `B2_77b4b26e5b4a50a6` | 1 | 6 / 6 | 0.8081 | 6 / 0.5957 | 成功 |
| `B2_00168db48467b1f4` | 5 | 5 / 5 | 0.8081 | 5 / 0.5957 | 成功 |

### `handover_transfer`：6/6 完整成功

| Candidate ID | M0 rank | M1 Legacy / Static rank | M1 Legacy score | M2 rank / risk | Oracle execution |
| --- | ---: | ---: | ---: | ---: | --- |
| `B2_6f0aee2a7a935b67` | 4 | 1 / 1 | 1.0000 | 2 / 1.3147 | 成功 |
| `B2_5c6dca94e6db7609` | 3 | 2 / 2 | 0.9178 | 1 / 1.3096 | 成功 |
| `B2_88f4b090e78630d0` | 2 | 3 / 3 | 0.9178 | 3 / 1.3165 | 成功 |
| `B2_a378a97a3856d2d6` | 6 | 4 / 4 | 0.9178 | 4 / 1.3165 | 成功 |
| `B2_77b4b26e5b4a50a6` | 1 | 6 / 6 | 0.8081 | 6 / 1.3282 | 成功 |
| `B2_00168db48467b1f4` | 5 | 5 / 5 | 0.8081 | 5 / 1.3282 | 成功 |

### `pour_water`：1/6 完整成功

| Candidate ID | M0 rank | M1 Legacy / Static rank | M1 Legacy score | M2 rank / risk | Oracle execution |
| --- | ---: | ---: | ---: | ---: | --- |
| `B2_6f0aee2a7a935b67` | 4 | 1 / 1 | 1.0000 | 1 / 1.0117 | 失败：`tilt` / `tilt_ik_failed` |
| `B2_5c6dca94e6db7609` | 3 | 2 / 2 | 0.9178 | 2 / 1.1238 | **成功**：完整 tilt、dwell、return、settle、terminal |
| `B2_88f4b090e78630d0` | 2 | 3 / 3 | 0.9178 | 3 / 1.7099 | 失败：`tilt` / `tilt_ik_failed` |
| `B2_a378a97a3856d2d6` | 6 | 4 / 4 | 0.9178 | 4 / 1.7099 | 失败：`tilt` / `tilt_ik_failed` |
| `B2_77b4b26e5b4a50a6` | 1 | 6 / 6 | 0.8081 | 6 / 2.1719 | 失败：`tilt` / `tilt_ik_failed` |
| `B2_00168db48467b1f4` | 5 | 5 / 5 | 0.8081 | 5 / 2.1719 | 失败：`tilt` / `tilt_ik_failed` |

### 排名与实际成功的一致性

| Task / ranker | Top-1 execution success | 成功候选排名 | Top-k 成功候选覆盖率（k=1 / 2 / 3 / 5 / 6） |
| --- | --- | --- | --- |
| transport / M0 | 是 | 1、2、3、5、6 | 20% / 40% / 60% / 80% / 100% |
| transport / M1 Legacy、Static、M2 | 否 | 2、3、4、5、6 | 0% / 20% / 40% / 80% / 100% |
| handover / 四种排序 | 是 | 全部 1–6 成功 | 17% / 33% / 50% / 83% / 100% |
| pouring / M0 | 否 | 3 | 0% / 0% / 100% / 100% / 100% |
| pouring / M1 Legacy、Static、M2 | 否 | 2 | 0% / 100% / 100% / 100% / 100% |

Top-k 覆盖率定义为“成功候选中排名不超过 k 的比例”；不是成功概率。M1 Legacy 与 Static v2 完全同序。`pour_water` 的成功候选在 M1/M2 均排第 2；M2 首选候选的 proxy risk 更低（1.0117 对 1.1238），但它 tilt IK 失败。这是风险 proxy 与完整序列 IK 可达性不一致的直接证据，不应用 oracle outcome 调整分数。候选 `B2_5c6dca94e6db7609` 在此前的单候选完整执行 probe 和本次 oracle 均以 seed 0 成功（2/2 完整运行），结果可复现。

本 oracle 对照建议将当前瓶颈判为**缺少完整任务序列可行性信息**：三个任务的 6 个初始可行集合均包含完整执行成功候选；`handover_transfer` 全部成功，`transport_place` 仅一项 placement 支撑失败，`pour_water` 则只有 1/6 能完成 tilt。当前 M2/M1 只接收初始 feasibility 子集和估计轨迹风险，不能识别 IK 目标是否可达。物理风险排名也存在校准目标不匹配（例如 Transport top-1 在 lower 阶段失败），但本轮不改评分公式。候选覆盖仍有限，尤其 Pouring 成功候选仅占初始可行集合 1/6；不过现有集合已覆盖该成功姿态，因此本次证据不支持把“完全没有候选”判为首要原因。

## M2 v2 风险一致性复核

上面的 seed-0 Oracle 表和结果保留为 `task_trajectory_risk_v1` 历史结果。后续风险版本 `task_trajectory_risk_v2` 仅把已有的静态接触边界余量项并入 Handover 与 Pouring 的抓持稳定风险，并增加单位载荷余量诊断和非有限数值保护；没有改 M0、M1 Legacy、M1 Static v2，也没有根据 Oracle 调权。详细公式与假设见 [M2 v2 算法定义](PART_B_RISK_V2.md)。

对同一 seed-0 hollow 资产、80 个原始候选、6 个 A 初始可行候选、同一配置与观测条件重新运行了不执行的配对排名。M0、M1 Legacy、M1 Static v2、M2 仍共用同一个可行集合。M2 与 M1 在 transport、handover、pouring 三个默认 sequence 场景的全排序一致（pairwise concordance 均为 1.0）；v2 改变了 Handover/Pouring 风险分解与数值，但没有改变这三个场景的默认选择。每次生成约 5.65 ms、A filtering 约 1.51–1.61 s，M2 排序约 30.4 ms（transport）、75.4 ms（handover）、59.7 ms（pouring）。这说明当前变化让静态边缘稳定性在默认候选中压过了任务几何的细微差异；不能把它报告成任务选择性能提升。单元测试另以等静态分数的候选验证了 receiver access 与 pouring clearance 确实进入各自风险项并可改变特定对照候选排序。

敏感性报告 `results/local/taskaware_final_refinement/sensitivity_v2.json` 是离线代理计算，不含 A 执行。该版本修正了此前扫描脚本把液体质量施加到搬运/交接任务的问题：液体质量轴现在只对 `pour_water` 生成三行；其他质量、COM、速度、摩擦参数按三类 sequence task 扫描，总计 51 行。液体仍按声明的固定杯局部 COM 点质量处理，不模拟流动或晃动。对照结果没有改变先前的 seed-0 Oracle：Transport 5/6 成功、Handover 6/6、Pouring 1/6；M2 top-1 仍是 Transport 的 lower support 失败候选，以及 Pouring 的 `tilt_ik_failed` 候选。原 Pouring 唯一成功候选 `B2_5c6dca94e6db7609` 仍非首选，且没有用于公式调整。Handover v2 首选 `B2_6f0aee2a7a935b67` 在既有完整 Oracle 中已成功；这属于既有 episode 证据，不是新执行重复。

本轮回归：Part B **57/57 passed**；A/B 与 B/C 定向接口 **14/14 passed**；全套 **246 项，242 passed、2 skipped、2 errors**。两项错误是 `grasp_c` 环境测试无法导入未安装的 `python-dotenv`，不涉及 B 算法。`git diff --check` 通过。没有重跑完整 sequence Oracle 矩阵，因为排序更新后的 Handover 首选已有相同 seed-0 完整执行成功记录，Transport 与 Pouring 首选没有改变。

## Baseline 与测试职责整理复验

本轮只重组测试职责和补充算法/文献说明；没有改 Legacy、Static v2 或 M2 的计算公式，旧方法版本与历史数据保持原样。测试共 52 项，拆分为 M0、M1 Legacy、M1 Static、M2 Transport/Handover/Pouring/Common、模型、运动和集成模块；断言均从旧混合风险测试原样迁移。M2 的三个任务 evaluator 仍由 `risk.py` 的显式分派调用，共用受力/力矩与几何 proxy 计算。

按原 seed-0 的物体、观测先验、任务配置和 A 可行列表，本轮重新运行三个 sequence task 的全部 18 个 `run_sequence()` episode。每候选设置 120 秒墙钟上限；初始集合均复现为 6/80，执行成功标志、失败阶段和原因逐项匹配 Oracle 存档（18/18）。`pour_water` 的唯一成功候选再次成功，4 个 tilt 失败仍为 `tilt_ik_failed`。这是固定场景的复现，不是新的独立样本或方法优劣统计。当前 A 返回的阶段结果不含 Oracle 存档格式的 `failure_stage` 字段；复验从 A 的阶段评估或 IK 失败原因归一化失败阶段后比较。

本轮测试入口：`test_b_m0_random.py`、`test_b_m1_geometry.py`、`test_b_m1_static.py`、`test_b_m2_transport.py`、`test_b_m2_handover.py`、`test_b_m2_pouring.py`、`test_b_m2_common.py`、`test_b_models.py`、`test_b_motion.py`、`test_b_integration.py`，以及既有 `test_b_features.py`、`test_b_generation.py`、`test_b_scoring.py`。旧混合 `test_b_risk.py` 已删除，避免重复发现和运行。

验证结果：Part B **52/52 passed**；A/B 与不依赖 dotenv 的 B/C 公共接口相关测试 **14/14 passed**；完整套件 **241 项：237 passed、2 skipped、2 errors**。两个 error 均来自当前环境未安装 `python-dotenv`，失败点在既有 C 本地 `.env` 加载测试（`ModuleNotFoundError: dotenv`），与 Part B 测试拆分无关；未修改 A/C，也未安装依赖。
