"""Object asset parsing and geometry-adaptive candidate generation tests."""
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pybullet_data
from grasp_a.contracts import Candidate, Pose
from grasp_b.adaptive import AdaptiveConfig, generate_adaptive
from grasp_b.models import ROOT, read_model, Shape
from grasp_b.geometry import tool_orientation, read_geometry

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class ModelTests(unittest.TestCase):
    def test_both_assets_and_default_inertial_origin(self):
        solid,hollow=(read_model(p,PANDA) for p in ('solid','hollow'))
        self.assertAlmostEqual(solid.geometry.height,.09)
        self.assertAlmostEqual(hollow.geometry.height,.09)
        self.assertEqual(hollow.geometry.com,(0,0,0))
        self.assertAlmostEqual(hollow.mass_kg,.28)
        self.assertEqual(len(solid.shapes),4)
        self.assertGreater(len(hollow.shapes),30)
        self.assertEqual(len([s for s in hollow.shapes if s.region=='handle']),3)
        self.assertNotEqual(solid.asset_sha256,hollow.asset_sha256)
        self.assertEqual(solid.geometry,read_geometry(ROOT/'assets/mug.urdf',PANDA))
        json.dumps(hollow.manifest(),allow_nan=False)


    def test_rotated_collision_bounds(self):
        s=Shape('box',(1,2,3),(0,0,math.pi/2),(2,4,6),'body')
        lo,hi=s.bounds()
        np.testing.assert_allclose(lo,(-1,1,0),atol=1e-12)
        np.testing.assert_allclose(hi,(3,3,6),atol=1e-12)
        lo,hi=Shape('cylinder',(0,0,0),(math.pi/2,0,0),(.1,.6),'body').bounds()
        np.testing.assert_allclose(hi,(.1,.3,.1),atol=1e-9)


    def test_invalid_mesh_and_nonphysical_mass_rejected(self):
        source=(ROOT/'assets/mug.urdf').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.urdf'
            for text in (source.replace('mass value="0.28"','mass value="-1"'),
                         source.replace('<cylinder radius="0.035" length="0.09"/>','<mesh filename="x"/>')):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_model('solid',PANDA,asset_path=path)


    def test_adaptive_size_scaling_identity_and_budget(self):
        m=read_model('solid',PANDA)
        config=AdaptiveConfig()
        a=generate_adaptive(m,config)
        self.assertEqual(len(a),80)
        self.assertEqual(len({c.id for c in a}),80)
        self.assertEqual(a,generate_adaptive(m,replace(config,height_fractions=tuple(reversed(config.height_fractions)))))
        self.assertAlmostEqual(np.linalg.norm(np.subtract(a[0].pregrasp_pose.position,a[0].grasp_pose.position)),.1225)
        scaled=replace(m,geometry=replace(m.geometry,radius=.028,height=.12),
                       body_bounds=((-0.028,-0.028,-.06),(.028,.028,.06)))
        b=generate_adaptive(scaled)
        self.assertLess(b[0].gripper_width,a[0].gripper_width)
        self.assertLess(b[0].grasp_pose.position[2],a[0].grasp_pose.position[2])
        self.assertLess(np.linalg.norm(np.subtract(b[0].pregrasp_pose.position,b[0].grasp_pose.position)),
                        np.linalg.norm(np.subtract(a[0].pregrasp_pose.position,a[0].grasp_pose.position)))
        subset=generate_adaptive(m,replace(config,height_fractions=(.5,)))
        self.assertTrue({c.id for c in subset}<={c.id for c in a})
        with self.assertRaises(ValueError): generate_adaptive(m,replace(config,max_candidates=1))
        with self.assertRaises(ValueError): generate_adaptive(replace(m,geometry=replace(m.geometry,radius=.05)))
        with self.assertRaises(ValueError): AdaptiveConfig(yaw_step_deg=17)
