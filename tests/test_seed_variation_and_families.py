import math
import unittest
from pathlib import Path

import numpy as np

from grasp_a.control import SCENE_PRESETS, SceneRandomization, scene_randomization
from grasp_a.sequence_contracts import SequenceConfig
from grasp_a.sequence_evaluation import terminal_checks
from grasp_b.features import tool_axes
from grasp_b.geometry import tool_orientation
from grasp_b.multifamily import (MultiFamilyConfig, generate_multifamily, m2_supported,
                                 orientation, side_axes)

try:
    import pybullet_data
    PANDA = Path(pybullet_data.getDataPath()) / "franka_panda/panda.urdf"
except ImportError:  # pragma: no cover
    PANDA = None


class SceneRandomizationTests(unittest.TestCase):
    def test_default_is_legacy(self):
        legacy = scene_randomization(None)
        self.assertEqual(legacy, SceneRandomization())
        self.assertEqual(legacy.x_range_m, (-0.008, 0.008))
        self.assertEqual(legacy.yaw_range_rad, (-0.08, 0.08))
        self.assertIsNone(legacy.estimate_config(3, None))

    def test_wide_estimate_tracks_seeded_truth(self):
        from grasp_a.contracts import Pose
        wide = SCENE_PRESETS["wide"]
        truth = Pose((0.57, -0.02, 0.645), (0, 0, 0.3, math.sqrt(1-0.09)))
        config = wide.estimate_config(4, truth)
        self.assertEqual(config.source, "external_estimate")
        self.assertEqual(config.object_pose, truth)
        self.assertNotEqual(wide.estimate_config(5, truth).noise_seed, config.noise_seed)

    def test_rejects_ambiguous_or_unbounded(self):
        with self.assertRaises(ValueError):
            SceneRandomization(x_range_m=(0.02, -0.02))
        with self.assertRaises(ValueError):
            SceneRandomization(y_range_m=(-0.2, 0.0))
        with self.assertRaises(ValueError):
            SceneRandomization(estimate_position_noise_m=0.01)  # prior mode


@unittest.skipIf(PANDA is None, "pybullet_data unavailable")
class SceneSeedTests(unittest.TestCase):
    def test_legacy_scene_reproduces_old_draws_and_wide_varies_estimate(self):
        import random
        from grasp_a.simulation import MugScene
        with MugScene(7) as scene:
            rng = random.Random(7)
            expected = (0.55+rng.uniform(-0.008, 0.008), rng.uniform(-0.008, 0.008))
            self.assertAlmostEqual(scene.initial_mug_pose.position[0], expected[0], places=12)
            self.assertAlmostEqual(scene.initial_mug_pose.position[1], expected[1], places=12)
            self.assertEqual(scene.observed_mug_pose.position, (0.55, 0.0, 0.645))
        estimates = []
        for seed in (0, 1):
            with MugScene(seed, scene_config="wide") as scene:
                truth, est = scene.initial_mug_pose.position, scene.observed_mug_pose.position
                self.assertLess(max(abs(a-b) for a, b in zip(truth, est)), 0.0031)
                estimates.append(est)
        self.assertNotEqual(estimates[0], estimates[1])


