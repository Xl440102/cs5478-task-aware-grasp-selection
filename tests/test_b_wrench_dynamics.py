"""Analytic mechanics, static asset queries and explicit unknown-load states."""
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_b.wrench_v2.motion_prediction import PredictedPose, PredictedMotionSample, compose
from grasp_b.wrench_v2.dynamics import object_parameters_from_snapshot, point_kinematics, predict_dynamics
from grasp_b.wrench_v2.tasks import ObjectParameters, Orientation, RigidPayload, combine_payload, build_task_wrench_set
from tests import test_b_wrench_motion_prediction as fixtures

ROOT = Path(__file__).resolve().parents[1]


def body(**changes):
    values = dict(mass_kg=1., com_m=(0, 0, 0), inertia_com_kg_m2=np.diag((.01, .02, .03)), source="explicit analytical numerical fixture")
    values.update(changes)
    return ObjectParameters(**values)


def state(*, pose=fixtures.IDENTITY, v=(0, 0, 0), a=(0, 0, 0), omega=(0, 0, 0), alpha=(0, 0, 0), support="donor_only_rigid_attachment_assumed"):
    context = fixtures.context()
    tcp = compose(pose, context.grasp_pose)
    _, tcp_v, tcp_a = point_kinematics(pose, v, a, omega, alpha, context.grasp_pose.position_m)
    sample = PredictedMotionSample("analytical_fixture", 0, 0, pose, tcp, v, a, tcp_v, tcp_a, omega, alpha, support)
    return replace(context, samples=(sample,), stages=(), boundaries=())


def evaluate(motion=None, model=None, reference=(0, 0, 0), gravity=(0, 0, -9.81)):
    return predict_dynamics(state() if motion is None else motion, body() if model is None else model,
        reference_point_m=reference, gravity_world_m_s2=gravity, source="explicit analytical fixture").samples[0]


