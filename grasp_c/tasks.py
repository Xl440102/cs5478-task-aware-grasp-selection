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
