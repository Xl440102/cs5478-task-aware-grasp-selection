"""Contract regressions for C's integration example; no physical episodes here."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from grasp_a.contracts import load_candidates

try:
    from examples import evaluate_one_trial as bridge
except ImportError:
    bridge = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(bridge is None, "Simulation dependencies not installed")
class EvaluationExampleTests(unittest.TestCase):
    def setUp(self):
        self.candidates = load_candidates(ROOT / "candidates.example.json")
        self.filtered = {
            "controller_version": bridge.CONTROLLER_VERSION, "seed": 0, "status": "ok",
            "feasible_candidates": [self.candidates[1].public_metadata()],
            "checks": [{"candidate_id": "G01", "feasible": False},
                       {"candidate_id": "G02", "feasible": True}],
        }
        self.episode = {
            "controller_version": bridge.CONTROLLER_VERSION,
            "seed": 0, "task_id": "pour_prep", "selected_id": "G02",
            "feasibility": {"feasible": True},
            "success": False, "failure_reason": "lost_grasp",
        }

    def evaluate(self, selector):
        return bridge.evaluate_trial("pour_prep", 0, "M3", self.candidates,
                                     selector, filtered=self.filtered)

    def test_physical_failure_is_completed_trial_and_cannot_mutate_filter(self):
        before = deepcopy(self.filtered)

        def selector(task, public, seed):
            public[0]["region"] = "mutated"
            return {"selected_id": "G02", "ranking": ["G02"],
                    "metadata": {"fallback_to_m1": True}}

        with patch.object(bridge, "run_episode", return_value=self.episode):
            record = self.evaluate(selector)
        self.assertEqual(record["status"], "completed")
        self.assertFalse(record["episode"]["success"])
        self.assertEqual(self.filtered, before)
        self.assertTrue(record["selection"]["metadata"]["fallback_to_m1"])
        json.dumps(record, allow_nan=False)

    def test_no_feasible_candidate_calls_neither_selector_nor_executor(self):
        self.filtered["status"] = "no_feasible_candidate"
        self.filtered["feasible_candidates"] = []
        for check in self.filtered["checks"]:
            check["feasible"] = False
        selector = Mock()
        with patch.object(bridge, "run_episode") as run:
            record = self.evaluate(selector)
        self.assertEqual(record["status"], "no_feasible_candidate")
        self.assertIsNone(record["episode"])
        selector.assert_not_called()
        run.assert_not_called()

    def test_invalid_id_or_incomplete_ranking_is_not_executed(self):
        for output in ("G99", {"selected_id": "G02", "ranking": []}):
            with self.subTest(output=output), patch.object(bridge, "run_episode") as run:
                record = self.evaluate(lambda *args: output)
                self.assertEqual(record["status"], "invalid_selection")
                self.assertIsNone(record["episode"])
                run.assert_not_called()

    def test_selector_exception_and_execution_exception_have_distinct_status(self):
        record = self.evaluate(Mock(side_effect=RuntimeError("selector unavailable")))
        self.assertEqual(record["status"], "selector_error")
        with patch.object(bridge, "run_episode", side_effect=RuntimeError("simulator unavailable")):
            record = self.evaluate(lambda *args: "G02")
        self.assertEqual(record["status"], "execution_error")
        self.assertIsNone(record["episode"])

    def test_mismatched_filter_rejected_before_selection(self):
        self.filtered["seed"] = 99
        selector = Mock()
        with self.assertRaisesRegex(ValueError, "seed/controller"):
            self.evaluate(selector)
        selector.assert_not_called()

    def test_fingerprint_includes_poses_and_candidate_order(self):
        from dataclasses import replace
        c = self.candidates[1]
        changed = replace(c, pregrasp_pose=c.grasp_pose)
        self.assertNotEqual(bridge.candidate_sha256(self.candidates),
                            bridge.candidate_sha256([self.candidates[0], changed]))
        self.assertNotEqual(bridge.candidate_sha256(self.candidates),
                            bridge.candidate_sha256(list(reversed(self.candidates))))


if __name__ == "__main__":
    unittest.main()
