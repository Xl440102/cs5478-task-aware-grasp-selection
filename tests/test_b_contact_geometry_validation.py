from dataclasses import replace
import unittest
import itertools

import numpy as np

from grasp_b.models import Shape
from grasp_b.wrench_v2.geometry import Primitive,ObjectGeometry,quaternion_matrix,transform_finger_mesh
from grasp_b.contact_refinement.geometry_validation import (
    swept_triangles_box,triangle_box_witness,mesh_primitive_sweep,validate_contact_geometry,load_palm_mesh,prepare_mesh,
)
from grasp_b.contact_refinement.contact_regions import GeometryTolerances


def box_triangles(center, dimensions):
    out=[];center=np.asarray(center);h=np.asarray(dimensions)/2
    for axis,sign in itertools.product(range(3),(-1,1)):
        u=np.eye(3)[(axis+1)%3]*h[(axis+1)%3];v=np.eye(3)[(axis+2)%3]*h[(axis+2)%3]
        base=center+sign*h[axis]*np.eye(3)[axis]
        square=[base-u-v,base+u-v,base+u+v,base-u+v]
        if sign<0:square.reverse()
        out.extend([[square[0],square[1],square[2]],[square[0],square[2],square[3]]])
    return tuple(tuple(tuple(p) for p in t) for t in out)


def planar_fixture(*, sequential=False, opening=.08, edge=False):
    from grasp_a.contracts import Candidate,Pose,candidate_sha256
    from grasp_b.wrench_v2.geometry import FingerMesh,GripperGeometry
    from tests.test_b_wrench_contacts import spec as make_spec
    base=make_spec();primitives=[]
    for i,(x,y,height) in enumerate([(-.005,0.,.06),(.005,-.0025 if sequential else 0.,.055 if sequential else .06)]):
        primitives.append(Primitive('body:box:'+str(i),Shape('box',(x,y,0.),(0.,0.,0.),(.01,height,.02),'body')))
    geom=ObjectGeometry('synthetic',tuple(primitives),(0,0,0),'synthetic_asset','synthetic_version')
    fingers=tuple(FingerMesh(name,box_triangles((0,sign*.005,0),(.02,.01,.02)),(0,0,0),(0,0,0),0.,.04,'synthetic_mesh')
                  for name,sign in [('left',1),('right',-1)])
    gripper=GripperGeometry(fingers,'synthetic_panda','synthetic_palm',tuple((x,z,0.,0.) for x,z in itertools.product((-.005,0.,.005),repeat=2)))
    c=Candidate('synthetic_plane','body','explicit analytical fixture',opening,0.,Pose((0,0,0),(0,0,0,1)),Pose((0,0,-.08),(0,0,0,1)))
    contacts=[]
    for finger,sign in [('left',1),('right',-1)]:
        template=next(p for p in base.contacts if p.finger_id.value==finger)
        for i,x in enumerate((-.005,.01 if edge else .005)):
            y=(.025 if sequential and finger=='left' and i==1 else sign*.03)
            contacts.append(replace(template,position_m=(x,y,0.),normal_outward=(0,sign,0),
                                    surface_id=f'body:box:{i}:y'+('+' if sign==1 else '-')))
    spec=replace(base,candidate_id=c.id,candidate_hash=candidate_sha256([c]),contacts=tuple(contacts),
                 provenance={'fixture':'analytical, no physical validation'},closing_direction=(0,1,0),approach_direction=(0,0,1))
    palm=dict(triangles_tool=np.array(box_triangles((10,0,0),(.01,.01,.01))),mesh_sha256=gripper.palm_sha256,closed=True)
    return c,spec,geom,gripper,palm


