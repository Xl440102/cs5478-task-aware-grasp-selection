"""Analytical and command-level checks, not execution trajectory validation."""
from dataclasses import FrozenInstanceError, replace
from functools import lru_cache
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_a.contracts import Pose
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from grasp_b.wrench_v2.motion_prediction import (
    MotionModel, PredictedPose, PredictedStage, compose, inverse, predict_motion,
    sample_stage, progress_law, audited_sources,
)

IDENTITY = PredictedPose((0, 0, 0), (0, 0, 0, 1))
INITIAL = PredictedPose((.55, 0, .645), (0, 0, 0, 1))


@lru_cache(maxsize=1)
def pool():
    from tests import test_b_wrench_adapter as fixtures
    from grasp_b.multifamily import generate_multifamily, pruned_config
    fixtures.StaticAdapterTests.setUpClass()
    f = fixtures.StaticAdapterTests()
    cs, records = generate_multifamily(f.model, pruned_config())
    return cs, {r["id"]: r["family"] for r in records}


def context(task="pour_water", **options):
    c = pool()[0][0]
    return predict_motion(c, task, initial_object_pose=INITIAL, estimate_source="explicit numerical prior", **options)


class PoseTests(unittest.TestCase):
    def test_identity_translation_rotation_and_inverse(self):
        a = PredictedPose((1, 2, 3), Rotation.from_euler("z", 90, degrees=True).as_quat())
        b = PredictedPose((1, 0, 0), Rotation.from_euler("x", 30, degrees=True).as_quat())
        composed = compose(a, b)
        np.testing.assert_allclose(composed.position_m, (1, 3, 3), atol=1e-14)
        recovered = compose(composed, inverse(b))
        np.testing.assert_allclose(recovered.position_m, a.position_m, atol=1e-14)
        np.testing.assert_allclose(Rotation.from_quat(recovered.quaternion_xyzw).as_matrix(), Rotation.from_quat(a.quaternion_xyzw).as_matrix(), atol=1e-14)
        self.assertEqual(compose(IDENTITY, IDENTITY), IDENTITY)

    def test_quaternion_sign_and_invalid_inputs(self):
        self.assertEqual(PredictedPose((0, 0, 0), (0, 0, 0, -1)), IDENTITY)
        for q in ((0, 0, 0, 0), (0, 0, 0, 2), (0, 0, float("nan"), 1)):
            with self.assertRaises(ValueError):
                PredictedPose((0, 0, 0), q)
        with self.assertRaises(ValueError):
            PredictedPose((0, float("inf"), 0), (0, 0, 0, 1))

    def test_interior_object_path_can_depend_on_grasp_not_just_endpoints(self):
        start = PredictedPose((1, 2, 3), (0, 0, 0, 1))
        end = PredictedPose((1, 2, 3), Rotation.from_euler("z", 90, degrees=True).as_quat())
        shift = PredictedPose((.1, 0, 0), (0, 0, 0, 1))
        def run(grasp):
            stage = PredictedStage("turn", compose(start, grasp), compose(end, grasp), 0., 1., "pure TCP interpolation fixture")
            return sample_stage(stage, grasp, MotionModel(samples_per_stage=3, interpolation="linear"))
        a, b = run(IDENTITY), run(shift)
        for i in (0, 2):
            np.testing.assert_allclose(a[i].object_pose.position_m, b[i].object_pose.position_m, atol=1e-14)
        self.assertGreater(np.linalg.norm(np.array(a[1].object_pose.position_m)-b[1].object_pose.position_m), .02)
        np.testing.assert_allclose(b[1].object_pose.position_m, (1+.05-.1/math.sqrt(2), 2+.05-.1/math.sqrt(2), 3))

    def test_pure_translation_object_motion_agrees_across_grasps(self):
        end = PredictedPose((1, 2, 3), (0, 0, 0, 1))
        results = []
        for g in (IDENTITY, PredictedPose((.1, .2, .3), Rotation.from_euler("xyz", (.2, .3, .4)).as_quat())):
            results.append(sample_stage(PredictedStage("translate", compose(IDENTITY, g), compose(end, g), 0., 2., "fixture"), g,
                                         MotionModel(samples_per_stage=5)))
        for a, b in zip(*results):
            np.testing.assert_allclose(a.object_pose.position_m, b.object_pose.position_m, atol=1e-14)
            np.testing.assert_allclose(a.object_origin_acceleration_world_m_s2, b.object_origin_acceleration_world_m_s2, atol=1e-14)

    def test_tool_rotation_offset_alone_does_not_invent_object_motion_difference(self):
        start = PredictedPose((0, 0, 0), Rotation.from_euler("xyz", (.2, .3, .4)).as_quat())
        end = PredictedPose((1, 2, 3), Rotation.from_euler("xyz", (-.3, .5, -.1)).as_quat())
        tracks = []
        for q in ((0, 0, 0, 1), Rotation.from_euler("xyz", (.7, -.2, .9)).as_quat()):
            grasp = PredictedPose((.03, .04, .01), q)
            tracks.append(sample_stage(PredictedStage("fixture", compose(start, grasp), compose(end, grasp), 0, 2, "fixture"),
                                       grasp, MotionModel(samples_per_stage=5)))
        for a, b in zip(*tracks):
            np.testing.assert_allclose(a.object_pose.position_m, b.object_pose.position_m, atol=1e-14)
            np.testing.assert_allclose(a.object_origin_acceleration_world_m_s2, b.object_origin_acceleration_world_m_s2, atol=1e-14)
            np.testing.assert_allclose(a.omega_world_rad_s, b.omega_world_rad_s, atol=1e-14)


