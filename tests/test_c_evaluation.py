"""C evaluation regressions: no paid calls or physical simulation required."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from grasp_c.config import load_config
from grasp_c.metrics import summarize_records
from grasp_c import runner
from grasp_c.providers import ProviderConfigurationError

ROOT = Path(__file__).resolve().parents[1]


class EvaluationFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        (self.base / "candidates.json").write_bytes((ROOT / "candidates.example.json").read_bytes())
        self.path = self.base / "config.json"
        self.config = {"candidates": "candidates.json", "seeds": [10],
                       "tasks": ["pour_prep"], "repeats": 2,
                       "methods": [{"id": "GPT", "kind": "llm", "model": "example-model",
                                    "provider": {"type": "openai"}}]}

    def load(self, config=None):
        self.path.write_text(json.dumps(config or self.config), encoding="utf-8")
        return load_config(self.path)


class ConfigTests(EvaluationFixture, unittest.TestCase):
    def test_validation_is_offline_and_resolves_paths_without_credentials(self):
        with patch.dict("os.environ", {}, clear=True), patch(
                "grasp_c.providers.OpenAIProvider.preflight") as preflight:
            validated = self.load()
        preflight.assert_not_called()
        self.assertEqual(validated["candidates"], str(self.base / "candidates.json"))
        self.assertEqual(validated["methods"][0]["variant"], "M3")
        self.assertEqual(validated["methods"][0]["cache_mode"], "read_write")

    def test_bad_protocol_and_literal_keys_are_rejected_offline(self):
        cases = []
        for key, value in (("seeds", [True]), ("seeds", [0, 0]),
                           ("tasks", ["unknown"]), ("repeats", 0)):
            config = deepcopy(self.config)
            config[key] = value
            cases.append(config)
        for update in ({"prompt_version": "v9"}, {"cache_mode": "refresh"},
                       {"parameters": {"seed": 1}},
                       {"parameters": {"temperature": "invalid"}},
                       {"parameters": {"max_output_tokens": 0}},
                       {"parameters": {"reasoning_effort": 123}},
                       {"provider": {"type": "openai", "api_key": "do-not-store"}},
                       {"provider": {"type": "openai", "base_url": "https://user:secret@example.com"}}):
            config = deepcopy(self.config)
            config["methods"][0].update(update)
            cases.append(config)
        for config in cases:
            with self.subTest(config=config), self.assertRaises((ValueError, ProviderConfigurationError)):
                self.load(config)

    def test_seed_split_rejects_controller_pilot_overlap(self):
        self.config["seed_split"] = {"pilot_controller": [10], "evaluation": [10]}
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.load()

    def test_codex_validation_needs_neither_cli_nor_login(self):
        self.config["methods"][0]["provider"] = {"type": "codex", "executable": "absent-codex"}
        with patch("grasp_c.providers.subprocess.run", side_effect=AssertionError("offline validation called CLI")):
            self.load()

    def test_independent_acceptable_sets_validate_candidate_ids(self):
        oracle = self.base / "oracle.json"
        oracle.write_text(json.dumps({"pour_prep": {"10": ["G02"], "default": []}}))
        self.config["acceptable_sets"] = "oracle.json"
        self.assertEqual(self.load()["acceptable_sets"], str(oracle))
        oracle.write_text(json.dumps({"pour_prep": ["G99"]}))
        with self.assertRaisesRegex(ValueError, "unknown candidate"):
            self.load()


class MetricsTests(unittest.TestCase):
    def plan(self, methods=("A", "B"), seeds=(0, 1, 2), repeats=1):
        return [{"method_id": method, "task_id": "pour_prep", "seed": seed, "repeat": repeat}
                for method in methods for seed in seeds for repeat in range(repeats)]

    def record(self, method="A", seed=0, repeat=0, status="completed", success=True):
        return {"method_id": method, "task_id": "pour_prep", "seed": seed, "repeat": repeat,
                "manifest_sha256": "same-environment", "controller_version": "same-controller",
                "candidate_sha256": "same-candidates", "status": status,
                "selected_id": "G02" if status == "completed" else None,
                "filter": {"status": "ok"}, "selection": {"latency_s": 0.25, "metadata": {}},
                "episode": {"success": success, "collision": not success, "dropped": False,
                            "grasp_maintained": True, "position_error_m": 0.01 if success else None,
                            "orientation_error_deg": 2.0 if success else None,
                            "failure_reason": None if success else "unintended_collision"}
                           if status == "completed" else None}

    def test_errors_are_excluded_from_physical_rates_and_nulls_from_means(self):
        records = [self.record(seed=0), self.record(seed=1, success=False),
                   self.record(seed=2, status="execution_error")]
        summary = summarize_records(records, self.plan(methods=("A",)))
        metrics = summary["methods"]["A"]["pour_prep"]
        self.assertEqual(metrics["conditional_success_rate"], 0.5)
        self.assertAlmostEqual(metrics["success_coverage"], 1/3)
        self.assertEqual(metrics["physical_flags"]["collision"]["valid_n"], 2)
        self.assertEqual(metrics["position_error_m"]["n"], 1)
        self.assertEqual(metrics["position_error_m"]["mean"], 0.01)
        self.assertIsNone(metrics["selection"]["acceptable_set"]["rate"])

    def test_missing_trials_and_no_completed_samples_do_not_become_false_failures(self):
        record = self.record(seed=0, status="filter_error")
        record["filter"] = None
        record["selection"] = None
        metrics = summarize_records([record], self.plan(methods=("A",)))["methods"]["A"]["pour_prep"]
        self.assertIsNone(metrics["conditional_success_rate"])
        self.assertEqual(metrics["status_counts"]["missing"], 2)
        self.assertEqual(metrics["physical_flags"]["dropped"]["valid_n"], 0)

    def test_pairing_uses_only_shared_completed_identity_and_provenance(self):
        records = [self.record("A", 0), self.record("B", 0, success=False),
                   self.record("A", 1), self.record("B", 1, status="selector_error"),
                   self.record("A", 2), self.record("B", 2)]
        records[-1]["manifest_sha256"] = "different-environment"
        pair = summarize_records(records, self.plan())["paired_comparisons"][0]
        self.assertEqual(pair["paired_completed_n"], 1)
        self.assertEqual(pair["excluded_n"], 2)
        self.assertEqual(pair["completed_provenance_mismatch_n"], 1)
        self.assertEqual(pair["left_only_success_n"], 1)

    def test_repeats_oracle_and_cached_versus_live_tokens_have_separate_denominators(self):
        records = [self.record(repeat=0), self.record(repeat=1)]
        records[1]["selected_id"] = "G01"
        records[0]["selection"]["metadata"] = {
            "cache_hit": True, "repair_attempts": 1, "fallback_to_m1": False,
            "requests": 2, "live_requests": 0,
            "usage": {"input_tokens": 200}, "live_usage": {},
            "calls": [{"phase": "initial", "validation": {"valid": False}},
                      {"phase": "repair", "validation": {"valid": True}}]}
        records[1]["selection"]["metadata"] = {
            "cache_hit": False, "repair_attempts": 0, "fallback_to_m1": True,
            "requests": 1, "live_requests": 1,
            "usage": {"input_tokens": 100}, "live_usage": {"input_tokens": 100},
            "calls": [{"phase": "initial", "error": {"type": "ProviderError", "code": "timeout"}}]}
        metrics = summarize_records(records, self.plan(methods=("A",), seeds=(0,), repeats=2),
                                    acceptable_sets={"pour_prep": ["G02"]})["methods"]["A"]["pour_prep"]["selection"]
        self.assertEqual(metrics["acceptable_set"]["rate"], 0.5)
        self.assertFalse(metrics["repeat_consistency"][0]["all_identical"])
        self.assertEqual(metrics["token_usage"]["input_tokens"], 300)
        self.assertEqual(metrics["live_token_usage"]["input_tokens"], 100)
        self.assertEqual(metrics["fallback_rate"], 0.5)
        self.assertEqual(metrics["initial_model_valid_rate"], 0.0)
        self.assertEqual(metrics["model_usable_after_repair_rate"], 0.5)
        self.assertEqual(metrics["model_call_outcomes"],
                         {"invalid_output": 1, "valid_output": 1, "transport_error": 1})


class RunnerTests(EvaluationFixture, unittest.TestCase):
    """Fake only A's simulator boundary; run actual config, persistence, and metrics."""
    def setUp(self):
        super().setUp()
        self.config.update(seeds=[0, 1], tasks=["pour_prep", "handover_prep"], repeats=2,
                           methods=[{"id": "SMOKE", "kind": "smoke"},
                                    {"id": "GEOMETRY", "kind": "geometry"}])
        self.out = self.base / "output"
        self.filtered = {"status": "ok", "feasible_candidates": [
            {"id": "G02", "region": "body", "approach": "side", "gripper_width": 0.078,
             "geometry_score": 0.66}], "checks": []}
        self.filter = Mock(side_effect=lambda seed, candidates: dict(deepcopy(self.filtered), seed=seed))
        self.evaluate = Mock(side_effect=self.fake_evaluate)
        simulation = types.ModuleType("grasp_a.simulation")
        simulation.get_feasible_candidates = self.filter
        simulation.CONTROLLER_VERSION = "test-controller"
        bridge = types.ModuleType("examples.evaluate_one_trial")
        bridge.evaluate_trial = self.evaluate
        bridge.smoke_selector = lambda task, candidates, seed: candidates[0]["id"]
        self.modules = patch.dict(sys.modules, {"grasp_a.simulation": simulation,
                                               "examples.evaluate_one_trial": bridge})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.provenance = patch.object(runner, "build_provenance", side_effect=self.fake_provenance)
        self.provenance.start()
        self.addCleanup(self.provenance.stop)

    def fake_provenance(self, config, providers, *, media, out_dir):
        return {"schema_version": "grasp_c_run_v1", "configuration": config,
                "plan": runner.trial_plan(config), "evaluation_scope": "pilot",
                "controller_version": "test-controller", "run_options": {"media": media},
                "candidate_sha256": hashlib.sha256(Path(config["candidates"]).read_bytes()).hexdigest(),
                "acceptable_sets_canonical_sha256":
                    runner._sha(json.loads(Path(config["acceptable_sets"]).read_text()))
                    if config.get("acceptable_sets") else None}

    def fake_evaluate(self, task, seed, method, candidates, selector, *, filtered, media_dir):
        raw = selector(task, deepcopy(filtered["feasible_candidates"]), seed)
        selected = raw if isinstance(raw, str) else raw["selected_id"]
        record = MetricsTests().record(method=method, seed=seed)
        record.update(task_id=task, controller_version="test-controller", filter=deepcopy(filtered),
                      candidate_sha256=hashlib.sha256((self.base / "candidates.json").read_bytes()).hexdigest(),
                      selected_id=selected, error=None)
        return record

    def run_config(self, **kwargs):
        self.load()
        return runner.run_evaluation(self.path, self.out, **kwargs)

    def test_each_seed_filters_once_all_methods_tasks_repeats_saved_and_resume_calls_nothing(self):
        first = self.run_config()
        self.assertEqual(self.filter.call_count, 2)
        self.assertEqual(self.evaluate.call_count, 16)
        self.assertEqual(first["saved_n"], 16)
        self.assertEqual(len(list((self.out / "trials").rglob("*.json"))), 16)
        resumed = self.run_config(resume=True)
        self.assertEqual(self.filter.call_count, 2)
        self.assertEqual(self.evaluate.call_count, 16)
        self.assertEqual(first, resumed)

    def test_filter_errors_saved_as_errors_and_retried_only_explicitly(self):
        self.filter.side_effect = RuntimeError("physics unavailable")
        summary = self.run_config()
        self.assertEqual(summary["saved_n"], 16)
        self.evaluate.assert_not_called()
        self.run_config(resume=True)
        self.assertEqual(self.filter.call_count, 2)
        self.filter.side_effect = lambda seed, candidates: dict(deepcopy(self.filtered), seed=seed)
        retried = self.run_config(resume=True, retry_errors=True)
        self.assertEqual(self.filter.call_count, 4)
        self.assertEqual(self.evaluate.call_count, 16)
        self.assertEqual(len(list((self.out / "attempts").rglob("*.json"))), 16)
        self.assertEqual(retried["methods"]["SMOKE"]["pour_prep"]["completed_n"], 4)
        self.assertEqual(retried["attempt_history"]["archived_status_counts"], {"filter_error": 16})

    def test_resume_rejects_changed_configuration_or_input_content(self):
        self.run_config()
        self.config["repeats"] = 3
        with self.assertRaisesRegex(ValueError, "resume refused"):
            self.run_config(resume=True)
        self.config["repeats"] = 2
        with (self.base / "candidates.json").open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "resume refused"):
            self.run_config(resume=True)

    def test_preflight_fails_before_any_filter_or_evaluation(self):
        self.config["methods"] = [{"id": "BAD", "kind": "llm", "model": "example-model",
                                    "provider": {"type": "openai"}}]
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ProviderConfigurationError):
            self.run_config()
        self.filter.assert_not_called()
        self.evaluate.assert_not_called()

    def test_fatal_provider_error_saves_one_trial_and_summary_then_stops(self):
        def failure(*args, **kwargs):
            record = self.fake_evaluate(*args, **kwargs)
            record.update(status="selector_error", selected_id=None, episode=None,
                          error={"type": "ProviderConfigurationError", "message": "model unavailable"})
            return record
        self.evaluate.side_effect = failure
        with self.assertRaises(ProviderConfigurationError):
            self.run_config()
        self.assertEqual(self.evaluate.call_count, 1)
        self.assertTrue((self.out / "summary.json").exists())
        self.assertEqual(len(list((self.out / "trials").rglob("*.json"))), 1)

    def test_completed_resume_and_read_only_cache_need_no_provider_preflight(self):
        self.config["methods"] = [{"id": "MOCK", "kind": "llm", "model": "mock",
                                    "provider": {"type": "mock"}}]
        self.run_config()
        with patch("grasp_c.providers.MockProvider.preflight", side_effect=AssertionError("unexpected preflight")):
            self.run_config(resume=True)

    def test_read_only_replay_skips_authentication_preflight(self):
        self.config["methods"] = [{"id": "OFFLINE", "kind": "llm", "model": "example-model",
                                    "cache_mode": "read_only", "provider": {"type": "openai"}}]
        def fake_replay(task, seed, method, candidates, selector, *, filtered, media_dir):
            # Selector/cache behavior is covered independently; exercise runner authentication policy.
            return self.fake_evaluate(task, seed, method, candidates,
                                      lambda *args: "G02", filtered=filtered, media_dir=media_dir)
        self.evaluate.side_effect = fake_replay
        with patch("grasp_c.providers.OpenAIProvider.preflight", side_effect=AssertionError("unexpected preflight")):
            self.run_config()

    def test_oracle_is_snapshotted_and_reaggregation_has_no_model_or_simulator_calls(self):
        (self.base / "oracle.json").write_text(json.dumps({"pour_prep": ["G02"]}))
        self.config["acceptable_sets"] = "oracle.json"
        first = self.run_config()
        (self.base / "oracle.json").unlink()
        self.filter.reset_mock()
        self.evaluate.reset_mock()
        second = runner.summarize_run(self.out)
        self.assertEqual(first, second)
        self.filter.assert_not_called()
        self.evaluate.assert_not_called()
        self.assertEqual(second["methods"]["SMOKE"]["pour_prep"]["selection"]["acceptable_set"]["rate"], 1.0)
        self.assertIsNone(second["methods"]["SMOKE"]["handover_prep"]["selection"]["acceptable_set"]["rate"])
        # Altering the auxiliary snapshot checksum cannot alter the signed labels.
        snapshot = self.out / "acceptable_sets.json"
        runner.atomic_json(snapshot, {"pour_prep": ["G01"]})
        manifest_path = self.out / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["acceptable_sets_snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        runner.atomic_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "signed run provenance"):
            runner.summarize_run(self.out)

    def test_atomic_write_does_not_replace_valid_record_on_invalid_json(self):
        path = self.base / "record.json"
        runner.atomic_json(path, {"good": True})
        with self.assertRaises(ValueError):
            runner.atomic_json(path, {"bad": float("nan")})
        self.assertEqual(json.loads(path.read_text()), {"good": True})
        self.assertEqual(list(self.base.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
