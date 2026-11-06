"""Phase2 tests use synthetic decisions and MOCK run_sequence, never rollouts."""
from copy import deepcopy
from dataclasses import asdict,replace,FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from grasp_a.contracts import Candidate,Pose,candidate_sha256
from grasp_a.control import scene_randomization
from grasp_a.sequence_contracts import SequenceConfig,SEQUENCE_TASKS
from grasp_b.wrench_v2.contacts import _plain
from grasp_b.wrench_v2.m1 import development_config
from grasp_b.wrench_v2.motion_prediction import MotionModel
from grasp_b.wrench_v2.dynamics import object_parameters_from_snapshot
from grasp_b.wrench_v2.wet_payload import ROOT,build_wet_payload,combine_wet_object
from grasp_b.wrench_v2.episode_store import EpisodeStore,StoreError,digest
from grasp_b.wrench_v2.pilot import (PilotProtocol,resources,freeze_bundle,load_bundle,build_plan,
    ExecutionAuthorization,execute_frozen_plan,execution_summary)
from grasp_b.wrench_v2.evaluation import random_ranking


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'results/local/task07_shared_feasible_20261106_final/summary.json'
        if path.exists():snapshot=json.loads(path.read_text())['static_snapshot']
        else:
            from grasp_b.wrench_v2.physics_snapshot import static_physics_snapshot
            snapshot=static_physics_snapshot(ROOT)
        dry=object_parameters_from_snapshot(snapshot,asset_path=ROOT/'assets/mug_hollow.urdf')
        water=build_wet_payload();wet,binding=combine_wet_object(dry,water)
        dm=dict(model_label='dry',dry_object_hash=digest(dry),combined_object_hash=digest(dry),object_parameters=_plain(dry))
        cls.candidates=[Candidate(f'fixture_{i:03d}','body','MOCK test fixture',.077,0.,Pose((0,0,0),(0,0,0,1)),Pose((0,0,-.08),(0,0,0,1))) for i in range(116)]
        cls.protocol=PilotProtocol(candidate_pool_hash=candidate_sha256(cls.candidates),resources=resources(),
            scene_config=asdict(scene_randomization('wide_strict_v2')),sequence_config=asdict(SequenceConfig()),
            scoring_config=development_config().to_dict(),prediction_models={SEQUENCE_TASKS[0]:dm,SEQUENCE_TASKS[1]:dm,
                'pour_water':dict(binding,object_parameters=_plain(wet))})

    def trials(self):
        result=[];c=self.candidates[0];proto=self.protocol
        for task in proto.tasks:
            for seed in proto.scene_seeds:
                record=dict(task_id=task,seed=seed,candidate_sha256=proto.candidate_pool_hash,
                    scene_randomization=_plain(proto.scene_config),task_spec=dict(config=_plain(proto.sequence_config)),
                    observation_config={'source':'MOCK external estimate'},initial_object_pose_estimate={'position':[.55,0,.645],'quaternion':[0,0,0,1]},
                    feasible_candidates=[c.public_metadata()],checks=[dict(candidate_id=q.id,feasible=q.id==c.id,reasons=[] if q.id==c.id else ['mock_rejection']) for q in self.candidates])
                score=dict(candidate_id=c.id,candidate_hash=candidate_sha256([c]),status='scored',family='body',
                    worst_utilization=1.2,score=1-1.2)
                m0=dict(random_ranking([c.id],proto.selector_seed),input_feasible_ids=[c.id],selected_family='body')
                ranked=dict(method_id='m1_gws_v2',ranking=[c.id],selected_id=c.id,selected_family='body',
                    input_feasible_ids=[c.id],scores=[score],status_counts={'scored':1})
                trial=dict(task=task,scene_seed=seed,candidate_sha256=proto.candidate_pool_hash,selector_seed=proto.selector_seed,
                    feasible_ids=[c.id],feasible_count=1,filter_record=record,physics=_plain(proto.scoring_config),
                    motion_models=[asdict(MotionModel(samples_per_stage=64))],
                    payload_mode=proto.prediction_models[task]['model_label'],body=_plain(proto.prediction_models[task]['object_parameters']),
                    methods={'M0':m0,'M1':deepcopy(ranked),'M2/64':dict(deepcopy(ranked),method_id='m2_gws_trajectory_v2')})
                if task=='pour_water':trial['load_model_identity']={k:_plain(v) for k,v in proto.prediction_models[task].items() if k!='object_parameters'}
                result.append(trial)
        return result

    def bundle(self,root):
        store=EpisodeStore(root);plan=freeze_bundle(store,self.protocol,self.candidates,self.trials())
        auth=ExecutionAuthorization(self.protocol.protocol_hash,digest(plan),'unit-test-MOCK-only','AUTHORIZE_PHYSICAL_EXECUTION')
        return store,plan,auth

    def runner(self,**changes):
        def run(task,seed,cid,candidates,**kwargs):
            self.assertEqual(kwargs['liquid'],'pbf');self.assertIsNone(kwargs['observation_config'])
            self.assertEqual(asdict(kwargs['scene_config']),asdict(scene_randomization('wide_strict_v2')))
            return dict(task_id=task,seed=seed,selected_id=cid,candidate_sha256=candidate_sha256(candidates),
                scene_randomization=_plain(self.protocol.scene_config),observation_config={'source':'MOCK external estimate'},
                initial_object_pose_estimate={'position':[.55,0,.645],'quaternion':[0,0,0,1]},
                status='completed',success=True,success_checks={'mock':True},
                controller_version='staged_sequences_v1',evaluation_version='physical_outcomes_v1',**changes)
        return Mock(side_effect=run)

    def test_protocol_immutable_roundtrip_formal_resolution(self):
        p=self.protocol
        self.assertEqual(PilotProtocol.from_dict(json.loads(json.dumps(p.to_dict()))),p)
        with self.assertRaises(FrozenInstanceError):p.samples_per_stage=16
        with self.assertRaises(ValueError):replace(p,samples_per_stage=16)
        self.assertNotEqual(replace(p,samples_per_stage=16,purpose='development_dry_run').protocol_hash,p.protocol_hash)
        with self.assertRaises(TypeError):p.scene_config['name']='other'

    def test_all_cells_and_common_feasible_pool_required(self):
        rows=self.trials()
        with self.assertRaises(ValueError):build_plan(self.protocol,self.candidates,rows[:-1])
        rows[0]['methods']['M1']['input_feasible_ids']=[]
        with self.assertRaises(ValueError):build_plan(self.protocol,self.candidates,rows)

    def test_selector_seed_is_separate(self):
        rows=self.trials();rows[0]['selector_seed']=rows[0]['scene_seed']
        with self.assertRaises(ValueError):build_plan(self.protocol,self.candidates,rows)

    def test_negative_margin_selected_and_deduplicated(self):
        plan=build_plan(self.protocol,self.candidates,self.trials())
        self.assertEqual(plan['decision_count'],27);self.assertEqual(plan['unique_episode_count'],9)
        self.assertEqual(plan['predicted_negative_margin_selections'],18)
        self.assertTrue(all(link['episode_key'] for link in plan['method_links']))
        self.assertNotIn('wet_payload_hash',next(iter(plan['episodes'].values())))

    def test_no_authorization_no_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);runner=self.runner()
            self.assertEqual(execute_frozen_plan(s,runner=runner)['status'],'authorization_required')
            runner.assert_not_called()
            with self.assertRaises(ValueError):execute_frozen_plan(s,authorization=replace(a,plan_hash='bad'),runner=runner)
            runner.assert_not_called()

    def test_mock_execution_reuse_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);runner=self.runner()
            first=execute_frozen_plan(s,authorization=a,runner=runner)
            self.assertEqual(runner.call_count,9);self.assertEqual(first['summary']['method_outcome_counts'],{'success':27})
            second=execute_frozen_plan(s,authorization=a,runner=runner)
            self.assertEqual(runner.call_count,9);self.assertEqual(second['new_episode_calls'],0)
            self.assertEqual(second['reused_episode_records'],9)

    def test_default_A_entrypoint_is_mocked_and_receives_exact_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp)
            with patch('grasp_a.sequence.run_sequence',self.runner()) as mocked:
                execute_frozen_plan(s,authorization=a)
                self.assertEqual(mocked.call_count,9)
                self.assertEqual({call.args[1] for call in mocked.call_args_list},{2,3,4})
                for call in mocked.call_args_list:
                    self.assertEqual(candidate_sha256(call.args[3]),self.protocol.candidate_pool_hash)
                    self.assertEqual(digest(asdict(call.kwargs['config'])),digest(asdict(SequenceConfig())))

    def test_malformed_outcome_is_infrastructure_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp)
            result=execute_frozen_plan(s,authorization=a,runner=Mock(return_value={'success':True}))
            self.assertEqual(result['summary']['method_outcome_counts'],{'infrastructure_error':27})

    def test_no_selection_never_dispatched(self):
        rows=self.trials()
        for row in rows:
            for method in ('M1','M2/64'):
                m=row['methods'][method];m['selected_id']=None;m['scores'][0].update(status='model_infeasible',worst_utilization=None,score=None);m['status_counts']={'model_infeasible':1}
        p=build_plan(self.protocol,self.candidates,rows)
        self.assertEqual(p['abstentions'],18);self.assertEqual(p['unique_episode_count'],9)
        # Entirely empty feasible pools -> no runner calls, including M0.
        for row in rows:
            row['feasible_ids']=[];row['feasible_count']=0;record=row['filter_record'];record['feasible_candidates']=[]
            for check in record['checks']:check['feasible']=False
            for m in row['methods'].values():m.update(ranking=[],selected_id=None,scores=[],input_feasible_ids=[],status_counts={})
        with tempfile.TemporaryDirectory() as tmp:
            s=EpisodeStore(tmp);p=freeze_bundle(s,self.protocol,self.candidates,rows)
            a=ExecutionAuthorization(self.protocol.protocol_hash,digest(p),'MOCK','AUTHORIZE_PHYSICAL_EXECUTION');runner=self.runner()
            execute_frozen_plan(s,authorization=a,runner=runner);runner.assert_not_called()

    def test_identity_different_seed_task_liquid_replica(self):
        p=build_plan(self.protocol,self.candidates,self.trials());job=next(iter(p['episodes'].values()));key=digest(job)
        for field,value in (('scene_seed',99),('task','other'),('liquid_mode','proxy'),('replicate_index',1),('candidate_hash','bad')):
            self.assertNotEqual(digest(dict(job,**{field:value})),key)
        self.assertNotIn('method',job);self.assertNotIn('selector_seed',job)

    def test_decision_mutation_after_freeze_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);name=f'decisions/{p["decisions"][0]["decision_hash"]}.json'
            payload=s.read(name);payload['selector_seed']=7
            s.path(name).write_text(json.dumps(dict(schema='immutable_pilot_record_v1',payload=payload,sha256=digest(payload))))
            runner=self.runner()
            with self.assertRaises((ValueError,StoreError)):execute_frozen_plan(s,authorization=a,runner=runner)
            runner.assert_not_called()

    def test_corrupt_cache_cannot_be_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);key=next(iter(p['episodes']))
            s.path(f'episodes/{key}.json').parent.mkdir(parents=True)
            s.path(f'episodes/{key}.json').write_text('{broken')
            runner=self.runner()
            with self.assertRaises(StoreError):execute_frozen_plan(s,authorization=a,runner=runner)
            runner.assert_not_called()

    def test_interrupted_start_no_blind_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);key=next(iter(p['episodes']))
            s.write_once(f'attempts/{key}.json',dict(episode_key=key,physical_identity=p['episodes'][key]))
            runner=self.runner();result=execute_frozen_plan(s,authorization=a,runner=runner)
            self.assertEqual(runner.call_count,8);self.assertEqual(result['status'],'pending_reconciliation')
            self.assertEqual(result['summary']['method_outcome_counts']['missing_outcome'],3)

    def test_exception_is_infrastructure_not_task_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);runner=Mock(side_effect=RuntimeError('MOCK infrastructure'))
            result=execute_frozen_plan(s,authorization=a,runner=runner)
            self.assertEqual(result['summary']['method_outcome_counts'],{'infrastructure_error':27})
            execute_frozen_plan(s,authorization=a,runner=runner);self.assertEqual(runner.call_count,9)

    def test_outcome_does_not_enter_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp)
            with patch('grasp_b.wrench_v2.evaluation.evaluate_selection',side_effect=AssertionError('no feedback')):
                execute_frozen_plan(s,authorization=a,runner=self.runner())
            self.assertEqual(load_bundle(s)[2],p)

    def test_atomic_write_existing_validation_and_partial_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=EpisodeStore(tmp);s.write_once('x.json',{'a':1});s.write_once('x.json',{'a':1})
            s.write_once('nested.json',[{'pose':(1,2,3)}])
            s.write_once('nested.json',[{'pose':[1,2,3]}])
            with self.assertRaises(StoreError):s.write_once('x.json',{'a':2})
            Path(tmp,'.pending-orphan').write_text('incomplete')
            self.assertEqual(s.read('x.json'),{'a':1})
            with self.assertRaises(StoreError):s.write_once('../escape.json',{})

    def test_raw_missing_cannot_masquerade_as_cached_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,p,a=self.bundle(tmp);key=next(iter(p['episodes']))
            s.write_once(f'episodes/{key}.json',dict(episode_key=key,physical_identity=p['episodes'][key],category='success',raw_outcome=None,error=None))
            runner=self.runner()
            with self.assertRaises(StoreError):execute_frozen_plan(s,authorization=a,runner=runner)
            runner.assert_not_called()

    def test_prediction_identity_does_not_duplicate_physical_job(self):
        from grasp_b.wrench_v2.tasks import ObjectParameters
        from grasp_b.wrench_v2.wet_payload import combine_wet_object
        prediction=_plain(self.protocol.prediction_models)
        dry=ObjectParameters(**prediction[SEQUENCE_TASKS[0]]['object_parameters'])
        wet,binding=combine_wet_object(dry,build_wet_payload(radius_m=.03175))
        prediction['pour_water']=dict(binding,object_parameters=_plain(wet))
        other=replace(self.protocol,prediction_models=prediction)
        trials=self.trials()
        for t in trials:
            if t['task']=='pour_water':t.update(body=_plain(wet),load_model_identity=binding)
        old=build_plan(self.protocol,self.candidates,self.trials());new=build_plan(other,self.candidates,trials)
        self.assertNotEqual(other.protocol_hash,self.protocol.protocol_hash)
        self.assertEqual(old['episodes'],new['episodes'])


if __name__=='__main__':unittest.main()
