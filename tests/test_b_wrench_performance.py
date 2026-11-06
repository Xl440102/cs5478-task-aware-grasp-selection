"""Cache invalidation and scalar/optimized full-pool equivalence tests."""
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

from grasp_b.multifamily import MultiFamilyConfig, VERSION, generate_multifamily
from grasp_b.wrench_v2.adapter import audit_candidates, build_contact_spec
from grasp_b.wrench_v2.geometry import (
    clear_geometry_caches, containing_primitives, geometry_cache_info, prepare_finger,
    read_gripper_geometry, union_intersections,
)
from grasp_b.wrench_v2.profiling import benchmark_geometry
from tests import test_b_wrench_adapter as fixtures

PANDA = fixtures.PANDA


class GeometryPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.StaticAdapterTests.setUpClass()
        cls.fixture = fixtures.StaticAdapterTests()

    def assert_records_equal(self, a, b, path=""):
        if isinstance(a, float):
            self.assertAlmostEqual(a, b, delta=1e-12, msg=path)
        elif isinstance(a, dict):
            self.assertEqual(a.keys(), b.keys(), path)
            for key in a:
                self.assert_records_equal(a[key], b[key], path+"/"+key)
        elif isinstance(a, list):
            self.assertEqual(len(a), len(b), path)
            for i, (x, y) in enumerate(zip(a, b)):
                self.assert_records_equal(x, y, path+f"/{i}")
        else:
            self.assertEqual(a, b, path)

    def test_full_208_pool_reference_and_optimized_specs_match(self):
        f = self.fixture
        candidates, records = generate_multifamily(f.model, MultiFamilyConfig())
        options = dict(records=records, generator_name="grasp_b.multifamily", generator_version=VERSION)
        reference = audit_candidates(candidates, f.obj, f.gripper, reference=True, **options)
        optimized = audit_candidates(candidates, f.obj, f.gripper, **options)
        self.assertEqual(len(reference["specs"]), 208)
        self.assertEqual(reference["by_family"], optimized["by_family"])
        self.assertEqual(reference["geometry_exception_count"], 0)
        self.assertEqual(optimized["geometry_exception_count"], 0)
        for a, b in zip(reference["specs"], optimized["specs"]):
            self.assert_records_equal(a.to_dict(), b.to_dict(), a.candidate_id)

    def test_different_candidate_poses_never_share_contact_results(self):
        f = self.fixture
        base = f.candidate("side")
        shift = np.array((0., 0., .002))
        moved = replace(base, grasp_pose=replace(base.grasp_pose, position=tuple(np.array(base.grasp_pose.position)+shift)),
                        pregrasp_pose=replace(base.pregrasp_pose, position=tuple(np.array(base.pregrasp_pose.position)+shift)))
        a, b = f.build(base), f.build(moved)
        self.assertNotEqual(a.candidate_hash, b.candidate_hash)
        self.assertEqual(len(a.contacts), len(b.contacts))
        for pa, pb in zip(a.contacts, b.contacts):
            np.testing.assert_allclose(np.array(pb.position_m)-pa.position_m, shift, atol=1e-12)

    def test_file_content_changes_invalidate_asset_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root/"panda.urdf"
            shutil.copyfile(PANDA, path)
            meshdir = root/"meshes/collision"
            meshdir.mkdir(parents=True)
            for name in ("finger.obj", "hand.obj"):
                shutil.copyfile(PANDA.parent/"meshes/collision"/name, meshdir/name)
            clear_geometry_caches()
            a = read_gripper_geometry(path)
            self.assertIs(a, read_gripper_geometry(path))
            finger_path = meshdir/"finger.obj"
            finger_path.write_text(finger_path.read_text()+"\n# changed asset revision\n")
            b = read_gripper_geometry(path)
            self.assertIsNot(a, b)
            self.assertNotEqual(a.fingers[0].sha256, b.fingers[0].sha256)
            # Identical geometry but changed source bytes must not reuse identity.
            self.assertEqual(a.pad_samples, b.pad_samples)
            path.write_text(path.read_text()+"\n<!-- new URDF revision -->\n")
            c = read_gripper_geometry(path)
            self.assertNotEqual(c.panda_sha256, b.panda_sha256)

    def test_model_hash_version_and_shape_are_all_part_of_cache_identity(self):
        f = self.fixture
        clear_geometry_caches()
        point = [[0., 0., 0.]]
        containing_primitives(f.obj, point)
        initial = geometry_cache_info()["object_arrays"]["misses"]
        for obj in (replace(f.obj, asset_sha256="b"*64), replace(f.obj, model_version="another-version"),
                    replace(f.obj, primitives=(replace(f.obj.primitives[0],
                        shape=replace(f.obj.primitives[0].shape, position=(0., 0., .1))), *f.obj.primitives[1:]))):
            containing_primitives(obj, point)
        self.assertEqual(geometry_cache_info()["object_arrays"]["misses"], initial+3)

    def test_finger_transform_changes_do_not_reuse_old_precompute(self):
        left = self.fixture.gripper.finger("left")
        moved = replace(left, joint_origin_tool_m=tuple(np.array(left.joint_origin_tool_m)+(0., 0., .001)))
        self.assertIsNot(prepare_finger(left), prepare_finger(moved))
        self.assertAlmostEqual(left.inner_face(0., 0.), moved.inner_face(0., .001), places=12)
        for finger in self.fixture.gripper.fingers:
            for x, z, _, _ in self.fixture.gripper.pad_samples:
                self.assertAlmostEqual(finger.inner_face(x, z), finger.inner_face_reference(x, z), places=14)

    def test_cached_and_uncached_gripper_preprocessing_are_identical(self):
        self.assertEqual(read_gripper_geometry(PANDA), read_gripper_geometry(PANDA, reference=True))

    def test_vectorized_union_keeps_reference_seam_and_tangent_results(self):
        f = self.fixture
        for angle in (0., 5., 11.25, 45., 90., 180.):
            a = np.deg2rad(angle)
            direction = (np.cos(a), np.sin(a), 0.)
            reference = union_intersections(f.obj, (0., 0., .03), direction, reference=True)
            fast = union_intersections(f.obj, (0., 0., .03), direction)
            self.assertEqual([(h.surface_id, h.ambiguous) for h in reference],
                             [(h.surface_id, h.ambiguous) for h in fast])
            np.testing.assert_allclose([h.t_m for h in reference], [h.t_m for h in fast], atol=1e-12)

    def test_stage_timing_is_outside_spec_and_does_not_change_result(self):
        f = self.fixture
        candidate = f.candidate("side")
        times = {}
        before = f.build(candidate)
        after = build_contact_spec(candidate, f.obj, f.gripper, generator_info=f.info(candidate), timings=times)
        self.assertEqual(before, after)
        self.assertTrue(all(t >= 0 for t in times.values()))
        self.assertAlmostEqual(sum(v for k, v in times.items() if k != "total_s"), times["total_s"], delta=1e-8)
        with self.assertRaises(ValueError):
            benchmark_geometry(PANDA, runs=1)


if __name__ == "__main__":
    unittest.main()
