"""03V: static physics, frame covariance, A nominal goals and one-factor probes.

Margins below are ONLY the dimensionless nominal capacity slack 1-u. They are
not a new score, success probability, or a dimensional force reserve.
"""
from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from grasp_b.wrench_v2.tasks import (
    Disturbance, ObjectParameters, Orientation, RigidPayload, UPRIGHT,
    axis_disturbances, build_task_wrench_set, pour_orientations_x,
    read_declared_object, required_wrench_for_primitives, rotation_matrix,
)
from grasp_b.wrench_v2.gws import FrictionValue, skew
from grasp_b.wrench_v2.solver import solve_wrench, SolverStatus
from grasp_b.wrench_v2.physics_snapshot import static_physics_snapshot
from tests import test_b_wrench_constraints as numeric
from tests import test_b_wrench_tasks as tws

ROOT = Path(__file__).resolve().parents[1]


def multiply(a, b):
    av, bv = np.array(a[:3]), np.array(b[:3])
    return (*tuple(a[3]*bv+b[3]*av+np.cross(av, bv)), a[3]*b[3]-float(av@bv))


def sensitivity_rows():
    """Reproducible numerical fixtures, with ONE parameter changed in each row.

    PCWF two-point geometry +/-0.035 m on x, k=16; baseline mu=.5,
    each finger 5 N, dry mass .28 kg, COM=origin, upright. ALL capacities
    and contact friction are uncalibrated assumptions, not A motor limits.
    """
    probes = {
        "friction": (.25, .5, .75), "capacity_n": (2.5, 5., 10.),
        "mass_kg": (.14, .28, .42), "com_x_m": (0., .01, .02),
        "com_y_m": (0., .001), "water_mass_kg": (0., .05, .1),
        "tilt_deg": (0., -30., -60., -90., -110.), "disturbance_x_n": (0., .1, .2),
    }
    rows = []
    for parameter, values in probes.items():
        for value in values:
            mu = value if parameter == "friction" else .5
            capacity = value if parameter == "capacity_n" else 5.
            com = (value, 0., 0.) if parameter == "com_x_m" else ((0., value, 0.) if parameter == "com_y_m" else (0., 0., 0.))
            body = ObjectParameters(mass_kg=value if parameter == "mass_kg" else .28, com_m=com,
                source="03V assumed dry numerical fixture; mass matches declared mug baseline")
            options = {}
            task = "transport_place"
            if parameter in ("tilt_deg", "water_mass_kg"):
                task = "pour_water"
                options["orientations"] = pour_orientations_x(
                    (math.radians(value) if parameter == "tilt_deg" else 0.,), source="03V assumed quasi-static tilt fixture")
            if parameter == "water_mass_kg":
                options["payload_cases"] = (RigidPayload(mass_kg=value, com_m=(0, 0, 0), source="03V assumed rigid water COM, no sloshing"),)
            if parameter == "disturbance_x_n":
                options["disturbances"] = (Disturbance(force_n=(value, 0, 0), application_point_m=(0, 0, 0), source="03V assumed perturbation at origin"),)
            scenario = build_task_wrench_set(task, body, reference_point_m=(0, 0, 0),
                gravity_world_m_s2=(0, 0, -9.81), context_source="03V declared gravity; explicit reference origin", **options).scenarios[0]
            data = numeric.data(k=16, mu=FrictionValue(coefficient=mu, source="03V assumed PCWF friction, uncalibrated"))
            result = solve_wrench(data, required_wrench_for_primitives(scenario, data),
                finger_budgets={g.group_id: numeric.capacity(capacity) for g in data.capacity_groups})
            rows.append(dict(parameter=parameter, value=value, required_wrench=scenario.required_contact_wrench,
                status=result.status.value, utilization=result.utilization,
                margin=None if result.utilization is None else 1-result.utilization,
                diagnostics=dict(result.diagnostics), assumption="03V numerical fixture, not calibrated",
                scenario_id=scenario.scenario_id))
    return rows


