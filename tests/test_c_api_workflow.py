"""Team-local API credentials and smoke examples; never calls an API or physics."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.machinery
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from grasp_c import env
from grasp_c.config import validate_config
from grasp_c.providers import (ModelRequest, ModelResponse, OpenAIProvider,
                               ProviderConfigurationError, build_provider)
from grasp_c.selectors import LLMSelector

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CANDIDATES = [
    {"id": "G02", "region": "body", "approach": "side", "gripper_width": .078,
     "geometry_score": .66},
    {"id": "G03", "region": "body", "approach": "side", "gripper_width": .078,
     "geometry_score": .60},
]
ANSWER = '{"selected_id":"G02","ranking":["G02","G03"]}'


class LocalEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "project" / ".env"
        self.path.parent.mkdir()
        self.project_env = patch.object(env, "PROJECT_ENV", self.path)
        self.project_env.start()
        self.addCleanup(self.project_env.stop)

    def test_only_explicit_project_file_is_loaded(self):
        (self.directory / ".env").write_text("UNEXPECTED_PARENT_KEY=parent-value\n")
        self.path.write_text("PROJECT_TEST_KEY=project-value\n")
        with patch.dict(os.environ, {}, clear=True), patch(
                "dotenv.find_dotenv", side_effect=AssertionError("must not discover other files")):
            env.load_local_env()
            self.assertEqual(os.environ.get("PROJECT_TEST_KEY"), "project-value")
            self.assertNotIn("UNEXPECTED_PARENT_KEY", os.environ)

    def test_exported_keys_win_and_file_values_remain_literal(self):
        self.path.write_text("OPENAI_API_KEY=file-test-key\n"
                             "GEMINI_API_KEY=${SHOULD_NOT_EXPAND}\n")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "exported-test-key",
                                     "SHOULD_NOT_EXPAND": "unexpected-expansion"}, clear=True):
            env.load_local_env()
            self.assertEqual(os.environ["OPENAI_API_KEY"], "exported-test-key")
            self.assertEqual(os.environ["GEMINI_API_KEY"], "${SHOULD_NOT_EXPAND}")

    def test_missing_project_file_needs_no_dotenv_and_does_not_search_parents(self):
        (self.directory / ".env").write_text("UNEXPECTED_PARENT_KEY=parent-value\n")
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"dotenv": None}):
            env.load_local_env()
            self.assertNotIn("UNEXPECTED_PARENT_KEY", os.environ)

    def test_existing_file_without_dotenv_reports_installation_not_file_contents(self):
        self.path.write_text("OPENAI_API_KEY=synthetic-private-value\n")
        with patch.dict(sys.modules, {"dotenv": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements-c.txt") as caught:
                env.load_local_env()
        self.assertNotIn("synthetic-private-value", str(caught.exception))


class OfflineGeminiConfigurationTests(unittest.TestCase):
    def config(self, parameters=None):
        return {"candidates": str(ROOT / "candidates.example.json"), "seeds": [10],
                "tasks": ["pour_prep"], "methods": [
                    {"id": "M3_GEMINI", "kind": "llm", "model": "gemini-3.8-flash",
                     "provider": {"type": "gemini", "api_key_env": "GEMINI_API_KEY",
                                  "timeout_s": 30, "max_retries": 0},
                     "parameters": parameters or {"thinking_level": "low", "max_output_tokens": 1024}}]}

    def test_factory_and_validation_need_no_credentials_env_file_or_google_sdk(self):
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"google.genai": None}), \
                patch("grasp_c.env.load_local_env", side_effect=AssertionError("offline env load")), \
                patch("grasp_c.gemini._load_sdk", side_effect=AssertionError("offline SDK import")):
            provider = build_provider({"type": "gemini"})
            self.assertEqual(provider.identity["type"], "gemini")
            validated = validate_config(self.config())
        self.assertEqual(validated["methods"][0]["provider"]["api_key_env"], "GEMINI_API_KEY")
        self.assertEqual(validated["methods"][0]["variant"], "M3")

    def test_native_gemini_parameters_are_checked_before_any_sdk_import(self):
        invalid = ({"reasoning_effort": "low"}, {"thinking_budget": True},
                   {"thinking_budget": -2}, {"thinking_budget": "1024"},
                   {"temperature": True}, {"temperature": 3}, {"top_p": -.1},
                   {"max_output_tokens": 0}, {"seed": True})
        with patch("grasp_c.gemini._load_sdk", side_effect=AssertionError("offline SDK import")):
            for parameters in invalid:
                with self.subTest(parameters=parameters), self.assertRaises(
                        (ValueError, ProviderConfigurationError)):
                    validate_config(self.config(parameters))
            validated = validate_config(self.config({"temperature": .2, "top_p": .9,
                "thinking_budget": -1, "max_output_tokens": 512, "seed": 9}))
        self.assertEqual(validated["methods"][0]["parameters"]["thinking_budget"], -1)

    def test_gemini_thinking_level_is_an_enum_and_excludes_thinking_budget(self):
        invalid = [{"thinking_level": value} for value in ("", "LOW", "unknown", True, None, 1)]
        invalid.extend({"thinking_level": "low", "thinking_budget": budget} for budget in (-1, 0, 1024))
        with patch("grasp_c.gemini._load_sdk", side_effect=AssertionError("offline SDK import")):
            for parameters in invalid:
                with self.subTest(parameters=parameters), self.assertRaises(
                        (ValueError, ProviderConfigurationError)):
                    validate_config(self.config(parameters))
            for level in ("minimal", "low", "medium", "high"):
                with self.subTest(level=level):
                    validated = validate_config(self.config({"thinking_level": level}))
                    self.assertEqual(validated["methods"][0]["parameters"]["thinking_level"], level)


class OpenAICredentialBoundaryTests(unittest.TestCase):
    def test_custom_environment_key_reaches_only_sdk_and_never_selection_logs(self):
        sdk = ModuleType("openai")
        sdk.__spec__ = importlib.machinery.ModuleSpec("openai", loader=None)
        response = SimpleNamespace(output_text=ANSWER, model="api-snapshot", usage=None,
                                   output=[], status="completed", id="response-test",
                                   incomplete_details=None, _request_id="request-test")
        create = Mock(return_value=response)
        sdk.OpenAI = Mock(return_value=SimpleNamespace(
            responses=SimpleNamespace(create=create)))
        provider = OpenAIProvider(api_key_env="TEAM_OPENAI_API_KEY")
        exported = {"TEAM_OPENAI_API_KEY": "synthetic-selected-key",
                    "OPENAI_API_KEY": "synthetic-unselected-openai-key",
                    "GEMINI_API_KEY": "synthetic-unselected-gemini-key"}
        with patch.dict(os.environ, exported, clear=True), patch.dict(sys.modules, {"openai": sdk}), \
                patch("grasp_c.env.load_local_env"):
            selection = LLMSelector(provider, "test-model", cache_mode="off")(
                "pour_prep", deepcopy(PUBLIC_CANDIDATES), 0)
        self.assertEqual(sdk.OpenAI.call_args.kwargs["api_key"], exported["TEAM_OPENAI_API_KEY"])
        self.assertEqual(selection["selected_id"], "G02")
        self.assertFalse(selection["metadata"]["fallback_to_geometry"])
        self.assertEqual(create.call_count, 1)
        logged = json.dumps({"identity": provider.identity, "selection": selection})
        for value in exported.values():
            self.assertNotIn(value, logged)


class TeamSmokeExampleTests(unittest.TestCase):
    def setUp(self):
        from examples import llm_smoke
        self.example = llm_smoke
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        bridge = ModuleType("examples.evaluate_one_trial")
        self.evaluate = Mock(side_effect=self.fake_evaluate)
        bridge.evaluate_trial = self.evaluate
        modules = patch.dict(sys.modules, {"examples.evaluate_one_trial": bridge})
        modules.start()
        self.addCleanup(modules.stop)

    @staticmethod
    def fake_evaluate(task, seed, method, candidates, selector, **kwargs):
        selection = selector(task, deepcopy(PUBLIC_CANDIDATES), seed)
        return {"status": "completed", "selected_id": selection["selected_id"],
                "selection": selection, "filter": {"feasible_candidates": PUBLIC_CANDIDATES},
                "episode": {"success": True, "collision": False, "dropped": False},
                "error": None}

    def fake_provider(self, provider_type):
        provider = Mock()
        provider.identity = {"type": provider_type, "api_key_env":
                             "GEMINI_API_KEY" if provider_type == "gemini" else "OPENAI_API_KEY"}
        provider.generate.return_value = ModelResponse(
            ANSWER, "test-snapshot", {"input_tokens": 10, "output_tokens": 5},
            metadata={"status": "completed"})
        return provider

    def test_default_example_uses_direct_openai_and_a_fresh_call(self):
        self.check_successful_example("openai", [], "gpt-6-luna",
                                      {"reasoning_effort": "low", "max_output_tokens": 1024})

    def test_gemini_example_uses_native_provider_with_its_own_defaults(self):
        self.check_successful_example("gemini", ["--provider", "gemini"], "gemini-3.8-flash",
                                      {"thinking_level": "low", "max_output_tokens": 1024})

    def check_successful_example(self, provider_type, arguments, model, parameters):
        provider = self.fake_provider(provider_type)
        out = self.directory / f"{provider_type}.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.example, "build_provider", return_value=provider) as factory, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = self.example.main(arguments + ["--out", str(out)])
        self.assertEqual(status, 0, stderr.getvalue())
        factory.assert_called_once_with({"type": provider_type})
        provider.preflight.assert_called_once_with()
        provider.generate.assert_called_once()
        request = provider.generate.call_args.args[0]
        self.assertIsInstance(request, ModelRequest)
        self.assertEqual(request.model, model)
        self.assertEqual(request.parameters, parameters)
        reported = json.loads(stdout.getvalue())
        saved = json.loads(out.read_text())
        self.assertTrue(reported["fresh_llm_response_verified"])
        self.assertEqual(reported["provider"], provider_type)
        self.assertFalse(saved["selection"]["metadata"]["cache_hit"])
        self.assertEqual(saved["selection"]["metadata"]["live_requests"], 1)

    def test_missing_api_key_stops_before_physics_and_never_uses_codex(self):
        for provider_type, variable in (("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")):
            with self.subTest(provider_type=provider_type):
                out = self.directory / f"missing-{provider_type}.json"
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.dict(os.environ, {}, clear=True), \
                        patch.object(env, "PROJECT_ENV", self.directory / "absent.env"), \
                        patch("grasp_c.providers.CodexProvider.preflight", side_effect=AssertionError(
                            "must never use ChatGPT login as API fallback")) as codex, \
                        patch("grasp_c.providers.subprocess.run", side_effect=AssertionError(
                            "must never invoke CLI")), redirect_stdout(stdout), redirect_stderr(stderr):
                    status = self.example.main(["--provider", provider_type, "--out", str(out)])
                self.assertEqual(status, 2)
                self.assertIn(variable, json.loads(stderr.getvalue())["message"])
                self.evaluate.assert_not_called()
                codex.assert_not_called()
                self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
