from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from curl_cffi import requests

from services.proxy_service import proxy_settings
from utils.helper import anonymize_token


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _int(value: object, fallback: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value if value is not None else fallback))
    except (TypeError, ValueError):
        return max(minimum, fallback)


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _result(
    *,
    status: str,
    ok: bool = False,
    http_status: int | None = None,
    flow_id: str = "",
    message: str = "",
    triggered_at: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": ok,
        "http_status": http_status,
        "flow_id": flow_id or None,
        "message": message,
        "triggered_at": triggered_at or _now(),
    }


def _redact(text: object, access_token: str = "") -> str:
    value = str(text or "")
    token = str(access_token or "").strip()
    if token:
        value = value.replace(token, anonymize_token(token))
    return value[:300]


def _headers(conf: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    bearer = str(conf.get("bearer") or conf.get("auth_token") or "").strip()
    cookie = str(conf.get("cookie") or "").strip()
    origin = str(conf.get("origin") or "").strip()
    referer = str(conf.get("referer") or "").strip()
    user_agent = str(conf.get("user_agent") or "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if cookie:
        headers["Cookie"] = cookie
    if origin:
        headers["Origin"] = origin
    if referer:
        headers["Referer"] = referer
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def trigger_flow(access_token: str, config: dict[str, Any], *, session_factory: Any = None) -> dict[str, Any]:
    conf = config.get("flow_trigger") if isinstance(config.get("flow_trigger"), dict) else {}
    if not _bool(conf.get("enabled"), False):
        return _result(status="skipped", message="flow_trigger disabled")

    token = str(access_token or "").strip()
    if not token:
        return _result(status="skipped", message="access_token is empty")

    url = str(conf.get("url") or "").strip()
    if not url:
        return _result(status="failed", message="flow_trigger url is empty")

    timeout = _int(conf.get("timeout"), 30, 1)
    access_token_key = str(conf.get("access_token_key") or "access_token").strip() or "access_token"
    try:
        body = _payload(conf.get("payload"))
    except Exception as exc:
        return _result(status="failed", message=f"invalid flow_trigger payload: {type(exc).__name__}: {exc}")
    body[access_token_key] = token

    use_proxy = _bool(conf.get("use_register_proxy"), False)
    verify_ssl = _bool(conf.get("verify_ssl"), True)
    session_kwargs = proxy_settings.build_session_kwargs(
        proxy=str(config.get("proxy") or "").strip() if use_proxy else "",
        verify=verify_ssl,
    )
    session_factory = session_factory or requests.Session
    session = session_factory(**session_kwargs)
    try:
        response = session.post(
            url,
            headers=_headers(conf),
            data=json.dumps(body, ensure_ascii=False),
            timeout=timeout,
        )
        http_status = int(getattr(response, "status_code", 0) or 0)
        text = _redact(getattr(response, "text", ""), token)
        flow_id = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                flow = payload.get("flow")
                if isinstance(flow, dict):
                    flow_id = str(flow.get("flow_id") or "")
                flow_id = flow_id or str(payload.get("flow_id") or payload.get("id") or "")
                text = _redact(payload.get("message") or text, token)
        except Exception:
            pass
        if 200 <= http_status < 300:
            return _result(status="success", ok=True, http_status=http_status, flow_id=flow_id, message=text)
        return _result(status="failed", http_status=http_status, flow_id=flow_id, message=text)
    except Exception as exc:
        return _result(status="failed", message=_redact(f"{type(exc).__name__}: {exc}", token))
    finally:
        try:
            session.close()
        except Exception:
            pass
