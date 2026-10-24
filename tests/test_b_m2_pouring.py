"""M2 pouring torque, rim clearance and liquid proxy tests."""
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
from grasp_b.risk import static_score, task_risk, load_sample

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class PouringRiskTests(unittest.TestCase):
    def setUp(self):
        self.m=read_model('hollow',PANDA)
        self.c=candidate()


    def test_liquid_mass_and_tilt_change_torque(self):
        ctx=command_context('pour_water')
        upper=candidate(.02)
        dry=task_risk(upper,self.m,replace(ctx,liquid_mass_kg=0))
        wet=task_risk(upper,self.m,ctx)
        self.assertGreater(wet['load']['max_load_ratio'],dry['load']['max_load_ratio'])
        tilted=MotionSample('tilt',0,rotation_rpy_rad=(math.pi/2,0,0))
        upright=MotionSample('upright',0)
        self.assertGreater(load_sample(upper,self.m,tilted,ctx)['torque_nm'],
                           load_sample(upper,self.m,upright,ctx)['torque_nm'])


    def test_pouring_prefers_rim_clearance_and_changes_static_order(self):
        center,lower=candidate(0,cid='center'),candidate(-.02,cid='lower')
        ctx=command_context('pour_water')
        # Lower COM of a fixed liquid distribution is an explicit sensitivity
        # scenario, not a measurement or a parameter fitted to IDs.
        ctx=replace(ctx,liquid_mass_kg=.2,liquid_com_m=(0,0,-.025))
        self.assertGreater(static_score(center,self.m)['score'],static_score(lower,self.m)['score'])
        self.assertLess(task_risk(lower,self.m,ctx)['rim_clearance_penalty'],task_risk(center,self.m,ctx)['rim_clearance_penalty'])
        self.assertGreater(task_risk(lower,self.m,ctx)['score'],task_risk(center,self.m,ctx)['score'])

    def test_stability_component_includes_edge_margin_and_risk_decomposition(self):
        result=task_risk(candidate(.0259),self.m,command_context('pour_water'))
        expected_stability=result['load']['max_load_ratio']+result['load']['contact_edge_penalty']
        self.assertAlmostEqual(result['grasp_stability_risk'],expected_stability)
        self.assertAlmostEqual(result['risk'],expected_stability+result['rim_obstruction_fraction']+
                               result['handle_obstruction_fraction']+result['rim_clearance_penalty'])
