"""Minimum declared-capacity utilization via an optional SciPy HiGHS backend.

Input w is the CONTACT wrench required on the object, in mug coordinates:
force N, torque N*m about data.reference_point_m. Balance an external wrench
by passing its negative. Residuals are always independently checked in physical
units, even if both sides of equilibrium were explicitly row-normalized.
No task rules, motor-force conversion, torsional contacts or execution access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter
from collections.abc import Mapping
from typing import Protocol
import warnings

import numpy as np

from .contacts import _freeze_json, _plain
from .constraints import (
    CapacityConstraints, ConstraintStatus, build_capacity_constraints,
    primitive_fingerprint, validate_constraints,
)
from .gws import (
    NumericalInputError, PrimitiveWrenchData, WrenchScaling, _array, _readonly,
    _scalar, normalize_wrenches,
)

SOLVER_VERSION = "shared_capacity_utilization_v1.2"


class SolverStatus(str, Enum):
    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    NUMERICAL_ERROR = "numerical_error"
    DEPENDENCY_MISSING = "dependency_missing"
    INVALID_INPUT = "invalid_input"
    MISSING_PHYSICS_PARAMETERS = "missing_physics_parameters"


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericalTolerances:
    force_absolute_n: float = 1e-7
    torque_absolute_nm: float = 1e-8
    inequality_absolute_n: float = 1e-7
    coefficient_absolute_n: float = 1e-9
    utilization_absolute: float = 1e-8
    relative: float = 1e-8
    backend_feasibility: float = 1e-9
    backend_small_matrix_value: float = 1e-12

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _scalar(getattr(self, name), name, positive=True))
        if self.backend_feasibility < 1e-10:
            raise NumericalInputError("HiGHS feasibility tolerance must be >=1e-10")
        if self.backend_small_matrix_value < 1e-12:
            raise NumericalInputError("HiGHS small_matrix_value must be >=1e-12")


@dataclass(frozen=True, slots=True, eq=False)
class LPProblem:
    objective: np.ndarray
    equality_matrix: np.ndarray
    equality_rhs: np.ndarray
    inequality_matrix: np.ndarray
    inequality_rhs: np.ndarray
    bounds: tuple
    coefficient_count: int


@dataclass(frozen=True, slots=True)
class BackendReply:
    status: SolverStatus
    x: tuple | None = None
    objective: float | None = None
    message: str = ""
    backend_name: str = "unknown"
    backend_version: str = "unknown"
    metadata: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))
        if self.x is not None:
            object.__setattr__(self, "x", tuple(self.x))


class LPBackend(Protocol):
    def solve(self, problem: LPProblem, tolerances: NumericalTolerances) -> BackendReply: ...


def _load_highs():
    import scipy
    from scipy.optimize import linprog
    return linprog, scipy.__version__


class SciPyHighsBackend:
    def __init__(self, *, method="highs", presolve=True, retry_unknown=True):
        if method not in ("highs", "highs-ds", "highs-ipm") or type(presolve) is not bool or type(retry_unknown) is not bool:
            raise ValueError("explicit HiGHS method and boolean presolve/retry required")
        self.method, self.presolve, self.retry_unknown = method, presolve, retry_unknown

    def solve(self, problem, tolerances):
        try:
            linprog, version = _load_highs()
        except ImportError as exc:
            return BackendReply(SolverStatus.DEPENDENCY_MISSING, message=str(exc),
                                backend_name="scipy_highs", backend_version="not_installed", metadata={"lp_called": False})
        options = {"primal_feasibility_tolerance": tolerances.backend_feasibility,
                   "dual_feasibility_tolerance": tolerances.backend_feasibility,
                   "small_matrix_value": tolerances.backend_small_matrix_value,
                   "presolve": self.presolve}
        # HiGHS' default 1e-9 coefficient cutoff distorted some static Rim
        # fixture LPs. Retain smaller coefficients without editing A or jitter.
        # SciPy forwards this HiGHS option verbatim and emits a known warning;
        # suppress only that exact passthrough notice, retaining provenance.
        attempts = []
        def run(method):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=(
                    r"Unrecognized options detected: \{'small_matrix_value': .*\}\. "
                    r"These will be passed to HiGHS verbatim\."))
                result = linprog(problem.objective, A_ub=problem.inequality_matrix,
                                 b_ub=problem.inequality_rhs, A_eq=problem.equality_matrix,
                                 b_eq=problem.equality_rhs, bounds=problem.bounds, method=method, options=options)
            attempts.append(dict(method=method, raw_status=int(result.status), message=str(result.message),
                                 success=bool(result.success), options=dict(options)))
            return result
        result = run(self.method)
        # Bounded numerical recovery, never a change to A/B/b/w or tolerances.
        # Two-point rank-deficient inconsistent equilibria produced HiGHS
        # Unknown (15) under simplex; IPM resolves the same model as infeasible.
        if self.retry_unknown and self.method != "highs-ipm" and result.status == 4 and "model_status is Unknown" in str(result.message):
            result = run("highs-ipm")
        status = {0: SolverStatus.OPTIMAL, 1: SolverStatus.NUMERICAL_ERROR,
                  2: SolverStatus.INFEASIBLE, 3: SolverStatus.UNBOUNDED,
                  4: SolverStatus.NUMERICAL_ERROR}.get(result.status, SolverStatus.NUMERICAL_ERROR)
        if status is SolverStatus.OPTIMAL and not result.success:
            status = SolverStatus.NUMERICAL_ERROR
        return BackendReply(status, None if result.x is None else tuple(result.x), result.fun,
            str(result.message), "scipy_highs", str(version),
            dict(raw_status=int(result.status), success=bool(result.success),
                 iterations=None if result.nit is None else int(result.nit), method=attempts[-1]["method"],
                 highs_version="not_exposed_by_public_linprog_api", lp_called=True,
                 options=options, attempts=attempts, linprog_calls=len(attempts),
                 recovery_policy="unknown_only_one_ipm_retry_v1" if self.retry_unknown else "disabled",
                 highs_options_passed_through_scipy=["small_matrix_value"]))


def build_lp_problem(data, capacities, wrench, *, scaling=None):
    validate_constraints(data, capacities)
    if capacities.status is not ConstraintStatus.READY:
        raise NumericalInputError("complete finger capacity parameters required")
    w = _array(wrench, shape=(6,))
    a = _array(data.matrix)
    if scaling is not None:
        a = normalize_wrenches(a, scaling=scaling)
        w = normalize_wrenches(w[:, None], scaling=scaling)[:, 0]
    n = a.shape[1]
    objective = np.zeros(n+1)
    objective[-1] = 1.
    return LPProblem(_readonly(objective), _readonly(np.column_stack((a, np.zeros(6)))),
                     _readonly(w), _readonly(np.column_stack((capacities.matrix, -capacities.bounds_n))),
                     _readonly(np.zeros(len(capacities.rows))), ((0., None),)*(n+1), n)


@dataclass(frozen=True, slots=True)
class SolverResult:
    candidate_id: str | None
    candidate_hash: str | None
    primitive_fingerprint: str | None
    reference_point_m: tuple | None
    requested_wrench: tuple | None
    status: SolverStatus
    utilization: float | None
    coefficients_n: tuple | None
    equilibrium_residual: tuple | None
    inequality_residual_n: tuple | None
    feasible_at_declared_capacity: bool | None
    normalization: WrenchScaling | None
    tolerances: NumericalTolerances | None
    backend_name: str
    backend_version: str
    runtime_s: float
    message: str
    assumptions: Mapping
    diagnostics: Mapping

    def __post_init__(self):
        for name in ("assumptions", "diagnostics"):
            object.__setattr__(self, name, _freeze_json(getattr(self, name)))

    def to_dict(self):
        return dict(version=SOLVER_VERSION, force_unit="N", torque_unit="N*m",
                    wrench_frame="mug", **_plain(self))


def _primal_check(data, capacities, w, x, tolerances):
    """Require physical absolute tolerances AND dimensionless backward errors.

    Equilibrium denominator: max(|| |A_block| |lambda| ||inf, ||w_block||inf),
    separately for force and torque. Capacity denominator: max(||B|lambda|||inf,
    ||u b||inf). Zero/zero is zero. These are backward errors, not a claim of
    forward accuracy or of an independently verified optimality certificate.
    """
    x = _array(x, shape=(len(data.columns)+1,))
    coefficients, u = x[:-1], float(x[-1])
    equality = data.matrix@coefficients-w
    inequality = capacities.matrix@coefficients-u*capacities.bounds_n
    if not np.isfinite(equality).all() or not np.isfinite(inequality).all():
        raise NumericalInputError("nonfinite reconstructed residual")
    force_limit = tolerances.force_absolute_n+tolerances.relative*float(np.max(np.abs(w[:3])))
    torque_limit = tolerances.torque_absolute_nm+tolerances.relative*float(np.max(np.abs(w[3:])))
    inequality_limit = tolerances.inequality_absolute_n+tolerances.relative*float(np.max(np.abs(u*capacities.bounds_n)))
    # Separate force/torque backward errors keep units apart. Using the sum
    # of absolute contributions also permits roundoff in cancelling forces.
    # Absolute tolerances alone would accept u=0 for small NONZERO demands.
    contributions = np.abs(data.matrix)@np.abs(coefficients)
    capacity_load = np.abs(capacities.matrix)@np.abs(coefficients)

    def ratio(error, scale):
        return float(error/scale) if scale > 0 else (0. if error == 0 else float("inf"))

    force_error = float(np.max(np.abs(equality[:3])))
    torque_error = float(np.max(np.abs(equality[3:])))
    inequality_error = float(max(0., np.max(inequality)))
    force_scale = float(max(np.max(contributions[:3]), np.max(np.abs(w[:3]))))
    torque_scale = float(max(np.max(contributions[3:]), np.max(np.abs(w[3:]))))
    capacity_scale = float(max(np.max(capacity_load), np.max(np.abs(u*capacities.bounds_n))))
    coefficient_error = float(max(0., -np.min(coefficients)))
    coefficient_scale = float(max(np.max(np.abs(coefficients)), np.max(np.abs(u*capacities.bounds_n))))
    checks = dict(force_residual_max_n=float(np.max(np.abs(equality[:3]))),
                  torque_residual_max_nm=float(np.max(np.abs(equality[3:]))),
                  inequality_violation_max_n=float(max(0., np.max(inequality))),
                  coefficient_min_n=float(np.min(coefficients)),
                  force_tolerance_n=force_limit, torque_tolerance_nm=torque_limit,
                  inequality_tolerance_n=inequality_limit,
                  force_relative_backward_error=ratio(force_error, force_scale),
                  torque_relative_backward_error=ratio(torque_error, torque_scale),
                  inequality_relative_violation=ratio(inequality_error, capacity_scale),
                  nonnegativity_violation_n=coefficient_error,
                  nonnegativity_relative_violation=ratio(coefficient_error, coefficient_scale),
                  relative_tolerance=tolerances.relative)
    valid = (checks["force_residual_max_n"] <= force_limit and checks["torque_residual_max_nm"] <= torque_limit and
             checks["inequality_violation_max_n"] <= inequality_limit and
             checks["coefficient_min_n"] >= -tolerances.coefficient_absolute_n and u >= 0 and
             all(checks[name] <= tolerances.relative for name in (
                 "force_relative_backward_error", "torque_relative_backward_error",
                 "inequality_relative_violation", "nonnegativity_relative_violation")))
    return valid, coefficients, u, equality, inequality, checks


def solve_wrench(data, wrench, *, constraints=None, finger_budgets=None, contact_limits=None,
                 require_local_limits=False, scaling=None, tolerances=None, backend=None):
    """min u: A lambda=w, B lambda<=u b, lambda>=0, u>=0.

    Unknown group budgets stay missing, including for zero wrench. With complete
    nonnegative capacities, EXACT zero wrench has the analytic optimum 0 without
    any LP call. All other optima require a backend. u>1 is still optimal but
    cannot meet the demand at the nominal declared capacity.
    """
    started = perf_counter()
    w = capacities = problem = None
    tolerances_used = None
    fingerprint = None
    assumptions = {}
    backend_attempted = False

    def finish(status, message, *, reply=None, u=None, coefficients=None, equality=None,
               inequality=None, diagnostics=None):
        feasible = ((u <= 1+tolerances_used.utilization_absolute) if status is SolverStatus.OPTIMAL else
                    (False if status is SolverStatus.INFEASIBLE else None))
        return SolverResult(getattr(data, "candidate_id", None), getattr(data, "candidate_hash", None), fingerprint,
            tuple(data.reference_point_m) if isinstance(data, PrimitiveWrenchData) else None,
            None if w is None else tuple(map(float, w)), status, u,
            None if coefficients is None else tuple(map(float, coefficients)),
            None if equality is None else tuple(map(float, equality)),
            None if inequality is None else tuple(map(float, inequality)), feasible,
            scaling if isinstance(scaling, WrenchScaling) else None, tolerances_used,
            (type(backend).__name__ if backend is not None else "scipy_highs") if reply is None and backend_attempted else
            ("not_called" if reply is None else reply.backend_name),
            ("unknown" if backend_attempted else "not_called") if reply is None else reply.backend_version,
            perf_counter()-started, message, assumptions,
            {"backend_attempted": backend_attempted, **({} if diagnostics is None else diagnostics)})

    try:
        if not isinstance(data, PrimitiveWrenchData):
            raise NumericalInputError("PrimitiveWrenchData required")
        w = _array(wrench, shape=(6,))
        fingerprint = primitive_fingerprint(data)
        tolerances_used = NumericalTolerances() if tolerances is None else tolerances
        if not isinstance(tolerances_used, NumericalTolerances):
            tolerances_used = None
            raise NumericalInputError("NumericalTolerances required")
        if scaling is not None and not isinstance(scaling, WrenchScaling):
            raise NumericalInputError("explicit WrenchScaling required")
        if constraints is not None and (finger_budgets is not None or contact_limits is not None or require_local_limits):
            raise NumericalInputError("do not combine built constraints with capacity override arguments")
        capacities = (build_capacity_constraints(data, finger_budgets=finger_budgets, contact_limits=contact_limits,
                                                 require_local_limits=require_local_limits)
                      if constraints is None else constraints)
        validate_constraints(data, capacities)
        assumptions = dict(friction_values=[v.to_dict() for v in data.friction_values],
            friction_origins=list(data.friction_origins), capacity_rows=_plain(capacities.rows),
            missing_parameters=list(capacities.missing_parameters),
            unknown_local_contacts=list(capacities.unknown_local_contacts),
            require_local_limits=capacities.require_local_limits,
            local_limit_policy="unspecified local limits impose no additional constraint; no physical infinity inferred",
            pcwf_only=True, current_trial_outcomes_used=False, optimality_certificate_checked=False)
        if capacities.status is ConstraintStatus.MISSING_PHYSICS_PARAMETERS:
            return finish(SolverStatus.MISSING_PHYSICS_PARAMETERS, "; ".join(capacities.missing_parameters))
        problem = build_lp_problem(data, capacities, w, scaling=scaling)
    except (ValueError, TypeError, OverflowError) as exc:
        return finish(SolverStatus.INVALID_INPUT, str(exc))

    if np.all(w == 0):
        zero = np.zeros(problem.coefficient_count)
        reply = BackendReply(SolverStatus.OPTIMAL, backend_name="analytic_zero", backend_version=SOLVER_VERSION,
                             metadata={"lp_called": False})
        return finish(SolverStatus.OPTIMAL, "Zero force and u=0 are feasible and attain the nonnegative objective lower bound.",
                      reply=reply, u=0., coefficients=zero, equality=np.zeros(6),
                      inequality=np.zeros(len(capacities.rows)), diagnostics=dict(reply.metadata))
    try:
        backend_attempted = True
        reply = (SciPyHighsBackend() if backend is None else backend).solve(problem, tolerances_used)
        if not isinstance(reply, BackendReply) or not isinstance(reply.status, SolverStatus):
            raise NumericalInputError("invalid LP backend reply")
    except ImportError as exc:
        return finish(SolverStatus.DEPENDENCY_MISSING, str(exc))
    except Exception as exc:
        return finish(SolverStatus.NUMERICAL_ERROR, f"backend exception: {type(exc).__name__}: {exc}")

    diagnostics = dict(reply.metadata)
    if reply.status is not SolverStatus.OPTIMAL:
        if reply.status is SolverStatus.INFEASIBLE and reply.x is not None:
            try:
                valid, *_ = _primal_check(data, capacities, w, reply.x, tolerances_used)
                if valid:
                    return finish(SolverStatus.NUMERICAL_ERROR, "Backend reports infeasible but supplies a feasible primal point.",
                                  reply=reply, diagnostics=diagnostics)
            except (ValueError, TypeError):
                pass
        if reply.status is SolverStatus.UNBOUNDED:
            diagnostics["unexpected_unbounded_report"] = "u>=0 bounds the objective below; backend diagnosis is not a physical conclusion"
        return finish(reply.status, reply.message, reply=reply, diagnostics=diagnostics)
    try:
        valid, coefficients, u, equality, inequality, checks = _primal_check(data, capacities, w, reply.x, tolerances_used)
        diagnostics.update(checks)
        objective = _scalar(reply.objective, "backend objective")
        if not valid or abs(objective-u) > tolerances_used.utilization_absolute+tolerances_used.relative*abs(u):
            return finish(SolverStatus.NUMERICAL_ERROR, "Optimal backend status contradicts physical residuals, bounds, or objective.",
                          reply=reply, coefficients=coefficients, equality=equality, inequality=inequality, diagnostics=diagnostics)
        return finish(SolverStatus.OPTIMAL, reply.message, reply=reply, u=u, coefficients=coefficients,
                      equality=equality, inequality=inequality, diagnostics=diagnostics)
    except (ValueError, TypeError, OverflowError) as exc:
        return finish(SolverStatus.NUMERICAL_ERROR, f"invalid optimal solution: {exc}", reply=reply, diagnostics=diagnostics)
