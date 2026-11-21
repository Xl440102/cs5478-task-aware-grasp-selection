"""Real HiGHS joint task LPs, separate from the strict production methods."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from tests.test_b_contact_refinement_diagnostics import model
from tests.test_b_wrench_constraints import capacity,MU
from grasp_b.wrench_v2.gws import build_primitive_wrenches,grasp_matrix
from grasp_b.wrench_v2.constraints import build_capacity_constraints
from grasp_b.wrench_v2.solver import solve_wrench
from grasp_b.contact_refinement.relaxed_solver import (
    TaskLexConfig,RequiredWrench,TaskDemands,solve_task,solve_task_matrices,
    ExactTaskCache,TaskLexResult,verify_stage,verify_original_scenarios,
)

CFG=TaskLexConfig(reference_force_n=1.,characteristic_length_m=.1)


def demands(candidate_id,candidate_hash,wrenches,*,method='relaxed_m1_task_lex_v1',reference=(0,0,0)):
    return TaskDemands(candidate_id=candidate_id,candidate_hash=candidate_hash,task='transport_place',method=method,
        object_identity='b'*64,common_context_identity='c'*64,source_identity='d'*64,reference_point_m=reference,
        scenarios=tuple(RequiredWrench('scenario_'+str(i),tuple(w)) for i,w in enumerate(wrenches)))


class JointTaskTests(unittest.TestCase):
    def setUp(self):
        self.spec,self.a,self.b=model([[-.03,0,0],[.03,0,0]],[[-1,0,0],[1,0,0]])

    def run_task(self,wrenches,*,a=None,b=None,config=CFG,cache=None):
        a=self.a if a is None else a;b=self.b if b is None else b
        task=demands(a.candidate_id,a.candidate_hash,wrenches,reference=a.reference_point_m)
        result=solve_task(a,b,task,config=config,cache=cache)
        d=result.to_dict();self.assertEqual(d['status'],'scored_conditional',str(d)[:1500])
        for key in ('stage1','stage2'):
            checked=verify_stage(a.matrix,b.matrix,b.bounds_n,d['unique_required_wrenches'],config,d[key]['record'])
            self.assertTrue(checked['valid'],checked)
        self.assertLessEqual(max(d['per_original_scenario_residual_stage2']),d['risk']+d['delta_scaled']+2e-7)
        return result

    def test_single_scenario_real_highs(self):
        d=self.run_task([[0,0,.5,0,0,0]]).to_dict()
        self.assertLess(d['risk'],1e-7);self.assertAlmostEqual(d['utilization'],.5,places=6)
        self.assertEqual(d['stage1']['shape']['variables'],33)

    def test_multiscenario_joint_variables_and_worst_residual(self):
        d=self.run_task([[0,0,.5,0,0,0],[0,0,3,0,0,0]]).to_dict()
        self.assertAlmostEqual(d['risk'],2.,places=6);self.assertAlmostEqual(d['utilization'],1.,places=6)
        self.assertEqual(d['stage1']['shape']['variables'],65)
        self.assertEqual(d['stage1']['shape']['inequalities'],28)
        self.assertEqual(d['stage2']['formulation'],'joint_all_required_scenario_blocks_one_shared_objective')

    def test_task_lex_is_not_per_scenario_lex(self):
        a=np.zeros((6,1));a[0,0]=1.
        task=demands('math_fixture','a'*64,[[0,0,0,.05,0,0],[.4,0,0,0,0,0]])
        result=solve_task_matrices(a,[[1]],[1],task,config=CFG,model_identity='e'*64).to_dict()
        self.assertEqual(result['status'],'scored_conditional')
        self.assertAlmostEqual(result['risk'],.5,places=10)
        self.assertAlmostEqual(result['utilization'],0.,places=10)
        # Per-scenario zero-residual force allocation would need u≈.4.
        self.assertLess(result['utilization'],.1)

    def test_independent_forces_per_scenario(self):
        d=self.run_task([[.5,0,0,0,0,0],[-.5,0,0,0,0,0]]).to_dict()
        self.assertLess(d['risk'],1e-7)
        x=np.array(d['stage2']['record']['x'][:-1]).reshape(2,-1)
        self.assertGreater(np.linalg.norm(x[0]-x[1]),.1)

    def test_rank5_structural_obstruction_retained(self):
        self.assertEqual(np.linalg.matrix_rank(grasp_matrix(self.spec)),5)
        w=[0,0,0,.001,0,0]
        self.assertEqual(solve_wrench(self.a,w,constraints=self.b).status.value,'infeasible')
        d=self.run_task([w]).to_dict();self.assertAlmostEqual(d['risk'],.01,places=8)
        self.assertEqual(d['actual_physical_feasibility'],'not_established')

    def test_rank6_cone_infeasibility_is_not_numerical_failure(self):
        s,a,b=model([[-.03,0,0],[.03,0,0],[0,.03,0],[0,-.03,0]],[[0,0,-1]]*4)
        self.assertEqual(np.linalg.matrix_rank(grasp_matrix(s)),6)
        self.assertEqual(solve_wrench(a,[0,0,-1,0,0,0],constraints=b).status.value,'infeasible')
        d=self.run_task([[0,0,-1,0,0,0]],a=a,b=b).to_dict();self.assertAlmostEqual(d['risk'],1.,places=8)

    def test_capacity_overrun_has_finite_task_risk(self):
        strict=solve_wrench(self.a,[0,0,3,0,0,0],constraints=self.b)
        self.assertEqual(strict.status.value,'optimal');self.assertGreater(strict.utilization,1.)
        self.assertAlmostEqual(self.run_task([[0,0,3,0,0,0]]).payload['risk'],2.,places=6)

    def test_zero_wrench_both_objectives_zero(self):
        d=self.run_task([[0]*6,[0]*6]).to_dict()
        self.assertEqual(d['risk'],0.);self.assertEqual(d['utilization'],0.)

    def test_stage_radius_uses_verified_upper_plus_explicit_delta(self):
        cfg=replace(CFG,residual_slack_absolute=2e-7,residual_slack_relative=1e-6)
        d=self.run_task([[0,0,3,0,0,0]],config=cfg).to_dict()
        self.assertAlmostEqual(d['delta_scaled'],2e-7+1e-6*d['risk'],places=12)
        self.assertAlmostEqual(d['stage2']['record']['radius'],d['risk']+d['delta_scaled'],places=12)

    def test_exact_duplicate_blocks_preserve_full_scenario_mapping(self):
        ws=[[0,0,.5,0,0,0],[0,0,.5,0,0,0],[0,0,.5,0,0,0]]
        packed=self.run_task(ws).to_dict();full=self.run_task(ws,config=replace(CFG,deduplicate_exact_demands=False)).to_dict()
        self.assertEqual(packed['scenario_count'],3);self.assertEqual(packed['unique_scenario_count'],1)
        self.assertEqual(len(packed['per_original_scenario_residual_stage2']),3)
        self.assertAlmostEqual(packed['risk'],full['risk'],places=8)
        self.assertAlmostEqual(packed['utilization'],full['utilization'],places=7)
        self.assertEqual(full['stage1']['shape']['variables'],97)
        task=demands(self.a.candidate_id,self.a.candidate_hash,ws)
        self.assertEqual(verify_original_scenarios(self.a.matrix,self.b.matrix,self.b.bounds_n,task,CFG,packed)['original_scenarios_verified'],3)
        bad=deepcopy(packed);bad['unique_required_wrenches'][0][2]+=.01
        with self.assertRaises(ValueError):verify_original_scenarios(self.a.matrix,self.b.matrix,self.b.bounds_n,task,CFG,bad)

    def test_repeated_contacts_share_force_budget(self):
        spec=replace(self.spec,contacts=self.spec.contacts+tuple(replace(p,surface_id=p.surface_id+':duplicate_numerical_fixture') for p in self.spec.contacts))
        a=build_primitive_wrenches(spec,friction=MU,k=16);b=build_capacity_constraints(a,finger_budgets={'left':capacity(1),'right':capacity(1)})
        one=self.run_task([[0,0,3,0,0,0]]).to_dict();two=self.run_task([[0,0,3,0,0,0]],a=a,b=b).to_dict()
        self.assertEqual(len(b.rows),2);self.assertAlmostEqual(one['risk'],two['risk'],places=6)
        self.assertAlmostEqual(one['utilization'],two['utilization'],places=6)

    def test_reference_frame_and_identity(self):
        task=demands(self.a.candidate_id,self.a.candidate_hash,[[0]*6])
        with self.assertRaises(ValueError):solve_task(self.a,self.b,replace(task,candidate_hash='f'*64),config=CFG)
        with self.assertRaises(ValueError):solve_task(self.a,self.b,replace(task,reference_point_m=(.01,0,0)),config=CFG)
        with self.assertRaises(ValueError):replace(task,frame='world')
        shift=np.array([0,.01,.02]);w=np.array([0,0,.5,0,0,0.]);w[3:]-=np.cross(shift,w[:3])
        a=build_primitive_wrenches(replace(self.spec,reference_point_m=tuple(shift)),friction=MU,k=16)
        b=build_capacity_constraints(a,finger_budgets={'left':capacity(1),'right':capacity(1)})
        self.assertLess(self.run_task([w],a=a,b=b).payload['risk'],1e-7)

    def test_scaling_and_near_degenerate_demand(self):
        ws=[[0,0,.5,1e-11,0,0]]
        one=self.run_task(ws).to_dict();two=self.run_task(ws,config=replace(CFG,reference_force_n=2.)).to_dict()
        self.assertAlmostEqual(one['risk'],2*two['risk'],places=8)
        self.assertGreater(one['risk'],0.)

    def test_incomplete_required_scenario_is_not_dropped(self):
        t=demands(self.a.candidate_id,self.a.candidate_hash,[[0]*6])
        t=replace(t,scenarios=t.scenarios+(RequiredWrench('missing',None,'missing_inertia'),))
        with patch('grasp_b.contact_refinement.relaxed_solver.linprog',side_effect=AssertionError('must not solve incomplete task')):
            d=solve_task(self.a,self.b,t,config=CFG).to_dict()
        self.assertEqual(d['status'],'missing_parameters');self.assertEqual(d['lp_calls'],0)

    def test_missing_capacity_and_invalid_input(self):
        b=build_capacity_constraints(self.a);t=demands(self.a.candidate_id,self.a.candidate_hash,[[0]*6])
        self.assertEqual(solve_task(self.a,b,t,config=CFG).payload['status'],'missing_parameters')
        with self.assertRaises(ValueError):RequiredWrench('nan',(float('nan'),)*6)
        with self.assertRaises(ValueError):replace(CFG,characteristic_length_m=0)
        with self.assertRaises(ValueError):replace(t,scenarios=t.scenarios+t.scenarios)

    def test_solver_exceptions_cannot_become_physical_infeasibility(self):
        t=demands(self.a.candidate_id,self.a.candidate_hash,[[0]*6])
        with patch('grasp_b.contact_refinement.relaxed_solver.linprog',side_effect=RuntimeError('backend unavailable')) as runner:
            d=solve_task(self.a,self.b,t,config=CFG).to_dict()
        self.assertEqual(d['status'],'numerical_error');self.assertIsNone(d['risk'])
        self.assertEqual(runner.call_count,3);self.assertEqual(d['lp_calls'],3)

    def test_exact_cache_and_corruption_rejection(self):
        cache=ExactTaskCache();t=demands(self.a.candidate_id,self.a.candidate_hash,[[0]*6])
        first=solve_task(self.a,self.b,t,config=CFG,cache=cache)
        with patch('grasp_b.contact_refinement.relaxed_solver.linprog',side_effect=AssertionError('exact cache must avoid solve')):
            second=solve_task(self.a,self.b,t,config=CFG,cache=cache)
        self.assertIs(first,second);self.assertEqual(cache.hits,1)
        other=solve_task(self.a,self.b,t,config=replace(CFG,residual_slack_absolute=2e-7),cache=cache)
        self.assertNotEqual(other.payload['input_hash'],first.payload['input_hash'])
        cache.records[first.payload['input_hash']]={}
        with self.assertRaises(ValueError):solve_task(self.a,self.b,t,config=CFG,cache=cache)

    def test_serialization_and_independent_certificate_rejection(self):
        result=self.run_task([[0,0,3,0,0,0]]);d=result.to_dict()
        self.assertEqual(result.to_json(),TaskLexResult.from_json(result.to_json()).to_json())
        with self.assertRaises(TypeError):result.payload['stage1']['status']='changed'
        bad=deepcopy(d['stage1']['record']);bad['x'][-1]=0.
        self.assertFalse(verify_stage(self.a.matrix,self.b.matrix,self.b.bounds_n,d['unique_required_wrenches'],CFG,bad)['valid'])
        bad=d.copy();bad['risk']=999.
        with self.assertRaises(ValueError):TaskLexResult(bad)


if __name__=='__main__':unittest.main()
