"""Analytic force-map fixtures and main-based static numerical integration.

Any mu supplied here is a numerical fixture assumption, not measured friction.
No LP, ConvexHull, grasp execution, quality score or force-closure solver.
"""
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pybullet_data

from grasp_b.models import read_model
from grasp_b.multifamily import VERSION, generate_multifamily, pruned_config
from grasp_b.wrench_v2.adapter import audit_candidates
from grasp_b.wrench_v2.contacts import (
    ContactModel, ContactParameters, ContactStatus, FingerCapacityGroup, FingerID,
)
from grasp_b.wrench_v2.geometry import ObjectGeometry, read_gripper_geometry
from grasp_b.wrench_v2.gws import (
    FrictionValue, NumericalInputError, UnknownFrictionError, UnusableContactSpecError,
    WrenchScaling, audit_wrench_primitives, build_primitive_wrenches, contact_frame,
    friction_cone, grasp_matrix, normalize_wrenches, point_grasp_matrix,
    rank_diagnostics, skew,
)
from tests import test_b_wrench_contacts as fixtures

FIXTURE_MU = FrictionValue(coefficient=.45, source="mu_assumed_for_numerical_fixture")


class GraspMatrixTests(unittest.TestCase):
    def test_single_contact_force_and_torque_units(self):
        g = point_grasp_matrix((.1, .2, .3), (.01, .02, .03))
        self.assertEqual(g.shape, (6, 3))
        np.testing.assert_allclose(g@(0., 0., 2.), (0., 0., 2., .36, -.18, 0.), atol=1e-14)
        diag = rank_diagnostics(g, matrix_name="single_point_force_map")
        self.assertEqual(diag.numerical_rank, 3)
        self.assertIsNone(diag.six_dimensional_condition)
        self.assertIn("fewer_than_six_columns", diag.degenerate_reasons)

    def test_skew_matches_cross_product_with_correct_sign(self):
        r, force = np.array((.02, -.03, .05)), np.array((2., -3., 4.))
        np.testing.assert_allclose(skew(r)@force, np.cross(r, force), atol=1e-15)

    def test_two_symmetric_points_have_analytic_rank_five(self):
        g = grasp_matrix(fixtures.spec())
        self.assertEqual(g.shape, (6, 6))
        diag = rank_diagnostics(g, matrix_name="G_mug_two_points")
        self.assertEqual(diag.numerical_rank, 5)
        self.assertAlmostEqual(diag.singular_values[-1], 0., delta=1e-15)
        np.testing.assert_array_equal(g[3], np.zeros(6))  # no pure axial torque

    def test_asymmetric_distinct_points_and_offset_reference_still_rank_five(self):
        a = fixtures.point(position_m=(.031, .02, -.04))
        b = fixtures.point(FingerID.RIGHT, position_m=(-.012, -.007, .03))
        spec = fixtures.spec(contacts=(a, b), reference_point_m=(.01, -.02, .003))
        self.assertEqual(rank_diagnostics(grasp_matrix(spec), matrix_name="asymmetric G").numerical_rank, 5)

    def test_reference_shift_applies_once(self):
        spec = fixtures.spec()
        shifted = replace(spec, reference_point_m=(.01, .02, -.03))
        force = np.array((1., 2., 3., -2., 1., .5))
        w, moved = grasp_matrix(spec)@force, grasp_matrix(shifted)@force
        np.testing.assert_allclose(moved[:3], w[:3], atol=1e-14)
        np.testing.assert_allclose(moved[3:], w[3:]+np.cross(
            np.array(spec.reference_point_m)-shifted.reference_point_m, w[:3]), atol=1e-14)

    def test_contact_local_and_mug_force_coordinates_are_equivalent(self):
        spec = fixtures.spec()
        local = np.array((1., -2., 3., .4, .5, 2.))
        mug = np.concatenate([contact_frame(p.normal_outward)@f
                              for p, f in zip(spec.contacts, local.reshape(-1, 3))])
        np.testing.assert_allclose(grasp_matrix(spec)@mug,
                                   grasp_matrix(spec, coordinates="contact")@local, atol=1e-14)

    def test_rotation_consistency_with_forces_reexpressed_in_rotated_frame(self):
        angle = .37
        q = np.array(((math.cos(angle), -math.sin(angle), 0.),
                      (math.sin(angle), math.cos(angle), 0.), (0., 0., 1.)))
        spec = fixtures.spec(reference_point_m=(.01, .02, .003))
        rotated = replace(spec, contacts=tuple(replace(p, position_m=tuple(q@p.position_m),
                        normal_outward=tuple(q@p.normal_outward)) for p in spec.contacts),
                        reference_point_m=tuple(q@spec.reference_point_m),
                        closing_direction=tuple(q@spec.closing_direction),
                        approach_direction=tuple(q@spec.approach_direction))
        force = np.array(((1., 2., 3.), (-2., 1., .4)))
        w = grasp_matrix(spec)@force.ravel()
        moved = grasp_matrix(rotated)@np.concatenate([q@f for f in force])
        np.testing.assert_allclose(moved, np.r_[q@w[:3], q@w[3:]], atol=1e-14)
        # Deterministic tangent bases can change phase under rotation. Reexpress
        # each local force instead of assuming finite cone columns rotate 1:1.
        local = np.array(((1., 2., 3.), (-2., 1., .4)))
        reexpressed = np.concatenate([contact_frame(b.normal_outward).T@q@
                     contact_frame(a.normal_outward)@f for a, b, f in zip(spec.contacts, rotated.contacts, local)])
        w = grasp_matrix(spec, coordinates="contact")@local.ravel()
        moved = grasp_matrix(rotated, coordinates="contact")@reexpressed
        np.testing.assert_allclose(moved, np.r_[q@w[:3], q@w[3:]], atol=1e-14)

    def test_invalid_force_frame_and_coordinate_name_are_rejected(self):
        for r in (np.diag((1., 1., -1.)), np.ones((3, 3))):
            with self.assertRaises(NumericalInputError):
                point_grasp_matrix((0., 0., 0.), (0., 0., 0.), force_frame=r)
        with self.assertRaises(NumericalInputError):
            grasp_matrix(fixtures.spec(), coordinates="world")


