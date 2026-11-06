"""Static primitive and actual Panda OBJ fixtures; no Bullet world is created."""
from dataclasses import replace
import math
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import pybullet_data

from grasp_b.models import Shape, read_model, rotation
from grasp_b.wrench_v2.geometry import (
    GeometryError, ObjectGeometry, Primitive, contains, intersect_line, on_surface,
    quaternion_matrix, read_gripper_geometry, union_intersections,
)

PANDA = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"


class PrimitiveGeometryTests(unittest.TestCase):
    def setUp(self):
        self.box = Primitive("handle:box:0", Shape("box", (0., .086, 0.), (0., 0., 0.),
                                                  (.012, .012, .069), "handle"))
        self.cylinder = Primitive("body:cylinder:0", Shape("cylinder", (0., 0., 0.),
                                                          (0., 0., 0.), (.035, .09), "body"))

    def test_box_contact_distance_and_outward_normals(self):
        hits = intersect_line(self.box, (0., .086, 0.), (1., 0., 0.))
        self.assertEqual(len(hits), 2)
        self.assertAlmostEqual(hits[1].t_m-hits[0].t_m, .012)
        self.assertEqual(hits[0].normal_outward, (-1., 0., 0.))
        self.assertEqual(hits[1].normal_outward, (1., 0., 0.))
        for h in hits:
            self.assertTrue(on_surface(self.box, h.position_m, h.normal_outward, h.surface_id))

    def test_rotated_box_frame_transform_and_surface_extent(self):
        primitive = Primitive("body:box:1", replace(self.box.shape, position=(.02, -.03, .01),
                                                   rpy=(0., 0., math.pi/4)))
        r = rotation(primitive.shape.rpy)
        direction = tuple(r[:, 0])
        hits = intersect_line(primitive, primitive.shape.position, direction)
        self.assertAlmostEqual(hits[1].t_m, .006)
        np.testing.assert_allclose(hits[1].normal_outward, direction, atol=1e-12)
        np.testing.assert_allclose(hits[1].position_m,
                                   np.array(primitive.shape.position)+.006*r[:, 0], atol=1e-12)

    def test_points_outside_face_and_inward_normals_are_rejected(self):
        hit = intersect_line(self.box, self.box.shape.position, (1., 0., 0.))[1]
        self.assertFalse(on_surface(self.box, (.006, .2, 0.), hit.normal_outward, hit.surface_id))
        self.assertFalse(on_surface(self.box, hit.position_m, (-1., 0., 0.), hit.surface_id))
        self.assertFalse(on_surface(self.box, (0., .086, 0.), hit.normal_outward, hit.surface_id))
        self.assertFalse(on_surface(self.box, hit.position_m, hit.normal_outward, "another:x+"))

    def test_cylinder_side_and_caps(self):
        hits = intersect_line(self.cylinder, (0., 0., 0.), (1., 0., 0.))
        self.assertAlmostEqual(hits[0].position_m[0], -.035)
        self.assertAlmostEqual(hits[1].position_m[0], .035)
        self.assertTrue(on_surface(self.cylinder, hits[1].position_m, hits[1].normal_outward,
                                   hits[1].surface_id))
        caps = intersect_line(self.cylinder, (0., 0., 0.), (0., 0., 1.))
        self.assertEqual(len(caps), 2)
        self.assertAlmostEqual(caps[1].t_m, .045)

    def test_degenerate_edge_tangent_and_face_coincidence_are_flagged(self):
        diagonal = (math.sqrt(.5), math.sqrt(.5), 0.)
        self.assertTrue(all(h.ambiguous for h in intersect_line(self.box, self.box.shape.position, diagonal)))
        tangent = intersect_line(self.cylinder, (-1., .035, 0.), (1., 0., 0.))
        self.assertTrue(tangent)
        self.assertTrue(all(h.ambiguous for h in tangent))
        face = intersect_line(self.box, (0., .092, 0.), (1., 0., 0.))
        self.assertTrue(all(h.ambiguous for h in face))

    def test_line_misses_or_points_outside_finite_height(self):
        self.assertEqual(intersect_line(self.box, (0., .3, 0.), (1., 0., 0.)), ())
        self.assertEqual(intersect_line(self.cylinder, (0., 0., .1), (1., 0., 0.)), ())
        self.assertFalse(contains(self.cylinder, (0., 0., .1)))

    def test_direction_and_quaternion_not_implicitly_repaired(self):
        for direction in ((2., 0., 0.), (0., 0., 0.), (float("nan"), 0., 0.)):
            with self.subTest(direction=direction), self.assertRaises(GeometryError):
                intersect_line(self.box, (0., .086, 0.), direction)
        for q in ((0., 0., 0., 2.), (0., 0., 0., 0.), (float("inf"), 0., 0., 1.)):
            with self.subTest(q=q), self.assertRaises(GeometryError):
                quaternion_matrix(q)
        for q in (np.array(1.), (True, 0., 0., 0.), ("0", "0", "0", "1")):
            with self.subTest(q=q), self.assertRaises(GeometryError):
                quaternion_matrix(q)

    def test_overlapping_and_touching_union_internal_faces_are_discarded(self):
        for distance in (.005, .01):
            a = Primitive("body:box:0", Shape("box", (-distance, 0., 0.), (0., 0., 0.),
                                              (.02, .02, .02), "body"))
            b = Primitive("body:box:1", replace(a.shape, position=(distance, 0., 0.)))
            obj = ObjectGeometry("synthetic", (a, b), (0., 0., 0.), "a"*64, "fixture")
            hits = union_intersections(obj, (0., 0., 0.), (1., 0., 0.))
            self.assertEqual(len(hits), 2)
            self.assertAlmostEqual(hits[1].t_m, distance+.01)


class AssetGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = read_model("hollow", PANDA)
        cls.obj = ObjectGeometry.from_model(cls.model)
        cls.gripper = read_gripper_geometry(PANDA)

    def test_actual_hollow_walls_and_handle_boxes_are_retained(self):
        self.assertEqual(len([p for p in self.obj.primitives if p.shape.kind == "box" and
                              p.shape.region == "body"]), 32)
        self.assertEqual(len([p for p in self.obj.primitives if p.shape.region == "handle"]), 3)
        self.assertEqual(self.obj.asset_sha256, self.model.asset_sha256)

    def test_hollow_facet_normal_differs_from_ideal_cylinder(self):
        direction = (math.cos(math.radians(5)), math.sin(math.radians(5)), 0.)
        hits = union_intersections(self.obj, (0., 0., 0.), direction)
        outer = max((h for h in hits if h.region == "body"), key=lambda h: h.t_m)
        self.assertAlmostEqual(outer.position_m[0], .03525, places=7)
        self.assertEqual(outer.normal_outward, (1., 0., 0.))
        self.assertLess(float(np.dot(direction, outer.normal_outward)), .999)
        self.assertNotAlmostEqual(outer.t_m, self.model.geometry.radius, places=6)

    def test_same_wall_has_distinct_inner_and_outer_faces(self):
        hits = union_intersections(self.obj, (.0335, 0., .033), (1., 0., 0.))
        nearest = sorted(hits, key=lambda h: abs(h.t_m))[:2]
        self.assertEqual(nearest[0].primitive_id, nearest[1].primitive_id)
        self.assertAlmostEqual(abs(nearest[0].t_m-nearest[1].t_m), .0035)
        self.assertAlmostEqual(float(np.dot(nearest[0].normal_outward, nearest[1].normal_outward)), -1.)

    def test_panda_finger_root_and_right_mesh_rotation_are_used(self):
        left, right = (self.gripper.finger(name) for name in ("left", "right"))
        self.assertAlmostEqual(left.joint_origin_tool_m[2], .0584-.105)
        self.assertAlmostEqual(right.collision_rpy[2], math.pi)
        self.assertAlmostEqual(self.gripper.max_opening_m, .08)
        self.assertAlmostEqual(left.inner_face(0., 0.), -right.inner_face(0., 0.), places=10)
        self.assertEqual(len(self.gripper.pad_samples), 9)

    def test_finger_mesh_coverage_is_not_its_full_aabb_or_tcp_plane(self):
        finger = self.gripper.finger("left")
        self.assertIsNotNone(finger.inner_face(0., 0.))
        self.assertNotEqual(finger.inner_face(0., 0.), 0.)
        self.assertIsNone(finger.inner_face(.02, 0.))
        self.assertIsNone(finger.inner_face(0., .02))
        # This root-end point is inside the whole-mesh AABB, but not on the
        # declared near-flat inward face at that x/z.
        self.assertIsNone(finger.inner_face(.0104, 0.))

    def test_object_hash_and_profile_mismatch_rejected(self):
        with self.assertRaisesRegex(GeometryError, "hash"):
            ObjectGeometry.from_model(replace(self.model, asset_sha256="0"*64))
        with self.assertRaisesRegex(GeometryError, "profile"):
            ObjectGeometry.from_model(replace(self.model, version="another-model"))

    def test_unsupported_finger_axis_and_tcp_parent_are_not_silently_assumed(self):
        for target, attribute, value in (
            ("joint[@name='panda_finger_joint1']/axis", "xyz", "1 0 0"),
            ("joint[@name='panda_grasptarget_hand']/parent", "link", "another_link"),
        ):
            tree = ET.parse(PANDA)
            tree.getroot().find(target).set(attribute, value)
            with self.subTest(target=target), patch(
                    "grasp_b.wrench_v2.geometry.ET.parse", return_value=tree):
                with self.assertRaises(GeometryError):
                    read_gripper_geometry(PANDA)


if __name__ == "__main__":
    unittest.main()
