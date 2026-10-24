"""M2 transport load and acceleration sensitivity tests."""
from dataclasses import replace
import math
import unittest
import numpy as np
import pybullet_data
from pathlib import Path
from grasp_a.contracts import Candidate, Pose
from grasp_b.models import read_model
from grasp_b.geometry import tool_orientation
from grasp_b.motion import MotionSample, TaskContext, command_context
from grasp_b.risk import Physics, task_risk, load_sample

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class TransportRiskTests(unittest.TestCase):
    def setUp(self):
        self.m=read_model('hollow',PANDA)
        self.c=candidate()


    def test_velocity_alone_does_not_create_translational_inertial_force(self):
        zero=MotionSample('constant_speed',0,speed_m_s=0.)
        fast=MotionSample('constant_speed',0,speed_m_s=10.)
        ctx=TaskContext('transport_place',(zero,))
        a=load_sample(self.c,self.m,zero,ctx)
        b=load_sample(self.c,self.m,fast,ctx)
        self.assertEqual(a['force_n'],b['force_n'])
        self.assertEqual(a['torque_nm'],b['torque_nm'])


    def test_sensitivity_mass_friction_com_and_motion_stage(self):
        ctx=command_context('transport_place')
        baseline=task_risk(self.c,self.m,ctx)
        heavier=task_risk(self.c,replace(self.m,mass_kg=.56),ctx)
        less_friction=task_risk(self.c,self.m,ctx,Physics(friction=.3))
        fast=task_risk(self.c,self.m,command_context('transport_place',speed_scale=3))
        self.assertGreater(heavier['max_load_ratio'],baseline['max_load_ratio'])
        self.assertLess(heavier['samples'][0]['normalized_force_margin'],
                        baseline['samples'][0]['normalized_force_margin'])
        self.assertGreater(less_friction['max_load_ratio'],baseline['max_load_ratio'])
        self.assertGreater(fast['max_load_ratio'],baseline['max_load_ratio'])
        offset=replace(self.m,geometry=replace(self.m.geometry,com=(.02,0,0)))
        self.assertGreater(task_risk(self.c,offset,ctx)['risk'],baseline['risk'])
        samples=(MotionSample('gentle',0),MotionSample('shock',1,acceleration_world_m_s2=(0,0,30)))
        self.assertEqual(task_risk(self.c,self.m,TaskContext('transport_place',samples))['worst_stage'],'shock')

    def test_nonfinite_demand_from_extreme_model_is_rejected(self):
        extreme=replace(self.m,mass_kg=1e308)
        with np.errstate(over='ignore',invalid='ignore'), self.assertRaisesRegex(ValueError,'nonfinite'):
            load_sample(self.c,extreme,MotionSample('extreme',0),
                        TaskContext('transport_place',(MotionSample('extreme',0),)))
