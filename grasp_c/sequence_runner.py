"""Resumable C evaluation of A's physical sequences, isolated from preparation."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from grasp_a.contracts import load_candidates
from .config import load_acceptable_sets, load_observation_config
from .providers import ProviderConfigurationError, build_provider
from .runner import (ERROR_STATUSES, _canonical, _check_manifest, _read_json, _run_lock,
                     _sha, _trial_path, atomic_json, build_provenance, selection_context, trial_plan)
from .selectors import LLMSelector, geometry_selector
from .sequence_trial import (SEQUENCE_EPISODE_STATUSES, SEQUENCE_TRIAL_SCHEMA,
                             check_success_evidence, evaluate_sequence_trial, get_sequence_candidates)
from .tasks import (SEQUENCE_CONTROLLER, SEQUENCE_EPISODE, SEQUENCE_EVALUATOR,
                    SEQUENCE_FILTER, SEQUENCE_SCENE, load_sequence_config, sequence_specs)
from .metrics import trial_identity

RUN_SCHEMA = "grasp_c_sequence_run_v1"
FILTER_SCHEMA = "grasp_c_sequence_filter_v1"
STATUSES = ERROR_STATUSES | SEQUENCE_EPISODE_STATUSES | {"no_feasible_candidate"}
# Physical task failures are retained. Retrying numerical failures also requires
# a new run if configuration changed; retry-errors only retries C call failures.
RETRYABLE = ERROR_STATUSES


def _condition(manifest, task, seed):
    return {"track": "sequence", "task_id": task, "seed": seed,
            **{key: manifest[key] for key in ("scene_version", "controller_version",
               "evaluation_version", "candidate_sha256", "observation_config")},
            "task_spec_sha256": manifest["task_specs"][task]["sha256"]}


def _require_fields(value, expected, label):
    for key, match in expected.items():
        if _canonical(value.get(key)) != _canonical(match):
            raise ValueError(f"{label} {key} mismatch")


def _filter_path(out, task, seed):
    return out / "filters" / task / f"seed{seed}.json"


def _check_filter(saved, manifest, task, seed):
    expected = _condition(manifest, task, seed)
    _require_fields(saved, dict(expected, schema_version=FILTER_SCHEMA,
                               manifest_sha256=manifest["manifest_sha256"]), "saved sequence filter")
    if saved.get("status") == "filter_error":
        if saved.get("filter") is not None or not isinstance(saved.get("error"), dict):
            raise ValueError("invalid sequence filter error record")
        return
    filtered = saved.get("filter")
    if not isinstance(filtered, dict) or saved.get("error") is not None:
        raise ValueError("invalid sequence filter payload")
    payload_expected = {key: value for key, value in expected.items() if key != "track"}
    payload_expected.update(schema_version=SEQUENCE_FILTER,
                            task_spec=manifest["task_specs"][task]["spec"])
    _require_fields(filtered, payload_expected, "sequence filter payload")
    snapshot = manifest["candidate_snapshot"]
    checks = filtered.get("checks")
    if (not isinstance(checks, list) or len(checks) != len(snapshot) or
            any(not isinstance(row, dict) or type(row.get("feasible")) is not bool for row in checks)):
        raise ValueError("sequence filter checks are invalid")
    ids = [row.get("candidate_id") for row in checks]
    if (any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids) or
            set(ids) != {c["id"] for c in snapshot}):
        raise ValueError("sequence filter candidate IDs mismatch")
    allowed = {row["candidate_id"] for row in checks if row["feasible"]}
    fields = ("id", "region", "approach", "gripper_width", "geometry_score")
    public = [{key: c[key] for key in fields} for c in snapshot if c["id"] in allowed]
    status = "ok" if public else "no_feasible_candidate"
    if (filtered.get("feasible_candidates") != public or
            filtered.get("status") != status or saved.get("status") != status):
        raise ValueError("sequence filter public fields/status mismatch")


def _check_record(record, manifest, item, saved_filter):
    _require_fields(record, dict(_condition(manifest, item["task_id"], item["seed"]),
        schema_version=SEQUENCE_TRIAL_SCHEMA, manifest_sha256=manifest["manifest_sha256"],
        task_spec=manifest["task_specs"][item["task_id"]]["spec"],
        sequence_config=manifest["sequence_config"], policy_mode="fixed",
        media_options={"enabled": manifest["run_options"]["media"],
                       "show_landmarks": manifest["run_options"]["show_landmarks"]}),
        "saved sequence trial")
    if trial_identity(record) != trial_identity(item) or record.get("status") not in STATUSES:
        raise ValueError("saved sequence trial identity/status mismatch")
    filtered = record.get("filter")
    if record["status"] == "filter_error":
        if filtered is not None or not isinstance(record.get("error"), dict):
            raise ValueError("invalid filter-error trial")
    elif _canonical(filtered) != _canonical(saved_filter["filter"]):
        raise ValueError("saved trial disagrees with shared task/seed filter")
    selection = record.get("selection") or {}
    selected = record.get("selected_id")
    if selected is not None:
        public = (filtered or {}).get("feasible_candidates", [])
        allowed = {c["id"] for c in public}
        if selected not in allowed or selection.get("selected_id") != selected:
            raise ValueError("saved selected ID is outside this filter")
        ranking = selection.get("ranking")
        if ranking is not None and (not isinstance(ranking, list) or
                any(not isinstance(key, str) for key in ranking) or len(ranking) != len(allowed) or
                set(ranking) != allowed or not ranking or ranking[0] != selected):
            raise ValueError("saved sequence ranking is invalid")
    if record["status"] in SEQUENCE_EPISODE_STATUSES:
        episode = record.get("episode")
        if not isinstance(episode, dict) or selected is None or type(episode.get("success")) is not bool:
            raise ValueError("sequence status requires a boolean-scored episode and selected ID")
        expected = {key: value for key, value in _condition(
            manifest, item["task_id"], item["seed"]).items() if key != "track"}
        expected.update(schema_version=SEQUENCE_EPISODE, status=record["status"],
                        selected_id=selected, task_spec=manifest["task_specs"][item["task_id"]]["spec"])
        _require_fields(episode, expected, "saved sequence episode")
        if episode["status"] != "completed" and episode["success"]:
            raise ValueError("non-completed sequence episode cannot be successful")
        check_success_evidence(episode)
        feasibility = episode.get("feasibility") or {}
        if (feasibility.get("candidate_id") != selected or type(feasibility.get("feasible")) is not bool or
                (episode["status"] == "rejected") == feasibility["feasible"]):
            raise ValueError("saved episode feasibility disagrees with the selected ID/status")
        if _canonical(episode.get("initial_object_pose_estimate")) != _canonical(
                (filtered or {}).get("initial_object_pose_estimate")):
            raise ValueError("saved sequence episode initial estimate mismatch")


def read_sequence_records(out, manifest):
    out = Path(out)
    _check_manifest(manifest)
    if manifest.get("schema_version") != RUN_SCHEMA or manifest.get("track") != "sequence":
        raise ValueError("not a supported sequence run")
    snapshot = manifest.get("candidate_snapshot")
    if not isinstance(snapshot, list) or _sha(snapshot) != manifest["candidate_sha256"]:
        raise ValueError("sequence candidate snapshot fingerprint mismatch")
    expected_trials = {_trial_path(out, p) for p in manifest["plan"]}
    if set((out / "trials").rglob("*.json")) - expected_trials:
        raise ValueError("unexpected sequence trial files")
    conditions = {(p["task_id"], p["seed"]) for p in manifest["plan"]}
    expected_filters = {_filter_path(out, task, seed) for task, seed in conditions}
    if set((out / "filters").rglob("*.json")) - expected_filters:
        raise ValueError("unexpected sequence filter files")
    filters = {}
    for task, seed in conditions:
        path = _filter_path(out, task, seed)
        if path.exists():
            saved = _read_json(path)
            _check_filter(saved, manifest, task, seed)
            filters[task, seed] = saved
    records = []
    for item in manifest["plan"]:
        path = _trial_path(out, item)
        if path.exists():
            saved_filter = filters.get((item["task_id"], item["seed"]))
            if saved_filter is None:
                raise ValueError("sequence trial has no shared task/seed filter")
            record = _read_json(path)
            _check_record(record, manifest, item, saved_filter)
            records.append(record)
    return records


def summarize_sequence_run(out):
    from .sequence_metrics import summarize_sequence_records
    out = Path(out).resolve()
    manifest = _read_json(out / "manifest.json")
    records = read_sequence_records(out, manifest)
    oracle = None
    if manifest["configuration"].get("acceptable_sets"):
        path = out / "acceptable_sets.json"
        oracle = load_acceptable_sets(path)
        if (_sha(oracle) != manifest["acceptable_sets_canonical_sha256"] or
                hashlib.sha256(path.read_bytes()).hexdigest() != manifest["acceptable_sets_snapshot_sha256"]):
            raise ValueError("acceptable-set snapshot mismatch")
    summary = summarize_sequence_records(records, manifest["plan"], acceptable_sets=oracle)
    archived = []
    planned = {trial_identity(item) for item in manifest["plan"]}
    for path in (out / "attempts").rglob("*.json"):
        record = _read_json(path)
        if (trial_identity(record) not in planned or
                record.get("manifest_sha256") != manifest["manifest_sha256"]):
            raise ValueError("archived sequence attempt provenance mismatch")
        archived.append(record)
    usage = Counter()
    logical = live = 0
    for record in records + archived:
        metadata = (record.get("selection") or {}).get("metadata", {})
        logical += metadata.get("requests", 0)
        live += metadata.get("live_requests", 0)
        usage.update({k: v for k, v in (metadata.get("live_usage") or {}).items()
                      if type(v) in (int, float)})
    summary["attempt_history"] = {"archived_attempt_n": len(archived),
        "archived_status_counts": dict(Counter(r["status"] for r in archived)),
        "current_plus_archived_attempt_n": len(records) + len(archived),
        "cumulative_logical_request_n": logical, "cumulative_live_request_n": live,
        "cumulative_live_token_usage": dict(usage) or None}
    summary.update(run_id=manifest["run_id"], manifest_sha256=manifest["manifest_sha256"],
        controller_version=manifest["controller_version"], evaluation_version=manifest["evaluation_version"],
        scene_version=manifest["scene_version"], candidate_sha256=manifest["candidate_sha256"],
        task_specs=manifest["task_specs"], observation_config=manifest["observation_config"],
        method_definitions=manifest["configuration"]["methods"],
        baseline_provenance=manifest["baseline_provenance"])
    atomic_json(out / "summary.json", summary)
    return summary


def run_sequence_evaluation(config, out, *, resume=False, retry_errors=False, media=False,
                            show_landmarks=True, progress=None):
    if retry_errors and not resume:
        raise ValueError("--retry-errors requires --resume")
    if type(show_landmarks) is not bool:
        raise ValueError("show_landmarks must be a boolean")
    observation = load_observation_config(config["observation_config"])
    config["observation_config"] = json.loads(json.dumps(asdict(observation)))
    sequence_config = load_sequence_config(config["sequence_config"])
    candidates = load_candidates(config["candidates"])
    specs = sequence_specs(config["tasks"], sequence_config)
    providers = {m["id"]: build_provider(m["provider"]) for m in config["methods"] if m["kind"] == "llm"}
    out = Path(out).resolve()
    with _run_lock(out):
        path = out / "manifest.json"
        previous = _read_json(path) if path.exists() else None
        if previous is not None and not resume:
            raise ValueError("run already exists; use --resume or a new output directory")
        if previous is None and resume:
            raise ValueError("cannot resume without manifest.json")
        existing = {trial_identity(r): r for r in read_sequence_records(out, previous)} if previous else {}
        plan = trial_plan(config)
        pending = [item for item in plan if trial_identity(item) not in existing or
                   (retry_errors and existing[trial_identity(item)]["status"] in RETRYABLE)]
        for method in config["methods"]:
            if (method["kind"] == "llm" and method["cache_mode"] != "read_only" and
                    any(item["method_id"] == method["id"] for item in pending)):
                providers[method["id"]].preflight()
        provenance = build_provenance(config, providers, media=media, out_dir=out)
        provenance.update(schema_version=RUN_SCHEMA, track="sequence", scene_version=SEQUENCE_SCENE,
            controller_version=SEQUENCE_CONTROLLER, evaluation_version=SEQUENCE_EVALUATOR,
            filter_schema=SEQUENCE_FILTER, episode_schema=SEQUENCE_EPISODE,
            sequence_config=asdict(sequence_config), task_specs=specs,
            candidate_snapshot=[asdict(c) for c in candidates],
            run_options={"media": media, "show_landmarks": show_landmarks},
            limitations=["B geometry uses known-mug analytic proxies, not hollow-vessel force closure.",
                         "This pilot compares ID-only selection with fixed A task sequences."])
        fingerprint = _sha(provenance)
        if previous is not None:
            if previous.get("manifest_sha256") != fingerprint:
                raise ValueError("resume refused: sequence configuration, source, assets, environment or provider changed")
            manifest = previous
        else:
            if any(p.name != ".run.lock" for p in out.iterdir()):
                raise ValueError("output directory is not empty")
            manifest = dict(provenance, manifest_sha256=fingerprint,
                created_at=datetime.now(timezone.utc).isoformat(), run_id=uuid4().hex)
            if config.get("acceptable_sets"):
                oracle = load_acceptable_sets(config["acceptable_sets"])
                atomic_json(out / "acceptable_sets.json", oracle)
                manifest["acceptable_sets_snapshot_sha256"] = hashlib.sha256(
                    (out / "acceptable_sets.json").read_bytes()).hexdigest()
            atomic_json(path, manifest)
        methods = {m["id"]: m for m in config["methods"]}
        selectors = {}
        for task in config["tasks"]:
            for seed in config["seeds"]:
                current = [item for item in pending if item["task_id"] == task and item["seed"] == seed]
                if not current:
                    continue
                filter_path = _filter_path(out, task, seed)
                saved = _read_json(filter_path) if filter_path.exists() else None
                if saved is None or (retry_errors and saved["status"] == "filter_error"):
                    try:
                        filtered = get_sequence_candidates(task, seed, candidates,
                            config=sequence_config, observation_config=observation)
                        saved = {"status": filtered["status"], "filter": filtered, "error": None}
                        saved.update(_condition(manifest, task, seed), schema_version=FILTER_SCHEMA,
                                     manifest_sha256=fingerprint)
                        _check_filter(saved, manifest, task, seed)
                    except Exception as exc:
                        saved = {"status": "filter_error", "filter": None,
                                 "error": {"type": type(exc).__name__, "message": str(exc)}}
                    saved.update(_condition(manifest, task, seed), schema_version=FILTER_SCHEMA,
                                 manifest_sha256=fingerprint)
                    _check_filter(saved, manifest, task, seed)
                    atomic_json(filter_path, saved)
                else:
                    _check_filter(saved, manifest, task, seed)
                for item in current:
                    method = methods[item["method_id"]]
                    if saved["status"] == "filter_error":
                        record = dict(_condition(manifest, task, seed), schema_version=SEQUENCE_TRIAL_SCHEMA,
                            evaluation_scope="pilot", method_id=method["id"], status="filter_error",
                            task_spec=manifest["task_specs"][task]["spec"],
                            sequence_config=manifest["sequence_config"], policy_mode="fixed",
                            media_options={"enabled": media, "show_landmarks": show_landmarks},
                            selected_id=None, selection=None, filter=None, episode=None, error=saved["error"])
                    else:
                        key = (method["id"], task, item["repeat"])
                        if key not in selectors:
                            if method["kind"] == "llm":
                                selectors[key] = LLMSelector(providers[method["id"]], method["model"],
                                    variant=method["variant"], parameters=method["parameters"],
                                    cache_dir=config["cache_dir"], cache_mode=method["cache_mode"],
                                    prompt_version=method["prompt_version"], sample_id=item["sample_id"],
                                    cache_context=selection_context(manifest, task))
                            elif method["kind"] in {"b_geometry", "b_random"}:
                                from .baselines import build_b_selector
                                selectors[key] = build_b_selector(method["kind"], manifest["baseline_provenance"])
                            elif method["kind"] == "geometry":
                                selectors[key] = geometry_selector
                            else:
                                selectors[key] = lambda task, public, seed: public[0]["id"]
                        record = evaluate_sequence_trial(task, seed, method["id"], candidates, selectors[key],
                            sequence_config=sequence_config, observation_config=observation,
                            filtered=saved["filter"], show_landmarks=show_landmarks,
                            media_dir=out / "media" / f"repeat{item['repeat']}" if media else None)
                    record.update(repeat=item["repeat"], sample_id=item["sample_id"],
                        run_id=manifest["run_id"], manifest_sha256=fingerprint, method_definition=method)
                    _check_record(record, manifest, item, saved)
                    old = existing.get(trial_identity(item))
                    if old is not None:
                        attempt = out / "attempts" / method["id"] / task / f"seed{seed}_repeat{item['repeat']}_{uuid4().hex}.json"
                        atomic_json(attempt, old)
                    atomic_json(_trial_path(out, item), record)
                    existing[trial_identity(item)] = record
                    if progress:
                        progress({"event": "trial_saved", **item, "status": record["status"],
                            "selected_id": record["selected_id"], "success": (record.get("episode") or {}).get("success"),
                            "path": str(_trial_path(out, item))})
                    if (record.get("error") or {}).get("type") == "ProviderConfigurationError":
                        summarize_sequence_run(out)
                        raise ProviderConfigurationError(record["error"]["message"], code="fatal_selector_configuration")
        return summarize_sequence_run(out)
