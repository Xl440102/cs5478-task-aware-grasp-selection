# A 部分单次抓取视频 pilot

最短流程使用默认候选文件中的 G02 和已校准的固定待机位姿。
两者都属于仿真校准参数。
杯子模型是实心圆柱代理，视频展示的是 PyBullet 抓取与交接准备动作，不包含真人交接。

在 Miniforge Prompt 中：

```bat
conda activate grasp-a
cd /d D:\SEM1\CS5478\project\A\grasp_part_a
python -m grasp_a filter --seed 0 --out results\local\filter_seed0.json
python -m grasp_a demo --task handover_prep --seed 0 --visualize --media-dir results\local\visuals --out results\local\handover_seed0.json
```

第一条命令生成的 JSON 应显示 G02 可行、G01 因接近阶段撞桌被拒绝。第二条命令会选择
G02，在 `results\local\visuals\handover_prep_seed0_G02.mp4` 保存抓取视频，同时保存
初始场景和最终帧 PNG。结果 JSON 的 `success` 应为 `true`。需要看交互窗口时，
可改用 `python -m grasp_a run --task handover_prep --seed 0 --id G02 --gui --visualize --media-dir results\local\visuals --out results\local\G02_gui_seed0.json`。

本方案只验证一条候选、一个 seed 的完整流程。正式实验需要 B 提供约八条带
预先定义 `geometry_score` 的候选，并在 pilot 后冻结场景、任务目标和参数。
`candidates.pilot.json` 另存了一条 G03 杯身抓取用于校准，其
`geometry_score: 0.0` 只是接口占位值，不能用于正式 M1 排名。G03 在
`pour_prep` 下能完成抓取和抬升，但任务目标逆运动学求解失败。
