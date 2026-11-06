"""Finite pre-execution TWS fixtures; no physics world, outcomes or ranking."""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from grasp_b.wrench_v2.tasks import (
    TASK_MODEL_VERSION, Disturbance, ObjectParameters, Orientation, RigidPayload,
    TaskWrenchScenario, TaskWrenchSet, UPRIGHT, ZERO_DISTURBANCE, axis_disturbances,
    build_task_wrench_set, combine_payload, pour_orientations_x, read_declared_object,
    required_wrench_for_primitives, rotation_matrix,
)

ROOT = Path(__file__).resolve().parents[1]
GRAVITY = (0., 0., -9.81)


def body(**changes):
    return ObjectParameters(**dict(dict(mass_kg=1., com_m=(0., 0., 0.),
        source="assumed numerical fixture; not calibrated"), **changes))


def task(name="transport_place", model=None, **changes):
    options = dict(reference_point_m=(0., 0., 0.), gravity_world_m_s2=GRAVITY,
                   context_source="declared gravity and explicit reference for numerical fixtures")
    options.update(changes)
    return build_task_wrench_set(name, body() if model is None else model, **options)


class GravityTests(unittest.TestCase):
    def test_upright_external_and_required_have_opposite_signs(self):
        s = task().scenarios[0]
        np.testing.assert_allclose(s.external_wrench, (0, 0, -9.81, 0, 0, 0))
        np.testing.assert_allclose(s.required_contact_wrench, (0, 0, 9.81, 0, 0, 0))

    def test_90_degree_world_x_tilt_changes_body_gravity(self):
        s = task("pour_water", orientations=pour_orientations_x((math.pi/2,), source="fixture tilt")).scenarios[0]
        np.testing.assert_allclose(s.external_wrench[:3], (0, -9.81, 0), atol=1e-14)

    def test_nonzero_com_moment(self):
        s = task(model=body(com_m=(.1, 0, 0))).scenarios[0]
        np.testing.assert_allclose(s.external_wrench[3:], (0, .981, 0))

    def test_reference_shift_changes_moment_once_not_tcp(self):
        model = body(com_m=(.1, -.02, 0))
        old = task(model=model).scenarios[0]
        offset = np.array((.03, .04, .01))
        new = task(model=model, reference_point_m=offset).scenarios[0]
        np.testing.assert_allclose(new.external_wrench[3:],
            np.array(old.external_wrench[3:])-np.cross(offset, old.external_wrench[:3]))
        np.testing.assert_allclose(new.required_contact_wrench, -np.array(new.external_wrench))

    def test_world_body_equilibrium_consistency(self):
        q = pour_orientations_x((.7,), source="fixture attitude", base_quaternion_xyzw=(0, 0, math.sin(.3), math.cos(.3)))
        s = task(model=body(com_m=(.1, .2, .3)), orientations=q).scenarios[0]
        r = rotation_matrix(q[0].quaternion_xyzw)
        world_force = np.array(GRAVITY)
        np.testing.assert_allclose(r@np.array(s.external_wrench[:3]), world_force, atol=1e-14)
        np.testing.assert_allclose(r@np.array(s.external_wrench[3:]), np.cross(r@np.array(s.object_parameters.com_m), world_force), atol=1e-14)

    def test_zero_gravity_is_explicitly_allowed_for_numerical_fixture(self):
        np.testing.assert_array_equal(task(gravity_world_m_s2=(0, 0, 0)).scenarios[0].external_wrench, np.zeros(6))


