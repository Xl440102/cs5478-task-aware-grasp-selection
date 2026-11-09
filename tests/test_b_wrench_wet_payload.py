"""Static numerical validation only; no episode, fluid step or outcome input."""
from dataclasses import FrozenInstanceError, replace, asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.wrench_v2.wet_payload import (WetPayloadSpec,build_wet_payload,combine_wet_object,
    validate_wet_object,LABEL,LATTICE_LABEL,ROOT)
from grasp_b.wrench_v2.dynamics import object_parameters_from_snapshot
from grasp_b.wrench_v2.tasks import build_task_wrench_set,Orientation
from grasp_b.wrench_v2.m1 import prepare_contact_spec,fingerprint
from grasp_b.wrench_v2.evaluation import evaluate_selection,wet_task_sets,dry_task_sets
from tests.test_b_wrench_dynamics import evaluate,state
from grasp_b.wrench_v2.motion_prediction import PredictedPose
from tests.test_b_wrench_evaluation import inputs


class WetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'results/local/task07_shared_feasible_20261106_final/summary.json'
        if path.exists():cls.record=json.loads(path.read_text())
        else:
            from grasp_b.wrench_v2.physics_snapshot import static_physics_snapshot
            cls.record={'static_snapshot':static_physics_snapshot(ROOT)}
        cls.body=object_parameters_from_snapshot(cls.record['static_snapshot'],asset_path=ROOT/'assets/mug_hollow.urdf')

    def test_default_math_and_parallel_axis(self):
        p=build_wet_payload(); w,binding=combine_wet_object(self.body,p)
        self.assertAlmostEqual(p.fill_height_m,.031084949822635807)
        self.assertAlmostEqual(p.water_com_mug[2],-.0264575250886821)
        np.testing.assert_allclose(np.diag(p.water_inertia_about_com_mug),(3.3652284212298216e-5,3.3652284212298216e-5,5.12e-5))
        self.assertAlmostEqual(w.mass_kg,.38);self.assertAlmostEqual(w.com_m[2],-.006962506602284763)
        np.testing.assert_allclose(np.diag(w.inertia_com_kg_m2),(.0006918279449497828,.00042617794494978283,.0005863966666666668))
        expected=np.zeros((3,3))
        for mass,com,I in ((self.body.mass_kg,self.body.com_m,self.body.inertia_com_kg_m2),(p.water_mass_kg,p.water_com_mug,p.water_inertia_about_com_mug)):
            d=np.array(com)-w.com_m;expected+=np.array(I)+mass*((d@d)*np.eye(3)-np.outer(d,d))
        np.testing.assert_allclose(w.inertia_com_kg_m2,expected)
        self.assertEqual(binding,validate_wet_object(w,p))

    def test_mass_radius_and_hash_identity(self):
        a=build_wet_payload();b=build_wet_payload(SequenceConfig(water_mass_kg=.2));c=build_wet_payload(radius_m=.03175)
        self.assertEqual(len({a.model_hash,b.model_hash,c.model_hash}),3)
        self.assertAlmostEqual(b.fill_height_m,2*a.fill_height_m)
        self.assertGreater(c.fill_height_m,a.fill_height_m)

    def test_zero_mass_dry_limit(self):
        p=build_wet_payload(water_mass_kg=0);w,_=combine_wet_object(self.body,p)
        self.assertEqual(w.mass_kg,self.body.mass_kg)
        np.testing.assert_allclose(w.com_m,self.body.com_m)
        np.testing.assert_allclose(w.inertia_com_kg_m2,self.body.inertia_com_kg_m2)
        with self.assertRaises(ValueError):SequenceConfig(water_mass_kg=0)

    def test_bad_parameters_and_geometry_rejected(self):
        for kwargs in ({'water_mass_kg':-1},{'water_mass_kg':float('nan')},{'water_mass_kg':float('inf')},
                       {'radius_m':0},{'radius_m':-.1},{'radius_m':.04},{'water_mass_kg':.3}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):build_wet_payload(**kwargs)
        p=build_wet_payload()
        for kwargs in ({'frame':'world'},{'density_kg_m3':0},{'water_com_mug':(0,0,float('nan'))},
                       {'water_com_mug':('0',0,0)},{'water_com_mug':(0,0)},
                       {'fill_height_m':.08},{'water_inertia_about_com_mug':np.eye(3)},
                       {'bottom_z_m':-.5},{'config_hash':'unknown'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):replace(p,**kwargs)

    def test_immutable_roundtrip_and_tamper(self):
        p=build_wet_payload()
        with self.assertRaises(FrozenInstanceError):p.water_mass_kg=2
        with self.assertRaises(TypeError):p.asset_hashes['x']='bad'
        raw=json.loads(json.dumps(p.to_dict()))
        self.assertEqual(WetPayloadSpec.from_dict(raw).model_hash,p.model_hash)
        for key,value in (('water_mass_kg',.2),('model_hash','a'*64),('frame','world'),('units','mm'),('status','valid')):
            bad={**raw,key:value}
            with self.subTest(key=key),self.assertRaises(ValueError):WetPayloadSpec.from_dict(bad)

    def test_no_duplicate_proxy_unverified_or_urdf_inertia(self):
        p=build_wet_payload();wet,_=combine_wet_object(self.body,p)
        for b in (wet,replace(self.body,mass_kg=.38),replace(self.body,source='urdf_declared_not_runtime_verified'),
                  replace(self.body,inertia_com_kg_m2=np.diag((.00035,.00035,.00022))),replace(self.body,provenance={})):
            with self.subTest(source=b.source),self.assertRaises(ValueError):combine_wet_object(b,p)

    def test_asset_change_invalidates_binding(self):
        p=build_wet_payload()
        with patch('grasp_b.wrench_v2.wet_payload.sha',return_value='0'*64):
            with self.assertRaises(ValueError):combine_wet_object(self.body,p)
        bad=replace(p,asset_hashes={**p.asset_hashes,'assets/mug_hollow.urdf':'0'*64})
        with self.assertRaises(ValueError):combine_wet_object(self.body,bad)

    def test_90_degree_torque_and_reference(self):
        wet,_=combine_wet_object(self.body,build_wet_payload())
        pose=PredictedPose((0,0,0),Rotation.from_euler('x',90,degrees=True).as_quat())
        result=evaluate(state(pose=pose),wet)
        w=np.array(result.required_contact_wrench_mug)
        np.testing.assert_allclose(w[:3],(0,3.7278,0),atol=1e-14)
        self.assertAlmostEqual(w[3],.0259548321129971)
        shifted=evaluate(state(pose=pose),wet,reference=(.01,0,0))
        np.testing.assert_allclose(shifted.required_contact_wrench_mug[3:],w[3:]-np.cross((.01,0,0),w[:3]),atol=1e-14)

    def test_static_dynamic_wet_limit(self):
        wet,_=combine_wet_object(self.body,build_wet_payload())
        for angle in (0,30,90,110):
            q=Rotation.from_euler('x',angle,degrees=True).as_quat()
            scenario=build_task_wrench_set('pour_water',wet,reference_point_m=(0,0,0),gravity_world_m_s2=(0,0,-9.81),context_source='fixture',
                orientations=(Orientation(quaternion_xyzw=q,source='fixture'),)).scenarios[0]
            np.testing.assert_allclose(evaluate(state(pose=PredictedPose((0,0,0),q)),wet).required_contact_wrench_mug,scenario.required_contact_wrench,atol=1e-14)

    def test_positive_unknown_inertia_not_zero(self):
        p=replace(build_wet_payload(),water_inertia_about_com_mug=None)
        self.assertEqual(p.status,'missing_inertia')
        self.assertEqual(WetPayloadSpec.from_dict(p.to_dict()).status,'missing_inertia')
        wet,_=combine_wet_object(self.body,p)
        self.assertIsNone(wet.inertia_com_kg_m2)
        self.assertEqual(evaluate(state(alpha=(1,0,0)),wet).status,'missing_inertia')

    def test_constructor_model_is_zero_step_sensitivity(self):
        with patch('grasp_a.fluid.PositionBasedFluid.step',side_effect=AssertionError('no step')):
            p=build_wet_payload(model_name=LATTICE_LABEL)
        self.assertAlmostEqual(p.water_com_mug[2],-.02033828265421616)
        self.assertNotEqual(p.model_hash,build_wet_payload().model_hash)
        self.assertEqual(WetPayloadSpec.from_dict(p.to_dict()).model_hash,p.model_hash)
        with self.assertRaises(ValueError):replace(p,water_inertia_about_com_mug=np.zeros((3,3)))

    def pipeline(self,payload=None,mode=LABEL,prepared_status=None,sequence_config=None):
        cs,record,prepared,_,config=inputs(task='pour_water',n_candidates=1)
        if sequence_config is not None:record['task_spec']['config']=asdict(sequence_config)
        prepared={cid:prepare_contact_spec(replace(p.spec,provenance={'object_asset_sha256':self.body.provenance['asset_sha256']}),config) for cid,p in prepared.items()}
        if prepared_status:
            prepared={cid:replace(p,status=prepared_status,reason='status-preservation fixture') for cid,p in prepared.items()}
        payload=build_wet_payload() if payload is None else payload
        wet,_=combine_wet_object(self.body,payload)
        return evaluate_selection(cs,record,prepared,body=wet,config=config,task_sets=wet_task_sets(wet,config),selector_seed=9,
            resolutions=(4,),load_model=mode,wet_payload=payload,sequence_config=sequence_config)

    def test_different_mass_requires_matching_config_and_new_lp(self):
        seq=SequenceConfig(water_mass_kg=.2);p=build_wet_payload(seq)
        with self.assertRaises(ValueError):self.pipeline(p)
        r=self.pipeline(p,sequence_config=seq)
        self.assertAlmostEqual(r['body']['mass_kg'],.48)
        self.assertGreater(r['methods']['M2/4']['timings']['lp_calls'],0)
        self.assertNotEqual(p.model_hash,build_wet_payload().model_hash)

    def test_shared_identity_m0_and_strict_statuses(self):
        a=self.pipeline();b=self.pipeline(build_wet_payload(radius_m=.03175))
        self.assertEqual(a['methods']['M0']['ranking'],b['methods']['M0']['ranking'])
        self.assertNotEqual(a['load_model_identity']['wet_payload_hash'],b['load_model_identity']['wet_payload_hash'])
        for m in a['methods'].values():self.assertEqual(m['load_model_identity'],a['load_model_identity'])
        for status in ('geometry_invalid','geometry_unsupported','missing_parameters'):
            r=self.pipeline(prepared_status=status)
            for m in ('M1','M2/4'):
                self.assertIsNone(r['methods'][m]['selected_id']);self.assertEqual(r['methods'][m]['status_counts'],{status:1})

    def test_wet_dry_mislabelling_rejected(self):
        with self.assertRaises(ValueError):self.pipeline(mode='dry')

    def test_partial_inertia_pipeline_abstains_dynamic(self):
        p=replace(build_wet_payload(),water_inertia_about_com_mug=None)
        r=self.pipeline(p)
        self.assertEqual(r['methods']['M2/4']['status_counts'],{'missing_inertia':1})
        self.assertIsNone(r['methods']['M2/4']['selected_id'])


if __name__=='__main__':unittest.main()
