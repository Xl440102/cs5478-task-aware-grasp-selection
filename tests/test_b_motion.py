"""Command-derived motion context validation tests."""
import math
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pybullet_data
from grasp_a.contracts import Candidate, Pose
from grasp_b.geometry import tool_orientation
from grasp_b.motion import MotionSample, TaskContext, command_context

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class MotionTests(unittest.TestCase):
    def test_five_task_angles_times_and_sources(self):
        for task in ('transport_place','handover_transfer','pour_water','pour_prep','handover_prep'):
            ctx=command_context(task)
            self.assertTrue(all(s.source=='command_endpoint_half_cosine_estimate' for s in ctx.samples))
            self.assertEqual(len(ctx.source_hashes),3)
            self.assertTrue(any(s.stage=='lift' for s in ctx.samples))
        pour=command_context('pour_water')
        self.assertAlmostEqual(min(s.rotation_rpy_rad[0] for s in pour.samples),math.radians(-110))
        self.assertEqual(pour.liquid_mass_kg,.1)
        self.assertAlmostEqual(max(s.rotation_rpy_rad[1] for s in command_context('pour_prep').samples),math.radians(35))
        self.assertAlmostEqual(min(s.rotation_rpy_rad[2] for s in command_context('handover_prep').samples),-math.pi/2)
        self.assertEqual(command_context('pour_prep').liquid_mass_kg,0)
        self.assertAlmostEqual(pour.stage_commands[1]['duration_s'],1.3)


    def test_time_scaling_acceleration_and_angular_speed(self):
        slow,fast=(command_context('pour_water',speed_scale=x) for x in (1,2))
        self.assertAlmostEqual(max(np.linalg.norm(s.acceleration_world_m_s2) for s in fast.samples),
                               4*max(np.linalg.norm(s.acceleration_world_m_s2) for s in slow.samples))
        self.assertAlmostEqual(max(np.linalg.norm(s.omega_world_rad_s) for s in fast.samples),
                               2*max(np.linalg.norm(s.omega_world_rad_s) for s in slow.samples))


    def test_source_guard_and_invalid_motion(self):
        with patch('grasp_b.motion.MIRROR_SOURCE_HASHES',{}),self.assertRaises(RuntimeError):
            command_context('transport_place')
        with self.assertRaises(ValueError): MotionSample('x',0,acceleration_world_m_s2=(float('nan'),0,0))
        with self.assertRaises(ValueError): TaskContext('transport_place',())
        with self.assertRaises(ValueError): command_context('pour_water',speed_scale=0)
