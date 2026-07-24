from __future__ import annotations

import json
from typing import Any

from services.register import openai_register as base
from utils.pow import build_legacy_requirements_token


ANON_BASE = "https://chatgpt.com/backend-anon"
API_BASE = "https://chatgpt.com/backend-api"


def _timezone_offset_min(registrar: Any) -> int:
    profile = getattr(registrar, "profile", None)
    for value in (
        getattr(profile, "timezone_offset_min", None),
        getattr(profile, "timezoneOffsetMin", None),
        getattr(registrar, "timezone_offset_min", None),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _accounts_check_url(api_base: str, timezone_offset_min: int) -> str:
    return f"{api_base}/accounts/check/v4-2023-04-27?timezone_offset_min={timezone_offset_min}"


def _conversation_init_payload(timezone_offset_min: int) -> dict[str, Any]:
    return {
        "requested_default_model": None,
        "conversation_id": None,
        "timezone_offset_min": timezone_offset_min,
        "conversation_origin": None,
    }


def _json_post(registrar: Any, url: str, payload: dict[str, Any], referer: str, headers: dict[str, str] | None = None) -> Any:
    req_headers = headers or registrar._chatgpt_headers(referer=referer)
    req_headers = dict(req_headers)
    req_headers["content-type"] = "application/json"
    response, _ = base.request_with_local_retry(
        registrar.session,
        "post",
        url,
        headers=req_headers,
        data=json.dumps(payload, separators=(",", ":")),
        verify=False,
    )
    return response


def _get(registrar: Any, url: str, headers: dict[str, str]) -> Any:
    response, _ = base.request_with_local_retry(
        registrar.session,
        "get",
        url,
        headers=headers,
        verify=False,
    )
    return response


def _safe_request(index: int, label: str, fn, *, strict: bool = False) -> Any:
    try:
        response = fn()
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {(getattr(response, 'text', '') or '')[:180]}")
        return response
    except Exception as exc:
        if strict:
            raise
        base.step(index, f"ChatGPT bootstrap 跳过 {label}: {type(exc).__name__}: {str(exc)[:160]}", "yellow")
        return None


def _requirements_prepare(registrar: Any, api_base: str, referer: str, *, index: int, strict: bool = False):
    token = build_legacy_requirements_token(
        getattr(registrar.profile, "user_agent", ""),
        script_sources=[],
        data_build="",
    )
    return _safe_request(
        index,
        f"{api_base}/sentinel/chat-requirements/prepare",
        lambda: _json_post(registrar, f"{api_base}/sentinel/chat-requirements/prepare", {"p": token}, referer),
        strict=strict,
    )


def _maybe_requirements_finalize(registrar: Any, api_base: str, referer: str, prepare_response: Any, *, index: int, strict: bool = False) -> None:
    if prepare_response is None:
        return
    try:
        data = prepare_response.json()
    except Exception:
        return
    if not isinstance(data, dict):
        return
    prepare_token = data.get("prepare_token") or data.get("token") or data.get("c")
    if not prepare_token:
        return
    payload: dict[str, Any] = {"prepare_token": prepare_token}
    proof = data.get("proofofwork")
    turnstile = data.get("turnstile")
    if proof:
        payload["proofofwork"] = proof
    if turnstile:
        payload["turnstile"] = turnstile
    _safe_request(
        index,
        f"{api_base}/sentinel/chat-requirements/finalize",
        lambda: _json_post(registrar, f"{api_base}/sentinel/chat-requirements/finalize", payload, referer),
        strict=strict,
    )


def anonymous_bootstrap(registrar: Any, *, index: int, strict: bool = False) -> None:
    referer = "https://chatgpt.com/"
    headers = registrar._chatgpt_headers(referer=referer)
    timezone_offset_min = _timezone_offset_min(registrar)
    base.step(index, "ChatGPT 匿名态 bootstrap 预热")
    for url in [
        _accounts_check_url(ANON_BASE, timezone_offset_min),
        f"{ANON_BASE}/me",
    ]:
        _safe_request(index, url, lambda u=url: _get(registrar, u, headers), strict=strict)
    prepare_response = _requirements_prepare(registrar, ANON_BASE, referer, index=index, strict=strict)
    for url in [
        f"{ANON_BASE}/system_hints?mode=custom_agents",
        f"{ANON_BASE}/system_hints?mode=connectors",
        f"{ANON_BASE}/system_hints?mode=basic",
        f"{ANON_BASE}/models?iim=false&is_gizmo=false&supports_model_picker_upgrade_presets=true",
    ]:
        _safe_request(index, url, lambda u=url: _get(registrar, u, headers), strict=strict)
    _safe_request(
        index,
        "anon conversation/init",
        lambda: _json_post(
            registrar,
            f"{ANON_BASE}/conversation/init",
            _conversation_init_payload(timezone_offset_min),
            referer,
            headers=headers,
        ),
        strict=strict,
    )
    _maybe_requirements_finalize(registrar, ANON_BASE, referer, prepare_response, index=index, strict=strict)


def authenticated_bootstrap(registrar: Any, access_token: str, *, index: int, strict: bool = False) -> None:
    referer = "https://chatgpt.com/"
    timezone_offset_min = _timezone_offset_min(registrar)

    def headers() -> dict[str, str]:
        h = registrar._chatgpt_headers(referer=referer)
        if access_token:
            h["authorization"] = f"Bearer {access_token}"
        return h

    base.step(index, "ChatGPT 登录态 bootstrap 预热")
    for path in [
        "/accounts/optimized/check",
        "/user_granular_consent",
        "/me",
        f"/accounts/check/v4-2023-04-27?timezone_offset_min={timezone_offset_min}",
        "/settings/user",
    ]:
        _safe_request(index, f"auth {path}", lambda p=path: _get(registrar, f"{API_BASE}{p}", headers()), strict=strict)
    prepare_response = _requirements_prepare(registrar, API_BASE, referer, index=index, strict=strict)
    for url in [
        f"{API_BASE}/system_hints?mode=custom_agents",
        f"{API_BASE}/system_hints?mode=connectors",
        f"{API_BASE}/system_hints?mode=basic",
        f"{API_BASE}/models?iim=false&is_gizmo=false&supports_model_picker_upgrade_presets=true",
    ]:
        _safe_request(index, url, lambda u=url: _get(registrar, u, headers()), strict=strict)
    _safe_request(
        index,
        "auth conversation/init",
        lambda: _json_post(
            registrar,
            f"{API_BASE}/conversation/init",
            _conversation_init_payload(timezone_offset_min),
            referer,
            headers=headers(),
        ),
        strict=strict,
    )
    _maybe_requirements_finalize(registrar, API_BASE, referer, prepare_response, index=index, strict=strict)
