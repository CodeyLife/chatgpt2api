from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from curl_cffi.requests import Session as CurlSession


logger = logging.getLogger(__name__)

DEFAULT_SMS_CONFIG = {
    "provider": "grizzly",
    "api_base": "https://api.grizzlysms.com/stubs/handler_api.php",
    "api_key": "",
    "service": "ot",
    "country": "187",
    "wait_timeout": 180,
    "poll_interval": 5,
    "request_timeout": 30,
    "max_price": "",
    "l_api_base": "",
    "l_admin_auth_code": "",
    "l_phone_prefix": "",
    "h_api_base": "",
    "h_admin_auth_code": "",
    "h_phone_prefix": "",
    "h_phone_acquire_mode": "reusable",
    "min_cancel_delay": 125,
}

_ACQUIRED_AT: dict[str, float] = {}


@dataclass(frozen=True)
class SmsActivation:
    activation_id: str
    phone: str
    provider: str


class SmsProviderError(RuntimeError):
    pass


class SmsNoNumbersError(SmsProviderError):
    pass


class SmsNoBalanceError(SmsProviderError):
    pass


class SmsCodeTimeout(SmsProviderError):
    pass


def normalize_config(config: dict | None) -> dict:
    raw = config if isinstance(config, dict) else {}
    normalized = {**DEFAULT_SMS_CONFIG, **raw}
    normalized["provider"] = str(normalized.get("provider") or "grizzly").strip().lower()
    normalized["api_base"] = str(normalized.get("api_base") or DEFAULT_SMS_CONFIG["api_base"]).strip()
    normalized["api_key"] = str(normalized.get("api_key") or "").strip()
    normalized["service"] = str(normalized.get("service") or DEFAULT_SMS_CONFIG["service"]).strip()
    normalized["country"] = str(normalized.get("country") or DEFAULT_SMS_CONFIG["country"]).strip()
    normalized["request_timeout"] = max(1, int(normalized.get("request_timeout") or 30))
    normalized["wait_timeout"] = max(1, int(normalized.get("wait_timeout") or 180))
    normalized["poll_interval"] = max(0, int(normalized.get("poll_interval") or 5))
    normalized["min_cancel_delay"] = max(0, int(normalized.get("min_cancel_delay") or 125))
    return normalized


def create_session(config: dict | None = None) -> CurlSession:
    cfg = normalize_config(config)
    session = CurlSession(impersonate="chrome")
    session.timeout = cfg["request_timeout"]
    return session


def _normalize_phone_digits(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit())


def _provider(config: dict) -> str:
    value = str(config.get("provider") or "grizzly").strip().lower()
    return value if value in {"grizzly", "l", "h"} else "grizzly"


def _request_grizzly(http, config: dict, params: dict) -> str:
    base_params = {"api_key": str(config.get("api_key") or "").strip()}
    base_params.update(params)
    response = http.get(str(config.get("api_base") or DEFAULT_SMS_CONFIG["api_base"]), params=base_params)
    text = str(getattr(response, "text", "") or "").strip()
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise SmsProviderError(f"GrizzlySMS HTTP {response.status_code}: {text[:200]}")
    if text == "BAD_KEY":
        raise SmsProviderError("SMS API key is invalid (BAD_KEY)")
    if text == "NO_BALANCE":
        raise SmsNoBalanceError("SMS provider balance is insufficient (NO_BALANCE)")
    if text == "NO_NUMBERS":
        raise SmsNoNumbersError("SMS provider has no available numbers (NO_NUMBERS)")
    if text == "SERVICE_UNAVAILABLE_REGION":
        raise SmsProviderError("SMS service is unavailable in this region")
    if text in {"BAD_ACTION", "BAD_SERVICE", "BAD_STATUS"}:
        raise SmsProviderError(f"SMS provider rejected request parameters: {text}")
    if text == "NO_ACTIVATION":
        raise SmsProviderError("SMS activation id does not exist (NO_ACTIVATION)")
    if text.startswith("The service is prohibited"):
        raise SmsProviderError(f"SMS service is prohibited: {text}")
    return text


def _json_response(response, label: str) -> dict:
    text = str(getattr(response, "text", "") or "").strip()
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if int(getattr(response, "status_code", 0) or 0) != 200:
        message = str(payload.get("error") or payload.get("message") or text if isinstance(payload, dict) else text)
        raise SmsProviderError(f"{label} HTTP {response.status_code}: {message[:200]}")
    if not isinstance(payload, dict):
        raise SmsProviderError(f"{label} response is not a JSON object: {text[:200]}")
    error = str(payload.get("error") or "").strip()
    if error:
        raw = str(payload.get("raw") or "").strip()
        combined = f"{error} {raw}".strip()
        if "NO_BALANCE" in combined or "余额不足" in combined:
            raise SmsNoBalanceError(f"{label} balance is insufficient: {combined}")
        if "NO_NUMBERS" in combined or "暂无号码" in combined:
            raise SmsNoNumbersError(f"{label} has no available numbers: {combined}")
        raise SmsProviderError(f"{label} request failed: {combined}")
    return payload


