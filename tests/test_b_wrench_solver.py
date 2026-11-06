"""Separate interface mocks/analytic zero checks from optional REAL HiGHS tests.

Mock replies are hand-authored fixtures, never actual solved LP evidence.
All real solver tests skip explicitly when SciPy is absent; no dependencies
are installed. Every capacity/friction used below is a numerical assumption.
"""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from grasp_b.wrench_v2.constraints import build_capacity_constraints
from grasp_b.wrench_v2.gws import FrictionValue, WrenchScaling, rank_diagnostics
from grasp_b.wrench_v2.solver import (
    BackendReply, NumericalTolerances, SciPyHighsBackend, SolverStatus, build_lp_problem, solve_wrench,
)
from tests import test_b_wrench_constraints as fixtures
from tests import test_b_wrench_contacts as contact_fixtures

SCIPY_AVAILABLE = importlib.util.find_spec("scipy") is not None
BUDGETS = {"left": fixtures.capacity(1.), "right": fixtures.capacity(1.)}


class MockBackend:
    """Returns a fixture reply, not an optimizer."""
    def __init__(self, status=SolverStatus.OPTIMAL, x=None, objective=None):
        self.calls = []
        self.reply = BackendReply(status, x, objective, "Hand-authored reply; no LP solved.",
                                  "mock_fixture", "test_only", {"mock": True, "lp_called": False})

    def solve(self, problem, tolerances):
        self.calls.append((problem, tolerances))
        return self.reply


def normal_fixture():
    d = fixtures.data(mu=FrictionValue(coefficient=0., source="zero_mu_numerical_fixture"))
    c = build_capacity_constraints(d, finger_budgets={"left": fixtures.capacity(4.), "right": fixtures.capacity(2.)})
    return d, c


