"""Command-line entry point for C's multi-model pilot evaluator."""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate configuration offline, without credentials")
    validate.add_argument("--config", required=True)
    run = commands.add_parser("run", help="run and save every planned trial incrementally")
    run.add_argument("--config", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--retry-errors", action="store_true", help="explicitly retry saved error trials (may call paid models)")
    run.add_argument("--media", action="store_true", help="save videos and images in separate repeat/method directories")
    run.add_argument("--plain", action="store_true", help="sequence videos without A/B landmarks; physics unchanged")
    prepare = commands.add_parser("prepare-b", help="export one shared B-scored candidate input; no physics or LLM")
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--generation-config")
    prepare.add_argument("--scoring-config")
    prepare.add_argument("--generator", choices=("legacy_v1", "multifamily_v3"), default="legacy_v1",
                         help="multifamily_v3: B's side/top/rim/handle candidates for the sequence track")
    prepare.add_argument("--profile", choices=("solid", "hollow"), default="hollow",
                         help="cup profile for multifamily_v3 (sequence tasks use hollow)")
    prepare.add_argument("--pruned", action="store_true", help="multifamily_v3: drop never-feasible dimensions")
    prepare.add_argument("--semantic", action="store_true",
                         help="multifamily_v3: put semantic_v1 relational descriptions in the public approach field")
    summarize = commands.add_parser("summarize", help="rebuild metrics from saved records without model calls")
    summarize.add_argument("run_dir")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-b":
            if args.generator == "multifamily_v3":
                from .prepare_b import prepare_b_v3_inputs
                output = prepare_b_v3_inputs(args.out, profile=args.profile, pruned=args.pruned,
                                             semantic=args.semantic)
            else:
                from .prepare_b import prepare_b_inputs
                output = prepare_b_inputs(args.out, generation_config=args.generation_config,
                                          scoring_config=args.scoring_config)
        elif args.command == "validate":
            config = load_config(args.config)
            output = {"valid": True, "track": config["track"], "evaluation_scope": config["evaluation_scope"],
                      "methods": [m["id"] for m in config["methods"]],
                      "planned_trials": len(config["seeds"]) * len(config["tasks"]) *
                                        config["repeats"] * len(config["methods"])}
        else:
            from .runner import run_evaluation, summarize_run
            if args.command == "summarize":
                summary = summarize_run(args.run_dir)
            else:
                summary = run_evaluation(args.config, args.out, resume=args.resume,
                                         retry_errors=args.retry_errors, media=args.media,
                                         show_landmarks=not args.plain,
                                         progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
            output = {"run_id": summary["run_id"], "planned_n": summary["planned_n"],
                      "saved_n": summary["saved_n"], "methods": summary["methods"]}
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        if args.command not in {"validate", "prepare-b"}:
            errors = sum(count for tasks in summary["methods"].values() for metrics in tasks.values()
                         for status, count in metrics["status_counts"].items()
                         if status in {"filter_error", "selector_error", "invalid_selection", "execution_error", "simulation_error", "simulation_invalid", "rejected", "missing"})
            return 1 if errors else 0
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