class FrameValidationTests(unittest.TestCase):
    def test_com_moment_signs_for_both_offset_directions(self):
        for offset in (-.01, .01):
            x = tws.task(model=tws.body(com_m=(offset, 0, 0))).scenarios[0]
            y = tws.task(model=tws.body(com_m=(0, offset, 0))).scenarios[0]
            self.assertAlmostEqual(x.required_contact_wrench[4], -offset*9.81)
            self.assertAlmostEqual(y.required_contact_wrench[3], offset*9.81)

    def test_world_basis_rotation_preserves_body_wrench(self):
        orientation = pour_orientations_x((.6,), source="fixture")[0]
        s = tws.task(model=tws.body(com_m=(.02, .03, .01)), orientations=(orientation,),
            disturbances=(Disturbance(force_n=(.2, .1, 0), free_torque_nm=(.01, .02, .03),
                         application_point_m=(.02, .01, .03), source="fixture world load"),)).scenarios[0]
        q = (0., math.sin(.3), 0., math.cos(.3))
        rotation = rotation_matrix(q)
        d = s.disturbance
        transformed = replace(s, orientation=Orientation(quaternion_xyzw=multiply(q, orientation.quaternion_xyzw), source="rotated world basis"),
            gravity_world_m_s2=rotation@s.gravity_world_m_s2,
            disturbance=replace(d, force_n=rotation@d.force_n, free_torque_nm=rotation@d.free_torque_nm))
        np.testing.assert_allclose(transformed.required_contact_wrench, s.required_contact_wrench, atol=1e-14)

    def test_object_coordinate_rotation_covariance(self):
        s = tws.task(model=tws.body(com_m=(.02, .03, .01)), reference_point_m=(.01, .02, -.01)).scenarios[0]
        q = (0., 0., math.sin(.4), math.cos(.4))
        rotation = rotation_matrix(q)
        transformed = replace(s, object_parameters=replace(s.object_parameters, com_m=rotation@s.object_parameters.com_m),
            reference_point_m=rotation@s.reference_point_m,
            orientation=Orientation(quaternion_xyzw=(-q[0], -q[1], -q[2], q[3]), source="relabelled mug basis"))
        np.testing.assert_allclose(transformed.required_contact_wrench,
            np.concatenate((rotation@s.required_contact_wrench[:3], rotation@s.required_contact_wrench[3:])), atol=1e-14)

    def test_upright_yaw_preserves_gravity_but_not_arbitrary_world_force(self):
        yaw = Orientation(quaternion_xyzw=(0, 0, math.sin(.4), math.cos(.4)), source="scene yaw fixture")
        model = tws.body(com_m=(.01, .02, .03))
        a, b = tws.task(model=model).scenarios[0], tws.task(model=model, orientations=(yaw,)).scenarios[0]
        np.testing.assert_allclose(a.required_contact_wrench, b.required_contact_wrench, atol=1e-14)
        d = Disturbance(force_n=(1, 0, 0), application_point_m=(0, 0, 0), source="fixed world force")
        a, b = tws.task(disturbances=(d,)).scenarios[0], tws.task(orientations=(yaw,), disturbances=(d,)).scenarios[0]
        self.assertFalse(np.allclose(a.required_contact_wrench, b.required_contact_wrench))

    def test_reference_shift_and_contact_cone_rotation_preserve_lp_utilization(self):
        d = numeric.data(k=16)
        scenario = tws.task(model=tws.body(mass_kg=.28, com_m=(.01, 0, 0))).scenarios[0]
        budgets = {g.group_id: numeric.capacity(5.) for g in d.capacity_groups}
        baseline = solve_wrench(d, required_wrench_for_primitives(scenario, d), finger_budgets=budgets)
        offset = np.array((.01, .02, .03))
        shifted = replace(d, matrix=np.vstack((d.matrix[:3], d.matrix[3:]-skew(offset)@d.matrix[:3])), reference_point_m=tuple(offset))
        shifted_scenario = replace(scenario, reference_point_m=offset)
        shifted_result = solve_wrench(shifted, required_wrench_for_primitives(shifted_scenario, shifted), finger_budgets=budgets)
        q = (0., 0., math.sin(.37), math.cos(.37))
        rotation = rotation_matrix(q)
        # Rotate the SAME finite rays. Rebuilding axis-phased cones would change
        # the finite polygon and would not test coordinate covariance alone.
        rotated = replace(d, matrix=np.vstack((rotation@d.matrix[:3], rotation@d.matrix[3:])))
        wrench = np.concatenate((rotation@scenario.required_contact_wrench[:3], rotation@scenario.required_contact_wrench[3:]))
        rotated_result = solve_wrench(rotated, wrench, finger_budgets=budgets)
        for result in (baseline, shifted_result, rotated_result):
            self.assertIs(result.status, SolverStatus.OPTIMAL)
            self.assertAlmostEqual(result.utilization, baseline.utilization, delta=1e-8)


