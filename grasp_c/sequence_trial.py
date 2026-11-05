"""C's ID-only trial boundary for A's complete physical task sequences.

Only the public candidate metadata reaches a selector. Task specifications,
contacts, fluid quantities and scoring evidence remain evaluation records.
Simulator imports are lazy so configuration and cached-filter checks stay offline.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Callable

from grasp_a.contracts import Candidate, candidate_sha256
from grasp_a.control import ObservationConfig
from grasp_a.sequence_contracts import (SEQUENCE_CONTROLLER, SEQUENCE_EVALUATOR,
                                        SEQUENCE_TASKS, SequenceConfig, task_spec)

SEQUENCE_SCENE = "hollow_cup_sequences_v1"
SEQUENCE_FILTER_SCHEMA = "sequence_filter_v1"
SEQUENCE_EPISODE_SCHEMA = "sequence_episode_v1"
SEQUENCE_TRIAL_SCHEMA = "evaluation_sequence_trial_v1"
SEQUENCE_EPISODE_STATUSES = frozenset({"completed", "rejected", "execution_failed",
                                      "simulation_error", "simulation_invalid"})
Selector = Callable[[str, list[dict], int], str | dict]


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sequence_spec_sha256(spec: dict) -> str:
    """Use the same canonical representation as A's ``spec_sha256``."""
    return hashlib.sha256(_canonical(spec).encode("utf-8")).hexdigest()


def check_success_evidence(episode):
    """Never save a successful outcome with missing or contradictory numerical evidence."""
    if episode.get("success") is True:
        density = (episode.get("success_checks") or {}).get("fluid_density_valid")
        water = (episode.get("final_evidence") or {}).get("water")
        if density is not True or (isinstance(water, dict) and water.get("finite_state") is False):
            raise ValueError("successful episode requires explicit valid numerical evidence")


def get_sequence_candidates(*args, **kwargs):
    from grasp_a.sequence import get_sequence_candidates as implementation
    return implementation(*args, **kwargs)


def run_sequence(*args, **kwargs):
    from grasp_a.sequence import run_sequence as implementation
    return implementation(*args, **kwargs)


def _identity(task_id: str, seed: int, candidates: list[Candidate],
              sequence_config: SequenceConfig, observation_config: ObservationConfig,
              scene_config=None) -> dict:
    spec = task_spec(task_id, sequence_config)
    identity = {"task_id": task_id, "seed": seed, "scene_version": SEQUENCE_SCENE,
                "controller_version": SEQUENCE_CONTROLLER, "evaluation_version": SEQUENCE_EVALUATOR,
                "candidate_sha256": candidate_sha256(candidates),
                "task_spec_sha256": sequence_spec_sha256(spec), "task_spec": spec,
                "observation_config": asdict(observation_config)}
    if scene_config is not None:
        identity["scene_randomization"] = asdict(scene_config)
    return identity


def _a_identity(identity: dict) -> dict:
    """Fields A's filter/episode must echo. A noisy_truth scene derives the
    estimate per seed, so A reports that derived ObservationConfig instead of
    the configured one; the filter/episode estimate match is checked separately."""
    scene = identity.get("scene_randomization")
    if scene is not None and scene["observation"] == "noisy_truth":
        return {k: v for k, v in identity.items() if k != "observation_config"}
    return identity


