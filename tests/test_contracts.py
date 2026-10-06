import json
from pathlib import Path
import tempfile
import unittest

from grasp_a.contracts import Pose, load_candidates, validate_task_id
from grasp_a.collision_rules import fixed_link_groups

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_example_has_unique_ids_and_unit_quaternions(self):
        candidates = load_candidates(ROOT / "candidates.example.json")
        self.assertEqual(len(candidates), 2)
        self.assertEqual({x.id for x in candidates}, {"G01", "G02"})
        for c in candidates:
            self.assertEqual(set(c.public_metadata()),
                             {"id", "region", "approach", "gripper_width", "geometry_score"})

    def test_duplicate_ids_are_rejected(self):
        raw = json.loads((ROOT / "candidates.example.json").read_text())
        raw[1]["id"] = raw[0]["id"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_candidates(path)

    def test_pose_rejects_bad_quaternion(self):
        with self.assertRaisesRegex(ValueError, "unit length"):
            Pose.from_dict({"position":[0,0,0], "quaternion":[0,0,0,0]})

    def test_fixed_hand_chain_is_one_rigid_assembly(self):
        # Panda joint/link indices: link7=6, link8=7, hand=8, fingers=9/10.
        parents = [-1,0,1,2,3,4,5,6,7,8,8,8]
        groups = fixed_link_groups(parents, {7,8,11})
        self.assertEqual(groups[6], groups[8])
        self.assertNotEqual(groups[5], groups[6])
        self.assertNotEqual(groups[8], groups[9])

    def test_task_contract_accepts_only_shared_task_ids(self):
        self.assertEqual(validate_task_id("pour_prep"), "pour_prep")
        with self.assertRaisesRegex(ValueError, "task must be one of"):
            validate_task_id("invented_task")


if __name__ == "__main__":
    unittest.main()
