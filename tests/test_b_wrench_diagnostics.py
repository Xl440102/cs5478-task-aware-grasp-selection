"""Static top conflicts and C0/C1 rim diagnostics, with no execution labels."""
from dataclasses import replace
import unittest

import numpy as np

from grasp_a.contracts import Pose
from grasp_b.models import Shape
from grasp_b.multifamily import orientation
from grasp_b.wrench_v2.adapter import GeneratorInfo, SIMULTANEOUS_TOLERANCE_M, build_contact_spec
from grasp_b.wrench_v2.contacts import ContactStatus
from grasp_b.wrench_v2.diagnostics import (
    diagnose_candidate, normal_angle_deg, opening_diagnostics, rim_contact_diagnostics,
    rim_diagnostics, wall_pair_diagnostics,
)
from grasp_b.wrench_v2.geometry import ObjectGeometry, Primitive, TOLERANCE_M, quaternion_matrix
from tests import test_b_wrench_adapter as fixtures


class GeometryDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.StaticAdapterTests.setUpClass()
        cls.fixture = fixtures.StaticAdapterTests()

    def test_two_top_invalids_are_real_declared_mesh_point_conflicts(self):
        f = self.fixture
        ids = ("B3_7b21f3780e5faf1f", "B3_9910b02da1871f5b")
        for cid in ids:
            candidate = next(c for c in f.candidates if c.id == cid)
            diag = diagnose_candidate(candidate, f.obj, f.gripper, generator_info=f.info(candidate))
            self.assertEqual(diag["status"], "invalid")
            opening = diag["opening"]
            self.assertEqual(opening["classification"], "declared_geometry_conflict")
            self.assertTrue(opening["penetrations"])
            for p in opening["penetrations"]:
                self.assertEqual(p["primitive_id"], "handle:box:33")
                self.assertTrue(p["inward_mesh_covered"])
                self.assertLess(p["transform_roundtrip_error_m"], 1e-12)
                self.assertGreater(p["point_interior_depth_m"], TOLERANCE_M)
            minimum = min(p["point_interior_depth_m"] for p in opening["penetrations"])
            self.assertAlmostEqual(minimum, .00076025, places=9)

    def test_both_finger_transforms_map_scaled_mesh_to_tool_and_mug(self):
        f = self.fixture
        candidate = f.candidate("top")
        diag = opening_diagnostics(candidate, f.obj, f.gripper)
        mug_tool = np.eye(4)
        mug_tool[:3, :3] = quaternion_matrix(candidate.grasp_pose.quaternion)
        mug_tool[:3, 3] = candidate.grasp_pose.position
        for name, values in diag["finger_transforms"].items():
            tool_mesh = np.array(values["mesh_to_tool"])
            mug_mesh = np.array(values["mesh_to_mug"])
            np.testing.assert_allclose(mug_tool@tool_mesh, mug_mesh, atol=1e-12)
            self.assertAlmostEqual(tool_mesh[1, 3], (1 if name == "left" else -1)*candidate.gripper_width/2)
            self.assertAlmostEqual(tool_mesh[2, 3], -.0466)
        self.assertAlmostEqual(diag["finger_transforms"]["right"]["mesh_to_tool"][0][0], -1.)

    def test_rim_neighbors_connect_but_have_discontinuous_actual_normals(self):
        f = self.fixture
        spec = f.build(f.candidate("rim"))
        diag = rim_diagnostics(spec, f.obj)
        pair = diag["left_right_pairs"][0]
        wall = pair["wall_pair"]
        self.assertEqual(wall["primitive_ids"], ["body:box:1", "body:box:32"])
        self.assertTrue(wall["adjacent"])
        self.assertTrue(wall["material_connected"])
        self.assertTrue(wall["outer_faces_meet"])
        self.assertTrue(wall["inner_faces_meet"])
        self.assertFalse(wall["single_planar_wall_patch"])
        self.assertFalse(wall["normals_continuous"])
        self.assertAlmostEqual(wall["normal_angle_deg"], 11.25, places=5)
        self.assertAlmostEqual(pair["opposing_normal_angle_deg"], 168.75, places=5)
        self.assertAlmostEqual(pair["distance_m"], .00654394121, places=10)
        self.assertTrue(diag["resolved_piecewise_planar_contacts"])
        self.assertTrue(all(not c["on_facet_seam"] for c in diag["contacts"]))
        self.assertTrue(all(diag["sampled_footprint_crosses_segments"].values()))

    def test_rim_correction_never_smooths_normal_vectors(self):
        f = self.fixture
        for candidate in (c for c in f.candidates if f.by_id[c.id]["family"] == "rim"):
            spec = f.build(candidate)
            if spec.status is ContactStatus.INVALID:
                continue
            self.assertIs(spec.status, ContactStatus.APPROXIMATE)
            for contact in spec.contacts:
                primitive = f.obj.primitive(contact.surface_id.rsplit(":", 1)[0])
                from grasp_b.models import rotation
                sign = 1 if contact.surface_id.endswith("x+") else -1
                np.testing.assert_array_equal(contact.normal_outward, sign*rotation(primitive.shape.rpy)[:, 0])
            self.assertLess(normal_angle_deg(spec.contacts[0].normal_outward, spec.contacts[1].normal_outward), 179.)

    def test_seam_point_with_nonunique_normal_is_not_resolved(self):
        f = self.fixture
        spec = f.build(f.candidate("rim"))
        pair = rim_diagnostics(spec, f.obj)["left_right_pairs"][0]["wall_pair"]
        seam_point = tuple(pair["outer_meeting_m"])
        contacts = (replace(spec.contacts[0], position_m=seam_point), *spec.contacts[1:])
        diag = rim_contact_diagnostics(contacts, f.obj)
        self.assertTrue(diag["contacts"][0]["on_facet_seam"])
        self.assertFalse(diag["resolved_piecewise_planar_contacts"])
        # A closing ray through this exact union seam must retain UNKNOWN.
        direction = np.array((*seam_point[:2], 0.))
        direction /= np.linalg.norm(direction)
        center = tuple(direction*.0335+np.array((0., 0., .033)))
        q = orientation((0., 0., -1.), direction)
        candidate = replace(f.candidate("rim"), id="rim_seam_fixture", grasp_pose=Pose(center, q),
                            pregrasp_pose=Pose(tuple(np.array(center)+(0., 0., .1)), q))
        result = build_contact_spec(candidate, f.obj, f.center_gripper(),
            generator_info=GeneratorInfo("fixture", "test", "rim", candidate.id))
        self.assertIs(result.status, ContactStatus.UNKNOWN)
        self.assertFalse(result.contacts)

    def test_consecutive_indices_alone_do_not_establish_connectivity(self):
        a = Primitive("body:box:0", Shape("box", (.03, 0., 0.), (0., 0., 0.), (.003, .007, .09), "body"))
        b = Primitive("body:box:1", replace(a.shape, position=(.03, .1, 0.)))
        obj = ObjectGeometry("fixture", (a, b), (0., 0., 0.), "a"*64, "fixture")
        result = wall_pair_diagnostics(obj, a.primitive_id, b.primitive_id)
        self.assertEqual(result["circular_index_distance"], 1)
        self.assertFalse(result["adjacent"])
        self.assertFalse(result["material_connected"])
        self.assertFalse(result["single_planar_wall_patch"])

    def test_coplanar_connected_faces_have_normal_continuity(self):
        a = Primitive("body:box:0", Shape("box", (.0335, 0., 0.), (0., 0., 0.),
                                          (.0035, .007, .09), "body"))
        b = Primitive("body:box:1", replace(a.shape, position=(.0335, .007, 0.)))
        obj = ObjectGeometry("fixture", (a, b), (0., 0., 0.), "a"*64, "fixture")
        result = wall_pair_diagnostics(obj, a.primitive_id, b.primitive_id)
        self.assertTrue(result["material_connected"])
        self.assertTrue(result["normals_continuous"])
        self.assertTrue(result["single_planar_wall_patch"])
        self.assertAlmostEqual(result["normal_angle_deg"], 0.)

    def test_tolerances_unchanged_and_top_invalid_not_promoted(self):
        self.assertEqual(TOLERANCE_M, 1e-7)
        self.assertEqual(SIMULTANEOUS_TOLERANCE_M, 1e-6)
        candidate = next(c for c in self.fixture.candidates if c.id == "B3_7b21f3780e5faf1f")
        for reference in (True, False):
            spec = build_contact_spec(candidate, self.fixture.obj, self.fixture.gripper,
                                      generator_info=self.fixture.info(candidate), reference=reference)
            self.assertIs(spec.status, ContactStatus.INVALID)

    def test_invalid_pose_has_no_invented_opening_transforms(self):
        f = self.fixture
        candidate = f.candidate("top")
        bad = replace(candidate, grasp_pose=replace(candidate.grasp_pose, quaternion=(0., 0., 0., 0.)))
        diag = diagnose_candidate(bad, f.obj, f.gripper, generator_info=f.info(bad))
        self.assertEqual(diag["status"], "invalid")
        self.assertEqual(diag["opening"]["classification"], "insufficient_information")
        self.assertFalse(diag["opening"]["finger_transforms"])


if __name__ == "__main__":
    unittest.main()