class DerivativeTests(unittest.TestCase):
    def test_static_constant_velocity_constant_acceleration(self):
        for name in ("linear", "quadratic", "half_cosine", "quintic"):
            s = sample_stage(PredictedStage("static", IDENTITY, IDENTITY, 0., 2., "fixture"), IDENTITY,
                             MotionModel(samples_per_stage=5, interpolation=name))
            np.testing.assert_array_equal(s[2].object_origin_acceleration_world_m_s2, (0, 0, 0))
        end = PredictedPose((2, 0, 0), (0, 0, 0, 1))
        stage = PredictedStage("move", IDENTITY, end, 0., 2., "fixture")
        linear = sample_stage(stage, IDENTITY, MotionModel(samples_per_stage=3, interpolation="linear"))
        quadratic = sample_stage(stage, IDENTITY, MotionModel(samples_per_stage=3, interpolation="quadratic"))
        np.testing.assert_allclose(linear[1].tcp_velocity_world_m_s, (1, 0, 0))
        np.testing.assert_allclose(linear[1].tcp_acceleration_world_m_s2, (0, 0, 0))
        np.testing.assert_allclose(quadratic[1].tcp_acceleration_world_m_s2, (1, 0, 0))

    def test_fixed_world_axis_and_constant_angular_acceleration(self):
        r0 = Rotation.from_euler("x", .4)
        r1 = Rotation.from_rotvec((0, 0, 1.))*r0
        stage = PredictedStage("turn", PredictedPose((0, 0, 0), r0.as_quat()), PredictedPose((0, 0, 0), r1.as_quat()), 0, 2, "fixture")
        for law, omega, alpha in (("linear", .5, 0.), ("quadratic", .5, .5)):
            s = sample_stage(stage, IDENTITY, MotionModel(samples_per_stage=3, interpolation=law))[1]
            np.testing.assert_allclose(s.omega_world_rad_s, (0, 0, omega), atol=1e-14)
            np.testing.assert_allclose(s.alpha_world_rad_s2, (0, 0, alpha), atol=1e-14)

    def test_general_so3_derivative_not_euler_rate(self):
        e0, e1 = np.array((.3, .4, .5)), np.array((-.4, .8, -.2))
        r0, r1 = Rotation.from_euler("xyz", e0), Rotation.from_euler("xyz", e1)
        stage = PredictedStage("turn", PredictedPose((0, 0, 0), r0.as_quat()), PredictedPose((0, 0, 0), r1.as_quat()), 0, 2, "fixture")
        s = sample_stage(stage, IDENTITY, MotionModel(samples_per_stage=3, interpolation="linear"))[1]
        phi = (r1*r0.inv()).as_rotvec()
        epsilon = 1e-6
        rm = (Rotation.from_rotvec((.5-epsilon/2)*phi)*r0).as_matrix()
        rp = (Rotation.from_rotvec((.5+epsilon/2)*phi)*r0).as_matrix()
        r = Rotation.from_quat(s.object_pose.quaternion_xyzw).as_matrix()
        skew = ((rp-rm)/(2*epsilon))@r.T
        np.testing.assert_allclose(s.omega_world_rad_s, (skew[2, 1], skew[0, 2], skew[1, 0]), atol=1e-9)
        self.assertGreater(np.linalg.norm(np.array(s.omega_world_rad_s)-(e1-e0)/2), .01)

    def test_boundary_derivatives_are_one_sided_and_no_impulse_is_invented(self):
        m = context(model=MotionModel(samples_per_stage=5))
        self.assertEqual(len(m.boundaries), len(m.stages)-1)
        self.assertTrue(any(np.linalg.norm(b["acceleration_jump_world_m_s2"]) > .01 for b in m.boundaries))
        self.assertTrue(all(np.isfinite(s.object_origin_acceleration_world_m_s2).all() for s in m.samples))
        linear = context(model=MotionModel(samples_per_stage=5, interpolation="linear"))
        self.assertTrue(any(np.linalg.norm(b["velocity_jump_world_m_s"]) > .01 for b in linear.boundaries))
        for law in ("half_cosine", "quintic"):
            _, v, a = progress_law(law, np.array((0., 1.)), 2.)
            np.testing.assert_allclose(v, (0, 0), atol=1e-14)
            if law == "quintic":
                np.testing.assert_allclose(a, (0, 0), atol=1e-14)


