"""Data contracts shared with the candidates, selectors and evaluator."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import hashlib
import math
from pathlib import Path

TASKS = ("pour_prep", "handover_prep")


def validate_task_id(task_id: str) -> str:
    """Validate and return the task identifier used by every workstream."""
    if task_id not in TASKS:
        raise ValueError(f"task must be one of {TASKS}")
    return task_id


@dataclass(frozen=True)
class Pose:
    position: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]  # PyBullet: x, y, z, w

    @classmethod
    def from_dict(cls, data: dict) -> "Pose":
        pos = tuple(float(x) for x in data["position"])
        quat = tuple(float(x) for x in data["quaternion"])
        if len(pos) != 3 or len(quat) != 4 or not all(math.isfinite(x) for x in pos + quat):
            raise ValueError("pose needs finite position[3] and quaternion[4]")
        norm = math.sqrt(sum(x*x for x in quat))
        if abs(norm - 1.0) > 0.01:
            raise ValueError("quaternion must be approximately unit length")
        return cls(pos, tuple(x / norm for x in quat))


@dataclass(frozen=True)
class Candidate:
    id: str
    region: str
    approach: str
    gripper_width: float  # metres, total opening, not one-finger travel
    geometry_score: float
    grasp_pose: Pose  # both poses are in the mug frame
    pregrasp_pose: Pose

    @classmethod
    def from_dict(cls, data: dict) -> "Candidate":
        item = cls(
            id=str(data["id"]), region=str(data["region"]),
            approach=str(data["approach"]), gripper_width=float(data["gripper_width"]),
            geometry_score=float(data["geometry_score"]),
            grasp_pose=Pose.from_dict(data["grasp_pose"]),
            pregrasp_pose=Pose.from_dict(data["pregrasp_pose"]),
        )
        if not item.id or not math.isfinite(item.gripper_width) or not math.isfinite(item.geometry_score):
            raise ValueError("candidate needs a nonempty ID and finite numeric fields")
        return item

    def public_metadata(self) -> dict:
        """Fields sent to selectors. No ground-truth task label or result."""
        return {k: getattr(self, k) for k in
                ("id", "region", "approach", "gripper_width", "geometry_score")}


def load_candidates(path: str | Path) -> list[Candidate]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("candidate file must be a JSON list")
    candidates = [Candidate.from_dict(x) for x in raw]
    ids = [x.id for x in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate candidate IDs")
    return candidates


def candidate_sha256(candidates: list[Candidate]) -> str:
    """Fingerprint the ordered full input, not only selector-visible labels."""
    payload = json.dumps([asdict(c) for c in candidates], sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def as_jsonable(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"Cannot encode {type(obj)}")
