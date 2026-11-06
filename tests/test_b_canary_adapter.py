"""Synthetic subset authorization tests; no PyBullet connection/rollout."""
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from tests import test_b_wrench_pilot as fixtures
from grasp_b.wrench_v2.pilot import freeze_bundle,ExecutionAuthorization
from grasp_b.wrench_v2.episode_store import EpisodeStore,StoreError,digest
from grasp_b.canary_adapter import make_canary_manifest,CanaryAuthorization,run_canary,frozen_preflight


class CanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PilotTests.setUpClass()
        cls.fixture=fixtures.PilotTests()
        cls.protocol=cls.fixture.protocol

    def bundle(self,path):
        rows=self.fixture.trials()
        for r in rows:r['filter_metrics']={'step_guard_enforced':True,'physical_step_calls':0,'mock_evidence':True}
        s=EpisodeStore(path);p=freeze_bundle(s,self.protocol,self.fixture.candidates,rows)
        keys=[next(k for k,v in p['episodes'].items() if v['task']==t and v['scene_seed']==seed)
              for t,seed in [('transport_place',2),('handover_transfer',2),('pour_water',3)]]
        m=make_canary_manifest(s,keys,expected_protocol=self.protocol.protocol_hash,expected_plan=digest(p))
        a=CanaryAuthorization(m['protocol_hash'],m['plan_hash'],m['canary_hash'],tuple(keys),m['adapter_sha256'],'MOCK UNIT TEST ONLY')
        return s,p,m,a

    def runner(self,plan,fail_task=None):
        def call(task,seed,cid,cs,**kw):
            import pybullet as p
            job=next(v for v in plan['episodes'].values() if (v['task'],v['scene_seed'],v['candidate_id'])==(task,seed,cid))
            self.assertEqual(kw['liquid'],'pbf')
            # Caller patches the ORIGINAL step function to Mock. No real step.
            for _ in range(123 if task=='pour_water' else 3):p.stepSimulation()
            ok=task!=fail_task
            raw=dict(task_id=task,seed=seed,selected_id=cid,candidate_sha256=self.protocol.candidate_pool_hash,
                controller_version='staged_sequences_v1',evaluation_version='physical_outcomes_v1',
                scene_randomization=job['scene_config'],observation_config=job['expected_observation_config'],
                initial_object_pose_estimate=job['expected_initial_pose_estimate'],status='completed',success=ok,
                schema_version='sequence_episode_v1',success_checks={'mock':ok},terminal_checks={'mock':ok},
                stages=[dict(id='terminal',control_status='completed')],stage_evaluation={'terminal':{'physical_pass':ok,'evidence':{'mock':True}}},
                trajectory=[dict(stage='terminal')],elapsed_s=3/240,collision=False,dropped=False,
                final_evidence={},liquid_mode='pbf' if task=='pour_water' else None)
            if task=='pour_water':
                raw.update(liquid_model={'model':'position_based_fluid'},liquid_initial_check={'finite_state':True})
                raw['final_evidence']['water']={'finite_state':True,'transfer_ratio':.95,'spill_ratio':.01}
            return raw
        return Mock(side_effect=call)

    def test_preflight_never_calls_any_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp)
            with patch('grasp_b.wrench_v2.pilot.random_ranking',side_effect=AssertionError('NO selector')):
                frozen_preflight(s,expected_protocol=m['protocol_hash'],expected_plan=m['plan_hash'])

    def test_no_authorization_no_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);runner=self.runner(p)
            self.assertEqual(run_canary(s,m,runner=runner)['status'],'authorization_required')
            runner.assert_not_called()

    def test_generic_or_wrong_subset_authorization_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);runner=self.runner(p)
            invalid=[ExecutionAuthorization(m['protocol_hash'],m['plan_hash'],'test','AUTHORIZE_PHYSICAL_EXECUTION'),
                'AUTHORIZE_PHYSICAL_EXECUTION',replace(a,canary_hash='bad'),replace(a,approved_episode_keys=tuple(reversed(a.approved_episode_keys))),
                replace(a,adapter_sha256='bad'),replace(a,plan_hash='bad')]
            for value in invalid:
                with self.subTest(value=value),self.assertRaises(ValueError):run_canary(s,m,authorization=value,runner=runner)
            runner.assert_not_called()

    def test_only_three_mock_calls_steps_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);runner=self.runner(p)
            with patch('pybullet.stepSimulation',return_value=None):
                result=run_canary(s,m,authorization=a,runner=runner)
                self.assertEqual(result['status'],'canary_complete');self.assertEqual(runner.call_count,3)
                again=run_canary(s,m,authorization=a,runner=runner)
            self.assertEqual(again['new_calls'],0);self.assertEqual(runner.call_count,3)
            self.assertEqual(len(list((s.root/'episodes').glob('*.json'))),3)
            values=[s.read('episodes/'+k+'.json') for k in m['ordered_episode_keys']]
            self.assertEqual([v['total_stepSimulation_calls'] for v in values],[3,3,123])
            self.assertEqual(values[-1]['canary_evidence']['pre_episode_step_difference'],120)

    def test_failure_stops_remaining_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);runner=self.runner(p,fail_task='transport_place')
            with patch('pybullet.stepSimulation',return_value=None):
                result=run_canary(s,m,authorization=a,runner=runner)
                self.assertEqual(result['status'],'stopped_canary_failure');self.assertEqual(runner.call_count,1)
                self.assertEqual(run_canary(s,m,authorization=a,runner=runner)['status'],'stopped_cached_failure')
            self.assertEqual(runner.call_count,1)

    def test_started_without_outcome_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);key=m['ordered_episode_keys'][0]
            s.write_once('attempts/'+key+'.json',dict(episode_key=key,physical_identity=p['episodes'][key]))
            runner=self.runner(p)
            self.assertEqual(run_canary(s,m,authorization=a,runner=runner)['status'],'missing_outcome_requires_reconciliation')
            runner.assert_not_called()

    def test_manifest_modification_or_code_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);bad=deepcopy(m);bad['ordered_episode_keys'].reverse()
            with self.assertRaises((ValueError,StoreError)):run_canary(s,bad,authorization=a,runner=Mock())
            with patch('grasp_b.canary_adapter.extension_sha256',return_value='0'*64):
                with self.assertRaises(StoreError):run_canary(s,m,authorization=a,runner=Mock())

    def test_raw_output_identity_error_is_infrastructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,m,a=self.bundle(tmp);runner=Mock(return_value={'success':True})
            r=run_canary(s,m,authorization=a,runner=runner)
            self.assertEqual(r['status'],'stopped_canary_failure');self.assertEqual(runner.call_count,1)
            record=s.read('episodes/'+m['ordered_episode_keys'][0]+'.json')
            self.assertEqual(record['category'],'infrastructure_error')
            self.assertIsNone(record['raw_outcome']);self.assertEqual(record['rejected_raw_outcome'],{'success':True})


if __name__=='__main__':unittest.main()
