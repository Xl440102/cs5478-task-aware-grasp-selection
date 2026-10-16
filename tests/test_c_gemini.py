"""Native Gemini wire contract and failure behavior; no network requests."""
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from grasp_c.gemini import GeminiProvider
from grasp_c.providers import (ModelRequest, ProviderConfigurationError,
                               ProviderError)


SCHEMA = {"type": "object", "properties": {
    "selected_id": {"type": "string", "enum": ["G02", "G03"]},
    "ranking": {"type": "array", "items": {"type": "string"}}},
    "required": ["selected_id", "ranking"], "additionalProperties": False}


def request(**parameters):
    return ModelRequest("gemini-2.5-flash", [
        {"role": "system", "content": "select only a candidate"},
        {"role": "user", "content": "select JSON"},
        {"role": "assistant", "content": '{"selected_id":"unknown"}'},
        {"role": "user", "content": "repair the invalid candidate"}], SCHEMA, parameters)


def response(*, finish="STOP", block=None, candidates=True, text='{"selected_id":"G02"}'):
    return SimpleNamespace(
        text=text, model_version="gemini-2.5-flash-version", response_id="gemini-response-1",
        candidates=[SimpleNamespace(finish_reason=finish)] if candidates else [],
        prompt_feedback=SimpleNamespace(block_reason=block),
        usage_metadata=SimpleNamespace(prompt_token_count=50, candidates_token_count=20,
            total_token_count=78, cached_content_token_count=10, thoughts_token_count=8))


def sdk():
    return SimpleNamespace(types=SimpleNamespace(
        Content=SimpleNamespace,
        Part=SimpleNamespace(from_text=lambda **kwargs: SimpleNamespace(**kwargs)),
        GenerateContentConfig=SimpleNamespace, ThinkingConfig=SimpleNamespace,
        HttpOptions=SimpleNamespace, HttpRetryOptions=SimpleNamespace), Client=Mock())


