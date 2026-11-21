"""Certified diagnostics for the original (never projected) required wrench.

All input wrenches are contact forces N / moments N*m, mug-local, about the
ContactSpec reference. Explicit common scaling is diagnostic row scaling only.
Certificates are floating-point, tolerance-qualified evidence, not exact proofs
or predictions of physical grasp success. No selector or execution is called.
"""
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import re
from time import perf_counter

import numpy as np

from grasp_b.wrench_v2.contacts import ContactStatus, _freeze_json, _plain
from grasp_b.wrench_v2.constraints import build_capacity_constraints, validate_constraints
from grasp_b.wrench_v2.gws import build_primitive_wrenches, grasp_matrix, WrenchScaling
from grasp_b.wrench_v2.solver import NumericalTolerances, SOLVER_VERSION, solve_wrench

VERSION = "gws_infeasibility_diagnostics_v1"


class Classification(str, Enum):
    STRUCTURAL = "structural_infeasible"
    CONE = "friction_cone_infeasible"
    WITHIN = "within_declared_budget"
    OVER = "capacity_overrun"
    NUMERICAL = "numerical_failure"
    UNKNOWN = "unknown"
    GEOMETRY = "geometry_invalid"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, kw_only=True)
class DiagnosticTolerances:
    rank_relative: float = 1e-10
    rank_absolute: float = 1e-12
    boundary_factor: float = 10.
    certificate_relative: float = 1e-10
    physical: NumericalTolerances = NumericalTolerances()

    def __post_init__(self):
        for key in ("rank_relative", "rank_absolute", "certificate_relative"):
            value = getattr(self, key)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"positive finite {key} required")
        if not np.isfinite(self.boundary_factor) or self.boundary_factor <= 1:
            raise ValueError("boundary_factor must exceed one")
        if not isinstance(self.physical, NumericalTolerances):
            raise ValueError("NumericalTolerances required")


@dataclass(frozen=True)
class DiagnosticRecord:
    """Deeply immutable JSON record. Runtime is observational, not model identity."""
    payload: object

    def __post_init__(self):
        # Convert NumPy scalar subclasses into JSON-native immutable leaves.
        p = json.loads(json.dumps(_plain(self.payload), allow_nan=False))
        if p.get("version") != VERSION:
            raise ValueError("unsupported diagnostic version")
        Classification(p["classification"])
        for key in ("candidate_hash", "contact_spec_identity", "matrix_hash", "required_wrench_identity"):
            if p.get(key) is not None and not re.fullmatch(r"[0-9a-f]{64}", p[key]):
                raise ValueError(f"invalid {key}")
        for key, size in (("reference_point_m", 3), ("required_wrench", 6)):
            if p.get(key) is not None:
                value = np.asarray(p[key], float)
                if value.shape != (size,) or not np.isfinite(value).all():
                    raise ValueError(f"invalid {key}")
        if p.get("rank") is not None and (type(p["rank"]) is not int or not 0 <= p["rank"] <= 6):
            raise ValueError("invalid rank")
        WrenchScaling(**p["scaling"])
        json.dumps(p, allow_nan=False)
        expected = p.pop("record_hash", None)
        actual = fingerprint(p)
        if expected is not None and expected != actual:
            raise ValueError("diagnostic checksum mismatch")
        p["record_hash"] = actual
        object.__setattr__(self, "payload", _freeze_json(p))

    @property
    def classification(self):
        return Classification(self.payload["classification"])

    def to_dict(self):
        return _plain(self.payload)

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, text):
        p = json.loads(text)
        if "record_hash" not in p:
            raise ValueError("serialized diagnostic checksum required")
        return cls(p)


def _scale(scaling):
    if not isinstance(scaling, WrenchScaling):
        raise ValueError("explicit fixed WrenchScaling required")
    return np.array([1 / scaling.reference_force_n] * 3 +
                    [1 / (scaling.reference_force_n * scaling.characteristic_length_m)] * 3)


def _limits(w, tol):
    return np.array([tol.physical.force_absolute_n] * 3 +
                    [tol.physical.torque_absolute_nm] * 3) + tol.physical.relative * np.abs(w)


def verify_certificate(g, a, wrench, *, scaling, certificate, tolerances=None):
    """Independently recompute certificate inequalities on original matrices."""
    tol = tolerances or DiagnosticTolerances()
    try:
        s = _scale(scaling)
        g, a, w = np.asarray(g, float), np.asarray(a, float), np.asarray(wrench, float)
        y = np.asarray(certificate["y_scaled"], float)
        if (g.ndim != 2 or a.ndim != 2 or g.shape[0] != 6 or a.shape[0] != 6
                or w.shape != (6,) or y.shape != (6,)
                or not all(np.isfinite(x).all() for x in (g, a, w, y))
                or not np.isclose(np.linalg.norm(y), 1., atol=1e-8, rtol=0)):
            return False
        yg, ya, yw = y @ (s[:, None] * g), y @ (s[:, None] * a), float(y @ (s * w))
        margin = tol.boundary_factor * np.sum(np.abs(y) * s * _limits(w, tol))
        if certificate["kind"] == "left_nullspace":
            return bool(np.max(np.abs(yg)) <= tol.certificate_relative * max(1., np.linalg.norm(s[:, None] * g))
                        and np.max(np.abs(ya)) <= tol.certificate_relative * max(1., np.linalg.norm(s[:, None] * a))
                        and abs(yw) > margin)
        if certificate["kind"] == "farkas":
            return bool(np.min(ya) >= -tol.certificate_relative * max(1., np.linalg.norm(s[:, None] * a))
                        and yw < -margin)
    except (ValueError, TypeError, KeyError, FloatingPointError):
        pass
    return False


