"""Declared PCWF normal-force budgets B lambda <= b (N), never motor limits.

Every used finger group needs an aggregate budget. Optional local limits add
constraints, never extra capacity. None local limits remain explicitly unknown
and impose no additional row unless require_local_limits=True. This policy is
part of the model assumptions, not a claim of unlimited physical local force.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json

import numpy as np

from .contacts import _plain
from .gws import NumericalInputError, PrimitiveWrenchData, _readonly, _scalar, _text, _wrench_matrix

CONSTRAINT_VERSION = "shared_normal_capacity_v1"


class ConstraintStatus(str, Enum):
    READY = "ready"
    MISSING_PHYSICS_PARAMETERS = "missing_physics_parameters"


@dataclass(frozen=True, slots=True, kw_only=True)
class CapacityValue:
    normal_force_n: float
    source: str
    calibrated: bool = False
    unit: str = "N"

    def __post_init__(self):
        object.__setattr__(self, "normal_force_n", _scalar(self.normal_force_n, "normal_force_n"))
        _text(self.source, "capacity source")
        if self.source == "unknown" or self.unit != "N" or type(self.calibrated) is not bool:
            raise NumericalInputError("capacity needs explicit source, unit N, and boolean calibrated")


@dataclass(frozen=True, slots=True)
class CapacityRow:
    kind: str
    key: str | int
    column_indices: tuple[int, ...]
    normal_force_n: float | None
    source: str
    calibrated: bool
    origin: str
    unit: str = "N"


@dataclass(frozen=True, slots=True, eq=False)
class CapacityConstraints:
    candidate_id: str
    candidate_hash: str
    primitive_fingerprint: str
    status: ConstraintStatus
    matrix: np.ndarray | None
    bounds_n: np.ndarray | None
    rows: tuple[CapacityRow, ...]
    missing_parameters: tuple[str, ...]
    unknown_local_contacts: tuple[int, ...]
    require_local_limits: bool

    def to_dict(self):
        return dict(version=CONSTRAINT_VERSION, candidate_id=self.candidate_id, candidate_hash=self.candidate_hash,
                    primitive_fingerprint=self.primitive_fingerprint, status=self.status.value,
                    matrix=None if self.matrix is None else self.matrix.tolist(),
                    bounds_n=None if self.bounds_n is None else self.bounds_n.tolist(), rows=_plain(self.rows),
                    missing_parameters=list(self.missing_parameters),
                    unknown_local_contacts=list(self.unknown_local_contacts), require_local_limits=self.require_local_limits,
                    model="all declared normal bounds scale with utilization; no motor/contact force conversion")


def primitive_fingerprint(data):
    if not isinstance(data, PrimitiveWrenchData):
        raise NumericalInputError("PrimitiveWrenchData required")
    _wrench_matrix(data.matrix)
    return hashlib.sha256(json.dumps(data.to_dict(), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _metadata(data):
    a = _wrench_matrix(data.matrix)
    if len(data.columns) != a.shape[1]:
        raise NumericalInputError("column metadata/matrix length mismatch")
    groups = {g.group_id: g for g in data.capacity_groups}
    if len(groups) != len(data.capacity_groups) or len({g.finger_id for g in groups.values()}) != len(groups):
        raise NumericalInputError("duplicate group or multiple budgets for same finger")
    n = len(data.contact_parameters)
    if n == 0 or len(data.effective_directions) != n:
        raise NumericalInputError("contact parameter metadata mismatch")
    contacts = {i: [] for i in range(n)}
    seen = set()
    for j, col in enumerate(data.columns):
        if type(col.contact_index) is not int or col.contact_index not in contacts:
            raise NumericalInputError("invalid contact index")
        group = groups.get(col.capacity_group_id)
        if group is None or group.finger_id != col.finger_id:
            raise NumericalInputError("column group/finger mismatch")
        if type(col.friction_direction_index) is not int or not 0 <= col.friction_direction_index < data.effective_directions[col.contact_index]:
            raise NumericalInputError("invalid direction index")
        identity = (col.contact_index, col.friction_direction_index)
        if identity in seen:
            raise NumericalInputError("duplicate column identity")
        seen.add(identity)
        contacts[col.contact_index].append(j)
    for i, indices in contacts.items():
        if len(indices) != data.effective_directions[i] or len({
                (data.columns[j].finger_id, data.columns[j].capacity_group_id, data.columns[j].surface_id)
                for j in indices}) != 1:
            raise NumericalInputError("inconsistent per-contact column metadata")
    used = {c.capacity_group_id for c in data.columns}
    if used != set(groups):
        raise NumericalInputError("unused or undeclared capacity groups")
    return groups, contacts


def build_capacity_constraints(data, *, finger_budgets=None, contact_limits=None, require_local_limits=False):
    """Explicit overrides are recorded, not written back to data/ContactSpec.

    A group row contains 1 for EVERY primitive of EVERY sample belonging to
    that group. Thus adding cone directions or samples cannot add a budget.
    Missing required parameters return a diagnostic object without numeric B,b.
    """
    groups, contacts = _metadata(data)
    if type(require_local_limits) is not bool:
        raise NumericalInputError("require_local_limits must be boolean")
    for name, values, allowed in (("finger_budgets", finger_budgets, groups),
                                   ("contact_limits", contact_limits, contacts)):
        if values is not None and (not isinstance(values, Mapping) or
                any(k not in allowed or (name == "contact_limits" and type(k) is not int) for k in values) or
                any(not isinstance(v, CapacityValue) for v in values.values())):
            raise NumericalInputError(f"invalid explicit {name} keys/values")
    finger_budgets = {} if finger_budgets is None else finger_budgets
    contact_limits = {} if contact_limits is None else contact_limits
    rows, missing, unknown = [], [], []
    for key, group in groups.items():
        external = finger_budgets.get(key)
        value = external.normal_force_n if external is not None else group.normal_force_budget_n
        source = external.source if external is not None else group.source
        calibrated = external.calibrated if external is not None else group.calibrated
        if value is None:
            missing.append(f"finger_group:{key}:normal_force_budget_n")
        else:
            CapacityValue(normal_force_n=value, source=source, calibrated=calibrated)
        indices = tuple(j for j, col in enumerate(data.columns) if col.capacity_group_id == key)
        rows.append(CapacityRow("finger", key, indices, value, source, calibrated,
                                "external_explicit" if external is not None else "contact_spec"))
    for i, parameters in enumerate(data.contact_parameters):
        external = contact_limits.get(i)
        value = external.normal_force_n if external is not None else parameters.normal_force_limit_n
        if value is None:
            unknown.append(i)
            if require_local_limits:
                missing.append(f"contact:{i}:normal_force_limit_n")
            continue
        source = external.source if external is not None else parameters.source
        calibrated = external.calibrated if external is not None else parameters.calibrated
        CapacityValue(normal_force_n=value, source=source, calibrated=calibrated)
        rows.append(CapacityRow("contact", i, tuple(contacts[i]), value, source, calibrated,
                                "external_explicit" if external is not None else "contact_spec"))
    if missing:
        b_matrix = bounds = None
        status = ConstraintStatus.MISSING_PHYSICS_PARAMETERS
    else:
        b_matrix = np.zeros((len(rows), len(data.columns)))
        for i, row in enumerate(rows):
            b_matrix[i, list(row.column_indices)] = 1.
        b_matrix = _readonly(b_matrix)
        bounds = _readonly([row.normal_force_n for row in rows])
        status = ConstraintStatus.READY
    return CapacityConstraints(data.candidate_id, data.candidate_hash, primitive_fingerprint(data),
                               status, b_matrix, bounds, tuple(rows), tuple(missing), tuple(unknown), require_local_limits)


def validate_constraints(data, constraints):
    """Reject stale identity, reordered columns, or tampered B/b before solving."""
    if not isinstance(constraints, CapacityConstraints) or (
            constraints.candidate_id != data.candidate_id or constraints.candidate_hash != data.candidate_hash or
            constraints.primitive_fingerprint != primitive_fingerprint(data)):
        raise NumericalInputError("capacity constraints do not match primitive identity")
    _metadata(data)
    if constraints.status is ConstraintStatus.MISSING_PHYSICS_PARAMETERS:
        if not constraints.missing_parameters or constraints.matrix is not None or constraints.bounds_n is not None:
            raise NumericalInputError("inconsistent missing-parameter constraints")
        return
    if constraints.status is not ConstraintStatus.READY or constraints.missing_parameters:
        raise NumericalInputError("invalid capacity constraint state")
    matrix = np.asarray(constraints.matrix)
    bounds = np.asarray(constraints.bounds_n)
    if matrix.shape != (len(constraints.rows), len(data.columns)) or bounds.shape != (len(constraints.rows),):
        raise NumericalInputError("invalid B/b shape")
    expected = np.zeros(matrix.shape)
    for i, row in enumerate(constraints.rows):
        CapacityValue(normal_force_n=row.normal_force_n, source=row.source, calibrated=row.calibrated, unit=row.unit)
        indices = tuple(j for j, col in enumerate(data.columns) if
                        (col.capacity_group_id == row.key if row.kind == "finger" else col.contact_index == row.key))
        if row.kind not in ("finger", "contact") or row.column_indices != indices or not indices:
            raise NumericalInputError("capacity row does not match column metadata")
        expected[i, list(indices)] = 1.
    if not np.array_equal(matrix, expected) or not np.array_equal(bounds, [r.normal_force_n for r in constraints.rows]):
        raise NumericalInputError("capacity B/b was modified")
    finger_keys = [r.key for r in constraints.rows if r.kind == "finger"]
    if len(finger_keys) != len(set(finger_keys)) or set(finger_keys) != {g.group_id for g in data.capacity_groups}:
        raise NumericalInputError("one shared row for every finger group is required")
    local_keys = [r.key for r in constraints.rows if r.kind == "contact"]
    required = {i for i, p in enumerate(data.contact_parameters) if p.normal_force_limit_n is not None
                or constraints.require_local_limits}
    if len(local_keys) != len(set(local_keys)) or not required.issubset(local_keys):
        raise NumericalInputError("duplicate or missing declared local constraint")
