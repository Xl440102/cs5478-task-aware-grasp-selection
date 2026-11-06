"""Real HiGHS M1 fixtures; no execution outcomes or mock LP optima."""
from dataclasses import replace
import json
import unittest

from grasp_b.wrench_v2.contacts import ContactStatus, FingerID
from grasp_b.wrench_v2.m1 import (M1Config, development_config, development_task_sets,
                                 prepare_contact_spec, score_prepared)
from grasp_b.wrench_v2.solver import NumericalTolerances, SolverStatus
from grasp_b.wrench_v2.tasks import Disturbance
from tests import test_b_wrench_contacts as contacts
from tests import test_b_wrench_constraints as fixtures
from tests import test_b_wrench_tasks as tasks


def score(spec=None, config=None, tws=None):
    return score_prepared(prepare_contact_spec(contacts.spec() if spec is None else spec,
        development_config() if config is None else config), tasks.task(model=tasks.body(mass_kg=.28)) if tws is None else tws)


class M1Tests(unittest.TestCase):
    def test_analytic_margin_and_rank_five_support(self):
        r = score()
        self.assertEqual(r.status, "scored")
        self.assertAlmostEqual(r.worst_utilization, .54936)
        self.assertAlmostEqual(r.score, .45064)
        self.assertTrue(all(x.status is SolverStatus.OPTIMAL for _, x in r.scenario_results))

    def test_capacity_and_mass_monotonicity(self):
        c = development_config()
        larger = replace(c, finger_capacities={f: fixtures.capacity(10.) for f in FingerID})
        self.assertGreaterEqual(score(config=larger).score, score(config=c).score)
        heavier = score(tws=tasks.task(model=tasks.body(mass_kg=.56)))
        self.assertAlmostEqual(heavier.worst_utilization, 2*score().worst_utilization)

    def test_com_torque_and_model_infeasibility_not_solver_error(self):
        r = score(tws=tasks.task(model=tasks.body(mass_kg=.28, com_m=(0, .001, 0))))
        self.assertEqual(r.status, "model_infeasible")
        self.assertIsNone(r.score)
        self.assertIsNone(r.worst_utilization)
        self.assertEqual(r.scenario_results[0][1].status, SolverStatus.INFEASIBLE)
        self.assertAlmostEqual(r.scenario_results[0][1].requested_wrench[3], .0027468)

    def test_infeasible_scenario_does_not_stop_diagnostic_evaluation(self):
        twist = Disturbance(force_n=(0, 0, 0), application_point_m=(0, 0, 0),
                            free_torque_nm=(.01, 0, 0), source="unsupported axial torque fixture")
        tws = tasks.task(model=tasks.body(mass_kg=.28), disturbances=(twist, tasks.ZERO_DISTURBANCE))
        r = score(tws=tws)
        self.assertEqual(r.status, "model_infeasible")
        self.assertEqual([x.status for _, x in r.scenario_results], [SolverStatus.INFEASIBLE, SolverStatus.OPTIMAL])
        self.assertIsNone(r.score)

    def test_object_model_hash_mismatch_is_not_a_valid_score(self):
        s = replace(contacts.spec(), provenance={"object_asset_sha256": "a"*64})
        b = tasks.body(mass_kg=.28, provenance={"asset_sha256": "b"*64})
        self.assertEqual(score(spec=s, tws=tasks.task(model=b)).status, "invalid_input")

    def test_unknown_friction_and_capacity_do_not_get_hidden_defaults(self):
        config = development_config()
        for c in (replace(config, friction=None), replace(config, finger_capacities={}),
                  replace(config, require_local_limits=True)):
            r = score(config=c)
            self.assertEqual(r.status, "missing_parameters")
            self.assertIsNone(r.score)
            self.assertFalse(r.scenario_results)

    def test_all_geometry_statuses_remain_distinct_and_approximate_is_retained(self):
        for status, expected in ((ContactStatus.INVALID, "geometry_invalid"),
                                 (ContactStatus.UNSUPPORTED, "geometry_unsupported"),
                                 (ContactStatus.UNKNOWN, "geometry_unknown")):
            s = replace(contacts.spec(), status=status, reasons=("fixture diagnostic",), contacts=())
            self.assertEqual(score(spec=s).status, expected)
        s = replace(contacts.spec(), status=ContactStatus.APPROXIMATE, reasons=("declared approximation",))
        r = score(spec=s)
        self.assertEqual((r.status, r.geometry_status), ("scored", "approximate"))

    def test_scenario_order_and_repeated_runs_leave_score_unchanged(self):
        ds = tasks.axis_disturbances(horizontal_force_n=.1, vertical_force_n=0,
                                    application_point_m=(0, 0, 0), source="fixture")
        tws = tasks.task(model=tasks.body(mass_kg=.28), disturbances=ds)
        a, b = score(tws=tws), score(tws=replace(tws, scenarios=tuple(reversed(tws.scenarios))))
        self.assertEqual(a.worst_utilization, b.worst_utilization)
        self.assertEqual(a.worst_scenario_ids, b.worst_scenario_ids)
        self.assertEqual(a.score, score(tws=tws).score)

    def test_different_tasks_can_have_different_load_scores_without_arbitrary_rules(self):
        c = development_config()
        sets = development_task_sets(tasks.body(mass_kg=.28), c)
        p = prepare_contact_spec(contacts.spec(), c)
        a = score_prepared(p, sets["transport_place"])
        b = score_prepared(p, sets["pour_water"])
        self.assertEqual((a.status, b.status), ("scored", "scored"))
        self.assertNotEqual(a.score, b.score)
        self.assertEqual(a.score, score_prepared(p, sets["handover_transfer"]).score)

    def test_partial_real_numerical_error_never_becomes_partial_max_score(self):
        # Deliberately unrealistic strict VALIDATION tolerance exercises a
        # real backend result rejected for roundoff; no fake LP reply is used.
        config = replace(development_config(), tolerances=NumericalTolerances(relative=1e-30))
        cancel = Disturbance(force_n=(0, 0, .28*9.81), application_point_m=(0, 0, 0), source="analytical gravity cancellation fixture")
        tws = tasks.task(model=tasks.body(mass_kg=.28), disturbances=(tasks.ZERO_DISTURBANCE, cancel))
        r = score(config=config, tws=tws)
        self.assertEqual(r.status, "numerical_error")
        self.assertEqual(len(r.scenario_results), 2)
        self.assertEqual({x.status for _, x in r.scenario_results}, {SolverStatus.NUMERICAL_ERROR, SolverStatus.OPTIMAL})
        self.assertIsNone(r.score)
        self.assertIsNone(r.worst_utilization)

    def test_reference_and_unsupported_stage_do_not_score(self):
        self.assertEqual(score(config=replace(development_config(), reference_point_m=(.1, 0, 0))).status, "invalid_input")
        self.assertEqual(score(tws=tasks.task(reference_point_m=(.1, 0, 0))).status, "invalid_input")
        self.assertEqual(score(tws=tasks.task(stage="release")).status, "task_unsupported")
        prepared = prepare_contact_spec(contacts.spec(), development_config())
        mismatched = replace(prepared, spec=replace(prepared.spec, candidate_id="another_candidate"))
        self.assertEqual(score_prepared(mismatched, tasks.task()).status, "invalid_input")

    def test_provenance_serialization_and_explicit_physics_configuration(self):
        r = score()
        data = r.to_dict()
        json.dumps(data, allow_nan=False)
        self.assertEqual(data["method_id"], "m1_gws_v2")
        self.assertFalse(r.provenance["execution_outcomes_used"])
        self.assertEqual(r.provenance["config"]["friction"]["coefficient"], .5)
        self.assertFalse(r.provenance["config"]["friction"]["calibrated"])
        with self.assertRaises(TypeError):
            r.provenance["config"]["name"] = "changed"
        with self.assertRaises(ValueError):
            replace(development_config(), k=7)
        with self.assertRaises(ValueError):
            replace(r, score=99.)


if __name__ == "__main__":
    unittest.main()
