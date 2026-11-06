"""Force-only grasp maps and PCWF wrench generators, not a GWS quality test.

All geometry is mug-local. Forces act ON the object; object outward normals
therefore have the opposite sign from compressive contact forces. Reference
torques are computed once as (position - reference) cross force.

Each primitive represents one newton of normal force. Its coefficients in a
future nonnegative force combination have numerical units N: top rows map to
N, bottom rows to N*m. Columns are directions, NOT independent force budgets.
Only declared finger groups/local limits may supply later LP constraints.
No LP, hull, force closure, epsilon, task ranking, or execution is computed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
from numbers import Real
from time import perf_counter
from types import MappingProxyType

import numpy as np

from .contacts import (
    ContactModel, ContactParameters, ContactSpec, ContactStatus, FingerCapacityGroup, FingerID,
)

GWS_INPUT_VERSION = "pcwf_wrench_primitives_v1"
_UNIT_TOLERANCE = 1e-8
_ANGLES = 2*np.pi*np.arange(32)/32
_COS, _SIN = np.cos(_ANGLES), np.sin(_ANGLES)
_COS.setflags(write=False)
_SIN.setflags(write=False)


class NumericalInputError(ValueError):
    pass


class UnusableContactSpecError(NumericalInputError):
    pass


class UnknownFrictionError(NumericalInputError):
    pass


def _scalar(value, name, *, positive=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise NumericalInputError(f"{name} must be an explicit finite real number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise NumericalInputError(f"{name} must be finite") from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise NumericalInputError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise NumericalInputError(f"{name} must be explicit nonempty text")
    return value


def _array(value, *, shape=None):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or (shape is not None and raw.shape != shape):
        raise NumericalInputError(f"expected real numeric array with shape {shape}")
    if not isinstance(value, np.ndarray) and any(isinstance(v, (bool, np.bool_))
            for v in np.asarray(value, dtype=object).flat):
        raise NumericalInputError("booleans are not physical numeric inputs")
    result = np.array(raw, dtype=np.float64, copy=True)
    if not np.isfinite(result).all():
        raise NumericalInputError("array must not contain NaN or Inf")
    return result


def _readonly(value):
    result = _array(value)
    result.setflags(write=False)
    return result


def _wrench_matrix(value):
    result = _array(value)
    if result.ndim != 2 or result.shape[0] != 6 or result.shape[1] == 0:
        raise NumericalInputError("expected nonempty six-row wrench matrix")
    return result


def _usable(spec):
    if not isinstance(spec, ContactSpec):
        raise UnusableContactSpecError("ContactSpec v2 required")
    if spec.status not in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
        raise UnusableContactSpecError(f"status_{spec.status.value}: {';'.join(spec.reasons)}")
    if not spec.contacts:
        raise UnusableContactSpecError("no contacts")
    for i, point in enumerate(spec.contacts):
        if point.contact_model is not ContactModel.PCWF:
            raise UnusableContactSpecError(f"contact_{i}: unsupported model {point.contact_model.value}; PCWF required")


def skew(vector):
    """[r]_x, such that [r]_x @ f == cross(r, f). Input position units: m."""
    x, y, z = _array(vector, shape=(3,))
    return _readonly(((0., -z, y), (z, 0., -x), (-y, x, 0.)))


def contact_frame(normal_outward):
    """Contact-to-mug R=[t1,t2,inward], deterministic and right handed.

    Choose the Cartesian helper least aligned with inward, then project it
    into the tangent plane. Only a working copy of an already unit normal is
    normalized to rounding precision; invalid normals are never repaired.
    Tangent phase is axis-defined, so arbitrary rotation can change the phase
    of a finite cone polygon; this is not a physical material orientation.
    """
    outward = _array(normal_outward, shape=(3,))
    length = float(np.linalg.norm(outward))
    if not math.isclose(length, 1., rel_tol=0., abs_tol=_UNIT_TOLERANCE):
        raise NumericalInputError("outward normal must be a unit vector")
    inward = -outward/length
    helper = np.eye(3)[int(np.argmin(np.abs(inward)))]
    tangent1 = helper-float(helper@inward)*inward
    tangent1 /= np.linalg.norm(tangent1)
    tangent2 = np.cross(inward, tangent1)
    return _readonly(np.column_stack((tangent1, tangent2, inward)))


def point_grasp_matrix(position_m, reference_point_m, *, force_frame=None):
    """Unrestricted 3D point-force map [R; skew(p-c)R]. No contact moment."""
    p, c = _array(position_m, shape=(3,)), _array(reference_point_m, shape=(3,))
    r = np.eye(3) if force_frame is None else _array(force_frame, shape=(3, 3))
    if not np.allclose(r.T@r, np.eye(3), atol=1e-8, rtol=0.) or not math.isclose(
            float(np.linalg.det(r)), 1., abs_tol=1e-8, rel_tol=0.):
        raise NumericalInputError("force_frame must be a proper orthonormal rotation")
    with np.errstate(over="ignore", invalid="ignore"):
        return _readonly(np.vstack((r, skew(p-c)@r)))


def grasp_matrix(spec, *, coordinates="mug"):
    """6 x (3*n) force map. No friction/capacity constraint is imposed here.

    mug: force triples are mug-local. contact: triples are (t1,t2,inward).
    For either convention the resulting wrench remains mug-local about c.
    """
    _usable(spec)
    if coordinates not in ("mug", "contact"):
        raise NumericalInputError("coordinates must be 'mug' or 'contact'")
    return _readonly(np.hstack([point_grasp_matrix(
        p.position_m, spec.reference_point_m,
        force_frame=contact_frame(p.normal_outward) if coordinates == "contact" else None)
        for p in spec.contacts]))


@dataclass(frozen=True, slots=True, kw_only=True)
class FrictionValue:
    coefficient: float
    source: str
    calibrated: bool = False

    def __post_init__(self):
        object.__setattr__(self, "coefficient", _scalar(self.coefficient, "mu"))
        _text(self.source, "friction source")
        if self.source == "unknown" or type(self.calibrated) is not bool:
            raise NumericalInputError("known mu requires explicit source and boolean calibrated flag")

    def to_dict(self):
        return dict(coefficient=self.coefficient, source=self.source, calibrated=self.calibrated)


def _directions(k):
    if type(k) is not int or k not in (8, 16, 32):
        raise NumericalInputError("friction directions must be k=8,16,32")
    return k


def friction_cone(normal_outward, *, mu, k=8):
    """3 x k inward force directions with UNIT NORMAL component, not unit norm.

    k=8/16/32 share the same 32-angle grid. mu=0 collapses to ONE normal
    direction (index 0), avoiding redundant copies. None is never a default mu.
    """
    mu, k = _scalar(mu, "mu"), _directions(k)
    r = contact_frame(normal_outward)
    if mu == 0:
        return _readonly(r[:, 2:3])
    step = 32//k
    with np.errstate(over="ignore", invalid="ignore"):
        return _readonly(r[:, 2:3]+mu*(r[:, 0:1]*_COS[None, ::step]+r[:, 1:2]*_SIN[None, ::step]))


@dataclass(frozen=True, slots=True, kw_only=True)
class WrenchScaling:
    reference_force_n: float
    characteristic_length_m: float

    def __post_init__(self):
        for name in ("reference_force_n", "characteristic_length_m"):
            object.__setattr__(self, name, _scalar(getattr(self, name), name, positive=True))

    def to_dict(self):
        return dict(reference_force_n=self.reference_force_n,
                    characteristic_length_m=self.characteristic_length_m)


def normalize_wrenches(matrix, *, scaling: WrenchScaling):
    """Explicit row scaling; returns a new matrix, never changes physical data.

    F*=N, L=m. A caller must freeze the SAME scales for every compared grasp.
    Neither scale is inferred from candidate coordinates or force budgets.
    """
    a = _wrench_matrix(matrix)
    if not isinstance(scaling, WrenchScaling):
        raise NumericalInputError("explicit WrenchScaling required")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        a[:3] /= scaling.reference_force_n
        a[3:] /= scaling.reference_force_n
        a[3:] /= scaling.characteristic_length_m
    return _readonly(a)


@dataclass(frozen=True, slots=True)
class PrimitiveColumn:
    contact_index: int
    finger_id: FingerID
    capacity_group_id: str
    friction_direction_index: int
    surface_id: str

    def to_dict(self):
        return dict(contact_index=self.contact_index, finger_id=self.finger_id.value,
                    capacity_group_id=self.capacity_group_id,
                    friction_direction_index=self.friction_direction_index, surface_id=self.surface_id)


@dataclass(frozen=True, slots=True, eq=False)
class PrimitiveWrenchData:
    candidate_id: str
    candidate_hash: str
    reference_point_m: tuple
    matrix: np.ndarray
    columns: tuple[PrimitiveColumn, ...]
    friction_values: tuple[FrictionValue, ...]
    friction_origins: tuple[str, ...]
    capacity_groups: tuple[FingerCapacityGroup, ...]
    contact_parameters: tuple[ContactParameters, ...]
    requested_directions: int
    effective_directions: tuple[int, ...]
    scaling: WrenchScaling | None
    normalized_matrix: np.ndarray | None

    @property
    def columns_by_capacity_group(self):
        return MappingProxyType({g.group_id: tuple(i for i, col in enumerate(self.columns)
                                                  if col.capacity_group_id == g.group_id)
                                 for g in self.capacity_groups})

    def to_dict(self):
        return dict(version=GWS_INPUT_VERSION, candidate_id=self.candidate_id, candidate_hash=self.candidate_hash,
                    object_frame="mug", reference_point_m=list(self.reference_point_m),
                    dtype="float64",
                    force_unit="N", torque_unit="N*m", coefficient_unit="N", primitive_normal_force_n=1.,
                    matrix=self.matrix.tolist(), columns=[c.to_dict() for c in self.columns],
                    friction_values=[v.to_dict() for v in self.friction_values],
                    friction_origins=list(self.friction_origins),
                    capacity_groups=[g.to_dict() for g in self.capacity_groups],
                    contact_parameters=[p.to_dict() for p in self.contact_parameters],
                    requested_directions=self.requested_directions,
                    effective_directions=list(self.effective_directions),
                    scaling=None if self.scaling is None else self.scaling.to_dict(),
                    normalized_matrix=None if self.normalized_matrix is None else self.normalized_matrix.tolist(),
                    capacity_constraints_constructed=False, force_closure_tested=False)


def _friction_values(spec, supplied):
    if supplied is None:
        values = []
        for i, p in enumerate(spec.contacts):
            if p.parameters.friction_coefficient is None:
                raise UnknownFrictionError(f"contact_{i}: unknown mu; explicit input required")
            values.append(FrictionValue(coefficient=p.parameters.friction_coefficient,
                                        source=p.parameters.source, calibrated=p.parameters.calibrated))
        return tuple(values), ("contact_spec",)*len(values)
    if isinstance(supplied, FrictionValue):
        return (supplied,)*len(spec.contacts), ("external_explicit",)*len(spec.contacts)
    if not isinstance(supplied, (tuple, list)) or len(supplied) != len(spec.contacts) or any(
            not isinstance(v, FrictionValue) for v in supplied):
        raise NumericalInputError("friction must be FrictionValue or one explicit value per contact")
    return tuple(supplied), ("external_explicit",)*len(supplied)


def build_primitive_wrenches(spec, *, friction=None, k=8, scaling=None):
    """Return A_g with contact-major, direction-minor column order.

    Explicit external mu is recorded separately from unchanged ContactSpec
    parameters. No capacity is inferred, summed, or multiplied by k or samples.
    """
    _usable(spec)
    k = _directions(k)
    if scaling is not None and not isinstance(scaling, WrenchScaling):
        raise NumericalInputError("scaling must be explicit WrenchScaling or None")
    values, origins = _friction_values(spec, friction)
    matrices, columns, effective = [], [], []
    for i, (point, value) in enumerate(zip(spec.contacts, values)):
        directions = friction_cone(point.normal_outward, mu=value.coefficient, k=k)
        with np.errstate(over="ignore", invalid="ignore"):
            matrix = point_grasp_matrix(point.position_m, spec.reference_point_m)@directions
        matrices.append(matrix)
        effective.append(directions.shape[1])
        columns.extend(PrimitiveColumn(i, point.finger_id, point.capacity_group_id, j, point.surface_id)
                       for j in range(directions.shape[1]))
    a = _readonly(np.hstack(matrices))
    return PrimitiveWrenchData(spec.candidate_id, spec.candidate_hash, spec.reference_point_m,
        a, tuple(columns), values, origins, spec.capacity_groups,
        tuple(p.parameters for p in spec.contacts), k, tuple(effective), scaling,
        None if scaling is None else normalize_wrenches(a, scaling=scaling))


@dataclass(frozen=True, slots=True)
class RankDiagnostics:
    matrix_name: str
    matrix_shape: tuple[int, int]
    matrix_sha256: str
    scaling: WrenchScaling | None
    tolerance: float
    tolerance_rule: str
    numerical_rank: int
    singular_values: tuple[float, ...]
    nonzero_spectrum_condition: float | None
    six_dimensional_condition: float | None
    degenerate_reasons: tuple[str, ...]

    def to_dict(self):
        return dict(matrix_name=self.matrix_name, matrix_shape=list(self.matrix_shape),
                    dtype="float64",
                    matrix_sha256=self.matrix_sha256,
                    scaling=None if self.scaling is None else self.scaling.to_dict(),
                    tolerance=self.tolerance, tolerance_rule=self.tolerance_rule,
                    numerical_rank=self.numerical_rank, singular_values=list(self.singular_values),
                    nonzero_spectrum_condition=self.nonzero_spectrum_condition,
                    six_dimensional_condition=self.six_dimensional_condition,
                    unspanned_wrench_dimensions=6-self.numerical_rank,
                    degenerate_reasons=list(self.degenerate_reasons),
                    force_closure_tested=False, task_feasibility_tested=False,
                    notes=["Rank=6 is not force closure.", "Rank<6 does not rule out every task wrench.",
                           "Physical force/torque rows mix units unless explicit scaling is supplied."])


def rank_diagnostics(matrix, *, matrix_name, absolute_tolerance=None, scaling=None):
    """SVD of the explicitly identified raw/scaled matrix. No jitter or repair.

    Default threshold is eps*max(shape)*sigma_max. With <6 columns SVD returns
    fewer than six values; missing wrench dimensions are still reported.
    Undefined/infinite conditions are represented by None, not JSON NaN/Inf.
    """
    _text(matrix_name, "matrix_name")
    a = _wrench_matrix(matrix)
    if scaling is not None:
        a = normalize_wrenches(a, scaling=scaling)
    try:
        values = np.linalg.svd(a, compute_uv=False)
    except np.linalg.LinAlgError as exc:
        raise NumericalInputError("SVD did not converge") from exc
    if not np.isfinite(values).all():
        raise NumericalInputError("nonfinite singular values")
    tolerance = (float(np.finfo(float).eps*max(a.shape)*values[0]) if absolute_tolerance is None else
                 _scalar(absolute_tolerance, "absolute_tolerance", positive=True))
    rank = int(np.count_nonzero(values > tolerance))
    reasons = []
    if rank < 6:
        reasons.append("rank_below_six")
    if a.shape[1] < 6:
        reasons.append("fewer_than_six_columns")
    if rank < min(a.shape):
        reasons.append("singular_values_at_or_below_tolerance")
    if rank == 0:
        reasons.append("zero_numerical_rank")
    condition = None if rank == 0 else float(values[0]/values[rank-1])
    if condition is not None and not math.isfinite(condition):
        raise NumericalInputError("nonfinite numerical condition")
    fingerprint = hashlib.sha256(str(a.shape).encode()+a.astype("<f8").tobytes()).hexdigest()
    return RankDiagnostics(matrix_name, a.shape, fingerprint, scaling, tolerance,
                           "eps*max(shape)*sigma_max" if absolute_tolerance is None else "explicit_absolute",
                           rank, tuple(map(float, values)), condition, condition if rank == 6 else None, tuple(reasons))


def audit_wrench_primitives(specs, *, friction=None, k=16, scaling=None, absolute_tolerance=None):
    """In-memory numerical input audit. No ranking, LP, outcome or file access."""
    _directions(k)
    if scaling is not None and not isinstance(scaling, WrenchScaling):
        raise NumericalInputError("explicit WrenchScaling or None required")
    if absolute_tolerance is not None:
        _scalar(absolute_tolerance, "absolute_tolerance", positive=True)
    specs = tuple(specs)
    if len({s.candidate_id for s in specs}) != len(specs):
        raise NumericalInputError("duplicate ContactSpec candidate IDs")
    rows, inputs, ranks = [], [], {}
    started = perf_counter()
    for spec in specs:
        begin = perf_counter()
        try:
            g = grasp_matrix(spec)
            g_end = perf_counter()
            primitives = build_primitive_wrenches(spec, friction=friction, k=k, scaling=scaling)
            primitives_end = perf_counter()
            gd = rank_diagnostics(g, matrix_name="grasp_matrix_mug", scaling=scaling,
                                  absolute_tolerance=absolute_tolerance)
            ad = rank_diagnostics(primitives.matrix, matrix_name="primitive_wrenches_mug", scaling=scaling,
                                  absolute_tolerance=absolute_tolerance)
            end = perf_counter()
        except (UnusableContactSpecError, UnknownFrictionError) as exc:
            rows.append(dict(candidate_id=spec.candidate_id, family=spec.family.value, status="skipped",
                             reason=str(exc), elapsed_s=perf_counter()-begin))
            continue
        inputs.append(primitives)
        distribution = ranks.setdefault(spec.family.value, {"grasp": Counter(), "primitive": Counter()})
        distribution["grasp"][gd.numerical_rank] += 1
        distribution["primitive"][ad.numerical_rank] += 1
        rows.append(dict(candidate_id=spec.candidate_id, family=spec.family.value, status="built",
            contact_count=len(spec.contacts), primitive_columns=len(primitives.columns),
            grasp_diagnostics=gd.to_dict(), primitive_diagnostics=ad.to_dict(),
            timings_s=dict(grasp_matrix=g_end-begin, friction_primitives=primitives_end-g_end,
                           rank_diagnostics=end-primitives_end, total=end-begin)))
    return dict(version=GWS_INPUT_VERSION, rows=rows, inputs=tuple(inputs),
                rank_distributions={f: {k: dict(v) for k, v in counts.items()} for f, counts in ranks.items()},
                total_s=perf_counter()-started, skipped=sum(r["status"] == "skipped" for r in rows),
                capacity_constraints_constructed=False, force_closure_tested=False)
