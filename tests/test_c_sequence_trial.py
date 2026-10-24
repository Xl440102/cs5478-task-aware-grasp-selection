"""Sequence wrapper contracts without costly fluid episodes or paid calls."""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from grasp_a.contracts import load_candidates
from grasp_a.control import ObservationConfig
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from grasp_c import sequence_trial as bridge

ROOT = Path(__file__).resolve().parents[1]


class SequenceTrialTests(unittest.TestCase):
    def setUp(self):
        self.candidates = load_candidates(ROOT / "candidates.example.json")
        self.config = SequenceConfig()
        self.observation = ObservationConfig(position_noise_m=.003, yaw_noise_deg=2, noise_seed=1000)
        self.identity = bridge._identity("transport_place", 0, self.candidates,
                                         self.config, self.observation)
        self.filtered = {**deepcopy(self.identity), "schema_version": bridge.SEQUENCE_FILTER_SCHEMA,
                         "status": "ok", "feasible_candidates": [self.candidates[1].public_metadata()],
                         "checks": [{"candidate_id": candidate.id, "feasible": candidate.id == "G02"}
                                    for candidate in self.candidates],
                         "initial_object_pose_estimate": {"position": [.55, 0, .645],
                                                          "quaternion": [0, 0, 0, 1]}}
        self.episode = {**deepcopy(self.identity), "schema_version": bridge.SEQUENCE_EPISODE_SCHEMA,
                        "selected_id": "G02", "status": "completed", "success": False,
                        "failure_reason": "terminal_outcome", "final_evidence": {"source_xy_error_m": .1},
                        "feasibility": {"candidate_id": "G02", "feasible": True},
                        "initial_object_pose_estimate": deepcopy(self.filtered["initial_object_pose_estimate"])}

    def evaluate(self, selector=None, **kwargs):
        options = {"sequence_config": self.config, "observation_config": self.observation,
                   "filtered": self.filtered}
        options.update(kwargs)
        return bridge.evaluate_sequence_trial("transport_place", 0, "M3", self.candidates,
                                               selector if selector is not None else lambda *args: "G02",
                                               **options)

    def test_completed_physical_failure_and_private_evidence_are_preserved(self):
        with patch.object(bridge, "run_sequence", return_value=self.episode) as run:
            record = self.evaluate()
        self.assertEqual(record["status"], "completed")
        self.assertFalse(record["episode"]["success"])
        self.assertIs(record["episode"], self.episode)
        self.assertEqual(record["schema_version"], "evaluation_sequence_trial_v1")
        self.assertEqual(record["track"], "sequence")
        self.assertEqual(record["sequence_config"], asdict(self.config))
        self.assertEqual(record["observation_config"], asdict(self.observation))
        self.assertIs(run.call_args.kwargs["config"], self.config)
        self.assertIs(run.call_args.kwargs["observation_config"], self.observation)
        json.dumps(record, allow_nan=False)

    def test_all_a_execution_statuses_remain_distinct_and_keep_raw_episode(self):
        for status in bridge.SEQUENCE_EPISODE_STATUSES:
            episode = deepcopy(self.episode)
            episode["status"] = status
            episode["feasibility"]["feasible"] = status != "rejected"
            with self.subTest(status=status), patch.object(bridge, "run_sequence", return_value=episode):
                record = self.evaluate()
                self.assertEqual(record["status"], status)
                self.assertIs(record["episode"], episode)
                self.assertIsNone(record["error"])

    def test_only_five_public_fields_reach_selector_and_mutation_is_isolated(self):
        before = deepcopy(self.filtered)
        captured = []

        def selector(task_id, candidates, seed):
            self.assertEqual(task_id, "transport_place")
            self.assertEqual(seed, 0)
            captured.extend(deepcopy(candidates))
            candidates[0]["region"] = "mutated"
            return {"selected_id": "G02", "ranking": ["G02"], "metadata": {"model_id": "test"}}

        with patch.object(bridge, "run_sequence", return_value=self.episode):
            record = self.evaluate(selector)
        self.assertEqual(set(captured[0]), {"id", "region", "approach", "gripper_width", "geometry_score"})
        self.assertEqual(self.filtered, before)
        self.assertEqual(record["selection"]["metadata"], {"model_id": "test"})
        self.assertEqual(record["filter"], before)

    def test_task_seed_version_candidate_config_and_spec_mismatches_fail_before_policy(self):
        for field, value in (("task_id", "pour_water"), ("seed", 1),
                             ("schema_version", "old_filter"), ("controller_version", "old"),
                             ("evaluation_version", "old"), ("scene_version", "old"),
                             ("candidate_sha256", "stale"), ("task_spec_sha256", "stale"),
                             ("task_spec", {}), ("observation_config", {})):
            altered = dict(deepcopy(self.filtered), **{field: value})
            selector = Mock()
            with self.subTest(field=field), patch.object(bridge, "run_sequence") as run:
                with self.assertRaisesRegex(ValueError, "filter"):
                    self.evaluate(selector, filtered=altered)
                selector.assert_not_called()
                run.assert_not_called()
        with self.assertRaisesRegex(ValueError, "filter"):
            self.evaluate(sequence_config=replace(self.config, target_xy=(.55, .18)))
        changed = [self.candidates[0], replace(self.candidates[1], geometry_score=.01)]
        with self.assertRaisesRegex(ValueError, "filter"):
            bridge.check_sequence_filter(self.filtered, "transport_place", 0, changed,
                                         sequence_config=self.config, observation_config=self.observation)

    def test_public_injected_fields_bad_checks_and_false_empty_status_are_rejected(self):
        alterations = []
        altered = deepcopy(self.filtered)
        altered["feasible_candidates"][0]["task_appropriate"] = True
        alterations.append(altered)
        altered = deepcopy(self.filtered)
        altered["checks"][1]["candidate_id"] = "G01"
        alterations.append(altered)
        altered = deepcopy(self.filtered)
        altered["checks"][1]["feasible"] = 1
        alterations.append(altered)
        alterations.append(dict(deepcopy(self.filtered), status="no_feasible_candidate"))
        for altered in alterations:
            with self.subTest(altered=altered), self.assertRaisesRegex(ValueError, "filter"):
                self.evaluate(filtered=altered)

    def test_empty_filter_calls_neither_selector_nor_execution(self):
        self.filtered.update(status="no_feasible_candidate", feasible_candidates=[])
        for check in self.filtered["checks"]:
            check["feasible"] = False
        selector = Mock()
        with patch.object(bridge, "run_sequence") as run:
            record = self.evaluate(selector)
        self.assertEqual(record["status"], "no_feasible_candidate")
        self.assertIsNone(record["selection"])
        self.assertIsNone(record["episode"])
        selector.assert_not_called()
        run.assert_not_called()

    def test_filter_exceptions_are_separate_and_do_not_call_selector(self):
        selector = Mock()
        with patch.object(bridge, "get_sequence_candidates", side_effect=RuntimeError("world unavailable")), \
                patch.object(bridge, "run_sequence") as run:
            record = self.evaluate(selector, filtered=None)
        self.assertEqual(record["status"], "filter_error")
        self.assertEqual(record["error"]["type"], "RuntimeError")
        selector.assert_not_called()
        run.assert_not_called()

    def test_fresh_filter_receives_exact_configs(self):
        with patch.object(bridge, "get_sequence_candidates", return_value=self.filtered) as filtered, \
                patch.object(bridge, "run_sequence", return_value=self.episode):
            self.assertEqual(self.evaluate(filtered=None)["status"], "completed")
        self.assertIs(filtered.call_args.kwargs["config"], self.config)
        self.assertIs(filtered.call_args.kwargs["observation_config"], self.observation)

    def test_selector_errors_invalid_id_partial_ranking_and_bad_metadata_are_distinct(self):
        with patch.object(bridge, "run_sequence") as run:
            record = self.evaluate(Mock(side_effect=RuntimeError("model unavailable")))
            self.assertEqual(record["status"], "selector_error")
            for output in ("G99", {"selected_id": "G02", "ranking": []},
                           {"selected_id": "G02", "metadata": {"bad": float("nan")}},
                           {"selected_id": "G02", "metadata": "invalid"}):
                with self.subTest(output=output):
                    record = self.evaluate(lambda *args: output)
                    self.assertEqual(record["status"], "invalid_selection")
                    self.assertIsNone(record["episode"])
            run.assert_not_called()

    def test_execution_exception_and_identity_status_errors_preserve_raw_result(self):
        with patch.object(bridge, "run_sequence", side_effect=RuntimeError("physics unavailable")):
            record = self.evaluate()
            self.assertEqual(record["status"], "execution_error")
            self.assertIsNone(record["episode"])
        for field, value in (("task_spec_sha256", "wrong"), ("status", "unknown"),
                             ("observation_config", {}), ("candidate_sha256", "wrong"),
                             ("success", "yes"), ("initial_object_pose_estimate", {})):
            episode = dict(deepcopy(self.episode), **{field: value})
            with self.subTest(field=field), patch.object(bridge, "run_sequence", return_value=episode):
                record = self.evaluate()
                self.assertEqual(record["status"], "execution_error")
                self.assertIs(record["episode"], episode)

    def test_media_paths_are_method_scoped_and_landmarks_are_display_metadata(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(bridge, "run_sequence", return_value=self.episode) as run:
            record = self.evaluate(media_dir=directory, show_landmarks=False)
        self.assertEqual(run.call_args.kwargs["media_dir"], Path(directory) / "M3" / "transport_place_seed0")
        self.assertFalse(run.call_args.kwargs["show_landmarks"])
        self.assertEqual(record["media_options"], {"enabled": True, "show_landmarks": False})

    def test_invalid_inputs_and_user_interrupt_propagate(self):
        for task, seed, method, candidates, extra in (
                ("pour_prep", 0, "M3", self.candidates, {}),
                ("transport_place", True, "M3", self.candidates, {}),
                ("transport_place", 0, "../M3", self.candidates, {}),
                ("transport_place", 0, "M3", [self.candidates[0]] * 2, {}),
                ("transport_place", 0, "M3", self.candidates, {"show_landmarks": 1}),
                ("transport_place", 0, "M3", self.candidates, {"sequence_config": {}})):
            with self.subTest(task=task, seed=seed, method=method, extra=extra), self.assertRaises(ValueError):
                bridge.evaluate_sequence_trial(task, seed, method, candidates, Mock(), **extra)
        with patch.object(bridge, "run_sequence", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.evaluate()


@unittest.skipIf(importlib.util.find_spec("pybullet") is None, "PyBullet is not installed")
class SequenceTrialIntegrationTests(unittest.TestCase):
    def test_real_transport_uses_sequence_track_and_preserves_physical_status(self):
        from grasp_a.sequence import spec_sha256
        candidates = load_candidates(ROOT / "candidates.example.json")
        record = bridge.evaluate_sequence_trial("transport_place", 0, "SMOKE", candidates,
                                                 lambda task, public, seed: public[0]["id"])
        self.assertEqual(record["task_spec_sha256"], spec_sha256(task_spec("transport_place")))
        self.assertEqual(record["filter"]["schema_version"], "sequence_filter_v1")
        self.assertEqual(record["status"], record["episode"]["status"])
        self.assertIn(record["status"], bridge.SEQUENCE_EPISODE_STATUSES)
        self.assertEqual(record["episode"]["schema_version"], "sequence_episode_v1")
        self.assertIsNone(record["error"])
        self.assertIn("stage_evaluation", record["episode"])
        self.assertIn("final_evidence", record["episode"])


if __name__ == "__main__":
    unittest.main()
