"""M2-v2: finite pre-execution trajectory wrench utilization, not outcomes.

Full scores require every required state/event to be complete and LP optimal.
Matched static ablation uses the SAME poses/times but evaluates gravity only;
it is explicitly a different method, never a complete dynamic Pour score.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from types import SimpleNamespace

import numpy as np

from .contacts import _freeze_json, _plain
from .constraints import validate_constraints
from .gws import PrimitiveWrenchData
from .m1 import PreparedCandidate, fingerprint
from .ranking import rank_results
from .solver import solve_wrench
from .tasks import ObjectParameters, TaskWrenchScenario, Orientation, ZERO_DISTURBANCE
from .motion_prediction import PredictedMotionContext, MOTION_VERSION, motion_identity
from .dynamics import PredictedDynamicsContext, DYNAMICS_VERSION

METHOD_ID = "m2_gws_trajectory_v2"
STATIC_METHOD_ID = "m2_matched_trajectory_gravity_ablation_v1"
VERSION = "finite_required_trajectory_utilization_v1"
SUPPORTED = {"lift", "transport", "lower", "tilt", "pour_dwell", "return_upright", "pre_receiver_contact_hold"}
NON_DONOR = {"release", "donor_release", "receiver_close", "receiver_lift", "retreat", "settle", "terminal"}


@dataclass(frozen=True, slots=True)
class TrajectoryPolicy:
    exclude_table_transition: bool = True
    include_untimed_precontact_hold: bool = True
    excluded_stages: tuple = tuple(sorted(NON_DONOR))
    version: str = field(default="donor_precontact_required_scope_v1", init=False)

    def __post_init__(self):
        if type(self.exclude_table_transition) is not bool or type(self.include_untimed_precontact_hold) is not bool:
            raise ValueError("explicit boolean scope options required")
        stages = tuple(self.excluded_stages)
        if not set(stages) <= NON_DONOR or len(set(stages)) != len(stages):
            raise ValueError("cannot silently exclude required lift/carry/tilt states")
        object.__setattr__(self, "excluded_stages", stages)


class ExactWrenchCache:
    """Mathematical LP memoization, not execution/decision cache. No rounding.

    Key binds candidate, A/column metadata, B/b, config/tolerances and exact
    physical wrench. Scope one candidate in smoke to bound memory. Signed zeros
    compare equally. All time samples are retained even on cache hits.
    """
    def __init__(self):
        self._values = {}

    def solve(self, prepared, wrench, model_key):
        key = (model_key, tuple(wrench))
        began = perf_counter()
        hit = key in self._values
        if not hit:
            ref = fingerprint(key)
            result = solve_wrench(prepared.primitives, wrench, constraints=prepared.constraints,
                                 tolerances=prepared.config.tolerances)
            self._values[key] = (ref, result)
        ref, result = self._values[key]
        return ref, result, hit, perf_counter()-began

    def catalog(self):
        return {ref: result.to_dict() for ref, result in self._values.values()}


@dataclass(frozen=True, slots=True)
class TrajectoryEvaluation:
    sample_id: str
    stage: str
    time_s: float
    timed: bool
    side: str
    required: bool
    status: str
    wrench_mug: tuple | None
    gravity_wrench_mug: tuple | None
    solver_ref: str | None
    solver_result: object
    cache_hit: bool
    wall_s: float
    reason: str | None

    def to_dict(self):
        value = {key: _plain(value) for key, value in ((f, getattr(self, f)) for f in self.__dataclass_fields__) if key != "solver_result"}
        value["inertial_component_used_mug"] = None if self.wrench_mug is None or self.gravity_wrench_mug is None else [a-b for a, b in zip(self.wrench_mug, self.gravity_wrench_mug)]
        return value


@dataclass(frozen=True, slots=True)
class M2Result:
    candidate_id: str
    candidate_hash: str
    task_name: str
    family: str
    geometry_status: str
    method_id: str
    status: str
    worst_utilization: float | None
    score: float | None
    worst_samples: tuple
    samples: tuple
    provenance: Mapping
    timings_s: Mapping
    reason: str | None

    def __post_init__(self):
        if self.status == "scored":
            if self.worst_utilization is None or not np.isfinite(self.worst_utilization) or self.worst_utilization < 0 or self.score != 1-self.worst_utilization:
                raise ValueError("finite complete score required")
        elif self.worst_utilization is not None or self.score is not None or self.worst_samples:
            raise ValueError("incomplete trajectory must not expose full score")
        object.__setattr__(self, "samples", tuple(self.samples))
        object.__setattr__(self, "worst_samples", tuple(_freeze_json(x) for x in self.worst_samples))
        for key in ("provenance", "timings_s"):
            object.__setattr__(self, key, _freeze_json(getattr(self, key)))

    def summary(self):
        return dict(candidate_id=self.candidate_id, candidate_hash=self.candidate_hash, task_name=self.task_name,
            method_id=self.method_id, family=self.family, geometry_status=self.geometry_status,
            status=self.status, worst_utilization=self.worst_utilization, score=self.score, margin=self.score,
            worst_samples=_plain(self.worst_samples), provenance=dict(config_hash=self.provenance["config_hash"],
                evaluation_context_hash=self.provenance["evaluation_context_hash"]), timings_s=_plain(self.timings_s))

    def to_dict(self, *, include_catalog=True):
        value = self.summary()
        value.update(version=VERSION, provenance=_plain(self.provenance), reason=self.reason,
                     samples=[s.to_dict() for s in self.samples])
        if include_catalog:
            value["solver_results"] = {s.solver_ref: s.solver_result.to_dict() for s in self.samples if s.solver_result is not None}
        return value


def score_trajectory(prepared, motion, dynamics, *, policy=None, gravity_only=False, cache=None):
    began = perf_counter()
    if not isinstance(prepared, PreparedCandidate) or not isinstance(motion, PredictedMotionContext) or not isinstance(dynamics, PredictedDynamicsContext):
        raise ValueError("typed PreparedCandidate, Motion and Dynamics required")
    policy = TrajectoryPolicy() if policy is None else policy
    if not isinstance(policy, TrajectoryPolicy) or type(gravity_only) is not bool:
        raise ValueError("explicit scope policy and boolean ablation flag required")
    cache = ExactWrenchCache() if cache is None else cache
    if not isinstance(cache, ExactWrenchCache):
        raise ValueError("ExactWrenchCache required")
    spec, config = prepared.spec, prepared.config
    method = STATIC_METHOD_ID if gravity_only else METHOD_ID
    motion_hash = motion_identity(motion)
    common = dict(task=motion.task, body=_plain(dynamics.body), reference_point_m=dynamics.reference_point_m,
        gravity=dynamics.gravity_world_m_s2, policy=_plain(policy), method=method,
        model=motion.provenance.get("model"), initial_pose=motion.provenance.get("initial_pose_estimate"),
        estimate_source=motion.provenance.get("estimate_source"), sequence_config=motion.provenance.get("sequence_config"),
        stage_durations=[(s.name, s.duration_s) for s in motion.stages], motion_version=motion.model_version,
        dynamics_version=dynamics.model_version)
    config_hash, context_hash = fingerprint(config.to_dict()), fingerprint(common)
    status, reason = prepared.status, prepared.reason
    invalid = None
    if (motion.candidate_id, motion.candidate_hash) != (spec.candidate_id, spec.candidate_hash) or (
            dynamics.candidate_id, dynamics.candidate_hash, dynamics.task) != (spec.candidate_id, spec.candidate_hash, motion.task):
        invalid = "candidate/hash/task mismatch"
    elif motion.model_version != MOTION_VERSION or dynamics.model_version != DYNAMICS_VERSION or dynamics.provenance.get("motion_version") != MOTION_VERSION:
        status, reason = "unknown_motion", "unsupported motion/dynamics version"
    elif dynamics.provenance.get("motion_input_sha256") != motion_hash:
        invalid = "Dynamics not bound to this Motion input"
    elif dynamics.reference_point_m != config.reference_point_m or spec.reference_point_m != config.reference_point_m:
        invalid = "mug-local reference mismatch; no implicit TCP shift"
    elif not isinstance(dynamics.body, ObjectParameters) or len(dynamics.samples) != len(motion.samples):
        invalid = "missing physics or sample alignment"
    elif motion.provenance.get("measured_trajectory_used") or dynamics.provenance.get("execution_outcome_used"):
        invalid = "observed/execution inputs are forbidden"
    elif any((a.stage, a.time_s, a.timed, a.side) != (b.stage, b.time_s, b.timed, b.side)
             for a, b in zip(motion.samples, dynamics.samples)):
        invalid = "sample stage/time/side alignment mismatch"
    body_provenance = dynamics.body.provenance if isinstance(dynamics.body, ObjectParameters) else {}
    if "dry_object" in body_provenance:
        body_provenance = body_provenance["dry_object"]["provenance"]
    contact_asset, body_asset = spec.provenance.get("object_asset_sha256"), body_provenance.get("asset_sha256")
    if contact_asset is None or body_asset is None:
        status, reason = "missing_parameters", "explicit shared object asset identity required"
    elif contact_asset != body_asset:
        invalid = "contact/dynamics asset mismatch"
    if invalid:
        status, reason = "invalid_input", invalid
    model_key = None
    if status is None:
        try:
            if not isinstance(prepared.primitives, PrimitiveWrenchData) or (
                    prepared.primitives.candidate_id, prepared.primitives.candidate_hash) != (spec.candidate_id, spec.candidate_hash):
                raise ValueError("prepared contact model identity mismatch")
            if prepared.primitives.reference_point_m != config.reference_point_m:
                raise ValueError("prepared primitive reference mismatch")
            validate_constraints(prepared.primitives, prepared.constraints)
            model_key = fingerprint((prepared.constraints.to_dict(), config.to_dict()))
        except (ValueError, TypeError):
            status, reason = "invalid_input", "invalid prepared LP model"
    records = []
    gravity_cache = {}
    solver_wall = 0.
    calls = hits = 0
    for i, (m, d) in enumerate(zip(motion.samples, dynamics.samples)):
        sid = f"{m.stage}:{i:05d}:{m.side}"
        excluded = (m.stage in policy.excluded_stages or
                    (policy.exclude_table_transition and d.status == "unsupported_support_transition" and m.support == "table_contact_transition_unknown") or
                    (not policy.include_untimed_precontact_hold and m.side == "untimed_hold"))
        if excluded:
            records.append(TrajectoryEvaluation(sid, m.stage, m.time_s, m.timed, m.side, False,
                "excluded_by_declared_scope", None, None, None, None, False, 0., "predeclared non-donor/table/untimed boundary"))
            continue
        if status is not None:
            records.append(TrajectoryEvaluation(sid, m.stage, m.time_s, m.timed, m.side, True,
                "not_evaluated", None, None, None, None, False, 0., reason))
            continue
        error = None
        if m.stage not in SUPPORTED or not np.isfinite(m.time_s):
            error = "unknown_motion"
        elif d.status == "unsupported_support_transition":
            error = "unsupported_support_transition"
        elif d.status == "missing_inertia" and not gravity_only:
            error = "missing_inertia"
        elif d.status not in ("complete", "missing_inertia"):
            error = "unknown_motion"
        if error:
            records.append(TrajectoryEvaluation(sid, m.stage, m.time_s, m.timed, m.side, True,
                error, None, None, None, None, False, 0., "; ".join(d.limitations)))
            continue
        q = m.object_pose.quaternion_xyzw
        if q not in gravity_cache:
            gravity_cache[q] = TaskWrenchScenario(task_name=motion.task, stage="donor_hold", object_parameters=dynamics.body,
                reference_point_m=dynamics.reference_point_m, gravity_world_m_s2=dynamics.gravity_world_m_s2,
                orientation=Orientation(quaternion_xyzw=q, source="matched predicted attitude"),
                disturbance=ZERO_DISTURBANCE, context_source="matched trajectory gravity-only diagnostic").required_contact_wrench
        gravity = gravity_cache[q]
        wrench = gravity if gravity_only else d.required_contact_wrench_mug
        if wrench is None:
            records.append(TrajectoryEvaluation(sid, m.stage, m.time_s, m.timed, m.side, True,
                "unknown_motion", None, gravity, None, None, False, 0., "required full wrench unavailable"))
            continue
        ref, result, hit, wall = cache.solve(prepared, wrench, model_key)
        hits += hit
        solver_wall += wall
        if not hit:
            calls += int(result.diagnostics.get("linprog_calls", bool(result.diagnostics.get("lp_called"))))
        records.append(TrajectoryEvaluation(sid, m.stage, m.time_s, m.timed, m.side, True, result.status.value,
            tuple(wrench), tuple(gravity), ref, result, hit, wall, result.message))
    # Piecewise velocity jumps have undefined impulse demand. Do not turn them
    # into a finite acceleration by differencing or quietly ignore the event.
    for j, boundary in enumerate(motion.boundaries):
        if max(np.linalg.norm(boundary["velocity_jump_world_m_s"]), np.linalg.norm(boundary["omega_jump_world_rad_s"])) > 1e-10:
            records.append(TrajectoryEvaluation(f"boundary:{j}", str(boundary["after"]), boundary["time_s"], True,
                "boundary_event", True, "unknown_motion", None, None, None, None, False, 0., "unresolved velocity jump/impulse; numerical zero threshold 1e-10"))
    required = [r for r in records if r.required]
    counts = Counter(r.status for r in required)
    if status is None:
        if not required:
            status = "empty_required_scope"
        elif set(counts) == {"optimal"} and all(np.isfinite(r.solver_result.utilization) for r in required):
            status = "scored"
        else:
            status = next((s for s in ("unknown_motion", "numerical_error", "invalid_input", "dependency_missing",
                "missing_physics_parameters", "missing_inertia", "unsupported_support_transition", "infeasible", "unbounded") if s in counts), "numerical_error")
            status = {"infeasible": "model_infeasible", "missing_physics_parameters": "missing_parameters", "unbounded": "numerical_error"}.get(status, status)
            reason = "Incomplete required trajectory; inspect all sample states, not a partial maximum"
    worst = max(r.solver_result.utilization for r in required) if status == "scored" else None
    worst_samples = tuple(dict(sample_id=r.sample_id, stage=r.stage, time_s=r.time_s, timed=r.timed, side=r.side,
                              required_wrench=r.wrench_mug) for r in required if worst is not None and r.solver_result.utilization == worst)
    stage_partial = {}
    for r in required:
        if r.status == "optimal":
            stage_partial[r.stage] = max(stage_partial.get(r.stage, 0.), r.solver_result.utilization)
    return M2Result(spec.candidate_id, spec.candidate_hash, motion.task, spec.family.value, spec.status.value,
        method, status, worst, None if worst is None else 1-worst, worst_samples, tuple(records),
        dict(config_hash=config_hash, config=config.to_dict(), evaluation_context_hash=context_hash,
            evaluation_context=common, contact_model_sha256=fingerprint(spec.to_dict()), contact_spec=spec.to_dict(),
            motion_input_sha256=motion_hash, required_wrench_frame="mug", wrench_units="N,N*m", reference_point_m=config.reference_point_m,
            motion_version=motion.model_version, dynamics_version=dynamics.model_version,
            sample_status_counts=dict(counts), excluded_samples=sum(not r.required for r in records),
            original_missing_inertia_samples=sum(s.status == "missing_inertia" for s in dynamics.samples),
            stage_max_utilization_PARTIAL=stage_partial, continuous_maximum_proven=False,
            matched_static_ablation=gravity_only, execution_outcome_used=False,
            cache_policy="exact mathematical LP input cache, no execution/decision reuse"),
        dict(evaluation=perf_counter()-began, solver_wall=solver_wall, lp_calls=calls, cache_hits=hits,
             required_samples=len(required), evaluated_samples=sum(r.solver_result is not None for r in records)), reason)


def rank_m2_results(results, input_ids):
    """Reuse M1's order/tie/tail policy via a common-context-only projection."""
    rows = tuple(results)
    if len({r.method_id for r in rows}) > 1:
        raise ValueError("do not mix dynamic and static-ablation methods")
    projected = [SimpleNamespace(candidate_id=r.candidate_id, task_name=r.task_name, status=r.status,
        worst_utilization=r.worst_utilization, provenance={"config_hash": r.provenance["config_hash"],
        "task_set_hash": r.provenance["evaluation_context_hash"]}) for r in rows]
    ranked = rank_results(projected, input_ids)
    by_id = {r.candidate_id: r for r in rows}
    ranked["method_id"] = rows[0].method_id if rows else METHOD_ID
    ranked["scores"] = [by_id[cid] for cid in ranked["ranking"]]
    return ranked