class FrameAndConeTests(unittest.TestCase):
    def test_frames_are_right_handed_orthonormal_stable_and_deterministic(self):
        normals = [*np.eye(3), *(-np.eye(3)), np.array((1., 1e-12, -2e-12)),
                   np.ones(3)/np.sqrt(3.)]
        for normal in normals:
            normal = normal/np.linalg.norm(normal)
            with self.subTest(normal=normal):
                r = contact_frame(normal)
                np.testing.assert_allclose(r.T@r, np.eye(3), atol=1e-14)
                np.testing.assert_allclose(np.cross(r[:, 0], r[:, 1]), r[:, 2], atol=1e-14)
                np.testing.assert_allclose(r[:, 2], -normal, atol=1e-14)
                self.assertAlmostEqual(np.linalg.det(r), 1.)
                np.testing.assert_array_equal(r, contact_frame(normal))

    def test_bad_normal_is_never_repaired(self):
        for normal in ((0., 0., 0.), (2., 0., 0.), (float("nan"), 0., 0.),
                       (0., float("inf"), 0.), (True, 0., 0.)):
            with self.subTest(normal=normal), self.assertRaises(NumericalInputError):
                contact_frame(normal)

    def test_positive_friction_directions_have_unit_inward_normal_component(self):
        outward = np.array((1., 2., 3.))/np.sqrt(14.)
        for k in (8, 16, 32):
            rays = friction_cone(outward, mu=.45, k=k)
            normal = (-outward)@rays
            tangent = rays-(-outward[:, None])*normal[None, :]
            self.assertEqual(rays.shape, (3, k))
            np.testing.assert_allclose(normal, 1., atol=1e-14)
            np.testing.assert_allclose(np.linalg.norm(tangent, axis=0), .45, atol=1e-14)
            np.testing.assert_allclose(np.linalg.norm(rays, axis=0), np.sqrt(1+.45**2), atol=1e-14)

    def test_nested_angles_are_exact_subsets(self):
        normal = np.array((1., 2., 3.))/np.sqrt(14.)
        a, b, c = (friction_cone(normal, mu=.45, k=k) for k in (8, 16, 32))
        np.testing.assert_array_equal(a, b[:, ::2])
        np.testing.assert_array_equal(b, c[:, ::2])

    def test_zero_friction_collapses_to_one_normal_direction(self):
        for k in (8, 16, 32):
            rays = friction_cone((1., 0., 0.), mu=0., k=k)
            self.assertEqual(rays.shape, (3, 1))
            np.testing.assert_array_equal(rays[:, 0], (-1., 0., 0.))
        data = build_primitive_wrenches(fixtures.spec(), friction=FrictionValue(
            coefficient=0., source="zero_friction_numerical_fixture"), k=32)
        self.assertEqual(data.matrix.shape, (6, 2))
        self.assertEqual(data.effective_directions, (1, 1))
        self.assertEqual([c.friction_direction_index for c in data.columns], [0, 0])

    def test_invalid_or_missing_mu_and_sampling_budget_are_rejected(self):
        for mu in (None, -.1, float("nan"), float("inf"), True, "0.45"):
            with self.subTest(mu=mu), self.assertRaises(NumericalInputError):
                friction_cone((1., 0., 0.), mu=mu)
        for k in (0, 7, 12, 64, True, 8.):
            with self.subTest(k=k), self.assertRaises(NumericalInputError):
                friction_cone((1., 0., 0.), mu=.45, k=k)
        with self.assertRaises(NumericalInputError):
            FrictionValue(coefficient=.45, source="unknown")


