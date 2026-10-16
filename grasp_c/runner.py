"""Incremental, resumable pilot evaluation over shared per-seed feasibility."""
from __future__ import annotations

from contextlib import contextmanager
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Callable
from uuid import uuid4

from grasp_a.contracts import candidate_sha256, load_candidates
from .config import load_acceptable_sets, load_config, load_observation_config
from .metrics import summarize_records, trial_identity

ROOT = Path(__file__).resolve().parents[1]
ERROR_STATUSES = {"filter_error", "selector_error", "invalid_selection", "execution_error"}
RUN_SCHEMA = "grasp_c_run_v2"
FILTER_SCHEMA = "grasp_c_filter_v2"
TRIAL_SCHEMA = "evaluation_trial_v0.2"
EPISODE_SCHEMA = "episode_v0.3"
EVALUATION_VERSION = "fixed_target_hold_v2"


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def atomic_json(path: str | Path, value) -> None:
    """Never expose a partially written trial to resume or aggregation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _run_lock(directory: Path):
    """An OS lock also releases after a terminated process; the file is harmless."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another evaluation is using this output directory") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def trial_plan(config: dict) -> list[dict]:
    return [{"method_id": method["id"], "task_id": task, "seed": seed,
             "repeat": repeat, "sample_id": repeat}
            for seed in config["seeds"] for task in config["tasks"]
            for repeat in range(config["repeats"]) for method in config["methods"]]


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                text=True, timeout=15, check=False)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _file_hashes(paths) -> dict:
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths)) if path.is_file()}


def build_provenance(config: dict, providers: dict, *, media: bool, out_dir: Path) -> dict:
    """Pin all code/assets, input content, controller, provider identity and environment."""
    from grasp_a.simulation import CONTROLLER_VERSION
    sources = [path for name in ("grasp_a", "grasp_c", "examples")
               for path in (ROOT / name).rglob("*.py")]
    sources += [ROOT / name for name in ("requirements.txt", "requirements-c.txt", "environment.yml")]
    source_hashes = _file_hashes(sources)
    assets = _file_hashes((ROOT / "assets").rglob("*"))
    candidate_file = Path(config["candidates"])
    candidates = load_candidates(candidate_file)
    diff = _git("diff", "HEAD", "--binary")
    untracked = (_git("ls-files", "--others", "--exclude-standard") or "").splitlines()
    excluded = [out_dir.resolve(), Path(config["cache_dir"]).resolve()]
    untracked_hashes = {}
    for name in untracked:
        path = ROOT / name
        if path.is_file() and not any(path.resolve().is_relative_to(ex) for ex in excluded):
            untracked_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    versions = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            versions[name] = dist.version
    return {
        "schema_version": RUN_SCHEMA, "configuration": config,
        "configuration_sha256": _sha(config), "evaluation_scope": "pilot",
        "controller_version": CONTROLLER_VERSION,
        "evaluation_version": EVALUATION_VERSION, "policy_mode": config["policy_mode"],
        "observation_config": config["observation_config"],
        "candidate_sha256": candidate_sha256(candidates),
        "candidate_file_sha256": hashlib.sha256(candidate_file.read_bytes()).hexdigest(),
        "acceptable_sets_sha256": (hashlib.sha256(Path(config["acceptable_sets"]).read_bytes()).hexdigest()
                                   if config.get("acceptable_sets") else None),
        "acceptable_sets_canonical_sha256": (_sha(load_acceptable_sets(config["acceptable_sets"]))
                                             if config.get("acceptable_sets") else None),
        "source_sha256": _sha(source_hashes), "source_files": source_hashes,
        "assets_sha256": _sha(assets), "asset_files": assets,
        "git": {"commit": (_git("rev-parse", "HEAD") or "").strip() or None,
                "dirty": bool(diff or untracked_hashes),
                "dirty_sha256": _sha({"tracked_diff": diff, "untracked": untracked_hashes}),
                "untracked_file_hashes": untracked_hashes},
        "environment": {"python": platform.python_version(), "executable": sys.executable,
                        "platform": platform.platform(), "packages": dict(sorted(versions.items()))},
        "providers": {key: value.identity for key, value in sorted(providers.items())},
        "run_options": {"media": media}, "plan": trial_plan(config),
        "seed_split": config["seed_split"],
    }


