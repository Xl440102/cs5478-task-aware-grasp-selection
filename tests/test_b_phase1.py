"""Phase1 contract/identity tests, true small HiGHS integration, zero physics."""
from dataclasses import replace
from pathlib import Path
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_b.contact_refinement import phase1 as p
from grasp_b.contact_refinement.relaxed_solver import TaskLexConfig, ExactTaskCache
from grasp_b.contact_refinement.relaxed_ranking import rank_task_results
from grasp_b.wrench_v2.episode_store import EpisodeStore, StoreError, digest
from grasp_b.wrench_v2.evaluation import random_ranking, dry_task_sets
from grasp_b.wrench_v2.m2 import score_trajectory
from tests.test_b_wrench_m2 import fixture


def example(empty=False):
    cs = [Candidate(x, 'body', 'fixture', .07, 0., Pose((0.,0.,0.), (0.,0.,0.,1.)),
          Pose((0.,0.,-.08), (0.,0.,0.,1.))) for x in ('a', 'b')]
    by = {c.id: c for c in cs}; ids = [] if empty else list(by)
    pool = dict(feasible_ids=ids, filter_hash='f'*64, filter_record={'initial_object_pose_estimate':{'test':'pose'}})
    catalog = dict(pool_hash=candidate_sha256(cs), candidates=[p.asdict(c) for c in cs],
                   candidate_hashes={c.id:candidate_sha256([c]) for c in cs})
    registry = p.seal({'fixture':True}, 'registry_hash')
    protocol = p.seal(dict(tasks=['transport_place'], scene_seeds=[5], registry_hash=registry['registry_hash'],
                          physical_config={'liquid_mode':'pbf'}), 'protocol_hash')
    ds = []
    for m in p.METHODS:
        state = 'random_ranked' if m == p.METHODS[0] else 'scored_conditional' if m in p.METHODS[3:] else 'scored'
        ds.append(p.make_decision(method=m, task='transport_place', seed=5, ids=ids, candidates=by,
            ranking=ids, selected=ids[0] if ids else None, statuses={c:state for c in ids},
            scores={c:{'status':state, 'score':-1.} for c in ids}, family_by_id={c:'side' for c in ids},
            provenance={'filter_hash':pool['filter_hash'], 'protocol_hash':protocol['protocol_hash'], 'candidate_pool_hash':catalog['pool_hash']}, near_tie={'ids':ids, 'status':'near_tie'}))
    return ds, protocol, catalog, {'transport_place/5':pool}


