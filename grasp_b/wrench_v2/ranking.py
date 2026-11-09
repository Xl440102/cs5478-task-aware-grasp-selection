"""Complete B-only ranking; non-scored IDs form a diagnostic tail, not quality."""
from .adapter import GeneratorInfo
from .gws import _text
from .m1 import METHOD_ID, prepare_candidate, score_prepared
from time import perf_counter

RANKING_POLICY = "scored_utilization_then_unscored_id_v1"


def rank_results(results, input_ids):
    ids = tuple(input_ids)
    for cid in ids:
        _text(cid, "candidate ID")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate input candidate IDs")
    rows = tuple(results)
    if len(rows) != len(ids) or len({r.candidate_id for r in rows}) != len(rows) or {r.candidate_id for r in rows} != set(ids):
        raise ValueError("results must cover input IDs exactly once")
    tasks = {(r.task_name, r.provenance["config_hash"], r.provenance["task_set_hash"]) for r in rows}
    if len(tasks) > 1:
        raise ValueError("do not rank mixed task/config/scenario contexts")
    ranked = sorted(rows, key=lambda r: (0, r.worst_utilization, r.candidate_id) if r.status == "scored" else (1, 0., r.candidate_id))
    scored = [r for r in ranked if r.status == "scored"]
    return dict(method_id=METHOD_ID, ranking_policy=RANKING_POLICY,
        ranking=[r.candidate_id for r in ranked],
        selected_id=scored[0].candidate_id if scored else None,
        selection_status="scored_choice" if scored else "no_scored_candidate",
        non_scored_tail_policy="ID only; no relative quality claim; no legacy fallback",
        scores=ranked)


def rank_candidates(candidates, input_ids, *, object_geometry, gripper_geometry, generator_infos,
                    config, task_sets, pool_scope):
    """Caller supplies A-filtered IDs or explicitly labels generator diagnostics.

    This function never runs A feasibility, reads oracle records, or infers
    feasibility from geometry. Prepared contact models are reused across tasks.
    """
    if pool_scope not in ("caller_supplied_a_feasible", "generator_pool_diagnostic"):
        raise ValueError("explicit pool scope required")
    ids = tuple(input_ids)
    for cid in ids:
        _text(cid, "candidate ID")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate input IDs")
    candidates = tuple(candidates)
    by_id = {c.id: c for c in candidates}
    if len(by_id) != len(candidates) or not set(ids) <= set(by_id):
        raise ValueError("duplicate candidates or unknown input ID")
    if not task_sets or any(key != value.task_name for key, value in task_sets.items()):
        raise ValueError("task map must be nonempty and match task names")
    prepared = []
    for cid in sorted(ids):
        info = generator_infos.get(cid)
        if not isinstance(info, GeneratorInfo):
            raise ValueError("explicit per-candidate GeneratorInfo required")
        prepared.append(prepare_candidate(by_id[cid], object_geometry, gripper_geometry, generator_info=info, config=config))
    report = dict(method_id=METHOD_ID, pool_scope=pool_scope, input_count=len(ids),
                  a_feasible_count=len(ids) if pool_scope == "caller_supplied_a_feasible" else None,
                  preparation_seconds={k: sum(p.timings_s[k] for p in prepared) for k in ("contact_spec", "primitives", "constraints")},
                  tasks={})
    for name, task_set in task_sets.items():
        rows, wall_times = [], []
        for p in prepared:
            began = perf_counter()
            rows.append(score_prepared(p, task_set))
            wall_times.append(perf_counter()-began)
        t = perf_counter()
        ranked = rank_results(rows, ids)
        ranked["ranking_assembly_s"] = perf_counter()-t
        ranked["candidate_wall_times_s"] = wall_times
        ranked["task_evaluation_wall_s"] = sum(wall_times)
        report["tasks"][name] = ranked
    return report