class AnalyticSweepTests(unittest.TestCase):
    def test_planar_rigid_double_contact_with_both_fingers(self):
        c,s,g,f,p=planar_fixture();v=validate_contact_geometry(c,s,g,f,palm=p).to_dict()
        self.assertEqual(v['classification'],'geometrically_compatible')
        self.assertTrue(v['joint_compatibility']['all_predicted_contacts_have_common_independent_configuration'])
        self.assertTrue(v['joint_compatibility']['symmetric_joint_position_compatible'])
        self.assertTrue(all(x['classification']=='geometrically_compatible' for x in v['finger_groups']))

    def test_sequential_surfaces_do_not_become_independent_contact_support(self):
        c,s,g,f,p=planar_fixture(sequential=True);v=validate_contact_geometry(c,s,g,f,palm=p).to_dict()
        self.assertEqual(v['classification'],'sequential_contact')
        self.assertAlmostEqual(v['finger_groups'][0]['spread_m'],.005)

    def test_opening_penetration_and_edge_ambiguity(self):
        c,s,g,f,p=planar_fixture(opening=.05);v=validate_contact_geometry(c,s,g,f,palm=p).to_dict()
        self.assertEqual(v['classification'],'geometry_conflict')
        self.assertTrue(v['finger_groups'][0]['opening_penetrations'])
        c,s,g,f,p=planar_fixture(edge=True);v=validate_contact_geometry(c,s,g,f,palm=p).to_dict()
        self.assertEqual(v['classification'],'insufficient_evidence')
        self.assertTrue(any(x['near_edge'] for x in v['contacts']))

    def test_missing_palm_and_tangent_normals_are_not_clearance(self):
        c,s,g,f,p=planar_fixture();v=validate_contact_geometry(c,s,g,f).to_dict()
        self.assertEqual(v['classification'],'insufficient_evidence')
        tangent=replace(s,contacts=tuple(replace(x,position_m=(.01,.03,0),normal_outward=(1,0,0),surface_id='body:box:1:x+')
                            if i==0 else x for i,x in enumerate(s.contacts)))
        v=validate_contact_geometry(c,tangent,g,f,palm=p).to_dict()
        self.assertIn('edge/seam/near-tangent ambiguity',v['reasons'])

    def test_seam_identity_not_smoothed_and_independent_joint_compatibility(self):
        c,s,g,f,p=planar_fixture()
        seam=replace(s,contacts=tuple(replace(x,position_m=(0.,x.position_m[1],0.)) for x in s.contacts))
        v=validate_contact_geometry(c,seam,g,f,palm=p).to_dict()
        self.assertEqual(v['classification'],'insufficient_evidence')
        self.assertTrue(all(x['near_edge'] for x in v['contacts']))
        shift=np.array([0.,.0015,0.])
        moved=replace(g,primitives=tuple(replace(x,shape=replace(x.shape,position=tuple(np.array(x.shape.position)+shift))) for x in g.primitives))
        shifted_spec=replace(s,contacts=tuple(replace(x,position_m=tuple(np.array(x.position_m)+shift)) for x in s.contacts))
        v=validate_contact_geometry(c,shifted_spec,moved,f,palm=p).to_dict()
        self.assertEqual(v['classification'],'geometrically_compatible')
        self.assertFalse(v['joint_compatibility']['symmetric_joint_position_compatible'])
        np.testing.assert_allclose(v['joint_compatibility']['independent_joint_positions_m'],[.0315,.0285])

    def test_triangle_plane_enters_box_at_analytic_displacement(self):
        tri=np.array([[[-.2,2.,-.2],[.2,2.,-.2],[0,2.,.2]]])
        time=swept_triangles_box(tri,[0,-1,0],[1,1,1],2.)[0]
        self.assertAlmostEqual(time,1.)
        self.assertTrue(np.isinf(swept_triangles_box(tri,[0,1,0],[1,1,1],2.)[0]))
        self.assertIsNotNone(triangle_box_witness(tri[0]+[0,-1.1,0],np.ones(3)))

    def test_aabb_overlap_is_not_collision(self):
        # This triangle AABB overlaps the box, but plane x+y=3 excludes it.
        tri=np.array([[[0,3,0],[3,0,0],[3,3,0]]],float)
        self.assertTrue(np.isinf(swept_triangles_box(tri,[0,0,0],[1,1,1],0.)[0]))
        self.assertIsNone(triangle_box_witness(tri[0],np.ones(3)))

    def test_opening_penetration_has_clipped_witness(self):
        tri=np.array([[[-.2,0,-.2],[.2,0,-.2],[0,0,.2]]])
        geom=ObjectGeometry('synthetic',(Primitive('body:box:0',Shape('box',(0,0,0),(0,0,0),(2,2,2),'body')),),(0,0,0),'fixture','fixture')
        r=mesh_primitive_sweep(tri,[0,-1,0],.1,geom,tolerance_m=1e-8,closed=False)
        hit=r['definite_first'];self.assertEqual(hit['displacement_m'],0.)
        self.assertGreater(hit['witness']['box_interior_slack_m'],.7)

    def test_cylinder_broadphase_is_unknown_not_collision(self):
        p=Primitive('body:cylinder:0',Shape('cylinder',(0,0,0),(0,0,0),(1.,2.),'body'))
        geom=ObjectGeometry('synthetic',(p,),(0,0,0),'fixture','fixture')
        tri=np.array([[[-.2,0,0],[.2,0,0],[0,0,.2]]])
        r=mesh_primitive_sweep(tri,[0,0,1],.1,geom,tolerance_m=1e-8)
        self.assertIsNone(r['definite_first']);self.assertTrue(r['unresolved'])
        self.assertFalse(mesh_primitive_sweep(tri+[10,0,0],[0,0,1],.1,geom,tolerance_m=1e-8)['unresolved'])

    def test_near_tangent_and_bad_geometry_inputs(self):
        tri=np.array([[[-1.,1.,0.],[1.,1.,0.],[0.,1.,.2]]])
        self.assertIsNone(triangle_box_witness(tri[0]+[0,1e-7,0],np.ones(3)))
        w=triangle_box_witness(tri[0],np.ones(3));self.assertAlmostEqual(w['box_interior_slack_m'],0.)
        with self.assertRaises(ValueError):swept_triangles_box(tri,[0,0,0],[1,1,1],float('nan'))


class RealGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_b_wrench_adapter import StaticAdapterTests,PANDA
        from grasp_b.multifamily import generate_multifamily,pruned_config
        StaticAdapterTests.setUpClass();cls.f=StaticAdapterTests();cls.panda=PANDA
        cls.candidates,cls.records=generate_multifamily(cls.f.model,pruned_config())
        cls.by_id={c.id:c for c in cls.candidates};cls.info={r['id']:r for r in cls.records}
        cls.palm=load_palm_mesh(PANDA)

    def build(self,candidate):
        from grasp_b.wrench_v2.adapter import GeneratorInfo,build_contact_spec
        from grasp_b.multifamily import VERSION
        family=self.info[candidate.id]['family']
        return build_contact_spec(candidate,self.f.obj,self.f.gripper,generator_info=GeneratorInfo('grasp_b.multifamily',VERSION,family,candidate.id))

    def evidence(self,candidate,**kwargs):
        return validate_contact_geometry(candidate,self.build(candidate),self.f.obj,self.f.gripper,palm=self.palm,**kwargs).to_dict()

    def test_six_body_candidates_preserve_contacts_and_expose_full_mesh_evidence(self):
        ids=['034c4eac3fb53111','235da732cc7a75db','2d90f2a44d16be5c','95ebaa748768a922','db2df20cfd3cb7be','e8ac427b77b0c9cf']
        for suffix in ids:
            with self.subTest(candidate=suffix):
                c=self.by_id['B3_'+suffix];s=self.build(c);before=s.to_dict()
                r=validate_contact_geometry(c,s,self.f.obj,self.f.gripper,palm=self.palm).to_dict()
                self.assertEqual(before,s.to_dict());self.assertEqual(len(r['contacts']),4)
                for g in r['finger_groups']:
                    self.assertTrue(g['rigid_contact_configuration']['interval_empty'])
                    pair=g['pairs'][0]
                    self.assertAlmostEqual(pair['distance_m'],.00637125595,places=9)
                    self.assertAlmostEqual(pair['normal_angle_deg'],11.25,places=6)
                    self.assertTrue(pair['same_mesh_region'])
                    self.assertFalse(pair['facet_relation']['normals_continuous'])
                    self.assertTrue(pair['facet_relation']['adjacent'])
                    hit=g['mesh_sweep']['definite_first']
                    self.assertIsNotNone(hit)
                    self.assertGreater(g['first_contact_displacement_m']-hit['displacement_m'],1e-5)
                    self.assertGreater(hit['witness']['box_interior_slack_m'],1e-8)

    def test_independent_clipping_witness_reconstructs_actual_mesh_point(self):
        c=self.by_id['B3_034c4eac3fb53111'];r=self.evidence(c);rot=quaternion_matrix(c.grasp_pose.quaternion)
        for g in r['finger_groups']:
            hit=g['mesh_sweep']['definite_first'];f=self.f.gripper.finger(g['finger_id']);sign=1 if f.finger_id=='left' else -1
            triangles=np.asarray(transform_finger_mesh(f));b=np.array(hit['witness']['triangle_barycentric'])
            p=b@triangles[hit['mesh_triangle_index']]+[0,sign*(c.gripper_width/2-hit['displacement_m']),0]
            actual=np.asarray(c.grasp_pose.position)+rot@p
            np.testing.assert_allclose(actual,hit['witness']['point_mug_m'],atol=1e-12)
            primitive=self.f.obj.primitive(hit['primitive_id']);local=rotation(primitive.shape.rpy).T@(actual-primitive.shape.position)
            self.assertTrue(np.all(np.abs(local)<np.array(primitive.shape.dimensions)/2))

    def test_top_rim_handle_surfaces_are_not_inferred_from_family(self):
        for family in ('top','rim','handle'):
            candidates=[c for c in self.candidates if self.info[c.id]['family']==family and self.build(c).contacts]
            self.assertTrue(candidates)
            r=self.evidence(candidates[0]);self.assertTrue(r['contacts'])
            self.assertTrue(all(c['surface_valid'] for c in r['contacts']))
            if family=='top':self.assertTrue(all(':x' in c['surface_id'] or ':y' in c['surface_id'] for c in r['contacts']))
            if family=='rim':
                a,b=[c['normal_outward'] for c in r['contacts'][:2]]
                self.assertLess(float(np.dot(a,b)),-.9)
            if family=='handle':self.assertTrue(all(c['surface_id'].startswith('handle:box:') for c in r['contacts']))

    def test_both_handle_mechanisms_and_invalid_top(self):
        for mechanism in ('handle_side','handle_top_down'):
            c=next(c for c in self.candidates if self.info[c.id]['family']=='handle' and self.build(c).mechanism.value==mechanism and self.build(c).contacts)
            self.assertTrue(self.evidence(c)['contacts'])
        c=next(c for c in self.candidates if self.info[c.id]['family']=='top' and self.build(c).status.value=='invalid')
        r=self.evidence(c);self.assertEqual(r['classification'],'geometry_conflict')
        self.assertFalse(r['contacts'])

    def test_collision_time_bounds_cannot_be_promoted_to_clearance(self):
        c=self.by_id['B3_178b0b42e93bcffb']
        r=self.evidence(c,tolerances=GeometryTolerances(displacement_m=1e-5))
        self.assertNotEqual(r['classification'],'geometrically_compatible')
        for g in r['finger_groups']:
            if g['mesh_sweep']['definite_first']:
                hit=g['mesh_sweep']['definite_first']
                self.assertGreater(hit['witness']['box_interior_slack_m'],1e-8)

    def test_mesh_transform_roundtrip_and_finger_orientation(self):
        c=self.by_id['B3_034c4eac3fb53111'];r=self.evidence(c);rot=quaternion_matrix(c.grasp_pose.quaternion)
        for p in r['contacts']:
            tool=np.array(p['tool_position_m']);np.testing.assert_allclose(rot@tool+c.grasp_pose.position,p['position_mug_m'],atol=1e-12)
            self.assertTrue(p['finger_triangle_hits'])
            self.assertTrue(all(h['normal_tool'][1]* (1 if p['finger_id']=='left' else -1)<-.9 for h in p['finger_triangle_hits']))

    def test_asset_and_candidate_identity_mismatch(self):
        c=self.by_id['B3_034c4eac3fb53111'];s=self.build(c)
        with self.assertRaises(ValueError):validate_contact_geometry(replace(c,id='different'),s,self.f.obj,self.f.gripper)
        with self.assertRaises(ValueError):validate_contact_geometry(c,s,replace(self.f.obj,asset_sha256='wrong'),self.f.gripper)
        with self.assertRaises(ValueError):validate_contact_geometry(c,s,self.f.obj,replace(self.f.gripper,panda_sha256='wrong'))

    def test_deterministic_input_identity_and_mesh_cache(self):
        c=self.by_id['B3_034c4eac3fb53111'];a=self.evidence(c)
        prepare_mesh.cache_clear();b=self.evidence(c)
        for key in ('input_hash','classification','contacts','finger_groups'):
            self.assertEqual(a[key],b[key])
        dense=self.evidence(c,sample_density=5,sampling_domain='full_inward_projection')
        self.assertNotEqual(a['input_hash'],dense['input_hash'])

    def test_sampling_and_tolerance_changes_are_diagnostics_only(self):
        c=self.by_id['B3_034c4eac3fb53111'];s=self.build(c);before=s.to_dict()
        states=[]
        for density in (3,5,7):
            e=validate_contact_geometry(c,s,self.f.obj,self.f.gripper,palm=self.palm,sample_density=density)
            states.append(e.classification.value)
        self.assertEqual(states,['geometry_conflict']*3)
        for tolerance in (1e-7,1e-6,1e-5):
            e=validate_contact_geometry(c,s,self.f.obj,self.f.gripper,palm=self.palm,
                                        tolerances=GeometryTolerances(displacement_m=tolerance))
            self.assertEqual(e.classification.value,'geometry_conflict')
        self.assertEqual(before,s.to_dict())


from grasp_b.models import rotation

if __name__=='__main__':unittest.main()