def diagnose(spec, primitives, required_wrench, *, scaling, constraints=None,
             reference_point_m=None, tolerances=None):
    """Structural -> cone -> capacity; unknown parameters never get defaults.

    Identity/invalid input is UNKNOWN with a reason. Geometry-invalid specs are
    not sent to numerical construction. Supply the request reference explicitly
    when crossing an API boundary; otherwise the spec reference is recorded.
    """
    start = perf_counter()
    tol = tolerances or DiagnosticTolerances()
    if not isinstance(tol, DiagnosticTolerances):
        raise ValueError("DiagnosticTolerances required")
    s = _scale(scaling)
    r = dict(version=VERSION, candidate_id=spec.candidate_id, candidate_hash=spec.candidate_hash,
             contact_spec_identity=fingerprint(spec.to_dict()), reference_point_m=list(spec.reference_point_m),
             object_frame="mug", units={"force": "N", "torque": "N*m"},
             scaling=scaling.to_dict(), tolerances=asdict(tol), solver_version=SOLVER_VERSION,
             classification="unknown", numerical_status="not_run", matrix_hash=None,
             required_wrench=None, required_wrench_identity=None, certificate=None, rank=None,
             parameter_provenance={}, timings_s={}, lp_calls={"cone": 0, "certificate": 0, "capacity": 0})

    def finish(kind, reason):
        r.update(classification=kind.value, reason=reason, elapsed_s=perf_counter() - start)
        return DiagnosticRecord(r)

    try:
        w = np.asarray(required_wrench, float)
        if w.shape != (6,) or not np.isfinite(w).all():
            return finish(Classification.UNKNOWN, "invalid required wrench")
        r.update(required_wrench=w.tolist(), required_wrench_identity=fingerprint(dict(
            wrench=w.tolist(), frame="mug", reference_point_m=list(spec.reference_point_m))))
        if reference_point_m is not None and tuple(reference_point_m) != spec.reference_point_m:
            return finish(Classification.UNKNOWN, "request reference-point mismatch")
        if spec.status is ContactStatus.INVALID:
            return finish(Classification.GEOMETRY, ";".join(spec.reasons))
        if spec.status not in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
            return finish(Classification.UNKNOWN, "geometry unsupported/unknown")
        if primitives is None:
            return finish(Classification.UNKNOWN, "missing primitives/friction")
        # Rebuild from declared contact geometry and explicit friction to prevent
        # a stale/tampered matrix or metadata being diagnosed under another ID.
        supplied = None if all(v == "contact_spec" for v in primitives.friction_origins) else primitives.friction_values
        rebuilt = build_primitive_wrenches(spec, friction=supplied,
                                          k=primitives.requested_directions, scaling=primitives.scaling)
        if fingerprint(rebuilt.to_dict()) != fingerprint(primitives.to_dict()):
            return finish(Classification.UNKNOWN, "primitive/spec identity mismatch")
        a, g = np.array(primitives.matrix, copy=True), grasp_matrix(spec)
        r["matrix_hash"] = fingerprint(dict(G=g.tolist(), A=a.tolist()))
        r["parameter_provenance"] = dict(primitives=primitives.to_dict(), geometry=spec.to_dict())
        if constraints is not None:
            validate_constraints(primitives, constraints)
        t = perf_counter()
        sg, sa, sw = s[:, None] * g, s[:, None] * a, s * w
        u, singular, _ = np.linalg.svd(sg, full_matrices=True)
        cutoff = max(tol.rank_absolute, tol.rank_relative * singular[0])
        rank = int(np.count_nonzero(singular > cutoff))
        primitive_singular = np.linalg.svd(sa, compute_uv=False)
        r.update(rank=rank, singular_values=singular.tolist(), rank_tolerance=cutoff,
                 primitive_rank=int(np.count_nonzero(primitive_singular > max(
                     tol.rank_absolute, tol.rank_relative * primitive_singular[0]))),
                 primitive_singular_values=primitive_singular.tolist(), left_nullspace_scaled=u[:, rank:].T.tolist())
        residual = sw - u[:, :rank] @ (u[:, :rank].T @ sw)
        r.update(projection_residual_scaled=residual.tolist(), projection_residual_physical=(residual / s).tolist())
        r["timings_s"]["svd_projection"] = perf_counter() - t
        near_rank = np.any((singular >= cutoff / tol.boundary_factor) &
                           (singular <= cutoff * tol.boundary_factor))
        if near_rank:
            r["numerical_status"] = "near_rank_boundary"
            return finish(Classification.UNKNOWN, "singular value near declared rank boundary")
        ratio = float(np.max(np.abs(residual / s) / _limits(w, tol)))
        r["projection_residual_ratio"] = ratio
        if ratio > tol.boundary_factor:
            t = perf_counter()
            y = residual / np.linalg.norm(residual)
            cert = dict(kind="left_nullspace", y_scaled=y.tolist(), y_G_max=float(np.max(np.abs(y @ sg))),
                        y_A_max=float(np.max(np.abs(y @ sa))), y_w=float(y @ sw))
            valid = verify_certificate(g, a, w, scaling=scaling, certificate=cert, tolerances=tol)
            r["timings_s"]["structural_witness"] = perf_counter() - t
            r["certificate"] = cert
            r["numerical_status"] = "certificate_verified" if valid else "certificate_unverified"
            return finish(Classification.STRUCTURAL if valid else Classification.UNKNOWN,
                          "outside G range" if valid else "insufficient structural certificate")
        if ratio > 1:
            return finish(Classification.UNKNOWN, "projection residual near acceptance boundary")
        try:
            import scipy
            from scipy.optimize import linprog
        except ImportError:
            return finish(Classification.UNKNOWN, "SciPy dependency missing")
        r["solver_backend"] = dict(name="scipy.optimize.linprog/highs", version=scipy.__version__)
        t = perf_counter()
        options = dict(primal_feasibility_tolerance=tol.physical.backend_feasibility,
                       dual_feasibility_tolerance=tol.physical.backend_feasibility)
        r["lp_calls"]["cone"] = 1
        cone = linprog(np.zeros(a.shape[1]), A_eq=sa, b_eq=sw, bounds=(0, None),
                       method="highs", options=options)
        r["timings_s"]["cone_lp"] = perf_counter() - t
        r["cone_solver"] = dict(status=int(cone.status), message=cone.message, options=options)
        if cone.status == 2:
            t = perf_counter()
            r["lp_calls"]["certificate"] = 1
            dual = linprog(sw, A_ub=-sa.T, b_ub=np.zeros(a.shape[1]), bounds=[(-1, 1)] * 6,
                           method="highs", options=options)
            r["timings_s"]["cone_certificate"] = perf_counter() - t
            if dual.status == 0 and np.linalg.norm(dual.x) > 0:
                y = dual.x / np.linalg.norm(dual.x)
                cert = dict(kind="farkas", y_scaled=y.tolist(), min_y_A=float(np.min(y @ sa)), y_w=float(y @ sw))
                r["certificate"] = cert
                if verify_certificate(g, a, w, scaling=scaling, certificate=cert, tolerances=tol):
                    r["numerical_status"] = "certificate_verified"
                    return finish(Classification.CONE, "original wrench outside nonnegative primitive cone")
            return finish(Classification.UNKNOWN, "LP infeasible without verified cone certificate")
        if cone.status != 0:
            r["numerical_status"] = "cone_solver_failure"
            return finish(Classification.NUMERICAL, "cone LP did not produce a usable result")
        eq = a @ cone.x - w
        r["cone_primal"] = dict(coefficients_n=cone.x.tolist(), equilibrium_residual=eq.tolist(),
                               minimum_coefficient_n=float(np.min(cone.x)))
        if (not np.isfinite(cone.x).all() or np.any(np.abs(eq) > _limits(w, tol))
                or np.min(cone.x) < -tol.physical.coefficient_absolute_n):
            r["numerical_status"] = "invalid_cone_primal"
            return finish(Classification.NUMERICAL, "cone optimal reply failed original physical residual checks")
        capacities = constraints if constraints is not None else build_capacity_constraints(primitives)
        r["parameter_provenance"]["capacity"] = capacities.to_dict()
        result = solve_wrench(primitives, w, constraints=capacities, scaling=scaling, tolerances=tol.physical)
        r["capacity_result"] = result.to_dict()
        r["lp_calls"]["capacity"] = int(result.diagnostics.get("linprog_calls", bool(result.diagnostics.get("lp_called"))))
        r["timings_s"]["capacity_lp"] = result.runtime_s
        r["numerical_status"] = result.status.value
        if result.status.value == "optimal":
            return finish(Classification.OVER if result.utilization > 1 else Classification.WITHIN,
                          "finite optimal utilization; not physical success")
        if result.status.value == "missing_physics_parameters":
            return finish(Classification.UNKNOWN, "missing declared force capacities")
        if result.status.value in ("dependency_missing", "invalid_input", "infeasible"):
            return finish(Classification.UNKNOWN, "capacity status requires further evidence; inspect capacity_result")
        return finish(Classification.NUMERICAL, "capacity solver failure")
    except (ValueError, TypeError, AttributeError, np.linalg.LinAlgError) as exc:
        return finish(Classification.UNKNOWN, f"invalid input or insufficient evidence: {exc}")