def _post_management_json(http, base_url: str, token: str, path: str, payload: dict, label: str) -> dict:
    base_url = str(base_url or "").strip()
    token = str(token or "").strip()
    if not base_url:
        raise SmsProviderError(f"{label} API base is required")
    if not token:
        raise SmsProviderError(f"{label} admin auth code is required")
    response = http.post(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(payload),
    )
    return _json_response(response, label)


def _l_post(http, config: dict, path: str, payload: dict) -> dict:
    return _post_management_json(http, config.get("l_api_base"), config.get("l_admin_auth_code"), path, payload, "L")


def _h_post(http, config: dict, path: str, payload: dict) -> dict:
    return _post_management_json(http, config.get("h_api_base"), config.get("h_admin_auth_code"), path, payload, "H")


def _h_acquire_path(config: dict) -> str:
    mode = str(config.get("h_phone_acquire_mode") or "reusable").strip().lower()
    if mode in {"new", "fresh", "always_new", "take_phone", "take-phone"}:
        return "/api/admin/h/take-phone"
    return "/api/admin/h/take-reusable-phone"


def _extract_item_activation(data: dict, *, label: str, phone_prefix: object = "") -> SmsActivation:
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    activation_id = str(item.get("id") or "").strip()
    phone = _normalize_phone_digits(item.get("phone"))
    prefix = _normalize_phone_digits(phone_prefix)
    if prefix and phone and not phone.startswith(prefix):
        phone = f"{prefix}{phone}"
    if not activation_id or not phone:
        raise SmsProviderError(f"{label} response missing item.id/item.phone: {str(data)[:200]}")
    _ACQUIRED_AT[activation_id] = time.time()
    return SmsActivation(activation_id=activation_id, phone=phone, provider=label.lower())


def acquire_number(config: dict | None = None, http=None, *, service: str | None = None, country: str | None = None) -> SmsActivation:
    cfg = normalize_config(config)
    own_http = http is None
    http = http or create_session(cfg)
    try:
        provider = _provider(cfg)
        service_value = str(service or cfg.get("service") or "").strip()
        country_value = str(country or cfg.get("country") or "").strip()
        if provider == "l":
            payload = {"service": service_value, "country": country_value}
            if cfg.get("max_price"):
                payload["maxPrice"] = cfg["max_price"]
            return _extract_item_activation(
                _l_post(http, cfg, "/api/admin/l/take-phone", payload),
                label="L",
                phone_prefix=cfg.get("l_phone_prefix"),
            )
        if provider == "h":
            payload = {"projectId": service_value, "country": country_value}
            return _extract_item_activation(
                _h_post(http, cfg, _h_acquire_path(cfg), payload),
                label="H",
                phone_prefix=cfg.get("h_phone_prefix"),
            )
        params = {"action": "getNumber", "service": service_value, "country": country_value}
        if cfg.get("max_price"):
            params["maxPrice"] = cfg["max_price"]
        text = _request_grizzly(http, cfg, params)
        if not text.startswith("ACCESS_NUMBER:"):
            raise SmsProviderError(f"getNumber unexpected response: {text[:200]}")
        parts = text.split(":")
        if len(parts) < 3:
            raise SmsProviderError(f"getNumber invalid response: {text[:200]}")
        activation_id = str(parts[1] or "").strip()
        phone = _normalize_phone_digits(parts[2])
        if not activation_id or not phone:
            raise SmsProviderError(f"getNumber response missing activation id or phone: {text[:200]}")
        _ACQUIRED_AT[activation_id] = time.time()
        return SmsActivation(activation_id=activation_id, phone=phone, provider="grizzly")
    finally:
        if own_http:
            http.close()


