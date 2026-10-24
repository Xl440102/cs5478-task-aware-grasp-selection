"""M2 handover receiver-space and handle-access tests."""
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
from grasp_b.risk import static_score, task_risk, rank_feasible

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class HandoverRiskTests(unittest.TestCase):
    def setUp(self):
        self.m=read_model('hollow',PANDA)
        self.c=candidate()


    def test_handover_geometry_distinguishes_equal_com_and_renaming(self):
        clear,blocked=candidate(yaw=0,cid='z'),candidate(yaw=270,cid='a')
        ctx=command_context('handover_transfer')
        self.assertAlmostEqual(static_score(clear,self.m)['score'],static_score(blocked,self.m)['score'])
        self.assertLess(task_risk(clear,self.m,ctx)['risk'],task_risk(blocked,self.m,ctx)['risk'])
        self.assertEqual(task_risk(clear,self.m,ctx)['risk'],task_risk(replace(clear,id='a'),self.m,ctx)['risk'])
        self.assertGreater(task_risk(blocked,self.m,ctx)['withdrawal_toward_receiver'],.9)


    def test_handover_pose_changes_receiver_accessibility(self):
        upright=TaskContext('handover_transfer',(MotionSample('receiver_close',0),))
        turned=replace(upright,samples=(MotionSample('receiver_close',0,rotation_rpy_rad=(0,0,math.pi)),))
        a=task_risk(self.c,self.m,upright)
        b=task_risk(self.c,self.m,turned)
        self.assertGreater(a['geometry'][0]['handle_accessible_fraction'],b['geometry'][0]['handle_accessible_fraction'])

    def test_stability_component_includes_static_contact_edge_margin(self):
        result=task_risk(candidate(.0259),self.m,command_context('handover_transfer'))
        expected=result['load']['max_load_ratio']+result['load']['contact_edge_penalty']
        self.assertAlmostEqual(result['grasp_stability_risk'],expected)
        self.assertAlmostEqual(result['risk'],max(row['geometry_risk'] for row in result['geometry'])+expected)
        self.assertGreater(result['load']['contact_edge_penalty'],0)

    def test_receiver_geometry_breaks_equal_static_score_in_task_ranking(self):
        clear,blocked=candidate(yaw=0,cid='z_clear'),candidate(yaw=270,cid='a_blocked')
        candidates=[clear,blocked]
        public=[c.public_metadata() for c in candidates]
        static=rank_feasible(candidates,public,self.m)
        aware=rank_feasible(candidates,public,self.m,context=command_context('handover_transfer'))
        self.assertEqual(static['ranking'],['a_blocked','z_clear'])
        self.assertEqual(aware['ranking'],['z_clear','a_blocked'])
