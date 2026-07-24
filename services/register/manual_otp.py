from __future__ import annotations

import threading
import time
from collections import defaultdict


_lock = threading.Lock()
_codes: dict[str, list[str]] = defaultdict(list)
_events: dict[str, threading.Event] = {}
_waiting: dict[str, dict] = {}


def _norm(email: str) -> str:
    return str(email or "").strip().lower()


def _event_for(email: str) -> threading.Event:
    key = _norm(email)
    event = _events.get(key)
    if event is None:
        event = threading.Event()
        _events[key] = event
    return event


def mark_waiting(email: str, *, task_id: int | None = None, provider_ref: str = "") -> None:
    key = _norm(email)
    if not key:
        return
    with _lock:
        _waiting[key] = {
            "email": email,
            "task_id": task_id,
            "provider_ref": provider_ref,
            "since": time.time(),
        }
        _event_for(key).clear()


def clear_waiting(email: str) -> None:
    with _lock:
        _waiting.pop(_norm(email), None)


def list_waiting() -> list[dict]:
    with _lock:
        now = time.time()
        return [
            {
                **dict(item),
                "age_seconds": max(0, int(now - float(item.get("since") or now))),
            }
            for item in _waiting.values()
        ]


def submit_manual_otp(email: str, code: str) -> dict:
    key = _norm(email)
    code = str(code or "").strip().replace(" ", "")
    if not key:
        raise ValueError("email is required")
    if not code:
        raise ValueError("code is required")
    if not code.isdigit() or len(code) not in {4, 5, 6, 7, 8}:
        raise ValueError("code format is invalid")
    with _lock:
        _codes[key].append(code)
        _event_for(key).set()
    return {"ok": True, "email": email, "code": code}


def pop_manual_otp(email: str) -> str | None:
    key = _norm(email)
    with _lock:
        queue = _codes.get(key) or []
        if not queue:
            return None
        code = queue.pop(0)
        if not queue:
            _event_for(key).clear()
        return code


def wait_for_manual_otp(
    email: str,
    *,
    timeout: int = 180,
    task_id: int | None = None,
    provider_ref: str = "",
) -> str:
    key = _norm(email)
    if not key:
        raise TimeoutError("manual OTP email is required")
    existing = pop_manual_otp(email)
    if existing:
        clear_waiting(email)
        return existing
    mark_waiting(email, task_id=task_id, provider_ref=provider_ref)
    deadline = time.time() + max(1, int(timeout or 180))
    event = _event_for(email)
    try:
        while time.time() < deadline:
            code = pop_manual_otp(email)
            if code:
                return code
            remaining = max(0.0, deadline - time.time())
            event.wait(timeout=min(1.0, remaining))
        raise TimeoutError(f"manual OTP timeout for {email}")
    finally:
        clear_waiting(email)
