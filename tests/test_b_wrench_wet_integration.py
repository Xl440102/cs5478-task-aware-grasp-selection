"""Real cached filter/static selection regression; no new A filtering/rollout."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pybullet_data

from grasp_a.control import scene_randomization
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.models import read_model
from grasp_b.multifamily import generate_multifamily,pruned_config,VERSION
from grasp_b.wrench_v2.evaluation_cli import verified_cached_filter
from grasp_b.wrench_v2.evaluation import evaluate_selection,dry_task_sets,wet_task_sets
from grasp_b.wrench_v2.geometry import ObjectGeometry,read_gripper_geometry
from grasp_b.wrench_v2.adapter import GeneratorInfo
from grasp_b.wrench_v2.m1 import development_config,prepare_candidate,fingerprint
from grasp_b.wrench_v2.dynamics import object_parameters_from_snapshot
from grasp_b.wrench_v2.wet_payload import ROOT,LABEL,build_wet_payload,combine_wet_object

CACHE=ROOT/'results/local/task07_shared_feasible_20261106_final'


@unittest.skipUnless((CACHE/'summary.json').exists(),'optional frozen Task07 local fixture unavailable')
class WetIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads((CACHE/'summary.json').read_text())
        panda=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
        model=read_model('hollow',panda)
        cls.candidates,records=generate_multifamily(model,pruned_config())
        cls.record,_=verified_cached_filter(CACHE,'pour_water',0,cls.candidates,sequence_config=SequenceConfig(),scene_config=scene_randomization('wide_strict_v2'),root=ROOT)
        infos={r['id']:GeneratorInfo('grasp_b.multifamily',VERSION,r['family'],r['id']) for r in records}
        allowed={r['id'] for r in cls.record['feasible_candidates']}
        cls.config=development_config()
        obj,gripper=ObjectGeometry.from_model(model),read_gripper_geometry(panda)
        cls.prepared={c.id:prepare_candidate(c,obj,gripper,generator_info=infos[c.id],config=cls.config) for c in cls.candidates if c.id in allowed}
        cls.body=object_parameters_from_snapshot(cls.manifest['static_snapshot'],asset_path=ROOT/'assets/mug_hollow.urdf')

    def test_cached_filter_validates_and_never_calls_A(self):
        with patch('grasp_a.sequence.get_sequence_candidates',side_effect=AssertionError('cached filter only')):
            r,metrics=verified_cached_filter(CACHE,'pour_water',0,self.candidates,sequence_config=SequenceConfig(),scene_config=scene_randomization('wide_strict_v2'),root=ROOT)
        self.assertEqual(len(r['feasible_candidates']),16)
        self.assertTrue(metrics['cache_hit'])
        with self.assertRaises(ValueError):
            verified_cached_filter(CACHE,'pour_water',100,self.candidates,sequence_config=SequenceConfig(),scene_config=scene_randomization('wide_strict_v2'),root=ROOT)

    def test_stale_filter_code_and_config_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad=json.loads(json.dumps(self.manifest));bad['source_sha256']['grasp_a/sequence.py']='a'*64
            (Path(tmp)/'summary.json').write_text(json.dumps(bad))
            with self.assertRaises(ValueError):verified_cached_filter(tmp,'pour_water',0,self.candidates,sequence_config=SequenceConfig(),scene_config=scene_randomization('wide_strict_v2'),root=ROOT)
        with self.assertRaises(ValueError):
            verified_cached_filter(CACHE,'pour_water',0,self.candidates,sequence_config=SequenceConfig(),scene_config=scene_randomization('wide_strict'),root=ROOT)

    def test_real_dry_regression_against_frozen_trial(self):
        r=evaluate_selection(self.candidates,self.record,self.prepared,body=self.body,config=self.config,
            task_sets=dry_task_sets(self.body,self.config),selector_seed=2305478,resolutions=(16,))
        old=json.loads((CACHE/'pour_water_scene0_selector2305478.json').read_text())
        for method in ('M0','M1','M2/16'):
            self.assertEqual(r['methods'][method]['ranking'],old['methods'][method]['ranking'])
            self.assertEqual(r['methods'][method]['status_counts'],old['methods'][method]['status_counts'])
            self.assertEqual(r['methods'][method]['selected_id'],old['methods'][method]['selected_id'])
            if method!='M0':
                self.assertEqual([x['worst_utilization'] for x in r['methods'][method]['scores']],
                                 [x['worst_utilization'] for x in old['methods'][method]['scores']])

    def test_wet_shared_model_complete_rankings_and_hashes(self):
        p=build_wet_payload();body,binding=combine_wet_object(self.body,p)
        r=evaluate_selection(self.candidates,self.record,self.prepared,body=body,config=self.config,
            task_sets=wet_task_sets(body,self.config),selector_seed=2305478,resolutions=(4,),load_model=LABEL,wet_payload=p)
        for name,m in r['methods'].items():
            self.assertEqual(m['input_feasible_ids'],r['feasible_ids'])
            self.assertEqual(set(m['ranking']),set(self.prepared))
            self.assertEqual(m['load_model_identity'],binding)
        for row in r['methods']['M1']['scores']:
            for scenario in row['provenance']['task_set']['scenarios']:
                self.assertEqual(fingerprint(scenario['object_parameters']),binding['combined_object_hash'])
        for row in r['methods']['M2/4']['scores']:
            self.assertEqual(fingerprint(row['provenance']['evaluation_context']['body']),binding['combined_object_hash'])

    def test_load_cases_have_separate_score_cache_and_output_identity(self):
        dry=evaluate_selection(self.candidates,self.record,self.prepared,body=self.body,config=self.config,
            task_sets=dry_task_sets(self.body,self.config),selector_seed=2305478,resolutions=(4,))
        seen=[]
        for radius in (.032,.03175):
            p=build_wet_payload(radius_m=radius);body,binding=combine_wet_object(self.body,p)
            r=evaluate_selection(self.candidates,self.record,self.prepared,body=body,config=self.config,
                task_sets=wet_task_sets(body,self.config),selector_seed=2305478,resolutions=(4,),load_model=LABEL,wet_payload=p)
            self.assertEqual(r['methods']['M0']['ranking'],dry['methods']['M0']['ranking'])
            self.assertGreater(r['methods']['M2/4']['timings']['lp_calls'],0)
            self.assertEqual(r['methods']['M2/4']['timings']['cache_hits'],
                sum(row['timings_s']['cache_hits'] for row in r['methods']['M2/4']['scores']))
            seen.append(binding['combined_object_hash'])
        self.assertEqual(len(set(seen)),2)


if __name__=='__main__':unittest.main()
