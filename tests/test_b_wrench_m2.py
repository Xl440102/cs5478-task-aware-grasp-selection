"""Real LP tests on explicitly declared analytical fixtures, never episodes."""
from dataclasses import replace
import json
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_b.wrench_v2.m1 import development_config, prepare_contact_spec, score_prepared
from grasp_b.wrench_v2.m2 import score_trajectory, TrajectoryPolicy, ExactWrenchCache, rank_m2_results, STATIC_METHOD_ID
from grasp_b.wrench_v2.motion_prediction import PredictedPose, PredictedStage, MotionModel, predict_motion, sample_stage, compose
from grasp_b.wrench_v2.dynamics import predict_dynamics
from grasp_b.wrench_v2.tasks import ObjectParameters, build_task_wrench_set
from grasp_b.wrench_v2.solver import NumericalTolerances
from tests import test_b_wrench_contacts as contacts
from tests import test_b_wrench_constraints as capacities

ASSET = "f"*64


def fixture(*, n=16, duration=1., displacement=.1, mass=.28, inertia=True, task="transport_place", cfg=None, full=False):
    candidate = Candidate("analytic_fixture", "body", "numerical fixture only", .077, 0.,
        Pose((0, 0, 0), tuple(Rotation.from_euler("z", -90, degrees=True).as_quat())), Pose((0, 0, -.08), (0, 0, 0, 1)))
    spec = replace(contacts.spec(), candidate_id=candidate.id, candidate_hash=candidate_sha256([candidate]),
                   provenance={"object_asset_sha256": ASSET, "fixture_only": True})
    cfg = development_config() if cfg is None else cfg
    prepared = prepare_contact_spec(spec, cfg)
    initial = PredictedPose((.55, 0, .8), (0, 0, 0, 1))
    model = MotionModel(samples_per_stage=n, interpolation="quintic")
    motion = predict_motion(candidate, task, initial_object_pose=initial, estimate_source="analytical numerical fixture", model=model)
    if not full:
        stage = PredictedStage("lift", compose(initial, motion.grasp_pose),
            compose(PredictedPose((.55, 0, .8+displacement), (0, 0, 0, 1)), motion.grasp_pose), 0., duration, "explicit analytical fixture")
        motion = replace(motion, stages=(stage,), samples=sample_stage(stage, motion.grasp_pose, model), boundaries=())
    body = ObjectParameters(mass_kg=mass, com_m=(0, 0, 0), inertia_com_kg_m2=np.diag((.001, .002, .003)) if inertia else None,
        source="explicit numerical fixture, not calibrated", provenance={"asset_sha256": ASSET})
    dynamics = predict_dynamics(motion, body, reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), source="analytical fixture")
    return prepared, motion, dynamics