class Phase1Tests(unittest.TestCase):
    @unittest.skipUnless((p.F2/'checksums.json').exists(), 'optional historical Task09 registry is not included in the source release')
    def test_frozen_registry_sources_resources_and_methods_verified(self):
        r,d = p.load_registry()
        self.assertEqual(r['registry_hash'], p.REGISTRY_HASH)
        self.assertEqual(set(m['method_id'] for m in r['methods'].values()), set(p.METHODS))

    def test_only_registered_development_scope(self):
        p.validate_scope(p.TASKS,p.SEEDS,2305478)
        for seeds in ([0], [8], [True], [5,5]):
            with self.assertRaises(ValueError):p.validate_scope(p.TASKS,seeds,2305478)
        with self.assertRaises(ValueError):p.validate_scope(['unknown'],[5],2305478)
        with self.assertRaises(ValueError):p.validate_scope(p.TASKS,[5],5)

    def test_observational_timings_do_not_remove_model_time(self):
        a={'runtime_s':3., 'samples':[{'time_s':1.,'duration_s':2.,'wall_s':5.}], 'stage1':{'runtime_s':2.,'value':.3}}
        b=copy.deepcopy(a);b['runtime_s']=7.;b['samples'][0]['wall_s']=8.
        self.assertEqual(digest(p.stable_payload(a)),digest(p.stable_payload(b)))
        b['samples'][0]['time_s']=3.
        self.assertNotEqual(digest(p.stable_payload(a)),digest(p.stable_payload(b)))

    def test_five_methods_same_pool_and_no_loss_of_invalid_tail(self):
        ds,pr,ca,po=example()
        for d in ds:
            self.assertTrue(p.validate_decision(d));self.assertEqual(set(d['ranking']),{'a','b'})
        bad=copy.deepcopy(ds[1]);bad.pop('decision_hash');bad['ranking']=['a','a']
        with self.assertRaises(StoreError):p.validate_decision(p.seal(bad,'decision_hash'))
        plan=p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)
        self.assertEqual(len(plan['episodes']),1);self.assertEqual(len(plan['method_links']),5)

    def test_negative_margin_stays_eligible(self):
        ds,_,_,_=example();self.assertEqual(ds[1]['selected_id'],'a');self.assertLess(ds[1]['scores']['a']['score'],0)

    def test_empty_pool_no_selection_and_no_episode(self):
        ds,pr,ca,po=example(empty=True)
        plan=p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)
        self.assertEqual(plan['episodes'],{});self.assertTrue(all(d['selected_id'] is None for d in ds))

    def test_invalid_geometry_never_selected_or_dropped(self):
        ds,pr,ca,po=example();d=ds[1];d.pop('decision_hash')
        for c in d['status_by_id']:d['status_by_id'][c]='geometry_invalid'
        d.update(selected_id=None,selected_family=None)
        ds[1]=p.seal(d,'decision_hash')
        plan=p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)
        self.assertIsNone(plan['method_links'][1]['episode_key'])
        self.assertEqual(ds[1]['ranking'],['a','b'])

    def test_plan_commit_refuses_missing_decisions_and_different_feasible_pool(self):
        ds,pr,ca,po=example()
        with self.assertRaises(StoreError):p.build_plan(ds[:-1],protocol=pr,catalog=ca,feasible_pools=po)
        po['transport_place/5']['feasible_ids']=['b']
        with self.assertRaises(StoreError):p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)

    def test_m0_seed_independent_of_scores_and_scene(self):
        a=random_ranking(['b','a'],2305478)
        self.assertEqual(a,random_ranking(['a','b'],2305478))
        self.assertEqual(a['scores'],[])

    def test_prediction_identity_excluded_from_physical_dedup(self):
        ds,pr,ca,po=example();a=p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)
        ds2=[]
        for d in ds:
            d=copy.deepcopy(d);d.pop('decision_hash');d['provenance']['wet_prediction_hash']='1'*64
            ds2.append(p.seal(d,'decision_hash'))
        b=p.build_plan(ds2,protocol=pr,catalog=ca,feasible_pools=po)
        self.assertEqual(a['episodes'],b['episodes']);self.assertNotEqual(a['plan_hash'],b['plan_hash'])
        pr2=copy.deepcopy(pr);pr2.pop('protocol_hash');pr2['physical_config']['liquid_mode']='proxy';pr2=p.seal(pr2,'protocol_hash')
        for d in ds2:
            d.pop('decision_hash');d['provenance']['protocol_hash']=pr2['protocol_hash'];d.update(decision_hash=digest(d))
        c=p.build_plan(ds2,protocol=pr2,catalog=ca,feasible_pools=po)
        self.assertTrue(set(a['episodes']).isdisjoint(c['episodes']))

    def test_store_identity_corruption_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=EpisodeStore(tmp);s.write_once('decision.json',{'id':'original'});s.write_once('decision.json',{'id':'original'})
            with self.assertRaises(StoreError):s.write_once('decision.json',{'id':'changed'})
            s.path('decision.json').write_text('{}')
            with self.assertRaises(StoreError):s.read('decision.json')

    def test_verify_frozen_round_trip_no_selectors(self):
        ds,pr,ca,po=example();plan=p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)
        with tempfile.TemporaryDirectory() as tmp:
            s=EpisodeStore(tmp)
            s.write_once('method_registry.json',p.seal({'fixture':True},'registry_hash'))
            for n,v in [('protocol.json',pr),('candidate_pool.json',ca),('feasible_pools.json',po),
                        ('method_links.json',plan['method_links']),('episode_plan.json',plan)]:s.write_once(n,v)
            for d in ds:s.write_once('decisions/'+d['decision_hash']+'.json',d)
            with patch.object(p,'random_ranking',side_effect=AssertionError('reselect')):
                self.assertEqual(p.verify_store(s)['decisions'],5)
            self.assertFalse(plan['execution_authorized'])

    def test_no_execution_guard_actual_entrypoints(self):
        import pybullet
        with self.assertRaises(RuntimeError):
            with p.no_execution():pybullet.stepSimulation()
        import grasp_a.sequence as a
        with self.assertRaises(RuntimeError):
            with p.no_execution():a.run_sequence()

    def test_near_tie_preserved_after_serialization(self):
        ds,_,_,_=example();d=json.loads(json.dumps(ds[-1]))
        self.assertEqual(d['near_tie']['ids'],['a','b']);self.assertTrue(p.validate_decision(d))

    def test_real_highs_shared_original_demands_and_two_stages(self):
        prepared,motion,dynamics=fixture(n=5)
        strict=score_trajectory(prepared,motion,dynamics)
        tws=dry_task_sets(dynamics.body,prepared.config)['transport_place']
        demands=p.demands_for(prepared,tws,motion,dynamics,strict,context_hash='a'*64,object_hash='b'*64,source_hash='c'*64)
        cache=ExactTaskCache()
        for d in demands:
            result,certificate=p.score_relaxed(prepared,d,TaskLexConfig(reference_force_n=1., characteristic_length_m=.1),cache)
            self.assertEqual(result.payload['status'],'scored_conditional')
            self.assertTrue(certificate['original']['exact_mapping'])
            again,_=p.score_relaxed(prepared,d,TaskLexConfig(reference_force_n=1., characteristic_length_m=.1),cache)
            self.assertEqual(result.to_dict(),again.to_dict())
        self.assertEqual(cache.hits,2);self.assertEqual(cache.misses,2)
        self.assertNotEqual(demands[0].method,demands[1].method)

    def test_infeasible_strict_does_not_discard_required_dynamic_wrench(self):
        prepared,motion,dynamics=fixture(n=5)
        strict=score_trajectory(prepared,motion,dynamics)
        tws=dry_task_sets(dynamics.body,prepared.config)['transport_place']
        ds=p.demands_for(prepared,tws,motion,dynamics,strict,context_hash='a'*64,object_hash='b'*64,source_hash='c'*64)
        self.assertEqual(len(ds[1].scenarios),sum(s.required for s in strict.samples))
        self.assertTrue(all(s.status=='complete' for s in ds[1].scenarios))
        self.assertEqual([s.wrench for s in ds[1].scenarios], [s.required_contact_wrench_mug for m,s in zip(motion.samples,dynamics.samples)
            if f'{m.stage}:{list(motion.samples).index(m):05d}:{m.side}' in {r.scenario_id for r in ds[1].scenarios}])

    def test_decision_hash_repeat_and_identity_mismatch_rejected(self):
        ds,pr,ca,po=example();repeat=example()[0];self.assertEqual(ds,repeat)
        ds[0]['candidate_hashes']['a']='0'*64;ds[0].pop('decision_hash');ds[0]=p.seal(ds[0],'decision_hash')
        with self.assertRaises(StoreError):p.build_plan(ds,protocol=pr,catalog=ca,feasible_pools=po)