class NominalATaskTests(unittest.TestCase):
    def test_scene_initial_randomization_is_yaw_only_with_declared_ranges(self):
        import ast
        from grasp_a.control import SCENE_PRESETS
        self.assertEqual(SCENE_PRESETS["legacy"].yaw_range_rad, (-.08, .08))
        self.assertEqual(SCENE_PRESETS["wide"].yaw_range_rad, (-math.pi/3, math.pi/3))
        source = ast.parse((ROOT/"grasp_a/simulation.py").read_text())
        assignments = [n for n in ast.walk(source) if isinstance(n, ast.Assign) and
                       any(isinstance(t, ast.Name) and t.id == "start_q" for t in n.targets)]
        self.assertEqual(len(assignments), 1)
        angles = assignments[0].value.args[0].elts
        self.assertEqual([ast.literal_eval(x) for x in angles[:2]], [0, 0])

    def test_actual_donor_moves_object_goals_are_grasp_independent(self):
        # Run A's PURE goal builder with a pose-only stub, never SequenceScene
        # construction, IK, motion, physics steps or contact observations.
        import pybullet as p
        from grasp_a.contracts import Pose
        from grasp_a.sequence import SequenceScene
        from grasp_a.sequence_contracts import SequenceConfig, task_spec
        from tests import test_b_wrench_adapter as fixtures
        fixtures.StaticAdapterTests.setUpClass()
        f = fixtures.StaticAdapterTests()
        yaw = .4
        initial = Pose((.55, 0, .645), (0, 0, math.sin(yaw/2), math.cos(yaw/2)))
        def world_pose(local):
            position, q = p.multiplyTransforms(initial.position, initial.quaternion, local.position, local.quaternion)
            return Pose(tuple(position), tuple(q))
        for task in ("transport_place", "handover_transfer", "pour_water"):
            c = SequenceConfig()
            stub = SimpleNamespace(config=c, task_id=task, spec=task_spec(task, c), world_pose=world_pose)
            for family in ("side", "top", "handle", "rim"):
                candidate = f.candidate(family)
                with patch.object(p, "stepSimulation", side_effect=AssertionError("no stepping")):
                    moves = SequenceScene.donor_moves(stub, candidate)
                grasp = rotation_matrix(candidate.grasp_pose.quaternion)
                # Bullet's transform arithmetic returns float32-rounded quats.
                # Read those through A's math representation for this audit;
                # do not weaken/implicitly normalize TWS input validation.
                lift = np.array(p.getMatrixFromQuaternion(moves["lift"][0].quaternion)).reshape(3, 3)@grasp.T
                carry = np.array(p.getMatrixFromQuaternion(moves["transport"][0].quaternion)).reshape(3, 3)@grasp.T
                np.testing.assert_allclose(lift, rotation_matrix(initial.quaternion), atol=1e-6)
                np.testing.assert_allclose(carry, np.eye(3), atol=1e-6)
                tcp = moves["transport"][0]
                recovered_position = np.array(tcp.position)-carry@candidate.grasp_pose.position
                expected = (*c.target_xy, c.carry_height_m)
                if task == "pour_water":
                    expected = (c.target_xy[0], c.target_xy[1]-.045, c.carry_height_m)
                    tilt = np.array(p.getMatrixFromQuaternion(moves["tilt"][0].quaternion)).reshape(3, 3)@grasp.T
                    expected_tilt = pour_orientations_x((math.radians(c.pour_angle_deg),), source="A goal comparison")[0]
                    np.testing.assert_allclose(tilt, rotation_matrix(expected_tilt.quaternion_xyzw), atol=1e-6)
                np.testing.assert_allclose(recovered_position, expected, atol=1e-6)

    def test_initial_yaw_is_not_a_commanded_pour_base(self):
        yaw = (0, 0, math.sin(.2), math.cos(.2))
        a = pour_orientations_x((-.8,), source="nominal")[0]
        b = pour_orientations_x((-.8,), base_quaternion_xyzw=yaw, source="residual carry-yaw assumption")[0]
        self.assertFalse(np.allclose(rotation_matrix(a.quaternion_xyzw), rotation_matrix(b.quaternion_xyzw)))


class RuntimeSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pybullet as p
        with ExitStack() as stack:
            for name in ("stepSimulation", "setJointMotorControl2", "setJointMotorControlArray", "changeDynamics"):
                stack.enter_context(patch.object(p, name, side_effect=AssertionError("static load/query only")))
            cls.snapshot = static_physics_snapshot(ROOT)

    def test_runtime_mass_com_flags_and_inertia_difference(self):
        for name, asset in (("legacy", "mug.urdf"), ("sequence", "mug_hollow.urdf")):
            declared = read_declared_object(ROOT/"assets"/asset)
            entry = self.snapshot["assets"][name]
            dynamics = entry["dynamics"]
            self.assertEqual(entry["flags"], 0)
            self.assertAlmostEqual(dynamics["mass_kg"], declared.mass_kg)
            np.testing.assert_allclose(dynamics["local_inertial_position_m"], declared.com_m)
            self.assertGreater(np.linalg.norm(np.array(dynamics["local_inertia_diagonal_kg_m2"])-np.diag(declared.inertia_com_kg_m2)), 1e-6)
            self.assertEqual(len(entry["collision_shapes"]), 4 if name == "legacy" else 36)

    def test_no_steps_overrides_not_confused_with_loaded_friction(self):
        self.assertEqual(self.snapshot["physics_steps"], 0)
        overrides = self.snapshot["source_declared_overrides_not_applied"]
        self.assertEqual(overrides["mug_lateral_friction"], 1.2)
        self.assertIsNone(overrides["contact_normal_force_capacity_n"])
        self.assertFalse(overrides["contact_mu_calibrated"])
        for entry in self.snapshot["assets"].values():
            self.assertGreaterEqual(entry["dynamics"]["lateral_friction"], 0)
        self.assertEqual(len(self.snapshot["assets"]["panda"]["fingers"]), 2)
        for source, digest in self.snapshot["source_sha256"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT/source).read_bytes()).hexdigest())

    def test_asset_source_mirror_matches_as_loading_policy(self):
        import ast
        source = (ROOT/"grasp_a/simulation.py").read_text()
        calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and
                 isinstance(n.func, ast.Attribute) and n.func.attr == "loadURDF"]
        mug = [c for c in calls if "self.MUG_ASSET" in ast.unparse(c)]
        self.assertEqual(len(mug), 1)
        self.assertNotIn("flags", {k.arg for k in mug[0].keywords})
        self.assertIn("URDF_USE_SELF_COLLISION_EXCLUDE_PARENT", source)
        dynamics = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and
                    isinstance(n.func, ast.Attribute) and n.func.attr == "changeDynamics"]
        self.assertTrue(any(any(k.arg == "lateralFriction" and ast.literal_eval(k.value) == 1.2
                                for k in n.keywords if k.arg == "lateralFriction") for n in dynamics))


