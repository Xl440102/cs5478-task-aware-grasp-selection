"""Analytic sweep fixtures and real, unstepped collision-proxy queries."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pybullet as p

from grasp_b.contact_refinement.collision_regions import (
    RegionQueryConfig, analyze_samples, refine_events, cluster_witnesses,
    representation_identity, build_collision_region_evidence,
    analytic_surface_associations,
)
from grasp_b.contact_refinement.contact_evidence import CollisionRegionEvidence
from grasp_b.contact_refinement.bullet_alignment import StaticBulletAlignment
from grasp_b.contact_refinement.geometry_validation import validate_contact_geometry, load_palm_mesh
BODY_IDS=tuple('B3_'+v for v in ('034c4eac3fb53111','235da732cc7a75db','2d90f2a44d16be5c',
    '95ebaa748768a922','db2df20cfd3cb7be','e8ac427b77b0c9cf'))
from grasp_b.models import read_model
from grasp_b.multifamily import generate_multifamily, pruned_config, VERSION
from grasp_b.wrench_v2.geometry import ObjectGeometry
from grasp_b.wrench_v2.adapter import GeneratorInfo, build_contact_spec


def query_fixture(function):
    return lambda x: dict(displacement_m=x, minimum_distance_m=float(function(x)), points=[])


class SweepMathTests(unittest.TestCase):
    def test_sign_monotonic_entry_and_exit(self):
        config = RegionQueryConfig(displacement_resolution_m=1e-7)
        q = query_fixture(lambda x: .3-x)
        analysis = analyze_samples([q(x) for x in [0., .25, .5, 1.]], config)
        self.assertTrue(analysis['sampled_monotone_nonincreasing'])
        event = refine_events(q, .25, .5, config)['brackets'][0]
        self.assertEqual(event['kind'], 'entry')
        self.assertLessEqual(event['interval_m'][0], .3)
        self.assertGreaterEqual(event['interval_m'][1], .3)
        self.assertLessEqual(np.ptp(event['interval_m']), config.displacement_resolution_m)

    def test_nonmonotone_multiple_transitions_retained(self):
        c = RegionQueryConfig()
        q = query_fixture(lambda x: (x-.2)*(x-.4)*(x-.6)*(x-.8))
        result = analyze_samples([q(float(x)) for x in np.linspace(0, 1, 21)], c)
        self.assertFalse(result['sampled_monotone_nonincreasing'])
        self.assertEqual([x['kind'] for x in result['transitions']], ['entry', 'exit', 'entry', 'exit'])
        self.assertFalse(result['continuous_first_contact_certified'])

    def test_refinement_preserves_new_multiple_crossings(self):
        c = RegionQueryConfig(displacement_resolution_m=1e-5)
        q = query_fixture(lambda x: -((x-.2)*(x-.4)*(x-.8)))
        result = refine_events(q, 0., 1., c)
        self.assertEqual([b['kind'] for b in result['brackets']], ['entry', 'exit', 'entry'])
        self.assertFalse(result['unique_first_contact_certified'])

    def test_tangent_touch_not_claimed_absent_or_unique(self):
        q = query_fixture(lambda x: (x-.1234567)**2)
        result = analyze_samples([q(float(x)) for x in np.linspace(0, 1, 17)], RegionQueryConfig())
        self.assertFalse(result['transitions'])
        self.assertFalse(result['continuous_first_contact_certified'])

    def test_range_censor_nan_and_order_rejected(self):
        c = RegionQueryConfig()
        self.assertEqual(analyze_samples([dict(displacement_m=0.,minimum_distance_m=None),dict(displacement_m=1.,minimum_distance_m=0)], c)['status'],'range_censored')
        for values in ([dict(displacement_m=0.,minimum_distance_m=float('nan')),dict(displacement_m=1.,minimum_distance_m=0)],
                       [dict(displacement_m=1.,minimum_distance_m=1),dict(displacement_m=0.,minimum_distance_m=0)]):
            with self.assertRaises(ValueError): analyze_samples(values, c)
        for kwargs in (dict(sweep_intervals=1),dict(region_link_distance_m=float('inf')),dict(distance_threshold_m=True)):
            with self.assertRaises(ValueError): RegionQueryConfig(**kwargs)

    def test_planar_group_and_edge_normal_ambiguity(self):
        c = RegionQueryConfig()
        def witness(position, normal, x):
            return dict(point_on_mug_m=position,normal_on_mug_mug=normal,displacement_m=x)
        a = witness([0,0,0],[0,0,1],.001)
        b = witness([1e-5,0,0],[0,0,1],.00100001)
        self.assertEqual(len(cluster_witnesses([a,b],c)),1)
        edge = witness([0,0,0],[1,0,0],.001)
        groups = cluster_witnesses([a,edge],c)
        self.assertEqual(len(groups),2)
        self.assertTrue(all(g['compound_child_identity']=='not_exposed' for g in groups))
        self.assertNotIn('force_capacity',groups[0])


class RealRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=StaticBulletAlignment();cls.session.__enter__()
        try:
            cls.model=read_model('hollow',cls.session.panda);cls.geometry=ObjectGeometry.from_model(cls.model)
            candidates, info=generate_multifamily(cls.model,pruned_config())
            cls.candidates={c.id:c for c in candidates};cls.info={r['id']:r for r in info}
            cls.raw={}  # Build small static fixtures on demand; no historical archive needed.
            cls.palm=load_palm_mesh(cls.session.panda)
        except BaseException: cls.session.close();raise

    @classmethod
    def tearDownClass(cls): cls.session.close()

    def inputs(self,cid):
        c=self.candidates[cid]
        spec=build_contact_spec(c,self.geometry,self.session.gripper,
            generator_info=GeneratorInfo('grasp_b.multifamily',VERSION,self.info[cid]['family'],c.id))
        if cid not in self.raw:
            self.raw[cid]=validate_contact_geometry(c,spec,self.geometry,self.session.gripper,palm=self.palm).to_dict()
        return c,spec,self.raw[cid]

    def build(self,cid,config=None):
        return build_collision_region_evidence(self.session,*self.inputs(cid),config=config)

    def test_six_body_regions_and_no_physical_support_claim(self):
        with patch('pybullet.stepSimulation',side_effect=AssertionError('NO PHYSICS')), patch('pybullet.setJointMotorControl2',side_effect=AssertionError('NO CONTROL')):
            for cid in BODY_IDS:
                result=self.build(cid).to_dict()
                self.assertTrue(result['coverage']['contact_region_evidence_available'])
                self.assertTrue(result['coverage']['earlier_collision_evidence'])
                self.assertEqual(result['actual_solver_manifold'],'not_observed')
                for finger in result['fingers']:
                    entry=[b for e in finger['event_refinements'] for b in e['brackets'] if b['kind']=='entry'][0]
                    self.assertLess(entry['interval_m'][1],.0025)
                    self.assertTrue(entry['resolution_reached'])
                    for region in finger['regions']:
                        self.assertEqual(region['physical_contact_patch'],'not_established')
                        for w in region['witnesses']:
                            self.assertAlmostEqual(np.linalg.norm(w['normal_on_mug_mug']),1.,places=6)

    def test_transforms_and_distance_sign_on_real_event(self):
        result=self.build(BODY_IDS[0]).to_dict()
        for finger in result['fingers']:
            self.assertGreater(finger['samples'][0]['minimum_distance_m'],0)
            entry=finger['event_refinements'][0]['brackets'][0]
            self.assertGreater(entry['endpoints'][0]['minimum_distance_m'],0)
            self.assertLessEqual(entry['endpoints'][1]['minimum_distance_m'],0)
            for row in finger['samples']:
                t=np.array(row['finger_link_to_mug_transform'])
                np.testing.assert_allclose(t[3],[0,0,0,1],atol=1e-12)
                np.testing.assert_allclose(t[:3,:3].T@t[:3,:3],np.eye(3),atol=1e-12)

    def test_top_rim_handle_real_mechanisms(self):
        ids=['B3_2880fbb6c6702788','B3_06a768a33d6d58aa','B3_01d31ecafeb58600','B3_178b0b42e93bcffb']
        for cid in ids:
            r=self.build(cid).to_dict()
            self.assertTrue(r['coverage']['collision_geometry_supported'])
            self.assertTrue(r['coverage']['contact_region_evidence_available'])
            self.assertEqual(r['provenance']['source'],'predicted_geometry_query')
            for finger in r['fingers']:
                for region in finger['regions']:
                    for witness in region['witnesses']:
                        self.assertTrue(witness['surface_associations'])
                        self.assertTrue(all(a['identity_status'].startswith('geometric_association_only') for a in witness['surface_associations']))

    def test_finite_urdf_box_face_association_is_not_engine_identity(self):
        from grasp_b.models import rotation
        primitive=next(p for p in self.geometry.primitives if p.shape.kind=='box')
        shape=primitive.shape;r=rotation(shape.rpy);half=np.array(shape.dimensions)/2
        point=np.array(shape.position)+r@np.array([half[0],0,0])
        associations=analytic_surface_associations(point,r[:,0],self.geometry,RegionQueryConfig())
        target=next(a for a in associations if a['primitive_id']==primitive.primitive_id and a['raw_box_face']=='x+')
        self.assertLess(target['finite_face_distance_m'],1e-12)
        self.assertLess(target['query_normal_angle_deg'],1e-5)
        self.assertEqual(target['identity_status'],'geometric_association_only_not_engine_child_identity')

    def test_opening_overlap_is_geometry_conflict_not_mechanics(self):
        r=self.build('B3_0a8e774c4c896d8e').to_dict()
        self.assertEqual(r['classification'],'geometry_conflict')
        self.assertTrue(r['fingers'][0]['sampled_analysis']['opening_overlap_or_contact'])
        self.assertNotIn('utilization',r)

    def test_strict_roundtrip_tamper_identity_and_immutability(self):
        record=self.build(BODY_IDS[0]);loaded=CollisionRegionEvidence.from_json(record.to_json())
        self.assertEqual(record.to_json(),loaded.to_json())
        with self.assertRaises(TypeError):record.payload['fingers'][0]['regions'][0]['region_id']='modified'
        with self.assertRaises(ValueError):record.assert_identity(candidate_hash='0'*64)
        bad=record.to_dict();bad['representation']['assets']['mug_urdf']='0'*64
        with self.assertRaises(ValueError):CollisionRegionEvidence(bad)
        with self.assertRaises(ValueError):CollisionRegionEvidence.from_json('{"a":1,"a":2}')
        with self.assertRaises(ValueError):CollisionRegionEvidence.from_json(record.to_json().replace('"length_unit":"m"','"length_unit":"mm"'))

    def test_representation_and_config_identity_cache_isolation(self):
        a=self.build(BODY_IDS[0]);b=self.build(BODY_IDS[0],RegionQueryConfig(sweep_intervals=16));c=self.build(BODY_IDS[1])
        self.assertNotEqual(a.payload['input_hash'],b.payload['input_hash'])
        self.assertNotEqual(a.payload['input_hash'],c.payload['input_hash'])
        self.assertEqual(a.payload['representation_hash'],b.payload['representation_hash'])
        self.assertEqual(self.build(BODY_IDS[0]).payload['input_hash'],a.payload['input_hash'])
        self.assertGreater(a.payload['cache_hits'],0)
        self.assertGreater(a.payload['actual_query_calls'],0)

    def test_raw_asset_or_candidate_mismatch_refused(self):
        c,s,raw=self.inputs(BODY_IDS[0])
        with self.assertRaises(ValueError):build_collision_region_evidence(self.session,replace(c,id='bad'),s,raw)
        raw=json.loads(json.dumps(raw));raw['geometry_assets']['mug_sha256']='0'*64
        with self.assertRaises(ValueError):build_collision_region_evidence(self.session,c,s,raw)

    def test_margin_sensitivity_only_separate_proxy(self):
        from grasp_b.contact_refinement.contact_regions import digest
        before=digest(representation_identity(self.session))
        c,s,raw=self.inputs(BODY_IDS[0]);self.session.place(c)
        group=raw['finger_groups'][0];shape=self.geometry.primitive(group['mesh_sweep']['definite_first']['primitive_id'])
        rows=self.session.proxy_comparison('left',shape,group['first_joint_position_m'])['rows']
        a=next(r for r in rows if r['reported_hull_margin_m']==.001 and r['reported_box_margin_m']==0)
        b=next(r for r in rows if r['reported_hull_margin_m']==0 and r['reported_box_margin_m']==0)
        self.assertAlmostEqual(b['hull_vs_original_mug_min_m']-a['hull_vs_original_mug_min_m'],.001,places=7)
        self.assertEqual(before,digest(representation_identity(self.session)))

    def test_resolution_and_threshold_sensitivity_recorded(self):
        a=self.build(BODY_IDS[0]).to_dict()
        b=self.build(BODY_IDS[0],RegionQueryConfig(sweep_intervals=64,distance_threshold_m=1e-7)).to_dict()
        for x,y in zip(a['fingers'],b['fingers']):
            p=x['event_refinements'][0]['brackets'][0]['interval_m'];q=y['event_refinements'][0]['brackets'][0]['interval_m']
            self.assertLess(abs(np.mean(p)-np.mean(q)),1e-6)
        self.assertNotEqual(a['input_hash'],b['input_hash'])


if __name__=='__main__': unittest.main()
