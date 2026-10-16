"""Explicit denominators for pilot execution, selection, and paired comparisons."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import math
from statistics import mean


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _numbers(values) -> dict:
    values = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
    return {"n": len(values), "mean": mean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def trial_identity(record: dict) -> tuple:
    """The model sample repeat is separate from the physical environment seed."""
    return record["method_id"], record["task_id"], record["seed"], record.get("repeat", 0)


def _oracle_ids(acceptable_sets: dict | None, task: str, seed: int) -> list[str] | None:
    values = (acceptable_sets or {}).get(task)
    if isinstance(values, dict):
        return values.get(str(seed), values.get("default"))
    return values


def _group_summary(records: list[dict], planned: list[dict], acceptable_sets: dict | None) -> dict:
    completed = [r for r in records if r["status"] == "completed"]
    selections = [r for r in records if r.get("selected_id") is not None]
    selection_calls = [r for r in records if r.get("selection") is not None]
    statuses = Counter(r["status"] for r in records)
    statuses["missing"] = len(planned) - len(records)
    for status in ("completed", "no_feasible_candidate", "filter_error", "selector_error",
                   "invalid_selection", "execution_error"):
        statuses.setdefault(status, 0)
    episodes = [r["episode"] for r in completed]
    success_n = sum(e["success"] is True for e in episodes)
    feasible = [r for r in records if (r.get("filter") or {}).get("status") == "ok"]
    seeds = {r["seed"] for r in planned}
    feasible_seeds = {r["seed"] for r in feasible}
    metadata = [(r.get("selection") or {}).get("metadata", {}) for r in selection_calls]
    metadata = [m for m in metadata if isinstance(m, dict)]
    llm_metadata = [m for m in metadata if "cache_hit" in m]
    calls = [call for meta in llm_metadata for call in meta.get("calls", [])]
    initial_calls = [call for call in calls if call.get("phase") == "initial"]
    initial_valid = sum((call.get("validation") or {}).get("valid") is True for call in initial_calls)
    initial_checked = sum(call.get("validation") is not None for call in initial_calls)
    model_usable = sum(m.get("fallback_to_m1", m.get("fallback_to_geometry")) is False for m in llm_metadata)
    call_outcomes = Counter()
    for call in calls:
        error = call.get("error")
        if error:
            code = error.get("code")
            outcome = ("refused" if code == "model_refusal" else
                       "incomplete" if code == "model_response_incomplete" else
                       "unsuccessful_response" if error.get("type") == "ModelResponseError" else
                       "transport_error")
        elif call.get("validation") is not None:
            outcome = "valid_output" if call["validation"].get("valid") is True else "invalid_output"
        else:
            outcome = "unclassified"
        call_outcomes[outcome] += 1
    tokens = Counter()
    live_tokens = Counter()
    usage_records = 0
    live_usage_records = 0
    for meta in metadata:
        usage = meta.get("usage")
        if isinstance(usage, dict):
            numeric = {key: value for key, value in usage.items()
                       if type(value) in (int, float) and math.isfinite(value)}
            if numeric:
                usage_records += 1
                tokens.update(numeric)
        live_usage = meta.get("live_usage")
        if isinstance(live_usage, dict):
            numeric = {key: value for key, value in live_usage.items()
                       if type(value) in (int, float) and math.isfinite(value)}
            if numeric:
                live_usage_records += 1
                live_tokens.update(numeric)
    oracle_checks = []
    for record in selections:
        ids = _oracle_ids(acceptable_sets, record["task_id"], record["seed"])
        if ids is not None:
            oracle_checks.append(record["selected_id"] in ids)
    repeat_groups = defaultdict(list)
    for record in selections:
        repeat_groups[record["seed"]].append(record["selected_id"])
    consistency = []
    for seed in sorted(seeds):
        chosen = repeat_groups[seed]
        planned_n = sum(r["seed"] == seed for r in planned)
        counts = Counter(chosen)
        consistency.append({"seed": seed, "planned_repeats": planned_n,
                            "valid_selection_n": len(chosen),
                            "selected_id_counts": dict(sorted(counts.items())),
                            "all_identical": len(counts) == 1 if len(chosen) >= 2 else None,
                            "modal_fraction": max(counts.values()) / len(chosen) if chosen else None})
    flags = {}
    for flag in ("collision", "dropped", "grasp_maintained"):
        known = [e[flag] for e in episodes if type(e.get(flag)) is bool]
        n = sum(known)
        flags[flag] = {"true_n": n, "valid_n": len(known), "rate": _ratio(n, len(known))}
    return {
        "planned_n": len(planned), "saved_n": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "completed_n": len(completed), "success_n": success_n,
        "conditional_success_rate": _ratio(success_n, len(completed)),
        "completed_coverage": _ratio(len(completed), len(planned)),
        "success_coverage": _ratio(success_n, len(planned)),
        "known_feasible_trial_n": len(feasible),
        "success_per_known_feasible_trial": _ratio(success_n, len(feasible)),
        "seed_coverage": {"feasible_n": len(feasible_seeds), "planned_n": len(seeds),
                          "rate": _ratio(len(feasible_seeds), len(seeds))},
        "physical_flags": flags,
        "physical_failure_reasons": dict(sorted(Counter(
            e.get("failure_reason") for e in episodes if e.get("failure_reason") is not None).items())),
        "position_error_m": _numbers(e.get("position_error_m") for e in episodes),
        "orientation_error_deg": _numbers(e.get("orientation_error_deg") for e in episodes),
        "selection": {
            "valid_n": len(selections), "call_n": len(selection_calls),
            "latency_s": _numbers(r["selection"].get("latency_s") for r in selection_calls),
            "llm_record_n": len(llm_metadata),
            "initial_model_valid_n": initial_valid,
            "initial_model_valid_rate": _ratio(initial_valid, len(llm_metadata)),
            "initial_model_checked_n": initial_checked,
            "initial_model_invalid_output_n": initial_checked - initial_valid,
            "initial_model_valid_per_checked_response": _ratio(initial_valid, initial_checked),
            "model_usable_after_repair_n": model_usable,
            "model_usable_after_repair_rate": _ratio(model_usable, len(llm_metadata)),
            "model_call_outcomes": dict(sorted(call_outcomes.items())),
            "model_call_n": len(calls),
            "model_call_cache_hit_n": sum(call.get("cache_hit") is True for call in calls),
            "model_call_latency_s": _numbers(call.get("latency_s") for call in calls),
            "cache_hit_n": sum(m.get("cache_hit") is True for m in llm_metadata),
            "cache_hit_rate": _ratio(sum(m.get("cache_hit") is True for m in llm_metadata), len(llm_metadata)),
            "repair_trial_n": sum(m.get("repair_attempts", 0) > 0 for m in llm_metadata),
            "repair_attempt_n": sum(m.get("repair_attempts", 0) for m in llm_metadata),
            "repair_rate": _ratio(sum(m.get("repair_attempts", 0) > 0 for m in llm_metadata), len(llm_metadata)),
            "fallback_trial_n": sum(bool(m.get("fallback_to_geometry", m.get("fallback_to_m1", False)))
                                    for m in llm_metadata),
            "fallback_rate": _ratio(sum(bool(m.get("fallback_to_geometry", m.get("fallback_to_m1", False)))
                                        for m in llm_metadata), len(llm_metadata)),
            "usage_record_n": usage_records, "token_usage": dict(tokens) if usage_records else None,
            "live_usage_record_n": live_usage_records,
            "live_token_usage": dict(live_tokens) if live_usage_records else None,
            "logical_request_n": sum(m.get("requests", 0) for m in llm_metadata),
            "live_request_n": sum(m.get("live_requests", 0) for m in llm_metadata),
            "acceptable_set": {"evaluated_n": len(oracle_checks), "accepted_n": sum(oracle_checks),
                               "rate": _ratio(sum(oracle_checks), len(oracle_checks))},
            "repeat_consistency": consistency,
        },
    }


def summarize_records(records: list[dict], planned: list[dict], *,
                      acceptable_sets: dict | None = None) -> dict:
    """Aggregate only completed episodes as physical observations, keeping all errors."""
    expected = {trial_identity(p) for p in planned}
    if len(expected) != len(planned):
        raise ValueError("duplicate identities in the trial plan")
    indexed = {}
    for record in records:
        identity = trial_identity(record)
        if identity not in expected:
            raise ValueError(f"unexpected trial identity: {identity}")
        if identity in indexed:
            raise ValueError(f"duplicate saved trial identity: {identity}")
        if record.get("status") == "completed" and not isinstance(record.get("episode"), dict):
            raise ValueError("completed trial requires an episode")
        if record.get("status") == "completed" and type(record["episode"].get("success")) is not bool:
            raise ValueError("completed episode.success must be a boolean")
        indexed[identity] = record
    groups = defaultdict(list)
    for item in planned:
        groups[(item["method_id"], item["task_id"])].append(item)
    output = {}
    for (method_id, task), items in sorted(groups.items()):
        saved = [indexed[trial_identity(p)] for p in items if trial_identity(p) in indexed]
        output.setdefault(method_id, {})[task] = _group_summary(saved, items, acceptable_sets)
    pairs = []
    methods = sorted({p["method_id"] for p in planned})
    for task in sorted({p["task_id"] for p in planned}):
        for left, right in combinations(methods, 2):
            a = {(r["seed"], r.get("repeat", 0)): r for r in records
                 if r["method_id"] == left and r["task_id"] == task and r["status"] == "completed"}
            b = {(r["seed"], r.get("repeat", 0)): r for r in records
                 if r["method_id"] == right and r["task_id"] == task and r["status"] == "completed"}
            joint = []
            mismatch = 0
            for key in sorted(a.keys() & b.keys()):
                same = all(a[key].get(field) is not None and a[key].get(field) == b[key].get(field)
                           for field in ("candidate_sha256", "controller_version", "manifest_sha256"))
                if same:
                    joint.append((a[key], b[key]))
                else:
                    mismatch += 1
            left_wins = sum(x["episode"]["success"] is True and y["episode"]["success"] is False
                            for x, y in joint)
            right_wins = sum(y["episode"]["success"] is True and x["episode"]["success"] is False
                             for x, y in joint)
            planned_keys = {(p["seed"], p.get("repeat", 0)) for p in planned
                            if p["task_id"] == task and p["method_id"] in (left, right)}
            exclusion_reasons = Counter()
            paired_keys = {(x["seed"], x.get("repeat", 0)) for x, _ in joint}
            for seed, repeat in sorted(planned_keys - paired_keys):
                left_status = indexed.get((left, task, seed, repeat), {}).get("status", "missing")
                right_status = indexed.get((right, task, seed, repeat), {}).get("status", "missing")
                reason = ("provenance_mismatch" if left_status == right_status == "completed"
                          else f"left={left_status},right={right_status}")
                exclusion_reasons[reason] += 1
            pairs.append({"task_id": task, "left": left, "right": right,
                          "planned_pair_n": len(planned_keys), "paired_completed_n": len(joint),
                          "excluded_n": len(planned_keys) - len(joint),
                          "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
                          "completed_provenance_mismatch_n": mismatch,
                          "left_only_success_n": left_wins, "right_only_success_n": right_wins,
                          "tied_outcome_n": len(joint) - left_wins - right_wins,
                          "success_rate_difference_left_minus_right":
                              _ratio(left_wins - right_wins, len(joint)),
                          "selected_id_agreement_rate": _ratio(
                              sum(x["selected_id"] == y["selected_id"] for x, y in joint), len(joint))})
    return {"schema_version": "grasp_c_summary_v1", "evaluation_scope": "pilot",
            "planned_n": len(planned), "saved_n": len(records),
            "methods": output, "paired_comparisons": pairs,
            "notes": ["Physical rates use completed episodes only; errors are separate statuses.",
                      "Method metrics describe the current final trial records; archived retries are accounted separately.",
                      "Valid selections include geometry fallbacks; model output validity is reported separately.",
                      "token_usage includes cached generations; live_token_usage counts current uncached calls only.",
                      "Missing pose errors remain null and are excluded from numeric means.",
                      "dropped/grasp_maintained are controller flags, not full-trajectory event sensors.",
                      "An acceptable-set metric is null unless independently supplied ground truth exists.",
                      "Current controller uses candidate-dependent pilot targets and last-frame holding checks."]}
