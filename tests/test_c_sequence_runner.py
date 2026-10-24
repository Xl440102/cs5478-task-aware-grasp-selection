"""Sequence batching, persistence and model-cache boundaries; no physical/paid calls."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from grasp_a.contracts import candidate_sha256, load_candidates
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from grasp_c import sequence_runner as runner
from grasp_c import sequence_trial as bridge
from grasp_c.config import load_b_provenance, load_config, validate_config
from grasp_c.prepare_b import prepare_b_inputs
from grasp_c.providers import MockProvider, ModelResponse
from grasp_c.runner import _sha, selection_context, trial_plan
from grasp_c.selectors import LLMSelector
from grasp_c.tasks import load_sequence_config, sequence_specs

ROOT = Path(__file__).resolve().parents[1]


class SequenceConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.raw = {"track": "sequence", "candidates": str(ROOT / "candidates.example.json"),
                    "seeds": [11], "methods": [{"id": "MOCK", "kind": "llm", "model": "mock",
                                                "provider": {"type": "mock"}}]}

    def test_sequence_validation_is_offline_and_defaults_to_three_new_tasks(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(MockProvider, "preflight", side_effect=AssertionError("provider contacted")), \
                patch.object(bridge, "get_sequence_candidates", side_effect=AssertionError("filter called")), \
                patch.object(bridge, "run_sequence", side_effect=AssertionError("episode called")):
            config = validate_config(self.raw, base_dir=self.base)
        self.assertEqual(config["tasks"], ["transport_place", "handover_transfer", "pour_water"])
        self.assertEqual(config["sequence_config"], json.loads(json.dumps(asdict(SequenceConfig()))))
        self.assertEqual(config["policy_mode"], "fixed")

    def test_preparation_and_sequence_tasks_cannot_be_mixed_or_relabelled(self):
        for updates in ({"tasks": ["pour_prep"]},
                        {"tasks": ["transport_place", "handover_prep"]},
                        {"track": "prep", "tasks": ["pour_water"]},
                        {"track": "prep", "tasks": ["pour_prep"], "sequence_config": {}},
                        {"track": "unsupported"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_config(dict(self.raw, **updates), base_dir=self.base)

    def test_invalid_sequence_physics_config_is_rejected_before_any_runtime(self):
        for values in ([], {"unknown": 1}, {"target_xy": [.55]}, {"target_xy": [1, 0]},
                       {"carry_height_m": .1}, {"fluid_particles": 63}, {"fluid_particles": True},
                       {"fluid_iterations": 0}, {"fluid_substeps": 9}, {"spill_max_ratio": 1},
                       {"transfer_min_ratio": 0}, {"terminal_window_s": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_config(dict(self.raw, sequence_config=values), base_dir=self.base)

    def test_b_metadata_must_match_candidate_bytes_and_report_before_runtime(self):
        paths = prepare_b_inputs(self.base / "B")
        self.raw.update(candidates=paths["candidates"], b_provenance=paths["provenance"],
                        methods=[{"id": "B_GEOMETRY", "kind": "b_geometry"}])
        config = validate_config(self.raw, base_dir=self.base)
        origin = load_b_provenance(config["b_provenance"], config["candidates"])
        self.assertEqual(origin["candidate_sha256"], candidate_sha256(load_candidates(paths["candidates"])))
        candidate_file = Path(paths["candidates"])
        original = candidate_file.read_bytes()
        candidate_file.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "candidate file"):
            validate_config(self.raw, base_dir=self.base)
        candidate_file.write_bytes(original)
        report = Path(paths["report"])
        report.write_bytes(report.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "report fingerprint"):
            validate_config(self.raw, base_dir=self.base)


class SequenceRunnerTests(unittest.TestCase):
    """Use the real wrapper, selectors, model cache, records and metrics."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.path = self.base / "config.json"
        self.out = self.base / "output"
        self.raw = {"track": "sequence", "candidates": str(ROOT / "candidates.example.json"),
                    "cache_dir": str(self.base / "cache"), "seeds": [11, 12],
                    "tasks": ["transport_place", "handover_transfer"], "repeats": 2,
                    "observation_config": {"position_noise_m": .003, "yaw_noise_deg": 2, "noise_seed": 1000},
                    "methods": [{"id": "GEOMETRY", "kind": "geometry"},
                                {"id": "M3", "kind": "llm", "model": "mock", "variant": "M3",
                                 "provider": {"type": "mock"}},
                                {"id": "A1", "kind": "llm", "model": "mock", "variant": "A1",
                                 "provider": {"type": "mock"}}]}
        self.filter = Mock(side_effect=self.fake_filter)
        self.execution = Mock(side_effect=self.fake_episode)
        original_generate = MockProvider.generate
        self.model = Mock(side_effect=lambda provider, request: original_generate(provider, request))
        patches = [patch.object(runner, "get_sequence_candidates", self.filter),
                   patch.object(bridge, "run_sequence", self.execution),
                   patch.object(MockProvider, "generate", autospec=True, side_effect=self.model),
                   patch.object(runner, "build_provenance", side_effect=self.fake_provenance)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def fake_provenance(self, config, providers, *, media, out_dir):
        candidates = load_candidates(config["candidates"])
        return {"schema_version": "test", "configuration": deepcopy(config),
                "configuration_sha256": _sha(config), "plan": trial_plan(config),
                "evaluation_scope": "pilot", "policy_mode": "fixed",
                "observation_config": config["observation_config"],
                "candidate_sha256": candidate_sha256(candidates),
                "candidate_file_sha256": hashlib.sha256(Path(config["candidates"]).read_bytes()).hexdigest(),
                "baseline_provenance": (load_b_provenance(config["b_provenance"], config["candidates"])
                                        if config.get("b_provenance") else None),
                "acceptable_sets_canonical_sha256": None, "source_sha256": "frozen-test-source",
                "providers": {key: value.identity for key, value in providers.items()},
                "seed_split": config["seed_split"]}

    def fake_filter(self, task, seed, candidates, *, config, observation_config):
        identity = bridge._identity(task, seed, candidates, config, observation_config)
        return {**identity, "schema_version": bridge.SEQUENCE_FILTER_SCHEMA, "status": "ok",
                "checks": [{"candidate_id": candidate.id, "feasible": True} for candidate in candidates],
                "feasible_candidates": [candidate.public_metadata() for candidate in candidates],
                "initial_object_pose_estimate": asdict(observation_config.object_pose)}

    def fake_episode(self, task, seed, selected, candidates, *, config, observation_config,
                     gui=False, media_dir=None, show_landmarks=True):
        return {**bridge._identity(task, seed, candidates, config, observation_config),
                "schema_version": bridge.SEQUENCE_EPISODE_SCHEMA, "status": "completed", "success": True,
                "selected_id": selected, "failure_reason": None, "collision": False, "dropped": False,
                "feasibility": {"candidate_id": selected, "feasible": True},
                "initial_object_pose_estimate": asdict(observation_config.object_pose),
                "success_checks": {"fluid_density_valid": True, "sequence_complete": True},
                "terminal_checks": {"stable": True}, "terminal_samples": 240, "elapsed_s": 12,
                "final_evidence": {"source_xy_error_m": .002, "source_z_m": .645,
                                   "source_upright_deg": .1, "donor_contact": False,
                                   "receiver_contact": task == "handover_transfer", "table_contact": True},
                "stages": [{"id": "terminal", "control_status": "completed"}],
                "stage_evaluation": {"terminal": {"physical_pass": True, "physical_checks": {"stable": True}}}}

    def run_config(self, **kwargs):
        self.path.write_text(json.dumps(self.raw), encoding="utf-8")
        return runner.run_sequence_evaluation(load_config(self.path), self.out, **kwargs)

    def test_filter_shared_by_task_seed_multiple_methods_and_finished_resume_calls_nothing(self):
        summary = self.run_config()
        self.assertEqual(self.filter.call_count, 4)
        self.assertEqual(self.execution.call_count, 24)
        self.assertEqual(self.model.call_count, 16)
        self.assertEqual(summary["saved_n"], 24)
        self.assertEqual({call.args[:2] for call in self.filter.call_args_list},
                         {(task, seed) for task in self.raw["tasks"] for seed in self.raw["seeds"]})
        self.assertEqual(len(list((self.out / "filters").rglob("*.json"))), 4)
        self.filter.reset_mock()
        self.execution.reset_mock()
        self.model.reset_mock()
        with patch.object(MockProvider, "preflight", side_effect=AssertionError("preflight repeated")):
            self.assertEqual(self.run_config(resume=True), summary)
        self.filter.assert_not_called()
        self.execution.assert_not_called()
        self.model.assert_not_called()
        self.assertEqual(runner.summarize_sequence_run(self.out), summary)

    def test_partial_resume_reuses_shared_filter_and_model_response(self):
        self.run_config()
        path = self.out / "trials" / "M3" / "transport_place" / "seed11_repeat0.json"
        path.unlink()
        self.filter.reset_mock()
        self.execution.reset_mock()
        self.model.reset_mock()
        summary = self.run_config(resume=True)
        self.assertEqual(summary["saved_n"], 24)
        self.filter.assert_not_called()
        self.assertEqual(self.execution.call_count, 1)
        self.model.assert_not_called()
        record = json.loads(path.read_text())
        self.assertTrue(record["selection"]["metadata"]["cache_hit"])

    def test_resume_rejects_changed_task_limits_noise_media_and_landmarks(self):
        self.run_config()
        before = deepcopy(self.raw)
        for field, value in (("sequence_config", {"terminal_window_s": 2}),
                             ("observation_config", {"noise_seed": 42}), ("tasks", ["transport_place"])):
            self.raw = dict(deepcopy(before), **{field: value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "resume refused"):
                self.run_config(resume=True)
        self.raw = before
        for options in ({"media": True}, {"show_landmarks": False}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "resume refused"):
                self.run_config(resume=True, **options)

    def test_completed_resume_and_summary_reject_filter_identity_or_spec_tampering(self):
        self.run_config()
        path = self.out / "filters" / "transport_place" / "seed11.json"
        original = json.loads(path.read_text())
        for nested, field, value in ((False, "schema_version", "old"), (False, "task_id", "pour_water"),
                                    (False, "scene_version", "old"), (False, "task_spec_sha256", "stale"),
                                    (True, "schema_version", "old"), (True, "task_id", "pour_water"),
                                    (True, "controller_version", "old"), (True, "evaluation_version", "old"),
                                    (True, "candidate_sha256", "stale"), (True, "task_spec", {}),
                                    (True, "observation_config", {})):
            altered = deepcopy(original)
            (altered["filter"] if nested else altered)[field] = value
            path.write_text(json.dumps(altered))
            with self.subTest(nested=nested, field=field):
                with self.assertRaises(ValueError):
                    self.run_config(resume=True)
                with self.assertRaises(ValueError):
                    runner.summarize_sequence_run(self.out)
        path.write_text(json.dumps(original))

    def test_saved_episode_task_version_and_spec_tampering_are_rejected(self):
        self.run_config()
        path = self.out / "trials" / "M3" / "transport_place" / "seed11_repeat0.json"
        original = json.loads(path.read_text())
        for nested, field, value in ((False, "task_id", "pour_prep"), (False, "scene_version", "old"),
                                    (False, "task_spec_sha256", "stale"),
                                    (True, "schema_version", "episode_v0.3"),
                                    (True, "task_id", "handover_transfer"), (True, "scene_version", "old"),
                                    (True, "controller_version", "old"), (True, "evaluation_version", "old"),
                                    (True, "task_spec_sha256", "stale"), (True, "task_spec", {}),
                                    (True, "status", "simulation_invalid"), (True, "observation_config", {})):
            altered = deepcopy(original)
            (altered["episode"] if nested else altered)[field] = value
            path.write_text(json.dumps(altered))
            with self.subTest(nested=nested, field=field):
                with self.assertRaises(ValueError):
                    self.run_config(resume=True)
                with self.assertRaises(ValueError):
                    runner.summarize_sequence_run(self.out)
        path.write_text(json.dumps(original))

    def test_saved_top_level_spec_and_sequence_config_tampering_are_rejected(self):
        self.run_config()
        path = self.out / "trials" / "M3" / "transport_place" / "seed11_repeat0.json"
        original = json.loads(path.read_text())
        for field, value in (("task_spec", {}), ("sequence_config", {}), ("policy_mode", "incremental")):
            path.write_text(json.dumps(dict(deepcopy(original), **{field: value})))
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.summarize_sequence_run(self.out)
        path.write_text(json.dumps(original))

    def test_filter_error_retries_archive_attempts(self):
        self.raw.update(tasks=["transport_place"], seeds=[11], repeats=1,
                        methods=[{"id": "SMOKE", "kind": "smoke"}])
        self.filter.side_effect = RuntimeError("world unavailable")
        summary = self.run_config()
        self.assertEqual(summary["methods"]["SMOKE"]["transport_place"]["status_counts"]["filter_error"], 1)
        self.execution.assert_not_called()
        self.filter.side_effect = self.fake_filter
        summary = self.run_config(resume=True, retry_errors=True)
        self.assertEqual(len(list((self.out / "attempts").rglob("*.json"))), 1)
        self.assertEqual(summary["methods"]["SMOKE"]["transport_place"]["completed_n"], 1)

    def test_physical_execution_and_numerical_failures_remain_separate_and_do_not_retry(self):
        self.raw.update(tasks=["transport_place"], repeats=1,
                        methods=[{"id": "SMOKE", "kind": "smoke"}])

        def physical_failure(*args, **kwargs):
            episode = self.fake_episode(*args, **kwargs)
            status = "execution_failed" if args[1] == 11 else "simulation_invalid"
            episode.update(status=status, success=False, failure_reason=status)
            episode["success_checks"]["fluid_density_valid"] = status == "execution_failed"
            return episode

        self.execution.side_effect = physical_failure
        summary = self.run_config()
        metrics = summary["methods"]["SMOKE"]["transport_place"]
        self.assertEqual(metrics["execution_failed_n"], 1)
        self.assertEqual(metrics["simulation_invalid_n"], 1)
        self.assertEqual(metrics["numerically_valid_n"], 1)
        self.assertEqual(metrics["numerically_invalid_n"], 1)
        self.filter.reset_mock()
        self.execution.reset_mock()
        self.assertEqual(self.run_config(resume=True, retry_errors=True), summary)
        self.filter.assert_not_called()
        self.execution.assert_not_called()
        self.assertEqual(len(list((self.out / "attempts").rglob("*.json"))), 0)

    def test_sequence_B_baselines_bind_metadata_and_share_the_exact_LLM_candidate_input(self):
        paths = prepare_b_inputs(self.base / "B")
        self.raw.update(candidates=paths["candidates"], b_provenance=paths["provenance"],
                        tasks=["transport_place"], seeds=[11], repeats=1,
                        methods=[{"id": "B_GEOMETRY", "kind": "b_geometry"},
                                 {"id": "B_RANDOM", "kind": "b_random"},
                                 {"id": "M3", "kind": "llm", "model": "mock",
                                  "provider": {"type": "mock"}}])
        self.run_config()
        self.assertEqual(self.filter.call_count, 1)
        self.assertEqual(self.execution.call_count, 3)
        manifest = json.loads((self.out / "manifest.json").read_text())
        origin = manifest["baseline_provenance"]
        for method in ("B_GEOMETRY", "B_RANDOM"):
            record = json.loads((self.out / "trials" / method / "transport_place" / "seed11_repeat0.json").read_text())
            metadata = record["selection"]["metadata"]
            for field in ("candidate_sha256", "candidate_file_sha256", "report_sha256", "score_version", "scope"):
                self.assertEqual(metadata[field], origin[field])
            self.assertNotIn("score_breakdown", metadata)
        request = self.model.call_args.args[1]
        content = json.loads(request.messages[1]["content"])
        self.assertEqual(content["candidates"], [c.public_metadata() for c in load_candidates(paths["candidates"])])
        for candidate in content["candidates"]:
            self.assertEqual(set(candidate), {"id", "region", "approach", "gripper_width", "geometry_score"})


class RecordingProvider:
    identity = {"type": "offline_test", "adapter_version": "1"}

    def __init__(self, invalid_first=False):
        self.requests = []
        self.invalid_first = invalid_first

    def generate(self, request):
        self.requests.append(deepcopy(request))
        ids = request.schema["properties"]["selected_id"]["enum"]
        text = "invalid json" if self.invalid_first and len(self.requests) == 1 else json.dumps(
            {"selected_id": ids[0], "ranking": ids})
        return ModelResponse(text=text, model=request.model)


class SequenceCacheContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)
        self.public = [candidate.public_metadata() for candidate in load_candidates(ROOT / "candidates.example.json")]
        specs = sequence_specs(["transport_place", "handover_transfer"], SequenceConfig())
        self.manifest = {"track": "sequence", "scene_version": "hollow_cup_sequences_v1",
                         "controller_version": "staged_sequences_v1", "evaluation_version": "physical_outcomes_v1",
                         "candidate_sha256": "full-pose-hash", "observation_config": {"noise_seed": 1000},
                         "task_specs": specs, "baseline_provenance": None}

    def test_scene_full_candidate_and_spec_changes_miss_cache_without_entering_prompt(self):
        provider = RecordingProvider()
        original = selection_context(self.manifest, "transport_place")

        def choose(context):
            return LLMSelector(provider, "offline", cache_dir=self.cache, cache_context=context)(
                "transport_place", self.public, 11)

        first = choose(original)
        self.assertFalse(first["metadata"]["cache_hit"])
        self.assertTrue(choose(original)["metadata"]["cache_hit"])
        for field in ("scene_version", "candidate_sha256", "task_spec_sha256"):
            altered = dict(deepcopy(original), **{field: "private-cache-only-" + field})
            result = choose(altered)
            self.assertFalse(result["metadata"]["cache_hit"])
            self.assertEqual(result["metadata"]["execution_context"], altered)
        self.assertEqual(len(provider.requests), 4)
        for request in provider.requests:
            self.assertEqual(request.messages, provider.requests[0].messages)
            self.assertNotIn("private-cache-only", json.dumps(asdict(request)))
            content = json.loads(request.messages[1]["content"])
            self.assertEqual(set(content), {"task_id", "task_intent", "candidates"})
            for candidate in content["candidates"]:
                self.assertEqual(set(candidate), {"id", "region", "approach", "gripper_width", "geometry_score"})

    def test_a1_initial_and_repair_omit_task_and_specs_but_context_still_separates_tasks(self):
        requests = []
        for task in ("transport_place", "handover_transfer"):
            provider = RecordingProvider(invalid_first=True)
            result = LLMSelector(provider, "offline", variant="A1", cache_dir=self.cache,
                                 cache_context=selection_context(self.manifest, task))(
                                     task, self.public, 11)
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(result["metadata"]["repair_attempts"], 1)
            requests.append(provider.requests)
            for request in provider.requests:
                prompt = json.dumps(request.messages)
                for private in ("transport_place", "handover_transfer", "task_spec", "transfer_min_ratio", "physical_outcomes_v1"):
                    self.assertNotIn(private, prompt)
            self.assertEqual(set(json.loads(provider.requests[0].messages[1]["content"])), {"candidates"})
        self.assertEqual([request.messages for request in requests[0]],
                         [request.messages for request in requests[1]])


if __name__ == "__main__":
    unittest.main()
