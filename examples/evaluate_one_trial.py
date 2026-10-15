"""Evaluator integration example, not an implementation of M0/M1/M2/M3.

Run after activating grasp-a:
    python -m examples.evaluate_one_trial --task pour_prep --seed 0
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Callable

from grasp_a.contracts import Candidate, TASKS, load_candidates, validate_task_id
from grasp_a.simulation import CONTROLLER_VERSION, get_feasible_candidates, run_episode

ROOT = Path(__file__).resolve().parents[1]
Selector = Callable[[str, list[dict], int], str | dict]


def candidate_sha256(candidates: list[Candidate]) -> str:
    """Hash the ordered full candidate snapshot, including execution poses."""
    payload = json.dumps([asdict(c) for c in candidates], sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_filter(filtered: dict, seed: int, candidates: list[Candidate]) -> None:
    expected = {c.id: c.public_metadata() for c in candidates}
    checks = filtered["checks"]
    public = filtered["feasible_candidates"]
    if filtered["seed"] != seed or filtered["controller_version"] != CONTROLLER_VERSION:
        raise ValueError("filter seed/controller does not match this trial")
    if len(checks) != len(expected) or {c["candidate_id"] for c in checks} != set(expected):
        raise ValueError("filter checks do not match the supplied candidate IDs")
    allowed = {c["candidate_id"] for c in checks if c["feasible"]}
    if (len(public) != len(allowed) or {c["id"] for c in public} != allowed or
            any(c != expected[c["id"]] for c in public)):
        raise ValueError("filter public records do not match the supplied candidates")
    if filtered["status"] != ("ok" if public else "no_feasible_candidate"):
        raise ValueError("filter status is inconsistent with its feasible list")


def _normalize_selection(raw: str | dict, allowed: set[str]) -> dict:
    if isinstance(raw, str):
        selection = {"selected_id": raw, "ranking": None, "metadata": {}}
    elif isinstance(raw, dict):
        selection = {"selected_id": raw.get("selected_id"),
                     "ranking": raw.get("ranking"), "metadata": raw.get("metadata", {})}
    else:
        raise ValueError("selector must return a string or selection dictionary")
    if not isinstance(selection["selected_id"], str) or selection["selected_id"] not in allowed:
        raise ValueError("selected_id must be in the current feasible list")
    ranking = selection["ranking"]
    if ranking is not None:
        if (not isinstance(ranking, list) or not all(isinstance(x, str) for x in ranking)
                or len(ranking) != len(allowed) or set(ranking) != allowed
                or ranking[0] != selection["selected_id"]):
            raise ValueError("ranking must contain every feasible ID once, with selected_id first")
    if not isinstance(selection["metadata"], dict):
        raise ValueError("metadata must be a JSON-compatible dictionary")
    json.dumps(selection, allow_nan=False)
    return selection


def evaluate_trial(
    task_id: str,
    seed: int,
    method_id: str,
    candidates: list[Candidate],
    selector: Selector,
    *,
    filtered: dict | None = None,
    media_dir: str | Path | None = None,
) -> dict:
    """Wrap one selection/execution in the evaluation_trial_v0.1 record.

    A supplied filter must be freshly obtained from the exact same candidate
    poses, seed, controller, and scene. Public metadata cannot prove pose equality.
    Input/filter errors propagate; selector/execution errors are recorded.
    """
    validate_task_id(task_id)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not isinstance(method_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", method_id):
        raise ValueError("method_id must contain only letters, digits, underscores, or hyphens")
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError("duplicate candidate IDs")
    if filtered is None:
        filtered = get_feasible_candidates(seed, candidates)
    _check_filter(filtered, seed, candidates)
    record = {
        "schema_version": "evaluation_trial_v0.1",
        "evaluation_scope": "pilot",
        "controller_version": CONTROLLER_VERSION,
        "method_id": method_id, "task_id": task_id, "seed": seed,
        "candidate_sha256": candidate_sha256(candidates),
        "status": "no_feasible_candidate",
        "selected_id": None,
        "selection": None,
        "filter": deepcopy(filtered),
        "episode": None,
        "error": None,
    }
    public = filtered["feasible_candidates"]
    if not public:
        return record
    started = perf_counter()
    try:
        raw = selector(task_id, deepcopy(public), seed)
    except Exception as exc:
        record.update(status="selector_error",
                      error={"type": type(exc).__name__, "message": str(exc)})
        record["selection"] = {"latency_s": perf_counter()-started}
        return record
    latency = perf_counter()-started
    try:
        selection = _normalize_selection(raw, {c["id"] for c in public})
    except (ValueError, TypeError, OverflowError) as exc:
        record.update(status="invalid_selection",
                      error={"type": type(exc).__name__, "message": str(exc)})
        record["selection"] = {"latency_s": latency, "raw_output_repr": repr(raw)}
        return record
    record["selection"] = deepcopy(selection)
    record["selection"]["latency_s"] = latency
    record["selected_id"] = selection["selected_id"]
    try:
        output = (Path(media_dir) / method_id / f"{task_id}_seed{seed}"
                  if media_dir is not None else None)
        episode = run_episode(task_id, seed, record["selected_id"], candidates,
                              gui=False, media_dir=output)
        record["episode"] = episode
        if (episode["seed"] != seed or episode["task_id"] != task_id
                or episode["selected_id"] != record["selected_id"]
                or episode["controller_version"] != CONTROLLER_VERSION
                or not episode["feasibility"]["feasible"]):
            raise ValueError("execution result disagrees with trial identity or feasibility")
        record["status"] = "completed"
    except Exception as exc:
        record.update(status="execution_error",
                      error={"type": type(exc).__name__, "message": str(exc)})
    return record


def smoke_selector(task_id: str, candidates: list[dict], seed: int) -> str:
    """First feasible only; never label this M0, M1, M2, M3, or A1."""
    return candidates[0]["id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=TASKS, default="pour_prep")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--candidates", default=str(ROOT / "candidates.example.json"))
    parser.add_argument("--out", type=Path, default=Path("results/local/interface_smoke.json"))
    args = parser.parse_args()
    record = evaluate_trial(args.task, args.seed, "SMOKE",
                            load_candidates(args.candidates), smoke_selector)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)+"\n",
                        encoding="utf-8")
    print(json.dumps({"path": str(args.out.resolve()), "status": record["status"],
                      "selected_id": record["selected_id"],
                      "success": record["episode"]["success"] if record["episode"] else None},
                     ensure_ascii=False))
    raise SystemExit(0 if record["status"] in ("completed", "no_feasible_candidate") else 1)


if __name__ == "__main__":
    main()
