"""Command line entry point: python -m grasp_a ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import TASKS, load_candidates

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
    args = parser.parse_args()
    candidates = load_candidates(args.candidates)
    if args.command == "validate":
        save({"candidate_count":len(candidates),"ids":[x.id for x in candidates]},None)
        return

    # Import only when a simulation command is requested; validate works offline.
    from .simulation import get_feasible_candidates, run_episode
    if args.command == "scene":
        from .simulation import MugScene
        from .visualization import save_scene_image, save_scene_orbit
        with MugScene(args.seed) as world:
            saved = save_scene_image(world.client, args.out, f"scene seed {args.seed}")
            movie = save_scene_orbit(world.client, args.video, args.seed)
        print(f"Saved {saved}")
        print(f"Saved {movie}")
    elif args.command == "filter":
        save(get_feasible_candidates(args.seed,candidates),args.out)
    elif args.command == "run":
        save(run_episode(args.task,args.seed,args.id,candidates,gui=args.gui,
                         media_dir=args.media_dir if args.visualize else None),args.out)
    else:
        filtered = get_feasible_candidates(args.seed,candidates)
        if not filtered["feasible_candidates"]:
            save({"status":"no_feasible_candidate","filter":filtered},args.out)
        else:
            selected = filtered["feasible_candidates"][0]["id"]
            save(run_episode(args.task,args.seed,selected,candidates,
                             media_dir=args.media_dir if args.visualize else None),args.out)


if __name__ == "__main__":
    main()