class MockAndAnalyticSolverTests(unittest.TestCase):
    def test_problem_has_lambda_then_u_and_shared_capacity_rows(self):
        d, c = normal_fixture()
        p = build_lp_problem(d, c, (2., 0., 0., 0., 0., 0.))
        np.testing.assert_array_equal(p.objective, [0., 0., 1.])
        np.testing.assert_array_equal(p.inequality_matrix, [[1., 0., -4.], [0., 1., -2.]])
        np.testing.assert_array_equal(p.inequality_rhs, [0., 0.])
        self.assertEqual(p.bounds, ((0., None),)*3)
        self.assertEqual(p.equality_matrix.shape, (6, 3))

    def test_mock_primal_residuals_and_utilization_not_real_lp_evidence(self):
        d, c = normal_fixture()
        mock = MockBackend(x=(0., 2., 1.), objective=1.)
        result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c, backend=mock)
        self.assertIs(result.status, SolverStatus.OPTIMAL)
        self.assertEqual(result.utilization, 1.)
        self.assertTrue(result.feasible_at_declared_capacity)
        self.assertTrue(result.diagnostics["mock"])
        self.assertFalse(result.diagnostics["lp_called"])
        np.testing.assert_array_equal(result.equilibrium_residual, np.zeros(6))
        self.assertEqual(len(mock.calls), 1)
        excessive = solve_wrench(d, (4., 0., 0., 0., 0., 0.), constraints=c,
                                 backend=MockBackend(x=(0., 4., 2.), objective=2.))
        self.assertIs(excessive.status, SolverStatus.OPTIMAL)
        self.assertFalse(excessive.feasible_at_declared_capacity)

    def test_zero_wrench_is_proven_analytically_without_backend(self):
        d, c = normal_fixture()
        mock = MockBackend()
        result = solve_wrench(d, np.zeros(6), constraints=c, backend=mock)
        self.assertIs(result.status, SolverStatus.OPTIMAL)
        self.assertEqual(result.utilization, 0.)
        self.assertEqual(result.backend_name, "analytic_zero")
        self.assertFalse(mock.calls)
        self.assertFalse(result.diagnostics["lp_called"])

    def test_missing_physics_is_not_filled_even_for_zero_demand(self):
        d = fixtures.data()
        mock = MockBackend()
        for w in (np.zeros(6), np.ones(6)):
            result = solve_wrench(d, w, backend=mock)
            self.assertIs(result.status, SolverStatus.MISSING_PHYSICS_PARAMETERS)
            self.assertIsNone(result.utilization)
            self.assertEqual(len(result.assumptions["missing_parameters"]), 2)
        self.assertFalse(mock.calls)

    def test_explicit_normalization_applies_to_both_equilibrium_sides_only(self):
        d, c = normal_fixture()
        scaling = WrenchScaling(reference_force_n=2., characteristic_length_m=.1)
        mock = MockBackend(x=(0., 2., 1.), objective=1.)
        result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c, backend=mock, scaling=scaling)
        p = mock.calls[0][0]
        self.assertEqual(p.equality_rhs[0], 1.)
        self.assertEqual(p.equality_matrix[0, 1], .5)
        np.testing.assert_array_equal(p.inequality_matrix[:, -1], [-4., -2.])
        self.assertIs(result.status, SolverStatus.OPTIMAL)
        self.assertEqual(result.normalization, scaling)
        self.assertEqual(result.requested_wrench[0], 2.)

    def test_optimal_status_cannot_override_bad_primal_bounds_or_objective(self):
        d, c = normal_fixture()
        for x, objective in (((0., 1., 1.), 1.), ((0., 2., .1), .1),
                             ((-1., 1., 1.), 1.), ((0., 2., -1.), 0.),
                             ((0., 2., 1.), 999.), ((0., float("nan"), 1.), 1.),
                             ((0., 2., 1.), None)):
            with self.subTest(x=x, objective=objective):
                result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c,
                                      backend=MockBackend(x=x, objective=objective))
                self.assertIs(result.status, SolverStatus.NUMERICAL_ERROR)
                self.assertIsNone(result.utilization)

    def test_infeasible_status_conflicting_with_feasible_primal_is_numerical_error(self):
        d, c = normal_fixture()
        result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c,
            backend=MockBackend(SolverStatus.INFEASIBLE, x=(0., 2., 1.)))
        self.assertIs(result.status, SolverStatus.NUMERICAL_ERROR)

    def test_mock_nonoptimal_statuses_remain_distinct(self):
        d, c = normal_fixture()
        for status in (SolverStatus.INFEASIBLE, SolverStatus.UNBOUNDED,
                       SolverStatus.NUMERICAL_ERROR, SolverStatus.DEPENDENCY_MISSING):
            with self.subTest(status=status):
                result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c, backend=MockBackend(status))
                self.assertIs(result.status, status)
                self.assertIsNone(result.utilization)
                if status is SolverStatus.UNBOUNDED:
                    self.assertIn("unexpected_unbounded_report", result.diagnostics)

    def test_backend_exception_is_not_physical_infeasibility(self):
        d, c = normal_fixture()
        class FailingBackend:
            def solve(self, *args):
                raise RuntimeError("fixture failure")
        result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c, backend=FailingBackend())
        self.assertIs(result.status, SolverStatus.NUMERICAL_ERROR)
        self.assertTrue(result.diagnostics["backend_attempted"])

    def test_bad_input_stale_constraints_and_nonfinite_wrenches_rejected_before_backend(self):
        d, c = normal_fixture()
        mock = MockBackend()
        for w in ((1., 2.), (float("nan"), 0., 0., 0., 0., 0.)):
            self.assertIs(solve_wrench(d, w, constraints=c, backend=mock).status, SolverStatus.INVALID_INPUT)
        self.assertIs(solve_wrench(d, np.zeros(6), constraints=replace(c, candidate_hash="0"*64),
                                  backend=mock).status, SolverStatus.INVALID_INPUT)
        self.assertFalse(mock.calls)

    def test_mock_tangential_and_signed_torque_primal_checks_in_rank_five_model(self):
        d = fixtures.data()
        self.assertEqual(rank_diagnostics(d.matrix, matrix_name="fixture A").numerical_rank, 5)
        for indices, desired in (((6, 10), (0., 0., 1., 0., 0., 0.)),
                                 ((2, 10), (0., 0., 0., 0., .035, 0.)),
                                 ((6, 14), (0., 0., 0., 0., -.035, 0.))):
            coefficients = np.zeros(16)
            coefficients[list(indices)] = 1.
            result = solve_wrench(d, desired, finger_budgets=BUDGETS,
                                  backend=MockBackend(x=(*coefficients, 1.), objective=1.))
            self.assertIs(result.status, SolverStatus.OPTIMAL)

    def test_local_contact_limit_is_checked_in_physical_units(self):
        d, _ = normal_fixture()
        good = solve_wrench(d, (2., 0., 0., 0., 0., 0.), finger_budgets=BUDGETS,
                            contact_limits={1: fixtures.capacity(.5)},
                            backend=MockBackend(x=(0., 2., 4.), objective=4.))
        self.assertIs(good.status, SolverStatus.OPTIMAL)
        bad = solve_wrench(d, (2., 0., 0., 0., 0., 0.), finger_budgets=BUDGETS,
                           contact_limits={1: fixtures.capacity(.5)},
                           backend=MockBackend(x=(0., 2., 2.), objective=2.))
        self.assertIs(bad.status, SolverStatus.NUMERICAL_ERROR)

    def test_near_singular_geometry_is_not_rejected_by_rank(self):
        spec = contact_fixtures.spec()
        spec = replace(spec, contacts=(replace(spec.contacts[0], position_m=(0., 0., 0.)),
                                      replace(spec.contacts[1], position_m=(1e-10, 0., 0.))))
        d = fixtures.data(mu=FrictionValue(coefficient=0., source="fixture"), spec=spec)
        result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), finger_budgets=BUDGETS,
                              backend=MockBackend(x=(0., 2., 2.), objective=2.))
        self.assertIs(result.status, SolverStatus.OPTIMAL)

    def test_immutable_json_result_and_explicit_assumptions(self):
        d, c = normal_fixture()
        result = solve_wrench(d, np.zeros(6), constraints=c)
        json.dumps(result.to_dict(), allow_nan=False)
        self.assertEqual(result.candidate_hash, d.candidate_hash)
        self.assertEqual(result.assumptions["unknown_local_contacts"], (0, 1))
        with self.assertRaises(FrozenInstanceError):
            result.utilization = 99.
        with self.assertRaises(TypeError):
            result.assumptions["new"] = True

    def test_highs_adapter_arguments_and_status_mapping_with_fake_linprog(self):
        d, c = normal_fixture()
        p = build_lp_problem(d, c, (2., 0., 0., 0., 0., 0.))
        for code, expected in ((0, SolverStatus.OPTIMAL), (1, SolverStatus.NUMERICAL_ERROR),
                               (2, SolverStatus.INFEASIBLE), (3, SolverStatus.UNBOUNDED), (4, SolverStatus.NUMERICAL_ERROR)):
            def fake(*args, **kwargs):
                self.assertEqual(kwargs["method"], "highs")
                self.assertEqual(kwargs["bounds"], ((0., None),)*3)
                self.assertEqual(kwargs["options"]["small_matrix_value"], 1e-12)
                return SimpleNamespace(status=code, success=code == 0, x=(0., 2., 1.) if code == 0 else None,
                                       fun=1. if code == 0 else None, message="mock scipy adapter only", nit=1)
            with patch("grasp_b.wrench_v2.solver._load_highs", return_value=(fake, "mock_scipy_adapter")):
                reply = SciPyHighsBackend().solve(p, NumericalTolerances())
                self.assertIs(reply.status, expected)
                self.assertEqual(reply.metadata["options"]["small_matrix_value"], 1e-12)

    def test_backend_tolerance_limits_reject_silent_highs_option_fallback(self):
        for values in ({"backend_feasibility": 1e-12}, {"backend_small_matrix_value": 1e-14}):
            with self.assertRaises(ValueError):
                NumericalTolerances(**values)

    def test_missing_scipy_status_is_explicit_and_no_install_is_attempted(self):
        d, c = normal_fixture()
        with patch("grasp_b.wrench_v2.solver._load_highs", side_effect=ImportError("fixture scipy missing")):
            result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c)
        self.assertIs(result.status, SolverStatus.DEPENDENCY_MISSING)
        self.assertFalse(result.diagnostics["lp_called"])
        self.assertIsNone(result.utilization)

    def test_absolute_tolerance_cannot_hide_tiny_demand_or_capacity_violation(self):
        d, c = normal_fixture()
        for x in ((0., 0., 0.), (0., 2e-12, 0.)):
            result = solve_wrench(d, (2e-12, 0., 0., 0., 0., 0.), constraints=c,
                                  backend=MockBackend(x=x, objective=0.))
            self.assertIs(result.status, SolverStatus.NUMERICAL_ERROR)
            self.assertIsNone(result.utilization)
            self.assertGreater(max(result.diagnostics["force_relative_backward_error"],
                                   result.diagnostics["inequality_relative_violation"]), .9)


class StaticPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_b_wrench_adapter as geometry_fixtures
        from grasp_b.multifamily import generate_multifamily, pruned_config
        from grasp_b.wrench_v2.adapter import GeneratorInfo, build_contact_spec
        from grasp_b.wrench_v2.contacts import ContactStatus
        geometry_fixtures.StaticAdapterTests.setUpClass()
        f = geometry_fixtures.StaticAdapterTests()
        candidates, records = generate_multifamily(f.model, pruned_config())
        cls.specs = {}
        for candidate, record in zip(candidates, records):
            family = record["family"]
            if family in cls.specs:
                continue
            spec = build_contact_spec(candidate, f.obj, f.gripper, generator_info=GeneratorInfo(
                "grasp_b.multifamily", geometry_fixtures.VERSION, family, candidate.id))
            if spec.status in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
                cls.specs[family] = spec

    def test_four_family_static_pipeline_with_mock_reply_only(self):
        from grasp_b.wrench_v2.gws import build_primitive_wrenches
        self.assertEqual(set(self.specs), {"side", "top", "handle", "rim"})
        for family, spec in self.specs.items():
            with self.subTest(family=family):
                d = build_primitive_wrenches(spec, friction=fixtures.MU)
                budgets = {g.group_id: fixtures.capacity(1.) for g in d.capacity_groups}
                c = build_capacity_constraints(d, finger_budgets=budgets)
                coefficients = np.zeros(len(d.columns))
                coefficients[0] = .5
                w = d.matrix@coefficients
                mock = MockBackend(x=(*coefficients, .5), objective=.5)
                result = solve_wrench(d, w, constraints=c, backend=mock)
                self.assertIs(result.status, SolverStatus.OPTIMAL)
                self.assertEqual(result.candidate_id, spec.candidate_id)
                self.assertEqual(result.candidate_hash, spec.candidate_hash)
                self.assertFalse(result.diagnostics["lp_called"])
                self.assertTrue(result.diagnostics["mock"])
                self.assertLess(max(map(abs, result.equilibrium_residual)), 1e-10)
                # This checks the complete STATIC input chain and primal point;
                # it does not establish that .5 is the actual LP optimum.

    @unittest.skipIf(SCIPY_AVAILABLE, "Only exercises actual missing-dependency environment")
    def test_actual_default_backend_reports_missing_dependency(self):
        d, c = normal_fixture()
        result = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c)
        self.assertIs(result.status, SolverStatus.DEPENDENCY_MISSING)
        self.assertFalse(result.diagnostics["lp_called"])