class TransportTests(unittest.TestCase):
    def test_zero_disturbance_baseline(self):
        self.assertEqual(len(task().scenarios), 1)
        self.assertEqual(task().scenarios[0].disturbance, ZERO_DISTURBANCE)

    def test_horizontal_and_vertical_finite_axes_and_order(self):
        ds = axis_disturbances(horizontal_force_n=2., vertical_force_n=1.,
            application_point_m=(0, 0, .1), source="assumed development fixture")
        self.assertEqual([d.force_n for d in ds], [(0, 0, 0), (2, 0, 0), (-2, 0, 0),
                                                  (0, 2, 0), (0, -2, 0), (0, 0, 1), (0, 0, -1)])
        self.assertEqual(len(task(disturbances=ds).scenarios), 7)

    def test_disturbance_force_and_offset_torque(self):
        d = Disturbance(force_n=(2, 0, 0), application_point_m=(0, 0, .1), source="assumed fixture")
        s = task(disturbances=(d,)).scenarios[0]
        np.testing.assert_allclose(s.external_wrench, (2, 0, -9.81, 0, .2, 0))

    def test_world_and_mug_disturbances_with_free_couple(self):
        o = pour_orientations_x((math.pi/2,), source="fixture")[0]
        r = rotation_matrix(o.quaternion_xyzw)
        world = Disturbance(force_n=(0, 0, 1), free_torque_nm=(0, 0, .2),
            application_point_m=(.1, .2, .3), source="assumed fixture")
        local = replace(world, frame="mug", force_n=r.T@world.force_n, free_torque_nm=r.T@world.free_torque_nm)
        a = task(orientations=(o,), disturbances=(world,)).scenarios[0]
        b = task(orientations=(o,), disturbances=(local,)).scenarios[0]
        np.testing.assert_allclose(a.external_wrench, b.external_wrench)

    def test_mass_and_perturbation_scaling(self):
        d = Disturbance(force_n=(1, 2, 0), application_point_m=(0, 0, .1), source="fixture")
        a = task(disturbances=(d,)).scenarios[0]
        b = task(model=body(mass_kg=2), disturbances=(replace(d, force_n=(2, 4, 0)),)).scenarios[0]
        np.testing.assert_allclose(b.required_contact_wrench, 2*np.array(a.required_contact_wrench))


class HandoverTests(unittest.TestCase):
    def test_donor_support_matches_full_gravity_no_receiver_credit(self):
        s = task("handover_transfer").scenarios[0]
        self.assertAlmostEqual(s.required_contact_wrench[2], 9.81)
        self.assertIn("no receiver/table assistance", " ".join(s.assumptions))

    def test_receiver_stage_is_explicit_unsupported_with_reason(self):
        for stage in ("receiver_close", "donor_release", "receiver_lift", "terminal"):
            value = task("handover_transfer", stage=stage)
            self.assertFalse(value.scenarios)
            self.assertIn("receiver support", value.unsupported_reason)
            self.assertEqual(value.to_dict()["status"], "unsupported")

    def test_explicit_handover_attitudes_and_disturbances(self):
        attitudes = pour_orientations_x((0., .2), source="assumed handover attitudes, not A commands")
        ds = axis_disturbances(horizontal_force_n=.1, vertical_force_n=0,
                               application_point_m=(0, 0, .02), source="assumed donor disturbance")
        self.assertEqual(len(task("handover_transfer", orientations=attitudes, disturbances=ds).scenarios), 10)


class PourTests(unittest.TestCase):
    def test_explicit_angles_required_no_hidden_tilt(self):
        with self.assertRaises(ValueError):
            task("pour_water")

    def test_upright_to_a_negative_110_degree_tilt(self):
        angles = (0., -math.pi/2, math.radians(-110))
        orientations = pour_orientations_x(angles, source="A declared final angle; assumed finite intermediates")
        result = task("pour_water", orientations=orientations)
        for angle, s in zip(angles, result.scenarios):
            np.testing.assert_allclose(s.external_wrench[:3], (0, -9.81*math.sin(angle), -9.81*math.cos(angle)), atol=1e-14)

    def test_zero_water_has_same_wrench_as_dry_case(self):
        orientations = (UPRIGHT,)
        dry = task("pour_water", orientations=orientations).scenarios[0]
        wet = task("pour_water", orientations=orientations,
            payload_cases=(RigidPayload(mass_kg=0, com_m=(.1, .2, .3), source="zero-water fixture"),)).scenarios[0]
        np.testing.assert_array_equal(dry.required_contact_wrench, wet.required_contact_wrench)

    def test_rigid_water_mass_com_and_gravity_moment(self):
        payload = RigidPayload(mass_kg=.1, com_m=(.02, 0, .03), source="assumed rigid water fixture")
        model = body(mass_kg=.28)
        s = task("pour_water", model=model, orientations=(UPRIGHT,), payload_cases=(payload,)).scenarios[0]
        self.assertAlmostEqual(s.object_parameters.mass_kg, .38)
        np.testing.assert_allclose(s.object_parameters.com_m, (.1*.02/.38, 0, .1*.03/.38))
        np.testing.assert_allclose(s.external_wrench, (0, 0, -.38*9.81, 0, .1*.02*9.81, 0))
        self.assertIsNone(s.object_parameters.inertia_com_kg_m2)

    def test_parallel_axis_combination_only_when_inertias_known(self):
        model = body(inertia_com_kg_m2=np.eye(3)*.01)
        payload = RigidPayload(mass_kg=1., com_m=(.2, 0, 0), inertia_com_kg_m2=np.zeros((3, 3)),
                               source="assumed explicit point mass, not PBF")
        combined = combine_payload(model, payload)
        np.testing.assert_allclose(combined.com_m, (.1, 0, 0))
        np.testing.assert_allclose(combined.inertia_com_kg_m2, np.diag((.01, .03, .03)))
        self.assertIsNone(combine_payload(model, replace(payload, inertia_com_kg_m2=None)).inertia_com_kg_m2)

    def test_water_uncertainty_cases_are_explicit_union_not_time_evolution(self):
        payloads = (None, RigidPayload(mass_kg=.1, com_m=(0, 0, 0), source="assumed case 1"),
                    RigidPayload(mass_kg=.12, com_m=(.01, 0, 0), source="assumed case 2"))
        value = task("pour_water", orientations=(UPRIGHT,), payload_cases=payloads)
        self.assertEqual([s.object_parameters.mass_kg for s in value.scenarios], [1., 1.1, 1.12])

    def test_world_x_rotation_multiplies_base_on_left(self):
        base = (0, 0, math.sin(.3), math.cos(.3))
        o = pour_orientations_x((.7,), source="fixture", base_quaternion_xyzw=base)[0]
        x = pour_orientations_x((.7,), source="fixture")[0]
        np.testing.assert_allclose(rotation_matrix(o.quaternion_xyzw), rotation_matrix(x.quaternion_xyzw)@rotation_matrix(base), atol=1e-14)