class Phase1IntegrationTests(unittest.TestCase):
    @unittest.skipUnless((p.F2/'checksums.json').exists(), 'optional historical Task09 registry is not included in the source release')
    def test_complete_live_scorer_mock_filter_freeze_repeat_and_raw_checksums(self):
        from grasp_b.contact_refinement.relaxed_solver import TaskLexResult
        def static_filter(task, seed, candidates, *, sequence_config, scene_config):
            ids={c.id for c in candidates[:2]}
            record=dict(task_id=task, seed=seed, candidate_sha256=candidate_sha256(candidates),
                scene_randomization=p.asdict(scene_config), task_spec={'config':p.asdict(sequence_config)},
                initial_object_pose_estimate={'position':[.55,0.,.8], 'quaternion':[0.,0.,0.,1.]},
                checks=[{'candidate_id':c.id,'feasible':c.id in ids,'reason':'explicit MOCK filter only'} for c in candidates],
                feasible_candidates=[c.public_metadata() for c in candidates if c.id in ids])
            return record, dict(runtime_s=0.,physical_step_calls=0,mock_only=True)
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'filter_without_rollout',side_effect=static_filter) as filtering:
            a=p.run_phase1(Path(tmp)/'first',tasks=('transport_place',),seeds=(5,))
            b=p.run_phase1(Path(tmp)/'second',tasks=('transport_place',),seeds=(5,))
            self.assertEqual(filtering.call_count,2)
            self.assertEqual(a['decision_hashes'],b['decision_hashes']);self.assertEqual(a['plan_hash'],b['plan_hash'])
            store=EpisodeStore(Path(tmp)/'first');plan=store.read('episode_plan.json')
            self.assertEqual(len(plan['method_links']),5);self.assertFalse(plan['execution_authorized'])
            self.assertTrue(all(v==0 for v in store.read('verification.json')['guards'].values()))
            for values in store.read('diagnostics/transport_place_5.json')['candidates'].values():
                for method in ('relaxed_m1','relaxed_m2'):
                    self.assertEqual(TaskLexResult(values[method]).to_dict(),values[method])
            with self.assertRaises(FileExistsError):p.run_phase1(Path(tmp)/'first',tasks=('transport_place',),seeds=(5,))
            self.assertEqual(filtering.call_count,2)

    def test_changed_protocol_and_missing_score_cannot_commit(self):
        ds,protocol,catalog,pools=example()
        changed=copy.deepcopy(protocol);changed['physical_config']['liquid_mode']='proxy'
        with self.assertRaises(StoreError):p.build_plan(ds,protocol=changed,catalog=catalog,feasible_pools=pools)
        ds[1].pop('decision_hash');ds[1]['status_by_id']['a']='geometry_invalid';ds[1]=p.seal(ds[1],'decision_hash')
        with self.assertRaises(StoreError):p.build_plan(ds,protocol=protocol,catalog=catalog,feasible_pools=pools)
