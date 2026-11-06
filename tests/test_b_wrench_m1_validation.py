"""04V real HiGHS recovery and geometric (not outcome-selected) ablations."""
from dataclasses import replace
import unittest

import numpy as np

from grasp_b.wrench_v2.contacts import ContactStatus, GraspFamily, ContactMechanism
from grasp_b.wrench_v2.gws import grasp_matrix, WrenchScaling
from grasp_b.wrench_v2.m1 import development_config, development_task_sets, prepare_contact_spec, score_prepared
from grasp_b.wrench_v2.m1_validation import two_point_spec, matrix_diagnostics
from grasp_b.wrench_v2.ranking import rank_results
from grasp_b.wrench_v2.solver import SciPyHighsBackend, solve_wrench, SolverStatus, _primal_check, NumericalTolerances
from tests import test_b_wrench_contacts as fixtures
from tests import test_b_wrench_tasks as tasks


def minimal_unknown_model():
    """Two predicted points from B3_178b0b42e93bcffb, no episode data.

    Retain only positions/normals and shared budgets to reproduce the 6x32 LP.
    This is a static numerical fixture, not a verified physical handle grasp.
    """
    spec = fixtures.spec()
    positions = ((.006, .06958125259935674, .0253755),
                 (-.006, .05910175259935674, .0253755))
    spec = replace(spec, family=GraspFamily.HANDLE, mechanism=ContactMechanism.HANDLE_SIDE,
        status=ContactStatus.APPROXIMATE, reasons=("minimal static Unknown reproduction",),
        contacts=tuple(replace(p, position_m=xyz) for p, xyz in zip(spec.contacts, positions)))
    return prepare_contact_spec(spec, development_config())


class SolverRecoveryTests(unittest.TestCase):
    def test_minimal_unknown_repro_old_and_bounded_recovery(self):
        p = minimal_unknown_model()
        w = (0, 0, .28*9.81, 0, 0, 0)
        old = solve_wrench(p.primitives, w, constraints=p.constraints,
                           backend=SciPyHighsBackend(retry_unknown=False))
        new = solve_wrench(p.primitives, w, constraints=p.constraints)
        self.assertIn(old.status, (SolverStatus.NUMERICAL_ERROR, SolverStatus.INFEASIBLE))
        self.assertEqual(new.status, SolverStatus.INFEASIBLE)
        self.assertIsNone(new.utilization)
        if old.status is SolverStatus.NUMERICAL_ERROR:
            self.assertEqual(old.diagnostics["raw_status"], 4)
            self.assertEqual(new.diagnostics["linprog_calls"], 2)
            self.assertEqual([a["method"] for a in new.diagnostics["attempts"]], ["highs", "highs-ipm"])
        diag = matrix_diagnostics(p.primitives, p.constraints, w)
        self.assertEqual(diag["scaled_rank"], 5)
        self.assertTrue(diag["outside_span"])
        self.assertLess(diag["witness_A_error"], 1e-12)
        self.assertGreater(diag["witness_w"], .1)

    def test_backend_and_presolve_consistency_for_known_feasible_and_infeasible(self):
        p = prepare_contact_spec(fixtures.spec(), development_config())
        for method in ("highs", "highs-ds", "highs-ipm"):
            for presolve in (True, False):
                backend = SciPyHighsBackend(method=method, presolve=presolve, retry_unknown=False)
                r = solve_wrench(p.primitives, (0, 0, 1., 0, 0, 0), constraints=p.constraints, backend=backend)
                self.assertEqual(r.status, SolverStatus.OPTIMAL)
                self.assertAlmostEqual(r.utilization, .2)
                self.assertLess(r.diagnostics["force_residual_max_n"], 1e-9)
                r = solve_wrench(p.primitives, (0, 0, 0, .001, 0, 0), constraints=p.constraints, backend=backend)
                self.assertEqual(r.status, SolverStatus.INFEASIBLE)

    def test_recovery_does_not_change_residual_tolerances(self):
        p = minimal_unknown_model()
        r = solve_wrench(p.primitives, (0, 0, .28*9.81, 0, 0, 0), constraints=p.constraints)
        for attempt in r.diagnostics["attempts"]:
            self.assertEqual(attempt["options"]["primal_feasibility_tolerance"], 1e-9)
            self.assertEqual(attempt["options"]["small_matrix_value"], 1e-12)
        self.assertEqual(r.tolerances.relative, 1e-8)
        for invalid in (dict(method="other"), dict(presolve=1), dict(retry_unknown="yes")):
            with self.assertRaises(ValueError):
                SciPyHighsBackend(**invalid)

    def test_m1_status_and_backend_call_count_after_recovery(self):
        r = score_prepared(minimal_unknown_model(), tasks.task(model=tasks.body(mass_kg=.28)))
        self.assertEqual(r.status, "model_infeasible")
        self.assertIsNone(r.score)
        self.assertGreaterEqual(r.timings_s["lp_calls"], r.timings_s["scenario_evaluations"])
        ranked = rank_results([r], [r.candidate_id])
        self.assertEqual(ranked["ranking"], [r.candidate_id])
        self.assertIsNone(ranked["selected_id"])


class ContactAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_b_wrench_adapter as geometry
        from grasp_b.multifamily import generate_multifamily, pruned_config
        from grasp_b.wrench_v2.adapter import build_contact_spec, GeneratorInfo
        geometry.StaticAdapterTests.setUpClass()
        f = geometry.StaticAdapterTests()
        cs, records = generate_multifamily(f.model, pruned_config())
        cls.geometry_fixture, cls.candidates, cls.records = f, cs, records
        for c, record in zip(cs, records):
            if record["family"] != "side":
                continue
            s = build_contact_spec(c, f.obj, f.gripper,
                generator_info=GeneratorInfo("grasp_b.multifamily", geometry.VERSION, record["family"], c.id))
            if s.status is ContactStatus.APPROXIMATE and len(s.contacts) == 4:
                cls.spec, cls.candidate = s, c
                break
        else:
            raise AssertionError("no static Body four-contact fixture")

    def test_geometric_subset_not_averaged_normals_and_capacity_groups_unchanged(self):
        reduced = two_point_spec(self.spec, self.candidate)
        self.assertEqual(len(reduced.contacts), 2)
        self.assertTrue(all(p in self.spec.contacts for p in reduced.contacts))
        self.assertEqual(reduced.capacity_groups, self.spec.capacity_groups)
        reversed_spec = replace(self.spec, contacts=tuple(reversed(self.spec.contacts)))
        self.assertEqual(reduced.contacts, two_point_spec(reversed_spec, self.candidate).contacts)
        with self.assertRaises(ValueError):
            two_point_spec(self.spec, replace(self.candidate, id="wrong"))

    def test_rank_and_axial_torque_difference_under_same_budget(self):
        reduced = two_point_spec(self.spec, self.candidate)
        p4, p2 = [prepare_contact_spec(s, development_config()) for s in (self.spec, reduced)]
        self.assertEqual(np.linalg.matrix_rank(grasp_matrix(self.spec)), 6)
        self.assertEqual(np.linalg.matrix_rank(grasp_matrix(reduced)), 5)
        np.testing.assert_array_equal(p4.constraints.bounds_n, p2.constraints.bounds_n)
        direction = np.array(reduced.contacts[0].position_m)-reduced.contacts[1].position_m
        direction /= np.linalg.norm(direction)
        for sign in (-1, 1):
            w = np.concatenate((np.zeros(3), sign*.001*direction))
            a = solve_wrench(p4.primitives, w, constraints=p4.constraints)
            b = solve_wrench(p2.primitives, w, constraints=p2.constraints)
            self.assertEqual(a.status, SolverStatus.OPTIMAL)
            self.assertEqual(b.status, SolverStatus.INFEASIBLE)

    def test_duplicate_sampling_does_not_create_patch_rank_or_extra_budget(self):
        s = fixtures.spec()
        duplicate = replace(s, contacts=tuple(s.contacts)+tuple(replace(p, surface_id=p.surface_id+":duplicate") for p in s.contacts))
        self.assertEqual(np.linalg.matrix_rank(grasp_matrix(duplicate)), 5)
        for spec in (s, duplicate):
            p = prepare_contact_spec(spec, development_config())
            r = solve_wrench(p.primitives, (0, 0, 1, 0, 0, 0), constraints=p.constraints)
            self.assertAlmostEqual(r.utilization, .2)
            self.assertEqual(solve_wrench(p.primitives, (0, 0, 0, .001, 0, 0), constraints=p.constraints).status, SolverStatus.INFEASIBLE)

    def test_transport_handover_load_equivalence_but_not_scenario_identity(self):
        cfg = development_config()
        sets = development_task_sets(tasks.body(mass_kg=.28), cfg)
        a, b = sets["transport_place"], sets["handover_transfer"]
        self.assertEqual([s.required_contact_wrench for s in a.scenarios], [s.required_contact_wrench for s in b.scenarios])
        self.assertFalse({s.scenario_id for s in a.scenarios}&{s.scenario_id for s in b.scenarios})

    def test_wider_cone_residual_error_is_not_physical_infeasibility(self):
        from grasp_b.wrench_v2.adapter import build_contact_spec, GeneratorInfo
        from grasp_b.wrench_v2.gws import FrictionValue
        from grasp_b.multifamily import VERSION
        candidate = next(c for c in self.candidates if c.id == "B3_db2df20cfd3cb7be")
        s = build_contact_spec(candidate, self.geometry_fixture.obj, self.geometry_fixture.gripper,
            generator_info=GeneratorInfo("grasp_b.multifamily", VERSION, "side", candidate.id))
        cfg = development_config()
        old = prepare_contact_spec(s, cfg)
        wider = prepare_contact_spec(s, replace(cfg, friction=FrictionValue(coefficient=.75, source="04V uncalibrated mu sensitivity fixture")))
        sets = development_task_sets(tasks.body(mass_kg=.28), cfg)
        failures = []
        for scenario in sets["transport_place"].scenarios:
            result = solve_wrench(wider.primitives, scenario.required_contact_wrench, constraints=wider.constraints)
            if result.status is not SolverStatus.NUMERICAL_ERROR:
                continue
            failures.append(result)
            self.assertEqual(result.diagnostics["raw_status"], 0)
            self.assertIsNone(result.utilization)
            narrower = solve_wrench(old.primitives, scenario.required_contact_wrench, constraints=old.constraints)
            self.assertEqual(narrower.status, SolverStatus.OPTIMAL)
            # Construct a feasible wider-cone point by convexly mixing each
            # ray with the inward normal (mean of all k rays). No LP invented.
            previous = np.array(narrower.coefficients_n)
            coefficients = previous*(.5/.75)
            for i in range(len(s.contacts)):
                indices = [j for j, c in enumerate(wider.primitives.columns) if c.contact_index == i]
                coefficients[indices] += (1-.5/.75)*sum(previous[indices])/len(indices)
            # Preserve the old solver point's rounding residual; do not demand
            # more accuracy from cone interpolation than that point had.
            np.testing.assert_allclose(wider.primitives.matrix@coefficients, old.primitives.matrix@previous, atol=1e-12)
            valid, *_ = _primal_check(wider.primitives, wider.constraints, np.array(scenario.required_contact_wrench),
                                     np.r_[coefficients, narrower.utilization], NumericalTolerances())
            self.assertTrue(valid)  # unchanged physical AND relative tolerances
            self.assertLessEqual(max(wider.constraints.matrix@coefficients/wider.constraints.bounds_n), narrower.utilization+1e-12)
            # 04W bounded follow-up: explicitly declared equality scaling and
            # TIGHTER internal solve tolerance. No change to physical residual
            # acceptance, capacity, mu, reference point or default M1 policy.
            normalized = solve_wrench(wider.primitives, scenario.required_contact_wrench,
                constraints=wider.constraints,
                scaling=WrenchScaling(reference_force_n=1., characteristic_length_m=.1),
                tolerances=NumericalTolerances(backend_feasibility=1e-10))
            self.assertEqual(normalized.status, SolverStatus.OPTIMAL)
            self.assertAlmostEqual(normalized.utilization, .36889216485916393, delta=1e-8)
            self.assertEqual(normalized.diagnostics["nonnegativity_violation_n"], 0.)
            self.assertLess(normalized.diagnostics["force_residual_max_n"], 1e-12)
            self.assertLess(normalized.diagnostics["torque_residual_max_nm"], 1e-12)
            self.assertEqual(normalized.tolerances.relative, result.tolerances.relative)
            self.assertEqual(normalized.tolerances.coefficient_absolute_n, result.tolerances.coefficient_absolute_n)
        self.assertTrue(failures, "expected SciPy 1.17.1 residual regression fixture")


if __name__ == "__main__":
    unittest.main()
