"""One fresh LLM selection followed by A's physical evaluation, with no cache.

Run with the existing ChatGPT CLI login (default; no OpenAI API key needed):
    python -m examples.llm_smoke --out results/local/codex_smoke.json

Run with GEMINI_API_KEY using the official google-genai SDK:
    python -m examples.llm_smoke --provider gemini --out results/local/gemini_smoke.json

An explicit OpenAI API backend remains available and uses separate API billing:
    python -m examples.llm_smoke --provider openai --out results/local/openai_smoke.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from grasp_a.contracts import TASKS, load_candidates
from grasp_c.providers import ProviderConfigurationError, build_provider
from grasp_c.runner import atomic_json
from grasp_c.selectors import LLMSelector

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "gemini", "openai"), default="codex")
    parser.add_argument("--model", help="Defaults: openai=gpt-6-luna, gemini=gemini-3.8-flash, codex=gpt-5.6-luna")
    parser.add_argument("--task", choices=TASKS, default="pour_prep")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--candidates", type=Path, default=ROOT / "configs/candidates.c-pilot.json")
    parser.add_argument("--out", type=Path, default=Path("results/local/llm_live_smoke.json"))
    parser.add_argument("--media-dir", type=Path, help="Optional video output; increases runtime")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("--out already exists; choose a fresh path to preserve the previous live call")

    try:
        from examples.evaluate_one_trial import evaluate_trial

        model = args.model or {"openai": "gpt-6-luna", "gemini": "gemini-3.8-flash",
                               "codex": "gpt-5.6-luna"}[args.provider]
        candidates = load_candidates(args.candidates)
        provider = build_provider({"type": args.provider})
        provider.preflight()
        if args.provider == "gemini":
            parameters = {"max_output_tokens": 1024}
            if model.startswith("gemini-2.5-pro"):
                # Pro cannot disable thinking; reserve its minimum budget.
                parameters["thinking_budget"] = 128
            elif model.startswith("gemini-2.5-"):
                parameters["thinking_budget"] = 0
            else:
                parameters["thinking_level"] = "low"
        else:
            parameters = {"reasoning_effort": "low"}
        if args.provider == "openai":
            parameters["max_output_tokens"] = 1024
        # A missing/invalid LLM response may fall back in the normal pipeline.
        # This smoke test explicitly reports that as a failed live-LLM check.
        selector = LLMSelector(provider, model, variant="M3", parameters=parameters,
                               cache_mode="off", cache_dir=None)
        trial = evaluate_trial(args.task, args.seed, "M3_LIVE_SMOKE", candidates, selector,
                               media_dir=args.media_dir)
        atomic_json(args.out, trial)
        selection = trial.get("selection") or {}
        metadata = selection.get("metadata") or {}
        episode = trial.get("episode") or {}
        fallback = metadata.get("fallback_to_geometry", metadata.get("fallback_to_m1"))
        llm_verified = (metadata.get("live_requests", 0) > 0
                        and metadata.get("cache_hit") is False and fallback is False)
        output = {
            "output_path": str(args.out.resolve()),
            "status": trial["status"], "provider": args.provider, "model": model,
            "task_id": args.task, "seed": args.seed,
            "feasible_ids": [c["id"] for c in trial["filter"]["feasible_candidates"]],
            "raw_initial_response": metadata.get("raw_initial_response"),
            "selected_id": trial.get("selected_id"), "ranking": selection.get("ranking"),
            "fresh_llm_response_verified": llm_verified,
            "live_requests": metadata.get("live_requests", 0),
            "cache_hit": metadata.get("cache_hit"),
            "repair_attempts": metadata.get("repair_attempts"),
            "fallback_to_geometry": fallback,
            "llm_errors": [call["error"] for call in metadata.get("calls", []) if call.get("error")],
            "live_usage": metadata.get("live_usage"),
            "episode": {key: episode.get(key) for key in (
                "controller_version", "evaluation_version", "success", "failure_reason",
                "position_error_m", "orientation_error_deg", "hold_duration_s",
                "hold_pose_within_tolerance", "collision", "dropped")},
            "error": trial.get("error"),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        # Zero proves both a non-cached, non-fallback LLM answer and physical success.
        return 0 if llm_verified and trial["status"] == "completed" and episode.get("success") else 1
    except (ValueError, ProviderConfigurationError, OSError, ImportError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