def _check_identity(value: dict, expected: dict, *, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    for field, match in expected.items():
        if _canonical(value.get(field)) != _canonical(match):
            raise ValueError(f"{label} {field} does not match this trial")


def check_sequence_filter(filtered: dict, task_id: str, seed: int,
                          candidates: list[Candidate], *, sequence_config: SequenceConfig,
                          observation_config: ObservationConfig, scene_config=None) -> None:
    """Reject stale/cross-track filters before any selection or execution."""
    expected = _a_identity(_identity(task_id, seed, candidates, sequence_config, observation_config, scene_config))
    _check_identity(filtered, dict(expected, schema_version=SEQUENCE_FILTER_SCHEMA), label="filter")
    checks = filtered.get("checks")
    if (not isinstance(checks, list) or len(checks) != len(candidates) or
            any(not isinstance(check, dict) or type(check.get("feasible")) is not bool
                for check in checks)):
        raise ValueError("filter checks do not match the supplied candidates")
    check_ids = [check.get("candidate_id") for check in checks]
    if (any(not isinstance(item, str) for item in check_ids) or
            len(set(check_ids)) != len(check_ids) or set(check_ids) != {c.id for c in candidates}):
        raise ValueError("filter check IDs do not match the supplied candidates")
    allowed = {check["candidate_id"] for check in checks if check["feasible"]}
    expected_public = [candidate.public_metadata() for candidate in candidates if candidate.id in allowed]
    if filtered.get("feasible_candidates") != expected_public:
        raise ValueError("filter public candidates do not match the supplied candidates")
    if filtered.get("status") != ("ok" if expected_public else "no_feasible_candidate"):
        raise ValueError("filter status is inconsistent with its feasible list")
    # Diagnostics are saved, never sent to the policy; reject non-JSON cache data.
    _canonical(filtered)


def _normalize_selection(raw: str | dict, allowed: set[str]) -> dict:
    if isinstance(raw, str):
        selection = {"selected_id": raw, "ranking": None, "metadata": {}}
    elif isinstance(raw, dict):
        selection = {"selected_id": raw.get("selected_id"), "ranking": raw.get("ranking"),
                     "metadata": raw.get("metadata", {})}
    else:
        raise ValueError("selector must return a string or selection dictionary")
    selected = selection["selected_id"]
    if not isinstance(selected, str) or selected not in allowed:
        raise ValueError("selected_id must be in the current feasible list")
    ranking = selection["ranking"]
    if ranking is not None and (
            not isinstance(ranking, list) or not all(isinstance(item, str) for item in ranking) or
            len(ranking) != len(allowed) or set(ranking) != allowed or ranking[0] != selected):
        raise ValueError("ranking must contain every feasible ID once, with selected_id first")
    if not isinstance(selection["metadata"], dict):
        raise ValueError("metadata must be a JSON-compatible dictionary")
    _canonical(selection)
    return deepcopy(selection)


def evaluate_sequence_trial(task_id: str, seed: int, method_id: str,
                            candidates: list[Candidate], selector: Selector, *,
                            sequence_config: SequenceConfig | None = None,
                            observation_config: ObservationConfig | None = None,
                            filtered: dict | None = None, media_dir: str | Path | None = None,
                            show_landmarks: bool = True, scene_config=None) -> dict:
    """Select an ID and preserve A's execution/numerical status independently.

    Invalid input or an injected filter that disagrees with this condition raises
    ValueError. Fresh filtering, selector, output and execution exceptions have
    separate records. A returned episode remains intact even if validation fails.
    """
    if task_id not in SEQUENCE_TASKS:
        raise ValueError(f"sequence task must be one of {SEQUENCE_TASKS}")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not isinstance(method_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", method_id):
        raise ValueError("method_id must contain only letters, digits, underscores, or hyphens")
    if not isinstance(candidates, list) or any(not isinstance(c, Candidate) for c in candidates):
        raise ValueError("candidates must be a list of Candidate objects")
    if len({candidate.id for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate candidate IDs")
    if sequence_config is not None and not isinstance(sequence_config, SequenceConfig):
        raise ValueError("sequence_config must be a SequenceConfig")
    if observation_config is not None and not isinstance(observation_config, ObservationConfig):
        raise ValueError("observation_config must be an ObservationConfig")
    if type(show_landmarks) is not bool:
        raise ValueError("show_landmarks must be a bool")
    config = sequence_config if sequence_config is not None else SequenceConfig()
    observation = observation_config if observation_config is not None else ObservationConfig()
    if scene_config is not None:
        from grasp_a.control import scene_randomization
        scene_config = scene_randomization(scene_config)
    # A derives the estimate itself for noisy_truth scenes and refuses one.
    a_observation = (None if scene_config is not None and scene_config.observation == "noisy_truth"
                     else observation)
    scene_kwargs = {} if scene_config is None else {"scene_config": scene_config}
    identity = _identity(task_id, seed, candidates, config, observation, scene_config)
    record = {"schema_version": SEQUENCE_TRIAL_SCHEMA, "track": "sequence",
              "evaluation_scope": "pilot", **identity,
              "method_id": method_id, "sequence_config": asdict(config),
              "policy_mode": "fixed", "media_options": {"enabled": media_dir is not None,
                                                            "show_landmarks": show_landmarks},
              "status": "no_feasible_candidate", "selected_id": None,
              "filter": None, "selection": None, "episode": None, "error": None}
    if filtered is None:
        try:
            filtered = get_sequence_candidates(task_id, seed, candidates, config=config,
                                               observation_config=a_observation, **scene_kwargs)
            check_sequence_filter(filtered, task_id, seed, candidates, sequence_config=config,
                                  observation_config=observation, scene_config=scene_config)
        except Exception as exc:
            record.update(status="filter_error", error={"type": type(exc).__name__, "message": str(exc)})
            return record
    else:
        check_sequence_filter(filtered, task_id, seed, candidates, sequence_config=config,
                              observation_config=observation, scene_config=scene_config)
    record["filter"] = deepcopy(filtered)
    public = filtered["feasible_candidates"]
    if not public:
        return record
    started = perf_counter()
    try:
        raw = selector(task_id, deepcopy(public), seed)
    except Exception as exc:
        record.update(status="selector_error", error={"type": type(exc).__name__, "message": str(exc)})
        record["selection"] = {"latency_s": perf_counter() - started}
        return record
    latency = perf_counter() - started
    try:
        selection = _normalize_selection(raw, {item["id"] for item in public})
    except (ValueError, TypeError, OverflowError) as exc:
        record.update(status="invalid_selection", error={"type": type(exc).__name__, "message": str(exc)})
        record["selection"] = {"latency_s": latency, "raw_output_repr": repr(raw)}
        return record
    record["selection"] = dict(selection, latency_s=latency)
    record["selected_id"] = selection["selected_id"]
    try:
        output = Path(media_dir) / method_id / f"{task_id}_seed{seed}" if media_dir is not None else None
        episode = run_sequence(task_id, seed, selection["selected_id"], candidates, config=config,
                               observation_config=a_observation, gui=False, media_dir=output,
                               show_landmarks=show_landmarks, **scene_kwargs)
        record["episode"] = episode
        expected = dict(_a_identity(identity), selected_id=selection["selected_id"], schema_version=SEQUENCE_EPISODE_SCHEMA)
        _check_identity(episode, expected, label="episode")
        if episode.get("status") not in SEQUENCE_EPISODE_STATUSES:
            raise ValueError("episode has an unknown execution status")
        if type(episode.get("success")) is not bool:
            raise ValueError("episode success must be a boolean")
        if episode["status"] != "completed" and episode["success"]:
            raise ValueError("non-completed episode cannot report success")
        check_success_evidence(episode)
        feasibility = episode.get("feasibility")
        if (not isinstance(feasibility, dict) or feasibility.get("candidate_id") != selection["selected_id"] or
                type(feasibility.get("feasible")) is not bool or
                (episode["status"] == "rejected") == feasibility["feasible"]):
            raise ValueError("episode feasibility disagrees with its execution status")
        if _canonical(episode.get("initial_object_pose_estimate")) != _canonical(
                filtered.get("initial_object_pose_estimate")):
            raise ValueError("episode initial estimate disagrees with the filter")
        _canonical(episode)
        record["status"] = episode["status"]
    except Exception as exc:
        record.update(status="execution_error", error={"type": type(exc).__name__, "message": str(exc)})
    return record
