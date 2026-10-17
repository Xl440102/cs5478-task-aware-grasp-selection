"""Command line entry point: python -m grasp_a ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import TASKS, Pose, load_candidates
from .control import ObservationConfig
from .sequence_contracts import SEQUENCE_TASKS, SequenceConfig, task_spec

ROOT = Path(__file__).resolve().parents[1]


def save(data: dict, out: str | None):
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        print(f"Saved {path.resolve()}")
        summary = {k:data[k] for k in ("selected_id", "success", "failure_reason", "media")
                   if k in data}
        if summary:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(payload)


def main():
    parser = argparse.ArgumentParser(description="Part A: mug + Panda simulation")
    parser.add_argument("--candidates", default=str(ROOT / "candidates.example.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Check candidate JSON without PyBullet")
    scene = commands.add_parser("scene", help="Save a PNG of the initial simulation world")
    scene.add_argument("--seed", type=int, default=0)
    scene.add_argument("--out", default="results/scene_seed0.png")
    scene.add_argument("--video", default="results/scene_orbit_seed0.mp4")
    filt = commands.add_parser("filter", help="Check width, IK/FK, sampled path collision")
    filt.add_argument("--seed", type=int, default=0)
    filt.add_argument("--out", default="results/filter_seed0.json")
    run = commands.add_parser("run", help="Run one selected grasp in a fresh world")
    run.add_argument("--task", choices=TASKS, required=True)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--id", required=True, help="Candidate ID already chosen by a selector")
    run.add_argument("--gui", action="store_true", help="Show local PyBullet window")
    run.add_argument("--out", default="results/episode.json")
    run.add_argument("--visualize", action="store_true", help="Record MP4 and scene/final PNGs")
    run.add_argument("--media-dir", default="results/visuals")
    demo = commands.add_parser("demo", help="Choose first feasible ID for a pipeline smoke run")
    demo.add_argument("--task", choices=TASKS, default="pour_prep")
    demo.add_argument("--seed", type=int, default=0)
    demo.add_argument("--out", default="results/demo.json")
    demo.add_argument("--visualize", action="store_true", help="Record MP4 and scene/final PNGs")
    demo.add_argument("--media-dir", default="results/visuals")
    spec = commands.add_parser("task-spec", help="Export detailed sequence definition without simulation")
    spec.add_argument("--task", choices=SEQUENCE_TASKS, required=True)
    spec.add_argument("--config", help="SequenceConfig JSON overrides")
    spec.add_argument("--out")
    seq = commands.add_parser("sequence", help="Run opt-in full physical task")
    seq.add_argument("--task", choices=SEQUENCE_TASKS, required=True)
    seq.add_argument("--id", required=True)
    seq.add_argument("--seed", type=int, default=0)
    seq.add_argument("--config", help="SequenceConfig JSON overrides")
    seq.add_argument("--gui", action="store_true")
    seq.add_argument("--visualize", action="store_true")
    seq.add_argument("--landmarks", action=argparse.BooleanOptionalAction,
                     dest="show_landmarks", default=True,
                     help="Show A/B video overlays (default); --no-landmarks keeps the plain view")
    seq.add_argument("--media-dir", default="results/local/sequences/media")
    seq.add_argument("--out", default="results/local/sequences/episode.json")
    seq_filter = commands.add_parser("sequence-filter", help="Initial grasp checks in the new task scene")
    seq_filter.add_argument("--task", choices=SEQUENCE_TASKS, required=True)
    seq_filter.add_argument("--seed", type=int, default=0)
    seq_filter.add_argument("--config")
    seq_filter.add_argument("--out", default="results/local/sequences/filter.json")
    for command in (filt, run, demo, seq, seq_filter):
        command.add_argument("--observed-pose", help="JSON Pose file from an external pose estimator")
        command.add_argument("--pose-noise-m", type=float, default=0.0,
                             help="Uniform +/- perturbation of estimate per world axis, metres")
        command.add_argument("--yaw-noise-deg", type=float, default=0.0)
        command.add_argument("--observation-seed", type=int, default=0)
    args = parser.parse_args()
    sequence_config = (SequenceConfig(**json.loads(Path(args.config).read_text(encoding="utf-8")))
                       if getattr(args,"config",None) else SequenceConfig())
    if args.command == "task-spec":
        save(task_spec(args.task,sequence_config),args.out)
        return
    candidates = load_candidates(args.candidates)
    if args.command == "validate":
        save({"candidate_count":len(candidates),"ids":[x.id for x in candidates]},None)
        return

    # Import only when a simulation command is requested; validate works offline.
    from .simulation import get_feasible_candidates, run_episode
    config = None
    if args.command != "scene":
        observed = (Pose.from_dict(json.loads(Path(args.observed_pose).read_text(encoding="utf-8")))
                    if args.observed_pose else ObservationConfig().object_pose)
        config = ObservationConfig(observed,
                                   "external_estimate" if args.observed_pose else "calibrated_prior",
                                   args.pose_noise_m, args.yaw_noise_deg, args.observation_seed)
    if args.command == "scene":
        from .simulation import MugScene
        from .visualization import save_scene_image, save_scene_orbit
        with MugScene(args.seed) as world:
            saved = save_scene_image(world.client, args.out, f"scene seed {args.seed}")
            movie = save_scene_orbit(world.client, args.video, args.seed)
        print(f"Saved {saved}")
        print(f"Saved {movie}")
    elif args.command in ("sequence", "sequence-filter"):
        from .sequence import run_sequence, get_sequence_candidates
        if args.command == "sequence-filter":
            save(get_sequence_candidates(args.task,args.seed,candidates,config=sequence_config,
                                         observation_config=config),args.out)
        else:
            save(run_sequence(args.task,args.seed,args.id,candidates,config=sequence_config,
                              observation_config=config,gui=args.gui,
                              media_dir=args.media_dir if args.visualize else None,
                              show_landmarks=args.show_landmarks),args.out)
    elif args.command == "filter":
        save(get_feasible_candidates(args.seed,candidates,observation_config=config),args.out)
    elif args.command == "run":
        save(run_episode(args.task,args.seed,args.id,candidates,gui=args.gui,
                         media_dir=args.media_dir if args.visualize else None,
                         observation_config=config),args.out)
    else:
        filtered = get_feasible_candidates(args.seed,candidates,observation_config=config)
        if not filtered["feasible_candidates"]:
            save({"status":"no_feasible_candidate","filter":filtered},args.out)
        else:
            selected = filtered["feasible_candidates"][0]["id"]
            save(run_episode(args.task,args.seed,selected,candidates,
                             media_dir=args.media_dir if args.visualize else None,
                             observation_config=config),args.out)


if __name__ == "__main__":
    main()
