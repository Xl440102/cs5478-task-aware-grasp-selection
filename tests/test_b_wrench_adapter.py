"""Actual main generator candidates and static-only adapter diagnostics."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest

import numpy as np
import pybullet_data

from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_b.models import read_model
from grasp_b.multifamily import MultiFamilyConfig, VERSION, generate_multifamily, orientation
from grasp_b.wrench_v2.adapter import GeneratorInfo, audit_candidates, build_contact_spec
from grasp_b.wrench_v2.contacts import ContactMechanism, ContactSpec, ContactStatus, FingerID, GraspFamily
from grasp_b.wrench_v2.geometry import ObjectGeometry, on_surface, quaternion_matrix, read_gripper_geometry

PANDA = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"


class StaticAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = read_model("hollow", PANDA)
        cls.obj = ObjectGeometry.from_model(cls.model)
        cls.gripper = read_gripper_geometry(PANDA)
        config = MultiFamilyConfig(
            side_yaw_step_deg=90, side_pitches_deg=(0,), side_height_fractions=(.5,), side_rolls_deg=(0,),
            top_depths_m=(.015,), top_yaw_step_deg=90, rim_azimuth_step_deg=90,
            handle_yaws_deg=(270,), handle_pitches_deg=(0,), handle_heights_m=(0,),
            handle_rolls_deg=(0,), handle_top_y_fractions=(.5,), max_candidates=16)
        cls.candidates, cls.records = generate_multifamily(cls.model, config)
        cls.by_id = {r["id"]: r for r in cls.records}

    def candidate(self, family):
        return next(c for c in self.candidates if self.by_id[c.id]["family"] == family)

    def info(self, candidate, **changes):
        values = dict(name="grasp_b.multifamily", version=VERSION,
                      family=self.by_id[candidate.id]["family"], candidate_id=candidate.id)
        values.update(changes)
        return GeneratorInfo(**values)

    def build(self, candidate, gripper=None, **info_changes):
        return build_contact_spec(candidate, self.obj, gripper or self.gripper,
                                  generator_info=self.info(candidate, **info_changes))

    def center_gripper(self):
        return replace(self.gripper, pad_samples=tuple(s for s in self.gripper.pad_samples if s[:2] == (0., 0.)))

    def fixture(self, center, approach, closing, width, cid="static_fixture"):
        q = orientation(approach, closing)
        pre = tuple(np.array(center)-.08*np.array(approach))
        return Candidate(cid, "handle", "text is not used", width, 0., Pose(center, q), Pose(pre, q))

    def test_body_first_contacts_have_left_right_outward_surfaces(self):
        candidate = self.candidate("side")
        spec = self.build(candidate)
        self.assertIs(spec.status, ContactStatus.APPROXIMATE)
        self.assertIs(spec.family, GraspFamily.BODY)
        self.assertIs(spec.mechanism, ContactMechanism.BODY_SIDE)
        self.assertEqual({p.finger_id for p in spec.contacts}, {FingerID.LEFT, FingerID.RIGHT})
        for point in spec.contacts:
            primitive_id = point.surface_id.rsplit(":", 1)[0]
            self.assertTrue(on_surface(self.obj.primitive(primitive_id), point.position_m,
                                       point.normal_outward, point.surface_id))
            sign = 1 if point.finger_id is FingerID.LEFT else -1
            self.assertGreater(sign*float(np.dot(point.normal_outward, spec.closing_direction)), 0.)

    def test_top_down_is_sidewall_contact_not_top_cap(self):
        candidate = self.candidate("top")
        spec = self.build(candidate)
        self.assertIs(spec.status, ContactStatus.APPROXIMATE)
        self.assertIs(spec.mechanism, ContactMechanism.BODY_TOP_DOWN)
        self.assertAlmostEqual(spec.approach_direction[2], -1.)
        self.assertTrue(all(abs(p.normal_outward[2]) < 1e-8 for p in spec.contacts))

    def test_main_handle_side_and_top_mechanisms(self):
        handles = [c for c in self.candidates if self.by_id[c.id]["family"] == "handle"]
        specs = [self.build(c) for c in handles]
        self.assertEqual({s.mechanism for s in specs}, {ContactMechanism.HANDLE_SIDE,
                                                      ContactMechanism.HANDLE_TOP_DOWN})
        for spec in specs:
            self.assertIs(spec.status, ContactStatus.APPROXIMATE)
            self.assertAlmostEqual(spec.provenance["central_surface_separation_m"], .012)
            self.assertNotEqual(spec.provenance["central_surface_separation_m"], .035)

    def test_explicit_handle_bar_pinch_preserves_vertical_closing_axis(self):
        candidate = self.fixture((0., .06, .029), (1., 0., 0.), (0., 0., 1.), .014)
        spec = build_contact_spec(candidate, self.obj, self.gripper, generator_info=GeneratorInfo(
            "static_fixture", "test_only", "handle", candidate.id, mechanism=ContactMechanism.HANDLE_BAR_PINCH))
        self.assertIs(spec.status, ContactStatus.APPROXIMATE)
        self.assertIs(spec.mechanism, ContactMechanism.HANDLE_BAR_PINCH)
        np.testing.assert_allclose(spec.closing_direction, (0., 0., 1.), atol=1e-12)
        self.assertAlmostEqual(spec.provenance["central_surface_separation_m"], .012)
        no_hint = build_contact_spec(candidate, self.obj, self.gripper,
                                    generator_info=GeneratorInfo("fixture", "test", "handle", candidate.id))
        self.assertIs(no_hint.status, ContactStatus.UNSUPPORTED)

    def test_rim_center_ray_inner_outer_normals_and_same_wall(self):
        spec = self.build(self.candidate("rim"), self.center_gripper())
        self.assertIs(spec.status, ContactStatus.APPROXIMATE)
        self.assertEqual(len(spec.contacts), 2)
        self.assertAlmostEqual(spec.provenance["central_surface_separation_m"], .0035)
        self.assertAlmostEqual(float(np.dot(spec.contacts[0].normal_outward, spec.contacts[1].normal_outward)), -1.)
        self.assertEqual(spec.contacts[0].surface_id.rsplit(":", 1)[0],
                         spec.contacts[1].surface_id.rsplit(":", 1)[0])

    def test_rim_full_sampled_footprint_retains_actual_multifacet_normals(self):
        spec = self.build(self.candidate("rim"))
        self.assertIs(spec.status, ContactStatus.APPROXIMATE)
        self.assertIn("rim_unique_contacts_on_connected_piecewise_planar_walls", spec.reasons)
        self.assertTrue(spec.provenance["rim_continuity"]["resolved_piecewise_planar_contacts"])
        self.assertTrue(spec.contacts)  # separate normals, not a smooth contact patch

    def test_multicontact_samples_share_capacity_and_only_first_contacts_survive(self):
        spec = self.build(self.candidate("side"))
        self.assertGreater(len(spec.contacts), 2)
        self.assertEqual(len(spec.capacity_groups), 2)
        for point in spec.contacts:
            self.assertEqual(point.capacity_group_id, point.finger_id.value)
            self.assertIsNone(point.parameters.friction_coefficient)
            self.assertIsNone(spec.capacity_for_finger(point.finger_id).normal_force_budget_n)
        self.assertLess(len(spec.contacts), 2*len(self.gripper.pad_samples))

    def test_mug_local_mesh_contact_consistency_including_root_offset(self):
        candidate = self.candidate("top")
        spec = self.build(candidate)
        r = quaternion_matrix(candidate.grasp_pose.quaternion)
        for point in spec.contacts:
            local = r.T@(np.array(point.position_m)-candidate.grasp_pose.position)
            name = point.finger_id.value
            q = spec.provenance["predicted_finger_joint_positions_m"][name]
            y = self.gripper.finger(name).inner_face(local[0], local[2])
            self.assertIsNotNone(y)
            self.assertAlmostEqual(local[1], (q if name == "left" else -q)+y, places=7)

    def test_pose_closing_contact_schema_statuses_are_separate(self):
        candidate = self.candidate("side")
        bad = replace(candidate, grasp_pose=Pose(candidate.grasp_pose.position, (0., 0., 0., 0.)))
        result = self.build(bad)
        self.assertIs(result.status, ContactStatus.INVALID)
        self.assertEqual(result.provenance["validation"]["candidate_pose"], "invalid")
        self.assertEqual(result.provenance["validation"]["closing_geometry"], "not_checked")
        self.assertEqual(result.provenance["validation"]["contactspec_schema"], "valid")
        self.assertEqual(ContactSpec.from_dict(result.to_dict()), result)

    def test_unsupported_body_closing_axis_has_no_invented_contacts(self):
        base = self.candidate("side")
        q = orientation((1., 0., 0.), (0., 0., 1.))
        candidate = replace(base, grasp_pose=Pose((0., 0., 0.), q), pregrasp_pose=Pose((-.1, 0., 0.), q))
        result = self.build(candidate)
        self.assertIs(result.status, ContactStatus.UNSUPPORTED)
        self.assertFalse(result.contacts)
        self.assertEqual(result.provenance["validation"]["candidate_pose"], "valid")

    def test_small_opening_and_absent_surface_are_invalid(self):
        base = self.candidate("side")
        self.assertIs(self.build(replace(base, gripper_width=.01)).status, ContactStatus.INVALID)
        shift = np.array((.2, 0., 0.))
        moved = replace(base, grasp_pose=replace(base.grasp_pose, position=tuple(np.array(base.grasp_pose.position)+shift)),
                        pregrasp_pose=replace(base.pregrasp_pose, position=tuple(np.array(base.pregrasp_pose.position)+shift)))
        self.assertIs(self.build(moved).status, ContactStatus.INVALID)

    def test_ambiguous_handle_corner_is_unknown_without_unique_normal(self):
        d = np.array((1., 1., 0.))/np.sqrt(2.)
        candidate = self.fixture((0., .086, 0.), (-d[1], d[0], 0.), tuple(d), .035)
        result = build_contact_spec(candidate, self.obj, self.center_gripper(),
                                   generator_info=GeneratorInfo("fixture", "test", "handle", candidate.id))
        self.assertIs(result.status, ContactStatus.UNKNOWN)
        self.assertIn("ambiguous_edge_seam_or_tangent", result.reasons)
        self.assertFalse(result.contacts)
        self.assertFalse(result.provenance["failure_details"]["unique_normal"])

    def test_initial_penetration_reports_primitive_and_finger_sample(self):
        candidate = next(c for c in self.candidates if self.by_id[c.id]["family"] == "top" and
                         abs(quaternion_matrix(c.grasp_pose.quaternion)[1, 1]) > .99)
        spec = self.build(candidate)
        self.assertIs(spec.status, ContactStatus.INVALID)
        details = spec.provenance["failure_details"]
        self.assertIn(details["finger"], ("left", "right"))
        self.assertTrue(details["penetrating_primitives"])
        self.assertEqual(len(details["position_m"]), 3)

    def test_solid_mug_rim_is_unsupported(self):
        candidate = self.candidate("rim")
        solid = ObjectGeometry.from_model(read_model("solid", PANDA))
        result = build_contact_spec(candidate, solid, self.gripper, generator_info=self.info(candidate))
        self.assertIs(result.status, ContactStatus.UNSUPPORTED)
        self.assertFalse(result.contacts)

    def test_solid_body_uses_declared_cylinder_not_hollow_wall_faces(self):
        model = read_model("solid", PANDA)
        candidates, _ = generate_multifamily(model, MultiFamilyConfig(
            families=("side",), side_yaw_step_deg=90, side_pitches_deg=(0,),
            side_height_fractions=(.5,), side_rolls_deg=(0,)))
        # Choose the centered height and an approach away from handle overlap.
        candidate = next(c for c in candidates if abs(c.grasp_pose.position[2]) < 1e-8 and
                         abs(quaternion_matrix(c.grasp_pose.quaternion)[1, 1]) > .99)
        spec = build_contact_spec(candidate, ObjectGeometry.from_model(model), self.gripper,
                                  generator_info=GeneratorInfo("grasp_b.multifamily", VERSION, "side", candidate.id))
        self.assertIs(spec.status, ContactStatus.APPROXIMATE)
        self.assertTrue(all("cylinder" in p.surface_id for p in spec.contacts))
        self.assertAlmostEqual(spec.provenance["central_surface_separation_m"], .07)

    def test_identity_checks_and_hash_match_unchanged_candidate(self):
        candidate = self.candidate("side")
        self.assertEqual(self.build(candidate).candidate_hash, candidate_sha256([candidate]))
        with self.assertRaisesRegex(ValueError, "ID"):
            self.build(candidate, candidate_id="wrong")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.build(candidate, candidate_hash="0"*64)

    def test_approach_prose_is_not_used_to_infer_mechanism_or_parameters(self):
        candidate = self.candidate("handle")
        before = self.build(candidate)
        after = self.build(replace(candidate, approach="top soft_finger friction=999 outcome=success"))
        self.assertEqual(before.contacts, after.contacts)
        self.assertEqual(before.mechanism, after.mechanism)
        self.assertNotEqual(before.candidate_hash, after.candidate_hash)

    def test_invalid_pregrasp_is_not_repaired(self):
        candidate = self.candidate("side")
        bad = replace(candidate, pregrasp_pose=replace(candidate.pregrasp_pose, position=(0., 0., -.1)))
        self.assertIs(self.build(bad).status, ContactStatus.INVALID)

    def test_reproducible_geometry_json_and_provenance(self):
        candidate = self.candidate("side")
        first, second = self.build(candidate), self.build(candidate)
        self.assertEqual(first, second)
        self.assertEqual(ContactSpec.from_dict(json.loads(json.dumps(first.to_dict(), allow_nan=False))), first)
        self.assertEqual(first.provenance["object_asset_sha256"], self.model.asset_sha256)
        self.assertFalse(first.provenance["observed_execution_used"])

    def test_real_generator_small_smoke_and_timing(self):
        result = audit_candidates(self.candidates, self.obj, self.gripper, records=self.records,
                                  generator_name="grasp_b.multifamily", generator_version=VERSION)
        self.assertEqual(len(result["candidates"]), 14)
        self.assertEqual(set(result["by_family"]), {"side", "top", "rim", "handle"})
        self.assertEqual(sum(sum(row.values()) for row in result["by_family"].values()), 14)
        self.assertEqual(result["geometry_exception_count"], 0)
        self.assertFalse(result["duplicate_candidate_ids"])
        self.assertEqual(len(result["specs"]), 14)
        self.assertTrue(all(row["seconds"] >= 0 for row in result["candidates"]))

    def test_audit_rejects_duplicate_ids_and_misaligned_records(self):
        candidate = self.candidate("side")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit_candidates([candidate, candidate], self.obj, self.gripper, records=self.records,
                             generator_name="test", generator_version="test")
        with self.assertRaisesRegex(ValueError, "align"):
            audit_candidates([candidate], self.obj, self.gripper, records=[],
                             generator_name="test", generator_version="test")

    def test_audit_retains_bad_numeric_payload_as_error_not_fake_identity(self):
        candidate = self.candidate("side")
        bad = replace(candidate, gripper_width=float("nan"))
        result = audit_candidates([bad], self.obj, self.gripper, records=[self.by_id[candidate.id]],
                                  generator_name="test", generator_version="test")
        self.assertEqual(result["geometry_exception_count"], 1)
        self.assertEqual(result["candidates"][0]["status"], "error")
        self.assertFalse(result["specs"])

    def test_audit_forwards_explicit_mechanism_and_expected_fingerprint(self):
        candidate = self.fixture((0., .06, .029), (1., 0., 0.), (0., 0., 1.), .014)
        record = dict(id=candidate.id, family="handle", mechanism="handle_bar_pinch",
                      candidate_hash=candidate_sha256([candidate]))
        result = audit_candidates([candidate], self.obj, self.gripper, records=[record],
                                  generator_name="fixture", generator_version="test")
        self.assertEqual(result["geometry_exception_count"], 0)
        self.assertIs(result["specs"][0].mechanism, ContactMechanism.HANDLE_BAR_PINCH)
        record["candidate_hash"] = "0"*64
        result = audit_candidates([candidate], self.obj, self.gripper, records=[record],
                                  generator_name="fixture", generator_version="test")
        self.assertEqual(result["geometry_exception_count"], 1)
        self.assertFalse(result["specs"])

    def test_adapter_never_imports_simulation_runtime(self):
        script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] == 'pybullet' or name.startswith(('grasp_a.simulation', 'grasp_a.sequence', 'grasp_c')):
        raise AssertionError('simulation or C import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from grasp_b.wrench_v2.adapter import build_contact_spec
'''
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
