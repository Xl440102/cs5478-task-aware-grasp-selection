"""Offline validation and path resolution for reproducible C evaluations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import hashlib
import math
from pathlib import Path
import re

from grasp_a.contracts import Pose, candidate_sha256, load_candidates
from grasp_a.control import ObservationConfig
from .tasks import (PREP_TASKS, SEQUENCE_TASKS, load_sequence_config,
                    tasks_for_track)

ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
PROVIDER_TYPES = {"openai", "openai_compatible", "gemini", "codex", "mock"}
CACHE_MODES = {"read_write", "read_only", "off"}


def load_observation_config(raw: dict | None = None) -> ObservationConfig:
    """Parse A's public estimate configuration without importing the simulator."""
    values = deepcopy(_json_object({} if raw is None else raw, "observation_config"))
    fields = {"object_pose", "source", "position_noise_m", "yaw_noise_deg", "noise_seed"}
    if set(values) - fields:
        raise ValueError("observation_config contains unknown fields")
    if "object_pose" in values:
        pose = _json_object(values["object_pose"], "observation_config.object_pose")
        if set(pose) != {"position", "quaternion"}:
            raise ValueError("observation_config.object_pose needs position and quaternion")
        for field, size in (("position", 3), ("quaternion", 4)):
            items = pose[field]
            if (not isinstance(items, (list, tuple)) or len(items) != size or
                    any(type(x) not in (int, float) or not math.isfinite(x) for x in items)):
                raise ValueError(f"observation_config.object_pose.{field} needs {size} finite numbers")
        values["object_pose"] = Pose.from_dict(pose)
    for field in ("position_noise_m", "yaw_noise_deg"):
        if field in values and (type(values[field]) not in (int, float) or
                                not math.isfinite(values[field]) or values[field] < 0):
            raise ValueError(f"observation_config.{field} must be finite and nonnegative")
    if "noise_seed" in values:
        _integer(values["noise_seed"], "observation_config.noise_seed")
    return ObservationConfig(**values)


def _integer(value, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{label} must be an integer" +
                         (f" >= {minimum}" if minimum is not None else ""))
    return value


def _json_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    json.dumps(value, allow_nan=False)
    return value


def _reject_secrets(value, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"api_key", "access_token", "refresh_token", "authorization",
                               "password", "secret", "token"}:
                raise ValueError(f"{path}.{key}: store credentials in environment variables, "
                                 "and configure only the environment variable name")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item, path)


def _resolve_path(value, base: Path, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty path string")
    return str((base / value).resolve())


def load_b_provenance(path, candidate_file):
    """Verify B's scored input and audit sidecar before any selection or simulation."""
    path, candidate_file = Path(path), Path(candidate_file)
    data = _json_object(json.loads(path.read_text(encoding="utf-8")), "b_provenance")
    _reject_secrets(data, "b_provenance")
    candidates = load_candidates(candidate_file)
    if (data.get("schema_version") != "grasp_c_b_inputs_v1" or
            data.get("candidate_sha256") != candidate_sha256(candidates) or
            data.get("candidate_file_sha256") != hashlib.sha256(candidate_file.read_bytes()).hexdigest() or
            type(data.get("candidate_count")) is not int or data["candidate_count"] != len(candidates)):
        raise ValueError("B provenance does not match the scored candidate file")
    name = data.get("report_file")
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("B provenance report_file must be a sibling filename")
    report = path.parent / name
    if data.get("report_sha256") != hashlib.sha256(report.read_bytes()).hexdigest():
        raise ValueError("B provenance report fingerprint mismatch")
    from .baselines import build_b_selector
    build_b_selector("b_geometry", data)
    _check_b_report(data, _json_object(json.loads(report.read_text(encoding="utf-8")), "B report"), candidates)
    return data


def _check_b_report(provenance, report, candidates):
    """A valid file digest alone cannot establish the claimed score formula/weights."""
    from .cache import canonical_json
    from grasp_b.scoring import normalized_weights
    if not isinstance(provenance.get("source_sha256"), dict) or not provenance["source_sha256"]:
        raise ValueError("B provenance needs its original source fingerprints")
    expected = {"schema_version": "grasp_c_b_report_v1", "scope": provenance["scope"],
                "score_version": provenance["score_version"],
                "candidate_sha256": provenance["candidate_sha256"],
                "scored_candidate_count": len(candidates), "source_sha256": provenance.get("source_sha256")}
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("B report disagrees with candidate/score provenance")
    scoring = _json_object(report.get("scoring_config"), "B scoring configuration")
    if (scoring.get("version") != provenance["score_version"] or
            scoring.get("scope") != provenance["scope"] or scoring.get("weights") != provenance["weights"]):
        raise ValueError("B report weights/version disagree with score provenance")
    generation = _json_object(report.get("generation_config"), "B generation configuration")
    if generation.get("version") != provenance.get("generation_version"):
        raise ValueError("B generation version mismatch")
    for key, value in (("scoring_config", scoring), ("generation_config", generation)):
        digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
        if report.get(key + "_canonical_sha256") != digest:
            raise ValueError("B report configuration fingerprint mismatch")
    rows = report.get("score_breakdown")
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or
            any(not isinstance(row.get("candidate_id"), str) for row in rows) or
            len({row["candidate_id"] for row in rows}) != len(rows)):
        raise ValueError("invalid B score breakdown")
    scored = {row["candidate_id"]: row for row in rows if row.get("status") == "ok"}
    if set(scored) != {candidate.id for candidate in candidates}:
        raise ValueError("B scored IDs do not match the exported candidates")
    weights = normalized_weights(provenance["weights"])
    for candidate in candidates:
        row = scored[candidate.id]
        parameters = _json_object(row.get("parameters"), "B score parameters")
        if (row.get("version") != provenance["score_version"] or row.get("scope") != provenance["scope"] or
                parameters.get("weights") != provenance["weights"] or
                parameters.get("normalized_weights") != weights or
                type(row.get("score")) not in (int, float) or not math.isfinite(row["score"]) or
                not math.isclose(row["score"], candidate.geometry_score, rel_tol=0, abs_tol=1e-12)):
            raise ValueError("B candidate score does not match its report breakdown and weights")