@unittest.skipIf(PANDA is None, "pybullet_data unavailable")
class MultiFamilyTests(unittest.TestCase):
    def setUp(self):
        from grasp_b.models import read_model
        self.hollow = read_model("hollow", PANDA)
        self.solid = read_model("solid", PANDA)

    def test_pitch_zero_matches_legacy_orientation(self):
        for yaw in range(0, 360, 45):
            for roll in (0, 180):
                a = np.array(tool_orientation(yaw, roll))
                b = np.array(orientation(*side_axes(yaw, 0, roll)))
                self.assertLess(min(abs(a-b).max(), abs(a+b).max()), 1e-12)

    def test_families_regions_and_tool_axes(self):
        candidates, records = generate_multifamily(self.hollow)
        kinds = {r["family"] for r in records}
        self.assertEqual(kinds, {"side", "top", "rim", "handle"})
        self.assertEqual(len({c.id for c in candidates}), len(candidates))
        for c, r in zip(candidates, records):
            self.assertEqual(c.region, r["region"])
            _, closing, approach = tool_axes(c.grasp_pose.quaternion)
            np.testing.assert_allclose(approach, r["approach_axis_mug"], atol=1e-9)
            delta = np.subtract(c.grasp_pose.position, c.pregrasp_pose.position)
            self.assertGreater(delta @ np.array(approach), 0.1)
            self.assertLessEqual(c.gripper_width, 0.08)
        _, solid_records = generate_multifamily(self.solid)
        self.assertNotIn("rim", {r["family"] for r in solid_records})

    def test_body_families_remain_scoreable_by_b_models(self):
        from grasp_b.risk import static_score
        candidates, records = generate_multifamily(self.hollow)
        for c, r in zip(candidates, records):
            if m2_supported(r):
                self.assertTrue(0 < static_score(c, self.hollow)["score"] <= 1)

    def test_budget_and_family_validation(self):
        with self.assertRaises(ValueError):
            MultiFamilyConfig(families=("side", "lid"))
        with self.assertRaises(ValueError):
            generate_multifamily(self.hollow, MultiFamilyConfig(max_candidates=10))


