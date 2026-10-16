"""Content-addressed raw-response cache with atomic writes and explicit replay."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

CACHE_SCHEMA_VERSION = "grasp_c_response_cache_v1"
CACHE_MODES = ("off", "read_only", "read_write")


class CacheMissError(RuntimeError):
    """A required response is absent during offline/read-only replay."""


class CacheCorruptionError(RuntimeError):
    """An existing cache record is unreadable or does not match its identity."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def cache_key(identity: dict) -> str:
    payload = {"cache_schema_version": CACHE_SCHEMA_VERSION, "identity": identity}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, directory: str | Path | None, mode: str = "read_write"):
        if mode not in CACHE_MODES:
            raise ValueError(f"cache mode must be one of {CACHE_MODES}")
        if directory is None and mode == "read_only":
            raise ValueError("read_only cache requires cache_dir")
        self.directory = Path(directory) if directory is not None else None
        self.mode = mode if self.directory is not None else "off"

    def lookup(self, identity: dict) -> tuple[str, dict | None]:
        key = cache_key(identity)
        if self.mode == "off":
            return key, None
        path = self.directory / f"{key}.json"
        if not path.exists():
            if self.mode == "read_only":
                raise CacheMissError(f"no cached model response for request {key}")
            return key, None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (record["cache_schema_version"] != CACHE_SCHEMA_VERSION
                    or canonical_json(record["identity"]) != canonical_json(identity)
                    or not isinstance(record["response"], dict)):
                raise ValueError("cache identity/schema mismatch")
            response = record["response"]
            if (set(response) != {"text", "model", "usage", "request_id", "metadata"}
                    or not isinstance(response["text"], str)
                    or not isinstance(response["model"], str)
                    or not isinstance(response["usage"], dict)
                    or not isinstance(response["metadata"], dict)
                    or (response["request_id"] is not None
                        and not isinstance(response["request_id"], str))):
                raise ValueError("invalid cached ModelResponse")
            canonical_json(record)  # Also rejects nonfinite JSON numeric values.
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CacheCorruptionError(f"invalid cache record: {path}") from exc
        return key, deepcopy(response)

    def store(self, identity: dict, response: dict) -> None:
        if self.mode != "read_write":
            return
        record = {"cache_schema_version": CACHE_SCHEMA_VERSION,
                  "identity": identity, "response": response}
        encoded = canonical_json(record) + "\n"
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{cache_key(identity)}.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.directory, prefix=".response-",
                                             suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
