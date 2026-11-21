"""Real independent HiGHS-IPM versus joint HiGHS mathematical fixtures."""
from dataclasses import replace
import unittest
import hashlib,json,tempfile
from pathlib import Path
from unittest.mock import patch
from scipy.optimize import OptimizeResult
import numpy as np

from tests.test_b_contact_refinement_diagnostics import model
from tests.test_b_task_relaxed_solver import CFG,demands
from grasp_b.contact_refinement.relaxed_solver import solve_task,solve_task_matrices
from grasp_b.contact_refinement.numerical_oracles import independent_scenario,IndependentOracleCache
from grasp_b.contact_refinement.ranking_stability_audit import classify_cell
from grasp_b.contact_refinement.validation import verify_nested,verify_manifest
from grasp_b.wrench_v2.gws import build_primitive_wrenches
from grasp_b.wrench_v2.constraints import build_capacity_constraints
from tests.test_b_wrench_constraints import MU,capacity


class IndependentMathTests(unittest.TestCase):
    def setUp(self):self.spec,self.a,self.b=model([[-.03,0,0],[.03,0,0]],[[-1,0,0],[1,0,0]])

    def compare(self,ws,a=None,b=None):
        a=self.a if a is None else a;b=self.b if b is None else b
        joint=solve_task(a,b,demands(a.candidate_id,a.candidate_hash,ws),config=CFG).to_dict()
        self.assertEqual(joint['status'],'scored_conditional')
        first=[];second=[]
        for w in ws:
            x=independent_scenario(a.matrix,b.matrix,b.bounds_n,w,config=CFG,stage=1,source_identity='a'*64)
            y=independent_scenario(a.matrix,b.matrix,b.bounds_n,w,config=CFG,stage=2,radius=joint['stage2']['record']['radius'],source_identity='a'*64)
            self.assertEqual(x['status'],'verified',x);self.assertEqual(y['status'],'verified',y)
            first.append(x['recomputed_objective']);second.append(y['recomputed_objective'])
        self.assertAlmostEqual(joint['risk'],max(first),places=6);self.assertAlmostEqual(joint['utilization'],max(second),places=6)
        return joint

    def test_multiscenario_both_oracles(self):self.compare([[0,0,.5,0,0,0],[0,0,3,0,0,0]])
    def test_zero_and_u_zero_boundary(self):
        d=self.compare([[0]*6,[0]*6]);self.assertEqual(d['risk'],0.);self.assertEqual(d['utilization'],0.)
    def test_rank5_structural(self):
        d=self.compare([[0,0,0,.001,0,0],[0,0,.5,0,0,0]]);self.assertAlmostEqual(d['risk'],.01)
    def test_rank6_cone(self):
        s,a,b=model([[-.03,0,0],[.03,0,0],[0,.03,0],[0,-.03,0]],[[0,0,-1]]*4)
        d=self.compare([[0,0,-1,0,0,0]],a,b);self.assertAlmostEqual(d['risk'],1.)
    def test_common_worst_and_nonunique_allocations(self):self.compare([[0,0,3,0,0,0],[0,0,3,0,0,0],[0,0,2,1e-12,0,0]])
    def test_distinct_optimal_allocations_same_residual_and_budget(self):
        a=np.zeros((6,2));a[0]=[1.,1.];b=np.ones((1,2));w=np.array([.5,0,0,0,0,0])
        first=independent_scenario(a,b,[1],w,config=CFG,stage=1,source_identity='e'*64)
        radius=first['recomputed_objective']+CFG.residual_slack_absolute
        second=independent_scenario(a,b,[1],w,config=CFG,stage=2,radius=radius,source_identity='e'*64)
        self.assertEqual(second['status'],'verified')
        u=second['recomputed_objective'];left=np.array([u,0.]);right=np.array([0.,u])
        self.assertFalse(np.array_equal(left,right))
        np.testing.assert_allclose(a@left,a@right,atol=1e-12)
        np.testing.assert_allclose(b@left,b@right,atol=1e-12)
        self.assertLessEqual(np.max(np.abs(CFG.scale*(a@left-w))),radius+1e-9)
    def test_numerical_failure_is_uncertain_not_verified(self):
        with patch('grasp_b.contact_refinement.numerical_oracles.linprog',return_value=OptimizeResult(status=4,success=False,x=None,message='fixture numerical failure')):
            r=independent_scenario(self.a.matrix,self.b.matrix,self.b.bounds_n,[0]*6,config=CFG,stage=1,source_identity='a'*64)
        self.assertEqual(r['status'],'uncertain')
    def test_cache_stage_source_capacity_k_and_tolerance_isolation(self):
        cache=IndependentOracleCache();kwargs=dict(config=CFG,stage=1,radius=None,source_identity='a'*64,model_identity='b'*64)
        first=cache.solve(self.a.matrix,self.b.matrix,self.b.bounds_n,[0]*6,**kwargs)
        with patch('grasp_b.contact_refinement.numerical_oracles.linprog',side_effect=AssertionError('cache hit should avoid solve')):
            self.assertEqual(first,cache.solve(self.a.matrix,self.b.matrix,self.b.bounds_n,[0]*6,**kwargs))
        self.assertEqual(cache.hits,1)
        for changes in (dict(stage=2,radius=1e-7),dict(source_identity='c'*64),dict(model_identity='d'*64),dict(config=replace(CFG,backend_feasibility=1e-10))):
            other=cache.solve(self.a.matrix,self.b.matrix,self.b.bounds_n,[0]*6,**dict(kwargs,**changes))
            self.assertNotEqual(first['input_hash'],other['input_hash'])
        changed=cache.solve(self.a.matrix,self.b.matrix,self.b.bounds_n*2,[0]*6,**kwargs)
        self.assertNotEqual(first['input_hash'],changed['input_hash'])
        cache.records[first['input_hash']]['status']='broken'
        with self.assertRaises(ValueError):cache.solve(self.a.matrix,self.b.matrix,self.b.bounds_n,[0]*6,**kwargs)

    def test_actual_nested_k_and_cache_separation(self):
        cache=IndependentOracleCache();models={}
        for k in (8,16,32):
            data=build_primitive_wrenches(self.spec,friction=MU,k=k)
            budget=build_capacity_constraints(data,finger_budgets={'left':capacity(1),'right':capacity(1)})
            r=cache.solve(data.matrix,budget.matrix,budget.bounds_n,[0]*6,config=CFG,stage=1,radius=None,
                          source_identity='a'*64,model_identity=budget.primitive_fingerprint)
            models[k]=data;self.assertEqual(r['variables'],len(data.columns)+1)
        self.assertTrue(verify_nested(models[8],models[16]));self.assertTrue(verify_nested(models[16],models[32]))
        self.assertEqual(cache.misses,3)

    def test_frozen_manifest_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'original.json').write_text('{"value":1}')
            expected=hashlib.sha256((p/'original.json').read_bytes()).hexdigest()
            (p/'checksums.json').write_text(json.dumps({'original.json':expected}))
            self.assertEqual(verify_manifest(p),1)
            (p/'original.json').write_text('{"value":2}')
            with self.assertRaises(ValueError):verify_manifest(p)


