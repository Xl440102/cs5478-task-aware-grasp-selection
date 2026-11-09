"""Selection-only integration; real numerical LP, no selected execution."""
import ast
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.wrench_v2.m1 import prepare_contact_spec
from grasp_b.wrench_v2.evaluation import evaluate_selection, validate_filter, random_ranking, dry_task_sets
from grasp_b.wrench_v2.evaluation_cli import filter_without_rollout, run_selection
from tests.test_b_wrench_m2 import fixture


def inputs(task='transport_place', n_candidates=2):
    p,m,d=fixture()
    c=Candidate(p.spec.candidate_id,'body','numerical fixture only',.077,0.,
        Pose(m.grasp_pose.position_m,m.grasp_pose.quaternion_xyzw),
        Pose((0,0,-.08),(0,0,0,1)))
    # Match the source fixture exactly; identities are full Candidate hashes.
    cs=[replace(c,id=f'fixture_{i}') for i in range(n_candidates)]
    prepared={c.id:prepare_contact_spec(replace(p.spec,candidate_id=c.id,candidate_hash=candidate_sha256([c])),p.config) for c in cs}
    record=dict(candidate_sha256=candidate_sha256(cs),task_id=task,seed=0,
        scene_randomization={'filter_mode':'execution'},task_spec={'config':asdict(SequenceConfig())},
        initial_object_pose_estimate={'position':(.55,0,.8),'quaternion':(0,0,0,1)},
        feasible_candidates=[c.public_metadata() for c in cs],
        checks=[dict(candidate_id=c.id,feasible=True,reasons=[]) for c in cs])
    return cs,record,prepared,d.body,p.config


def evaluate(data,**options):
    cs,record,prepared,body,config=data
    return evaluate_selection(cs,record,prepared,body=body,config=config,
        task_sets=dry_task_sets(body,config),selector_seed=options.pop('selector_seed',12345),
        resolutions=options.pop('resolutions',(4,8)),**options)