class M2Tests(unittest.TestCase):
    def test_static_limit_matches_m1_and_task03(self):
        p, m, d = fixture(displacement=0)
        r = score_trajectory(p, m, d)
        tws = build_task_wrench_set(m.task, d.body, reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), context_source="fixture")
        m1 = score_prepared(p, tws)
        self.assertEqual(r.status, "scored")
        self.assertAlmostEqual(r.worst_utilization, m1.worst_utilization)
        self.assertAlmostEqual(r.score, .45064)
        self.assertEqual(len(r.samples), len(m.samples))

    def test_exact_cache_preserves_samples_and_uses_capacity_identity(self):
        p, m, d = fixture(displacement=0)
        cache = ExactWrenchCache()
        a, b = score_trajectory(p, m, d, cache=cache), score_trajectory(p, m, d, cache=cache)
        self.assertEqual(a.score, b.score)
        self.assertEqual(b.timings_s["lp_calls"], 0)
        self.assertEqual(b.timings_s["cache_hits"], len(m.samples))
        larger = replace(p.config, finger_capacities={f: capacities.capacity(10.) for f in p.config.finger_capacities})
        p2 = prepare_contact_spec(p.spec, larger)
        r = score_trajectory(p2, m, d, cache=cache)
        self.assertAlmostEqual(r.worst_utilization, a.worst_utilization/2)
        self.assertGreater(r.timings_s["lp_calls"], 0)

    def test_mass_and_time_scale_inertial_force(self):
        p, m, d = fixture()
        p2, m2, d2 = fixture(mass=.56)
        a, b = score_trajectory(p, m, d), score_trajectory(p2, m2, d2)
        self.assertAlmostEqual(b.worst_utilization, 2*a.worst_utilization)
        _, slow, ds = fixture(duration=2.)
        gravity = np.array((0, 0, .28*9.81))
        for fast, slower in zip(d.samples, ds.samples):
            np.testing.assert_allclose(np.array(slower.net_required_force_world_n)-gravity,
                                       .25*(np.array(fast.net_required_force_world_n)-gravity), atol=1e-14)

    def test_dynamic_vs_matched_static_use_same_points_not_same_tws(self):
        p, m, d = fixture()
        dynamic, static = score_trajectory(p, m, d), score_trajectory(p, m, d, gravity_only=True)
        self.assertEqual(static.method_id, STATIC_METHOD_ID)
        self.assertGreater(dynamic.worst_utilization, static.worst_utilization)
        self.assertEqual([(s.stage, s.time_s) for s in dynamic.samples], [(s.stage, s.time_s) for s in static.samples])
        with self.assertRaises(ValueError):
            rank_m2_results([dynamic, static], [p.spec.candidate_id]*2)

    def test_unknown_water_inertia_is_not_silently_ignored(self):
        p, m, d = fixture(task="pour_water", full=True, inertia=False)
        r = score_trajectory(p, m, d)
        self.assertEqual(r.status, "missing_inertia")
        self.assertIsNone(r.score)
        self.assertGreater(r.provenance["sample_status_counts"]["missing_inertia"], 0)
        self.assertEqual(score_trajectory(p, m, d, gravity_only=True).status, "scored")

    def test_support_transition_is_predeclared_exclusion_or_blocks_score(self):
        p, m, d = fixture(full=True)
        a = score_trajectory(p, m, d)
        self.assertEqual(a.status, "scored")
        self.assertGreater(a.provenance["excluded_samples"], 0)
        b = score_trajectory(p, m, d, policy=TrajectoryPolicy(exclude_table_transition=False))
        self.assertEqual(b.status, "unsupported_support_transition")
        self.assertIsNone(b.score)
        with self.assertRaises(ValueError):
            TrajectoryPolicy(excluded_stages=("tilt",))

    def test_identity_asset_reference_version_and_lineage_checks(self):
        p, m, d = fixture()
        self.assertEqual(score_trajectory(p, replace(m, candidate_hash="a"*64), d).status, "invalid_input")
        self.assertEqual(score_trajectory(p, m, replace(d, reference_point_m=(.1, 0, 0))).status, "invalid_input")
        body = replace(d.body, provenance={"asset_sha256": "a"*64})
        self.assertEqual(score_trajectory(p, m, replace(d, body=body)).status, "invalid_input")
        wrong = replace(d, provenance={**d.provenance, "motion_version": "unknown"})
        self.assertEqual(score_trajectory(p, m, wrong).status, "unknown_motion")
        samples = (replace(m.samples[0], time_s=.0001), *m.samples[1:])
        self.assertEqual(score_trajectory(p, replace(m, samples=samples), d).status, "invalid_input")

    def test_malformed_physics_and_stale_primitive_reference_are_invalid_input(self):
        p, m, d = fixture()
        self.assertEqual(score_trajectory(p, m, replace(d, body=None)).status, "invalid_input")
        primitive = replace(p.primitives, reference_point_m=(.01, 0, 0))
        self.assertEqual(score_trajectory(replace(p, primitives=primitive), m, d).status, "invalid_input")

    def test_missing_capacity_and_real_numerical_error(self):
        p, m, d = fixture(cfg=replace(development_config(), finger_capacities={}))
        self.assertEqual(score_trajectory(p, m, d).status, "missing_parameters")
        cfg = replace(development_config(), tolerances=NumericalTolerances(relative=1e-30))
        p, m, d = fixture(displacement=0, cfg=cfg)
        r = score_trajectory(p, m, d)
        self.assertEqual(r.status, "numerical_error")
        self.assertIsNone(r.worst_utilization)

    def test_degenerate_model_infeasible_retained_and_no_choice(self):
        p, m, d = fixture(displacement=0)
        shifted = replace(d.body, com_m=(0, .001, 0))
        d = predict_dynamics(m, shifted, reference_point_m=(0, 0, 0), gravity_world_m_s2=(0, 0, -9.81), source="COM uncertainty fixture")
        r = score_trajectory(p, m, d)
        self.assertEqual(r.status, "model_infeasible")
        ranked = rank_m2_results([r], [r.candidate_id])
        self.assertIsNone(ranked["selected_id"])
        self.assertEqual(ranked["ranking"], [r.candidate_id])

    def test_sampling_convergence_is_finite_not_continuous_guarantee(self):
        rows = [score_trajectory(*fixture(n=n, mass=1.)) for n in (16, 32, 64)]
        continuous = (9.81+.1*10/np.sqrt(3))/5
        for r in rows:
            self.assertEqual(r.status, "scored")
            self.assertLessEqual(r.worst_utilization, continuous+1e-8)
            self.assertLess(continuous-r.worst_utilization, .01)
        self.assertGreater(rows[2].worst_utilization, max(r.worst_utilization for r in rows[:2]))
        self.assertFalse(rows[2].provenance["continuous_maximum_proven"])

    def test_serialization_and_order_completeness(self):
        p, m, d = fixture()
        r = score_trajectory(p, m, d)
        json.dumps(r.to_dict(), allow_nan=False)
        self.assertEqual(rank_m2_results([r], [r.candidate_id])["selected_id"], r.candidate_id)
        with self.assertRaises(ValueError):
            rank_m2_results([r, r], [r.candidate_id, r.candidate_id])

    def test_determinism_and_profile_times_do_not_change_motion_identity(self):
        from grasp_b.wrench_v2.motion_prediction import motion_identity
        p, m, d = fixture()
        changed_profile = replace(m, timings_s={"total": 999.})
        self.assertEqual(motion_identity(m), motion_identity(changed_profile))
        a = score_trajectory(p, m, d)
        b = score_trajectory(p, changed_profile, d)
        self.assertEqual(a.score, b.score)
        self.assertEqual(a.worst_samples, b.worst_samples)
        self.assertEqual(a.provenance["evaluation_context_hash"], b.provenance["evaluation_context_hash"])

    def test_ranking_rejects_different_motion_contexts(self):
        p, m, d = fixture()
        a = score_trajectory(p, m, d)
        p2 = prepare_contact_spec(replace(p.spec, candidate_id="second"), p.config)
        changed = replace(m, candidate_id="second", provenance={**m.provenance, "estimate_source": "different pose-estimation protocol"})
        dynamic = predict_dynamics(changed, d.body, reference_point_m=(0, 0, 0),
                                   gravity_world_m_s2=(0, 0, -9.81), source="fixture")
        b = score_trajectory(p2, changed, dynamic)
        with self.assertRaises(ValueError):
            rank_m2_results([a, b], [a.candidate_id, b.candidate_id])

    def test_unknown_required_stage_cannot_be_silently_excluded(self):
        p, m, d = fixture()
        samples = (replace(m.samples[0], stage="unknown_motion_stage"), *m.samples[1:])
        changed = replace(m, samples=samples)
        dynamic = predict_dynamics(changed, d.body, reference_point_m=(0, 0, 0),
                                   gravity_world_m_s2=(0, 0, -9.81), source="fixture")
        r = score_trajectory(p, changed, dynamic)
        self.assertEqual(r.status, "unknown_motion")
        self.assertIsNone(r.score)
        self.assertTrue(r.samples[0].required)
        self.assertEqual(r.samples[0].status, "unknown_motion")
        self.assertTrue(any(s.status == "optimal" for s in r.samples[1:]))


if __name__ == "__main__":
    unittest.main()
