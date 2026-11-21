from dataclasses import FrozenInstanceError
import json
import unittest

from grasp_b.contact_refinement.contact_regions import (
    GeometryEvidence,GeometryTolerances,Simultaneity,displacement_evidence,
)


class ScalarSimultaneityTests(unittest.TestCase):
    def test_same_plane_two_points_share_configuration(self):
        r=displacement_evidence([.03,.03],.04,tolerance_m=1e-6)
        self.assertEqual(r['classification'],Simultaneity.COMPATIBLE.value)
        self.assertAlmostEqual(r['first_contact_displacement_m'],.01)
        self.assertEqual(r['closing_gaps_m'],[0.,0.])
        self.assertIsNone(r['position_uncertainty_m'])

    def test_sequential_and_conflicting_coordinates(self):
        self.assertEqual(displacement_evidence([.03,.025],.04,tolerance_m=1e-6)['classification'],'sequential_contact')
        for q in ([.041,.03],[-.001,.03]):
            self.assertEqual(displacement_evidence(q,.04,tolerance_m=1e-6)['classification'],'geometry_conflict')

    def test_tolerance_boundary_is_not_physical_improvement(self):
        values=[.03,.0300006]
        self.assertEqual(displacement_evidence(values,.04,tolerance_m=1e-7)['classification'],'sequential_contact')
        self.assertEqual(displacement_evidence(values,.04,tolerance_m=1e-6)['classification'],'insufficient_evidence')
        self.assertEqual(displacement_evidence(values,.04,tolerance_m=1e-5)['classification'],'geometrically_compatible')

    def test_invalid_units_or_nonfinite(self):
        for kwargs in (dict(surface_m=-1),dict(displacement_m=float('nan')),dict(tangent_cosine=2)):
            with self.assertRaises(ValueError):GeometryTolerances(**kwargs)
        with self.assertRaises(ValueError):displacement_evidence([float('inf')],.04,tolerance_m=1e-6)


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_b_wrench_adapter import StaticAdapterTests,PANDA
        from grasp_b.contact_refinement.geometry_validation import validate_contact_geometry,load_palm_mesh
        StaticAdapterTests.setUpClass();f=StaticAdapterTests();c=f.candidate('side')
        cls.record=validate_contact_geometry(c,f.build(c),f.obj,f.gripper,palm=load_palm_mesh(PANDA))

    def test_strict_roundtrip_and_immutability(self):
        r=self.record
        self.assertEqual(r.to_json(),GeometryEvidence.from_json(r.to_json()).to_json())
        with self.assertRaises(TypeError):r.payload['contacts'][0]['normal_outward'][0]=123
        with self.assertRaises(FrozenInstanceError):r.payload={}
        changed=r.to_dict();changed['contacts'][0]['normal_outward']=[1,1,1]
        with self.assertRaises(ValueError):GeometryEvidence(changed)

    def test_wrong_identity_checksum_frame_or_duplicate_json_rejected(self):
        r=self.record
        r.assert_identity(candidate_hash=r.payload['candidate_hash'],contact_spec_hash=r.payload['contact_spec_hash'])
        with self.assertRaises(ValueError):r.assert_identity(candidate_hash='f'*64)
        with self.assertRaises(ValueError):r.assert_identity(units='mm')
        changed=r.to_dict();changed['classification']='unsupported'
        with self.assertRaises(ValueError):GeometryEvidence(changed)
        changed=r.to_dict();changed['frame']='world';changed.pop('evidence_hash')
        with self.assertRaises(ValueError):GeometryEvidence(changed)
        with self.assertRaises(ValueError):GeometryEvidence.from_json('{"x":1,"x":2}')
        with self.assertRaises(ValueError):GeometryEvidence.from_json('{"x":NaN}')


if __name__=='__main__':unittest.main()