class PrimitiveDataTests(unittest.TestCase):
    def test_columns_and_metadata_are_contact_major_and_direction_minor(self):
        spec = fixtures.spec()
        data = build_primitive_wrenches(spec, friction=FIXTURE_MU, k=8)
        self.assertEqual(data.matrix.shape, (6, 16))
        for i, point in enumerate(spec.contacts):
            rays = friction_cone(point.normal_outward, mu=.45, k=8)
            np.testing.assert_allclose(data.matrix[:3, i*8:(i+1)*8], rays)
            expected = np.cross(np.array(point.position_m)-spec.reference_point_m, rays.T).T
            np.testing.assert_allclose(data.matrix[3:, i*8:(i+1)*8], expected)
            for j, column in enumerate(data.columns[i*8:(i+1)*8]):
                self.assertEqual((column.contact_index, column.friction_direction_index), (i, j))
                self.assertEqual(column.finger_id, point.finger_id)
                self.assertEqual(column.surface_id, point.surface_id)
                self.assertEqual(column.capacity_group_id, point.capacity_group_id)
        self.assertEqual(data.candidate_hash, spec.candidate_hash)
        self.assertIsNone(data.scaling)
        self.assertIsNone(data.normalized_matrix)

    def test_unknown_mu_has_no_fallback_and_contact_spec_mu_is_explicit(self):
        spec = fixtures.spec()
        with self.assertRaises(UnknownFrictionError):
            build_primitive_wrenches(spec)
        parameters = ContactParameters(friction_coefficient=.31, source="synthetic_declared_contact_mu")
        known = replace(spec, contacts=tuple(replace(p, parameters=parameters) for p in spec.contacts))
        data = build_primitive_wrenches(known)
        self.assertEqual(tuple(v.coefficient for v in data.friction_values), (.31, .31))
        self.assertEqual(data.friction_origins, ("contact_spec", "contact_spec"))
        override = build_primitive_wrenches(known, friction=FIXTURE_MU)
        self.assertEqual(override.friction_origins, ("external_explicit", "external_explicit"))
        self.assertEqual(override.contact_parameters[0].friction_coefficient, .31)

    def test_per_contact_friction_is_recorded_and_not_silently_broadcast(self):
        values = (FIXTURE_MU, FrictionValue(coefficient=.23, source="second_contact_fixture"))
        data = build_primitive_wrenches(fixtures.spec(), friction=values)
        self.assertEqual(data.friction_values, values)
        for supplied in ((FIXTURE_MU,), [.45, .45], .45):
            with self.subTest(supplied=supplied), self.assertRaises(NumericalInputError):
                build_primitive_wrenches(fixtures.spec(), friction=supplied)

    def test_extra_points_or_rays_do_not_create_extra_finger_capacity(self):
        spec = fixtures.spec()
        group = FingerCapacityGroup(group_id="left", finger_id="left", normal_force_budget_n=3.,
                                    source="synthetic_capacity_fixture")
        spec = replace(spec, contacts=(*spec.contacts, replace(spec.contacts[0], position_m=(.035, 0., .002))),
                       capacity_groups=(group, spec.capacity_groups[1]))
        for k in (8, 16, 32):
            data = build_primitive_wrenches(spec, friction=FIXTURE_MU, k=k)
            self.assertEqual(len(data.capacity_groups), 2)
            self.assertEqual(data.capacity_groups[0].normal_force_budget_n, 3.)
            self.assertEqual(len(data.columns_by_capacity_group["left"]), 2*k)
            self.assertIsNone(data.capacity_groups[1].normal_force_budget_n)
            self.assertFalse(data.to_dict()["capacity_constraints_constructed"])

    def test_input_immutable_and_output_serializes_without_nonfinite_values(self):
        spec = fixtures.spec()
        before = spec.to_dict()
        data = build_primitive_wrenches(spec, friction=FIXTURE_MU,
                                       scaling=WrenchScaling(reference_force_n=1., characteristic_length_m=.05))
        self.assertEqual(spec.to_dict(), before)
        for point in spec.contacts:
            self.assertIsNone(point.parameters.friction_coefficient)
            self.assertIsNone(point.parameters.normal_force_limit_n)
            self.assertIsNone(point.parameters.torsional_moment_per_normal_m)
        json.dumps(data.to_dict(), allow_nan=False)
        with self.assertRaises(ValueError):
            data.matrix[0, 0] = 999.
        with self.assertRaises(TypeError):
            data.columns_by_capacity_group["new"] = ()

    def test_unsupported_models_are_not_converted_to_pcwf(self):
        for model in (ContactModel.SOFT_FINGER, ContactModel.FRICTIONLESS_POINT):
            spec = fixtures.spec()
            spec = replace(spec, contacts=tuple(replace(p, contact_model=model) for p in spec.contacts))
            with self.subTest(model=model):
                with self.assertRaises(UnusableContactSpecError):
                    build_primitive_wrenches(spec, friction=FIXTURE_MU)
                with self.assertRaises(UnusableContactSpecError):
                    grasp_matrix(spec)

    def test_unusable_statuses_are_explicitly_skipped(self):
        for status in (ContactStatus.INVALID, ContactStatus.UNKNOWN, ContactStatus.UNSUPPORTED):
            spec = fixtures.spec(status=status, reasons=("synthetic_geometry_reason",))
            with self.subTest(status=status), self.assertRaises(UnusableContactSpecError):
                build_primitive_wrenches(spec, friction=FIXTURE_MU)
            audit = audit_wrench_primitives([spec], friction=FIXTURE_MU)
            self.assertEqual(audit["skipped"], 1)
            self.assertIn(status.value, audit["rows"][0]["reason"])
        audit = audit_wrench_primitives([fixtures.spec()])
        self.assertIn("unknown mu", audit["rows"][0]["reason"])
        with self.assertRaises(NumericalInputError):
            audit_wrench_primitives([spec], k=7)


