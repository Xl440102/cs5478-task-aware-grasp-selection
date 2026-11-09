"""Static/numerical Task05 profiling; no LP, A scene, rollout or outcomes.

python -B -m grasp_b.wrench_v2.motion_diagnostics [--output-dir NEW_DIRECTORY]
One isolated no-step asset snapshot is used only to bind loaded inertia.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pybullet_data

from grasp_a.contracts import candidate_sha256
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.models import read_model
from grasp_b.multifamily import VERSION, generate_multifamily, pruned_config
from .motion_prediction import MotionModel, PredictedPose, predict_motion
from .dynamics import object_parameters_from_snapshot, predict_dynamics
from .physics_snapshot import static_physics_snapshot
from .tasks import RigidPayload, combine_payload
from .contacts import _plain


def milliseconds(values):
    return dict(median_ms=float(np.median(values))*1000, p95_ms=float(np.percentile(values, 95))*1000,
                sum_s=float(sum(values)))


def run_diagnostics():
    began = perf_counter()
    root = Path(__file__).resolve().parents[2]
    snapshot = static_physics_snapshot(root)
    dry = object_parameters_from_snapshot(snapshot, asset_path=root/"assets/mug_hollow.urdf")
    config = SequenceConfig()
    # Deliberately UNKNOWN water inertia: no point-mass/soft-finger defaults.
    water = RigidPayload(mass_kg=config.water_mass_kg, com_m=(0, 0, 0),
        source="A configured water mass; assumed rigid entrainment COM at origin, rotational inertia unknown")
    wet = combine_payload(dry, water)
    panda = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"
    candidates, records = generate_multifamily(read_model("hollow", panda), pruned_config())
    families = {r["id"]: r["family"] for r in records}
    initial = PredictedPose((.55, 0, .645), (0, 0, 0, 1))
    report = dict(model="Task05 analytical command-level diagnostics", pool_scope="generator_pool_not_A_feasible",
        candidate_count=len(candidates), candidate_sha256=candidate_sha256(candidates), generator_version=VERSION,
        initial_pose_estimate=asdict(initial), snapshot=snapshot, dry_body=_plain(dry), wet_body=_plain(wet),
        water_assumption=asdict(water), numerical_results={}, physical_rollout=False, lp_calls=0,
        source_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                       (root/"grasp_b/wrench_v2").glob("*.py")})
    representatives = {}
    for n in (16, 32, 64):
        by_task = {}
        for task in ("transport_place", "handover_transfer", "pour_water"):
            metrics, rows, family_metrics = defaultdict(list), [], defaultdict(list)
            for c in candidates:
                t = perf_counter()
                motion = predict_motion(c, task, initial_object_pose=initial,
                    estimate_source="explicit calibrated-prior nominal estimate, not scene truth",
                    model=MotionModel(samples_per_stage=n), sequence_config=config)
                metrics["motion_context_wall"].append(perf_counter()-t)
                for key in ("construction", "pose_sampling", "velocity_acceleration", "sample_records"):
                    metrics[key].append(motion.timings_s[key])
                t = perf_counter()
                dynamics = predict_dynamics(motion, wet if task == "pour_water" else dry,
                    reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81),
                    source="runtime dry inertia; explicit rigid payload approximation; no measured trajectory")
                metrics["dynamic_context_wall"].append(perf_counter()-t)
                metrics["dynamic_numerical"].append(dynamics.timings_s["numerical_calculation"])
                family_metrics[families[c.id]].append(metrics["motion_context_wall"][-1]+metrics["dynamic_context_wall"][-1])
                tilt = [s for s in motion.samples if s.stage == "tilt"]
                deviation = 0. if not tilt else max(float(np.linalg.norm(np.array(s.object_pose.position_m)-tilt[-1].object_pose.position_m)) for s in tilt)
                rows.append(dict(candidate_id=c.id, family=families[c.id], sample_count=len(motion.samples),
                    dynamic_states=dict(Counter(s.status for s in dynamics.samples)),
                    tilt_center_chord_deviation_m=deviation,
                    max_com_acceleration_m_s2=max(float(np.linalg.norm(s.com_acceleration_world_m_s2)) for s in dynamics.samples)))
                if n == 32 and (task, families[c.id]) not in representatives:
                    representatives[(task, families[c.id])] = dict(motion=motion.to_dict(), dynamics=dynamics.to_dict())
            by_task[task] = dict(metrics={k: milliseconds(v) for k, v in metrics.items()},
                family_combined_wall={k: milliseconds(v) for k, v in family_metrics.items()},
                dynamic_status_totals=dict(sum((Counter(r["dynamic_states"]) for r in rows), Counter())), candidates=rows)
            print(f"samples/stage={n} {task}: completed {len(candidates)} candidates", flush=True)
        report["numerical_results"][str(n)] = by_task
    report["representatives"] = {f"{task}/{family}": value for (task, family), value in representatives.items()}
    report["total_wall_s"] = perf_counter()-began
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is not None and args.output_dir.exists():
        raise FileExistsError("refusing to overwrite existing diagnostics")
    report = run_diagnostics()
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        with (args.output_dir/"motion_diagnostics.json").open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"total_wall_s": report["total_wall_s"], "candidate_count": report["candidate_count"],
        "results": {n: {task: dict(metrics=row["metrics"], dynamic_status_totals=row["dynamic_status_totals"])
                        for task, row in tasks.items()} for n, tasks in report["numerical_results"].items()}}, indent=2))


if __name__ == "__main__":
    main()