class StabilityEvidenceTests(unittest.TestCase):
    def row(self,cid,r,u,error=1e-7):
        return dict(candidate_id=cid,status='scored_conditional',risk=r,utilization=u,risk_interval=[r,r],utilization_interval=[u,u],
                    risk_numerical_allowance=error,utilization_numerical_allowance=error,validation_status='verified')
    def ranking(self,ids):return dict(selected_id=ids[0],top_primary_near_tie_ids=ids,top_both_near_tie_ids=ids)
    def test_near_tie_not_clear_by_id(self):
        r=classify_cell([self.row('a',0,.5),self.row('b',1e-10,.50000001)],self.ranking(['a','b']),[])
        self.assertEqual(r['classification'],'near_tie');self.assertEqual(set(r['near_tie_ids']),{'a','b'})
    def test_clear_requires_interval_separation(self):
        ranking=self.ranking(['a']);rows=[self.row('a',0,.5),self.row('b',.1,.1)]
        r=classify_cell(rows,ranking,[dict(profile='k8',ranking=ranking,all_certificates_verified=True)])
        self.assertEqual(r['classification'],'clear_winner')
    def test_discretization_flip_is_not_statistical_noise(self):
        rows=[self.row('a',0,.5),self.row('b',.1,.1)]
        r=classify_cell(rows,self.ranking(['a']),[dict(profile='k8',ranking=self.ranking(['b']),all_certificates_verified=True)])
        self.assertEqual(r['classification'],'unresolved')
    def test_missing_certificate_unresolved(self):
        r=self.row('a',0,.5);r['validation_status']='uncertain'
        self.assertEqual(classify_cell([r],self.ranking(['a']),[])['classification'],'unresolved')
    def test_stable_id_with_sensitivity_tie_is_not_clear(self):
        rows=[self.row('a',0,.5),self.row('b',.1,.1)]
        r=classify_cell(rows,self.ranking(['a']),[dict(profile='k8',ranking=self.ranking(['a','b']),all_certificates_verified=True)])
        self.assertEqual(r['classification'],'near_tie');self.assertEqual(set(r['near_tie_ids']),{'a','b'})


if __name__=='__main__':unittest.main()
