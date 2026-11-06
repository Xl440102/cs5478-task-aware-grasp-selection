"""m1_gws_v2: finite quasi-static TWS worst utilization, not legacy M1.

No oracle/runner/results imports. All scenario outcomes here are LP results,
not physical execution labels. Approximate geometry remains approximate.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from time import perf_counter

import numpy as np

from .adapter import build_contact_spec
from .contacts import ContactModel, ContactSpec, ContactStatus, FingerID, _freeze_json, _plain
from .constraints import CapacityValue, ConstraintStatus, build_capacity_constraints
from .gws import FrictionValue, PrimitiveWrenchData, UnknownFrictionError, build_primitive_wrenches, _array, _text
from .solver import NumericalTolerances, SolverStatus, solve_wrench
from .tasks import (TaskWrenchSet, RigidPayload, axis_disturbances, build_task_wrench_set,
                    pour_orientations_x, required_wrench_for_primitives)

METHOD_ID = "m1_gws_v2"
M1_VERSION = "finite_tws_worst_utilization_v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class M1Config:
    name: str
    source: str
    friction: FrictionValue | None
    finger_capacities: Mapping
    reference_point_m: tuple
    k: int
    contact_model: ContactModel = ContactModel.PCWF
    require_local_limits: bool = False
    tolerances: NumericalTolerances = field(default_factory=NumericalTolerances)
    version: str = field(default="m1_physics_config_v1", init=False)

    def __post_init__(self):
        _text(self.name, "config name")
        _text(self.source, "config source")
        if self.friction is not None and not isinstance(self.friction, FrictionValue):
            raise ValueError("explicit FrictionValue or None required")
        if not isinstance(self.finger_capacities, Mapping) or any(
                not isinstance(k, FingerID) or (v is not None and not isinstance(v, CapacityValue))
                for k, v in self.finger_capacities.items()):
            raise ValueError("FingerID -> CapacityValue/None required; no motor conversion")
        from types import MappingProxyType
        object.__setattr__(self, "finger_capacities", MappingProxyType(dict(self.finger_capacities)))
        object.__setattr__(self, "reference_point_m", tuple(map(float, _array(self.reference_point_m, shape=(3,)))))
        if type(self.k) is not int or self.k not in (8, 16, 32):
            raise ValueError("k must be 8/16/32")
        if self.contact_model is not ContactModel.PCWF:
            raise ValueError("only explicit PCWF supported; no soft-finger substitution")
        if type(self.require_local_limits) is not bool or not isinstance(self.tolerances, NumericalTolerances):
            raise ValueError("invalid local-limit policy or tolerances")

    def to_dict(self):
        values = _plain(self)
        values["finger_capacities"] = {key.value: _plain(value) for key, value in self.finger_capacities.items()}
        return dict(values, calibration_policy="per-parameter flags; no claim of calibrated physics",
                    normalization="none: physical N and N*m", method_id=METHOD_ID)


def development_config():
    """Explicit EXPERIMENTAL fixture, never selected implicitly by scoring."""
    source = "Task04 development assumption; uncalibrated, not motor force or legacy physics"
    return M1Config(name="experimental_mu05_capacity5_k16", source=source,
        friction=FrictionValue(coefficient=.5, source=source, calibrated=False),
        finger_capacities={f: CapacityValue(normal_force_n=5., source=source, calibrated=False) for f in FingerID},
        reference_point_m=(0., 0., 0.), k=16)


def development_task_sets(body, config):
    """Task parameters are serialized in every returned TWS. No formal freeze.

    Transport and donor Handover intentionally use IDENTICAL loads: no evidence
    for different task-specific disturbance budgets is silently invented.
    """
    import math
    ds = axis_disturbances(horizontal_force_n=.1, vertical_force_n=0., application_point_m=(0, 0, 0),
                           source="Task04 development assumed +/-0.1 N world horizontal; not measured acceleration")
    common = dict(reference_point_m=config.reference_point_m, gravity_world_m_s2=(0, 0, -9.81),
                  context_source="A declared gravity; explicit mug reference; Task04 development scenarios", disturbances=ds)
    result = {name: build_task_wrench_set(name, body, **common)
              for name in ("transport_place", "handover_transfer")}
    result["pour_water"] = build_task_wrench_set("pour_water", body, **common,
        orientations=pour_orientations_x((0., math.radians(-55), math.radians(-110)),
            source="A nominal -110 deg endpoint; assumed finite quasi-static intermediates"),
        payload_cases=(RigidPayload(mass_kg=.1, com_m=(0, 0, 0),
            source="A configured water mass; assumed rigid COM origin, no PBF equivalence"),))
    return result


@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    spec: ContactSpec
    config: M1Config
    primitives: object
    constraints: object
    status: str | None
    reason: str | None
    timings_s: Mapping

    def __post_init__(self):
        object.__setattr__(self, "timings_s", _freeze_json(self.timings_s))


def prepare_contact_spec(spec, config, *, contact_seconds=0.):
    """Build primitives/capacity ONCE; unknown overrides use declared spec data."""
    if not isinstance(spec, ContactSpec) or not isinstance(config, M1Config):
        raise ValueError("ContactSpec and M1Config required")
    times = dict(contact_spec=contact_seconds, primitives=0., constraints=0.)
    data = capacities = None
    status = reason = None
    geometry_status = {ContactStatus.INVALID: "geometry_invalid", ContactStatus.UNSUPPORTED: "geometry_unsupported",
                       ContactStatus.UNKNOWN: "geometry_unknown"}
    if spec.status in geometry_status:
        status, reason = geometry_status[spec.status], "; ".join(spec.reasons)
    elif spec.reference_point_m != config.reference_point_m:
        status, reason = "invalid_input", "ContactSpec/config reference mismatch; no implicit shift"
    else:
        try:
            t = perf_counter()
            data = build_primitive_wrenches(spec, friction=config.friction, k=config.k)
            times["primitives"] = perf_counter()-t
            t = perf_counter()
            overrides = {g.group_id: config.finger_capacities[g.finger_id] for g in data.capacity_groups
                         if config.finger_capacities.get(g.finger_id) is not None}
            capacities = build_capacity_constraints(data, finger_budgets=overrides,
                                                    require_local_limits=config.require_local_limits)
            times["constraints"] = perf_counter()-t
            if capacities.status is ConstraintStatus.MISSING_PHYSICS_PARAMETERS:
                status, reason = "missing_parameters", "; ".join(capacities.missing_parameters)
        except UnknownFrictionError as exc:
            status, reason = "missing_parameters", str(exc)
        except ValueError as exc:
            status = "geometry_unsupported" if any(p.contact_model is not ContactModel.PCWF for p in spec.contacts) else "invalid_input"
            reason = str(exc)
    return PreparedCandidate(spec, config, data, capacities, status, reason, times)


def prepare_candidate(candidate, object_geometry, gripper_geometry, *, generator_info, config):
    t = perf_counter()
    spec = build_contact_spec(candidate, object_geometry, gripper_geometry, generator_info=generator_info)
    return prepare_contact_spec(spec, config, contact_seconds=perf_counter()-t)


@dataclass(frozen=True, slots=True)
class M1Result:
    candidate_id: str
    candidate_hash: str
    task_name: str
    family: str
    geometry_status: str
    status: str
    worst_utilization: float | None
    score: float | None
    worst_scenario_ids: tuple
    scenario_results: tuple
    provenance: Mapping
    timings_s: Mapping
    reason: str | None

    def __post_init__(self):
        allowed = {"scored", "model_infeasible", "geometry_invalid", "geometry_unsupported", "geometry_unknown",
                   "missing_parameters", "numerical_error", "invalid_input", "dependency_missing", "task_unsupported"}
        if self.status not in allowed:
            raise ValueError("unknown M1 status")
        if self.status == "scored":
            if self.worst_utilization is None or not np.isfinite(self.worst_utilization) or self.worst_utilization < 0 or self.score != 1-self.worst_utilization:
                raise ValueError("scored requires finite utilization and Q=1-rho")
        elif self.worst_utilization is not None or self.score is not None or self.worst_scenario_ids:
            raise ValueError("incomplete results cannot expose a complete score/worst case")
        object.__setattr__(self, "worst_scenario_ids", tuple(self.worst_scenario_ids))
        object.__setattr__(self, "scenario_results", tuple((sid, r) for sid, r in self.scenario_results))
        object.__setattr__(self, "provenance", _freeze_json(self.provenance))
        object.__setattr__(self, "timings_s", _freeze_json(self.timings_s))

    def to_dict(self):
        result = _plain(self)
        result["scenario_results"] = [dict(scenario_id=sid, solver_result=r.to_dict()) for sid, r in self.scenario_results]
        return dict(method_id=METHOD_ID, version=M1_VERSION, **result)


def score_prepared(prepared, task_set):
    """Complete evaluation: never short circuit infeasible/error scenarios.

    Mixed-status precedence is error > missing > infeasible. All individual
    statuses are retained; NO partial max is exposed as a full score.
    """
    if not isinstance(prepared, PreparedCandidate) or not isinstance(task_set, TaskWrenchSet):
        raise ValueError("PreparedCandidate and TaskWrenchSet required")
    start = perf_counter()
    spec, config = prepared.spec, prepared.config
    status, reason = prepared.status, prepared.reason
    results = []
    if status is None and (not isinstance(prepared.primitives, PrimitiveWrenchData) or
            prepared.primitives.candidate_id != spec.candidate_id or
            prepared.primitives.candidate_hash != spec.candidate_hash or
            prepared.primitives.reference_point_m != config.reference_point_m or prepared.constraints is None):
        status, reason = "invalid_input", "prepared primitives/spec/config identity mismatch"
    if status is None:
        object_asset = spec.provenance.get("object_asset_sha256")
        body_assets = []
        for scenario in task_set.scenarios:
            source = scenario.object_parameters.provenance
            if "dry_object" in source:
                source = source["dry_object"]["provenance"]
            body_assets.append(source.get("asset_sha256"))
        if task_set.unsupported_reason:
            status, reason = "task_unsupported", task_set.unsupported_reason
        elif object_asset is not None and any(h is not None and h != object_asset for h in body_assets):
            status, reason = "invalid_input", "ContactSpec/TWS object asset hash mismatch"
        elif any(s.reference_point_m != config.reference_point_m for s in task_set.scenarios):
            status, reason = "invalid_input", "TWS/config reference mismatch"
        else:
            for s in task_set.scenarios:
                r = solve_wrench(prepared.primitives, required_wrench_for_primitives(s, prepared.primitives),
                                 constraints=prepared.constraints, tolerances=config.tolerances)
                results.append((s.scenario_id, r))
            states = {r.status for _, r in results}
            if states == {SolverStatus.OPTIMAL} and all(r.utilization is not None and np.isfinite(r.utilization) for _, r in results):
                status = "scored"
            elif states & {SolverStatus.NUMERICAL_ERROR, SolverStatus.UNBOUNDED}:
                status = "numerical_error"
            elif SolverStatus.INVALID_INPUT in states:
                status = "invalid_input"
            elif SolverStatus.DEPENDENCY_MISSING in states:
                status = "dependency_missing"
            elif SolverStatus.MISSING_PHYSICS_PARAMETERS in states:
                status = "missing_parameters"
            elif SolverStatus.INFEASIBLE in states:
                status = "model_infeasible"
            else:
                status = "numerical_error"
            if status != "scored":
                reason = "Incomplete finite-TWS score; inspect every scenario status"
    worst = max(r.utilization for _, r in results) if status == "scored" else None
    worst_ids = tuple(sorted(sid for sid, r in results if r.utilization == worst)) if worst is not None else ()
    provenance = dict(config=config.to_dict(), config_hash=fingerprint(config.to_dict()),
        contact_spec=spec.to_dict(), task_set=task_set.to_dict(), task_set_hash=fingerprint(task_set.to_dict()),
        primitive_fingerprint=None if prepared.constraints is None else prepared.constraints.primitive_fingerprint,
        capacity_constraints=None if prepared.constraints is None else prepared.constraints.to_dict(),
        scenario_status_counts=dict(Counter(r.status.value for _, r in results)),
        initial_feasibility_verified_here=False, execution_outcomes_used=False,
        formula="rho_max=max_s rho_g(w_s); Q=1-rho_max only if ALL s finite optimal")
    return M1Result(spec.candidate_id, spec.candidate_hash, task_set.task_name, spec.family.value,
        spec.status.value, status, worst, None if worst is None else 1-worst, worst_ids, tuple(results), provenance,
        dict(**prepared.timings_s, scenario_solver=sum(r.runtime_s for _, r in results),
             evaluation=perf_counter()-start, scenario_evaluations=len(results),
             lp_calls=sum(int(r.diagnostics.get("linprog_calls", bool(r.diagnostics.get("lp_called")))) for _, r in results)), reason)