class GeminiTests(unittest.TestCase):
    def provider(self, result=None, error=None):
        provider = GeminiProvider()
        generate = Mock(return_value=result or response(), side_effect=error)
        provider._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        return provider, generate

    def test_native_schema_and_repair_roles_and_parameters(self):
        provider, generate = self.provider()
        with patch("grasp_c.gemini._load_sdk", return_value=sdk()):
            result = provider.generate(request(temperature=0, top_p=.9, max_output_tokens=1024,
                                               thinking_budget=0, seed=7))
        wire = generate.call_args.kwargs
        self.assertEqual(wire["model"], "gemini-2.5-flash")
        self.assertEqual([part.role for part in wire["contents"]], ["user", "model", "user"])
        self.assertEqual(wire["contents"][1].parts[0].text, '{"selected_id":"unknown"}')
        config = wire["config"]
        self.assertEqual(config.system_instruction, "select only a candidate")
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertEqual(config.response_json_schema, SCHEMA)
        self.assertEqual(config.max_output_tokens, 1024)
        self.assertEqual(config.seed, 7)
        self.assertEqual(config.thinking_config.thinking_budget, 0)
        self.assertFalse(config.thinking_config.include_thoughts)
        self.assertFalse(hasattr(config, "reasoning_effort"))
        self.assertEqual(result.text, '{"selected_id":"G02"}')
        self.assertEqual(result.model, "gemini-2.5-flash-version")
        self.assertEqual(result.request_id, "gemini-response-1")
        self.assertEqual(result.metadata["status"], "completed")
        self.assertEqual(result.usage, {"input_tokens": 50, "output_tokens": 20,
            "total_tokens": 78, "cached_input_tokens": 10, "reasoning_output_tokens": 8})

    def test_client_uses_selected_key_milliseconds_and_retry_attempts(self):
        native = sdk()
        native.Client.return_value = SimpleNamespace(models=SimpleNamespace(
            generate_content=Mock(return_value=response())))
        provider = GeminiProvider(timeout_s=2.5, max_retries=2)
        with patch("grasp_c.gemini._load_sdk", return_value=native), \
                patch.object(provider, "preflight"), \
                patch.dict(os.environ, {"GEMINI_API_KEY": "test-secret", "GOOGLE_API_KEY": "other"}):
            provider.generate(request())
            provider.generate(request())
        native.Client.assert_called_once()
        wire = native.Client.call_args.kwargs
        self.assertEqual(wire["api_key"], "test-secret")
        self.assertFalse(wire["vertexai"])
        self.assertEqual(wire["http_options"].timeout, 2500)
        self.assertEqual(wire["http_options"].retry_options.attempts, 3)
        self.assertNotIn("test-secret", json.dumps(provider.identity))
        self.assertEqual(provider.identity["sdk"], "google-genai")

    def test_truncated_response_retains_text_but_is_incomplete(self):
        provider, _ = self.provider(response(finish="MAX_TOKENS", text='{"selected'))
        with patch("grasp_c.gemini._load_sdk", return_value=sdk()):
            result = provider.generate(request())
        self.assertEqual(result.text, '{"selected')
        self.assertEqual(result.metadata["status"], "incomplete")
        self.assertEqual(result.metadata["incomplete_reason"], "MAX_TOKENS")

    def test_thinking_level_maps_to_native_config_and_excludes_budget(self):
        provider, generate = self.provider()
        with patch("grasp_c.gemini._load_sdk", return_value=sdk()):
            provider.generate(request(thinking_level="low"))
        config = generate.call_args.kwargs["config"].thinking_config
        self.assertEqual(config.thinking_level, "low")
        self.assertFalse(config.include_thoughts)
        self.assertFalse(hasattr(config, "thinking_budget"))
        for parameters in ({"thinking_level": "low", "thinking_budget": 0},
                           {"thinking_level": "unknown"}, {"thinking_level": 5},
                           {"thinking_level": ""}):
            with self.subTest(parameters=parameters), \
                    patch("grasp_c.gemini._load_sdk") as load, \
                    self.assertRaises(ProviderConfigurationError):
                provider.generate(request(**parameters))
            load.assert_not_called()

    def test_safety_and_prompt_blocks_are_refusals(self):
        for result in (response(finish="SAFETY", text=None),
                       response(block="SAFETY", candidates=False, text=None)):
            with self.subTest(result=result):
                provider, _ = self.provider(result)
                with patch("grasp_c.gemini._load_sdk", return_value=sdk()):
                    actual = provider.generate(request())
                self.assertEqual(actual.metadata["status"], "refused")
                self.assertEqual(actual.text, "")

    def test_no_candidates_and_unknown_finish_are_not_success(self):
        for result in (response(candidates=False, text=None), response(finish="OTHER")):
            provider, _ = self.provider(result)
            with patch("grasp_c.gemini._load_sdk", return_value=sdk()):
                actual = provider.generate(request())
            self.assertEqual(actual.metadata["status"], "incomplete")

    def test_absent_usage_and_model_version_are_not_invented(self):
        raw = response()
        raw.usage_metadata, raw.model_version = None, None
        provider, _ = self.provider(raw)
        with patch("grasp_c.gemini._load_sdk", return_value=sdk()):
            actual = provider.generate(request())
        self.assertEqual(actual.usage, {})
        self.assertEqual(actual.model, "gemini-2.5-flash")

    def test_http_errors_are_sanitized_and_classified(self):
        for code in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(code=code):
                error = RuntimeError("private API key and URL must never appear")
                error.code = code
                provider, _ = self.provider(error=error)
                with patch("grasp_c.gemini._load_sdk", return_value=sdk()), \
                        self.assertRaises(ProviderError) as caught:
                    provider.generate(request())
                self.assertEqual(caught.exception.fatal, code in {400, 401, 403, 404})
                self.assertEqual(caught.exception.code, f"http_{code}")
                self.assertNotIn("private", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_connection_and_sdk_validation_failures_are_sanitized(self):
        for error, fatal in ((TimeoutError("private URL"), False),
                             (ValueError("private SDK config"), True)):
            provider, _ = self.provider(error=error)
            with patch("grasp_c.gemini._load_sdk", return_value=sdk()), \
                    self.assertRaises(ProviderError) as caught:
                provider.generate(request())
            self.assertEqual(caught.exception.fatal, fatal)
            self.assertNotIn("private", str(caught.exception))

    def test_rejects_openai_parameters_before_sdk_or_network(self):
        provider, generate = self.provider()
        with patch("grasp_c.gemini._load_sdk") as load, \
                self.assertRaisesRegex(ProviderConfigurationError, "Unsupported"):
            provider.generate(request(reasoning_effort="low"))
        load.assert_not_called()
        generate.assert_not_called()
        for budget in (-2, True, 1.2):
            with self.assertRaises(ProviderConfigurationError):
                provider.generate(request(thinking_budget=budget))

    def test_rejects_nontext_or_unknown_role_and_invalid_settings(self):
        provider, generate = self.provider()
        for message in ({"role": "tool", "content": "text"},
                        {"role": "user", "content": [{"text": "image"}]}):
            invalid = request()
            invalid.messages = [message]
            with patch("grasp_c.gemini._load_sdk", return_value=sdk()), \
                    self.assertRaises(ProviderConfigurationError):
                provider.generate(invalid)
        generate.assert_not_called()
        for config in ({"api_key_env": "invalid=secret"}, {"timeout_s": float("nan")},
                       {"timeout_s": 0}, {"max_retries": -1}, {"max_retries": True}):
            with self.subTest(config=config), self.assertRaises(ProviderConfigurationError):
                GeminiProvider(**config)

    def test_preflight_requires_key_and_sdk_with_local_env_loading(self):
        provider = GeminiProvider()
        with patch("grasp_c.env.load_local_env") as load, \
                patch.dict(os.environ, {}, clear=True), \
                patch("grasp_c.gemini._load_sdk") as native, \
                self.assertRaisesRegex(ProviderConfigurationError, "GEMINI_API_KEY"):
            provider.preflight()
        load.assert_called_once()
        native.assert_not_called()
        with patch("grasp_c.env.load_local_env"), \
                patch.dict(os.environ, {"GEMINI_API_KEY": "test-secret"}), \
                patch("grasp_c.gemini.importlib.import_module", side_effect=ImportError), \
                self.assertRaisesRegex(ProviderConfigurationError, "google-genai"):
            provider.preflight()

    def test_official_sdk_types_accept_request_and_normalize_response_fixture(self):
        try:
            from google.genai import types
        except ImportError:
            self.skipTest("google-genai SDK not installed")
        fixture = types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason=types.FinishReason.STOP,
                content=types.Content(role="model", parts=[types.Part(
                    text='{"selected_id":"G03","ranking":["G03","G02"]}')]))],
            model_version="gemini-2.5-flash", response_id="fixture-response",
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=30, candidates_token_count=15, total_token_count=45))
        provider, generate = self.provider(fixture)
        actual = provider.generate(request(thinking_budget=0, max_output_tokens=1024))
        config = generate.call_args.kwargs["config"]
        self.assertIsInstance(config, types.GenerateContentConfig)
        self.assertEqual(config.response_json_schema, SCHEMA)
        self.assertEqual(config.thinking_config.thinking_budget, 0)
        self.assertEqual(actual.metadata["status"], "completed")
        self.assertEqual(json.loads(actual.text)["selected_id"], "G03")
        self.assertEqual(actual.usage, {"input_tokens": 30, "output_tokens": 15, "total_tokens": 45})
        provider.generate(request(thinking_level="low", max_output_tokens=1024))
        config = generate.call_args.kwargs["config"]
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.LOW)
        self.assertIsNone(config.thinking_config.thinking_budget)

    def test_official_sdk_error_details_never_escape(self):
        try:
            from google.genai import errors
        except ImportError:
            self.skipTest("google-genai SDK not installed")
        error = errors.ClientError(401, {"error": {"code": 401,
            "message": "synthetic-secret-key in request URL", "status": "UNAUTHENTICATED"}})
        provider, _ = self.provider(error=error)
        with self.assertRaises(ProviderConfigurationError) as caught:
            provider.generate(request())
        self.assertEqual(str(caught.exception), "Gemini API returned HTTP 401")
        self.assertEqual(caught.exception.metadata, {"status_code": 401})


if __name__ == "__main__":
    unittest.main()
