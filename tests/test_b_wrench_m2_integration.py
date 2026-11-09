from dataclasses import replace
import ast
from pathlib import Path
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_a.contracts import candidate_sha256
from grasp_b.wrench_v2.adapter import GeneratorInfo
from grasp_b.wrench_v2.m1 import development_config, prepare_candidate, prepare_contact_spec
from grasp_b.wrench_v2.m2 import score_trajectory, rank_m2_results
from grasp_b.wrench_v2.motion_prediction import predict_motion, PredictedPose, MotionModel
from grasp_b.wrench_v2.dynamics import predict_dynamics
from grasp_b.wrench_v2.tasks import read_declared_object, RigidPayload, combine_payload
from tests import test_b_wrench_m2 as fixtures
from tests import test_b_wrench_motion_prediction as motion_fixtures

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_real_family_candidates_full_id_permutation_and_matched_points(self):
        from tests import test_b_wrench_adapter as geometry
        from grasp_b.multifamily import VERSION
        geometry.StaticAdapterTests.setUpClass()
        f = geometry.StaticAdapterTests()
        cs, families = motion_fixtures.pool()
        chosen = [next(c for c in cs if families[c.id] == family) for family in ("side", "top", "handle", "rim")]
        before = candidate_sha256(chosen)
        cfg = development_config()
        body = read_declared_object(ROOT/"assets/mug_hollow.urdf")
        prepared = [prepare_candidate(c, f.obj, f.gripper, generator_info=GeneratorInfo("grasp_b.multifamily", VERSION, families[c.id], c.id), config=cfg) for c in chosen]
        for task in ("transport_place", "handover_transfer", "pour_water"):
            rows = []
            for c, p in zip(chosen, prepared):
                m = predict_motion(c, task, initial_object_pose=motion_fixtures.INITIAL, estimate_source="fixture nominal prior", model=MotionModel(samples_per_stage=5))
                d = predict_dynamics(m, body, reference_point_m=cfg.reference_point_m, gravity_world_m_s2=(0, 0, -9.81), source="declared inertia fixture, no runtime validation implied")
                r = score_trajectory(p, m, d)
                a = score_trajectory(p, m, d, gravity_only=True)
                self.assertEqual([(s.stage, s.time_s, s.required) for s in r.samples], [(s.stage, s.time_s, s.required) for s in a.samples])
                rows.append(r)
            ranking = rank_m2_results(rows, [c.id for c in chosen])
            self.assertEqual(set(ranking["ranking"]), {c.id for c in chosen})
            self.assertEqual(len(ranking["ranking"]), 4)
        self.assertEqual(candidate_sha256(chosen), before)

    def test_complete_rigid_payload_inputs_are_supported_without_inventing_inertia(self):
        p, m, d = fixtures.fixture(task="pour_water", full=True, n=8)
        s = p.spec
        four = replace(s, contacts=tuple(replace(c, position_m=(c.position_m[0], 0, z), surface_id=c.surface_id+str(z))
                                         for c in s.contacts for z in (-.005, .005)))
        p = prepare_contact_spec(four, p.config)
        payload = RigidPayload(mass_kg=.1, com_m=(0, 0, 0), inertia_com_kg_m2=np.diag((.0001, .0002, .0003)),
                               source="explicit solid rigid payload numerical fixture, NOT calibrated water")
        total = combine_payload(d.body, payload)
        dynamic = predict_dynamics(m, total, reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), source="known-inertia numerical payload fixture")
        r = score_trajectory(p, m, dynamic)
        self.assertEqual(r.status, "scored")
        self.assertEqual(r.provenance["original_missing_inertia_samples"], 0)

    def test_tool_orientation_parameterization_does_not_force_score_difference(self):
        p, m, d = fixtures.fixture()
        q = (Rotation.from_quat(m.grasp_pose.quaternion_xyzw)*Rotation.from_euler("y", .4)).as_quat()
        from grasp_a.contracts import Candidate, Pose
        c = Candidate("reparameterized", "body", "fixture", .077, 0., Pose((0, 0, 0), tuple(q)), Pose((0, 0, -.08), tuple(q)))
        # Same declared analytic contact model; tool rotation is about closing
        # axis. This tests mathematical invariance, not real mesh feasibility.
        spec = replace(p.spec, candidate_id=c.id, candidate_hash=candidate_sha256([c]))
        p2 = prepare_contact_spec(spec, p.config)
        m2 = replace(m, candidate_id=c.id, candidate_hash=spec.candidate_hash,
                     grasp_pose=PredictedPose((0, 0, 0), q),
                     samples=tuple(replace(s, tcp_pose=PredictedPose(s.tcp_pose.position_m, q)) for s in m.samples))
        d2 = predict_dynamics(m2, d.body, reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), source="fixture")
        self.assertAlmostEqual(score_trajectory(p, m, d).score, score_trajectory(p2, m2, d2).score)

    def test_velocity_jump_is_required_unknown_motion_not_silent_finite_force(self):
        p, _, d = fixtures.fixture()
        # Recover the original full Candidate geometry used by fixture().
        from grasp_a.contracts import Candidate, Pose
        q = tuple(Rotation.from_euler("z", -90, degrees=True).as_quat())
        c = Candidate(p.spec.candidate_id, "body", "numerical fixture only", .077, 0., Pose((0, 0, 0), q), Pose((0, 0, -.08), (0, 0, 0, 1)))
        m = predict_motion(c, "transport_place", initial_object_pose=motion_fixtures.INITIAL,
                           estimate_source="fixture", model=MotionModel(samples_per_stage=5, interpolation="linear"))
        dynamic = predict_dynamics(m, d.body, reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), source="fixture")
        r = score_trajectory(p, m, dynamic)
        self.assertEqual(r.status, "unknown_motion")
        self.assertIsNone(r.score)
        self.assertTrue(any(s.side == "boundary_event" for s in r.samples))

    def test_no_physical_execution_or_outcome_calls_in_new_modules(self):
        for filename in ("m2.py", "m2_smoke.py"):
            for node in ast.walk(ast.parse((ROOT/"grasp_b/wrench_v2"/filename).read_text())):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                    self.assertNotIn(name, ("run_sequence", "run_episode", "stepSimulation", "getContactPoints", "getBasePositionAndOrientation"))


if __name__ == "__main__":
    unittest.main()