def _trial_path(out: Path, item: dict) -> Path:
    return out / "trials" / item["method_id"] / item["task_id"] / f"seed{item['seed']}_repeat{item['repeat']}.json"


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def _check_manifest(manifest: dict) -> None:
    provenance = {key: value for key, value in manifest.items() if key not in
                  {"manifest_sha256", "created_at", "run_id", "acceptable_sets_snapshot_sha256"}}
    if _sha(provenance) != manifest.get("manifest_sha256"):
        raise ValueError("manifest contents do not match its fingerprint")


def _check_saved_filter(saved: dict, manifest: dict, seed: int) -> None:
    """Check the full shared condition even when all trials already completed."""
    expected = {"schema_version": FILTER_SCHEMA, "manifest_sha256": manifest["manifest_sha256"],
                "seed": seed, "controller_version": manifest["controller_version"],
                "candidate_sha256": manifest["candidate_sha256"],
                "evaluation_version": manifest["evaluation_version"], "policy_mode": manifest["policy_mode"],
                "observation_config": manifest["observation_config"]}
    for field, value in expected.items():
        if _canonical(saved.get(field)) != _canonical(value):
            raise ValueError(f"saved filter {field} mismatch")
    status = saved.get("status")
    if status == "filter_error":
        if saved.get("filter") is not None or not isinstance(saved.get("error"), dict):
            raise ValueError("saved filter error record is invalid")
        return
    filtered = saved.get("filter")
    if status not in {"ok", "no_feasible_candidate"} or not isinstance(filtered, dict):
        raise ValueError("saved filter status is invalid")
    for field in ("seed", "controller_version", "candidate_sha256", "observation_config"):
        if _canonical(filtered.get(field)) != _canonical(expected[field]):
            raise ValueError(f"saved filter payload {field} mismatch")
    public = filtered.get("feasible_candidates")
    if (filtered.get("status") != status or not isinstance(public, list) or
            (status == "ok") != bool(public) or saved.get("error") is not None):
        raise ValueError("saved filter payload status is inconsistent")


def _check_trial_protocol(record: dict, manifest: dict, path: Path) -> None:
    if record.get("schema_version") != TRIAL_SCHEMA:
        raise ValueError(f"saved trial schema mismatch: {path}")
    for field in ("evaluation_version", "policy_mode", "observation_config"):
        if _canonical(record.get(field)) != _canonical(manifest[field]):
            raise ValueError(f"saved trial {field} mismatch: {path}")
    episode = record.get("episode")
    if record.get("status") == "completed":
        if not isinstance(episode, dict) or episode.get("schema_version") != EPISODE_SCHEMA:
            raise ValueError(f"saved episode schema mismatch: {path}")
        for field in ("controller_version", "evaluation_version", "policy_mode", "observation_config"):
            if _canonical(episode.get(field)) != _canonical(manifest[field]):
                raise ValueError(f"saved episode {field} mismatch: {path}")