class SensitivityTests(unittest.TestCase):
    def test_force_axes_cover_both_signs_but_do_not_imply_full_torque_envelope(self):
        ds = axis_disturbances(horizontal_force_n=1, vertical_force_n=1,
            application_point_m=(0, 0, .1), source="finite coverage fixture")
        self.assertEqual(len(ds), 7)
        scenarios = tws.task(disturbances=ds).scenarios
        # Fixed z lever generates +/- x,y moments; no independent z moment.
        torques = [s.external_wrench[3:] for s in scenarios]
        self.assertEqual({round(t[2], 12) for t in torques}, {0.})
        for axis in (0, 1):
            self.assertLess(min(t[axis] for t in torques), 0)
            self.assertGreater(max(t[axis] for t in torques), 0)
        # Independent couples are representable only when explicitly supplied.
        for axis in range(3):
            for sign in (-1, 1):
                torque = np.zeros(3)
                torque[axis] = sign*.01
                d = Disturbance(force_n=(0, 0, 0), application_point_m=(0, 0, 0),
                                free_torque_nm=torque, source="assumed explicit couple")
                np.testing.assert_allclose(tws.task(disturbances=(d,)).scenarios[0].required_contact_wrench[3:], -torque)

    @classmethod
    def setUpClass(cls):
        cls.rows = sensitivity_rows()

    def rows_for(self, key):
        return [r for r in self.rows if r["parameter"] == key]

    def test_monotonicity_mass_water_capacity_friction_and_disturbance(self):
        for parameter, sign in (("mass_kg", 1), ("water_mass_kg", 1), ("capacity_n", -1),
                                ("friction", -1), ("disturbance_x_n", 1), ("com_x_m", 1)):
            rows = self.rows_for(parameter)
            for r in rows:
                self.assertEqual(r["status"], "optimal")
                self.assertAlmostEqual(r["margin"], 1-r["utilization"])
            self.assertTrue(all(sign*(b["utilization"]-a["utilization"]) >= -1e-8 for a, b in zip(rows, rows[1:])))

    def test_com_axial_torque_boundary_is_structural_not_numerical_error(self):
        self.assertEqual([r["status"] for r in self.rows_for("com_y_m")], ["optimal", "infeasible"])
        row = self.rows_for("com_y_m")[1]
        self.assertAlmostEqual(row["required_wrench"][3], .001*.28*9.81)
        self.assertIsNone(row["margin"])
        self.assertNotIn("numerical_error", Counter(r["status"] for r in self.rows))

    def test_tilt_changes_are_bounded_finite_cone_discretization_error(self):
        circular = .28*9.81/(2*.5*5)
        for r in self.rows_for("tilt_deg"):
            self.assertEqual(r["status"], "optimal")
            self.assertGreaterEqual(r["utilization"], circular-1e-8)
            self.assertLessEqual(r["utilization"], circular/math.cos(math.pi/16)+1e-8)

    def test_missing_parameters_and_repeatability(self):
        d = numeric.data()
        scenario = tws.task().scenarios[0]
        result = solve_wrench(d, required_wrench_for_primitives(scenario, d))
        self.assertIs(result.status, SolverStatus.MISSING_PHYSICS_PARAMETERS)
        with self.assertRaises(ValueError):
            numeric.data(mu=None)
        repeat = sensitivity_rows()
        self.assertEqual([(r["scenario_id"], r["required_wrench"], r["utilization"]) for r in self.rows],
                         [(r["scenario_id"], r["required_wrench"], r["utilization"]) for r in repeat])

    def test_all_116_candidate_references_can_share_one_task_context(self):
        from tests import test_b_wrench_adapter as fixtures
        from grasp_b.multifamily import generate_multifamily, pruned_config
        from grasp_b.wrench_v2.adapter import audit_candidates
        fixtures.StaticAdapterTests.setUpClass()
        f = fixtures.StaticAdapterTests()
        cs, rs = generate_multifamily(f.model, pruned_config())
        static = audit_candidates(cs, f.obj, f.gripper, records=rs,
            generator_name="grasp_b.multifamily", generator_version=fixtures.VERSION)
        self.assertEqual(len(static["specs"]), 116)
        self.assertEqual({s.reference_point_m for s in static["specs"]}, {tws.task().scenarios[0].reference_point_m})


if __name__ == "__main__":
    unittest.main()