@unittest.skipUnless(SCIPY_AVAILABLE, "SciPy missing: REAL HiGHS integration not executed")
class RealHighsIntegrationTests(unittest.TestCase):
    """These actually solve LPs when SciPy is available. No mocks in this class."""
    def solve(self, d, w, budgets=None):
        return solve_wrench(d, w, finger_budgets=BUDGETS if budgets is None else budgets)

    def assert_optimal(self, result, utilization=None):
        self.assertIs(result.status, SolverStatus.OPTIMAL, result.message)
        self.assertTrue(result.diagnostics["lp_called"])
        if utilization is not None:
            self.assertAlmostEqual(result.utilization, utilization, delta=1e-6)

    def test_single_normal_and_independent_finger_budgets(self):
        d, c = normal_fixture()
        a = solve_wrench(d, (2., 0., 0., 0., 0., 0.), constraints=c)
        b = solve_wrench(d, (-2., 0., 0., 0., 0., 0.), constraints=c)
        self.assert_optimal(a, 1.)
        self.assert_optimal(b, .5)

    def test_symmetric_tangential_force_and_both_torque_signs(self):
        d = fixtures.data()
        for w, u in (((0., 0., 1., 0., 0., 0.), 1.),
                     ((0., 0., 0., 0., .0175, 0.), .5), ((0., 0., 0., 0., -.0175, 0.), .5)):
            self.assert_optimal(self.solve(d, w), u)

    def test_rank_five_reachable_and_axial_torque_unreachable(self):
        d = fixtures.data()
        self.assert_optimal(self.solve(d, (0., 0., 1., 0., 0., 0.)))
        self.assertIs(self.solve(d, (0., 0., 0., .01, 0., 0.)).status, SolverStatus.INFEASIBLE)

    def test_frictionless_target_outside_cone_is_infeasible(self):
        d, c = normal_fixture()
        result = solve_wrench(d, (0., 0., 1., 0., 0., 0.), constraints=c)
        self.assertIs(result.status, SolverStatus.INFEASIBLE)
        self.assertIsNone(result.utilization)

    def test_nonzero_friction_cone_outside_target_is_infeasible(self):
        spec = contact_fixtures.spec()
        # Two declared numerical contacts pushing +x; both tangent demands
        # cancel their moment arms. Schema fixture, not a generated grasp.
        same_normal = replace(spec, contacts=tuple(replace(p, normal_outward=(-1., 0., 0.))
                                                  for p in spec.contacts))
        d = fixtures.data(spec=same_normal)
        self.assert_optimal(self.solve(d, (1., .25, 0., 0., 0., 0.)), .5)
        self.assertIs(self.solve(d, (1., .6, 0., 0., 0., 0.)).status, SolverStatus.INFEASIBLE)

    def test_capacity_monotonicity_and_wrench_homogeneity(self):
        d = fixtures.data()
        w = np.array((0., 0., 1., 0., 0., 0.))
        a = self.solve(d, w)
        bigger = self.solve(d, w, {"left": fixtures.capacity(2.), "right": fixtures.capacity(2.)})
        scaled = self.solve(d, 2.5*w)
        for result in (a, bigger, scaled):
            self.assert_optimal(result)
        self.assertAlmostEqual(bigger.utilization, a.utilization/2, delta=1e-6)
        self.assertAlmostEqual(scaled.utilization, a.utilization*2.5, delta=1e-6)

    def test_friction_and_nested_k_monotonicity(self):
        w = (0., .3, .7, 0., 0., 0.)
        values = [self.solve(fixtures.data(k=k), w) for k in (8, 16, 32)]
        for result in values:
            self.assert_optimal(result)
        self.assertLessEqual(values[1].utilization, values[0].utilization+1e-6)
        self.assertLessEqual(values[2].utilization, values[1].utilization+1e-6)
        wider = self.solve(fixtures.data(mu=FrictionValue(coefficient=.8, source="fixture_mu")), w)
        self.assert_optimal(wider)
        self.assertLessEqual(wider.utilization, values[0].utilization+1e-6)

    def test_duplicate_sampling_does_not_increase_shared_budget(self):
        spec = contact_fixtures.spec()
        duplicated = replace(spec, contacts=(*spec.contacts, replace(spec.contacts[0], surface_id="duplicate_fixture")))
        w = (0., 0., 1., 0., 0., 0.)
        a, b = self.solve(fixtures.data(), w), self.solve(fixtures.data(spec=duplicated), w)
        self.assert_optimal(a)
        self.assert_optimal(b)
        self.assertAlmostEqual(a.utilization, b.utilization, delta=1e-6)

    def test_local_limit_and_zero_group_capacity(self):
        d, _ = normal_fixture()
        local = solve_wrench(d, (2., 0., 0., 0., 0., 0.), finger_budgets=BUDGETS,
                             contact_limits={1: fixtures.capacity(.5)})
        self.assert_optimal(local, 4.)
        blocked = self.solve(d, (2., 0., 0., 0., 0., 0.),
                             {"left": fixtures.capacity(0), "right": fixtures.capacity(0)})
        self.assertIs(blocked.status, SolverStatus.INFEASIBLE)

    def test_rank_six_same_hemisphere_does_not_allow_negative_force(self):
        spec = contact_fixtures.spec()
        a, b = spec.contacts
        contacts = (replace(a, position_m=(0., 0., 0.), normal_outward=(-1., 0., 0.)),
                    replace(b, position_m=(0., .02, 0.), normal_outward=(-1., 0., 0.)),
                    replace(a, position_m=(0., 0., .02), normal_outward=(-1., 0., 0.), surface_id="third"))
        d = fixtures.data(spec=replace(spec, contacts=contacts))
        self.assertEqual(rank_diagnostics(d.matrix, matrix_name="rank6 fixture").numerical_rank, 6)
        self.assertIs(self.solve(d, (-1., 0., 0., 0., 0., 0.)).status, SolverStatus.INFEASIBLE)

    def test_main_geometry_to_real_highs_with_explicit_assumptions(self):
        from grasp_b.wrench_v2.gws import build_primitive_wrenches
        StaticPipelineTests.setUpClass()
        for spec in StaticPipelineTests.specs.values():
            d = build_primitive_wrenches(spec, friction=fixtures.MU)
            result = self.solve(d, d.matrix[:, 0]*.5)
            self.assert_optimal(result)
            self.assertLessEqual(result.utilization, .5+1e-6)

    def test_decreasing_all_effective_capacities_increases_utilization(self):
        d, _ = normal_fixture()
        w = (2., 0., 0., 0., 0., 0.)
        results = [solve_wrench(d, w,
            finger_budgets={"left": fixtures.capacity(scale), "right": fixtures.capacity(scale)},
            contact_limits={1: fixtures.capacity(.5*scale)}) for scale in (.5, 1., 2.)]
        for result, expected in zip(results, (8., 4., 2.)):
            self.assert_optimal(result, expected)

    def test_finite_friction_polygon_error_has_known_analytic_bound(self):
        # Unit tangential demand halfway between k=8 rays. mu=.5 and two
        # unit finger budgets: circular-cone optimum=1; inscribed k=8 gives
        # sec(pi/8). k=16/32 include this direction exactly and give 1.
        angle = np.pi/8
        w = (0., float(np.cos(angle)), float(np.sin(angle)), 0., 0., 0.)
        for k, expected in ((8, 1/np.cos(angle)), (16, 1.), (32, 1.)):
            self.assert_optimal(self.solve(fixtures.data(k=k), w), expected)

    def test_normal_demand_scale_homogeneity_and_relative_residuals(self):
        d, c = normal_fixture()
        for alpha in (1e-6, 1e-3, 1., 1e3, 1e6, 1e9, 1e12):
            result = solve_wrench(d, (2*alpha, 0., 0., 0., 0., 0.), constraints=c)
            self.assert_optimal(result)
            self.assertLessEqual(abs(result.utilization-alpha)/alpha, 1e-8)
            for name in ("force_relative_backward_error", "torque_relative_backward_error",
                         "inequality_relative_violation", "nonnegativity_relative_violation"):
                self.assertLessEqual(result.diagnostics[name], 1e-8)

    def test_sub_tolerance_demand_cannot_silently_return_zero_utilization(self):
        d, c = normal_fixture()
        result = solve_wrench(d, (2e-12, 0., 0., 0., 0., 0.), constraints=c)
        # Different HiGHS versions may solve accurately or fail the independent
        # validation. A false optimal u=0 must never escape either way.
        self.assertIn(result.status, (SolverStatus.OPTIMAL, SolverStatus.NUMERICAL_ERROR))
        if result.status is SolverStatus.OPTIMAL:
            self.assertLess(abs(result.utilization-1e-12)/1e-12, 1e-8)
        else:
            self.assertIsNone(result.utilization)

    def test_near_degenerate_torque_requires_resolution_not_jitter(self):
        spec = contact_fixtures.spec()
        distance = 1e-9
        near = replace(spec, contacts=(replace(spec.contacts[0], position_m=(distance, 0., 0.)),
                                       replace(spec.contacts[1], position_m=(-distance, 0., 0.))))
        d = fixtures.data(spec=near)
        w = (0., 0., 0., 0., distance*.5, 0.)
        raw = self.solve(d, w)
        self.assertIn(raw.status, (SolverStatus.OPTIMAL, SolverStatus.NUMERICAL_ERROR))
        if raw.status is SolverStatus.OPTIMAL:
            self.assertAlmostEqual(raw.utilization, .5, delta=1e-6)
        normalized = solve_wrench(d, w, finger_budgets=BUDGETS,
            scaling=WrenchScaling(reference_force_n=1., characteristic_length_m=distance))
        self.assert_optimal(normalized, .5)
        self.assertLessEqual(normalized.diagnostics["torque_relative_backward_error"], 1e-8)

    def test_116_pool_two_static_known_feasible_wrenches_per_usable_candidate(self):
        from tests import test_b_wrench_adapter as geometry_fixtures
        from grasp_b.multifamily import generate_multifamily, pruned_config
        from grasp_b.wrench_v2.adapter import audit_candidates
        from grasp_b.wrench_v2.contacts import ContactStatus
        from grasp_b.wrench_v2.gws import build_primitive_wrenches
        geometry_fixtures.StaticAdapterTests.setUpClass()
        f = geometry_fixtures.StaticAdapterTests()
        candidates, records = generate_multifamily(f.model, pruned_config())
        static = audit_candidates(candidates, f.obj, f.gripper, records=records,
            generator_name="grasp_b.multifamily", generator_version=geometry_fixtures.VERSION)
        self.assertEqual(len(static["specs"]), 116)
        solved, skipped = 0, 0
        for spec in static["specs"]:
            if spec.status not in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
                skipped += 1
                continue
            d = build_primitive_wrenches(spec, friction=fixtures.MU, k=16)
            budgets = {g.group_id: fixtures.capacity(1.) for g in d.capacity_groups}
            constraints = build_capacity_constraints(d, finger_budgets=budgets)
            single = np.zeros(len(d.columns))
            single[0] = .5
            mixed = np.zeros(len(d.columns))
            for j, column in enumerate(d.columns):
                # One ray per contact; each finger's total is exactly .4 N.
                if column.friction_direction_index == 0:
                    same_finger = {c.contact_index for c in d.columns
                                   if c.capacity_group_id == column.capacity_group_id}
                    mixed[j] = .4/len(same_finger)
            for coefficients in (single, mixed):
                with self.subTest(candidate_id=spec.candidate_id):
                    w = d.matrix@coefficients
                    self.assertGreater(np.max(np.abs(w)), 0.)
                    upper_bound = float(np.max(constraints.matrix@coefficients/constraints.bounds_n))
                    result = solve_wrench(d, w, constraints=constraints)
                    self.assert_optimal(result)
                    self.assertLessEqual(result.utilization, upper_bound+1e-7)
                    solved += 1
        self.assertEqual((solved, skipped), (210, 11))


if __name__ == "__main__":
    unittest.main()
