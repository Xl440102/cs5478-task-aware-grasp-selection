# 完整任务演示与验证结果

三个任务均保留无 A/B 标记和有 A/B 标记的版本，均为 seed 0、G02 的成功演示。两套视频只改变显示；完整评分轨迹和最终物理证据逐条一致，视频文件指纹见 [清单](demo_manifest.json)。这是执行器演示，不是选择方法的正式对比。

## 演示视频

| 任务 | 无 A/B 标记 | 有 A/B 标记 |
| --- | --- | --- |
| 搬运放置 | [视频](videos/plain/transport_place_seed0_G02_plain.mp4) | [视频](videos/with_landmarks/transport_place_seed0_G02_ab.mp4) |
| 物理交接 | [视频](videos/plain/handover_transfer_seed0_G02_plain.mp4) | [视频](videos/with_landmarks/handover_transfer_seed0_G02_ab.mp4) |
| 液体倒水 | [视频](videos/plain/pour_water_seed0_G02_plain.mp4) | [视频](videos/with_landmarks/pour_water_seed0_G02_ab.mp4) |

“无标记”保留阶段／结果标题和液体显示，仅不显示 A/B 圈、标签、坐标和方向箭头。各视频同目录保存 `_scene.png`、`_final.png`。GitHub 如不内嵌播放，可下载 MP4 或克隆后打开；这六条视频已纳入 Git，不依赖本机 `results/local/`。

标记版中，A 为标称起点，B 为最终目标。交接蓝圈是 z=0.800 m 的交接位置，绿圈是抬升后 z=0.850 m 的最终位置；倒水 B 为接水杯，POUR POSE 为源杯工作中心。虚线箭头仅表示任务方向，不是规划路径。

## 物理回归

[summary.json](summary.json) 保存 G02、seed 0–2、三个任务的完整定义和精简物理证据：放置 3/3、交接 3/3、倒水 2/3。倒水 seed 2 杯外 6.25%，超过 5% 上限而失败；失败样本没有剔除。原始稠密轨迹保留在开发机 `results/local/sequence_v1/final/`，组员可用下面脚本重跑。

这些 seed 已用于开发；结果不是未见测试集成功率、LLM 优势或真实人机／水动力学验证。交接使用标准化配合接收夹爪；液体为低分辨率 PBF，适用限制见 [任务定义](../../docs/TASK_SEQUENCES.md)。

## 复现

```bat
conda activate grasp-a
python -m grasp_a sequence --task handover_transfer --seed 0 --id G02 --visualize --landmarks --media-dir results\local\demos --out results\local\handover_ab.json
python -m grasp_a sequence --task handover_transfer --seed 0 --id G02 --visualize --no-landmarks --media-dir results\local\demos --out results\local\handover_plain.json
python scripts/validate_sequences.py --seeds 0 1 2 --out-dir results\local\sequence_validation
python scripts/validate_liquid.py
```

新录像文件带 `_ab / _plain` 后缀，两种模式共用目录不会相互覆盖。运行参数、成功判据和 B/C 接入见 [接口说明](../../docs/SEQUENCE_INTERFACE.md)。
