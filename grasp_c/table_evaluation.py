"""Audited C evaluation against a frozen oracle table; no outcomes reach selectors.

python -m grasp_c.table_evaluation {run,summarize,pbf} --help
The historical table remains read-only. New model responses and full PBF episodes
live in a separate run, bound to input, code, environment and provider fingerprints.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import importlib.metadata
import itertools
import json
import math
from pathlib import Path
import platform
import time

from grasp_a.contracts import candidate_sha256, load_candidates
from .config import load_config
from .oracle import sign_test, wilson
from .prompts import build_messages, public_snapshot
from .providers import build_provider
from .runner import ROOT, _file_hashes, _git, _sha, atomic_json, _run_lock, trial_plan
from .selectors import LLMSelector, validate_response
from .tasks import sequence_specs, load_sequence_config

BASELINES = ("M0_RANDOM", "M1_STATIC_V2", "M2_TASK_RISK", "M4_FAMILY_PRIOR", "M5_PAPER_STYLE")
SCHEMA = "grasp_c_table_run_v1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seal(record):
    return {**record, "record_sha256": _sha(record)}


def checked(path):
    record = read(path)
    value = {k: v for k, v in record.items() if k != "record_sha256"}
    if record.get("record_sha256") != _sha(value):
        raise ValueError(f"record fingerprint mismatch: {path}")
    return record


def trial_path(out, item):
    return out / "trials" / item["method_id"] / item["task_id"] / f"seed{item['seed']}.json"


def candidate_fingerprints(path):
    """Audit the pre-3.12 sequential float sum used by historical Pose loading.

    Python 3.12 changed float sum accuracy. Reconstruct that exact old operation,
    never round arbitrary mismatches away. The source file digest is also checked.
    This compatibility is confined to archived tables, not A's live contracts.
    """
    loaded = load_candidates(path)
    legacy = [asdict(c) for c in loaded]
    for raw, row in zip(read(path), legacy):
        for name in ("grasp_pose", "pregrasp_pose"):
            q = [float(x) for x in raw[name]["quaternion"]]
            squared = 0.0
            for x in q:
                squared += x*x
            row[name]["quaternion"] = [x/math.sqrt(squared) for x in q]
    return {"runtime": candidate_sha256(loaded), "sequential_float_sum": _sha(legacy)}


def load_table(source, config):
    """Validate identities, public inputs and every referenced outcome before use."""
    meta = read(source / "meta.json")
    candidates = load_candidates(source / "b_inputs/candidates.scored.json")
    b_provenance = read(source / "b_inputs/provenance.json")
    fingerprints = candidate_fingerprints(source / "b_inputs/candidates.scored.json")
    if (b_provenance.get("candidate_file_sha256") != digest(source / "b_inputs/candidates.scored.json")
            or b_provenance.get("families_sha256") != digest(source / "b_inputs/families.internal.json")
            or b_provenance.get("candidate_sha256") not in fingerprints.values()):
        raise ValueError("archived B provenance does not match candidate/family content")
    if candidate_sha256(candidates) != candidate_sha256(load_candidates(config["candidates"])):
        raise ValueError("configuration and table candidates differ")
    if config["track"] != "sequence" or config["repeats"] != 1:
        raise ValueError("table evaluation requires sequence track and one sample per seed")
    if config.get("scene_randomization") != meta["scene"]:
        raise ValueError("configuration and table scenes differ")
    if not set(config["seeds"]) <= set(meta["seeds"]) or not set(config["tasks"]) <= set(meta["tasks"]):
        raise ValueError("requested task/seed absent from table")
    if set(config["seeds"]) & set(meta.get("m4_prior", {}).get("seeds", [])):
        raise ValueError("evaluation seeds overlap M4 training seeds")
    if any(m["kind"] != "llm" or m["id"] in BASELINES for m in config["methods"]):
        raise ValueError("configure only additional LLM methods with distinct IDs")
    families = {r["id"]: r for r in read(source / "b_inputs/families.internal.json")}
    public_by_id = {c.id: c.public_metadata() for c in candidates}
    if set(families) != set(public_by_id):
        raise ValueError("family IDs differ from candidate IDs")
    files = [source / p for p in ("meta.json", "selections.json", "b_inputs/candidates.scored.json",
                                   "b_inputs/families.internal.json", "b_inputs/provenance.json")]
    filters, episodes = {}, {}
    specs = sequence_specs(config["tasks"], load_sequence_config(config["sequence_config"]))
    for task, seed in itertools.product(config["tasks"], config["seeds"]):
        path = source / "filters" / task / f"seed{seed}.json"
        f = read(path)
        expected = dict(task_id=task, seed=seed, candidate_sha256=b_provenance["candidate_sha256"],
                        scene_randomization=meta["scene"], task_spec_sha256=specs[task]["sha256"],
                        controller_version="staged_sequences_v1", evaluation_version="physical_outcomes_v1")
        if any(f.get(k) != v for k, v in expected.items()):
            raise ValueError(f"filter identity mismatch: {path}")
        public = public_snapshot(f["feasible_candidates"]) if f["feasible_candidates"] else []
        if any(r != public_by_id.get(r["id"]) for r in public):
            raise ValueError(f"filter public fields differ from frozen candidates: {path}")
        filters[task, seed] = f
        files.append(path)
        for r in public:
            ep_path = source / "episodes" / task / f"seed{seed}" / f"{r['id']}.json"
            if not ep_path.exists():
                continue  # Missing outcomes remain missing, never fabricated failures.
            e = read(ep_path)
            if (e.get("task_id"), e.get("seed"), e.get("candidate_id")) != (task, seed, r["id"]):
                raise ValueError(f"episode identity mismatch: {ep_path}")
            if type(e.get("success")) is not bool or (e["success"] and e.get("status") != "completed"):
                raise ValueError(f"invalid episode outcome: {ep_path}")
            if task == "pour_water" and e.get("liquid") != meta["liquid"]:
                raise ValueError(f"mixed liquid modes: {ep_path}")
            episodes[task, seed, r["id"]] = e
            files.append(ep_path)
    selections = read(source / "selections.json")
    for (task, seed), f in filters.items():
        ids = [r["id"] for r in f["feasible_candidates"]]
        for method in BASELINES:
            s = selections.get(task, {}).get(str(seed), {}).get(method)
            if not s or s.get("error") or not ids:
                continue
            ranking = s.get("ranking", [])
            _, errors = validate_response(json.dumps(dict(selected_id=ranking[0] if ranking else None,
                                                          ranking=ranking)), ids)
            if errors:
                raise ValueError(f"invalid baseline ranking: {task}/{seed}/{method}")
    hashes = {str(p.relative_to(source)): digest(p) for p in sorted(files)}
    return dict(meta=meta, families=families, filters=filters, episodes=episodes,
                selections=selections, files=hashes, candidate_fingerprints=fingerprints)


def physical_success(episode):
    """Exclude invocation/numerical failures; retain physical execution failures."""
    if episode is None or episode.get("status") not in {"completed", "execution_failed"}:
        return None
    water = (episode.get("final_evidence") or {}).get("water") or {}
    if water.get("finite_state") is False:
        return None
    if (episode.get("success_checks") or {}).get("fluid_density_valid") is not True:
        return None
    return episode["success"] if type(episode.get("success")) is bool else None


def provenance(config, source, table, providers):
    files = [p for name in ("grasp_a", "grasp_b", "grasp_c", "assets")
             for p in (ROOT / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts
             and p.suffix in {".py", ".urdf", ".obj", ".mtl"}]
    return dict(schema_version=SCHEMA, config=config, source=str(source),
                source_files=table["files"], source_sha256=_sha(table["files"]),
                candidate_fingerprints=table["candidate_fingerprints"],
                code_and_assets=_file_hashes(files),
                environment={"python": platform.python_version(), "platform": platform.platform(),
                             "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                                          if d.metadata.get("Name")}},
                providers={k: p.identity for k, p in providers.items()},
                historical_table_environment="not recorded by original oracle runner",
                plan=trial_plan(config))


def select_one(item, method, public, manifest):
    started = time.perf_counter()
    record = {**item, "manifest_sha256": manifest["manifest_sha256"],
              "input_sha256": _sha(public), "selection": None, "error": None}
    messages = build_messages(item["task_id"], public, variant=method["variant"],
                              prompt_version=method["prompt_version"]) if public else []
    record["messages"] = messages
    if not public:
        record["status"] = "no_feasible_candidate"
    else:
        context = dict(manifest_sha256=manifest["manifest_sha256"], task_id=item["task_id"],
                       source_sha256=manifest["identity"]["source_sha256"])
        selector = LLMSelector(build_provider(method["provider"]), method["model"],
                               variant=method["variant"], parameters=method["parameters"],
                               cache_dir=manifest["identity"]["config"]["cache_dir"],
                               cache_mode=method["cache_mode"], prompt_version=method["prompt_version"],
                               cache_context=context)
        try:
            record["selection"] = selector(item["task_id"], public, item["seed"])
            record["status"] = "selected"
        except Exception as exc:
            record["status"] = "selector_error"
            record["error"] = dict(type=type(exc).__name__, message=str(exc), code=getattr(exc, "code", None))
    record["seconds"] = time.perf_counter() - started
    return seal(record)


def verify_trial(record, item, public, manifest):
    expected = {**item, "manifest_sha256": manifest["manifest_sha256"], "input_sha256": _sha(public)}
    if any(record.get(k) != v for k, v in expected.items()):
        raise ValueError("saved trial identity mismatch")
    if record["status"] == "selected":
        selection = record["selection"]
        _, errors = validate_response(json.dumps({k: selection.get(k) for k in ("selected_id", "ranking")}),
                                      [r["id"] for r in public])
        if errors:
            raise ValueError("saved trial ranking invalid")
    elif record["status"] not in {"selector_error", "no_feasible_candidate"}:
        raise ValueError("unknown saved trial status")


def run(config_path, source, out, *, resume=False, workers=2):
    config = load_config(config_path)
    table = load_table(source, config)
    providers = {m["id"]: build_provider(m["provider"]) for m in config["methods"]}
    identity = provenance(config, source, table, providers)
    with _run_lock(out):
        path = out / "manifest.json"
        if path.exists():
            if not resume:
                raise ValueError("output exists; use --resume or a new directory")
            manifest = checked(path)
            if manifest["identity"] != identity:
                raise ValueError("resume identity changed (input/code/environment/provider); use a new directory")
        else:
            if resume:
                raise ValueError("cannot resume without a manifest")
            manifest = seal(dict(identity=identity, manifest_sha256=_sha(identity),
                                 created_at=datetime.now(timezone.utc).isoformat(),
                                 git_commit=(_git("rev-parse", "HEAD") or "").strip()))
            atomic_json(path, manifest)
        pending = []
        for item in identity["plan"]:
            public = table["filters"][item["task_id"], item["seed"]]["feasible_candidates"]
            path = trial_path(out, item)
            if path.exists():
                verify_trial(checked(path), item, public, manifest)
            else:
                pending.append((item, public))
        # Completed resumes require no credentials and make no model requests.
        if pending:
            for provider in providers.values():
                provider.preflight()
        methods = {m["id"]: m for m in config["methods"]}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(select_one, i, methods[i["method_id"]], p, manifest): i
                       for i, p in pending}
            for n, future in enumerate(as_completed(futures), 1):
                item, record = futures[future], future.result()
                atomic_json(trial_path(out, item), record)
                md = (record.get("selection") or {}).get("metadata", {})
                print(f"[{n}/{len(pending)}] {item['method_id']} {item['task_id']} seed{item['seed']}: "
                      f"{record['status']} fallback={md.get('fallback_to_geometry')}", flush=True)
        return summarize(out)


def rate_summary(values, planned):
    valid = [v for v in values if type(v) is bool]
    k, n = sum(valid), len(valid)
    return dict(successes=k, scored_n=n, planned_n=planned, excluded_n=planned-n,
                success_rate=k/n if n else None, ci95=wilson(k, n),
                end_to_end_success_coverage=k/planned if planned else None)


def paired(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if type(x) is bool and type(y) is bool]
    wins = sum(x and not y for x, y in pairs)
    losses = sum(y and not x for x, y in pairs)
    return dict(paired_n=len(pairs), excluded_n=len(a)-len(pairs), wins=wins, losses=losses,
                difference=(wins-losses)/len(pairs) if pairs else None,
                p_exact=sign_test(wins, losses) if pairs else None)


def holm(comparisons):
    """Correct within the explicitly reported comparison family."""
    valid = sorted((r for r in comparisons if r["p_exact"] is not None), key=lambda r: r["p_exact"])
    previous = 0.0
    for i, row in enumerate(valid):
        previous = max(previous, min(1., (len(valid)-i)*row["p_exact"]))
        row["p_holm"] = previous


def load_run(out):
    manifest = checked(out / "manifest.json")
    if manifest["manifest_sha256"] != _sha(manifest["identity"]):
        raise ValueError("manifest identity fingerprint mismatch")
    config = manifest["identity"]["config"]
    table = load_table(Path(manifest["identity"]["source"]), config)
    if table["files"] != manifest["identity"]["source_files"]:
        raise ValueError("frozen table was modified")
    trials = {}
    for item in manifest["identity"]["plan"]:
        path = trial_path(out, item)
        if path.exists():
            record = checked(path)
            verify_trial(record, item, table["filters"][item["task_id"], item["seed"]]["feasible_candidates"], manifest)
            trials[item["task_id"], item["seed"], item["method_id"]] = record
    return manifest, table, trials


def picks_for(task, seed, methods, table, trials):
    picks = {}
    for method in methods:
        if method in BASELINES:
            s = table["selections"].get(task, {}).get(str(seed), {}).get(method, {})
        else:
            s = (trials.get((task, seed, method), {}).get("selection") or {})
        ranking = s.get("ranking")
        picks[method] = ranking[0] if ranking else None
    return picks


def load_pbf_table(pbf_source, config, base_table):
    """Validate an archived full-PBF pour_water oracle table against base_table."""
    pbf_source = Path(pbf_source).resolve()
    pbf_config = {**config, "tasks": ["pour_water"]}
    pbf_table = load_table(pbf_source, pbf_config)
    if pbf_table["meta"].get("liquid") != "pbf":
        raise ValueError("pbf_table must use liquid='pbf'")
    if (pbf_table["candidate_fingerprints"] != base_table["candidate_fingerprints"]
            or pbf_table["families"] != base_table["families"]):
        raise ValueError("pbf_table candidate or family definitions differ from base_table")
    for seed in config["seeds"]:
        if (pbf_table["filters"]["pour_water", seed]["feasible_candidates"] !=
                base_table["filters"]["pour_water", seed]["feasible_candidates"]):
            raise ValueError(f"pbf_table pour_water filter differs at seed {seed}")
    return pbf_table


def summarize(out, *, pbf_table=None, summary_path=None, report_path=None):
    manifest, table, trials = load_run(out)
    pbf_manifest = checked(out / "pbf_manifest.json") if (out / "pbf_manifest.json").exists() else None
    if pbf_manifest and pbf_manifest["identity"]["selection_manifest_sha256"] != manifest["manifest_sha256"]:
        raise ValueError("PBF manifest belongs to different selections")
    config = manifest["identity"]["config"]
    loaded_pbf_table = (load_pbf_table(pbf_table, config, table)
                        if pbf_table is not None and "pour_water" in config["tasks"] else None)
    methods = [*BASELINES, *(m["id"] for m in config["methods"])]
    notes = ["One response per method/task/seed; no repeated-model stability claim.",
             "M4 training seeds excluded; seeds 10-29 already appeared in upstream analysis, not a blind test.",
             "Proxy pouring is separate from PBF; no particle-fluid Oracle is available."
             if loaded_pbf_table is None else
             "Proxy pouring is separate from PBF; pour_water_pbf_table reports full PBF table lookup and Oracle.",
             "Historical table lacks environment fingerprints; table lookup is retrospective."]
    result = dict(schema_version="grasp_c_table_summary_v1", manifest_sha256=manifest["manifest_sha256"],
                  seeds=config["seeds"], methods=methods, tasks={}, selection_audit={}, comparisons=[],
                  notes=notes)
    if loaded_pbf_table is not None:
        result["pbf_table_source"] = str(Path(pbf_table).resolve())
        result["pbf_table_sha256"] = _sha(loaded_pbf_table["files"])
    for method in config["methods"]:
        records = [r for (t, s, m), r in trials.items() if m == method["id"]]
        metadata = [(r.get("selection") or {}).get("metadata", {}) for r in records]
        calls = [c for md in metadata for c in md.get("calls", [])]
        result["selection_audit"][method["id"]] = dict(
            planned_n=len(config["seeds"])*len(config["tasks"]), saved_n=len(records),
            selected_n=sum(r["status"] == "selected" for r in records),
            errors=sum(r["status"] == "selector_error" for r in records),
            live_requests=sum(md.get("live_requests", 0) for md in metadata),
            cache_hits=sum(md.get("cache_hits", 0) for md in metadata),
            initial_valid_n=sum(c.get("validation", {}).get("valid", False) for c in calls
                                if c["phase"] == "initial" and c.get("validation")),
            repairs=sum(md.get("repair_attempts", 0) for md in metadata),
            fallbacks=sum(md.get("fallback_to_geometry", False) for md in metadata),
            live_token_usage=dict(sum((Counter(md.get("live_usage", {})) for md in metadata), Counter())),
            mean_selection_seconds=sum(r["seconds"] for r in records)/len(records) if records else None,
            synthetic=method["provider"]["type"] == "mock")
    views = [(task, "table") for task in config["tasks"]]
    if "pour_water" in config["tasks"]:
        views.append(("pour_water", "pbf"))
        if loaded_pbf_table is not None:
            views.append(("pour_water", "pbf_table"))
    for task, mode in views:
        active_table = loaded_pbf_table if mode == "pbf_table" else table
        rows, scores = [], {m: [] for m in methods}
        oracles, uniform = [], []
        for seed in config["seeds"]:
            picks = picks_for(task, seed, methods, active_table, trials)
            row = dict(seed=seed, methods={})
            for method, cid in picks.items():
                episode = active_table["episodes"].get((task, seed, cid)) if mode in ("table", "pbf_table") else None
                if cid and mode == "pbf":
                    path = out / "pbf" / f"seed{seed}" / f"{cid}.json"
                    if path.exists():
                        saved = checked(path)
                        if pbf_manifest is None:
                            raise ValueError("PBF results require their execution manifest")
                        verify_pbf(saved, manifest, seed, cid, pbf_manifest["record_sha256"])
                        episode = saved["episode"]
                value = physical_success(episode)
                scores[method].append(value)
                md = (trials.get((task, seed, method), {}).get("selection") or {}).get("metadata", {})
                row["methods"][method] = dict(selected_id=cid, success=value,
                    family=active_table["families"][cid]["family"] if cid else None,
                    failure_reason=episode.get("failure_reason") if episode else None,
                    episode_status=episode.get("status") if episode else None,
                    fallback=md.get("fallback_to_geometry", False))
            feasible = active_table["filters"][task, seed]["feasible_candidates"]
            values = [physical_success(active_table["episodes"].get((task, seed, r["id"]))) for r in feasible]
            # No oracle assertion without a complete, numerically valid table.
            complete = bool(values) and all(type(v) is bool for v in values)
            has_table_oracle = mode in ("table", "pbf_table")
            oracles.append(any(values) if complete and has_table_oracle else None)
            uniform.append(sum(values)/len(values) if complete and has_table_oracle else None)
            rows.append(row)
        key = task if mode == "table" else ("pour_water_pbf" if mode == "pbf" else "pour_water_pbf_table")
        groups = {}
        for method, values in scores.items():
            group = rate_summary(values, len(config["seeds"]))
            group["selected_families"] = dict(Counter(r["methods"][method]["family"] for r in rows
                                                      if r["methods"][method]["family"]))
            group["failure_reasons"] = dict(Counter(r["methods"][method]["failure_reason"] for r in rows
                                                    if r["methods"][method]["success"] is False))
            group["without_fallback"] = rate_summary(
                [r["methods"][method]["success"] for r in rows if not r["methods"][method]["fallback"]],
                sum(not r["methods"][method]["fallback"] for r in rows))
            groups[method] = group
        result["tasks"][key] = dict(
            liquid=active_table["meta"]["liquid"] if task == "pour_water" and mode in ("table", "pbf_table")
            else ("pbf" if mode == "pbf" else None),
            summary=groups, rows=rows,
            oracle=rate_summary(oracles, len(rows)) if mode in ("table", "pbf_table") else None,
            uniform_expectation=sum(uniform)/len(uniform)
            if uniform and all(v is not None for v in uniform) else None)
        pairs = [(m, "M0_RANDOM") for m in methods if m != "M0_RANDOM"]
        pairs += [(a["id"], b["id"]) for a in config["methods"] for b in config["methods"]
                  if a["variant"] == "M3" and b["variant"] == "A1"
                  and (a["provider"], a["model"], a.get("prompt_version")) == (
                      b["provider"], b["model"], b.get("prompt_version"))]
        pairs += [(a["id"], b["id"]) for a in config["methods"] for b in config["methods"]
                  if a["variant"] == b["variant"]
                  and (a["provider"], a["model"]) == (b["provider"], b["model"])
                  and a.get("prompt_version") == "v2" and b.get("prompt_version") == "v1"]
        for a, b in pairs:
            result["comparisons"].append(dict(task=key, method=a, reference=b, **paired(scores[a], scores[b])))
    holm(result["comparisons"])
    target_summary = Path(summary_path) if summary_path is not None else (out / "summary.json")
    target_report = Path(report_path) if report_path is not None else (out / "report.md")
    atomic_json(target_summary, result)
    target_report.write_text(render_report(result), encoding="utf-8")
    return result


def render_report(result):
    lines = ["# C frozen-table evaluation", "", f"Seeds: {result['seeds']}", "",
             "Success includes physical execution failures in the denominator; numerical/call/missing outcomes are excluded and counted.",
             "Wilson intervals use scene seeds as units. Holm correction covers all reported comparisons with valid pairs.", ""]
    for task, data in result["tasks"].items():
        lines += [f"## {task} ({data['liquid'] or 'rigid-body'})", "",
                  "| Method | Success/scored | Planned | 95% Wilson CI | Families |", "|---|---:|---:|---|---|"]
        for method, s in data["summary"].items():
            ci = s["ci95"]
            lines.append(f"| {method} | {s['successes']}/{s['scored_n']} | {s['planned_n']} | "
                         f"{f'{ci[0]:.3f}–{ci[1]:.3f}' if ci else '—'} | {s['selected_families']} |")
        if task == "pour_water_pbf_table" and data.get("oracle") and data["oracle"].get("scored_n"):
            o = data["oracle"]
            oci = o["ci95"]
            lines.append(f"| ORACLE | {o['successes']}/{o['scored_n']} | {o['planned_n']} | "
                         f"{f'{oci[0]:.3f}–{oci[1]:.3f}' if oci else '—'} | — |")
            if data.get("uniform_expectation") is not None:
                u = data["uniform_expectation"]
                lines.append(f"| UNIFORM_FEASIBLE_EXPECTATION | {u*len(result['seeds']):.1f}/{len(result['seeds'])} "
                             f"({u:.3f}) | {len(result['seeds'])} | — | — |")
        lines += ["", "| Comparison | Paired n | Wins/losses | Exact p | Holm p |", "|---|---:|---:|---:|---:|"]
        for c in result["comparisons"]:
            if c["task"] == task:
                lines.append(f"| {c['method']} vs {c['reference']} | {c['paired_n']} | {c['wins']}/{c['losses']} | "
                             f"{c['p_exact']} | {c.get('p_holm')} |")
        lines.append("")
    lines += ["## Model-call audit", "", "```json", json.dumps(result["selection_audit"], indent=2), "```", ""]
    lines += [f"- {note}" for note in result["notes"]]
    return "\n".join(lines) + "\n"


def verify_pbf(record, manifest, seed, cid, execution_sha256=None):
    if (record.get("manifest_sha256"), record.get("seed"), record.get("candidate_id")) != (
            manifest["manifest_sha256"], seed, cid):
        raise ValueError("PBF record identity mismatch")
    if execution_sha256 is not None and record.get("execution_sha256") != execution_sha256:
        raise ValueError("PBF execution fingerprint mismatch")
    e = record.get("episode")
    if e is None:  # Explicit simulator invocation error, never a physical failure.
        if not record.get("error"):
            raise ValueError("missing PBF result without an error")
        return
    config = manifest["identity"]["config"]
    expected = dict(task_id="pour_water", seed=seed, selected_id=cid,
                    candidate_sha256=candidate_sha256(load_candidates(config["candidates"])),
                    scene_randomization=config["scene_randomization"],
                    task_spec_sha256=sequence_specs(["pour_water"], load_sequence_config(config["sequence_config"]))["pour_water"]["sha256"])
    # A returns tuple-valued dataclasses; disk round-trips turn those into lists.
    # Compare canonical JSON, as the formal C runner does, not Python containers.
    if any(_sha(e.get(k)) != _sha(v) for k, v in expected.items()) or e.get("pour_proxy"):
        raise ValueError("PBF episode protocol mismatch")


def pbf_job(config, manifest_sha, seed, cid, execution_sha256=None):
    from grasp_a.sequence import run_sequence
    started = time.perf_counter()
    record = dict(manifest_sha256=manifest_sha, execution_sha256=execution_sha256,
                  seed=seed, candidate_id=cid, episode=None, error=None)
    try:
        record["episode"] = run_sequence("pour_water", seed, cid, load_candidates(config["candidates"]),
                                         scene_config=config["scene_randomization"],
                                         config=load_sequence_config(config["sequence_config"]))
    except Exception as exc:
        record["error"] = dict(type=type(exc).__name__, message=str(exc))
    record["seconds"] = time.perf_counter() - started
    return seal(record)


def run_pbf(out, workers=4):
    with _run_lock(out):
        manifest, table, trials = load_run(out)
        config = manifest["identity"]["config"]
        # A/B, assets, inputs and environment remain frozen. A separately versioned
        # C reporting/validation fix need not invalidate already obtained answers.
        current = provenance(config, Path(manifest["identity"]["source"]), table,
                             {m["id"]: build_provider(m["provider"]) for m in config["methods"]})
        original = manifest["identity"]
        physics = lambda identity: {k: v for k, v in identity["code_and_assets"].items()
                                    if k.startswith(("grasp_a/", "grasp_b/", "assets/"))}
        if (physics(current) != physics(original) or current["environment"] != original["environment"]
                or current["source_sha256"] != original["source_sha256"]):
            raise ValueError("physics/environment/input changed since selections; use a new run")
        pbf_identity = dict(selection_manifest_sha256=manifest["manifest_sha256"], execution=current)
        pbf_path = out / "pbf_manifest.json"
        if pbf_path.exists():
            pbf_manifest = checked(pbf_path)
            if pbf_manifest["identity"] != pbf_identity:
                raise ValueError("PBF execution identity changed; use a new run")
        else:
            pbf_manifest = seal(dict(identity=pbf_identity,
                                     created_at=datetime.now(timezone.utc).isoformat()))
            atomic_json(pbf_path, pbf_manifest)
        execution_sha = pbf_manifest["record_sha256"]
        methods = [*BASELINES, *(m["id"] for m in config["methods"])]
        jobs = []
        for seed in config["seeds"]:
            for cid in sorted({c for c in picks_for("pour_water", seed, methods, table, trials).values() if c}):
                path = out / "pbf" / f"seed{seed}" / f"{cid}.json"
                if path.exists():
                    verify_pbf(checked(path), manifest, seed, cid, execution_sha)
                else:
                    jobs.append((seed, cid, path))
        print(f"PBF: {len(jobs)} new distinct seed/candidate episodes", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(pbf_job, config, manifest["manifest_sha256"], seed, cid, execution_sha): path
                       for seed, cid, path in jobs}
            for i, future in enumerate(as_completed(futures), 1):
                record = future.result()
                verify_pbf(record, manifest, record["seed"], record["candidate_id"], execution_sha)
                atomic_json(futures[future], record)
                print(f"PBF [{i}/{len(jobs)}] seed{record['seed']} {record['candidate_id']}: "
                      f"{physical_success(record['episode'])} ({record['seconds']:.1f}s)", flush=True)
        return summarize(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--workers", type=int, default=2)
    p = sub.add_parser("summarize")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--pbf-table", type=Path,
                   help="Optional full-PBF pour_water oracle table directory (e.g. r3_pbf_v3p_wide_strict_v2)")
    p.add_argument("--summary-out", type=Path, help="Optional custom output path for summary JSON")
    p.add_argument("--report-out", type=Path, help="Optional custom output path for markdown report")
    p = sub.add_parser("pbf")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if getattr(args, "workers", 1) < 1:
        parser.error("workers must be positive")
    if args.command == "run":
        result = run(args.config, args.source.resolve(), args.out.resolve(), resume=args.resume, workers=args.workers)
    elif args.command == "pbf":
        result = run_pbf(args.out.resolve(), args.workers)
    else:
        result = summarize(
            args.out.resolve(),
            pbf_table=args.pbf_table.resolve() if args.pbf_table else None,
            summary_path=args.summary_out.resolve() if args.summary_out else None,
            report_path=args.report_out.resolve() if args.report_out else None,
        )
    print(json.dumps(result["selection_audit"], indent=2))
    return int(any(a["errors"] or a["saved_n"] != a["planned_n"] for a in result["selection_audit"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
