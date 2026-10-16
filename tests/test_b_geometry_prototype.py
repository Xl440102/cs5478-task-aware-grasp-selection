from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grasp_a.contracts import Candidate, Pose
from grasp_b.geometry import Geometry, tool_orientation
from grasp_b.generation import write_candidates
from grasp_b.scoring import normalized_weights, score_geometry, unit_interval_score
from grasp_b.prototype import assign_geometry_scores, select_geometry


class GeometryPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.g = Geometry(.035, .09, .08, .105)
        q = tool_orientation(0,0)
        self.c = Candidate('a', 'body', 'side', .078, 0,
                           Pose((0,0,.012),q), Pose((-.12,0,.012),q))
        self.weights = dict(alignment=1, symmetry=1, com_distance=1)

    def test_normalized_proxies_invalid_missing_and_roundoff(self):
        for component in ('alignment','symmetry'):
            for value in (0,.3,1):
                self.assertEqual(unit_interval_score(value,component=component)['score'], value)
            self.assertEqual(unit_interval_score(1+1e-13,component=component)['score'],1)
            for value in (None,float('nan'),float('inf'),-.1,1.1,True,'1'):
                result = unit_interval_score(value,component=component)
                self.assertIsNone(result['score'])
                json.dumps(result,allow_nan=False)

    def test_weights_and_overflow_safe_normalization(self):
        for weights in ({},dict(self.weights,alignment=-1),dict(self.weights,alignment=float('nan')),
                        dict.fromkeys(self.weights,0),dict(self.weights,alignment=True)):
            with self.assertRaises(ValueError):
                normalized_weights(weights)
        normalized = normalized_weights(dict.fromkeys(self.weights,1e308))
        self.assertAlmostEqual(sum(normalized.values()),1)

    def test_known_aggregate_monotonic_com_and_scaling_weights(self):
        result = score_geometry(self.c,self.g,self.weights)
        self.assertAlmostEqual(result['score'],(1+1+11/15)/3)
        higher = replace(self.c,grasp_pose=replace(self.c.grasp_pose,position=(0,0,.024)))
        self.assertGreater(result['score'],score_geometry(higher,self.g,self.weights)['score'])
        self.assertEqual(result['score'], score_geometry(self.c,self.g,
                         {k:10*v for k,v in self.weights.items()})['score'])
        self.assertTrue(0 <= result['score'] <= 1)
        json.dumps(result,allow_nan=False)

    def test_reuses_features_once_and_monotonic_components(self):
        from grasp_b.features import candidate_features
        features = candidate_features(self.c,self.g)
        with patch('grasp_b.scoring.candidate_features', return_value=features) as compute:
            baseline = score_geometry(self.c,self.g,self.weights)
            compute.assert_called_once()
        for feature in ('contact_alignment_proxy','contact_symmetry_proxy'):
            from copy import deepcopy
            reduced = deepcopy(features)
            reduced['values'][feature] = .5
            with patch('grasp_b.scoring.candidate_features',return_value=reduced):
                self.assertLess(score_geometry(self.c,self.g,self.weights)['score'],baseline['score'])

    def test_missing_never_silently_reweights_even_zero_weight(self):
        tilted = replace(self.c,grasp_pose=Pose((0,0,.012),tool_orientation(0,90)))
        scored,rows = assign_geometry_scores([tilted],self.g,self.weights)
        self.assertEqual(scored,[])
        self.assertEqual(rows[0]['status'],'unavailable')
        self.assertIsNone(rows[0]['score'])
        self.assertIsNone(score_geometry(self.c,replace(self.g,height=0),self.weights)['score'])

    def test_new_candidates_roundtrip_and_public_only_selection(self):
        b = replace(self.c,id='b')
        scored,rows = assign_geometry_scores([b,self.c],self.g,self.weights)
        self.assertEqual(self.c.geometry_score,0)
        with tempfile.TemporaryDirectory() as tmp:
            loaded = write_candidates(Path(tmp)/'scored.json',scored)
        selection = select_geometry([c.public_metadata() for c in loaded])
        self.assertEqual(selection['ranking'],['a','b'])
        self.assertIn(selection['selected_id'],{c.id for c in loaded})
        self.assertEqual(rows,assign_geometry_scores([b,self.c],self.g,self.weights)[1])
        with self.assertRaises(ValueError):
            select_geometry([dict(scored[0].public_metadata(),success=True)])
        self.assertIsNone(select_geometry([])['selected_id'])

    def test_real_A_filter_selection_and_execution_identity(self):
        from grasp_a.simulation import get_feasible_candidates, run_episode
        scored,_ = assign_geometry_scores([self.c],self.g,self.weights)
        filtered = get_feasible_candidates(0,scored)
        self.assertEqual(filtered['status'],'ok')
        selection = select_geometry(filtered['feasible_candidates'])
        episode = run_episode('handover_prep',0,selection['selected_id'],scored)
        self.assertEqual(episode['selected_id'],selection['selected_id'])
        self.assertTrue(episode['feasibility']['feasible'])
        self.assertEqual(episode['policy_mode'],'fixed')
        self.assertIsInstance(episode['success'],bool)
