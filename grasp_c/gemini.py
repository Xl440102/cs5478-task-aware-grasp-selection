"""Gemini Developer API through the official, optional google-genai SDK.

Only the provider boundary knows about this SDK. Requests, cache identity and
results retain the same shape as the other C model backends.
"""
from __future__ import annotations

import importlib
import math
import os
import re

from .providers import (ModelResponse, ProviderConfigurationError, ProviderError,
                        _parameters)


def _load_sdk():
    """Import lazily so offline configuration validation needs no model SDK."""
    try:
        return importlib.import_module("google.genai")
    except ImportError:
        raise ProviderConfigurationError(
            "Install requirements-c.txt to use the official google-genai SDK") from None


def _enum_name(value):
    if value is None:
        return None
    return str(getattr(value, "value", value)).rsplit(".", 1)[-1]


def _usage(metadata):
    if metadata is None:
        return {}
    mapping = {
        "input_tokens": "prompt_token_count",
        "output_tokens": "candidates_token_count",
        "total_tokens": "total_token_count",
        "cached_input_tokens": "cached_content_token_count",
        "reasoning_output_tokens": "thoughts_token_count",
    }
    # Preserve absent counts as absent, rather than reporting invented zeroes.
    return {name: getattr(metadata, source) for name, source in mapping.items()
            if getattr(metadata, source, None) is not None}


class GeminiProvider:
    """Stateless text/JSON selection with Gemini's native content protocol."""

    def __init__(self, *, api_key_env="GEMINI_API_KEY", timeout_s=60, max_retries=2):
        if not isinstance(api_key_env, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ProviderConfigurationError("api_key_env must name an environment variable")
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ProviderConfigurationError("timeout_s must be positive and finite")
        if type(max_retries) is not int or max_retries < 0:
            raise ProviderConfigurationError("max_retries must be a nonnegative integer")
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._client = None

    @property
    def identity(self):
        return {"type": "gemini", "adapter_version": "google_genai_v1",
                "api": "generate_content", "backend": "gemini_developer_api",
                "sdk": "google-genai", "api_key_env": self.api_key_env,
                "timeout_s": self.timeout_s, "max_retries": self.max_retries,
                "structured_outputs": True}

    def preflight(self):
        from .env import load_local_env
        load_local_env()
        if not os.environ.get(self.api_key_env):
            raise ProviderConfigurationError(
                f"Set {self.api_key_env} in your local environment to use Gemini")
        _load_sdk()

    def _get_client(self, sdk):
        if self._client is None:
            self.preflight()
            self._client = sdk.Client(
                api_key=os.environ[self.api_key_env],
                # Explicitly select the Developer API even if the user's shell
                # also contains GOOGLE_GENAI_USE_VERTEXAI or GOOGLE_API_KEY.
                vertexai=False,
                http_options=sdk.types.HttpOptions(
                    timeout=max(1, int(self.timeout_s * 1000)),
                    retry_options=sdk.types.HttpRetryOptions(attempts=self.max_retries + 1)))
        return self._client

    def generate(self, request):
        parameters = _parameters(request.parameters, {
            "temperature", "top_p", "max_output_tokens", "thinking_budget", "thinking_level", "seed"})
        if "thinking_budget" in parameters and "thinking_level" in parameters:
            raise ProviderConfigurationError("Set only one of thinking_budget or thinking_level")
        budget = parameters.pop("thinking_budget", None)
        level = parameters.pop("thinking_level", None)
        if budget is not None and (type(budget) is not int or budget < -1):
            raise ProviderConfigurationError("thinking_budget must be an integer >= -1")
        if level is not None and (not isinstance(level, str) or
                                  level not in {"minimal", "low", "medium", "high"}):
            raise ProviderConfigurationError("thinking_level must be minimal, low, medium or high")
        sdk = _load_sdk()
        systems, contents = [], []
        for message in request.messages:
            role, content = message.get("role"), message.get("content")
            if not isinstance(content, str):
                raise ProviderConfigurationError("Gemini selection messages must contain text strings")
            if role == "system":
                systems.append(content)
            elif role in {"user", "assistant"}:
                contents.append(sdk.types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=[sdk.types.Part.from_text(text=content)]))
            else:
                raise ProviderConfigurationError("Gemini messages support system, user and assistant roles")
        if not contents:
            raise ProviderConfigurationError("Gemini requires at least one user or assistant message")
        config = {"response_mime_type": "application/json",
                  "response_json_schema": request.schema, **parameters}
        if systems:
            config["system_instruction"] = "\n\n".join(systems)
        if budget is not None:
            config["thinking_config"] = sdk.types.ThinkingConfig(
                thinking_budget=budget, include_thoughts=False)
        elif level is not None:
            config["thinking_config"] = sdk.types.ThinkingConfig(
                thinking_level=level, include_thoughts=False)
        try:
            response = self._get_client(sdk).models.generate_content(
                model=request.model, contents=contents,
                config=sdk.types.GenerateContentConfig(**config))
        except ProviderError:
            raise
        except Exception as exc:
            # Native exception messages may include request URLs or credentials.
            # Expose only a stable category and numeric HTTP status.
            code = getattr(exc, "code", None)
            if isinstance(code, int) and 100 <= code <= 599:
                error = ProviderConfigurationError if 400 <= code < 500 and code not in {
                    408, 409, 425, 429} else ProviderError
                raise error(f"Gemini API returned HTTP {code}", code=f"http_{code}",
                            metadata={"status_code": code}) from None
            if isinstance(exc, (ValueError, TypeError)):
                raise ProviderConfigurationError(
                    "Gemini SDK rejected the request configuration", code="invalid_request") from None
            raise ProviderError("Gemini API connection failed or timed out",
                                code="connection_error") from None

        candidates = getattr(response, "candidates", None) or []
        finish = _enum_name(getattr(candidates[0], "finish_reason", None)) if candidates else None
        feedback = getattr(response, "prompt_feedback", None)
        blocked = _enum_name(getattr(feedback, "block_reason", None))
        blocked = blocked if blocked not in {None, "BLOCK_REASON_UNSPECIFIED"} else None
        refusal_finishes = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT",
                            "SPII", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}
        status = ("refused" if blocked or finish in refusal_finishes else
                  "completed" if candidates and finish == "STOP" else "incomplete")
        # response.text contains only answer text; thoughts are never requested
        # or included in logs. A blocked response commonly has text=None.
        text = response.text or ""
        metadata = {"status": status, "backend": "gemini_developer_api",
                    "finish_reason": finish, "prompt_block_reason": blocked,
                    "response_id": getattr(response, "response_id", None)}
        if not candidates:
            metadata["incomplete_reason"] = "no_candidates" if not blocked else "prompt_blocked"
        elif status == "incomplete":
            metadata["incomplete_reason"] = finish or "unspecified_finish_reason"
        return ModelResponse(text=text,
                             model=getattr(response, "model_version", None) or request.model,
                             usage=_usage(getattr(response, "usage_metadata", None)),
                             request_id=getattr(response, "response_id", None),
                             metadata=metadata)
