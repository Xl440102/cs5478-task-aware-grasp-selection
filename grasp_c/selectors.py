"""Task-aware LLM ranking, one format repair, and explicit geometry fallback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import random
from time import perf_counter

from grasp_a.contracts import validate_task_id
from grasp_c.cache import ResponseCache, canonical_json
from grasp_c.prompts import (PROMPT_VERSIONS, build_messages, public_snapshot,
                             repair_messages, selection_schema)
from grasp_c.providers import (ModelRequest, ModelResponse, ProviderConfigurationError,
                               ProviderError)


def geometry_selector(task_id: str, candidates: list[dict], seed: int) -> dict:
    """Pilot geometry reference, pending B's official score and M1 definition."""
    validate_task_id(task_id)
    public = public_snapshot(candidates)
    ranking = [item["id"] for item in sorted(
        public, key=lambda item: (-item["geometry_score"], item["id"]))]
    return {"selected_id": ranking[0], "ranking": ranking,
            "metadata": {"method_id": "reference_geometry", "fallback_method": "geometry_reference",
                         "score_scope": "pilot_input", "tie_break": "candidate_id_ascending"}}


def random_selector(task_id: str, candidates: list[dict], seed: int) -> dict:
    """Reference random selector using a local RNG, independent of simulation."""
    validate_task_id(task_id)
    public = public_snapshot(candidates)
    ranking = [item["id"] for item in public]
    random.Random(seed).shuffle(ranking)
    return {"selected_id": ranking[0], "ranking": ranking,
            "metadata": {"method_id": "reference_random", "selection_seed": seed}}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def validate_response(raw_text: str, candidate_ids: list[str]) -> tuple[dict | None, list[str]]:
    """Reject prose, duplicate keys, foreign IDs and partial/duplicate rankings."""
    try:
        parsed = json.loads(raw_text, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        return None, [f"invalid_json: {exc}"]
    if not isinstance(parsed, dict):
        return None, ["response must be a JSON object"]
    errors = []
    if set(parsed) != {"selected_id", "ranking"}:
        errors.append("response must have exactly selected_id and ranking")
    selected = parsed.get("selected_id")
    if not isinstance(selected, str) or selected not in candidate_ids:
        errors.append("selected_id must be one of the supplied candidate IDs")
    ranking = parsed.get("ranking")
    if (not isinstance(ranking, list) or not all(isinstance(x, str) for x in ranking)
            or len(ranking) != len(candidate_ids) or set(ranking) != set(candidate_ids)):
        errors.append("ranking must contain every supplied candidate ID exactly once")
    elif ranking[0] != selected:
        errors.append("ranking[0] must equal selected_id")
    if errors:
        return None, errors
    return {"selected_id": selected, "ranking": ranking}, []


def _response_failure(response: ModelResponse) -> str | None:
    status = response.metadata.get("status", "completed")
    if status in ("refused", "refusal") or response.metadata.get("refusal"):
        return "model_refusal"
    if status != "completed":
        return f"model_response_{status}"
    return None


class LLMSelector:
    """Provider-agnostic selector; transport setup errors deliberately propagate.

    A1 removes the task ID and description from both the initial prompt and repair.
    The environment seed is recorded in cache identity, never automatically sent
    as a model decoding parameter. sample_id separates independent repetitions.
    """

    def __init__(self, provider, model_id: str, *, variant: str = "M3",
                 parameters: dict | None = None, cache_dir=None,
                 cache_mode: str = "read_write", prompt_version: str = "v1",
                 sample_id: int = 0):
        if variant not in ("M3", "A1"):
            raise ValueError("LLM variant must be M3 or A1")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id must be a nonempty string")
        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"unsupported prompt version: {prompt_version}")
        if type(sample_id) is not int or sample_id < 0:
            raise ValueError("sample_id must be a nonnegative integer")
        if parameters is not None and not isinstance(parameters, dict):
            raise ValueError("parameters must be a JSON-compatible dictionary")
        self.provider = provider
        self.model_id = model_id
        self.variant = variant
        self.parameters = deepcopy(parameters) if parameters is not None else {}
        canonical_json(self.parameters)
        self.cache = ResponseCache(cache_dir, cache_mode)
        self.prompt_version = prompt_version
        self.sample_id = sample_id

    def _generate(self, messages: list[dict], schema: dict, seed: int,
                  phase: str) -> tuple[ModelResponse | None, dict]:
        request = ModelRequest(model=self.model_id, messages=deepcopy(messages),
                               schema=deepcopy(schema), parameters=deepcopy(self.parameters))
        identity = deepcopy({
            "provider": self.provider.identity, "model": self.model_id,
            "messages": request.messages, "schema": request.schema,
            "parameters": request.parameters, "prompt_version": self.prompt_version,
            "seed": seed, "sample_id": self.sample_id, "phase": phase,
        })
        started = perf_counter()
        key, cached = self.cache.lookup(identity)
        call = {"phase": phase, "cache_key": key, "cache_hit": cached is not None,
                "latency_s": None, "model": self.model_id, "request_id": None,
                "usage": {}, "response_metadata": {}, "raw_text": None,
                "validation": None, "error": None}
        try:
            response = ModelResponse(**cached) if cached is not None else self.provider.generate(request)
        except ProviderConfigurationError:
            raise
        except (ProviderError, TimeoutError, ConnectionError) as exc:
            if getattr(exc, "fatal", False):
                raise
            call["latency_s"] = perf_counter() - started
            call["error"] = {"type": type(exc).__name__, "message": str(exc),
                             "code": getattr(exc, "code", "transport_error"),
                             "transient": getattr(exc, "transient", True),
                             "metadata": deepcopy(getattr(exc, "metadata", {}))}
            return None, call
        call["latency_s"] = perf_counter() - started
        if not isinstance(response, ModelResponse):
            raise TypeError("provider.generate must return ModelResponse")
        if (not isinstance(response.text, str) or not isinstance(response.model, str)
                or not isinstance(response.usage, dict) or not isinstance(response.metadata, dict)
                or (response.request_id is not None and not isinstance(response.request_id, str))):
            raise TypeError("provider returned an invalid ModelResponse")
        # Fail before any physical execution if provider metadata cannot be logged.
        encoded_response = asdict(response)
        canonical_json(encoded_response)
        failure = _response_failure(response)
        if cached is None and failure is None:
            self.cache.store(identity, encoded_response)
        call.update(model=response.model, request_id=response.request_id,
                    usage=deepcopy(response.usage), response_metadata=deepcopy(response.metadata),
                    raw_text=response.text)
        if failure:
            call["error"] = {"type": "ModelResponseError", "code": failure,
                             "message": "provider returned an unsuccessful model response",
                             "transient": failure != "model_refusal"}
        return response, call

    def __call__(self, task_id: str, public_candidates: list[dict], seed: int) -> dict:
        validate_task_id(task_id)
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        public = public_snapshot(public_candidates)
        ids = [candidate["id"] for candidate in public]
        schema = selection_schema(ids)
        messages = build_messages(task_id, public, variant=self.variant,
                                  prompt_version=self.prompt_version)
        calls = []
        selection = None
        repair_count = 0
        response, call = self._generate(messages, schema, seed, "initial")
        calls.append(call)
        if response is not None and call["error"] is None:
            selection, errors = validate_response(response.text, ids)
            call["validation"] = {"valid": selection is not None, "errors": errors}
            if selection is None:
                repair_count = 1
                repaired, repair_call = self._generate(
                    repair_messages(messages, response.text, errors), schema, seed, "repair")
                calls.append(repair_call)
                if repaired is not None and repair_call["error"] is None:
                    selection, errors = validate_response(repaired.text, ids)
                    repair_call["validation"] = {"valid": selection is not None,
                                                 "errors": errors}
        fallback = selection is None
        if fallback:
            baseline = geometry_selector(task_id, public, seed)
            selection = {"selected_id": baseline["selected_id"], "ranking": baseline["ranking"]}
        usage = {}
        live_usage = {}
        for item in calls:
            for name, value in item["usage"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[name] = usage.get(name, 0) + value
                    if not item["cache_hit"]:
                        live_usage[name] = live_usage.get(name, 0) + value
        metadata = {
            "method_id": self.variant, "model_id": self.model_id,
            "provider_identity": deepcopy(self.provider.identity),
            "prompt_version": self.prompt_version, "parameters": deepcopy(self.parameters),
            "sample_id": self.sample_id, "selection_seed": seed,
            "ablation": "remove_task_information" if self.variant == "A1" else None,
            "repair_attempts": repair_count, "fallback_to_geometry": fallback,
            "fallback_method": "geometry_reference", "score_scope": "pilot_input",
            # Retain old consumers without claiming the pilot scores implement B's M1.
            "fallback_to_m1": fallback,
            "fallback_to_m1_semantics": "legacy_alias_for_fallback_to_geometry_not_official_b_m1",
            "fallback_count": int(fallback),
            "fallback_reason": ("provider_error" if any(c["error"] for c in calls)
                                else "invalid_model_output") if fallback else None,
            "cache_mode": self.cache.mode,
            "cache_hit": all(item["cache_hit"] for item in calls),
            "cache_hits": sum(item["cache_hit"] for item in calls),
            "raw_initial_response": calls[0]["raw_text"],
            "raw_repair_response": calls[1]["raw_text"] if len(calls) > 1 else None,
            "requests": len(calls),
            "live_requests": sum(not item["cache_hit"] for item in calls),
            "usage": usage, "live_usage": live_usage, "calls": calls,
        }
        selection["metadata"] = metadata
        canonical_json(selection)
        return selection
