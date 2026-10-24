"""Reuse C's reference random selector; verify B's adapter, not a new RNG."""
from copy import deepcopy
from collections import Counter
import random
import unittest
from unittest.mock import patch

from grasp_b.prototype import select_random
from grasp_c.selectors import random_selector


class RandomPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.public = [dict(id=i, region='body', approach='side', gripper_width=.078,
                            geometry_score=.5) for i in ('a','b','c','d','e')]

    def test_reuses_existing_C_selector_exactly_for_development_seeds(self):
        before = deepcopy(self.public)
        for seed in (0,1,2):
            expected = random_selector('handover_prep', self.public, seed)
            with patch('grasp_c.selectors.random_selector', wraps=random_selector) as reused:
                selection = select_random(self.public, task_id='handover_prep', seed=seed)
                reused.assert_called_once_with('handover_prep', self.public, seed)
            self.assertEqual(selection['ranking'], expected['ranking'])
            self.assertEqual(selection['selected_id'], expected['selected_id'])
            self.assertEqual(selection, select_random(self.public,task_id='handover_prep',seed=seed))
            self.assertEqual(set(selection['ranking']), {r['id'] for r in self.public})
            self.assertEqual(selection['selected_id'], selection['ranking'][0])
        self.assertEqual(self.public,before)

    def test_does_not_consume_global_rng_or_use_scores(self):
        state = random.getstate()
        first = select_random(self.public,task_id='pour_prep',seed=0)
        self.assertEqual(state,random.getstate())
        changed = deepcopy(self.public)
        for i,r in enumerate(changed):
            r['geometry_score'] = i/10
        self.assertEqual(first, select_random(changed,task_id='pour_prep',seed=0))
        self.assertEqual(first, select_random(self.public,task_id='handover_prep',seed=0))

    def test_seed_sweep_covers_candidates_without_order_skew(self):
        forward = Counter(select_random(self.public,task_id='transport_place',seed=s)['selected_id']
                          for s in range(1000))
        reverse = Counter(select_random(list(reversed(self.public)),task_id='transport_place',seed=s)['selected_id']
                          for s in range(1000))
        expected_ids = {row['id'] for row in self.public}
        self.assertEqual(set(forward),expected_ids)
        self.assertEqual(set(reverse),expected_ids)
        self.assertTrue(all(160 <= forward[cid] <= 240 for cid in expected_ids),forward)
        self.assertTrue(all(160 <= reverse[cid] <= 240 for cid in expected_ids),reverse)
        self.assertTrue(all(abs(forward[cid]-reverse[cid]) <= 60 for cid in expected_ids),
                        (forward,reverse))

    def test_empty_skips_C_and_singleton_is_valid(self):
        with patch('grasp_c.selectors.random_selector') as reused:
            self.assertIsNone(select_random([],task_id='pour_prep',seed=0)['selected_id'])
            reused.assert_not_called()
        self.assertEqual(select_random(self.public[:1],task_id='pour_prep',seed=0)['selected_id'],'a')

    def test_invalid_public_and_foreign_output_are_rejected(self):
        for public in ([self.public[0],self.public[0]], [dict(self.public[0],success=True)]):
            with self.assertRaises(ValueError):
                select_random(public,task_id='pour_prep',seed=0)
        with patch('grasp_c.selectors.random_selector',return_value={'selected_id':'unknown','ranking':['unknown']}):
            with self.assertRaises(ValueError):
                select_random(self.public,task_id='pour_prep',seed=0)
