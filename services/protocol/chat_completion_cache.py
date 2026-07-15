from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from services.config import config

CACHEABLE_TEXT_KEYS = {
    "frequency_penalty",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "model",
    "presence_penalty",
    "reasoning_effort",
    "response_format",
    "seed",
    "stop",
    "temperature",
    "thinking_effort",
    "tool_choice",
    "tools",
    "top_p",
    "user",
    "reasoning",
}


@dataclass
class CacheEntry:
    expires_at: float
    value: Any


class ChatCompletionInflightTimeoutError(TimeoutError):
    status_code = 504

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"chat completion inflight wait timed out after {timeout_seconds:g} seconds")

    def to_openai_error(self) -> dict[str, object]:
        return {
            "error": {
                "message": str(self),
                "type": "server_error",
                "code": "inflight_timeout",
            }
        }


class ChatCompletionInflightCancelledError(RuntimeError):
    status_code = 503

    def __init__(self, message: str = "chat completion owner was cancelled before finishing") -> None:
        super().__init__(message)

    def to_openai_error(self) -> dict[str, object]:
        return {
            "error": {
                "message": str(self),
                "type": "server_error",
                "code": "inflight_owner_cancelled",
            }
        }


@dataclass
class InflightCall:
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))
    started_at: float = field(default_factory=time.monotonic)
    done: bool = False
    value: Any = None
    error: Exception | None = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, bytearray):
        data = bytes(value)
        return {"__bytes_sha256__": hashlib.sha256(data).hexdigest(), "length": len(data)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def canonical_body(body: dict[str, Any], messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
    payload = {key: body.get(key) for key in CACHEABLE_TEXT_KEYS if key in body}
    payload["messages"] = messages
    payload["stream"] = bool(stream)
    return payload


def cache_key(body: dict[str, Any], messages: list[dict[str, Any]], *, stream: bool) -> str:
    encoded = json.dumps(
        _json_safe(canonical_body(body, messages, stream=stream)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _message_signature(message: dict[str, Any]) -> str:
    return json.dumps(_json_safe(message), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = config.get_chat_completion_cache_settings()
    if not settings.get("normalize_messages"):
        return messages

    normalized: list[dict[str, Any]] = []
    previous_signature = ""
    for message in messages:
        if settings.get("drop_assistant_history") and str(message.get("role") or "") == "assistant":
            continue
        signature = _message_signature(message)
        if settings.get("drop_adjacent_duplicates") and signature == previous_signature:
            continue
        normalized.append(message)
        previous_signature = signature
    return normalized


class ChatCompletionCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._inflight: dict[str, InflightCall] = {}

    def clear(self) -> None:
        with self._lock:
            inflight = list(self._inflight.values())
            self._entries.clear()
            self._inflight.clear()
        for item in inflight:
            self._finish_inflight(
                item,
                error=ChatCompletionInflightCancelledError("chat completion cache was cleared before finishing"),
            )

    def _settings(self) -> dict[str, object]:
        return config.get_chat_completion_cache_settings()

    def _prune_locked(self, now: float, max_entries: int) -> None:
        expired = [key for key, item in self._entries.items() if item.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)
        while len(self._entries) > max_entries:
            oldest_key = min(self._entries, key=lambda key: self._entries[key].expires_at)
            self._entries.pop(oldest_key, None)

    @staticmethod
    def _copy(value: Any) -> Any:
        return copy.deepcopy(value)

    @staticmethod
    def _inflight_timeout(settings: dict[str, object]) -> float:
        try:
            return max(0.001, float(settings.get("inflight_timeout_seconds") or 360))
        except (TypeError, ValueError):
            return 360.0

    @staticmethod
    def _finish_inflight(
        inflight: InflightCall,
        *,
        value: Any = None,
        error: Exception | None = None,
    ) -> bool:
        with inflight.condition:
            if inflight.done:
                return False
            inflight.value = value
            inflight.error = error
            inflight.done = True
            inflight.condition.notify_all()
            return True

    def _remove_inflight_if_current(self, key: str, inflight: InflightCall) -> bool:
        with self._lock:
            if self._inflight.get(key) is not inflight:
                return False
            self._inflight.pop(key, None)
            return True

    def _claim_inflight_locked(
        self,
        key: str,
        *,
        dedupe_inflight: bool,
        timeout_seconds: float,
    ) -> tuple[InflightCall, bool, InflightCall | None]:
        inflight = self._inflight.get(key) if dedupe_inflight else None
        stale: InflightCall | None = None
        if inflight is not None and time.monotonic() - inflight.started_at >= timeout_seconds:
            self._inflight.pop(key, None)
            stale = inflight
            inflight = None
        if inflight is None:
            inflight = InflightCall()
            if dedupe_inflight:
                self._inflight[key] = inflight
            return inflight, True, stale
        return inflight, False, stale

    def _wait_for_inflight(
        self,
        key: str,
        inflight: InflightCall,
        timeout_seconds: float,
    ) -> Any:
        deadline = inflight.started_at + timeout_seconds
        with inflight.condition:
            while not inflight.done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                inflight.condition.wait(timeout=remaining)
            if inflight.done:
                if inflight.error:
                    raise inflight.error
                return self._copy(inflight.value)

        timeout_error = ChatCompletionInflightTimeoutError(timeout_seconds)
        self._remove_inflight_if_current(key, inflight)
        if self._finish_inflight(inflight, error=timeout_error):
            raise timeout_error
        with inflight.condition:
            if inflight.error:
                raise inflight.error
            return self._copy(inflight.value)

    @staticmethod
    def _waiter_error(exc: BaseException) -> Exception:
        if isinstance(exc, Exception):
            return exc
        return ChatCompletionInflightCancelledError()

    def get_or_compute_response(self, key: str, compute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        settings = self._settings()
        if not settings.get("enabled") or int(settings.get("ttl_seconds") or 0) <= 0:
            return compute()

        now = time.time()
        max_entries = int(settings.get("max_entries") or 1)
        dedupe_inflight = bool(settings.get("dedupe_inflight"))
        inflight_timeout = self._inflight_timeout(settings)
        with self._lock:
            self._prune_locked(now, max_entries)
            entry = self._entries.get(key)
            if entry and entry.expires_at > now:
                return self._copy(entry.value)
            inflight, owner, stale = self._claim_inflight_locked(
                key,
                dedupe_inflight=dedupe_inflight,
                timeout_seconds=inflight_timeout,
            )

        if stale is not None:
            self._finish_inflight(stale, error=ChatCompletionInflightTimeoutError(inflight_timeout))

        if not owner:
            return self._wait_for_inflight(key, inflight, inflight_timeout)

        try:
            value = compute()
        except BaseException as exc:
            self._remove_inflight_if_current(key, inflight)
            self._finish_inflight(inflight, error=self._waiter_error(exc))
            raise

        expires_at = time.time() + int(settings.get("ttl_seconds") or 0)
        with self._lock:
            is_current = self._inflight.get(key) is inflight
            if not dedupe_inflight or is_current:
                self._entries[key] = CacheEntry(expires_at=expires_at, value=value)
                self._prune_locked(time.time(), max_entries)
            if is_current:
                self._inflight.pop(key, None)
        self._finish_inflight(inflight, value=value)
        return self._copy(value)

    def get_or_compute_stream(self, key: str, compute: Callable[[], Iterable[dict[str, Any]]]) -> Iterator[dict[str, Any]]:
        settings = self._settings()
        if (
            not settings.get("enabled")
            or not settings.get("stream_cache")
            or int(settings.get("ttl_seconds") or 0) <= 0
        ):
            yield from compute()
            return

        now = time.time()
        max_entries = int(settings.get("max_entries") or 1)
        dedupe_inflight = bool(settings.get("dedupe_inflight"))
        inflight_timeout = self._inflight_timeout(settings)
        with self._lock:
            self._prune_locked(now, max_entries)
            entry = self._entries.get(key)
            if entry and entry.expires_at > now:
                yield from self._copy(entry.value)
                return
            inflight, owner, stale = self._claim_inflight_locked(
                key,
                dedupe_inflight=dedupe_inflight,
                timeout_seconds=inflight_timeout,
            )

        if stale is not None:
            self._finish_inflight(stale, error=ChatCompletionInflightTimeoutError(inflight_timeout))

        if not owner:
            yield from self._wait_for_inflight(key, inflight, inflight_timeout)
            return

        chunks: list[dict[str, Any]] = []
        try:
            for chunk in compute():
                chunks.append(chunk)
                yield chunk
        except BaseException as exc:
            self._remove_inflight_if_current(key, inflight)
            self._finish_inflight(inflight, error=self._waiter_error(exc))
            raise

        expires_at = time.time() + int(settings.get("ttl_seconds") or 0)
        with self._lock:
            is_current = self._inflight.get(key) is inflight
            if not dedupe_inflight or is_current:
                self._entries[key] = CacheEntry(expires_at=expires_at, value=chunks)
                self._prune_locked(time.time(), max_entries)
            if is_current:
                self._inflight.pop(key, None)
        self._finish_inflight(inflight, value=chunks)


chat_completion_cache = ChatCompletionCache()
