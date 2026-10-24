"""M1 Static v2 physical proxy tests."""
from dataclasses import replace
import math
import unittest
import numpy as np
import pybullet_data
from pathlib import Path
from grasp_a.contracts import Candidate, Pose
from grasp_b.models import read_model
from grasp_b.geometry import tool_orientation
from grasp_b.motion import MotionSample, TaskContext
from grasp_b.risk import Physics, features, load_sample, static_score

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class StaticV2Tests(unittest.TestCase):
    def setUp(self):
        self.m=read_model('hollow',PANDA)
        self.c=candidate()


    def test_analytic_coulomb_and_static_moment(self):
        sample=MotionSample('hold',0)
        ctx=TaskContext('transport_place',(sample,))
        row=load_sample(self.c,self.m,sample,ctx)
        self.assertAlmostEqual(row['force_n'],.28*9.81)
        self.assertAlmostEqual(row['torque_nm'],0)
        self.assertAlmostEqual(row['normal_force_required_per_finger_n'],.28*9.81/(2*.6))
        shifted=replace(self.m,geometry=replace(self.m.geometry,com=(.01,0,0)))
        row=load_sample(self.c,shifted,sample,ctx)
        self.assertAlmostEqual(row['torque_nm'],.28*9.81*.01)