def wait_for_sms_code(
    activation_id: str,
    config: dict | None = None,
    http=None,
    *,
    max_wait: int | None = None,
    poll_interval: int | None = None,
    sleep_func=time.sleep,
) -> str:
    cfg = normalize_config(config)
    activation_id = str(activation_id or "").strip()
    if not activation_id:
        raise SmsProviderError("activation_id is required")
    own_http = http is None
    http = http or create_session(cfg)
    total_wait = cfg["wait_timeout"] if max_wait is None else max(1, int(max_wait))
    interval = cfg["poll_interval"] if poll_interval is None else max(0, int(poll_interval))
    deadline = time.time() + total_wait
    try:
        while time.time() < deadline:
            provider = _provider(cfg)
            if provider == "l":
                data = _l_post(http, cfg, "/api/admin/l/fetch-code", {"id": activation_id})
                code = str(data.get("code") or "").strip()
                if code:
                    return code
            elif provider == "h":
                data = _h_post(http, cfg, "/api/admin/h/fetch-code", {"id": activation_id})
                code = str(data.get("code") or "").strip()
                if code:
                    return code
            else:
                text = _request_grizzly(http, cfg, {"action": "getStatus", "id": activation_id})
                if text.startswith("STATUS_OK:"):
                    return text.split(":", 1)[1].strip()
                if text == "STATUS_CANCEL":
                    raise SmsProviderError("SMS activation was cancelled")
            if interval:
                sleep_func(interval)
        raise SmsCodeTimeout(f"SMS code timeout after {total_wait}s, activation_id={activation_id}")
    finally:
        if own_http:
            http.close()


def set_status(activation_id: str, status: int, config: dict | None = None, http=None) -> str:
    cfg = normalize_config(config)
    own_http = http is None
    http = http or create_session(cfg)
    try:
        provider = _provider(cfg)
        if provider in {"l", "h"}:
            return "OK"
        return _request_grizzly(http, cfg, {"action": "setStatus", "status": str(status), "id": activation_id})
    finally:
        if own_http:
            http.close()


def complete(activation_id: str, config: dict | None = None, http=None) -> None:
    cfg = normalize_config(config)
    provider = _provider(cfg)
    if provider in {"l", "h"}:
        _ACQUIRED_AT.pop(str(activation_id or "").strip(), None)
        return
    try:
        set_status(activation_id, 6, cfg, http=http)
        _ACQUIRED_AT.pop(str(activation_id or "").strip(), None)
    except Exception as exc:
        logger.warning("SMS complete failed but registration can continue: %s", exc)


def _release_l_number(activation_id: str, config: dict, http) -> None:
    data = _l_post(http, config, "/api/admin/l/release", {"id": activation_id})
    failed = data.get("failed") if isinstance(data, dict) else None
    if isinstance(failed, list) and failed:
        raise SmsProviderError(f"L release failed for id={activation_id}: {json.dumps(failed, ensure_ascii=False)[:300]}")
    _ACQUIRED_AT.pop(activation_id, None)


def _release_h_number(activation_id: str, config: dict, http) -> None:
    data = _h_post(http, config, "/api/admin/h/release", {"id": activation_id})
    failed = data.get("failed") if isinstance(data, dict) else None
    if isinstance(failed, list) and failed:
        raise SmsProviderError(f"H release failed for id={activation_id}: {json.dumps(failed, ensure_ascii=False)[:300]}")
    _ACQUIRED_AT.pop(activation_id, None)


def _cancel_grizzly_sync(activation_id: str, config: dict, http_factory=create_session) -> None:
    acquired_at = _ACQUIRED_AT.get(activation_id)
    if acquired_at is not None:
        wait_seconds = int(config.get("min_cancel_delay") or 0) - (time.time() - acquired_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
    http = http_factory(config)
    try:
        set_status(activation_id, 8, config, http=http)
        _ACQUIRED_AT.pop(activation_id, None)
    except Exception as exc:
        logger.warning("SMS cancel failed, manual cleanup may be required: activation_id=%s, error=%s", activation_id, exc)
    finally:
        http.close()


def cancel(activation_id: str, config: dict | None = None, http=None, *, background: bool = True) -> None:
    cfg = normalize_config(config)
    activation_id = str(activation_id or "").strip()
    if not activation_id:
        return
    provider = _provider(cfg)
    if provider == "l":
        try:
            own_http = http is None
            http = http or create_session(cfg)
            _release_l_number(activation_id, cfg, http)
        except Exception as exc:
            logger.warning("L SMS release failed: id=%s, error=%s", activation_id, exc)
            _ACQUIRED_AT.pop(activation_id, None)
        finally:
            if own_http:
                http.close()
        return
    if provider == "h":
        try:
            own_http = http is None
            http = http or create_session(cfg)
            _release_h_number(activation_id, cfg, http)
        except Exception as exc:
            logger.warning("H SMS release failed: id=%s, error=%s", activation_id, exc)
            _ACQUIRED_AT.pop(activation_id, None)
        finally:
            if own_http:
                http.close()
        return
    if not background:
        set_status(activation_id, 8, cfg, http=http)
        _ACQUIRED_AT.pop(activation_id, None)
        return
    thread = threading.Thread(target=_cancel_grizzly_sync, args=(activation_id, cfg), daemon=True, name=f"sms-cancel-{activation_id}")
    thread.start()
