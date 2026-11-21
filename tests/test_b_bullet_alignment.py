"""Real DIRECT queries, reset states only; no physics steps or motor API."""
from dataclasses import replace,FrozenInstanceError
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pybullet as p

from grasp_b.contact_refinement.bullet_alignment import (
    StaticBulletAlignment,StaticQueryConfig,BulletAlignmentEvidence,transform,pose,
)
from grasp_b.contact_refinement.geometry_validation import validate_contact_geometry,load_palm_mesh
from grasp_b.wrench_v2.adapter import GeneratorInfo,build_contact_spec
from grasp_b.wrench_v2.geometry import ObjectGeometry
from grasp_b.multifamily import generate_multifamily,pruned_config,VERSION
from grasp_b.models import read_model


class TransformTests(unittest.TestCase):
    def test_composition_order_and_inertial_roundtrip(self):
        from scipy.spatial.transform import Rotation
        wo=transform([.3,-.2,.7],Rotation.from_euler('xyz',[.2,.3,.4]).as_quat())
        ot=transform([.03,.02,.01],Rotation.from_euler('z',.8).as_quat())
        oi=transform([.01,-.02,.03],Rotation.from_euler('x',.5).as_quat())
        np.testing.assert_allclose((wo@ot)@np.linalg.inv(ot),wo,atol=1e-15)
        np.testing.assert_allclose((wo@oi)@np.linalg.inv(oi),wo,atol=1e-15)
        np.testing.assert_allclose(transform(*pose(wo)),wo,atol=1e-15)

    def test_invalid_query_config(self):
        for args in (dict(point_probe_radius_m=0),dict(frame_tolerance_m=float('nan')),dict(scan_intervals=1)):
            with self.assertRaises(ValueError):StaticQueryConfig(**args)


class StaticBulletTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=StaticBulletAlignment();cls.session.__enter__()
        try:
            cls.model=read_model('hollow',cls.session.panda);cls.geometry=ObjectGeometry.from_model(cls.model)
            cs,rs=generate_multifamily(cls.model,pruned_config());cls.candidates={c.id:c for c in cs};cls.records={r['id']:r for r in rs}
            cls.palm=load_palm_mesh(cls.session.panda)
        except BaseException:cls.session.close();raise

    @classmethod
    def tearDownClass(cls):cls.session.close()

    def inputs(self,cid):
        c=self.candidates[cid];s=build_contact_spec(c,self.geometry,self.session.gripper,
            generator_info=GeneratorInfo('grasp_b.multifamily',VERSION,self.records[cid]['family'],c.id))
        g=validate_contact_geometry(c,s,self.geometry,self.session.gripper,palm=self.palm).to_dict()
        return c,s,g

    def test_metadata_frames_scaling_and_mesh_vertices(self):
        for name,f in self.session.metadata['fingers'].items():
            self.assertEqual(f['urdf_mesh_scale'],[1.,1.,1.])
            self.assertEqual(f['collision'][0]['dimensions'],[1.,1.,1.])
            self.assertLess(f['collision_frame_residual'],1e-10)
            self.assertEqual(f['raw_triangles'],32)
            self.assertEqual(len(f['simulation_mesh_vertices_inertial']),18)
            self.assertGreater(f['nearest_vertex_difference_m']['max'],1e-7)
            self.assertAlmostEqual(f['dynamics'][11],.001)
        self.assertEqual(len(self.session.metadata['mug']['collision']),len(self.geometry.primitives))
        self.assertEqual(self.session.metadata['mug']['collision'][0]['geometry_type'],p.GEOM_MESH)
        self.assertEqual(self.geometry.primitives[0].shape.kind,'cylinder')

    def test_six_body_comparisons_without_control_or_steps(self):
        ids=['034c4eac3fb53111','235da732cc7a75db','2d90f2a44d16be5c','95ebaa748768a922','db2df20cfd3cb7be','e8ac427b77b0c9cf']
        with patch('pybullet.stepSimulation',side_effect=AssertionError('NO PHYSICS')),patch('pybullet.setJointMotorControl2',side_effect=AssertionError('NO MOTORS')):
            for suffix in ids:
                c,s,g=self.inputs('B3_'+suffix);r=self.session.audit(c,s,g,self.geometry).to_dict()
                self.assertEqual(r['classification'],'surface_mismatch')
                self.assertFalse(r['coverage']['frame_mismatch'])
                self.assertTrue(r['coverage']['earlier_contact_evidence'])
                for f in r['fingers']:
                    self.assertLess(f['frame_error'],1e-10)
                    self.assertLess(f['transition']['displacement_interval_m'][1],.0025)
                    self.assertLess(f['at_C1_raw_intersection']['minimum_distance_m'],-.0008)

    def test_top_rim_and_both_handle_mechanisms(self):
        for family,mechanism in [('top',None),('rim',None),('handle','handle_side'),('handle','handle_top_down')]:
            for cid in sorted(self.candidates):
                if self.records[cid]['family']!=family:continue
                c,s,g=self.inputs(cid)
                if not s.contacts or (mechanism and s.mechanism.value!=mechanism):continue
                r=self.session.audit(c,s,g,self.geometry).to_dict()
                self.assertTrue(r['coverage']['static_query_supported'])
                self.assertEqual(len(r['contacts']),len(s.contacts));break
            else:self.fail('missing static fixture')

    def test_cached_distance_still_resets_actual_joint_state(self):
        c,s,g=self.inputs('B3_034c4eac3fb53111');self.session.place(c)
        a=self.session.at_joint('left',.038);self.session.at_joint('left',.035)
        b=self.session.at_joint('left',.038)
        self.assertEqual(a,b)
        self.assertAlmostEqual(p.getJointState(self.session.robot,self.session.fingers['left'],physicsClientId=self.session.client)[0],.038)

    def test_proxy_margin_change_is_observed_and_not_applied_to_baseline(self):
        c,s,g=self.inputs('B3_034c4eac3fb53111');r=self.session.audit(c,s,g,self.geometry,detailed=True).to_dict()
        for f in r['fingers']:
            rows=f['proxy_interventions']['rows'];self.assertTrue(all(x['intervention_readback_matches'] for x in rows))
            a=next(x for x in rows if x['requested_hull_margin_m']==.001 and x['requested_box_margin_m']==0)
            b=next(x for x in rows if x['requested_hull_margin_m']==0 and x['requested_box_margin_m']==0)
            self.assertAlmostEqual(b['hull_vs_original_mug_min_m']-a['hull_vs_original_mug_min_m'],.001,places=7)
            self.assertAlmostEqual(p.getDynamicsInfo(self.session.robot,self.session.fingers[f['finger_id']],physicsClientId=self.session.client)[11],.001)

    def test_roundtrip_identity_and_missing_contact_evidence(self):
        c,s,g=self.inputs('B3_034c4eac3fb53111');r=self.session.audit(c,s,g,self.geometry)
        self.assertEqual(r.to_json(),BulletAlignmentEvidence.from_json(r.to_json()).to_json())
        with self.assertRaises(TypeError):r.payload['coverage']['surface_mismatch']=False
        with self.assertRaises(ValueError):r.assert_identity(candidate_hash='0'*64)
        bad=r.to_dict();bad['classification']='unknown'
        with self.assertRaises(ValueError):BulletAlignmentEvidence(bad)
        with self.assertRaises(ValueError):BulletAlignmentEvidence.from_json('{"a":1,"a":2}')
        cid=next(cid for cid in self.candidates if self.inputs(cid)[1].status.value=='invalid')
        c,s,g=self.inputs(cid);v=self.session.audit(c,s,g,self.geometry).to_dict()
        self.assertEqual(v['classification'],'unknown');self.assertFalse(v['contacts'])

    def test_asset_and_spec_mismatch_rejected(self):
        with self.assertRaises(ValueError),patch('pybullet.connect',side_effect=AssertionError('must reject before connect')):
            StaticBulletAlignment(expected_assets={'mug_urdf':'0'*64})
        c,s,g=self.inputs('B3_034c4eac3fb53111')
        with self.assertRaises(ValueError):self.session.audit(replace(c,id='wrong'),s,g,self.geometry)

    def test_query_threshold_and_probe_precision_sensitivity(self):
        c,s,g=self.inputs('B3_034c4eac3fb53111');r=self.session.audit(c,s,g,self.geometry,detailed=True).to_dict()
        for x in r['contacts']:
            gaps=[p['inferred_point_gap_m'] for p in x['probe_radius_sensitivity']]
            self.assertLess(max(gaps)-min(gaps),2e-5)
            self.assertTrue(all(abs(d)>.0009 for d in gaps))

    def test_nonunit_mesh_scale_and_probe_formula_on_known_geometry(self):
        client=self.session.client
        vertices=[[x,y,z] for x in (-.01,.01) for y in (-.01,.01) for z in (-.01,.01)]
        shapes=[]
        try:
            sphere=p.createCollisionShape(p.GEOM_SPHERE,radius=1e-6,physicsClientId=client)
            probe=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=sphere,basePosition=[10.03,0,0],physicsClientId=client);shapes.append(probe)
            for scale,expected in ((1.,.02),(2.,.01)):
                col=p.createCollisionShape(p.GEOM_MESH,vertices=vertices,meshScale=[scale,1,1],physicsClientId=client)
                body=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=col,basePosition=[10,0,0],physicsClientId=client);shapes.append(body)
                p.changeDynamics(body,-1,collisionMargin=0,physicsClientId=client)
                cp=p.getClosestPoints(body,probe,.1,physicsClientId=client)
                self.assertTrue(cp)
                self.assertAlmostEqual(min(x[8] for x in cp)+1e-6,expected,places=7)
        finally:
            for b in shapes:p.removeBody(b,physicsClientId=client)


class ClientIsolationTests(unittest.TestCase):
    def test_context_closes_on_exception_and_keeps_other_client(self):
        other=p.connect(p.DIRECT)
        try:
            audit=StaticBulletAlignment()
            with self.assertRaises(RuntimeError):
                with audit:
                    own=audit.client;self.assertNotEqual(own,other);raise RuntimeError('test')
            self.assertIsNone(audit.client);self.assertFalse(p.isConnected(own));self.assertTrue(p.isConnected(other))
        finally:p.disconnect(other)

    def test_world_yaw_does_not_change_relative_geometry(self):
        from scipy.spatial.transform import Rotation
        values=[]
        for yaw in (0.,.7):
            with StaticBulletAlignment(base_pose=((.1,-.2,.6),tuple(Rotation.from_euler('z',yaw).as_quat()))) as a:
                model=read_model('hollow',a.panda);geo=ObjectGeometry.from_model(model);cs,rows=generate_multifamily(model,pruned_config())
                c=next(c for c in cs if c.id=='B3_034c4eac3fb53111');a.place(c)
                values.append(a.at_joint('left',.03534)['minimum_distance_m'])
        self.assertAlmostEqual(*values,places=10)


if __name__=='__main__':unittest.main()