class NewtonEulerTests(unittest.TestCase):
    def test_static_gravity_matches_task03_sign_com_and_reference(self):
        pose = PredictedPose((1, 2, 3), Rotation.from_euler("xyz", (.2, .3, .4)).as_quat())
        model = body(mass_kg=.28, com_m=(.01, .02, .03))
        reference = (.002, -.004, .005)
        result = evaluate(state(pose=pose), model, reference)
        static = build_task_wrench_set("transport_place", model, reference_point_m=reference,
            gravity_world_m_s2=(0, 0, -9.81), context_source="explicit reference fixture",
            orientations=(Orientation(quaternion_xyzw=pose.quaternion_xyzw, source="fixture"),)).scenarios[0]
        np.testing.assert_allclose(result.required_contact_wrench_mug, static.required_contact_wrench, atol=1e-14)

    def test_constant_velocity_and_acceleration_force(self):
        stationary = evaluate()
        moving = evaluate(state(v=(1, 2, 3)))
        np.testing.assert_allclose(stationary.net_required_force_world_n, moving.net_required_force_world_n)
        accelerated = evaluate(state(a=(1, 2, 3)), body(mass_kg=2))
        np.testing.assert_allclose(accelerated.net_required_force_world_n, (2, 4, 25.62))

    def test_tcp_and_object_origin_com_acceleration_agree_without_double_offset(self):
        pose = PredictedPose((1, 2, 3), Rotation.from_euler("xyz", (.4, .2, .1)).as_quat())
        m = state(pose=pose, v=(.1, .2, .3), a=(1, 2, 3), omega=(1, 2, 3), alpha=(.1, .2, -.1))
        s = m.samples[0]
        com = np.array((.01, .02, .03))
        direct = point_kinematics(s.object_pose, s.object_origin_velocity_world_m_s,
            s.object_origin_acceleration_world_m_s2, s.omega_world_rad_s, s.alpha_world_rad_s2, com)
        tcp_local_offset = Rotation.from_quat(m.grasp_pose.quaternion_xyzw).inv().apply(com-m.grasp_pose.position_m)
        via_tcp = point_kinematics(s.tcp_pose, s.tcp_velocity_world_m_s, s.tcp_acceleration_world_m_s2,
                                  s.omega_world_rad_s, s.alpha_world_rad_s2, tcp_local_offset)
        for x, y in zip(direct, via_tcp):
            np.testing.assert_allclose(x, y, atol=1e-14)
        result = evaluate(m, body(com_m=com))
        np.testing.assert_allclose(result.com_acceleration_world_m_s2, direct[2], atol=1e-14)

    def test_centripetal_acceleration(self):
        result = evaluate(state(omega=(0, 0, 2)), body(com_m=(.1, 0, 0)), gravity=(0, 0, 0))
        np.testing.assert_allclose(result.com_velocity_world_m_s, (0, .2, 0))
        np.testing.assert_allclose(result.com_acceleration_world_m_s2, (-.4, 0, 0))
        np.testing.assert_allclose(result.net_required_force_world_n, (-.4, 0, 0))

    def test_pure_rotation_and_gyroscopic_torque(self):
        alpha = evaluate(state(alpha=(0, 0, 3)), gravity=(0, 0, 0))
        np.testing.assert_allclose(alpha.net_required_torque_com_world_nm, (0, 0, .09))
        gyro = evaluate(state(omega=(1, 2, 3)), gravity=(0, 0, 0))
        np.testing.assert_allclose(gyro.net_required_torque_com_world_nm, (.06, -.06, .02))

    def test_inertia_rotation_to_world(self):
        pose = PredictedPose((0, 0, 0), Rotation.from_euler("y", 90, degrees=True).as_quat())
        r = evaluate(state(pose=pose, alpha=(1, 0, 0)), gravity=(0, 0, 0))
        np.testing.assert_allclose(r.net_required_torque_com_world_nm, (.03, 0, 0), atol=1e-14)

    def test_reference_shift(self):
        motion = state(pose=PredictedPose((1, 2, 3), Rotation.from_euler("x", .4).as_quat()), a=(1, 2, 3), alpha=(.1, .2, .3))
        old = evaluate(motion, body(com_m=(.01, .02, .03)))
        delta = np.array((.01, -.02, .03))
        new = evaluate(motion, body(com_m=(.01, .02, .03)), reference=delta)
        a, b = np.array(old.required_contact_wrench_mug), np.array(new.required_contact_wrench_mug)
        np.testing.assert_allclose(a[:3], b[:3])
        np.testing.assert_allclose(b[3:], a[3:]-np.cross(delta, a[:3]), atol=1e-14)

    def test_unknown_inertia_retained_for_rotation_but_static_terms_do_not_need_it(self):
        unknown = body(inertia_com_kg_m2=None)
        self.assertEqual(evaluate(model=unknown).status, "complete")
        rotating = evaluate(state(alpha=(0, 0, 1)), unknown)
        self.assertEqual(rotating.status, "missing_inertia")
        self.assertIsNone(rotating.required_contact_wrench_mug)
        self.assertIsNone(rotating.net_required_torque_com_world_nm)
        np.testing.assert_allclose(rotating.net_required_force_world_n, (0, 0, 9.81))

    def test_no_post_release_or_unknown_table_support_donor_wrench(self):
        motion = fixtures.context("transport_place")
        result = predict_dynamics(motion, body(), reference_point_m=(0, 0, 0),
                                  gravity_world_m_s2=(0, 0, -9.81), source="fixture")
        self.assertEqual(result.samples[-1].status, "unsupported_support_transition")
        self.assertIsNone(result.samples[-1].required_contact_wrench_mug)
        self.assertNotIn("release", {s.stage for s in result.samples})
        self.assertTrue(result.provenance["stage_boundaries"])

    def test_serialization_immutability_and_invalid_gravity(self):
        motion = fixtures.context()
        d = predict_dynamics(motion, body(), reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), source="fixture")
        json.dumps(d.to_dict(), allow_nan=False)
        with self.assertRaises(FrozenInstanceError):
            d.samples = ()
        with self.assertRaises(ValueError):
            evaluate(gravity=(0, 0, float("nan")))


class SnapshotAndPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pybullet as p
        from grasp_b.wrench_v2.physics_snapshot import static_physics_snapshot
        with ExitStack() as stack:
            for method in ("stepSimulation", "setJointMotorControl2", "setJointMotorControlArray", "changeDynamics"):
                stack.enter_context(patch.object(p, method, side_effect=AssertionError("static query only")))
            cls.snapshot = static_physics_snapshot(ROOT)
        cls.path = ROOT/"assets/mug_hollow.urdf"

    def model(self):
        return object_parameters_from_snapshot(self.snapshot, asset_path=self.path)

    def test_runtime_inertia_asset_binding_and_provenance(self):
        model = self.model()
        entry = self.snapshot["assets"]["sequence"]
        np.testing.assert_allclose(model.inertia_com_kg_m2, np.diag(entry["dynamics"]["local_inertia_diagonal_kg_m2"]))
        self.assertEqual(model.provenance["asset_sha256"], entry["asset_sha256"])
        self.assertEqual(model.provenance["loading_flags"], 0)
        self.assertEqual(model.source, "runtime_loaded_initial_asset_dynamics_not_measured_trajectory")
        with self.assertRaises(ValueError):
            object_parameters_from_snapshot(self.snapshot, asset_path=self.path, expected_flags=2)
        with tempfile.TemporaryDirectory() as tmp:
            modified = Path(tmp)/"mug.urdf"
            modified.write_bytes(self.path.read_bytes()+b'\n')
            with self.assertRaises(ValueError):
                object_parameters_from_snapshot(self.snapshot, asset_path=modified)

    def test_rotated_inertial_frame_mapper_synthetic_snapshot_fixture(self):
        snapshot = json.loads(json.dumps(self.snapshot))
        d = snapshot["assets"]["sequence"]["dynamics"]
        d["local_inertial_quaternion_xyzw"] = Rotation.from_euler("y", 90, degrees=True).as_quat().tolist()
        d["local_inertial_position_m"] = [.01, 0, .02]
        result = object_parameters_from_snapshot(snapshot, asset_path=self.path)
        xx, yy, zz = d["local_inertia_diagonal_kg_m2"]
        np.testing.assert_allclose(result.inertia_com_kg_m2, np.diag((zz, yy, xx)), atol=1e-14)
        self.assertEqual(result.com_m, (.01, 0, .02))

    def test_rigid_payload_parallel_axis_and_zero_water(self):
        dry = self.model()
        payload = RigidPayload(mass_kg=.1, com_m=(.01, .02, .03), inertia_com_kg_m2=np.zeros((3, 3)),
                               source="explicit assumed entrained point mass fixture, not PBF")
        combined = combine_payload(dry, payload)
        np.testing.assert_allclose(combined.com_m, np.array(payload.com_m)*.1/.38)
        r = np.array(payload.com_m)
        expected = np.array(dry.inertia_com_kg_m2)+(.28*.1/.38)*((r@r)*np.eye(3)-np.outer(r, r))
        np.testing.assert_allclose(combined.inertia_com_kg_m2, expected, atol=1e-14)
        zero = combine_payload(dry, replace(payload, mass_kg=0, inertia_com_kg_m2=None))
        self.assertEqual(zero.inertia_com_kg_m2, dry.inertia_com_kg_m2)
        unknown = combine_payload(dry, replace(payload, inertia_com_kg_m2=None))
        self.assertIsNone(unknown.inertia_com_kg_m2)
        self.assertEqual(evaluate(state(alpha=(1, 0, 0)), unknown).status, "missing_inertia")


class BoundaryTests(unittest.TestCase):
    def test_new_modules_have_no_execution_or_lp_calls(self):
        import ast
        for filename in ("motion_prediction.py", "dynamics.py"):
            source = ROOT/"grasp_b/wrench_v2"/filename
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                    self.assertNotIn(name, ("run_episode", "run_sequence", "stepSimulation", "getContactPoints", "solve_wrench", "linprog"))


if __name__ == "__main__":
    unittest.main()
