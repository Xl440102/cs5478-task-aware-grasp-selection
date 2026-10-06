"""Deterministically search for a pilot grasp; no benchmark labels are set here.

This is a scene-calibration aid. The candidate JSON and final episode still
need to be inspected before freezing a benchmark candidate set.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

import pybullet as p

from grasp_a.contracts import Candidate, Pose
from grasp_a.simulation import MugScene


def candidate_grid():
    for yaw_deg in (0, 45, 90, 135, 180, 225, 270, 315):
        yaw = math.radians(yaw_deg)
        direction = (math.cos(yaw), math.sin(yaw))
        for roll_deg in (0, 90, 180, 270):
            base = p.getQuaternionFromEuler((0, math.pi / 2, yaw))
            roll = p.getQuaternionFromEuler((0, 0, math.radians(roll_deg)))
            quat = tuple(p.multiplyTransforms((0, 0, 0), base,
                                              (0, 0, 0), roll)[1])
            for height in (0.0, 0.01, 0.02, 0.03):
                for distance in (0.08, 0.12):
                    grasp = Pose((0.0, 0.0, height), quat)
                    pre = Pose((-distance * direction[0],
                                -distance * direction[1], height), quat)
                    yield Candidate(
                        id=f"pilot_y{yaw_deg}_r{roll_deg}_z{height:.2f}_d{distance:.2f}",
                        region="body", approach=f"yaw_{yaw_deg}_roll_{roll_deg}",
                        gripper_width=0.078, geometry_score=0.0,
                        grasp_pose=grasp, pregrasp_pose=pre,
                    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("results/pilot_search.json"))
    args = parser.parse_args()
    tested = 0
    reasons = Counter()
    feasible = []
    with MugScene(args.seed) as scene:
        for candidate in candidate_grid():
            result = scene.check_candidate(candidate)
            tested += 1
            if result.feasible:
                feasible.append(candidate)
                print("feasible:", candidate.id, flush=True)
            else:
                reasons.update(result.reasons)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "seed": args.seed,
        "tested": tested,
        "feasible": [{
            "id": c.id, "region": c.region, "approach": c.approach,
            "gripper_width": c.gripper_width,
            "geometry_score": c.geometry_score,
            "grasp_pose": {"position": c.grasp_pose.position,
                           "quaternion": c.grasp_pose.quaternion},
            "pregrasp_pose": {"position": c.pregrasp_pose.position,
                              "quaternion": c.pregrasp_pose.quaternion},
        } for c in feasible],
        "reasons": dict(reasons),
    }, indent=2), encoding="utf-8")
    print(f"checked {tested}; feasible {len(feasible)}; wrote {args.out}")


if __name__ == "__main__":
    main()
