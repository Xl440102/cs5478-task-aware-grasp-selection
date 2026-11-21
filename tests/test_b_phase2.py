"""Frozen loader, failure injection and serial mock dispatch; zero real physics."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock,patch

from grasp_b.contact_refinement import phase2 as p
from grasp_b.contact_refinement.phase2_authorization import MOCK_DOMAIN
from grasp_b.wrench_v2.episode_store import EpisodeStore,StoreError,digest
from tests.test_b_phase2_authorization import mock_approval
from tests import test_b_batch_adapter as batch_fixtures


@unittest.skipUnless(p.RUN_A.exists() and p.RUN_B.exists(), 'optional historical Task09 phase bundles are not included in the source release')
class FrozenLoaderTests(unittest.TestCase):
    def test_full_live_phase1_loader_no_generation_no_ranking_no_feasibility(self):
        with patch('grasp_b.multifamily.generate_multifamily',side_effect=AssertionError('generate')) as gen, \
             patch('grasp_b.wrench_v2.evaluation.random_ranking',side_effect=AssertionError('M0')) as m0, \
             patch('grasp_a.sequence.get_sequence_candidates',side_effect=AssertionError('filter')) as feas, \
             patch('grasp_b.wrench_v2.m1.score_prepared',side_effect=AssertionError('M1')) as m1, \
             patch('grasp_b.wrench_v2.m2.score_trajectory',side_effect=AssertionError('M2')) as m2, \
             patch('grasp_b.contact_refinement.relaxed_solver.solve_task',side_effect=AssertionError('relaxed')) as relaxed, \
             patch('grasp_b.wrench_v2.pilot.load_bundle',side_effect=AssertionError('legacy')) as old:
            bundle=p.load_frozen()
        self.assertEqual(bundle['validation']['decisions'],45)
        self.assertEqual(bundle['validation']['abstentions'],8)
        self.assertTrue(all(g.call_count==0 for g in (gen,m0,feas,m1,m2,relaxed,old)))

    def test_candidate_content_hash_rejected(self):
        b=p.load_frozen();catalog=deepcopy(b['catalog']);catalog['candidates'][0]['gripper_width']+=.001
        with self.assertRaises(StoreError):p.raw_candidates(catalog)

    def test_readonly_store_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'absent'
            with self.assertRaises(StoreError):p.ReadOnlyStore(root)
            self.assertFalse(root.exists())

    def test_independent_runs_difference_rejected(self):
        original=p.ReadOnlyStore.read
        def changed(store,name):
            raw=original(store,name)
            if store.root==p.RUN_B and name=='method_registry.json':raw=dict(raw,registry_hash='wrong')
            return raw
        with patch.object(p.ReadOnlyStore,'read',changed),self.assertRaises(StoreError):p.load_frozen()


@unittest.skipUnless(p.RUN_A.exists() and p.RUN_B.exists(), 'optional historical Task09 phase bundles are not included in the source release')
class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=p.load_frozen()

    def setUp(self):
        self.loader=patch.object(p,'load_frozen',return_value=self.bundle);self.loader.start();self.addCleanup(self.loader.stop)
        self.canary,self.remainder=p.prepare_manifests(self.bundle)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'mock_store';self.store=None

    def raw(self,job,failure=False):
        result=batch_fixtures.BatchTests().raw(job['dispatch_contract'],failure=failure)
        result['scene_version']='hollow_cup_sequences_v1'
        return result

    def runner(self,change=None):
        def call(task,seed,cid,candidates,**kwargs):
            jobs=p.execution_jobs(self.bundle)
            job=next(v for v in jobs.values() if (v['dispatch_contract']['task'],v['dispatch_contract']['scene_seed'],v['dispatch_contract']['candidate_id'])==(task,seed,cid))
            self.assertEqual(p.candidate_sha256(candidates),self.bundle['catalog']['pool_hash'])
            self.assertEqual(kwargs['liquid'],'pbf')
            self.assertIsNone(kwargs['observation_config'])
            raw=self.raw(job)
            return change(job,raw) if change else raw
        return Mock(side_effect=call)

    def run_mock(self,runner=None,manifest=None):
        m=manifest or self.canary
        return p.run_batch(m,self.root,approval=mock_approval(m),runner=runner or self.runner())

    def test_preflight_no_store_no_attempts_no_outcomes(self):
        value=p.inspect_batch(self.canary,self.root,mock_only=True)
        self.assertTrue(value['valid']);self.assertFalse(self.root.exists())
        self.assertFalse(value['physical_execution_authorized'])

    def test_missing_wrong_authorization_zero_calls_and_writes(self):
        runner=self.runner()
        for approval in (None,True,'YES',{'authorized':True},mock_approval(self.remainder)):
            with self.assertRaises(StoreError):p.run_batch(self.canary,self.root,approval=approval,runner=runner)
        runner.assert_not_called();self.assertFalse(self.root.exists())

    def test_wrong_source_hash_zero_calls(self):
        runner=self.runner()
        with patch.object(p,'adapter_sources',return_value={'wrong.py':'0'*64}),self.assertRaises(StoreError):
            p.run_batch(self.canary,self.root,approval=mock_approval(self.canary),runner=runner)
        runner.assert_not_called()

    def test_wrong_physical_configuration_zero_calls(self):
        manifest=deepcopy(self.canary);key=manifest['ordered_episode_keys'][0]
        manifest['episodes'][key]['dispatch_contract']['liquid_mode']='proxy';manifest.pop('manifest_hash');manifest=p.seal(manifest,'manifest_hash')
        runner=self.runner()
        with self.assertRaises(StoreError):p.run_batch(manifest,self.root,approval=mock_approval(manifest),runner=runner)
        runner.assert_not_called()

    def test_reordered_or_other_episode_subset_zero_calls(self):
        runner=self.runner()
        for keys in (list(reversed(self.canary['ordered_episode_keys'])),self.canary['ordered_episode_keys'][:-1],self.remainder['ordered_episode_keys'][:3]):
            m=deepcopy(self.canary);m['ordered_episode_keys']=keys;m.pop('manifest_hash');m=p.seal(m,'manifest_hash')
            with self.assertRaises(StoreError):p.run_batch(m,self.root,approval=mock_approval(m),runner=runner)
        runner.assert_not_called()

    def test_exact_order_valid_failure_continues_and_reuse(self):
        first=self.canary['ordered_episode_keys'][0]
        runner=self.runner(lambda job,raw:self.raw(job,failure=job['identity']['phase1_episode_key']==first))
        value=self.run_mock(runner)
        self.assertEqual(value['status'],'complete');self.assertEqual(runner.call_count,3)
        self.assertEqual([c.args[:3] for c in runner.call_args_list],[(self.canary['episodes'][k]['dispatch_contract']['task'],self.canary['episodes'][k]['dispatch_contract']['scene_seed'],self.canary['episodes'][k]['dispatch_contract']['candidate_id']) for k in self.canary['ordered_episode_keys']])
        audit=p.completion_audit(self.canary,self.root,mock_only=True)
        self.assertTrue(audit['complete']);self.assertEqual(audit['counts'],{'task_failure':1,'success':2})
        self.assertEqual(audit['selected_method_links'],8)
        again=self.run_mock(runner);self.assertEqual(again['new_runner_calls'],0);self.assertEqual(again['reused'],3);self.assertEqual(runner.call_count,3)

    def test_infrastructure_exception_stops_no_retry(self):
        runner=Mock(side_effect=RuntimeError('injected infrastructure exception'))
        value=self.run_mock(runner);self.assertEqual(value['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)
        with self.assertRaises(StoreError):self.run_mock(runner)
        self.assertEqual(runner.call_count,1)

    def test_none_missing_raw_evidence_nonfinite_and_pbf_init_failure_stop(self):
        for change in (lambda job,raw:None,lambda job,raw:{'status':'completed'},lambda job,raw:dict(raw,elapsed_s=float('nan')),
                       lambda job,raw:dict(raw,status='simulation_error',success=False)):
            with tempfile.TemporaryDirectory() as tmp:
                runner=self.runner(change)
                value=p.run_batch(self.canary,Path(tmp)/'store',approval=mock_approval(self.canary),runner=runner)
                self.assertEqual(value['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)

    def test_pbf_initialization_schema_mismatch_stops(self):
        def change(job,raw):
            if job['dispatch_contract']['task']=='pour_water':raw.pop('liquid_initial_check')
            return raw
        runner=self.runner(change);value=self.run_mock(runner)
        self.assertEqual(value['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,2)

    def test_started_without_outcome_no_dispatch(self):
        self.root.mkdir();store=EpisodeStore(self.root)
        identity=p._safe_store_root(self.root,self.bundle,MOCK_DOMAIN)[1];store.write_once('store_identity.json',identity)
        key=self.canary['ordered_episode_keys'][0]
        store.write_once(MOCK_DOMAIN+'/attempts/'+key+'.json',{'started':True})
        runner=self.runner()
        with self.assertRaises(StoreError):self.run_mock(runner)
        runner.assert_not_called()

    def test_corrupt_checksum_rejected_without_overwrite(self):
        self.run_mock();key=self.canary['ordered_episode_keys'][0];path=self.root/MOCK_DOMAIN/'raw'/str(key+'.json')
        path.write_text('{}');runner=self.runner()
        with self.assertRaises(StoreError):self.run_mock(runner)
        self.assertEqual(path.read_text(),'{}');runner.assert_not_called()

    def test_missing_completion_after_crash_never_retried(self):
        self.run_mock();key=self.canary['ordered_episode_keys'][0]
        (self.root/MOCK_DOMAIN/'completions'/str(key+'.json')).unlink();runner=self.runner()
        with self.assertRaises(StoreError):self.run_mock(runner)
        runner.assert_not_called()

    def test_remainder_prerequisite_and_resume(self):
        runner=self.runner()
        with self.assertRaises(StoreError):self.run_mock(runner,self.remainder)
        runner.assert_not_called()
        self.run_mock(runner);self.assertEqual(runner.call_count,3)
        v=self.run_mock(runner,self.remainder);self.assertEqual(v['new_runner_calls'],17);self.assertEqual(runner.call_count,20)
        audit=p.completion_audit(self.remainder,self.root,mock_only=True);self.assertTrue(audit['complete'])
        self.assertEqual(audit['selected_method_links'],29)
        self.run_mock(runner,self.remainder);self.assertEqual(runner.call_count,20)

    def test_mock_domain_cannot_call_real_runner_or_mix_store(self):
        with self.assertRaises(StoreError):p.run_batch(self.canary,self.root,approval=mock_approval(self.canary),runner=None)
        self.assertFalse(self.root.exists())
        self.run_mock()
        with self.assertRaises(StoreError):p.inspect_batch(self.canary,self.root,mock_only=False)

    def test_historical_and_phase1_stores_forbidden(self):
        runner=self.runner()
        for root in (p.RUN_A,p.RUN_A/'nested',p.ROOT/'results/local/task08b0_pilot64_20261107_final/nested',p.ROOT/'grasp_a/newstore'):
            with self.assertRaises(StoreError):p.run_batch(self.canary,root,approval=mock_approval(self.canary),runner=runner)
        runner.assert_not_called()

    def test_all_physical_identities_disjoint_from_methods_prediction(self):
        for job in p.execution_jobs(self.bundle).values():
            payload=json.dumps(job['identity'])
            self.assertNotIn('wet_payload_hash',payload);self.assertNotIn('relaxed_m1_task_lex_v1',payload)
        keys=self.canary['ordered_episode_keys']+self.remainder['ordered_episode_keys']
        self.assertEqual(len(set(keys)),20)

    def test_no_custom_nonmock_runner(self):
        with self.assertRaises(StoreError):p.run_batch(self.canary,self.root,approval=mock_approval(self.canary),runner=lambda *a:None)


    def test_safe_resume_after_stop_between_completed_episodes(self):
        count=0
        def loader(*args,**kwargs):
            nonlocal count
            count+=1
            if count==6:raise StoreError('injected stop BEFORE next attempt')
            return self.bundle
        runner=self.runner()
        with patch.object(p,'load_frozen',side_effect=loader):value=self.run_mock(runner)
        self.assertEqual(value['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)
        value=self.run_mock(runner)
        self.assertEqual(value['status'],'complete');self.assertEqual(value['reused'],1);self.assertEqual(runner.call_count,3)

    def test_identity_mismatch_outcome_halts_without_retry(self):
        runner=self.runner(lambda job,raw:dict(raw,selected_id='different_candidate'))
        self.assertEqual(self.run_mock(runner)['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)
        with self.assertRaises(StoreError):self.run_mock(runner)
        self.assertEqual(runner.call_count,1)

    def test_duplicate_key_or_abstention_insertion_refused(self):
        m=deepcopy(self.canary);m['ordered_episode_keys'].append(m['ordered_episode_keys'][0]);m.pop('manifest_hash');m=p.seal(m,'manifest_hash')
        runner=self.runner()
        with self.assertRaises(StoreError):p.run_batch(m,self.root,approval=mock_approval(m),runner=runner)
        runner.assert_not_called()

    def test_independent_valid_outcome_without_attempt_chain_refused(self):
        self.run_mock();key=self.canary['ordered_episode_keys'][0]
        (self.root/MOCK_DOMAIN/'attempts'/str(key+'.json')).unlink();runner=self.runner()
        with self.assertRaises(StoreError):self.run_mock(runner)
        runner.assert_not_called()

    def test_completion_audit_preserves_global_abstention_denominator(self):
        self.run_mock();value=p.completion_audit(self.canary,self.root,mock_only=True)
        self.assertEqual(value['phase1_no_selection_decisions'],8)
        self.assertEqual(value['independent_completed_episodes'],3)
        self.assertEqual(value['selected_method_links'],8)


    def test_valid_container_with_wrong_cached_job_identity_rejected(self):
        self.run_mock();key=self.canary['ordered_episode_keys'][0]
        path=self.root/MOCK_DOMAIN/'episodes'/str(key+'.json')
        envelope=json.loads(path.read_text());envelope['payload']['execution_identity_hash']='0'*64
        envelope['sha256']=digest(envelope['payload']);path.write_text(json.dumps(envelope))
        runner=self.runner()
        with self.assertRaises(StoreError):self.run_mock(runner)
        runner.assert_not_called()


    def test_infrastructure_retains_complete_finite_raw_evidence(self):
        runner=self.runner(lambda job,raw:dict(raw,status='simulation_error',success=False))
        result=self.run_mock(runner);self.assertEqual(result['status'],'stopped_infrastructure')
        key=self.canary['ordered_episode_keys'][0]
        incident=EpisodeStore(self.root).read(MOCK_DOMAIN+'/incidents/'+key+'.json')
        self.assertEqual(incident['rejected_raw_outcome']['status'],'simulation_error')
        self.assertIn('stage_evaluation',incident['rejected_raw_outcome'])
        self.assertFalse((self.root/MOCK_DOMAIN/'episodes'/str(key+'.json')).exists())
