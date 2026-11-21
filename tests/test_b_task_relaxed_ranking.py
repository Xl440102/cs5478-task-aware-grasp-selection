"""Ranking tie fixtures use actual joint HiGHS results."""
from dataclasses import replace
import unittest
import numpy as np

from tests.test_b_task_relaxed_solver import CFG,demands
from grasp_b.contact_refinement.relaxed_solver import solve_task_matrices,abstain_task
from grasp_b.contact_refinement.relaxed_ranking import rank_task_results


def fixture(cid,force=1.,wrench=None,context=None):
    a=np.zeros((6,1));a[0,0]=force
    t=demands(cid,'a'*64,[wrench or [1.,0,0,0,0,0]])
    if context:t=replace(t,common_context_identity=context)
    return solve_task_matrices(a,[[1]],[1],t,config=CFG,model_identity='e'*64)


class RankingTests(unittest.TestCase):
    def test_near_tie_stable_id_not_float_noise(self):
        a=fixture('a',force=1.);z=fixture('z',force=1.0000001)
        r=rank_task_results([z,a],['a','z'])
        self.assertEqual(r['selected_id'],'a');self.assertEqual(set(r['top_both_near_tie_ids']),{'a','z'})
        self.assertEqual(r['ranking'],rank_task_results([a,z],['z','a'])['ranking'])

    def test_primary_residual_before_utilization(self):
        bad=fixture('a',force=0.);good=fixture('z',force=1.)
        r=rank_task_results([bad,good],['a','z']);self.assertEqual(r['selected_id'],'z')

    def test_utilization_breaks_residual_tie(self):
        a=fixture('a',force=1.);z=fixture('z',force=2.)
        self.assertEqual(rank_task_results([a,z],['a','z'])['selected_id'],'z')

    def test_anchored_ties_do_not_chain(self):
        values=[fixture('a'),fixture('b',1.-.8e-6),fixture('c',1.-1.6e-6)]
        r=rank_task_results(values,['a','b','c'])
        self.assertEqual(set(r['tie_groups'][0]['primary_ids']),{'a','b'})
        self.assertEqual(r['tie_groups'][1]['primary_ids'],['c'])

    def test_complete_tail_and_all_abstention(self):
        invalid=abstain_task(demands('bad','a'*64,[]),CFG,status='geometry_invalid',reason='no contacts',uncertainty={'collision_geometry':'unknown'})
        r=rank_task_results([invalid,fixture('good')],['bad','good']);self.assertEqual(r['ranking'],['good','bad'])
        self.assertEqual(r['status_by_id']['bad'],'geometry_invalid')
        self.assertIsNone(rank_task_results([invalid],['bad'])['selected_id'])
        self.assertEqual(rank_task_results([],[])['ranking'],[])

    def test_identity_and_incomplete_pool_refused(self):
        a=fixture('a');b=fixture('b',context='f'*64)
        with self.assertRaises(ValueError):rank_task_results([a,b],['a','b'])
        with self.assertRaises(ValueError):rank_task_results([a],['a','b'])
        with self.assertRaises(ValueError):rank_task_results([a,a],['a','a'])


if __name__=='__main__':unittest.main()