def validate_config(raw: dict, *, base_dir: str | Path = ".") -> dict:
    """Validate inputs without importing a simulator, SDK, or calling a provider."""
    config = deepcopy(_json_object(raw, "config"))
    _reject_secrets(config)
    base = Path(base_dir).resolve()
    config["candidates"] = _resolve_path(config.get("candidates"), base, "candidates")
    candidates = load_candidates(config["candidates"])
    if not candidates:
        raise ValueError("candidates must contain at least one candidate")
    observation = load_observation_config(config.get("observation_config"))
    # A uses tuples internally; JSON canonicalization makes loaded and in-memory
    # configurations identical for provenance, cache checks, and comparisons.
    config["observation_config"] = json.loads(json.dumps(asdict(observation)))
    config.setdefault("policy_mode", "fixed")
    if config["policy_mode"] != "fixed":
        raise ValueError("C evaluates ID-only methods with policy_mode='fixed'")
    seeds = config.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("seeds must be a nonempty integer list")
    for seed in seeds:
        _integer(seed, "seed")
    if len(seeds) != len(set(seeds)):
        raise ValueError("seeds must be unique")
    config.setdefault("track", "prep")
    supported_tasks = tasks_for_track(config["track"])
    if config["track"] == "sequence":
        config["sequence_config"] = json.loads(json.dumps(asdict(
            load_sequence_config(config.get("sequence_config")))))
    elif "sequence_config" in config:
        raise ValueError("sequence_config requires track='sequence'")
    tasks = config.get("tasks", list(supported_tasks))
    if (not isinstance(tasks, list) or not tasks or
            any(task not in supported_tasks for task in tasks) or len(tasks) != len(set(tasks))):
        raise ValueError(f"tasks must be a unique nonempty subset of {supported_tasks} for this track")
    config["tasks"] = tasks
    config["repeats"] = _integer(config.get("repeats", 1), "repeats", minimum=1)
    methods = config.get("methods")
    if not isinstance(methods, list) or not methods:
        raise ValueError("methods must be a nonempty list")
    seen = set()
    for method in methods:
        _json_object(method, "method")
        method_id = method.get("id")
        if not isinstance(method_id, str) or not ID_PATTERN.fullmatch(method_id):
            raise ValueError("method.id may contain only letters, digits, underscores, hyphens")
        if method_id in seen:
            raise ValueError("method IDs must be unique")
        seen.add(method_id)
        if method.get("kind") not in {"llm", "geometry", "smoke", "b_random", "b_geometry"}:
            raise ValueError(f"{method_id}: unsupported method kind")
        if method["kind"] == "llm":
            from .providers import build_provider
            provider = _json_object(method.get("provider"), f"{method_id}.provider")
            if provider.get("type") not in PROVIDER_TYPES:
                raise ValueError(f"{method_id}: unsupported provider type")
            adapter = build_provider(provider)
            if not isinstance(method.get("model"), str) or not method["model"]:
                raise ValueError(f"{method_id}: model must be a nonempty string")
            method.setdefault("variant", "M3")
            if method["variant"] not in {"M3", "A1"}:
                raise ValueError(f"{method_id}: variant must be M3 or A1")
            method.setdefault("parameters", {})
            _json_object(method["parameters"], f"{method_id}.parameters")
            if provider["type"] == "codex":
                allowed_parameters = {"reasoning_effort"}
            elif provider["type"] == "gemini":
                allowed_parameters = {"temperature", "top_p", "max_output_tokens",
                                      "thinking_budget", "thinking_level", "seed"}
            else:
                allowed_parameters = {"temperature", "top_p", "max_output_tokens", "reasoning_effort", "seed"}
            if set(method["parameters"]) - allowed_parameters:
                raise ValueError(f"{method_id}: unsupported model parameters")
            if {"thinking_budget", "thinking_level"} <= method["parameters"].keys():
                raise ValueError(f"{method_id}: thinking_budget and thinking_level are mutually exclusive")
            for key, value in method["parameters"].items():
                if key == "temperature" and (type(value) not in (int, float) or not 0 <= value <= 2):
                    raise ValueError(f"{method_id}: temperature must be a number in [0, 2]")
                if key == "top_p" and (type(value) not in (int, float) or not 0 <= value <= 1):
                    raise ValueError(f"{method_id}: top_p must be a number in [0, 1]")
                if key == "max_output_tokens":
                    _integer(value, f"{method_id}.max_output_tokens", minimum=1)
                if key == "seed":
                    _integer(value, f"{method_id}.parameters.seed")
                if key == "thinking_budget":
                    _integer(value, f"{method_id}.thinking_budget", minimum=-1)
                if key == "thinking_level" and (not isinstance(value, str) or
                                                value not in {"minimal", "low", "medium", "high"}):
                    raise ValueError(f"{method_id}: thinking_level must be minimal, low, medium, or high")
                if key == "reasoning_effort" and (not isinstance(value, str) or not value):
                    raise ValueError(f"{method_id}: reasoning_effort must be a nonempty string")
            if getattr(adapter, "api", None) == "responses" and "seed" in method["parameters"]:
                raise ValueError(f"{method_id}: Responses API does not support decoding seed")
            method.setdefault("cache_mode", "read_write")
            if method["cache_mode"] not in CACHE_MODES:
                raise ValueError(f"{method_id}: unsupported cache_mode")
            method.setdefault("prompt_version", "v1")
            if method["prompt_version"] != "v1":
                raise ValueError(f"{method_id}: only prompt_version='v1' is implemented")
    config["cache_dir"] = _resolve_path(config.get("cache_dir", ".cache/grasp_c"),
                                        base, "cache_dir")
    config.setdefault("evaluation_scope", "pilot")
    if config["evaluation_scope"] != "pilot":
        raise ValueError("the current controller only supports evaluation_scope='pilot'")
    config.setdefault("seed_split", {"pilot_controller": list(range(10))})
    split = _json_object(config["seed_split"], "seed_split")
    for name, values in split.items():
        if not isinstance(values, list) or any(type(v) is not int for v in values):
            raise ValueError(f"seed_split.{name} must be an integer list")
        if len(values) != len(set(values)):
            raise ValueError(f"seed_split.{name} must be unique")
    if "evaluation" in split:
        if set(split["evaluation"]) != set(seeds):
            raise ValueError("seed_split.evaluation must equal the configured seeds")
        if set(split["evaluation"]) & set(split.get("pilot_controller", [])):
            raise ValueError("evaluation seeds must not overlap pilot_controller seeds")
    if "b_provenance" in config:
        config["b_provenance"] = _resolve_path(config["b_provenance"], base, "b_provenance")
        load_b_provenance(config["b_provenance"], config["candidates"])
    elif any(m["kind"] in {"b_geometry", "b_random"} for m in methods):
        raise ValueError("B methods require b_provenance from prepare-b scored inputs")
    if "acceptable_sets" in config:
        config["acceptable_sets"] = _resolve_path(config["acceptable_sets"], base,
                                                   "acceptable_sets")
        load_acceptable_sets(config["acceptable_sets"], {c.id for c in candidates},
                             supported_tasks=supported_tasks)
    json.dumps(config, allow_nan=False)
    return config


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    return validate_config(json.loads(path.read_text(encoding="utf-8")), base_dir=path.parent)


def load_acceptable_sets(path: str | Path, candidate_ids: set[str] | None = None, *,
                         supported_tasks=None) -> dict:
    """Read an independently supplied oracle. Its contents never enter a prompt."""
    data = _json_object(json.loads(Path(path).read_text(encoding="utf-8")), "acceptable_sets")
    for task, values in data.items():
        if task not in (supported_tasks or (*PREP_TASKS, *SEQUENCE_TASKS)):
            raise ValueError(f"acceptable_sets: unknown task {task}")
        groups = values.values() if isinstance(values, dict) else [values]
        if isinstance(values, dict) and any(
                key != "default" and not re.fullmatch(r"-?\d+", key) for key in values):
            raise ValueError("acceptable_sets seed keys must be integer strings or default")
        for ids in groups:
            if (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)
                    or len(ids) != len(set(ids))):
                raise ValueError("acceptable_sets entries must be unique lists of IDs")
            if candidate_ids is not None and not set(ids) <= candidate_ids:
                raise ValueError("acceptable_sets contains an unknown candidate ID")
    return data
