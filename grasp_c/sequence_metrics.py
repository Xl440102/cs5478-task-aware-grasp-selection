"""Separate statistics for the versioned full physical task sequence track.

Only public selection audit fields are reused from prep statistics. Physical
denominators and sequence evidence are interpreted here, without relabeling
legacy preparation episodes or treating numerical failures as physical events.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

from .cache import canonical_json
from .metrics import _group_summary, _numbers, _ratio, trial_identity

SEQUENCE_TASKS = frozenset({"transport_place", "handover_transfer", "pour_water"})
TRIAL_SCHEMA = "evaluation_sequence_trial_v1"
EPISODE_SCHEMA = "sequence_episode_v1"
SCENE = "hollow_cup_sequences_v1"
CONTROLLER = "staged_sequences_v1"
EVALUATOR = "physical_outcomes_v1"
EPISODE_STATUSES = frozenset({"completed", "rejected", "execution_failed",
                              "simulation_error", "simulation_invalid"})
C_STATUSES = frozenset({"no_feasible_candidate", "filter_error", "selector_error",
                       "invalid_selection", "execution_error"})
PROTOCOL_FIELDS = ("track", "scene_version", "controller_version", "evaluation_version",
                   "candidate_sha256", "task_spec_sha256", "observation_config",
                   "schema_version", "manifest_sha256")


def _protocol(record: dict) -> dict:
    return {key: record[key] for key in PROTOCOL_FIELDS}


def _numerical_validity(record: dict) -> bool | None:
    status = record["status"]
    if status in {"simulation_error", "simulation_invalid"}:
        return False
    if status not in {"completed", "execution_failed"}:
        return None
    water = (record["episode"].get("final_evidence") or {}).get("water")
    if isinstance(water, dict) and water.get("finite_state") is False:
        return False
    value = (record["episode"].get("success_checks") or {}).get("fluid_density_valid")
    return value if type(value) is bool else None


def _valid_physical(record: dict) -> bool:
    return record["status"] in {"completed", "execution_failed"} and _numerical_validity(record) is True


def _validate_record(record: dict) -> None:
    if record.get("task_id") not in SEQUENCE_TASKS or record.get("track") != "sequence":
        raise ValueError("sequence summary accepts only the explicit sequence track and tasks")
    for key in PROTOCOL_FIELDS:
        if key not in record or record[key] is None:
            raise ValueError(f"sequence record missing protocol field: {key}")
    expected = {"schema_version": TRIAL_SCHEMA, "scene_version": SCENE,
                "controller_version": CONTROLLER, "evaluation_version": EVALUATOR}
    for key, value in expected.items():
        if record[key] != value:
            raise ValueError(f"unsupported sequence {key}: {record[key]}")
    for key in ("candidate_sha256", "task_spec_sha256", "manifest_sha256"):
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError(f"sequence {key} must be a nonempty fingerprint")
    if not isinstance(record["observation_config"], dict):
        raise ValueError("sequence observation_config must be an object")
    status = record.get("status")
    if status not in EPISODE_STATUSES | C_STATUSES:
        raise ValueError(f"unknown sequence trial status: {status}")
    if status not in EPISODE_STATUSES:
        return  # Retained exception payloads are diagnostics, never scored episodes.
    if not isinstance(record.get("selected_id"), str) or not record["selected_id"]:
        raise ValueError("returned sequence episode requires a selected candidate ID")
    episode = record.get("episode")
    if not isinstance(episode, dict) or episode.get("schema_version") != EPISODE_SCHEMA:
        raise ValueError("returned sequence trial requires a sequence_episode_v1 payload")
    if episode.get("status") != status:
        raise ValueError("sequence trial status disagrees with its episode status")
    if type(episode.get("success")) is not bool:
        raise ValueError("sequence episode.success must be a boolean")
    if status != "completed" and episode["success"]:
        raise ValueError("only a completed sequence episode may report success")
    for key in ("task_id", "seed", "selected_id", "scene_version", "controller_version",
                "evaluation_version", "candidate_sha256", "task_spec_sha256", "observation_config"):
        if canonical_json(episode.get(key)) != canonical_json(record.get(key)):
            raise ValueError(f"sequence episode {key} disagrees with its trial")
    if episode["success"] and _numerical_validity(record) is not True:
        raise ValueError("successful sequence episode requires explicit valid numerical evidence")


def _boolean_stats(values) -> dict:
    values = [value for value in values if type(value) is bool]
    return {"true_n": sum(values), "valid_n": len(values), "rate": _ratio(sum(values), len(values))}


def _check_stats(episodes: list[dict], field: str) -> dict:
    keys = sorted({key for episode in episodes for key in (episode.get(field) or {})})
    return {key: _boolean_stats((episode.get(field) or {}).get(key) for episode in episodes)
            for key in keys}


def _rate(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "rate": _ratio(numerator, denominator)}


def _task_outcomes(task: str, episodes: list[dict]) -> dict:
    evidence = [episode.get("final_evidence") or {} for episode in episodes]
    common_numbers = ("source_xy_error_m", "source_z_m", "source_upright_deg",
                      "source_linear_speed_m_s", "source_angular_speed_deg_s", "donor_clearance_m")
    final = {key: _numbers(row.get(key) for row in evidence) for key in common_numbers}
    final.update({key: _boolean_stats(row.get(key) for row in evidence)
                  for key in ("table_contact", "donor_contact", "receiver_contact")})
    specific = {}
    if task == "transport_place":
        specific = {"table_support": final["table_contact"],
                    "donor_detached": _boolean_stats(
                        not row["donor_contact"] if type(row.get("donor_contact")) is bool else None
                        for row in evidence),
                    "placement_xy_error_m": final["source_xy_error_m"]}
    elif task == "handover_transfer":
        specific = {"receiver_both_fingers_support": final["receiver_contact"],
                    "donor_detached": _boolean_stats(
                        not row["donor_contact"] if type(row.get("donor_contact")) is bool else None
                        for row in evidence),
                    "source_height_m": final["source_z_m"]}
    else:
        for key in ("source_goal_error_m", "target_upright_deg", "target_displacement_m",
                    "target_linear_speed_m_s", "target_angular_speed_deg_s"):
            final[key] = _numbers(row.get(key) for row in evidence)
        water = [row.get("water") or {} for row in evidence]
        specific["water"] = {key: _numbers(row.get(key) for row in water) for key in
                             ("transfer_ratio", "spill_ratio", "initial_mass_kg", "source_mass_kg",
                              "target_mass_kg", "outside_mass_kg", "accounting_error_kg", "max_compression")}
        specific["water"]["finite_state"] = _boolean_stats(row.get("finite_state") for row in water)
    stage_ids = sorted({row["id"] for episode in episodes for row in episode.get("stages", [])})
    stages = {}
    for stage in stage_ids:
        rows = [row for episode in episodes for row in episode.get("stages", []) if row["id"] == stage]
        control = Counter(row.get("control_status", "unknown") for row in rows)
        scoring = [(episode.get("stage_evaluation") or {}).get(stage) or {} for episode in episodes]
        physical_keys = sorted({key for row in scoring for key in (row.get("physical_checks") or {})})
        stages[stage] = {
            "recorded_n": len(rows), "control_status_counts": dict(sorted(control.items())),
            "physical_pass": _boolean_stats(row.get("physical_pass") for row in scoring),
            "physical_checks": {key: _boolean_stats(
                (row.get("physical_checks") or {}).get(key) for row in scoring) for key in physical_keys},
        }
    return {"evidence_scope": "numerically_valid_completed_and_execution_failed_episodes",
            "episode_n": len(episodes), "success_checks": _check_stats(episodes, "success_checks"),
            "terminal_checks": _check_stats(episodes, "terminal_checks"),
            "stages": stages, "final_evidence": final, "task_specific": specific,
            "elapsed_simulation_s": _numbers(episode.get("elapsed_s") for episode in episodes)}


def _summarize_group(records: list[dict], planned: list[dict], acceptable_sets: dict | None) -> dict:
    summary = _group_summary(records, planned, acceptable_sets)
    statuses = Counter(record["status"] for record in records)
    for status in EPISODE_STATUSES | C_STATUSES:
        summary["status_counts"].setdefault(status, 0)
    summary["status_counts"] = dict(sorted(summary["status_counts"].items()))
    returned = [record for record in records if record["status"] in EPISODE_STATUSES]
    valid = [record for record in returned if _valid_physical(record)]
    completed_valid = [record for record in valid if record["status"] == "completed"]
    numerical_attempts = [record for record in returned if record["status"] != "rejected"]
    numerical_invalid = [record for record in numerical_attempts if _numerical_validity(record) is False]
    numerical_unknown = [record for record in numerical_attempts if _numerical_validity(record) is None]
    valid_successes = sum(record["episode"]["success"] is True for record in valid)
    episodes = [record["episode"] for record in valid]
    summary.update(
        episode_status_counts={status: statuses[status] for status in sorted(EPISODE_STATUSES)},
        episode_failure_reasons_by_status={status: dict(sorted(Counter(
            record["episode"]["failure_reason"] for record in returned
            if record["status"] == status and record["episode"].get("failure_reason") is not None).items()))
            for status in sorted(EPISODE_STATUSES)},
        returned_n=len(returned), returned_coverage=_ratio(len(returned), len(planned)),
        numerically_valid_n=len(valid), numerically_invalid_n=len(numerical_invalid),
        numerical_validity_unknown_n=len(numerical_unknown),
        numerical_validity_coverage=_ratio(len(valid), len(planned)),
        numerical_validity_rate=_ratio(len(valid), len(numerical_attempts)),
        completed_numerically_valid_n=len(completed_valid),
        conditional_success_rate=_ratio(valid_successes, len(completed_valid)),
        success_per_numerically_valid_episode=_ratio(valid_successes, len(valid)),
        execution_failed_n=statuses["execution_failed"], rejected_n=statuses["rejected"],
        simulation_error_n=statuses["simulation_error"], simulation_invalid_n=statuses["simulation_invalid"],
        retained_exception_episode_n=sum(record["status"] in C_STATUSES and record.get("episode") is not None
                                        for record in records),
        rates={
            "conditional_task_success": _rate(valid_successes, len(completed_valid)),
            "task_success_including_execution_failures": _rate(valid_successes, len(valid)),
            "end_to_end_success_coverage": _rate(valid_successes, len(planned)),
            "numerical_validity": _rate(len(valid), len(numerical_attempts)),
            "execution_failure_per_returned_episode": _rate(statuses["execution_failed"], len(returned)),
            "rejection_per_returned_episode": _rate(statuses["rejected"], len(returned)),
            "simulation_error_per_attempt": _rate(statuses["simulation_error"], len(numerical_attempts)),
            "simulation_invalid_per_attempt": _rate(statuses["simulation_invalid"], len(numerical_attempts)),
        },
        physical_flag_scope="numerically_valid_completed_and_execution_failed_episodes",
        physical_flags={flag: _boolean_stats(episode.get(flag) for episode in episodes)
                        for flag in ("collision", "dropped")},
        physical_failure_reasons=dict(sorted(Counter(
            episode["failure_reason"] for episode in episodes if episode.get("failure_reason") is not None).items())),
        task_outcomes=_task_outcomes(planned[0]["task_id"], episodes),
    )
    summary["execution_protocols"] = [dict(_protocol(records[0]), saved_n=len(records))] if records else []
    return summary


def summarize_sequence_records(records: list[dict], planned: list[dict], *,
                               acceptable_sets: dict | None = None) -> dict:
    """Summarize one sequence run; keep every error and reject mixed group protocols."""
    if any(item.get("task_id") not in SEQUENCE_TASKS or item.get("track", "sequence") != "sequence"
           for item in planned):
        raise ValueError("sequence trial plan contains a nonsequence task or track")
    expected = {trial_identity(item) for item in planned}
    if len(expected) != len(planned):
        raise ValueError("duplicate sequence identities in the trial plan")
    indexed = {}
    group_contexts = defaultdict(set)
    protocols = {}
    for record in records:
        _validate_record(record)
        identity = trial_identity(record)
        if identity not in expected:
            raise ValueError(f"unexpected sequence trial identity: {identity}")
        if identity in indexed:
            raise ValueError(f"duplicate saved sequence trial identity: {identity}")
        indexed[identity] = record
        protocol = _protocol(record)
        key = canonical_json(protocol)
        group_contexts[(record["method_id"], record["task_id"])].add(key)
        if key not in protocols:
            protocols[key] = dict(protocol, saved_n=0)
        protocols[key]["saved_n"] += 1
    if any(len(contexts) > 1 for contexts in group_contexts.values()):
        raise ValueError("mixed sequence protocols in the same method/task group; summarize them separately")
    groups = defaultdict(list)
    for item in planned:
        groups[(item["method_id"], item["task_id"])].append(item)
    methods = {}
    for (method, task), items in sorted(groups.items()):
        saved = [indexed[trial_identity(item)] for item in items if trial_identity(item) in indexed]
        methods.setdefault(method, {})[task] = _summarize_group(saved, items, acceptable_sets)
    pairs = []
    for task in sorted({item["task_id"] for item in planned}):
        task_methods = sorted({item["method_id"] for item in planned if item["task_id"] == task})
        for left, right in combinations(task_methods, 2):
            keys = {(item["seed"], item.get("repeat", 0)) for item in planned
                    if item["task_id"] == task and item["method_id"] in (left, right)}
            joint = []
            excluded = Counter()
            for seed, repeat in sorted(keys):
                a = indexed.get((left, task, seed, repeat))
                b = indexed.get((right, task, seed, repeat))
                if a is None or b is None:
                    excluded[f"left={a['status'] if a else 'missing'},right={b['status'] if b else 'missing'}"] += 1
                elif canonical_json(_protocol(a)) != canonical_json(_protocol(b)):
                    excluded["provenance_mismatch"] += 1
                elif not _valid_physical(a) or not _valid_physical(b):
                    reason = f"left={a['status']},right={b['status']}"
                    if a["status"] in {"completed", "execution_failed"} and not _valid_physical(a):
                        reason += ",left_numerical_validity=" + str(_numerical_validity(a))
                    if b["status"] in {"completed", "execution_failed"} and not _valid_physical(b):
                        reason += ",right_numerical_validity=" + str(_numerical_validity(b))
                    excluded[reason] += 1
                else:
                    joint.append((a, b))
            left_wins = sum(a["episode"]["success"] and not b["episode"]["success"] for a, b in joint)
            right_wins = sum(b["episode"]["success"] and not a["episode"]["success"] for a, b in joint)
            pairs.append({"task_id": task, "left": left, "right": right,
                          "planned_pair_n": len(keys), "paired_numerically_valid_n": len(joint),
                          "paired_completed_n": sum(a["status"] == b["status"] == "completed" for a, b in joint),
                          "excluded_n": len(keys) - len(joint), "exclusion_reasons": dict(sorted(excluded.items())),
                          "completed_provenance_mismatch_n": excluded["provenance_mismatch"],
                          "left_only_success_n": left_wins, "right_only_success_n": right_wins,
                          "tied_outcome_n": len(joint) - left_wins - right_wins,
                          "comparison_denominator": "paired_numerically_valid_n",
                          "success_rate_difference_left_minus_right": _ratio(left_wins - right_wins, len(joint)),
                          "selected_id_agreement_rate": _ratio(
                              sum(a["selected_id"] == b["selected_id"] for a, b in joint), len(joint))})
    return {"schema_version": "grasp_c_sequence_summary_v1", "track": "sequence",
            "evaluation_scope": "pilot", "planned_n": len(planned), "saved_n": len(records),
            "execution_protocols": [protocols[key] for key in sorted(protocols)],
            "methods": methods, "paired_comparisons": pairs,
            "notes": [
                "Sequence and legacy preparation episodes are never combined.",
                "A episode status is preserved; completed does not imply physical task success.",
                "Numerical validity requires explicit fluid_density_valid evidence and excludes simulation errors and rejected episodes.",
                "Conditional success uses numerically valid completed episodes; execution-inclusive success also includes numerically valid execution_failed episodes.",
                "Numerical errors and rejected or missing episodes are not invented collision/drop observations.",
                "Physical flags and task evidence use numerically valid completed/execution_failed episodes; missing values stay unobserved.",
                "Model validity, repair, fallback, cache, tokens and oracle labels retain their explicit selection denominators.",
                "Group metrics use final saved trials; archived retry accounting belongs to the run attempt history.",
                "Paired effects use shared numerically valid identities with matching complete protocol fingerprints.",
                "elapsed_simulation_s is simulated time, not CPU or model inference latency.",
            ]}
