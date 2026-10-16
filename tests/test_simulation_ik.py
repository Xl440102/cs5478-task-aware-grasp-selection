"""Physical regressions for the two pilot IK failures.

Run in the activated grasp-a environment. These tests require PyBullet;
contract-only environments skip them.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest

from grasp_a.contracts import Pose, load_candidates

try:
    import pybullet as p
    from grasp_a.simulation import MugScene
except ImportError:
    p = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(p is None, "PyBullet environment not installed")
class SimulationIKTests(unittest.TestCase):
    def test_ik_restores_joint_velocities_fingers_and_object(self):
        with MugScene(1) as scene:
            for joint in scene.arm + scene.fingers:
                position = p.getJointState(scene.robot, joint,
                                           physicsClientId=scene.client)[0]
                p.resetJointState(scene.robot, joint, position, targetVelocity=0.03,
                                  physicsClientId=scene.client)
            p.resetBaseVelocity(scene.mug, (0.1, 0.2, 0.3), (0.01, 0.02, 0.03),
                                physicsClientId=scene.client)

            def snapshot():
                return (
                    [p.getJointState(scene.robot, j, physicsClientId=scene.client)[:2]
                     for j in scene.movable],
                    p.getBasePositionAndOrientation(scene.mug, physicsClientId=scene.client),
                    p.getBaseVelocity(scene.mug, physicsClientId=scene.client),
                )

            before = snapshot()
            # Target captured from the original failing seed-1 lift.
            target = Pose((0.5441471338272095, 0.005560596939176321, 0.7970082306861878),
                          (-0.014920261688530445, 0.7069500684738159,
                           0.014920102432370186, 0.7069486379623413))
            solution, _, _ = scene.ik(target)
            self.assertIsNotNone(solution)
            self.assertEqual(snapshot(), before)
            self.assertEqual(scene.ik_diagnostics[-1]["movable_dofs"], 9)

    def test_unreachable_candidate_is_rejected_with_json_safe_errors(self):
        candidate = load_candidates(ROOT / "candidates.example.json")[1]
        bad = replace(candidate, grasp_pose=Pose((3, 3, 3), (0, 0, 0, 1)),
                      pregrasp_pose=Pose((3, 3, 3.1), (0, 0, 0, 1)))
        with MugScene(0) as scene:
            check = scene.check_candidate(bad)
            self.assertFalse(check.feasible)
            self.assertIn("grasp_ik_or_fk_error", check.reasons)
            json.dumps(asdict(check), allow_nan=False)
            json.dumps(scene.ik_diagnostics, allow_nan=False)

    def test_original_lift_and_pour_failures_complete_with_valid_joints(self):
        candidate = load_candidates(ROOT / "candidates.example.json")[1]
        for seed, task in ((1, "handover_prep"), (0, "pour_prep")):
            with self.subTest(seed=seed, task=task), MugScene(seed) as scene:
                result = scene.run_episode(task, candidate)
                self.assertTrue(result["success"], result["failure_reason"])
                self.assertFalse(result["collision"])
                self.assertFalse(result["dropped"])
                self.assertEqual(result["controller_version"], "observable_primitives_v2")
                for diagnostic in result["ik_diagnostics"]:
                    if diagnostic["success"]:
                        for joint, angle in zip(scene.arm, diagnostic["joint_positions"]):
                            info = p.getJointInfo(scene.robot, joint,
                                                  physicsClientId=scene.client)
                            self.assertGreaterEqual(angle, info[8])
                            self.assertLessEqual(angle, info[9])
                json.dumps(result, allow_nan=False)

    def test_historical_ik_targets_still_exercise_bounded_solver(self):
        # The v2 controller uses different observations/paths, so do not require
        # a particular fallback stage in its newly generated episode.
        for filename, stage in (("handover_G02_seed1.json", "lift"),
                                ("pour_G02_seed0.json", "task_motion")):
            old = json.loads((ROOT / "results/ik_fix_20261015" / filename).read_text(encoding="utf-8"))
            diagnostic = next(d for d in old["ik_diagnostics"]
                              if d["stage"] == stage and d.get("solver") == "bounded")
            with self.subTest(stage=stage), MugScene(old["seed"]) as scene:
                q, _, _ = scene.ik(Pose.from_dict(diagnostic["target_pose"]), stage=stage)
                self.assertIsNotNone(q)
                self.assertEqual(scene.ik_diagnostics[-1]["solver"], "bounded")


if __name__ == "__main__":
    unittest.main()
