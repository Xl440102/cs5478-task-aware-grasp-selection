"""Known answers and invariants for COM score Step 1 (no simulation)."""
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import unittest

from grasp_a.contracts import Candidate, Pose
from grasp_b.geometry import Geometry, tool_orientation
from grasp_b.scoring import COM_SCORE_VERSION, com_distance_score, score_candidate_com


class COMDistanceScoreTests(unittest.TestCase):
    def score(self, distance, scale=.045):
        return com_distance_score(distance, reference_distance_m=scale)

    def test_known_values_and_boundaries(self):
        for distance, expected in ((0, 1), (.012, 11/15), (.024, 7/15),
                                   (.045, 0), (.09, 0)):
            with self.subTest(distance=distance):
                result = self.score(distance)
                self.assertAlmostEqual(result['score'], expected)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['version'], COM_SCORE_VERSION)

    def test_range_monotonicity_and_scale_invariance(self):
        values = [self.score(i*.001)['score'] for i in range(100)]
        self.assertTrue(all(0 <= s <= 1 for s in values))
        self.assertTrue(all(a >= b for a, b in zip(values, values[1:])))
        self.assertAlmostEqual(self.score(.012)['score'], self.score(.024, .09)['score'])

    def test_missing_is_distinct_from_zero_score(self):
        missing = self.score(None)
        self.assertIsNone(missing['score'])
        self.assertEqual(missing['status'], 'unavailable')
        self.assertEqual(self.score(.09)['score'], 0)
        self.assertEqual(self.score(.09)['status'], 'ok')

    def test_invalid_numbers_are_json_safe(self):
        for distance in (float('nan'), float('inf'), -float('inf'), -1, True, '.012', 10**400):
            with self.subTest(distance=str(distance)):
                result = self.score(distance)
                self.assertIsNone(result['score'])
                self.assertEqual(result['status'], 'invalid_input')
                json.dumps(result, allow_nan=False)
        for scale in (None, 0, -1, float('nan'), float('inf'), True, '.045'):
            with self.subTest(scale=scale):
                result = self.score(.012, scale)
                self.assertIsNone(result['score'])
                json.dumps(result, allow_nan=False)

    def test_extreme_finite_values_do_not_overflow(self):
        self.assertEqual(self.score(1e308, 1e-308)['score'], 0)
        self.assertEqual(self.score(0, 1e-308)['score'], 1)


class CandidateCOMScoreTests(unittest.TestCase):
    def setUp(self):
        self.geometry = Geometry(.035, .09, .08, .105)
        q = tool_orientation(0, 0)
        self.candidate = Candidate('test', 'body', 'side', .078, 0,
                                  Pose((0, 0, .012), q), Pose((-.12, 0, .012), q))

    def test_static_feature_integration_and_no_candidate_mutation(self):
        before = asdict(self.candidate)
        result = score_candidate_com(self.candidate, self.geometry)
        self.assertAlmostEqual(result['distance_m'], .012)
        self.assertAlmostEqual(result['reference_distance_m'], .045)
        self.assertAlmostEqual(result['score'], 11/15)
        self.assertEqual(asdict(self.candidate), before)
        self.assertEqual(self.candidate.geometry_score, 0)
        # Changing ID cannot influence score.
        renamed = score_candidate_com(replace(self.candidate, id='other'), self.geometry)
        self.assertEqual(result['score'], renamed['score'])
        self.assertEqual(result, score_candidate_com(self.candidate, self.geometry))
        self.assertEqual(json.dumps(result, sort_keys=True, allow_nan=False),
                         json.dumps(score_candidate_com(self.candidate, self.geometry), sort_keys=True))

    def test_com_from_model_changes_distance_not_manual_score(self):
        result = score_candidate_com(self.candidate, replace(self.geometry, com=(0,0,.01)))
        self.assertAlmostEqual(result['distance_m'], .002)
        self.assertAlmostEqual(result['score'], 1-.002/.045)

    def test_unsupported_contact_geometry_and_bad_pose(self):
        tilted = replace(self.candidate, grasp_pose=Pose((0,0,.012), tool_orientation(0,90)))
        self.assertEqual(score_candidate_com(tilted, self.geometry)['status'], 'unavailable')
        bad = replace(self.candidate, grasp_pose=Pose((0,0,float('nan')), (0,0,0,1)))
        self.assertEqual(score_candidate_com(bad, self.geometry)['status'], 'invalid_candidate')
        bad_q = replace(self.candidate, grasp_pose=Pose((0,0,.012), (0,0,0,0)))
        self.assertEqual(score_candidate_com(bad_q, self.geometry)['status'], 'invalid_candidate')

    def test_invalid_geometry_does_not_produce_a_score(self):
        for change in ({'height':0}, {'height':float('nan')}, {'radius':-1},
                       {'com':(0,0,float('inf'))}, {'com':(0,0)}):
            with self.subTest(change=change):
                result = score_candidate_com(self.candidate, replace(self.geometry, **change))
                self.assertIsNone(result['score'])
                self.assertEqual(result['status'], 'invalid_geometry')
                json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