class IntegrityTests(unittest.TestCase):
    def test_task_module_import_has_no_simulation_or_selector_dependency(self):
        code = ('import sys; import grasp_b.wrench_v2.tasks; '
                'assert not any(m == "pybullet" or m.startswith(("grasp_a", "grasp_c")) '
                'for m in sys.modules)')
        subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, check=True, capture_output=True)

    def test_context_is_explicit_and_quaternion_sign_identity_is_stable(self):
        with self.assertRaises(TypeError):
            build_task_wrench_set("transport_place", body())
        equivalent = Orientation(quaternion_xyzw=(0, 0, 0, -1), source=UPRIGHT.source)
        self.assertEqual(equivalent, UPRIGHT)
        self.assertEqual(task().scenarios[0].scenario_id,
                         task(orientations=(equivalent,)).scenarios[0].scenario_id)

    def test_declared_sequence_and_legacy_asset_parameters_not_runtime(self):
        for name in ("mug.urdf", "mug_hollow.urdf"):
            m = read_declared_object(ROOT/"assets"/name)
            self.assertEqual(m.mass_kg, .28)
            self.assertEqual(m.com_m, (0., 0., 0.))
            np.testing.assert_allclose(m.inertia_com_kg_m2, np.diag((.00035, .00035, .00022)))
            self.assertIn("not_runtime_verified", m.source)
            self.assertEqual(len(m.provenance["asset_sha256"]), 64)
        self.assertNotEqual(read_declared_object(ROOT/"assets/mug.urdf").provenance["asset_sha256"],
                            read_declared_object(ROOT/"assets/mug_hollow.urdf").provenance["asset_sha256"])

    def test_urdf_inertial_rotation_and_asset_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"mug.urdf"
            p.write_text('<robot><link name="mug"><inertial><origin xyz=".1 0 0" rpy="0 0 1.5707963267948966"/>'
                         '<mass value="1"/><inertia ixx="1" iyy="2" izz="3" ixy="0" ixz="0" iyz="0"/></inertial></link></robot>')
            a = read_declared_object(p)
            np.testing.assert_allclose(a.inertia_com_kg_m2, np.diag((2, 1, 3)), atol=1e-14)
            p.write_text(p.read_text()+'\n')
            self.assertNotEqual(a.provenance["asset_sha256"], read_declared_object(p).provenance["asset_sha256"])

    def test_invalid_mass_com_inertia_and_unknown_inertia(self):
        for value in (-1, 0, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                body(mass_kg=value)
        for value in ((0, 0), (0, float("nan"), 0), (0, 0, float("inf"))):
            with self.assertRaises(ValueError):
                body(com_m=value)
        with self.assertRaises(ValueError):
            body(inertia_com_kg_m2=-np.eye(3))
        self.assertIsNone(body().inertia_com_kg_m2)

    def test_invalid_orientations_and_nonfinite_loads(self):
        for value in ((0, 0, 0, 0), (0, 0, 0, 2), (0, 0, 1), (0, 0, 0, float("nan"))):
            with self.assertRaises(ValueError):
                Orientation(quaternion_xyzw=value, source="fixture")
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                axis_disturbances(horizontal_force_n=value, vertical_force_n=0, application_point_m=(0, 0, 0), source="fixture")
        with self.assertRaises(ValueError):
            task(gravity_world_m_s2=(0, 0, float("nan")))
        with self.assertRaises(ValueError):
            RigidPayload(mass_kg=-1, com_m=(0, 0, 0), source="fixture")

    def test_units_and_frames_are_not_implicitly_converted(self):
        d = ZERO_DISTURBANCE
        for changes in (dict(force_unit="kN"), dict(position_unit="mm"), dict(torque_unit="N*mm"), dict(frame="tcp")):
            with self.assertRaises(ValueError):
                replace(d, **changes)
        self.assertEqual(task().scenarios[0].to_dict()["coordinate_frame"], "mug")

    def test_immutable_nested_provenance_and_json(self):
        provenance = {"input": ["fixture"]}
        model = body(provenance=provenance)
        provenance["input"].append("changed")
        self.assertEqual(model.provenance["input"], ("fixture",))
        value = task(model=model)
        with self.assertRaises(FrozenInstanceError):
            value.scenarios = ()
        self.assertEqual(value.model_version, TASK_MODEL_VERSION)
        self.assertIn("explicit reference", value.scenarios[0].context_source)
        json.dumps(value.to_dict(), allow_nan=False)

    def test_deterministic_ids_order_and_parameter_identity(self):
        ds = axis_disturbances(horizontal_force_n=.2, vertical_force_n=0, application_point_m=(0, 0, .02), source="assumed fixture")
        a, b = task(disturbances=ds), task(disturbances=ds)
        self.assertEqual(a, b)
        self.assertEqual([s.scenario_id for s in a.scenarios], [s.scenario_id for s in b.scenarios])
        self.assertNotEqual(a.scenarios[0].scenario_id, task(model=body(mass_kg=2)).scenarios[0].scenario_id)

    def test_exact_duplicate_wrenches_retained_when_scenarios_differ(self):
        yaw = Orientation(quaternion_xyzw=(0, 0, 1, 0), source="different attitude, same gravity")
        value = task(orientations=(UPRIGHT, yaw))
        self.assertEqual(value.diagnostics()["exact_duplicate_wrench_count"], 1)
        self.assertGreater(value.diagnostics()["json_bytes"], 0)
        with self.assertRaises(ValueError):
            task(orientations=(UPRIGHT, UPRIGHT))

    def test_task_name_typed_inputs_and_unsupported_stages(self):
        with self.assertRaises(ValueError):
            task("pour_prep")
        with self.assertRaises(ValueError):
            task(orientations=())
        with self.assertRaises(ValueError):
            task(payload_cases=(RigidPayload(mass_kg=.1, com_m=(0, 0, 0), source="fixture"),))
        with self.assertRaises(ValueError):
            replace(task().scenarios[0], stage="receiver_lift")
        with self.assertRaises(ValueError):
            TaskWrenchSet(task_name="transport_place", stage="donor_hold", scenarios=())


@unittest.skipUnless(importlib.util.find_spec("scipy"), "real HiGHS requires SciPy")
class SolverInterfaceTests(unittest.TestCase):
    def test_real_lp_supports_upright_gravity_but_not_axial_torque(self):
        from tests import test_b_wrench_constraints as fixtures
        from grasp_b.wrench_v2.solver import solve_wrench, SolverStatus
        d = fixtures.data()
        budgets = {g.group_id: fixtures.capacity(1.) for g in d.capacity_groups}
        s = task(model=body(mass_kg=.1)).scenarios[0]
        result = solve_wrench(d, required_wrench_for_primitives(s, d), finger_budgets=budgets)
        self.assertIs(result.status, SolverStatus.OPTIMAL)
        self.assertAlmostEqual(result.utilization, .981, delta=1e-7)
        offset = task(model=body(mass_kg=.1, com_m=(0, .1, 0))).scenarios[0]
        result = solve_wrench(d, required_wrench_for_primitives(offset, d), finger_budgets=budgets)
        self.assertIs(result.status, SolverStatus.INFEASIBLE)

    def test_mismatched_reference_rejected_before_lp(self):
        from tests import test_b_wrench_constraints as fixtures
        with self.assertRaises(ValueError):
            required_wrench_for_primitives(task(reference_point_m=(.01, 0, 0)).scenarios[0], fixtures.data())


if __name__ == "__main__":
    unittest.main()