def read_run_records(out: str | Path, manifest: dict) -> list[dict]:
    out = Path(out)
    _check_manifest(manifest)
    records = []
    expected_paths = {_trial_path(out, item) for item in manifest["plan"]}
    unexpected = set((out / "trials").rglob("*.json")) - expected_paths
    if unexpected:
        raise ValueError("unexpected trial files in the run directory")
    # Historical runs can still be summarized with their original protocol.
    # They cannot resume because their provenance differs from RUN_SCHEMA.
    if manifest.get("schema_version") == RUN_SCHEMA:
        planned_seeds = {item["seed"] for item in manifest["plan"]}
        expected_filters = {out / "filters" / f"seed{seed}.json" for seed in planned_seeds}
        if set((out / "filters").glob("*.json")) - expected_filters:
            raise ValueError("unexpected saved filter files in the run directory")
        for seed in planned_seeds:
            filter_path = out / "filters" / f"seed{seed}.json"
            if filter_path.exists():
                _check_saved_filter(_read_json(filter_path), manifest, seed)
    for item in manifest["plan"]:
        path = _trial_path(out, item)
        if path.exists():
            record = _read_json(path)
            if trial_identity(record) != trial_identity(item):
                raise ValueError(f"saved trial identity mismatch: {path}")
            if record.get("manifest_sha256") != manifest["manifest_sha256"]:
                raise ValueError(f"saved trial manifest mismatch: {path}")
            for field in ("candidate_sha256", "controller_version"):
                if record.get(field) != manifest[field]:
                    raise ValueError(f"saved trial {field} mismatch: {path}")
            if record.get("status") not in ERROR_STATUSES | {"completed", "no_feasible_candidate"}:
                raise ValueError(f"unknown saved trial status: {path}")
            if manifest.get("schema_version") == RUN_SCHEMA:
                _check_trial_protocol(record, manifest, path)
                filter_path = out / "filters" / f"seed{item['seed']}.json"
                if not filter_path.exists():
                    raise ValueError(f"saved trial has no shared filter: {path}")
            records.append(record)
    return records


def summarize_run(out: str | Path) -> dict:
    """Rebuild a summary from saved trials; this never calls any model or simulator."""
    out = Path(out).resolve()
    manifest = _read_json(out / "manifest.json")
    records = read_run_records(out, manifest)
    oracle_path = manifest["configuration"].get("acceptable_sets")
    oracle = None
    if oracle_path:
        # The run captures the independent labels, allowing summaries after moving hosts.
        snapshot = out / "acceptable_sets.json"
        oracle = load_acceptable_sets(snapshot)
        if _sha(oracle) != manifest["acceptable_sets_canonical_sha256"]:
            raise ValueError("acceptable-set labels disagree with signed run provenance")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != manifest["acceptable_sets_snapshot_sha256"]:
            raise ValueError("acceptable-set snapshot changed since the run began")
    summary = summarize_records(records, manifest["plan"], acceptable_sets=oracle)
    archived = []
    expected_ids = {trial_identity(p) for p in manifest["plan"]}
    for path in sorted((out / "attempts").rglob("*.json")):
        record = _read_json(path)
        if (trial_identity(record) not in expected_ids or
                record.get("manifest_sha256") != manifest["manifest_sha256"]):
            raise ValueError(f"archived attempt provenance mismatch: {path}")
        archived.append(record)
    live_tokens = Counter()
    logical_requests = live_requests = 0
    for record in records + archived:
        metadata = (record.get("selection") or {}).get("metadata", {})
        logical_requests += metadata.get("requests", 0)
        live_requests += metadata.get("live_requests", 0)
        usage = metadata.get("live_usage") or {}
        live_tokens.update({key: value for key, value in usage.items() if type(value) in (int, float)})
    summary["attempt_history"] = {
        "archived_attempt_n": len(archived),
        "archived_status_counts": dict(sorted(Counter(r["status"] for r in archived).items())),
        "current_plus_archived_attempt_n": len(records) + len(archived),
        "cumulative_logical_request_n": logical_requests,
        "cumulative_live_request_n": live_requests,
        "cumulative_live_token_usage": dict(live_tokens) or None,
        "scope": "saved_final_and_archived_attempts_only",
    }
    summary.update(run_id=manifest["run_id"], manifest_sha256=manifest["manifest_sha256"],
                   controller_version=manifest["controller_version"],
                   candidate_sha256=manifest["candidate_sha256"],
                   method_definitions=manifest["configuration"]["methods"])
    for field in ("evaluation_version", "policy_mode", "observation_config"):
        if field in manifest:
            summary[field] = manifest[field]
    atomic_json(out / "summary.json", summary)
    return summary


