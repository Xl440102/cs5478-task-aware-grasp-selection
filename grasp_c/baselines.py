"""Public-input adapters for B's random and experimental geometry references."""
from __future__ import annotations

from copy import deepcopy
import math
import re

from grasp_b.prototype import select_geometry, select_random
from grasp_b.scoring import GEOMETRY_SCORE_VERSION, normalized_weights
from grasp_b.selectors import validate_public, validate_ranking

EXPERIMENTAL_SCOPE = "experimental_smoke_only_not_formal_M1"
SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _score_provenance(provenance: dict, *, geometry: bool) -> dict:
    """Copy only scoring origin fields; internal diagnostic sidecars stay out."""
    if not isinstance(provenance, dict):
        raise ValueError("B provenance must be an object")
    for field in ("score_version", "scope"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise ValueError(f"B provenance requires a nonempty {field}")
    if geometry and (provenance["score_version"] != GEOMETRY_SCORE_VERSION
                     or provenance["scope"] != EXPERIMENTAL_SCOPE):
        raise ValueError("b_geometry requires B's supported experimental score version and scope; "
                         "placeholder scores and unapproved formal M1 versions are disabled")
    status = provenance.get("score_status")
    if geometry and status is not None and status != "scored_experimental":
        raise ValueError("b_geometry requires scored_experimental provenance, not placeholder scores")
    weights = deepcopy(provenance.get("weights"))
    normalized = normalized_weights(weights)
    supplied_normalized = provenance.get("normalized_weights")
    if supplied_normalized is not None:
        if (not isinstance(supplied_normalized, dict)
                or set(supplied_normalized) != set(normalized)
                or any(type(supplied_normalized[k]) not in (int, float)
                       or not math.isfinite(supplied_normalized[k])
                       or not math.isclose(supplied_normalized[k], normalized[k], rel_tol=1e-12,
                                           abs_tol=1e-12) for k in normalized)):
            raise ValueError("B provenance normalized_weights do not match weights")
    result = {"score_version": provenance["score_version"], "scope": provenance["scope"],
              "weights": weights, "normalized_weights": normalized}
    for field in ("candidate_sha256", "candidate_file_sha256", "report_sha256"):
        value = provenance.get(field)
        if value is None and field != "candidate_sha256":
            continue
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ValueError(f"B provenance requires a valid {field}")
        result[field] = value.lower()
    for field in ("generation_version", "score_status"):
        if field in provenance:
            if not isinstance(provenance[field], str) or not provenance[field]:
                raise ValueError(f"B provenance {field} must be a nonempty string")
            result[field] = provenance[field]
    return result


def build_b_selector(kind: str, provenance: dict):
    """Create selector(task_id, public_candidates, seed) using B's implementation.

    The runner must first verify the provenance against the full candidate file
    and report. This adapter has no access to poses, features, reports or labels.
    Its geometry variant is an experimental reference, not an approved B M1.
    Random selection uses the scene seed, so repeated trials reuse its ranking.
    """
    if kind not in ("b_geometry", "b_random"):
        raise ValueError("B selector kind must be b_geometry or b_random; M2 is unavailable")
    scoring = _score_provenance(provenance, geometry=kind == "b_geometry")

    def selector(task_id: str, public_candidates: list[dict], seed: int) -> dict:
        from .tasks import validate_task_id
        validate_task_id(task_id)
        if type(seed) is not int:
            raise ValueError("selection seed must be an integer")
        if not isinstance(public_candidates, list) or not public_candidates:
            raise ValueError("no feasible candidates; caller must skip selection")
        public = deepcopy(public_candidates)
        validate_public(public)  # Reject diagnostics instead of forwarding extra fields.
        if kind == "b_geometry":
            if any(not 0 <= item["geometry_score"] <= 1 for item in public):
                raise ValueError("B experimental geometry scores must be in [0, 1]")
            selected = select_geometry(public)
        else:
            selected = select_random(public, task_id=task_id, seed=seed)
        ranking = validate_ranking(selected["ranking"], public)
        if selected["selected_id"] != ranking[0]:
            raise ValueError("B selection must equal the first ranked feasible ID")
        metadata = dict(deepcopy(scoring), baseline_kind=kind,
                        method_id="B_EXPERIMENTAL_GEOMETRY" if kind == "b_geometry" else "B_RANDOM",
                        score_used=kind == "b_geometry", selection_seed=seed)
        if kind == "b_random":
            metadata["selection_seed_rule"] = "scene_seed_repeat_invariant"
            metadata["ordered_input_rule"] = "preserve_feasible_candidate_order"
        else:
            metadata["tie_break"] = "candidate_id_ascending"
        return {"selected_id": selected["selected_id"], "ranking": ranking, "metadata": metadata}

    return selector
