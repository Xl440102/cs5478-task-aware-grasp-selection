"""Generator invariants, independently checked against Panda rotations."""
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest

import pybullet as p
import pybullet_data

from grasp_b.generation import DEFAULT_CONFIG, generate_candidates, write_candidates
from grasp_b.geometry import read_geometry, tool_orientation

ROOT = Path(__file__).resolve().parents[1]


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DEFAULT_CONFIG.read_text())
        self.geometry = read_geometry(ROOT / 'assets/mug.urdf',
            Path(pybullet_data.getDataPath()) / 'franka_panda/panda.urdf')

    def test_roundtrip_determinism_and_stable_ids(self):
        candidates, records = generate_candidates(self.config, self.geometry)
        self.assertEqual(len(candidates), 12)
        self.assertEqual(len({c.id for c in candidates}), 12)
        self.assertEqual(generate_candidates(self.config, self.geometry), (candidates, records))
        reordered = dict(self.config, yaw_deg=list(reversed(self.config['yaw_deg'])))
        self.assertEqual(generate_candidates(reordered, self.geometry)[0], candidates)
        subset = dict(self.config, yaw_deg=[0])
        self.assertTrue(set(c.id for c in generate_candidates(subset, self.geometry)[0])
                        <= {c.id for c in candidates})
        with tempfile.TemporaryDirectory() as directory:
            loaded = write_candidates(Path(directory) / 'candidates.json', candidates)
            for a, b in zip(candidates, loaded):
                self.assertEqual(a.id, b.id)
                self.assertEqual(a.grasp_pose.position, b.grasp_pose.position)
                for x, y in zip(a.grasp_pose.quaternion, b.grasp_pose.quaternion):
                    self.assertAlmostEqual(x, y)

    def test_local_positions_quaternions_and_approach(self):
        candidates, records = generate_candidates(self.config, self.geometry)
        for c, r in zip(candidates, records):
            self.assertEqual(c.grasp_pose.position, (0, 0, r['height_m']))
            self.assertLess(abs(r['height_m']), self.geometry.height / 2)
            self.assertAlmostEqual(sum(x*x for x in c.grasp_pose.quaternion), 1)
            self.assertEqual(c.pregrasp_pose.quaternion, c.grasp_pose.quaternion)
            matrix = p.getMatrixFromQuaternion(c.grasp_pose.quaternion)
            tool_z = (matrix[2], matrix[5], matrix[8])
            for i in range(3):
                self.assertAlmostEqual(tool_z[i], r['approach_axis_mug'][i])
                self.assertAlmostEqual(c.grasp_pose.position[i]-c.pregrasp_pose.position[i],
                                       r['pregrasp_offset_m']*tool_z[i])
            self.assertAlmostEqual(c.gripper_width, 0.078)
            self.assertEqual(c.geometry_score, 0)

    def test_orientation_matches_independent_bullet_composition(self):
        for yaw in (0, 45, 315):
            for roll in (0, 90, 180, 270):
                base = p.getQuaternionFromEuler((0, math.pi/2, math.radians(yaw)))
                twist = p.getQuaternionFromEuler((0, 0, math.radians(roll)))
                expected = p.multiplyTransforms((0,0,0), base, (0,0,0), twist)[1]
                actual = tool_orientation(yaw, roll)
                self.assertAlmostEqual(abs(sum(a*b for a,b in zip(actual,expected))), 1, places=6)

    def test_invalid_parameters_rejected(self):
        for change in ({'yaw_deg':[0,360]}, {'height_m':[0.045]},
                       {'roll_deg':[float('nan')]}, {'pregrasp_offset_m':[0]},
                       {'opening_margin_m':0.02}, {'yaw_deg':[]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                generate_candidates(dict(self.config, **change), self.geometry)

    def test_geometry_controls_aperture_and_limits(self):
        smaller = replace(self.geometry, radius=0.03)
        candidates, _ = generate_candidates(self.config, smaller)
        self.assertAlmostEqual(candidates[0].gripper_width, 0.068)
        with self.assertRaises(ValueError):
            generate_candidates(self.config, replace(self.geometry, max_opening=0.07))


if __name__ == '__main__':
    unittest.main()
