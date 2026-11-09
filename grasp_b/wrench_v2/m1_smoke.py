"""B-only generator-pool diagnostic, NO A filter/episodes/oracle lookup.

python -B -m grasp_b.wrench_v2.m1_smoke [--output-dir NEW_DIRECTORY]
Without an output directory this writes only stdout. Export refuses overwrite.
"""
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
from time import perf_counter

import numpy as np
import pybullet_data

from grasp_a.contracts import candidate_sha256
from grasp_b.models import read_model
from grasp_b.multifamily import VERSION, generate_multifamily, pruned_config
from .adapter import GeneratorInfo
from .geometry import ObjectGeometry, read_gripper_geometry
from .m1 import development_config, development_task_sets
from .ranking import rank_candidates
from .tasks import read_declared_object


def distribution(values):
    return None if not values else dict(min=float(min(values)), median=float(np.median(values)),
                                       p95=float(np.percentile(values, 95)), max=float(max(values)))


def run_smoke():
    start = perf_counter()
    root = Path(__file__).resolve().parents[2]
    panda = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"
    model = read_model("hollow", panda)
    geometry, gripper = ObjectGeometry.from_model(model), read_gripper_geometry(panda)
    candidates, records = generate_multifamily(model, pruned_config())
    config = development_config()
    task_start = perf_counter()
    task_sets = development_task_sets(read_declared_object(root/"assets/mug_hollow.urdf"), config)
    task_generation_s = perf_counter()-task_start
    report = rank_candidates(candidates, [c.id for c in candidates], object_geometry=geometry,
        gripper_geometry=gripper, generator_infos={r["id"]: GeneratorInfo("grasp_b.multifamily", VERSION, r["family"], r["id"]) for r in records},
        config=config, task_sets=task_sets, pool_scope="generator_pool_diagnostic")
    summary = dict(method_id=report["method_id"], pool_scope=report["pool_scope"],
        input_count=len(candidates), a_feasible_count=None, a_feasibility_executed=False,
        config=config.to_dict(), candidate_sha256=candidate_sha256(candidates), generator_version=VERSION,
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        python=sys.version.split()[0],
        source_sha256={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root/"grasp_b/wrench_v2").glob("*.py"))} |
            {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
             ("grasp_b/multifamily.py", "grasp_b/models.py", "grasp_a/contracts.py")},
        environment={key: version(key) for key in ("numpy", "scipy", "pybullet")},
        task_generation_s=task_generation_s, preparation_seconds=report["preparation_seconds"], tasks={})
    export = dict(manifest=summary, task_sets={name: ts.to_dict() for name, ts in task_sets.items()}, tasks={})
    for name, ranked in report["tasks"].items():
        rows = ranked["scores"]
        family = defaultdict(Counter)
        worst = Counter()
        rejected = Counter()
        for r in rows:
            family[r.family][r.status] += 1
            worst.update(r.worst_scenario_ids)
            rejected.update(sid for sid, lp in r.scenario_results if lp.status.value != "optimal")
        scored = [r for r in rows if r.status == "scored"]
        summary["tasks"][name] = dict(scenario_count=len(task_sets[name].scenarios),
            status_counts=dict(Counter(r.status for r in rows)),
            contact_status_counts=dict(Counter(r.geometry_status for r in rows)),
            family_status_counts={k: dict(v) for k, v in family.items()},
            worst_utilization=distribution([r.worst_utilization for r in scored]),
            selected_id=ranked["selected_id"], selected_family=None if not scored else scored[0].family,
            selected_family_distribution={} if not scored else {scored[0].family: 1},
            top5=[dict(id=r.candidate_id, family=r.family, utilization=r.worst_utilization,
                       score=r.score, worst_scenario_ids=r.worst_scenario_ids) for r in scored[:5]],
            worst_scenario_counts=dict(worst), nonoptimal_scenario_counts=dict(rejected),
            scenario_definitions={s.scenario_id: dict(quaternion_xyzw=s.orientation.quaternion_xyzw,
                disturbance_force_n=s.disturbance.force_n, required_wrench=s.required_contact_wrench) for s in task_sets[name].scenarios},
            candidate_evaluation_seconds=distribution(ranked["candidate_wall_times_s"]),
            task_evaluation_wall_s=ranked["task_evaluation_wall_s"],
            solver_call_seconds=distribution([lp.runtime_s for r in rows for _, lp in r.scenario_results]),
            lp_calls=sum(r.timings_s["lp_calls"] for r in rows),
            solver_seconds=sum(r.timings_s["scenario_solver"] for r in rows),
            ranking_assembly_s=ranked["ranking_assembly_s"],
            preparation_seconds={key: distribution([r.timings_s[key] for r in rows]) for key in ("contact_spec", "primitives", "constraints")})
        details = []
        for r in rows:
            data = r.to_dict()
            # Deduplicate only serialization, NEVER scenarios/LP evaluations.
            # Full inputs are stored once at the named root manifest/task_sets.
            data["provenance"].pop("task_set")
            data["provenance"].pop("config")
            data["provenance"]["shared_inputs"] = dict(config="manifest.config", task_set=f"task_sets.{name}")
            details.append(data)
        export["tasks"][name] = {**{k: v for k, v in ranked.items() if k != "scores"}, "scores": details}
    summary["total_wall_s"] = perf_counter()-start
    return summary, export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is not None and args.output_dir.exists():
        raise FileExistsError("refusing existing output directory")
    summary, export = run_smoke()
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        with (args.output_dir/"report.json").open("x") as stream:
            json.dump(export, stream, indent=2, allow_nan=False)
        with (args.output_dir/"summary.json").open("x") as stream:
            json.dump(summary, stream, indent=2, allow_nan=False)
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
