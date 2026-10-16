"""Reproducible controller/noise regression, not an LLM selector experiment."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform

from grasp_a.contracts import TASKS, candidate_sha256, load_candidates
from grasp_a.control import ObservationConfig
from grasp_a.simulation import CONTROLLER_VERSION, get_feasible_candidates, run_episode

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {"baseline": (0.0, 0.0), "mild": (.003, 2.0), "stress": (.008, 5.0)}


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    parser.add_argument("--profiles", choices=PROFILES, nargs="+", default=list(PROFILES))
    parser.add_argument("--out", type=Path, default=Path("results/local/observable_regression"))
    args = parser.parse_args()
    candidates = [c for file in ("candidates.example.json", "candidates.pilot.json")
                  for c in load_candidates(ROOT / file) if c.id in ("G02", "G03")]
    rows = []
    for profile in args.profiles:
        noise_m, noise_deg = PROFILES[profile]
        for seed in args.seeds:
            config = ObservationConfig(position_noise_m=noise_m, yaw_noise_deg=noise_deg,
                                       noise_seed=1000+seed)
            filtered = get_feasible_candidates(seed, candidates, observation_config=config)
            write_json(args.out / profile / f"filter_seed{seed}.json", filtered)
            for task in TASKS:
                for candidate in candidates:
                    result = run_episode(task, seed, candidate.id, candidates, observation_config=config)
                    relative = Path(profile) / f"{task}_{candidate.id}_seed{seed}.json"
                    write_json(args.out / relative, result)
                    rows.append({"profile": profile, "task": task, "seed": seed,
                                 "candidate_id": candidate.id, "success": result["success"],
                                 "failure_reason": result["failure_reason"],
                                 "feasible": result["feasibility"]["feasible"],
                                 "position_error_m": result["position_error_m"],
                                 "orientation_error_deg": result["orientation_error_deg"],
                                 "episode": relative.as_posix()})
        group = [r for r in rows if r["profile"] == profile]
        print(profile, "success", sum(r["success"] for r in group), "/", len(group), flush=True)
    summary = {"controller_version": CONTROLLER_VERSION, "scope": "controller_pilot_not_selector_evaluation",
               "candidate_sha256": candidate_sha256(candidates), "seeds": args.seeds,
               "profiles": {k: {"position_noise_m": PROFILES[k][0], "yaw_noise_deg": PROFILES[k][1]}
                            for k in args.profiles},
               "noise_seed_rule": "1000 + scene seed; shared across candidates/tasks/methods",
               "observation_source": "calibrated_prior; noise is NOT generated from true object pose",
               "python": platform.python_version(),
               "packages": {name: importlib.metadata.version(name) for name in ("pybullet", "numpy", "imageio")},
               "source_sha256": {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in
                                  ("grasp_a/simulation.py", "grasp_a/control.py", "grasp_a/evaluation.py",
                                   "grasp_a/contracts.py", "assets/mug.urdf")},
               "totals": {}, "trials": rows}
    for profile in args.profiles:
        group = [r for r in rows if r["profile"] == profile]
        summary["totals"][profile] = {"trials": len(group), "successes": sum(r["success"] for r in group),
                                      "feasible_trials": sum(r["feasible"] for r in group),
                                      "failure_reasons": dict(Counter(r["failure_reason"] for r in group if not r["success"]))}
    write_json(args.out / "summary.json", summary)
    print("Saved", (args.out / "summary.json").resolve(), flush=True)


if __name__ == "__main__":
    main()
