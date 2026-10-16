"""Offline selector regressions using injected providers, never a paid model."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from grasp_c.cache import CacheCorruptionError, CacheMissError
from grasp_c.prompts import PUBLIC_FIELDS
from grasp_c.providers import ModelResponse, ProviderConfigurationError
from grasp_c.selectors import LLMSelector, geometry_selector, random_selector, validate_response


PUBLIC = [
    {"id": "G02", "region": "body", "approach": "from_robot_side_high",
     "gripper_width": 0.078, "geometry_score": 0.66},
    {"id": "G01", "region": "handle", "approach": "from_handle_side",
     "gripper_width": 0.035, "geometry_score": 0.8},
]
VALID = '{"selected_id":"G02","ranking":["G02","G01"]}'


class FakeProvider:
    def __init__(self, *responses, name="fake", revision="1"):
        self.responses = list(responses)
        self.requests = []
        self.identity = {"provider": name, "revision": revision}

    def generate(self, request):
        self.requests.append(deepcopy(request))
        if not self.responses:
            raise AssertionError("provider called unexpectedly")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ModelResponse):
            return result
        return ModelResponse(text=result, model=request.model,
                             usage={"input_tokens": 10, "output_tokens": 5,
                                    "total_tokens": 15}, request_id=f"fake-{len(self.requests)}")


class SelectorTests(unittest.TestCase):
    def select(self, provider, task="pour_prep", public=None, seed=3, **kwargs):
        return LLMSelector(provider, "fake-model", **kwargs)(
            task, deepcopy(PUBLIC) if public is None else public, seed)

    def test_valid_ranking_preserves_audit_and_sends_only_public_fields(self):
        public = deepcopy(PUBLIC)
        public[0].update(grasp_pose={"position": [99, 99, 99]},
                         filter_reasons=["private_collision"], oracle="private_answer")
        before = deepcopy(public)
        provider = FakeProvider(VALID)
        result = self.select(provider, public=public)
        self.assertEqual(result["selected_id"], "G02")
        self.assertEqual(result["ranking"], ["G02", "G01"])
        metadata = result["metadata"]
        self.assertEqual(metadata["repair_attempts"], 0)
        self.assertFalse(metadata["fallback_to_geometry"])
        self.assertFalse(metadata["fallback_to_m1"])
        self.assertEqual(metadata["fallback_to_m1_semantics"],
                         "legacy_alias_for_fallback_to_geometry_not_official_b_m1")
        self.assertEqual(metadata["raw_initial_response"], VALID)
        self.assertEqual(metadata["calls"][0]["request_id"], "fake-1")
        self.assertEqual(metadata["calls"][0]["validation"], {"valid": True, "errors": []})
        self.assertGreaterEqual(metadata["calls"][0]["latency_s"], 0)
        payload = json.loads(provider.requests[0].messages[1]["content"])
        for item in payload["candidates"]:
            self.assertEqual(set(item), set(PUBLIC_FIELDS))
        prompt = json.dumps(provider.requests[0].messages)
        self.assertNotIn("private_collision", prompt)
        self.assertNotIn("private_answer", prompt)
        self.assertNotIn("grasp_pose", prompt)
        self.assertEqual(public, before)
        json.dumps(result, allow_nan=False)

    def test_invalid_response_repaired_exactly_once(self):
        bad = '{"selected_id":"G99","ranking":["G99"]}'
        provider = FakeProvider(bad, VALID)
        result = self.select(provider)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(result["selected_id"], "G02")
        self.assertEqual(result["metadata"]["repair_attempts"], 1)
        self.assertFalse(result["metadata"]["fallback_to_m1"])
        self.assertEqual(result["metadata"]["raw_repair_response"], VALID)
        self.assertFalse(result["metadata"]["calls"][0]["validation"]["valid"])
        self.assertEqual(provider.requests[1].messages[-2]["content"], bad)
        self.assertEqual(result["metadata"]["usage"]["total_tokens"], 30)

    def test_two_invalid_responses_fallback_to_geometry_and_keep_raw_outputs(self):
        provider = FakeProvider("not JSON", '{"selected_id":"G02","ranking":[]}')
        result = self.select(provider)
        self.assertEqual(result["selected_id"], "G01")
        self.assertEqual(result["ranking"], ["G01", "G02"])
        self.assertEqual(len(provider.requests), 2)
        metadata = result["metadata"]
        self.assertEqual(metadata["repair_attempts"], 1)
        self.assertTrue(metadata["fallback_to_geometry"])
        self.assertTrue(metadata["fallback_to_m1"])
        self.assertEqual(metadata["fallback_method"], "geometry_reference")
        self.assertEqual(metadata["score_scope"], "pilot_input")
        self.assertEqual(metadata["fallback_count"], 1)
        self.assertEqual(metadata["fallback_reason"], "invalid_model_output")
        self.assertEqual(metadata["raw_initial_response"], "not JSON")
        self.assertFalse(metadata["calls"][1]["validation"]["valid"])

    def test_repair_prompt_excludes_independent_labels_and_execution_diagnostics(self):
        public = deepcopy(PUBLIC)
        for item in public:
            item.update(acceptable_sets={"pour_prep": ["private_oracle_id"]},
                        task_suitability="private_task_label",
                        selection_space={"warnings": ["private_audit_warning"]},
                        episode={"success": True, "target": "private_pose"})
        provider = FakeProvider("invalid JSON", VALID)
        self.select(provider, public=public)
        self.assertEqual(len(provider.requests), 2)
        for request in provider.requests:
            initial_payload = json.loads(request.messages[1]["content"])
            for item in initial_payload["candidates"]:
                self.assertEqual(set(item), set(PUBLIC_FIELDS))
            prompt = json.dumps(request.messages)
            for private_value in ("acceptable_sets", "private_oracle_id", "task_suitability",
                                  "private_task_label", "selection_space", "private_audit_warning",
                                  "episode", "private_pose"):
                self.assertNotIn(private_value, prompt)

    def test_strict_json_and_complete_permutation_validation(self):
        invalid = [
            "```json\n" + VALID + "\n```",
            '{"selected_id":"G02","selected_id":"G01","ranking":["G01","G02"]}',
            '{"selected_id":"G02","ranking":["G02","G02"]}',
            '{"selected_id":"G02","ranking":["G01","G02"]}',
            '{"selected_id":"G02"}',
            '{"selected_id":"G02","ranking":["G02","G01"],"reason":"extra"}',
            '{"selected_id":"G02","ranking":[NaN,"G01"]}',
            '[]',
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                parsed, errors = validate_response(raw, ["G02", "G01"])
                self.assertIsNone(parsed)
                self.assertTrue(errors)
        parsed, errors = validate_response(VALID, ["G02", "G01"])
        self.assertEqual(parsed["selected_id"], "G02")
        self.assertFalse(errors)

    def test_transport_timeout_fallback_is_distinct_and_not_repaired(self):
        provider = FakeProvider(TimeoutError("request timed out"))
        result = self.select(provider)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(result["metadata"]["repair_attempts"], 0)
        self.assertEqual(result["metadata"]["fallback_reason"], "provider_error")
        self.assertEqual(result["metadata"]["calls"][0]["error"]["type"], "TimeoutError")
        self.assertIsNone(result["metadata"]["raw_initial_response"])

    def test_refusal_and_incomplete_responses_fallback_without_format_repair(self):
        for status in ("refused", "incomplete"):
            with self.subTest(status=status):
                response = ModelResponse(text="cannot rank", model="fake-model",
                                         metadata={"status": status}, request_id="failed")
                provider = FakeProvider(response)
                result = self.select(provider)
                self.assertEqual(len(provider.requests), 1)
                metadata = result["metadata"]
                self.assertEqual(metadata["repair_attempts"], 0)
                self.assertTrue(metadata["fallback_to_m1"])
                self.assertEqual(metadata["raw_initial_response"], "cannot rank")
                self.assertIsNotNone(metadata["calls"][0]["error"])

    def test_repair_transport_error_still_only_one_repair(self):
        result = self.select(FakeProvider("bad", ConnectionError("offline")))
        self.assertEqual(result["selected_id"], "G01")
        self.assertEqual(result["metadata"]["repair_attempts"], 1)
        self.assertEqual(result["metadata"]["fallback_reason"], "provider_error")
        self.assertEqual(len(result["metadata"]["calls"]), 2)

    def test_configuration_and_programming_errors_propagate(self):
        for error in (ProviderConfigurationError("API credentials missing"),
                      TypeError("provider implementation bug")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    self.select(FakeProvider(error))

    def test_a1_removes_task_information_from_initial_and_repair(self):
        providers = [FakeProvider("bad", VALID), FakeProvider("bad", VALID)]
        self.select(providers[0], variant="A1", task="pour_prep")
        self.select(providers[1], variant="A1", task="handover_prep")
        for first, second in zip(providers[0].requests, providers[1].requests):
            self.assertEqual(first, second)
            text = json.dumps(first.messages)
            for secret in ("pour_prep", "handover_prep", "pouring", "recipient",
                           "35 degrees", "two seconds", "task_intent", "task_id"):
                self.assertNotIn(secret, text)
        m3 = FakeProvider(VALID)
        self.select(m3, task="pour_prep")
        self.assertIn("pour_prep", json.dumps(m3.requests[0].messages))

    def test_geometry_ties_and_random_rng_are_deterministic(self):
        public = deepcopy(PUBLIC)
        for item in public:
            item["geometry_score"] = 0.5
        self.assertEqual(geometry_selector("pour_prep", public, 0)["selected_id"], "G01")
        self.assertEqual(random_selector("pour_prep", public, 42),
                         random_selector("pour_prep", public, 42))

    def test_geometry_reference_does_not_claim_the_unimplemented_b_baseline(self):
        metadata = geometry_selector("pour_prep", PUBLIC, 0)["metadata"]
        self.assertEqual(metadata["method_id"], "reference_geometry")
        self.assertEqual(metadata["fallback_method"], "geometry_reference")
        self.assertEqual(metadata["score_scope"], "pilot_input")

    def test_invalid_input_or_configuration_never_calls_provider(self):
        provider = FakeProvider()
        for bad in ([], PUBLIC + [PUBLIC[0]], [{**PUBLIC[0], "geometry_score": float("nan")}],
                    [{**PUBLIC[0], "gripper_width": True}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.select(provider, public=bad)
        for kwargs in ({"variant": "M9"}, {"prompt_version": "v99"},
                       {"cache_mode": "refresh"}, {"sample_id": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.select(provider, **kwargs)
        self.assertFalse(provider.requests)


class SelectorCacheTests(unittest.TestCase):
    def test_mutating_provider_cannot_change_the_original_cache_identity(self):
        class MutatingProvider(FakeProvider):
            def generate(self, request):
                response = super().generate(request)
                request.messages.clear()
                request.schema.clear()
                request.parameters["provider_internal_mutation"] = True
                return response

        with tempfile.TemporaryDirectory() as directory:
            provider = MutatingProvider(VALID)
            selector = LLMSelector(provider, "fake-model", cache_dir=directory)
            first = selector("pour_prep", PUBLIC, 0)
            second = selector("pour_prep", PUBLIC, 0)
            self.assertEqual(first["metadata"]["calls"][0]["cache_key"],
                             second["metadata"]["calls"][0]["cache_key"])
            self.assertTrue(second["metadata"]["cache_hit"])
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(selector.parameters, {})

    def test_json_serializable_tuples_roundtrip_in_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(VALID)
            provider.identity["endpoint_tags"] = ("local", "test")
            selector = LLMSelector(provider, "fake-model", cache_dir=directory)
            selector("pour_prep", PUBLIC, 0)
            self.assertTrue(selector("pour_prep", PUBLIC, 0)["metadata"]["cache_hit"])
            self.assertEqual(len(provider.requests), 1)

    def test_repeat_and_readonly_replay_preserve_original_response_without_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider("bad", VALID)
            selector = LLMSelector(provider, "fake-model", cache_dir=directory)
            first = selector("pour_prep", PUBLIC, 0)
            second = selector("pour_prep", PUBLIC, 0)
            self.assertEqual(len(provider.requests), 2)
            self.assertFalse(first["metadata"]["cache_hit"])
            self.assertTrue(second["metadata"]["cache_hit"])
            self.assertEqual(second["metadata"]["cache_hits"], 2)
            self.assertEqual(second["metadata"]["live_requests"], 0)
            self.assertEqual(second["metadata"]["live_usage"], {})
            self.assertEqual(first["metadata"]["usage"], second["metadata"]["usage"])
            self.assertEqual(first["metadata"]["raw_initial_response"],
                             second["metadata"]["raw_initial_response"])
            replay_provider = FakeProvider()
            replay = LLMSelector(replay_provider, "fake-model", cache_dir=directory,
                                 cache_mode="read_only")("pour_prep", PUBLIC, 0)
            self.assertEqual(replay["selected_id"], "G02")
            self.assertFalse(replay_provider.requests)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_identity_includes_provider_model_params_prompt_seed_repeat_and_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(*([VALID] * 8))
            common = {"cache_dir": directory}
            LLMSelector(provider, "model-a", **common)("pour_prep", PUBLIC, 0)
            LLMSelector(provider, "model-b", **common)("pour_prep", PUBLIC, 0)
            LLMSelector(provider, "model-a", parameters={"temperature": 0.4}, **common)(
                "pour_prep", PUBLIC, 0)
            LLMSelector(provider, "model-a", **common)("handover_prep", PUBLIC, 0)
            LLMSelector(provider, "model-a", **common)("pour_prep", PUBLIC, 1)
            LLMSelector(provider, "model-a", sample_id=1, **common)("pour_prep", PUBLIC, 0)
            changed = deepcopy(PUBLIC)
            changed[0]["geometry_score"] = 0.7
            LLMSelector(provider, "model-a", **common)("pour_prep", changed, 0)
            provider.identity["revision"] = "2"
            LLMSelector(provider, "model-a", **common)("pour_prep", PUBLIC, 0)
            self.assertEqual(len(provider.requests), 8)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 8)

    def test_readonly_miss_raises_without_fallback_or_api(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            selector = LLMSelector(provider, "fake-model", cache_dir=directory,
                                   cache_mode="read_only")
            with self.assertRaises(CacheMissError):
                selector("pour_prep", PUBLIC, 0)
            self.assertFalse(provider.requests)

    def test_failed_provider_calls_are_not_cached(self):
        for failed in (TimeoutError("offline"), ModelResponse(
                text="", model="fake-model", metadata={"status": "incomplete"})):
            with self.subTest(failed=type(failed).__name__), tempfile.TemporaryDirectory() as directory:
                provider = FakeProvider(failed, VALID)
                selector = LLMSelector(provider, "fake-model", cache_dir=directory)
                self.assertTrue(selector("pour_prep", PUBLIC, 0)["metadata"]["fallback_to_m1"])
                self.assertEqual(list(Path(directory).glob("*.json")), [])
                self.assertFalse(selector("pour_prep", PUBLIC, 0)["metadata"]["fallback_to_m1"])
                self.assertEqual(len(provider.requests), 2)

    def test_off_mode_never_reads_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(VALID, VALID)
            selector = LLMSelector(provider, "fake-model", cache_dir=directory, cache_mode="off")
            for _ in range(2):
                self.assertFalse(selector("pour_prep", PUBLIC, 0)["metadata"]["cache_hit"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_corrupt_cache_is_an_explicit_storage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(VALID)
            selector = LLMSelector(provider, "fake-model", cache_dir=directory)
            selector("pour_prep", PUBLIC, 0)
            path = next(Path(directory).glob("*.json"))
            path.write_text("not JSON", encoding="utf-8")
            with self.assertRaises(CacheCorruptionError):
                selector("pour_prep", PUBLIC, 0)
            self.assertEqual(len(provider.requests), 1)


if __name__ == "__main__":
    unittest.main()
