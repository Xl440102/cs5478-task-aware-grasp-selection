"""Immutable evidence only: collision witnesses are not load-bearing contacts."""
from dataclasses import dataclass
import json

from grasp_b.wrench_v2.contacts import _freeze_json, _plain
from .contact_regions import digest

VERSION = "collision_contact_region_evidence_v1"
CLASSIFICATIONS = frozenset({
    "collision_geometry_supported", "sequential_contact_evidence",
    "geometry_conflict", "ambiguous_multiple_regions", "unsupported", "unknown",
})


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True)
class CollisionRegionEvidence:
    """Frozen SI/mug-frame sidecar with strict checksum round-trip.

    Hashes bind provenance, not an assertion of complete engine observability.
    No area, pressure, force budget, torsion or GWS columns are produced here.
    """
    payload: object

    def __post_init__(self):
        d = json.loads(json.dumps(_plain(self.payload), allow_nan=False))
        if d.get("version") != VERSION or d.get("frame") != "mug" or d.get("length_unit") != "m":
            raise ValueError("explicit version, mug frame and SI lengths required")
        if d.get("classification") not in CLASSIFICATIONS or not d.get("candidate_id"):
            raise ValueError("candidate identity and evidence classification required")
        for key in ("candidate_hash", "contact_spec_hash", "raw_evidence_hash", "representation_hash", "input_hash"):
            if not _hash(d.get(key)):
                raise ValueError("invalid identity: " + key)
        representation = d.get("representation", {})
        if digest(representation) != d["representation_hash"]:
            raise ValueError("representation identity mismatch")
        if representation.get("observation_status") != "representation_partially_observed":
            raise ValueError("complete internal shape observation is not supported")
        if d.get("actual_solver_manifold") != "not_observed":
            raise ValueError("static queries do not establish solver manifold")
        if {f.get("finger_id") for f in d.get("fingers", [])} != {"left", "right"} or len(d["fingers"]) != 2:
            raise ValueError("two explicitly identified fingers required")
        previous = d.pop("record_hash", None)
        checksum = digest(d)
        if previous is not None and previous != checksum:
            raise ValueError("contact evidence checksum mismatch")
        d["record_hash"] = checksum
        object.__setattr__(self, "payload", _freeze_json(d))

    def to_dict(self):
        return _plain(self.payload)

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, text):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        def reject(value):
            raise ValueError("non-finite JSON: " + value)
        d = json.loads(text, object_pairs_hook=unique, parse_constant=reject)
        if "record_hash" not in d:
            raise ValueError("stored evidence requires checksum")
        return cls(d)

    def assert_identity(self, **expected):
        allowed = {"candidate_id", "candidate_hash", "contact_spec_hash", "raw_evidence_hash", "representation_hash", "input_hash"}
        if not expected or set(expected) - allowed:
            raise ValueError("explicit supported identity required")
        if any(self.payload[key] != value for key, value in expected.items()):
            raise ValueError("contact region identity mismatch")
