"""A small observable policy example, NOT M3 or a general manipulation agent.

python -m examples.incremental_policy --task pour_prep --id G02
The caller controls waypoints, opening, timing and retreat, not pickup/place.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path

import pybullet as p

from grasp_a.contracts import TASKS, Pose, load_candidates
from grasp_a.control import Primitive
from grasp_a.simulation import run_primitive_episode


def make_policy(retreat_m=.10, clearance_m=.04):
    actions = []

    def policy(context, observation):
        last = observation["last_action"]
        if last and last["status"] == "ik_failed":
            # Stop instead of claiming a failed waypoint was reached. A real
            # policy can try a different waypoint within the shared budget.
            return None
        if not actions:
            pre = Pose.from_dict(context["pregrasp_tool_pose"])
            grasp = Pose.from_dict(context["grasp_tool_pose"])
            target = Pose.from_dict(context["target_object_pose"])
            local = Pose.from_dict(context["candidate"]["grasp_pose"])
            pos, quat = p.multiplyTransforms(target.position, target.quaternion,
                                             local.position, local.quaternion)
            task = Pose(tuple(pos), tuple(quat))
            lift = replace(grasp, position=(grasp.position[0], grasp.position[1], grasp.position[2]+.14))
            actions.extend([Primitive("move_tcp", pre, duration_s=.75, path="joint"),
                            Primitive("move_tcp", grasp, duration_s=.5, path="joint"),
                            Primitive("gripper", width=0, duration_s=.75),
                            Primitive("move_tcp", lift, duration_s=200/240)])
            if context["task_id"] == "pour_prep":
                retreat = replace(lift, position=(lift.position[0]-retreat_m, lift.position[1],
                                                  lift.position[2]+clearance_m))
                rotate = replace(task, position=(task.position[0]-retreat_m, task.position[1],
                                                 task.position[2]+clearance_m))
                actions.extend([Primitive("move_tcp", retreat, duration_s=200/240),
                                Primitive("move_tcp", rotate, duration_s=200/240)])
            actions.append(Primitive("move_tcp", task, duration_s=200/240))
        index = observation["actions_executed"]
        return actions[index] if index < len(actions) else None

    return policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=TASKS, default="pour_prep")
    parser.add_argument("--id", default="G02")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--candidates", default="candidates.example.json")
    parser.add_argument("--retreat-m", type=float, default=.10)
    parser.add_argument("--clearance-m", type=float, default=.04)
    parser.add_argument("--out", type=Path, default=Path("results/local/incremental_demo.json"))
    args = parser.parse_args()
    if not 0 <= args.retreat_m <= .15 or not 0 <= args.clearance_m <= .08:
        parser.error("retreat must be in [0, .15] m; clearance in [0, .08] m")
    result = run_primitive_episode(args.task, args.seed, args.id, load_candidates(args.candidates),
                                   make_policy(args.retreat_m, args.clearance_m))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print({k: result[k] for k in ("policy_mode", "success", "failure_reason")})


if __name__ == "__main__":
    main()
