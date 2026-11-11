"""Public task intentions and separate preparation/sequence protocol identities.

This module imports contracts only, never a simulator or terminal evaluator.
Task specs are hashed for cache identity; their private scoring details are not prompts.
"""
import hashlib
import json

from grasp_a.contracts import TASKS as PREP_TASKS
from grasp_a.sequence_contracts import (SEQUENCE_TASKS, SEQUENCE_CONTROLLER,
                                        SEQUENCE_EVALUATOR, SequenceConfig, task_spec)

SEQUENCE_SCENE = "hollow_cup_sequences_v1"
SEQUENCE_FILTER = "sequence_filter_v1"
SEQUENCE_EPISODE = "sequence_episode_v1"


def tasks_for_track(track):
    if track == "prep":
        return PREP_TASKS
    if track == "sequence":
        return SEQUENCE_TASKS
    raise ValueError("track must be prep or sequence")


def validate_task_id(task_id):
    if not isinstance(task_id, str) or task_id not in (*PREP_TASKS, *SEQUENCE_TASKS):
        raise ValueError(f"unsupported selector task: {task_id}")
    return task_id


def load_sequence_config(raw=None):
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("sequence_config must be an object")
    try:
        config = SequenceConfig(**(raw or {}))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid sequence_config: {exc}") from None
    return config


def sequence_specs(tasks, config):
    """Use A's exact hashing convention without importing grasp_a.sequence."""
    result = {}
    for task in tasks:
        spec = task_spec(task, config)
        encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False)
        result[task] = {"spec": json.loads(encoded),
                        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
    return result


SEQUENCE_INTENT = {
    "transport_place": "Pick up the mug, transport it to the target table region, release it and retreat. Choose a grasp that supports stable transport and allows the mug to stand independently after release.",
    "handover_transfer": "Pick up the mug and present its handle to a cooperative receiving gripper. Release and retreat so the receiver can independently lift and support the mug. Choose a grasp that leaves the handle accessible.",
    "pour_water": "Pick up the water-filled mug, carry it upright to the receiving cup, pour through its opening and return upright. Choose a grasp that supports controlled pouring and leaves the opening unobstructed.",
}

SEQUENCE_INTENT_V2 = {
    "transport_place": (
        "Pick up the upright mug, transport it horizontally to the target table region, "
        "lower it onto the table, open the gripper, and retreat upward/backward so the mug "
        "stands independently. Prefer grasps that avoid table or handle interference during "
        "lowering and retreat: (1) handle grasps on the outer bar closing across the handle "
        "axis (region='handle', part=handle outer bar, closing=across handle axis), or "
        "(2) rim grasps from above closing across the handle axis (region='rim', from=above, "
        "closing=across handle axis, handle=left free), or (3) high-scoring top-down body "
        "grasps (part=body top, geometry_score >= 0.70). Avoid low/mid-body or pitched side "
        "grasps that risk finger/wrist collision or slip."
    ),
    "handover_transfer": (
        "Pick up the mug, carry it to the handover pose, and hold it while a cooperative "
        "receiving gripper sweeps horizontally into the +Y handle region to grasp the handle; "
        "then release, retreat along the donor approach axis, and let the receiver lift the mug. "
        "Crucially, horizontal or pitched side grasps on the body (part=body mid or body high, "
        "from=side) occupy the horizontal corridor around the mug and risk colliding with the "
        "receiving gripper or failing arrival. Strongly prefer top-down rim grasps that leave "
        "the handle free (region='rim', part=rim, from=above, closing=across handle axis, "
        "handle=left free), which keep both the handle and the horizontal receiver corridor "
        "completely clear and retreat straight up (+Z). If no rim grasp is available, prefer "
        "top-down body grasps with handle=left free (part=body top, from=above, closing=across "
        "handle axis, handle=left free)."
    ),
    "pour_water": (
        "Pick up the water-filled mug, carry it near the receiving cup, tilt it 110 degrees "
        "around the horizontal axis to pour, hold the tilt, and return upright. Success requires "
        "high kinematic reachability and grip stability across the large 110-degree wrist rotation. "
        "Note that in this setup, top-down and rim grasps do not physically obstruct water flow "
        "despite the generic 'mouth=hand over the opening' tag, whereas steep 60-degree-down side "
        "grasps opposite the handle and mid-body side grasps often fail pregrasp or tilt IK/arrival. "
        "Prefer: (1) high-scoring top-down body grasps (part=body top, geometry_score >= 0.70), "
        "(2) horizontal upper-body side grasps beside the handle (part=body high, from=side, "
        "horizontal, side=beside the handle), or (3) rim grasps closing across the handle axis "
        "(region='rim', closing=across handle axis)."
    ),
}

SEQUENCE_INTENT_V2_PHYSICS = {
    "transport_place": (
        "Pick up the upright mug from the table, translate it horizontally to the target coaster "
        "on the table, lower it until the mug base rests on the table, open the gripper, and retreat. "
        "Choose a grasp that holds the mug securely during horizontal transport and allows the fingers "
        "and wrist to open and withdraw near the table surface without colliding with the table or tipping the mug."
    ),
    "handover_transfer": (
        "Pick up the upright mug, move it to the handover pose in mid-air, and hold it steady while a "
        "cooperative parallel-jaw receiving gripper approaches horizontally from the side toward the mug "
        "handle (+Y direction) to grasp the handle. Once the receiver closes on the handle, open the donor "
        "gripper and retreat along the donor approach direction so the receiver carries the mug alone. "
        "Choose a donor grasp that leaves the handle unobstructed for the receiver and does not occupy the "
        "horizontal side space around the mug where the receiving gripper fingers and wrist enter."
    ),
    "pour_water": (
        "Pick up the water-filled mug, carry it adjacent to the receiving cup on the table, rotate the wrist "
        "by 110 degrees to tilt the mug and pour water into the receiving cup, hold the tilt, and rotate back upright. "
        "Choose a grasp that remains kinematically reachable for a 7-DoF arm across the full 110-degree wrist "
        "rotation near the receiving cup without hitting wrist limits, slipping under tilted load, or colliding "
        "with the receiving cup."
    ),
}

