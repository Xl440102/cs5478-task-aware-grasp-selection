"""Provider wire contracts and authentication isolation; never calls a live model."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from grasp_c.providers import (CodexProvider, ModelRequest, OpenAIProvider,
                               ProviderConfigurationError, ProviderError, build_provider)


SCHEMA = {"type": "object", "properties": {
    "selected_id": {"type": "string", "enum": ["G02", "G03"]},
    "ranking": {"type": "array", "items": {"type": "string"}}},
    "required": ["selected_id", "ranking"], "additionalProperties": False}


def request(**parameters):
    return ModelRequest("test-model", [{"role": "user", "content": "select JSON"}],
                        SCHEMA, parameters)


class ProviderTests(unittest.TestCase):
    def test_responses_wire_schema_reasoning_and_no_server_storage(self):
        provider = OpenAIProvider()
        response = SimpleNamespace(output_text='{"selected_id":"G02","ranking":["G02","G03"]}',
                                   model="snapshot", usage=None, output=[], status="completed",
                                   id="resp-1", incomplete_details=None, _request_id="req-1")
        provider._client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        result = provider.generate(request(reasoning_effort="low", max_output_tokens=1024))
        wire = provider._client.responses.create.call_args.kwargs
        self.assertFalse(wire["store"])
        self.assertEqual(wire["reasoning"], {"effort": "low"})
        self.assertEqual(wire["text"]["format"]["schema"], SCHEMA)
        self.assertTrue(wire["text"]["format"]["strict"])
        self.assertEqual(result.model, "snapshot")
        self.assertEqual(result.request_id, "req-1")

    def test_compatible_chat_maps_tokens_and_refusal(self):
        provider = OpenAIProvider(provider_type="openai_compatible", api="chat_completions",
                                  base_url="http://localhost:8000/v1")
        response = SimpleNamespace(model="served-model", id="chat-1", usage=None,
                                   choices=[SimpleNamespace(finish_reason="stop", message=
                                       SimpleNamespace(content=None, refusal="cannot comply"))])
        create = Mock(return_value=response)
        provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = provider.generate(request(max_output_tokens=400))
        self.assertEqual(create.call_args.kwargs["max_completion_tokens"], 400)
        self.assertEqual(create.call_args.kwargs["response_format"]["type"], "json_schema")
        self.assertEqual(result.metadata["status"], "refused")

    def test_no_key_fails_before_client_or_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch("grasp_c.env.load_local_env"):
            with self.assertRaisesRegex(ProviderConfigurationError, "OPENAI_API_KEY"):
                OpenAIProvider().preflight()

    def test_reject_secret_config_endpoint_credentials_and_unknown_parameters(self):
        for config in ({"type": "openai", "api_key": "not-a-real-secret"},
                       {"type": "openai", "base_url": "https://user:password@example.org/v1"},
                       {"type": "openai", "base_url": "https://example.org/v1?token=secret"},
                       {"type": "openai", "base_url": "http://example.org/v1"},
                       {"type": "openai", "timeout_s": float("nan")},
                       {"type": "openai_compatible"}):
            with self.subTest(config=config), self.assertRaises(ProviderConfigurationError):
                build_provider(config)
        with self.assertRaisesRegex(ProviderConfigurationError, "Unsupported"):
            OpenAIProvider().generate(request(api_key="secret"))

    def test_environment_seed_is_not_a_responses_api_parameter(self):
        provider = OpenAIProvider()
        provider._client = Mock()
        with self.assertRaisesRegex(ProviderConfigurationError, "decoding seed"):
            provider.generate(request(seed=7))
        provider._client.responses.create.assert_not_called()

    def test_codex_authentication_is_checked_without_reading_credentials(self):
        provider = CodexProvider()
        results = [subprocess.CompletedProcess([], 0, "codex-cli 0.152.0\n", ""),
                   subprocess.CompletedProcess([], 0, "", "Logged in using ChatGPT\n")]
        with patch("grasp_c.providers.shutil.which", return_value="/bin/codex"), \
                patch("grasp_c.providers.subprocess.run", side_effect=results):
            provider.preflight()
        self.assertEqual(provider.identity["cli_version"], "codex-cli 0.152.0")
        self.assertNotIn("token", json.dumps(provider.identity))

    def test_codex_is_empty_directory_stdin_structured_and_records_usage(self):
        provider = CodexProvider()
        provider.version = "codex-cli test"
        provider._authenticated = True

        def run(command, **kwargs):
            cwd = kwargs["cwd"]
            self.assertEqual({p.name for p in cwd.iterdir()}, {"schema.json"})
            self.assertIn("--ignore-user-config", command)
            self.assertIn("--ephemeral", command)
            self.assertIn("read-only", command)
            self.assertIn("features.shell_tool=false", command)
            self.assertIn("project_doc_max_bytes=0", command)
            self.assertEqual(command[-1], "-")
            self.assertIn("select JSON", kwargs["input"])
            Path(command[command.index("--output-last-message") + 1]).write_text(
                '{"selected_id":"G02","ranking":["G02","G03"]}')
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}), "")

        with patch("grasp_c.providers.subprocess.run", side_effect=run):
            result = provider.generate(request(reasoning_effort="low"))
        self.assertEqual(result.usage["input_tokens"], 20)
        self.assertEqual(json.loads(result.text)["selected_id"], "G02")

    def test_codex_tool_calls_excluded_and_timeout_classified(self):
        provider = CodexProvider()
        provider.version = "test"
        provider._authenticated = True
        tool = subprocess.CompletedProcess([], 0, json.dumps({"type": "item.started", "item": {
            "type": "command_execution", "command": "cat oracle.json"}}), "")
        with patch("grasp_c.providers.subprocess.run", return_value=tool):
            with self.assertRaisesRegex(ProviderError, "tool use"):
                provider.generate(request())
        with patch("grasp_c.providers.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 1)):
            with self.assertRaises(ProviderError) as caught:
                provider.generate(request())
        self.assertEqual(caught.exception.code, "timeout")

    def test_codex_model_config_error_is_fatal_and_future_tools_are_rejected(self):
        provider = CodexProvider()
        provider.version, provider._authenticated = "test", True
        failure = subprocess.CompletedProcess([], 1, '{"type":"turn.failed"}', "unsupported model")
        with patch("grasp_c.providers.subprocess.run", return_value=failure):
            with self.assertRaises(ProviderConfigurationError):
                provider.generate(request())
        future_tool = subprocess.CompletedProcess([], 0, json.dumps({"type": "item.completed", "item": {
            "type": "collab_tool_call"}}), "")
        with patch("grasp_c.providers.subprocess.run", return_value=future_tool):
            with self.assertRaisesRegex(ProviderError, "tool use"):
                provider.generate(request())

    def test_codex_cache_identity_resolves_version_once_without_authentication(self):
        provider = CodexProvider()
        version = subprocess.CompletedProcess([], 0, "codex-cli test\n", "")
        with patch("grasp_c.providers.shutil.which", return_value="codex"), \
                patch("grasp_c.providers.subprocess.run", return_value=version) as run:
            self.assertEqual(provider.identity, provider.identity)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(provider._authenticated)

    def test_http_auth_error_is_sanitized_and_fatal(self):
        try:
            import httpx
            from openai import AuthenticationError
        except ImportError:
            self.skipTest("OpenAI SDK not installed")
        provider = OpenAIProvider()
        error = AuthenticationError("secret response text must not appear", response=httpx.Response(
            401, request=httpx.Request("POST", "https://api.openai.com/v1/responses")), body=None)
        create = Mock(side_effect=error)
        provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with self.assertRaises(ProviderConfigurationError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.code, "http_401")
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