class ScalingAndRankTests(unittest.TestCase):
    def test_explicit_scaling_is_reversible_and_does_not_touch_physical_matrix(self):
        matrix = np.arange(12., dtype=float).reshape(6, 2)
        before = matrix.copy()
        scaling = WrenchScaling(reference_force_n=2., characteristic_length_m=.1)
        normalized = normalize_wrenches(matrix, scaling=scaling)
        np.testing.assert_array_equal(matrix, before)
        np.testing.assert_allclose(normalized[:3], matrix[:3]/2.)
        np.testing.assert_allclose(normalized[3:], matrix[3:]/.2)
        data = build_primitive_wrenches(fixtures.spec(), friction=FIXTURE_MU, scaling=scaling)
        np.testing.assert_allclose(data.normalized_matrix, normalize_wrenches(data.matrix, scaling=scaling))

    def test_invalid_scaling_and_nonfinite_matrix_rejected(self):
        for value in (0., -1., None, True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(NumericalInputError):
                WrenchScaling(reference_force_n=value, characteristic_length_m=.1)
            with self.subTest(value=value), self.assertRaises(NumericalInputError):
                WrenchScaling(reference_force_n=1., characteristic_length_m=value)
        for value in (float("nan"), float("inf")):
            bad = np.zeros((6, 6)); bad[0, 0] = value
            with self.assertRaises(NumericalInputError):
                rank_diagnostics(bad, matrix_name="bad")
        with self.assertRaises(NumericalInputError):
            point_grasp_matrix((float("nan"), 0., 0.), (0., 0., 0.))
        with self.assertRaises(NumericalInputError):
            rank_diagnostics(np.zeros((5, 6)), matrix_name="bad shape")

    def test_two_point_cone_cannot_increase_rank_beyond_five(self):
        for k in (8, 16, 32):
            data = build_primitive_wrenches(fixtures.spec(), friction=FIXTURE_MU, k=k)
            diag = rank_diagnostics(data.matrix, matrix_name="A_unit_normal_primitives")
            self.assertEqual(diag.numerical_rank, 5)
            self.assertIsNone(diag.six_dimensional_condition)
            self.assertFalse(diag.to_dict()["force_closure_tested"])

    def test_near_coincident_contacts_report_tolerance_sensitivity_without_jitter(self):
        spec = fixtures.spec()
        a, b = spec.contacts
        spec = replace(spec, contacts=(replace(a, position_m=(0., 0., 0.)),
                                       replace(b, position_m=(1e-10, 0., 0.))))
        g = grasp_matrix(spec)
        before = g.copy()
        fine = rank_diagnostics(g, matrix_name="near_coincident_G", absolute_tolerance=1e-13)
        coarse = rank_diagnostics(g, matrix_name="near_coincident_G", absolute_tolerance=1e-8)
        self.assertEqual((fine.numerical_rank, coarse.numerical_rank), (5, 3))
        self.assertEqual(coarse.tolerance, 1e-8)
        self.assertEqual(coarse.tolerance_rule, "explicit_absolute")
        np.testing.assert_array_equal(before, g)

    def test_rank_six_does_not_imply_force_closure(self):
        a, b = fixtures.spec().contacts
        contacts = (replace(a, position_m=(0., 0., 0.), normal_outward=(-1., 0., 0.)),
                    replace(b, position_m=(0., .02, 0.), normal_outward=(-1., 0., 0.)),
                    replace(a, position_m=(0., 0., .02), normal_outward=(-1., 0., 0.), surface_id="third"))
        data = build_primitive_wrenches(fixtures.spec(contacts=contacts), friction=FIXTURE_MU)
        diag = rank_diagnostics(data.matrix, matrix_name="same_hemisphere_A")
        self.assertEqual(diag.numerical_rank, 6)
        self.assertTrue(np.all(data.matrix[0] > 0))  # cannot generate negative Fx with nonnegative coefficients
        self.assertFalse(diag.to_dict()["force_closure_tested"])

    def test_rank_deficient_map_still_generates_some_task_wrenches(self):
        g = grasp_matrix(fixtures.spec())
        w = g@np.array((0., 0., 1., 0., 0., 1.))
        np.testing.assert_allclose(w, (0., 0., 2., 0., 0., 0.), atol=1e-15)
        self.assertEqual(rank_diagnostics(g, matrix_name="G").numerical_rank, 5)
        # Explicit nonnegative cone combination, without solving an LP or
        # assuming that the real gripper has this normal force available.
        data = build_primitive_wrenches(fixtures.spec(), friction=FIXTURE_MU, k=8)
        coefficients = np.zeros(16)
        coefficients[6], coefficients[10] = 1., 1.
        np.testing.assert_allclose(data.matrix@coefficients, (0., 0., .9, 0., 0., 0.), atol=1e-14)

    def test_diagnostics_identify_actual_raw_or_scaled_matrix_and_tolerance(self):
        g = grasp_matrix(fixtures.spec())
        raw = rank_diagnostics(g, matrix_name="raw_G")
        scaling = WrenchScaling(reference_force_n=1., characteristic_length_m=.05)
        scaled = rank_diagnostics(g, matrix_name="scaled_G", scaling=scaling)
        explicit = rank_diagnostics(normalize_wrenches(g, scaling=scaling), matrix_name="already_scaled_G")
        self.assertEqual(scaled.matrix_sha256, explicit.matrix_sha256)
        self.assertNotEqual(raw.matrix_sha256, scaled.matrix_sha256)
        self.assertIsNone(raw.scaling)
        self.assertEqual(scaled.scaling, scaling)
        self.assertAlmostEqual(raw.tolerance, np.finfo(float).eps*max(g.shape)*raw.singular_values[0])
        zero = rank_diagnostics(np.zeros((6, 8)), matrix_name="zero")
        self.assertEqual(zero.numerical_rank, 0)
        self.assertIsNone(zero.nonzero_spectrum_condition)
        json.dumps(zero.to_dict(), allow_nan=False)

    def test_algorithms_do_not_use_random_jitter(self):
        with patch("numpy.random.default_rng", side_effect=AssertionError("random forbidden")), patch(
                "numpy.random.normal", side_effect=AssertionError("jitter forbidden")):
            a = build_primitive_wrenches(fixtures.spec(), friction=FIXTURE_MU)
            first = rank_diagnostics(a.matrix, matrix_name="A")
            second = rank_diagnostics(a.matrix, matrix_name="A")
            self.assertEqual(first, second)


class MainPoolNumericalIntegrationTests(unittest.TestCase):
    def test_real_116_pool_builds_only_usable_specs_with_explicit_test_mu(self):
        path = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"
        model = read_model("hollow", path)
        candidates, records = generate_multifamily(model, pruned_config())
        static = audit_candidates(candidates, ObjectGeometry.from_model(model), read_gripper_geometry(path),
                                  records=records, generator_name="grasp_b.multifamily", generator_version=VERSION)
        numeric = audit_wrench_primitives(static["specs"], friction=FIXTURE_MU, k=16)
        self.assertEqual(len(numeric["rows"]), 116)
        self.assertEqual(len(numeric["inputs"]), 105)
        self.assertEqual(numeric["skipped"], 11)
        self.assertEqual(set(numeric["rank_distributions"]), {"body", "top", "handle", "rim"})
        by_id = {s.candidate_id: s for s in static["specs"]}
        for data in numeric["inputs"]:
            original = by_id[data.candidate_id]
            self.assertEqual(data.candidate_hash, original.candidate_hash)
            self.assertEqual(data.capacity_groups, original.capacity_groups)
            self.assertTrue(all(g.normal_force_budget_n is None for g in data.capacity_groups))
            self.assertTrue(all(p.parameters.friction_coefficient is None for p in original.contacts))
            self.assertTrue(all(v.source == "mu_assumed_for_numerical_fixture" for v in data.friction_values))
        for row in numeric["rows"]:
            if row["status"] == "skipped":
                self.assertIn("status_invalid", row["reason"])
                continue
            self.assertTrue(all(t >= 0 for t in row["timings_s"].values()))
            if row["contact_count"] == 2:
                self.assertEqual(row["grasp_diagnostics"]["numerical_rank"], 5)
                self.assertEqual(row["primitive_diagnostics"]["numerical_rank"], 5)

    def test_numerical_module_import_does_not_require_simulator_or_optimizer(self):
        script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'grasp_a', 'grasp_c', 'pybullet', 'pybullet_data', 'scipy'}:
        raise AssertionError('forbidden dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from grasp_b.wrench_v2.gws import contact_frame
assert contact_frame((1.,0.,0.)).shape == (3,3)
'''
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
