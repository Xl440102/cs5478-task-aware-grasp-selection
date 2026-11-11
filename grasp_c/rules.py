"""M6 prompt-rules baseline: the v2 prompt's grasp priorities applied without an LLM.

``prompt_version="v2"`` (grasp_c/tasks.py ``SEQUENCE_INTENT_V2``) names, per task,
which grasp descriptions to prefer. This selector applies exactly those
priority lists to the public fields, so the v2 LLM results can be compared with
the rules alone. The rules were written after inspecting outcomes on seeds
10-29, so on those seeds this is a retrospective reference, not a blind test.

Only public fields are read (``region``, ``approach``, ``geometry_score``,
``id``). Within a tier the highest ``geometry_score`` wins, then the ID. An
``approach`` string without ``key=value`` parts (non-semantic labels) matches
no tier and falls back to that ordering.
"""
from __future__ import annotations

METHOD_ID = "M6_PROMPT_RULES"


def _fields(approach: str) -> dict:
    parts = (p.split("=", 1) for p in approach.split("; ") if "=" in p)
    return {k.strip(): v.split(" (")[0].strip() for k, v in parts}


def _tier(task_id: str, c: dict) -> int:
    d, score, region = _fields(c["approach"]), c["geometry_score"], c["region"]
    above = d.get("from") == "above"
    across = d.get("closing", "").startswith("across")
    handle_free = d.get("handle") == "left free"
    top_high = d.get("part") == "body top" and score >= 0.70
    if task_id == "transport_place":
        tiers = [region == "handle" and "outer bar" in d.get("part", "") and across,
                 region == "rim" and above and across and handle_free,
                 top_high]
    elif task_id == "handover_transfer":
        tiers = [region == "rim" and above and across and handle_free,
                 d.get("part") == "body top" and above and across and handle_free]
    elif task_id == "pour_water":
        tiers = [top_high,
                 d.get("part") == "body high" and d.get("from") == "side, horizontal"
                 and d.get("side") == "beside the handle",
                 region == "rim" and across]
    else:
        raise ValueError(f"no prompt rules for task {task_id!r}")
    return next((i for i, hit in enumerate(tiers) if hit), len(tiers))


def rank(task_id: str, public: list[dict]) -> list[str]:
    """Full ranking of candidate IDs, best first."""
    ordered = sorted(public, key=lambda c: (_tier(task_id, c), -c["geometry_score"], c["id"]))
    return [c["id"] for c in ordered]