def _filter_error(item: dict, manifest: dict, error: dict) -> dict:
    return {"schema_version": TRIAL_SCHEMA, "evaluation_scope": "pilot",
            "controller_version": manifest["controller_version"],
            "candidate_sha256": manifest["candidate_sha256"],
            "evaluation_version": manifest["evaluation_version"], "policy_mode": manifest["policy_mode"],
            "observation_config": manifest["observation_config"],
            "method_id": item["method_id"], "task_id": item["task_id"], "seed": item["seed"],
            "status": "filter_error", "selected_id": None, "selection": None,
            "filter": None, "episode": None, "error": error}


def run_evaluation(config_path: str | Path, out: str | Path, *, resume: bool = False,
                   retry_errors: bool = False, media: bool = False,
                   progress: Callable[[dict], None] | None = None) -> dict:
    """Run missing trials; saved errors are retried only on explicit request."""
    if retry_errors and not resume:
        raise ValueError("--retry-errors requires --resume")
    config = load_config(config_path)
    observation_config = load_observation_config(config["observation_config"])
    # Reuse this exact validated instance throughout the run. Normalizing an
    # arbitrary quaternion again can change its final floating-point bit; sign
    # the actual runtime configuration rather than the pre-reconstruction JSON.
    config["observation_config"] = json.loads(json.dumps(asdict(observation_config)))
    from .providers import ProviderConfigurationError, build_provider
    from .selectors import LLMSelector, geometry_selector
    from examples.evaluate_one_trial import evaluate_trial, smoke_selector
    from grasp_a.simulation import get_feasible_candidates
    providers = {m["id"]: build_provider(m["provider"]) for m in config["methods"] if m["kind"] == "llm"}
    out = Path(out).resolve()
    with _run_lock(out):
        manifest_path = out / "manifest.json"
        previous = _read_json(manifest_path) if manifest_path.exists() else None
        if previous is not None and not resume:
            raise ValueError("run already exists; use --resume or a new output directory")
        if previous is None and resume:
            raise ValueError("cannot resume a directory without manifest.json")
        provisional = ({trial_identity(r): r for r in read_run_records(out, previous)}
                       if previous is not None else {})
        # Validate all live backends before any paid/model/physical trial begins.
        # Pure cache replay and an already finished resume need no API credentials.
        for method in config["methods"]:
            if method["kind"] != "llm" or method["cache_mode"] == "read_only":
                continue
            pending = any(p["method_id"] == method["id"] and
                          (trial_identity(p) not in provisional or
                           (retry_errors and provisional[trial_identity(p)]["status"] in ERROR_STATUSES))
                          for p in trial_plan(config))
            if pending:
                providers[method["id"]].preflight()
        provenance = build_provenance(config, providers, media=media, out_dir=out)
        provenance_hash = _sha(provenance)
        if previous is not None:
            manifest = previous
            if manifest.get("manifest_sha256") != provenance_hash:
                raise ValueError("resume refused: configuration, source, assets, environment, or provider identity changed")
        else:
            if any(p.name != ".run.lock" for p in out.iterdir()):
                raise ValueError("output directory is not empty; choose a new directory")
            manifest = dict(provenance, manifest_sha256=provenance_hash,
                            created_at=datetime.now(timezone.utc).isoformat(), run_id=uuid4().hex)
            if config.get("acceptable_sets"):
                oracle = load_acceptable_sets(config["acceptable_sets"])
                atomic_json(out / "acceptable_sets.json", oracle)
                manifest["acceptable_sets_snapshot_sha256"] = hashlib.sha256(
                    (out / "acceptable_sets.json").read_bytes()).hexdigest()
            atomic_json(manifest_path, manifest)
        existing = {trial_identity(r): r for r in read_run_records(out, manifest)}
        candidates = load_candidates(config["candidates"])
        methods = {m["id"]: m for m in config["methods"]}
        selectors = {}
        for seed in config["seeds"]:
            pending = [p for p in manifest["plan"] if p["seed"] == seed and
                       (trial_identity(p) not in existing or
                        (retry_errors and existing[trial_identity(p)]["status"] in ERROR_STATUSES))]
            filter_path = out / "filters" / f"seed{seed}.json"
            saved_filter = _read_json(filter_path) if filter_path.exists() else None
            if saved_filter is not None:
                _check_saved_filter(saved_filter, manifest, seed)
            if not pending:
                continue
            if saved_filter is None or (retry_errors and saved_filter["status"] == "filter_error"):
                try:
                    filtered = get_feasible_candidates(seed, candidates, observation_config=observation_config)
                    saved_filter = {"status": filtered["status"], "filter": filtered, "error": None}
                except Exception as exc:
                    saved_filter = {"status": "filter_error", "filter": None,
                                    "error": {"type": type(exc).__name__, "message": str(exc)}}
                saved_filter.update(schema_version=FILTER_SCHEMA, seed=seed,
                                    controller_version=manifest["controller_version"],
                                    candidate_sha256=manifest["candidate_sha256"],
                                    evaluation_version=manifest["evaluation_version"],
                                    policy_mode=manifest["policy_mode"],
                                    observation_config=config["observation_config"],
                                    manifest_sha256=provenance_hash)
                _check_saved_filter(saved_filter, manifest, seed)
                atomic_json(filter_path, saved_filter)
            for item in pending:
                method = methods[item["method_id"]]
                if saved_filter["status"] == "filter_error":
                    record = _filter_error(item, manifest, saved_filter["error"])
                else:
                    selector_key = (method["id"], item["repeat"])
                    if selector_key not in selectors:
                        if method["kind"] == "llm":
                            selectors[selector_key] = LLMSelector(
                                providers[method["id"]], method["model"], variant=method["variant"],
                                parameters=method["parameters"], cache_dir=config["cache_dir"],
                                cache_mode=method["cache_mode"], prompt_version=method["prompt_version"],
                                sample_id=item["sample_id"])
                        else:
                            selectors[selector_key] = geometry_selector if method["kind"] == "geometry" else smoke_selector
                    media_dir = out / "media" / f"repeat{item['repeat']}" if media else None
                    record = evaluate_trial(item["task_id"], seed, method["id"], candidates,
                                            selectors[selector_key], filtered=saved_filter["filter"],
                                            media_dir=media_dir, observation_config=observation_config)
                record.update(evaluation_version=manifest["evaluation_version"],
                              policy_mode=manifest["policy_mode"])
                if record["status"] == "completed":
                    try:
                        _check_trial_protocol(record, manifest, _trial_path(out, item))
                    except ValueError as exc:
                        record.update(status="execution_error", error={"type": type(exc).__name__,
                                                                      "message": str(exc)})
                record.update(repeat=item["repeat"], sample_id=item["sample_id"],
                              run_id=manifest["run_id"], manifest_sha256=provenance_hash,
                              method_definition=method)
                path = _trial_path(out, item)
                old = existing.get(trial_identity(item))
                if old is not None:
                    # Keep previous paid attempts for audit rather than overwriting their only record.
                    attempt_dir = out / "attempts" / method["id"] / item["task_id"]
                    attempt_path = attempt_dir / f"seed{seed}_repeat{item['repeat']}_{uuid4().hex}.json"
                    atomic_json(attempt_path, old)
                atomic_json(path, record)
                existing[trial_identity(item)] = record
                if progress:
                    progress({"event": "trial_saved", **item, "status": record["status"],
                              "selected_id": record["selected_id"],
                              "success": (record["episode"] or {}).get("success"), "path": str(path)})
                if (record.get("error") or {}).get("type") == "ProviderConfigurationError":
                    summarize_run(out)
                    raise ProviderConfigurationError(record["error"]["message"],
                                                     code="fatal_selector_configuration")
        return summarize_run(out)