class SelectionTests(unittest.TestCase):
    def test_same_pool_complete_rankings_and_unmodified_candidates(self):
        data=inputs(); before=candidate_sha256(data[0]); r=evaluate(data)
        for value in r['methods'].values():
            self.assertEqual(set(value['ranking']),set(r['feasible_ids']))
            self.assertEqual(len(value['ranking']),r['feasible_count'])
            self.assertEqual(value['input_feasible_ids_sha256'],r['feasible_ids_sha256'])
            if value['selected_id'] is not None: self.assertIn(value['selected_id'],r['feasible_ids'])
        self.assertEqual(before,candidate_sha256(data[0]))
        self.assertFalse(r['execution_performed'])

    def test_random_seed_is_local_reproducible_and_score_independent(self):
        state=random.getstate()
        ids=[str(i) for i in range(20)]
        a=random_ranking(ids,9)
        self.assertEqual(a,random_ranking(list(reversed(ids)),9))
        self.assertNotEqual(a['ranking'],random_ranking(ids,10)['ranking'])
        self.assertEqual(state,random.getstate())
        with self.assertRaises(ValueError): random_ranking(ids,True)

    def test_selector_seed_does_not_change_m1_m2(self):
        data=inputs(); a=evaluate(data,selector_seed=1); b=evaluate(data,selector_seed=999)
        for method in ('M1','M2/4','M2/8'):
            self.assertEqual(a['methods'][method]['ranking'],b['methods'][method]['ranking'])
            self.assertEqual([r.get('score') for r in a['methods'][method]['scores']],
                             [r.get('score') for r in b['methods'][method]['scores']])

    def test_identity_and_public_schema_rejections(self):
        data=inputs()
        changed=deepcopy(data[1]);changed['candidate_sha256']='a'*64
        with self.assertRaises(ValueError):validate_filter(data[0],changed)
        changed=deepcopy(data[1]);changed['feasible_candidates'][0]['geometry_score']=999
        with self.assertRaises(ValueError):validate_filter(data[0],changed)
        changed=deepcopy(data[1]);changed['checks'][0]['feasible']=False
        with self.assertRaises(ValueError):validate_filter(data[0],changed)
        data[2][data[0][0].id]=replace(data[2][data[0][0].id],spec=replace(data[2][data[0][0].id].spec,candidate_hash='f'*64))
        with self.assertRaises(ValueError):evaluate(data)

    def test_empty_feasible_pool(self):
        data=inputs();data[1]['feasible_candidates']=[]
        for check in data[1]['checks']:check.update(feasible=False,reasons=['fixture_rejection'])
        r=evaluate(data)
        for value in r['methods'].values():
            self.assertEqual(value['ranking'],[])
            self.assertIsNone(value['selected_id'])

    def test_all_unscored_preserves_ids_status_and_none_choice(self):
        data=inputs()
        statuses=('missing_parameters','geometry_invalid')
        for cid,status in zip(data[2],statuses):
            data[2][cid]=replace(data[2][cid],status=status,reason='explicit unscored interface fixture')
        r=evaluate(data)
        self.assertIn(r['methods']['M0']['selected_id'],r['feasible_ids'])
        for method in ('M1','M2/4','M2/8'):
            value=r['methods'][method]
            self.assertIsNone(value['selected_id'])
            self.assertEqual(set(value['status_counts']),set(statuses))
            self.assertEqual(value['ranking'],r['feasible_ids'])

    def test_real_model_infeasible_is_not_numerical_error(self):
        data=list(inputs(n_candidates=1));data[3]=replace(data[3],com_m=(0,.01,0))
        r=evaluate(data)
        for method in ('M1','M2/4','M2/8'):
            self.assertEqual(r['methods'][method]['status_counts'],{'model_infeasible':1})
            self.assertIsNone(r['methods'][method]['selected_id'])

    def test_missing_inertia_prevents_complete_pour_score(self):
        data=list(inputs(task='pour_water',n_candidates=1));data[3]=replace(data[3],inertia_com_kg_m2=None)
        r=evaluate(data)
        self.assertEqual(r['methods']['M2/4']['status_counts'],{'missing_inertia':1})
        self.assertIsNone(r['methods']['M2/4']['selected_id'])

    def test_m1_m2_cannot_silently_use_different_object_physics(self):
        cs,record,prepared,body,config=inputs(n_candidates=1)
        with self.assertRaises(ValueError):
            evaluate_selection(cs,record,prepared,body=replace(body,mass_kg=2*body.mass_kg),config=config,
                task_sets=dry_task_sets(body,config),selector_seed=2,resolutions=(4,))

    def test_trial_cache_isolation_and_resolution_reproducibility(self):
        data=inputs(n_candidates=1)
        a=evaluate(data,resolutions=(4,8,16)); b=evaluate(data,resolutions=(4,8,16))
        for n in (4,8,16):
            x,y=a['methods'][f'M2/{n}'],b['methods'][f'M2/{n}']
            self.assertEqual(x['ranking'],y['ranking'])
            self.assertEqual(x['scores'][0]['score'],y['scores'][0]['score'])
            self.assertEqual(x['timings']['lp_calls'],y['timings']['lp_calls'])
        self.assertGreater(a['methods']['M2/4']['timings']['lp_calls'],0)

    def test_real_numerical_error_is_preserved_without_fallback(self):
        from grasp_b.wrench_v2.solver import NumericalTolerances
        data=list(inputs(n_candidates=1))
        data[4]=replace(data[4],tolerances=NumericalTolerances(relative=1e-30))
        data[2]={cid:prepare_contact_spec(p.spec,data[4]) for cid,p in data[2].items()}
        r=evaluate(data)
        self.assertEqual(r['methods']['M2/4']['status_counts'],{'numerical_error':1})
        self.assertIsNone(r['methods']['M2/4']['selected_id'])

    def test_filter_guard_calls_public_api_but_never_steps(self):
        data=inputs()
        with patch('grasp_a.sequence.get_sequence_candidates',return_value=data[1]) as api:
            record,metrics=filter_without_rollout('transport_place',0,data[0],sequence_config=SequenceConfig(),scene_config='wide_strict_v2')
            self.assertEqual(api.call_count,1)
            self.assertEqual(metrics['physical_step_calls'],0)
        import pybullet as p
        with patch('grasp_a.sequence.get_sequence_candidates',side_effect=lambda *a,**kw:p.stepSimulation()):
            with self.assertRaises(RuntimeError):
                filter_without_rollout('transport_place',0,data[0],sequence_config=SequenceConfig(),scene_config='wide_strict_v2')

    def test_existing_output_and_non_execution_mode_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):run_selection(Path(directory))
            with self.assertRaises(ValueError):run_selection(Path(directory)/'unused',scene='legacy')

    def test_no_execution_or_oracle_dependencies(self):
        root=Path(__file__).resolve().parents[1]/'grasp_b/wrench_v2'
        for name in ('evaluation.py','evaluation_cli.py'):
            tree=ast.parse((root/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse((node.module or '').startswith('grasp_c'))
                if isinstance(node,ast.Call):
                    called=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''
                    self.assertNotIn(called,('run_sequence','run_episode','getContactPoints'))


if __name__=='__main__':unittest.main()
