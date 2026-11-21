"""Synthetic frozen-loader fixtures and mocked A calls; no physics execution."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import math
import unittest
from unittest.mock import Mock, patch

from grasp_a.contracts import Candidate, Pose
from grasp_a.control import scene_randomization
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from grasp_b import batch_adapter as ba
from grasp_b.wrench_v2.episode_store import EpisodeStore, StoreError, digest


class BatchTests(unittest.TestCase):
    @contextmanager
    def case(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EpisodeStore(tmp); config = asdict(SequenceConfig())
            physical = dict(controller_version='staged_sequences_v1', evaluation_version='physical_outcomes_v1')
            jobs = {}; scopes = {}; candidates = []
            for label, tasks in [('A', ['transport_place']*6+['handover_transfer']*4), ('B', ['pour_water']*5)]:
                scope_jobs = {}; links = {}
                for i, task in enumerate(tasks):
                    cid = label+str(i); candidates.append(Candidate(cid, 'body', 'MOCK', .077, 0., Pose((0,0,0),(0,0,0,1)), Pose((0,0,-.08),(0,0,0,1))))
                    job = dict(task=task, scene_seed=2+i%3, candidate_id=cid,
                        candidate_pool_hash='MOCK_POOL', scene_config=asdict(scene_randomization('wide_strict_v2')),
                        sequence_config=config, physical_resources=physical, liquid_mode='pbf',
                        expected_observation_config={'source':'MOCK'}, expected_initial_pose_estimate={'position':[.55,0,.645],'quaternion':[0,0,0,1]},
                        gui=False, media_dir=None, show_landmarks=True)
                    key = digest(job); scope_jobs[key] = job; jobs[key] = job
                    names = ['M1','M2'] if i < (5 if label == 'A' else 2) else ['M0']
                    links[key] = [dict(method=m, episode_key=key, task=task, scene_seed=job['scene_seed'], selected_id=cid) for m in names]
                scopes[label] = dict(batch_scope_hash='MOCK_SCOPE_'+label, plan_hash='MOCK_PLAN',
                    ordered_episode_keys=list(scope_jobs), episodes=scope_jobs, method_links=links,
                    baseline_completed={}, source_hashes={}, parent_canary_hash='MOCK_PARENT', parent_stop_record={'status':'stopped'})
            proto = SimpleNamespace(protocol_hash='MOCK_PROTOCOL', resources={'mock':True})
            # Immutable historical records are outside both scopes.
            for name in ('transport', 'handover', 'pour', 'canary_stop', 'original_authorization'):
                store.write_once('history/'+name+'.json', dict(mock_history=name, immutable=True))
            with patch.object(ba, '_load', return_value=(proto, candidates, scopes, {'version':'remaining_pilot_execution_policy_v1'})), \
                 patch.object(ba, 'frozen_preflight', return_value=(proto, candidates, {'episodes':jobs}, {})), \
                 patch('pybullet.stepSimulation', return_value=None):
                manifests = {k:ba.prepare_batch(store,k,scope_dir=tmp) for k in ('A','B')}
                yield store, manifests, jobs

    def approval(self, manifest):
        request = ba.authorization_request(manifest)
        return ba.BatchAuthorization(**{k:manifest[k] for k in ('protocol_hash','plan_hash','batch_id',
            'ordered_episode_keys','manifest_hash','adapter_source_sha256','resource_identity','policy_version')},
            authorization_identity_hash=request['authorization_identity_hash'], approved_by='MOCK TEST ONLY',
            user_authorization_text=request['required_user_authorization_text'])

    def raw(self, job, failure=False):
        spec = task_spec(job['task'], SequenceConfig(**job['sequence_config']))
        stages = [dict(id=r['id'],control_status='completed',start_s=float(i),end_s=float(i+1)) for i,r in enumerate(spec['stages'])]
        physical = {r['id']:dict(physical_pass=True,physical_checks={'mock':True},evidence={'mock':True}) for r in spec['stages']}
        elapsed = float(len(stages))
        checks = {k:True for k in ba.SUCCESS_KEYS}
        if failure:
            elapsed = 1.;checks['sequence_complete']=False;checks['terminal_outcome']=False
            stages[0].update(control_status='failed',start_s=0.,end_s=1.)
            for row in stages[1:]:
                row.update(control_status='not_executed',start_s=None,end_s=None)
                physical[row['id']]=dict(physical_pass=False,physical_checks={},evidence=None)
        raw = dict(schema_version='sequence_episode_v1',task_id=job['task'],seed=job['scene_seed'],
            selected_id=job['candidate_id'],candidate_sha256=job['candidate_pool_hash'],
            controller_version='staged_sequences_v1',evaluation_version='physical_outcomes_v1',
            scene_randomization=job['scene_config'],observation_config=job['expected_observation_config'],
            initial_object_pose_estimate=job['expected_initial_pose_estimate'],task_spec=spec,task_spec_sha256=digest(spec),
            status='execution_failed' if failure else 'completed',success=not failure,
            failure_reason='pregrasp_arrival_timeout' if failure else None,
            stages=stages,stage_evaluation=physical,success_checks=checks,terminal_checks={} if failure else {'mock':True},
            trajectory=[],elapsed_s=elapsed,terminal_samples=0 if failure else 240,
            final_evidence={'donor_contact':not failure},primitive_log=[],ik_diagnostics=[],
            feasibility={'candidate_id':job['candidate_id'],'feasible':True,'reasons':[]},
            collision=False,collision_events=[],dropped=False,max_fluid_compression=0.)
        if job['task']=='pour_water':
            water=dict(finite_state=True,accounting_error_kg=0.,max_compression=0.,initial_mass_kg=.1,
                       fluid_sample_count=256,spill_ratio=0.,transfer_ratio=1.)
            raw.update(liquid_mode='pbf',liquid_model={'model':'position_based_fluid'},liquid_initial_check=deepcopy(water))
            raw['final_evidence']['water']=water
        return raw

    def runner(self,jobs,change=None):
        def call(task,seed,cid,candidates,**kwargs):
            import pybullet
            job = next(v for v in jobs.values() if (v['task'],v['scene_seed'],v['candidate_id'])==(task,seed,cid))
            self.assertEqual(kwargs['liquid'],job['liquid_mode']);self.assertIsNone(kwargs['observation_config'])
            raw = self.raw(job)
            if change: raw = change(job,raw)
            if isinstance(raw,Exception):raise raw
            elapsed = raw.get('elapsed_s',0)
            steps = round(elapsed*240) if type(elapsed) in (int,float) and math.isfinite(elapsed) else 0
            steps += 120 if task=='pour_water' else 0
            for _ in range(steps):pybullet.stepSimulation()
            return raw
        return Mock(side_effect=call)

    def assert_order(self,runner,m):
        actual=[c.args[:3] for c in runner.call_args_list]
        expected=[(m['episodes'][k]['task'],m['episodes'][k]['scene_seed'],m['episodes'][k]['candidate_id']) for k in m['ordered_episode_keys']]
        self.assertEqual(actual,expected)

    def test_no_authorization_zero_calls_no_writes(self):
        with self.case() as (s,ms,jobs):
            before=set(s.root.rglob('*.json'));runner=self.runner(jobs)
            self.assertEqual(ba.run_batch(s,ms['A'],runner=runner)['status'],'authorization_required')
            runner.assert_not_called();self.assertEqual(before,set(s.root.rglob('*.json')))

    def test_readiness_is_read_only_and_B_gate_is_explicit(self):
        with self.case() as (s,ms,jobs):
            before=set(s.root.rglob('*.json'))
            self.assertTrue(ba.inspect_batch(s,ms['A'])['ready_for_physical_execution'])
            self.assertFalse(ba.inspect_batch(s,ms['B'])['ready_for_physical_execution'])
            self.assertFalse(ba.inspect_batch(s,ms['A'])['execution_authorized'])
            self.assertEqual(before,set(s.root.rglob('*.json')))

    def test_resource_identity_failure_calls_nothing(self):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs)
            with patch.object(ba,'_load',side_effect=StoreError('source/asset/dependency mismatch')),self.assertRaises(StoreError):
                ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            runner.assert_not_called()

    def bad_auth(self,**changes):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs)
            with self.assertRaises(ValueError):ba.run_batch(s,ms['A'],authorization=replace(self.approval(ms['A']),**changes),runner=runner)
            runner.assert_not_called()

    def test_wrong_batch_hash(self):self.bad_auth(manifest_hash='wrong')
    def test_wrong_source_hash(self):self.bad_auth(adapter_source_sha256='wrong')
    def test_wrong_ordered_keys(self):self.bad_auth(ordered_episode_keys=('wrong',))
    def test_wrong_resource_identity(self):self.bad_auth(resource_identity='wrong')
    def test_wrong_policy_version(self):self.bad_auth(policy_version='wrong')
    def test_wrong_authorization_identity(self):self.bad_auth(authorization_identity_hash='wrong')
    def test_generic_text_rejected(self):self.bad_auth(user_authorization_text='YES')

    def test_cross_batch_both_directions_rejected(self):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs)
            for target,other in [('A','B'),('B','A')]:
                with self.assertRaises(ValueError):ba.run_batch(s,ms[target],authorization=self.approval(ms[other]),runner=runner)
            runner.assert_not_called()

    def test_generic_and_old_authorizations_rejected(self):
        from grasp_b.canary_adapter import CanaryAuthorization
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs)
            for auth in (True,'APPROVED',CanaryAuthorization('p','q','c',(),'s','MOCK')):
                with self.assertRaises(ValueError):ba.run_batch(s,ms['A'],authorization=auth,runner=runner)
            runner.assert_not_called()

    def test_manifest_and_source_drift_zero_calls(self):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs);bad=deepcopy(ms['A']);bad['ordered_episode_keys'].reverse()
            with self.assertRaises(StoreError):ba.run_batch(s,bad,authorization=self.approval(ms['A']),runner=runner)
            with patch.object(ba,'source_hash',return_value='wrong'),self.assertRaises(StoreError):
                ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            runner.assert_not_called()

    def test_no_selector_ranking_or_feasibility_calls(self):
        with self.case() as (s,ms,jobs), \
             patch('grasp_b.wrench_v2.pilot.load_bundle',side_effect=AssertionError('legacy loader')), \
             patch('grasp_b.wrench_v2.pilot.random_ranking',side_effect=AssertionError('M0')), \
             patch('grasp_b.wrench_v2.m1.score_prepared',side_effect=AssertionError('M1')), \
             patch('grasp_b.wrench_v2.m2.score_trajectory',side_effect=AssertionError('M2')), \
             patch('grasp_a.sequence.get_sequence_candidates',side_effect=AssertionError('feasibility')):
            runner=self.runner(jobs);result=ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(result['status'],'complete');self.assert_order(runner,ms['A'])

    def test_valid_task_failure_continues_all_remaining(self):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs,lambda j,r:self.raw(j,failure=True))
            result=ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(result['status'],'complete');self.assertEqual(result['new_calls'],10)
            self.assertEqual(result['summary']['physical_counts'],{'task_failure':10});self.assert_order(runner,ms['A'])
            again=ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(again['new_calls'],0);self.assertEqual(again['reused'],10)

    def test_stage_failure_categories_remain_valid_observations(self):
        with self.case() as (_,ms,_):
            for label,stage in [('A','pregrasp'),('A','approach'),('A','close'),('A','lift'),
                                ('A','receiver_approach'),('B','tilt')]:
                jobs=list(ms[label]['episodes'].values())
                job=next(j for j in jobs if stage in [s['id'] for s in task_spec(j['task'],SequenceConfig(**j['sequence_config']))['stages']])
                raw=self.raw(job);index=[r['id'] for r in raw['stages']].index(stage)
                raw.update(status='execution_failed',success=False,failure_reason=stage+'_arrival_timeout',
                           elapsed_s=float(index+1),terminal_samples=0,terminal_checks={})
                raw['success_checks']['sequence_complete']=False
                raw['success_checks']['terminal_outcome']=False
                raw['stages'][index]['control_status']='failed'
                for row in raw['stages'][index+1:]:
                    row.update(control_status='not_executed',start_s=None,end_s=None)
                    raw['stage_evaluation'][row['id']]=dict(physical_pass=False,physical_checks={},evidence=None)
                self.assertEqual(ba.validate_outcome(raw,job),'task_failure')

    def test_completed_controller_but_failed_contact_is_task_failure(self):
        with self.case() as (_,ms,_):
            job=next(iter(ms['A']['episodes'].values()));raw=self.raw(job)
            raw['success']=False;raw['success_checks']['stage_outcomes']=False
            raw['stage_evaluation']['close']['physical_checks']={'contact_formed':False}
            raw['stage_evaluation']['close']['physical_pass']=False
            self.assertEqual(ba.validate_outcome(raw,job),'task_failure')

    def test_episode_persistence_error_stops_and_never_retries_attempt(self):
        with self.case() as (s,ms,jobs):
            write=s.write_once
            def fail(name,*args,**kwargs):
                if name.startswith('episodes/'):raise OSError('MOCK disk failure')
                return write(name,*args,**kwargs)
            runner=self.runner(jobs)
            with patch.object(s,'write_once',side_effect=fail):
                result=ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(result['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)
            with self.assertRaises(StoreError):ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(runner.call_count,1)

    def test_infrastructure_error_stops_immediately_no_retry(self):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs,lambda j,r:RuntimeError('MOCK infra'))
            result=ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(result['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)
            with self.assertRaises(StoreError):ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            self.assertEqual(runner.call_count,1)

    def test_malformed_identity_nonfinite_stop_next_dispatch(self):
        for change in (lambda r:dict(r,selected_id='wrong'),lambda r:{'success':False},
                       lambda r:dict(r,elapsed_s=float('nan')),lambda r:dict(r,status='simulation_error')):
            with self.subTest(change=change),self.case() as (s,ms,jobs):
                runner=self.runner(jobs,lambda j,r:change(r))
                result=ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
                self.assertEqual(result['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)

    def test_cached_outcomes_reused_and_method_denominators(self):
        with self.case() as (s,ms,jobs):
            before={str(p):p.read_bytes() for p in (s.root/'history').glob('*.json')}
            runner=self.runner(jobs);a=self.approval(ms['A'])
            first=ba.run_batch(s,ms['A'],authorization=a,runner=runner)
            second=ba.run_batch(s,ms['A'],authorization=a,runner=runner)
            self.assertEqual(first['new_calls'],10);self.assertEqual(second['new_calls'],0)
            self.assertEqual(second['reused'],10);self.assertEqual(runner.call_count,10)
            self.assertEqual(second['summary']['independent_episode_denominator'],10)
            self.assertEqual(second['summary']['method_link_denominator'],15)
            self.assertEqual(sum(second['summary']['method_link_counts'].values()),15)
            self.assert_order(runner,ms['A'])
            for path,data in before.items():self.assertEqual(Path(path).read_bytes(),data)

    def test_started_without_outcome_not_retried(self):
        with self.case() as (s,ms,jobs):
            m=ms['A'];key=m['ordered_episode_keys'][3]
            s.write_once('attempts/'+key+'.json',dict(episode_key=key,physical_identity=m['episodes'][key]))
            runner=self.runner(jobs)
            with self.assertRaises(StoreError):ba.run_batch(s,m,authorization=self.approval(m),runner=runner)
            runner.assert_not_called()

    def test_corruption_in_late_cache_blocks_all_calls(self):
        with self.case() as (s,ms,jobs):
            key=ms['A']['ordered_episode_keys'][-1];path=s.path('episodes/'+key+'.json');path.parent.mkdir();path.write_text('{}')
            runner=self.runner(jobs)
            with self.assertRaises(StoreError):ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            runner.assert_not_called()

    def test_corrupt_cached_authorization_chain_never_reexecutes(self):
        with self.case() as (s,ms,jobs):
            m=ms['A'];a=self.approval(m);runner=self.runner(jobs)
            ba.run_batch(s,m,authorization=a,runner=runner)
            key=m['ordered_episode_keys'][0];record=s.read('episodes/'+key+'.json')
            s.path('batches/'+m['manifest_hash']+'/authorizations/'+record['authorization_hash']+'.json').write_text('{}')
            with self.assertRaises(StoreError):ba.run_batch(s,m,authorization=a,runner=runner)
            self.assertEqual(runner.call_count,10)

    def test_interruption_between_episodes_safely_resumes(self):
        with self.case() as (s,ms,jobs):
            m=ms['A'];auth=self.approval(m);runner=self.runner(jobs);prepare=ba.prepare_batch;count=0
            def interrupt(*args,**kwargs):
                nonlocal count
                count+=1
                if count==6:raise StoreError('MOCK interruption before third claim')
                return prepare(*args,**kwargs)
            with patch.object(ba,'prepare_batch',side_effect=interrupt):first=ba.run_batch(s,m,authorization=auth,runner=runner)
            self.assertEqual(first['new_calls'],2);self.assertEqual(first['status'],'stopped_infrastructure')
            second=ba.run_batch(s,m,authorization=auth,runner=runner)
            self.assertEqual(second['new_calls'],8);self.assertEqual(second['reused'],2)
            self.assertEqual(second['status'],'complete');self.assert_order(runner,m)

    def test_B_requires_A_complete_then_executes_five_only(self):
        with self.case() as (s,ms,jobs):
            runner=self.runner(jobs)
            with self.assertRaises(StoreError):ba.run_batch(s,ms['B'],authorization=self.approval(ms['B']),runner=runner)
            runner.assert_not_called()
            ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=runner)
            runner.reset_mock()
            result=ba.run_batch(s,ms['B'],authorization=self.approval(ms['B']),runner=runner)
            self.assertEqual(result['new_calls'],5);self.assertEqual(result['summary']['method_link_denominator'],7)
            self.assert_order(runner,ms['B'])

    def test_pbf_invalid_initialization_stops(self):
        with self.case() as (s,ms,jobs):
            ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=self.runner(jobs))
            def bad(j,r):r['liquid_initial_check']=None;return r
            runner=self.runner(jobs,bad)
            result=ba.run_batch(s,ms['B'],authorization=self.approval(ms['B']),runner=runner)
            self.assertEqual(result['status'],'stopped_infrastructure');self.assertEqual(runner.call_count,1)

    def test_pbf_low_transfer_is_task_failure_not_infrastructure(self):
        with self.case() as (s,ms,jobs):
            ba.run_batch(s,ms['A'],authorization=self.approval(ms['A']),runner=self.runner(jobs))
            def failed(j,r):
                r['success']=False;r['success_checks']['terminal_outcome']=False;r['terminal_checks']={'received':False}
                r['final_evidence']['water']['transfer_ratio']=.2;return r
            runner=self.runner(jobs,failed)
            result=ba.run_batch(s,ms['B'],authorization=self.approval(ms['B']),runner=runner)
            self.assertEqual(result['status'],'complete');self.assertEqual(runner.call_count,5)
            self.assertEqual(result['summary']['physical_counts'],{'task_failure':5})

    def test_real_frozen_loader_is_pure_read_only(self):
        path=ba.ROOT/'results/local/task08b0_pilot64_20261107_final'
        if not path.exists():self.skipTest('local frozen pilot artifacts unavailable')
        store=EpisodeStore(path);before={str(p):p.stat().st_mtime_ns for p in path.rglob('*.json')}
        with patch('grasp_b.wrench_v2.pilot.load_bundle',side_effect=AssertionError('legacy loader')), \
             patch('grasp_b.wrench_v2.pilot.random_ranking',side_effect=AssertionError('selector')), \
             patch('grasp_a.sequence.get_sequence_candidates',side_effect=AssertionError('filter')), \
             patch('grasp_a.sequence.run_sequence',side_effect=AssertionError('episode')):
            from grasp_b.canary_adapter import resources
            from grasp_b.wrench_v2.episode_store import digest,StoreError
            # Preparing dispatch requires LIVE physical identity (semantics A).
            # Reading old outcomes requires checksum/provenance, not live parity.
            compatible=digest(resources())==digest(store.read('protocol.json')['resources'])
            for label,n in [('A',10),('B',5)]:
                if compatible:
                    manifest=ba.prepare_batch(store,label)
                    self.assertEqual(len(manifest['ordered_episode_keys']),n)
                else:
                    with self.assertRaisesRegex(StoreError,'runtime source/asset/dependency mismatch'):
                        ba.prepare_batch(store,label)
            original=store.read('episode_plan.json')
            for p in (path/'episodes').glob('*.json'):
                record=store.read('episodes/'+p.name)
                self.assertEqual(ba.validate_outcome(record['raw_outcome'],original['episodes'][p.stem]),record['category'])
        self.assertEqual(before,{str(p):p.stat().st_mtime_ns for p in path.rglob('*.json')})


if __name__=='__main__':unittest.main()
