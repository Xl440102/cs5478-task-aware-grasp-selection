from dataclasses import replace
import json
import math
from pathlib import Path
import unittest

from grasp_a.contracts import Candidate, Pose
from grasp_b.features import candidate_features, equivalent_pairs, feature_sidecar
from grasp_b.geometry import Geometry, tool_orientation
from grasp_b.selectors import (deterministic_geometry_ranking, smoke_selector,
                               validate_public, validate_ranking)

G = Geometry(.035, .09, .08, .105)


def candidate(cid='a', roll=0, pos=(0, 0, .012)):
    q = tool_orientation(0, roll)
    return Candidate(cid, 'body', 'tool_z', .078, 0, Pose(pos, q),
                     Pose((pos[0]-.12, pos[1], pos[2]), q))


class FeatureTests(unittest.TestCase):
    def test_analytic_contacts_and_com_provenance(self):
        row = candidate_features(candidate(), G)
        v = row['values']
        self.assertTrue(row['approach_consistent'])
        self.assertAlmostEqual(v['opening_margin_m'], .008)
        self.assertAlmostEqual(v['contact_alignment_proxy'], 1)
        self.assertAlmostEqual(v['contact_symmetry_proxy'], 1)
        self.assertAlmostEqual(v['contact_midpoint_com_distance_m'], .012)
        for contact in row['ideal_contacts_mug_m']:
            self.assertAlmostEqual(math.hypot(*contact[:2]), .035)
        shifted = candidate_features(candidate(), replace(G, com=(0, 0, .01)))
        self.assertAlmostEqual(shifted['values']['contact_midpoint_com_distance_m'], .002)

    def test_offcentre_line_has_measured_alignment_and_symmetry(self):
        row = candidate_features(candidate(pos=(.01, .005, .012)), G)['values']
        self.assertAlmostEqual(row['contact_alignment_proxy'], math.sqrt(.035**2-.01**2)/.035)
        self.assertLess(row['contact_symmetry_proxy'], 1)

    def test_unsupported_contacts_are_null_and_bad_approach_diagnosed(self):
        for c in (candidate(roll=90), candidate(pos=(.1,0,.012)),
                  candidate(pos=(0,0,.05))):
            row = candidate_features(c, G)
            self.assertIsNone(row['values']['contact_alignment_proxy'])
        c = candidate()
        c = replace(c, pregrasp_pose=replace(c.pregrasp_pose, position=(.12, .01, .012)))
        self.assertFalse(candidate_features(c, G)['approach_consistent'])

    def test_contact_family_pairs_do_not_merge_height_or_yaw(self):
        a, b = candidate(), candidate('b', 180)
        c = candidate('c', pos=(0,0,.024))
        self.assertEqual(equivalent_pairs([b,c,a]), [['a','b']])
        flipped = replace(a, id='d', grasp_pose=replace(a.grasp_pose,
                           quaternion=tuple(-x for x in a.grasp_pose.quaternion)))
        self.assertEqual(equivalent_pairs([flipped,a]), [['a','d']])
        sidecar = feature_sidecar([a,b], G, {'mug': 'test_hash'})
        self.assertEqual(sidecar['visibility'], 'internal_diagnostics_not_selector_input')
        self.assertEqual(sidecar, feature_sidecar([a,b], G, {'mug':'test_hash'}))
        json.dumps(sidecar, allow_nan=False)
        try:
            from grasp_a.contracts import candidate_sha256
        except ImportError:
            candidate_sha256 = None
        if candidate_sha256 is not None:
            unicode_candidate = replace(a, id='候选')
            self.assertEqual(feature_sidecar([unicode_candidate], G, {})['candidate_sha256'],
                             candidate_sha256([unicode_candidate]))
        changed = feature_sidecar([c,b], G, {})
        self.assertNotEqual(sidecar['candidate_sha256'], changed['candidate_sha256'])


class PublicSelectorTests(unittest.TestCase):
    def setUp(self):
        self.rows = [candidate('b').public_metadata(), candidate('a').public_metadata()]

    def test_stable_tie_and_no_placeholder_ranking(self):
        with self.assertRaises(ValueError):
            deterministic_geometry_ranking(self.rows)
        self.assertEqual(deterministic_geometry_ranking(self.rows, score_version='synthetic_test_v1'), ['a','b'])
        self.assertEqual(smoke_selector('pour_prep', self.rows, 0)['ranking'], ['a','b'])
        self.assertEqual(smoke_selector('pour_prep', self.rows[::-1], 0)['ranking'], ['a','b'])
        self.rows[0]['geometry_score'] = .1
        self.assertEqual(deterministic_geometry_ranking(self.rows, score_version='synthetic_test_v1'), ['b','a'])

    def test_rejects_hidden_fields_invalid_ids_and_ranking(self):
        for rows in ([dict(self.rows[0], success=True)], [self.rows[0], self.rows[0]],
                     [dict(self.rows[0], geometry_score=float('nan'))]):
            with self.assertRaises(ValueError):
                validate_public(rows)
        for ranking in (['a'], ['a','a'], ['a','unknown']):
            with self.assertRaises(ValueError):
                validate_ranking(ranking, self.rows)
        with self.assertRaises(ValueError):
            smoke_selector('pour_prep', [], 0)
        self.assertEqual(validate_ranking(['b','a'], self.rows), ['b','a'])


class AdapterTests(unittest.TestCase):
    def test_reviewed_wrapper_or_explicit_unavailable(self):
        from grasp_b.runner import evaluate_smoke
        try:
            from examples import evaluate_one_trial as bridge
        except ImportError:
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                evaluate_smoke('pour_prep', 0, [candidate()])
            return
        # Exercise the real wrapper/physics with generated candidates.
        import pybullet_data
        from grasp_b.generation import generate_candidates, DEFAULT_CONFIG
        from grasp_b.geometry import read_geometry
        root = Path(__file__).resolve().parents[1]
        g = read_geometry(root/'assets/mug.urdf', Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf')
        candidates, _ = generate_candidates(json.loads(DEFAULT_CONFIG.read_text()), g)
        result = evaluate_smoke('handover_prep', 0, candidates)
        self.assertEqual(result['status'], 'completed', result['error'])
        self.assertEqual(result['schema_version'], 'evaluation_trial_v0.2')
        self.assertEqual(result['selection']['metadata']['scope'], 'SMOKE_ONLY')
        self.assertEqual(result['episode']['policy_mode'], 'fixed')
        self.assertIn(result['selected_id'], {c['id'] for c in result['filter']['feasible_candidates']})
        json.dumps(result, allow_nan=False)