class PourProxyChecksTests(unittest.TestCase):
    def evidence(self, **proxy):
        base = dict(dwell_samples=480, over_target_fraction=1.0, path_clear_fraction=1.0,
                    min_tilt_deg=110.0)
        base.update(proxy)
        return dict(source_upright_deg=1, source_linear_speed_m_s=0, source_angular_speed_deg_s=0,
                    donor_contact=True, source_goal_error_m=0.01, target_upright_deg=0,
                    target_linear_speed_m_s=0, target_angular_speed_deg_s=0,
                    target_displacement_m=0, water=None, pour_proxy=base)

    def test_proxy_replaces_water_checks(self):
        checks = terminal_checks("pour_water", SequenceConfig(), self.evidence())
        self.assertTrue(all(checks.values()))
        self.assertNotIn("received", checks)

    def test_proxy_failures(self):
        c = SequenceConfig()
        self.assertFalse(terminal_checks("pour_water", c, self.evidence(path_clear_fraction=.5))["pour_path_clear"])
        self.assertFalse(terminal_checks("pour_water", c, self.evidence(over_target_fraction=.2))["spout_over_target"])
        self.assertFalse(terminal_checks("pour_water", c, self.evidence(dwell_samples=0))["poured_at_tilt"])

    def test_without_proxy_water_still_required(self):
        evidence = self.evidence()
        evidence.pop("pour_proxy")
        self.assertFalse(terminal_checks("pour_water", SequenceConfig(), evidence)["received"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(PANDA is None, "pybullet_data unavailable")
class ExecutionFilterTests(unittest.TestCase):
    def test_preset_and_prep_track_guard(self):
        self.assertEqual(SCENE_PRESETS["wide_strict"].filter_mode, "execution")
        with self.assertRaises(ValueError):
            SceneRandomization(filter_mode="fast")
        from grasp_a.simulation import MugScene
        from grasp_b.models import read_model
        candidate = generate_multifamily(read_model("solid", PANDA))[0][0]
        with MugScene(0, scene_config="wide_strict") as scene:
            with self.assertRaises(ValueError):
                scene.check_candidate(candidate)

    def test_donor_moves_match_stage_list_and_replay_restores_state(self):
        from grasp_a.sequence import SequenceScene
        from grasp_b.models import read_model
        candidates, records = generate_multifamily(read_model("hollow", PANDA))
        top = next(c for c, r in zip(candidates, records) if r["family"] == "top")
        with SequenceScene("pour_water", 0, scene_config="wide_strict") as scene:
            moves = scene.donor_moves(top)
            self.assertEqual(list(moves), ["pregrasp", "approach", "lift", "transport", "tilt", "return_upright"])
            before = (scene._angles(), scene._object_pose(), len(scene.ik_diagnostics))
            check = scene.check_candidate(top)
            self.assertIsInstance(check.feasible, bool)
            self.assertEqual(before, (scene._angles(), scene._object_pose(), len(scene.ik_diagnostics)))


class ApproachContactConfigTests(unittest.TestCase):
    def test_final_only_requires_execution_filter(self):
        self.assertEqual(SCENE_PRESETS["wide_strict_v2"].approach_finger_contact, "final_only")
        self.assertEqual(SCENE_PRESETS["wide_strict"].approach_finger_contact, "any")
        with self.assertRaises(ValueError):
            SceneRandomization(approach_finger_contact="final_only")  # legacy filter
        with self.assertRaises(ValueError):
            SceneRandomization(filter_mode="execution", approach_finger_contact="never")


@unittest.skipIf(PANDA is None, "pybullet_data unavailable")
class ApproachContactReplayTests(unittest.TestCase):
    def test_deep_top_grasp_closing_along_handle_is_rejected(self):
        from grasp_a.sequence import SequenceScene
        from grasp_b.models import read_model
        candidates, _ = generate_multifamily(read_model("hollow", PANDA))
        blocked = next(c for c in candidates if c.approach == "top_down_closing_yaw_90_depth_30mm")
        with SequenceScene("transport_place", 0, scene_config="wide_strict_v2") as scene:
            check = scene.check_candidate(blocked)
        self.assertFalse(check.feasible)
        self.assertIn("finger_contact_before_grasp", check.reasons[0])


class CSceneConfigTests(unittest.TestCase):
    def config(self, **extra):
        root = Path(__file__).resolve().parents[1]
        base = {"candidates": str(root / "configs/candidates.c-pilot.json"), "seeds": [0],
                "tasks": ["transport_place"], "repeats": 1, "evaluation_scope": "pilot",
                "cache_dir": str(root / ".cache/grasp_c"), "seed_split": {"pilot_controller": [0]},
                "methods": [{"id": "GEOMETRY_REFERENCE", "kind": "geometry"}],
                "policy_mode": "fixed", "track": "sequence", "sequence_config": {}}
        base.update(extra)
        return base

    def test_scene_preset_is_normalized_and_guarded(self):
        from grasp_c.config import validate_config
        config = validate_config(self.config(scene_randomization="wide_strict_v2"))
        self.assertEqual(config["scene_randomization"]["filter_mode"], "execution")
        self.assertNotIn("scene_randomization", validate_config(self.config()))
        with self.assertRaises(ValueError):
            validate_config(self.config(scene_randomization="wide", observation_config={"position_noise_m": 0.01}))
        prep = self.config(track="prep", tasks=["pour_prep"], scene_randomization="wide")
        prep.pop("sequence_config")
        with self.assertRaisesRegex(ValueError, "scene_randomization requires"):
            validate_config(prep)


class WristHandCollisionTests(unittest.TestCase):
    def test_preset_and_validation(self):
        self.assertTrue(SCENE_PRESETS["wide_strict_v3"].disable_wrist_hand_collision)
        self.assertFalse(SCENE_PRESETS["wide_strict_v2"].disable_wrist_hand_collision)
        with self.assertRaises(ValueError):
            SceneRandomization(disable_wrist_hand_collision=1)

    @unittest.skipIf(PANDA is None, "pybullet_data unavailable")
    def test_only_link7_hand_pair_is_filtered(self):
        import pybullet as p
        from grasp_a.simulation import MugScene

        def contacts(scene_config):
            with MugScene(0, scene_config=scene_config) as scene:
                for _ in range(5):
                    p.stepSimulation(physicsClientId=scene.client)
                return {(c[3], c[4]) for c in p.getContactPoints(scene.robot, scene.robot,
                                                                 physicsClientId=scene.client)}
        names = {}
        with MugScene(0) as scene:
            for j in range(p.getNumJoints(scene.robot, physicsClientId=scene.client)):
                names[p.getJointInfo(scene.robot, j, physicsClientId=scene.client)[12].decode()] = j
        pair = {(names["panda_link7"], names["panda_hand"]), (names["panda_hand"], names["panda_link7"])}
        self.assertTrue(contacts("wide_strict_v2") & pair)
        self.assertFalse(contacts("wide_strict_v3") & pair)
