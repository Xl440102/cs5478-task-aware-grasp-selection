"""Shared task-risk API and determinism contracts."""
from dataclasses import replace
import json
import math
import unittest
import numpy as np
import pybullet_data
from pathlib import Path
from grasp_a.contracts import Candidate, Pose
from grasp_b.models import read_model
from grasp_b.geometry import tool_orientation
from grasp_b.motion import MotionSample, TaskContext, command_context
from grasp_b.risk import Physics, features, task_risk, rank_feasible

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class RiskApiContractTests(unittest.TestCase):
    def setUp(self):
        self.m=read_model('hollow',PANDA)
        self.c=candidate()


    def test_explicit_external_samples_and_diagnostics_json(self):
        ctx=TaskContext('transport_place',(MotionSample('measured',0,source='external_observed'),))
        result=task_risk(self.c,self.m,ctx)
        self.assertEqual(result['samples'][0]['source'],'external_observed')
        json.dumps(result,allow_nan=False)
        with self.assertRaises(ValueError): Physics(friction=0)
        with self.assertRaises(ValueError): features(candidate(.044),self.m)


    def test_symmetric_roll_roundoff_is_a_tie_not_task_evidence(self):
        a=candidate(.0111,cid='a')
        b=replace(a,id='b',grasp_pose=Pose(a.grasp_pose.position,tool_orientation(0,180)),
                  pregrasp_pose=Pose(a.pregrasp_pose.position,tool_orientation(0,180)))
        ctx=command_context('pour_water')
        ranked=rank_feasible([b,a],[b.public_metadata(),a.public_metadata()],self.m,context=ctx)
        self.assertEqual(ranked['ranking'],['a','b'])
        self.assertAlmostEqual(ranked['scores'][0]['score'],ranked['scores'][1]['score'],places=12)


    def test_exact_same_feasible_set_and_metadata_integrity(self):
        candidates=[candidate(cid='one'),candidate(.01,cid='two')]
        public=[candidates[1].public_metadata()]
        for ctx in (None,command_context('pour_water')):
            result=rank_feasible(candidates,public,self.m,context=ctx)
            self.assertEqual(result['ranking'],['two'])
        self.assertEqual(rank_feasible(candidates,[],self.m)['ranking'],[])
        with self.assertRaises(ValueError): rank_feasible(candidates,[dict(public[0],geometry_score=.3)],self.m)
        with self.assertRaises(ValueError): rank_feasible(candidates,[dict(public[0],hidden=True)],self.m)
        unsupported=replace(candidates[1],grasp_pose=Pose((0,0,.01),tool_orientation(0,90)))
        with self.assertRaises(ValueError): rank_feasible([unsupported],[unsupported.public_metadata()],self.m)
