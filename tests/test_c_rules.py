import json
from pathlib import Path
import unittest

from grasp_c.rules import METHOD_ID, rank

ROOT = Path(__file__).resolve().parents[1]
R3 = ROOT / "results/oracle_20261103/r3_v3p_wide_strict_v2"


def cand(cid, region, approach, score=0.0):
    return dict(id=cid, region=region, approach=approach, gripper_width=0.03, geometry_score=score)


SIDE = cand("side", "body", "part=body high; from=side, horizontal; side=beside the handle; "
            "closing=along handle axis (a finger may land on the handle); mouth=clear; handle=left free", 0.75)
TOP = cand("top", "body", "part=body top; from=above; side=from above; closing=across handle axis; "
           "mouth=hand over the opening (blocks pouring); handle=left free", 0.70)
RIM = cand("rim", "rim", "part=rim (one finger inside the cup); from=above; side=from above; "
           "closing=across handle axis; mouth=hand over the opening (blocks pouring); handle=left free")
HANDLE = cand("handle", "handle", "part=handle outer bar; from=side, horizontal; side=from handle side; "
              "closing=across handle axis; mouth=clear; handle=occupied by this grasp")


class PromptRuleTests(unittest.TestCase):
    def test_priorities_follow_v2_prompt(self):
        pool = [SIDE, TOP, RIM, HANDLE]
        self.assertEqual(rank("transport_place", pool)[:3], ["handle", "rim", "top"])
        self.assertEqual(rank("handover_transfer", pool)[:2], ["rim", "top"])
        self.assertEqual(rank("pour_water", pool)[:3], ["top", "side", "rim"])
        self.assertEqual(METHOD_ID, "M6_PROMPT_RULES")

    def test_unmatched_and_plain_labels_fall_back_to_score_then_id(self):
        plain = [cand("b", "body", "side_mid", 0.6), cand("a", "body", "side_mid", 0.6),
                 cand("c", "body", "top", 0.7)]
        self.assertEqual(rank("handover_transfer", plain), ["c", "a", "b"])
        with self.assertRaises(ValueError):
            rank("unknown_task", plain)

    @unittest.skipUnless(R3.exists(), "archived round-3 table not present")
    def test_matches_archived_round3_heldout_successes(self):
        expected = {"transport_place": 18, "handover_transfer": 20, "pour_water": 18}
        for task, want in expected.items():
            got = 0
            for seed in range(10, 30):
                public = json.loads((R3 / f"filters/{task}/seed{seed}.json").read_text())["feasible_candidates"]
                pick = rank(task, public)[0]
                got += json.loads((R3 / f"episodes/{task}/seed{seed}/{pick}.json").read_text())["success"]
            self.assertEqual(got, want, task)


if __name__ == "__main__":
    unittest.main()
