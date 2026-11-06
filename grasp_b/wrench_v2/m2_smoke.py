"""Full-pool B-only dry-mug diagnostics: M1 vs M2 vs matched gravity ablation.

No A feasibility or physical execution. Finite 16/32/64 grids are NOT nested.
Exact LP memoization retains every sample and does not prune candidate IDs.
python -B -m grasp_b.wrench_v2.m2_smoke --output-dir NEW_DIRECTORY
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from time import perf_counter
import argparse
import gzip
import hashlib
import json
import platform

import numpy as np
import pybullet_data

from grasp_a.contracts import candidate_sha256
from grasp_b.models import read_model
from grasp_b.multifamily import VERSION as GENERATOR_VERSION, generate_multifamily, pruned_config
from .adapter import GeneratorInfo
from .geometry import ObjectGeometry, read_gripper_geometry
from .m1 import development_config, prepare_candidate, score_prepared
from .m2 import ExactWrenchCache, TrajectoryPolicy, score_trajectory, rank_m2_results
from .ranking import rank_results
from .motion_prediction import PredictedPose, MotionModel, predict_motion
from .dynamics import object_parameters_from_snapshot, predict_dynamics
from .physics_snapshot import static_physics_snapshot
from .tasks import build_task_wrench_set, axis_disturbances, pour_orientations_x, RigidPayload, combine_payload
from .contacts import _plain


def statistics(values):
    return None if not values else dict(min=float(min(values)), median=float(np.median(values)),
        p95=float(np.percentile(values, 95)), max=float(max(values)))


def dry_m1_sets(body, config):
    ds = axis_disturbances(horizontal_force_n=.1, vertical_force_n=0, application_point_m=(0, 0, 0),
                           source="Task06 explicit M1 static diagnostic +/-0.1 N; M2 has gravity/inertia only")
    result = {}
    for task in ("transport_place", "handover_transfer", "pour_water"):
        options = {} if task != "pour_water" else dict(orientations=pour_orientations_x(
            (0, -55*np.pi/180, -110*np.pi/180), source="A final -110 degrees; finite M1 static attitude fixture"))
        result[task] = build_task_wrench_set(task, body, reference_point_m=config.reference_point_m,
            gravity_world_m_s2=(0, 0, -9.81), context_source="Task06 DRY mug, runtime inertia, shared physical body", disturbances=ds, **options)
    return result


def run_smoke(output, resolutions=(16, 32, 64)):
    if output.exists():
        raise FileExistsError("refusing existing results directory")
    if not resolutions or len(set(resolutions)) != len(resolutions) or any(type(n) is not int or n < 3 for n in resolutions):
        raise ValueError("unique sample counts >=3 required")
    output.mkdir(parents=True, exist_ok=False)
    began = perf_counter()
    root = Path(__file__).resolve().parents[2]
    snapshot = static_physics_snapshot(root)
    body = object_parameters_from_snapshot(snapshot, asset_path=root/"assets/mug_hollow.urdf")
    panda = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"
    model = read_model("hollow", panda)
    geometry, gripper = ObjectGeometry.from_model(model), read_gripper_geometry(panda)
    candidates, records = generate_multifamily(model, pruned_config())
    info = {r["id"]: GeneratorInfo("grasp_b.multifamily", GENERATOR_VERSION, r["family"], r["id"]) for r in records}
    config, policy = development_config(), TrajectoryPolicy()
    initial = PredictedPose((.55, 0, .645), (0, 0, 0, 1))
    sets = dry_m1_sets(body, config)
    groups = defaultdict(list)
    metrics = defaultdict(lambda: defaultdict(list))
    prepared_timings = defaultdict(list)
    coverage = Counter()
    sample_counts = defaultdict(Counter)
    unknown_water = []
    with gzip.open(output/"candidate_diagnostics.jsonl.gz", "xt", compresslevel=3) as stream:
        for index, candidate in enumerate(candidates):
            p = prepare_candidate(candidate, geometry, gripper, generator_info=info[candidate.id], config=config)
            coverage[p.spec.status.value] += 1
            for key, value in p.timings_s.items():
                prepared_timings[key].append(value)
            cache = ExactWrenchCache()
            details = dict(candidate=asdict(candidate), contact_spec=p.spec.to_dict(), M1={}, trajectories={})
            for task, ts in sets.items():
                r = score_prepared(p, ts)
                proxy = SimpleNamespace(candidate_id=r.candidate_id, task_name=r.task_name, method_id="m1_gws_v2", status=r.status,
                    worst_utilization=r.worst_utilization, score=r.score, family=r.family, provenance=r.provenance)
                groups[f"M1/{task}"].append(proxy)
                details["M1"][task] = r.to_dict()
            for n in resolutions:
                for task in sets:
                    key = f"{n}/{task}"
                    t = perf_counter()
                    motion = predict_motion(candidate, task, initial_object_pose=initial, estimate_source="Task06 explicit nominal pose prior; uncalibrated, not scene truth",
                                            model=MotionModel(samples_per_stage=n))
                    metrics[key]["motion_s"].append(perf_counter()-t)
                    t = perf_counter()
                    dynamic = predict_dynamics(motion, body, reference_point_m=config.reference_point_m,
                        gravity_world_m_s2=(0, 0, -9.81), source="Task06 dry rigid body, no fluid inertia assumption")
                    metrics[key]["dynamics_s"].append(perf_counter()-t)
                    details["trajectories"][key] = dict(motion_hash=dynamic.provenance["motion_input_sha256"], model=_plain(motion.provenance), methods={})
                    for static in (False, True):
                        t = perf_counter()
                        r = score_trajectory(p, motion, dynamic, policy=policy, gravity_only=static, cache=cache)
                        group = f"{key}/{'matched_static' if static else 'M2'}"
                        metrics[group]["evaluation_s"].append(perf_counter()-t)
                        for field in ("solver_wall", "lp_calls", "cache_hits", "evaluated_samples", "required_samples"):
                            metrics[group][field].append(r.timings_s[field])
                        summary = r.summary()
                        sample_counts[group].update(x.status for x in r.samples)
                        groups[group].append(SimpleNamespace(**summary))
                        details["trajectories"][key]["methods"][r.method_id] = r.to_dict(include_catalog=False)
                    # One representative per family verifies unknown water does
                    # not become a complete dynamic Pour score. No new inertia.
                    if task == "pour_water" and n == resolutions[0] and p.status is None and p.spec.family.value not in {x["family"] for x in unknown_water}:
                        wet = combine_payload(body, RigidPayload(mass_kg=.1, com_m=(0, 0, 0), source="explicit unknown water-inertia control, no point-mass default"))
                        wd = predict_dynamics(motion, wet, reference_point_m=config.reference_point_m,
                            gravity_world_m_s2=(0, 0, -9.81), source="unknown rotational water inertia control")
                        wr = score_trajectory(p, motion, wd, cache=cache)
                        unknown_water.append(dict(candidate_id=candidate.id, family=p.spec.family.value, status=wr.status,
                            score=wr.score, original_missing_inertia=wr.provenance["original_missing_inertia_samples"]))
            details["solver_results"] = cache.catalog()
            stream.write(json.dumps(details, allow_nan=False, separators=(",", ":"))+"\n")
            if index % 10 == 0 or index+1 == len(candidates):
                print(f"completed {index+1}/{len(candidates)} candidates, all requested grids/tasks", flush=True)
    summary = dict(version="m2_smoke_dry_generator_diagnostic_v1", pool_scope="generator_pool_diagnostic",
        candidate_count=len(candidates), a_feasible_count=None, candidate_sha256=candidate_sha256(candidates),
        contact_status_counts=dict(coverage), body=_plain(body), physics_config=config.to_dict(), scope_policy=_plain(policy),
        payload_mode="DRY mug for all three compared methods; not A's filled pour",
        sampling_note="16/32/64 linspace grids are NOT nested; finite maxima need not be monotone",
        m1_tws={k: v.to_dict() for k, v in sets.items()}, unknown_water_controls=unknown_water,
        initial_pose_estimate=_plain(initial), snapshot=snapshot, environment=dict(python=platform.python_version(), **{k: version(k) for k in ("numpy", "scipy", "pybullet")}),
        source_sha256={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (root/"grasp_b/wrench_v2").glob("*.py")},
        preparation_s={k: dict(distribution=statistics(v), sum_s=sum(v)) for k, v in prepared_timings.items()}, groups={},
        motion_dynamics_metrics={key: {k: statistics(v) for k, v in m.items()} for key, m in metrics.items() if "motion_s" in m},
        physical_execution=False, execution_outcomes_used=False)
    ids = [c.id for c in candidates]
    for key, rows in groups.items():
        t = perf_counter()
        ranked = rank_results(rows, ids) if key.startswith("M1/") else rank_m2_results(rows, ids)
        wall = perf_counter()-t
        scored = [r for r in ranked["scores"] if r.status == "scored"]
        worst_stages = Counter()
        family = defaultdict(Counter)
        for r in rows:
            family[r.family][r.status] += 1
            if not key.startswith("M1/"):
                worst_stages.update(s["stage"] for s in r.worst_samples)
        if key.startswith("M1/"):
            top = [dict(candidate_id=r.candidate_id, utilization=r.worst_utilization, score=r.score, family=r.family) for r in scored[:5]]
        else:
            top = [dict(candidate_id=r.candidate_id, utilization=r.worst_utilization, score=r.score, family=r.family, worst_samples=r.worst_samples) for r in scored[:5]]
        summary["groups"][key] = dict(status_counts=dict(Counter(r.status for r in rows)), family_counts={k: dict(v) for k, v in family.items()},
            sample_status_counts=dict(sample_counts[key]),
            ranking=ranked["ranking"], selected_id=ranked["selected_id"], top5=top, worst_utilization=statistics([r.worst_utilization for r in scored]),
            worst_stage_counts=dict(worst_stages), ranking_assembly_s=wall,
            metrics={k: dict(distribution=statistics(v), sum=sum(v)) for k, v in metrics[key].items()},
            rows=[dict(candidate_id=r.candidate_id, status=r.status, utilization=r.worst_utilization, score=r.score,
                       worst_samples=[] if key.startswith("M1/") else r.worst_samples) for r in rows])
    summary["total_wall_s"] = perf_counter()-began
    with (output/"summary.json").open("x") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
    with (output/"candidates.input.json").open("x") as stream:
        json.dump([asdict(c) for c in candidates], stream, indent=2, allow_nan=False)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--samples", nargs="+", type=int, default=(16, 32, 64))
    args = parser.parse_args()
    result = run_smoke(args.output_dir, tuple(args.samples))
    print(json.dumps(dict(total_wall_s=result["total_wall_s"], groups={k: dict(status_counts=v["status_counts"], selected_id=v["selected_id"], worst_utilization=v["worst_utilization"]) for k, v in result["groups"].items()}), indent=2))


if __name__ == "__main__":
    main()
