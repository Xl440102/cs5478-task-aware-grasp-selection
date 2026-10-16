"""Small provider boundary: no simulator, candidate oracle, or credentials in logs."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Protocol
from urllib.parse import urlsplit


@dataclass
class ModelRequest:
    model: str
    messages: list[dict]
    schema: dict
    parameters: dict = field(default_factory=dict)


@dataclass
class ModelResponse:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    request_id: str | None = None
    metadata: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    def __init__(self, message, *, code="provider_error", transient=True, metadata=None):
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.fatal = not transient
        self.metadata = metadata or {}


class ProviderConfigurationError(ProviderError):
    def __init__(self, message, *, code="configuration_error", metadata=None):
        super().__init__(message, code=code, transient=False, metadata=metadata)


class LLMProvider(Protocol):
    @property
    def identity(self) -> dict: ...

    def preflight(self) -> None: ...

    def generate(self, request: ModelRequest) -> ModelResponse: ...


def _endpoint(value):
    parts = urlsplit(value)
    if (parts.username or parts.password or parts.query or parts.fragment or
            not parts.hostname or
            (parts.scheme != "https" and not (
                parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ProviderConfigurationError("base_url must be HTTPS (HTTP is allowed only for localhost), without credentials/query")
    return value.rstrip("/")


def _parameters(parameters, allowed):
    unknown = set(parameters) - set(allowed)
    if unknown:
        raise ProviderConfigurationError(f"Unsupported model parameters: {sorted(unknown)}")
    try:
        json.dumps(parameters, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ProviderConfigurationError("Model parameters must be finite JSON values") from exc
    return dict(parameters)


class OpenAIProvider:
    """Official Responses API or an explicitly configured compatible endpoint."""
    def __init__(self, *, provider_type="openai", api="responses", api_key_env="OPENAI_API_KEY",
                 base_url=None, timeout_s=60, max_retries=2, structured_outputs=True):
        if api not in {"responses", "chat_completions"}:
            raise ProviderConfigurationError("api must be responses or chat_completions")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ProviderConfigurationError("api_key_env must name an environment variable")
        self.provider_type, self.api, self.api_key_env = provider_type, api, api_key_env
        self.base_url = _endpoint(base_url or "https://api.openai.com/v1")
        self.timeout_s, self.max_retries = timeout_s, max_retries
        self.structured_outputs = structured_outputs
        self._client = None

    @property
    def identity(self):
        return {"type": self.provider_type, "adapter_version": "openai_v1",
                "api": self.api, "base_url": self.base_url,
                "api_key_env": self.api_key_env, "timeout_s": self.timeout_s,
                "max_retries": self.max_retries, "structured_outputs": self.structured_outputs}

    def preflight(self):
        from .env import load_local_env
        load_local_env()
        if not os.environ.get(self.api_key_env):
            raise ProviderConfigurationError(
                f"Set {self.api_key_env} in your local environment or the project's ignored .env file")
        if importlib.util.find_spec("openai") is None:
            raise ProviderConfigurationError("Install requirements-c.txt to use the OpenAI SDK")

    def _get_client(self):
        if self._client is None:
            self.preflight()
            from openai import OpenAI
            self._client = OpenAI(api_key=os.environ[self.api_key_env], base_url=self.base_url,
                                  timeout=self.timeout_s, max_retries=self.max_retries)
        return self._client

    def generate(self, request):
        parameters = _parameters(request.parameters, {
            "temperature", "top_p", "max_output_tokens", "reasoning_effort", "seed"})
        client = self._get_client()
        try:
            if self.api == "responses":
                if "seed" in parameters:
                    raise ProviderConfigurationError("Responses API has no decoding seed; environment seed is logged separately")
                effort = parameters.pop("reasoning_effort", None)
                if effort is not None:
                    parameters["reasoning"] = {"effort": effort}
                if self.structured_outputs:
                    parameters["text"] = {"format": {"type": "json_schema", "name": "grasp_selection",
                                                       "strict": True, "schema": request.schema}}
                response = client.responses.create(model=request.model, input=request.messages,
                                                   store=False, **parameters)
                refusals = [getattr(content, "refusal", "") for item in response.output
                            for content in (getattr(item, "content", None) or [])
                            if getattr(content, "type", None) == "refusal"]
                return ModelResponse(response.output_text or "", response.model,
                                     response.usage.model_dump(mode="json") if response.usage else {},
                                     getattr(response, "_request_id", None),
                                     {"status": "refused" if refusals else response.status,
                                      "refusals": refusals,
                                      "response_id": response.id,
                                      "incomplete_details": response.incomplete_details.model_dump(mode="json")
                                      if response.incomplete_details else None})
            max_tokens = parameters.pop("max_output_tokens", None)
            if max_tokens is not None:
                parameters["max_completion_tokens"] = max_tokens
            if self.structured_outputs:
                parameters["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "grasp_selection", "strict": True, "schema": request.schema}}
            response = client.chat.completions.create(model=request.model, messages=request.messages,
                                                      **parameters)
            choice = response.choices[0]
            refused = bool(getattr(choice.message, "refusal", None))
            status = "refused" if refused else ("completed" if choice.finish_reason == "stop" else "incomplete")
            return ModelResponse(choice.message.content or "", response.model,
                                 response.usage.model_dump(mode="json") if response.usage else {},
                                 getattr(response, "_request_id", None),
                                 {"status": status, "response_id": response.id,
                                  "refusal": getattr(choice.message, "refusal", None),
                                  "finish_reason": choice.finish_reason})
        except ProviderError:
            raise
        except Exception as exc:
            # SDK exception messages can echo response bodies or endpoint credentials.
            # Preserve diagnostic category/status without serializing their contents.
            from openai import APIConnectionError, APIStatusError, APITimeoutError
            if isinstance(exc, APIStatusError):
                status = exc.status_code
                error_type = ProviderConfigurationError if status in {400, 401, 402, 403, 404, 422} else ProviderError
                raise error_type(f"LLM API returned HTTP {status}", code=f"http_{status}",
                                 metadata={"status_code": status, "request_id": getattr(exc, "request_id", None)}) from exc
            if isinstance(exc, (APIConnectionError, APITimeoutError)):
                raise ProviderError("LLM API connection failed or timed out", code="connection_error") from exc
            raise


class CodexProvider:
    """Use supported codex exec with the user's existing ChatGPT CLI session.

    Runs in an empty temporary working directory, disables project instructions,
    shell, search, image and multi-agent tools; rejects tool calls in its event log.
    Codex supplies its own system instructions, so this backend is labeled separately
    from direct OpenAI API experiments.
    """
    def __init__(self, *, executable="codex", timeout_s=120):
        self.executable = executable
        self.timeout_s = timeout_s
        self.version = None
        self._authenticated = False

    @property
    def identity(self):
        # Resolve only the public CLI version, without requiring model credentials.
        # The identity must not change between the first cache lookup and inference.
        self._resolve_version()
        return {"type": "codex", "executable": self.executable, "cli_version": self.version,
                "timeout_s": self.timeout_s, "isolation": "empty_cwd_no_tools_v1",
                "auth": "existing_chatgpt_cli_login"}

    def _resolve_version(self):
        if self.version is not None:
            return
        if not shutil.which(self.executable):
            raise ProviderConfigurationError("Codex CLI missing; install it and run codex login")
        try:
            version = subprocess.run([self.executable, "--version"], capture_output=True,
                                     text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderConfigurationError("Could not check Codex CLI version") from exc
        if version.returncode:
            raise ProviderConfigurationError("Could not determine Codex CLI version")
        self.version = version.stdout.strip()

    @staticmethod
    def _chatgpt_environment():
        # This backend is explicitly for the saved ChatGPT login. API keys may
        # coexist in the parent process for separately configured API methods.
        child_env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            child_env.pop(key, None)
        return child_env

    def preflight(self):
        self._resolve_version()
        try:
            auth = subprocess.run([self.executable, "login", "status"], capture_output=True,
                                  text=True, timeout=15, check=False,
                                  env=self._chatgpt_environment())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderConfigurationError("Could not check Codex CLI authentication") from exc
        # Do not read/copy auth.json or return account/session tokens.
        if auth.returncode or "chatgpt" not in (auth.stdout + auth.stderr).lower():
            raise ProviderConfigurationError("Run codex login with your ChatGPT account before using provider codex")
        self._authenticated = True

    def generate(self, request):
        parameters = _parameters(request.parameters, {"reasoning_effort"})
        if not self._authenticated:
            self.preflight()
        with tempfile.TemporaryDirectory(prefix="grasp-c-llm-") as temporary:
            directory = Path(temporary)
            schema_path, output_path = directory / "schema.json", directory / "response.json"
            schema_path.write_text(json.dumps(request.schema, allow_nan=False), encoding="utf-8")
            command = [self.executable, "exec", "--ignore-user-config", "--ephemeral",
                       "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                       "--color", "never", "--model", request.model,
                       "--output-schema", str(schema_path), "--output-last-message", str(output_path)]
            for setting in ("features.shell_tool=false", "features.multi_agent=false",
                            "features.shell_snapshot=false", "tools.view_image=false",
                            'web_search="disabled"', "project_doc_max_bytes=0"):
                command.extend(["-c", setting])
            if "reasoning_effort" in parameters:
                command.extend(["-c", "model_reasoning_effort=" + json.dumps(parameters["reasoning_effort"])])
            command.append("-")
            prompt = ("You are acting only as a stateless JSON selection model. Use only the supplied messages. "
                      "Do not use tools, inspect files, or retrieve outside information. "
                      "Return the requested JSON object.\n" + json.dumps(request.messages, ensure_ascii=False))
            try:
                result = subprocess.run(command, input=prompt, cwd=directory, capture_output=True,
                                        text=True, timeout=self.timeout_s, check=False,
                                        env=self._chatgpt_environment())
            except subprocess.TimeoutExpired as exc:
                raise ProviderError("Codex inference timed out", code="timeout") from exc
            except OSError as exc:
                raise ProviderConfigurationError("Could not start Codex CLI") from exc
            events = []
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
            safe_item_types = {"agent_message", "reasoning", "plan", "todo_list", "error"}
            if any(e.get("item", {}).get("type") not in safe_item_types
                   for e in events if isinstance(e.get("item"), dict)):
                raise ProviderError("Codex attempted tool use; response excluded from evaluation", code="tool_use")
            if result.returncode or any(e.get("type") == "turn.failed" for e in events):
                diagnostic = result.stderr + "\n" + result.stdout
                transient = any(term in diagnostic.lower() for term in (
                    "rate_limit", "rate limit", "too many requests", "timed out", "connection reset",
                    "connection refused", "temporarily unavailable", '"status":429',
                    '"status": 429', '"status":500', '"status": 500', '"status":503', '"status": 503'))
                error = ProviderError if transient else ProviderConfigurationError
                raise error("Codex inference failed; verify configured model/account access with codex exec", code="cli_error",
                            metadata={"returncode": result.returncode})
            if not output_path.is_file():
                raise ProviderError("Codex returned no final JSON response", code="empty_response")
            usage = next((e.get("usage", {}) for e in reversed(events) if e.get("type") == "turn.completed"), {})
            thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
            return ModelResponse(output_path.read_text(encoding="utf-8"), request.model, usage, thread_id,
                                 {"status": "completed", "cli_version": self.version,
                                  "backend": "codex_cli", "model_is_requested_id": True})


class MockProvider:
    """Deterministic plumbing smoke test. Never evidence of LLM performance."""
    @property
    def identity(self):
        return {"type": "mock", "version": "first_schema_id_v1"}

    def preflight(self):
        pass

    def generate(self, request):
        ids = request.schema["properties"]["selected_id"]["enum"]
        return ModelResponse(json.dumps({"selected_id": ids[0], "ranking": ids}), "mock",
                             metadata={"status": "completed", "synthetic": True})


def build_provider(config: dict) -> LLMProvider:
    """Add provider adapters here; selectors/evaluator do not depend on SDK types."""
    kind = config.get("type", "openai")
    options = {k: v for k, v in config.items() if k != "type"}
    allowed = {"openai": {"api", "api_key_env", "base_url", "timeout_s", "max_retries", "structured_outputs"},
               "openai_compatible": {"api", "api_key_env", "base_url", "timeout_s", "max_retries", "structured_outputs"},
               "gemini": {"api_key_env", "timeout_s", "max_retries"},
               "codex": {"executable", "timeout_s"}, "mock": set()}
    if kind not in allowed or set(options) - allowed[kind]:
        raise ProviderConfigurationError("Unknown provider type or provider options; credentials must use api_key_env")
    if "timeout_s" in options and (type(options["timeout_s"]) not in {int, float}
                                     or not math.isfinite(options["timeout_s"]) or options["timeout_s"] <= 0):
        raise ProviderConfigurationError("timeout_s must be a positive finite number")
    if "max_retries" in options and (type(options["max_retries"]) is not int or options["max_retries"] < 0):
        raise ProviderConfigurationError("max_retries must be a nonnegative integer")
    if "structured_outputs" in options and type(options["structured_outputs"]) is not bool:
        raise ProviderConfigurationError("structured_outputs must be boolean")
    if kind in {"openai", "openai_compatible"}:
        if kind == "openai_compatible" and "base_url" not in options:
            raise ProviderConfigurationError("openai_compatible requires explicit base_url")
        options.setdefault("api", "responses" if kind == "openai" else "chat_completions")
        return OpenAIProvider(provider_type=kind, **options)
    if kind == "codex":
        return CodexProvider(**options)
    if kind == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(**options)
    return MockProvider()