class CommandTests(unittest.TestCase):
    def test_four_families_a_goal_transform_duration_and_initial_yaw(self):
        # Only A's pure goal builder on a pose-only stub; no execution logic.
        import pybullet as p
        from grasp_a.sequence import SequenceScene
        cs, families = pool()
        initial = PredictedPose((.55, 0, .645), Rotation.from_euler("z", .6).as_quat())
        def world_pose(local):
            result = compose(initial, PredictedPose(local.position, local.quaternion))
            return Pose(result.position_m, result.quaternion_xyzw)
        for family in ("side", "top", "handle", "rim"):
            candidate = next(c for c in cs if families[c.id] == family)
            for task in ("transport_place", "handover_transfer", "pour_water"):
                cfg = SequenceConfig()
                stub = SimpleNamespace(config=cfg, task_id=task, spec=task_spec(task, cfg), world_pose=world_pose)
                with patch.object(p, "stepSimulation", side_effect=AssertionError("no stepping")), patch.object(
                        SequenceScene, "__init__", side_effect=AssertionError("no scene")):
                    expected = SequenceScene.donor_moves(stub, candidate)
                    prediction = predict_motion(candidate, task, initial_object_pose=initial, estimate_source="explicit yaw fixture", model=MotionModel(samples_per_stage=3))
                for stage in prediction.stages:
                    if stage.name not in expected:
                        continue
                    endpoint, path, duration = expected[stage.name]
                    self.assertEqual(stage.duration_s, duration)
                    self.assertEqual(stage.path, path)
                    np.testing.assert_allclose(stage.tcp_end.position_m, endpoint.position, atol=1e-6)
                    np.testing.assert_allclose(Rotation.from_quat(stage.tcp_end.quaternion_xyzw).as_matrix(),
                                               Rotation.from_quat(endpoint.quaternion).as_matrix(), atol=1e-6)
                carry = next(s for s in prediction.samples if s.stage == "transport" and s.side == "end")
                np.testing.assert_allclose(Rotation.from_quat(carry.object_pose.quaternion_xyzw).as_matrix(), np.eye(3), atol=1e-14)

    def test_support_boundaries_and_untimed_handover(self):
        transport = context("transport_place")
        self.assertNotIn("release", {s.stage for s in transport.samples})
        self.assertEqual(transport.samples[-1].support, "table_contact_transition_unknown")
        handover = context("handover_transfer")
        self.assertIsNone(handover.stages[-1].duration_s)
        self.assertFalse(handover.samples[-1].timed)
        self.assertEqual(handover.samples[-1].side, "untimed_hold")
        self.assertNotIn("receiver_close", {s.stage for s in handover.samples})

    def test_determinism_immutability_sources_and_json(self):
        a, b = context(), context()
        self.assertEqual(a.samples, b.samples)
        self.assertEqual(a.candidate_hash, b.candidate_hash)
        self.assertEqual(a.provenance["source_hashes"], audited_sources())
        with self.assertRaises(FrozenInstanceError):
            a.samples = ()
        json.dumps(a.to_dict(), allow_nan=False)
        with patch("grasp_b.wrench_v2.motion_prediction.AUDITED_SOURCE_SHA256", {"grasp_a/sequence.py": "wrong"}):
            with self.assertRaises(ValueError):
                context()

    def test_invalid_time_models(self):
        for value in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                progress_law("linear", .5, value)
        with self.assertRaises(ValueError):
            progress_law("linear", 1.1, 1)
        with self.assertRaises(ValueError):
            MotionModel(samples_per_stage=2)


if __name__ == "__main__":
    unittest.main()
