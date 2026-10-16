"""Control/ground-truth boundary, primitive protocol and hold regressions."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from grasp_a.contracts import Pose, load_candidates
from grasp_a.control import ObservationConfig, Primitive

try:
    import pybullet as p
    from grasp_a.simulation import MugScene, get_feasible_candidates, run_primitive_episode
    from grasp_a.evaluation import EpisodeEvaluator
except ImportError:
    p = None

ROOT = Path(__file__).resolve().parents[1]


class PrimitiveContractTests(unittest.TestCase):
    def test_bad_actions_are_rejected(self):
        for args in (dict(kind="pickup"), dict(kind="wait", duration_s=3),
                     dict(kind="gripper", width=.081), dict(kind="gripper", width=float("nan")),
                     dict(kind="move_tcp"), dict(kind="wait", width=0),
                     dict(kind="move_tcp", target=Pose((2, 0, .8), (0, 0, 0, 1)))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Primitive(**args)

    def test_bad_observation_config_is_rejected(self):
        for args in (dict(position_noise_m=-1), dict(yaw_noise_deg=float("inf")),
                     dict(source="oracle"), dict(noise_seed=True)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ObservationConfig(**args)


@unittest.skipIf(p is None, "PyBullet environment not installed")
class ObservableControlTests(unittest.TestCase):
    def setUp(self):
        self.candidates = load_candidates(ROOT / "candidates.example.json")

    def test_estimate_and_target_do_not_follow_simulator_seed(self):
        with MugScene(0) as first, MugScene(9) as second:
            self.assertNotEqual(first._object_pose(), second._object_pose())
            self.assertEqual(first.observed_mug_pose, second.observed_mug_pose)
            self.assertEqual(first.task_target("pour_prep"), second.task_target("pour_prep"))
        noisy = ObservationConfig(position_noise_m=.003, yaw_noise_deg=2, noise_seed=17)
        with MugScene(0, observation_config=noisy) as a, MugScene(9, observation_config=noisy) as b:
            self.assertEqual(a.observed_mug_pose, b.observed_mug_pose)
            self.assertEqual(a.task_target("pour_prep"), first.task_target("pour_prep"))

    def test_controller_can_execute_with_all_object_truth_access_disabled(self):
        # Replace the evaluator only: any illicit control-time truth read now
        # raises. Probes/planning may still query the known collision model.
        evaluator = Mock()
        evaluator.finish.return_value = {"collision_types": []}
        with MugScene(0) as scene, patch("grasp_a.simulation.EpisodeEvaluator", return_value=evaluator), \
                patch.object(scene, "_object_pose", side_effect=AssertionError("object truth leaked")), \
                patch.object(scene, "_finger_contacts", side_effect=AssertionError("contact truth leaked")):
            result = scene.run_episode("pour_prep", self.candidates[1])
            self.assertEqual([x["stage"] for x in result["primitive_log"]],
                             ["pregrasp", "grasp", "close", "lift", "task_clearance",
                              "task_rotate", "task_motion", "hold"])
            self.assertTrue(all(x["feedback"]["status"] == "executed" for x in result["primitive_log"]))
            for action in result["primitive_log"]:
                obs = action["feedback"]["observation"]
                self.assertEqual(set(obs), {"tool_pose", "joint_positions", "gripper_width",
                                           "initial_object_pose_estimate", "observation_source",
                                           "elapsed_s", "actions_executed", "last_action"})

    def test_filter_restores_complete_physics_state(self):
        with MugScene(4, observation_config=ObservationConfig(position_noise_m=.004)) as scene:
            p.resetBaseVelocity(scene.mug, (.1, .2, .3), (.2, .3, .4), physicsClientId=scene.client)
            def snapshot():
                return (scene._object_pose(),
                        p.getBaseVelocity(scene.mug, physicsClientId=scene.client),
                        [p.getJointState(scene.robot, j, physicsClientId=scene.client)[:2]
                         for j in scene.movable])
            before = snapshot()
            scene.check_candidate(self.candidates[1])
            self.assertEqual(before, snapshot())

    def test_one_candidate_warning_and_config_are_logged(self):
        result = get_feasible_candidates(0, self.candidates)
        self.assertIn("fewer_than_two_feasible_candidates", result["selection_space"]["warnings"])
        self.assertEqual(result["observation_config"], asdict(ObservationConfig()))
        json.dumps(result, allow_nan=False)

    def test_no_policy_actions_means_no_automatic_pickup(self):
        observations = []
        def stop(context, observation):
            observations.append(observation)
            return None
        result = run_primitive_episode("pour_prep", 0, "G02", self.candidates, stop)
        self.assertFalse(result["success"])
        self.assertEqual(result["policy_mode"], "incremental")
        self.assertEqual([a["command"]["kind"] for a in result["primitive_log"]], ["wait"])
        self.assertNotIn("finger_contacts", observations[0])

    def test_action_budget_stops_policy_without_executing_extra_action(self):
        result = run_primitive_episode("pour_prep", 0, "G02", self.candidates,
                                       lambda *_: Primitive("wait", duration_s=.1), max_actions=2)
        self.assertEqual(result["failure_reason"], "action_budget_exceeded")
        self.assertEqual(len(result["primitive_log"]), 2)
        self.assertEqual(result["hold_steps"], 0)

    def test_success_requires_every_hold_step_not_only_final_frame(self):
        with MugScene(0) as scene:
            target = scene.task_target("pour_prep")
            off_target = replace(target, position=(target.position[0]+.06, *target.position[1:]))
            evaluator = EpisodeEvaluator(scene, target)
            with patch.object(scene, "_object_pose", return_value=target) as pose, \
                    patch.object(scene, "_finger_contacts", return_value=2), \
                    patch.object(scene, "_unexpected_collision", return_value=[]), \
                    patch("pybullet.getContactPoints", return_value=[]):
                for step in range(480):
                    pose.return_value = off_target if step == 100 else target
                    evaluator.sample("hold", step, step == 479)
                pose.return_value = target
                result = evaluator.finish()
                self.assertEqual(result["position_error_m"], 0)
                self.assertFalse(result["success"])
                self.assertEqual(result["failure_reason"], "target_pose_error")
                self.assertEqual(result["hold_steps"], 480)

    def test_primitive_rejects_far_move_and_preserves_state_during_failed_ik(self):
        with MugScene(0) as scene:
            with self.assertRaisesRegex(ValueError, "0.25"):
                scene.execute_primitive(Primitive("move_tcp", Pose((.9, .4, 1.1), (0, 0, 0, 1))))
            before = scene.observe()
            with patch.object(scene, "_plan_cartesian", return_value=None):
                feedback = scene.execute_primitive(Primitive("move_tcp", scene._tip_pose()))
            self.assertEqual(feedback["status"], "ik_failed")
            self.assertEqual(before["joint_positions"], scene.observe()["joint_positions"])
            self.assertEqual(before["elapsed_s"], scene.observe()["elapsed_s"])


if __name__ == "__main__":
    unittest.main()
