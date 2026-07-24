from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import requests

from services.register.cloud_browser import CloudBrowserSession


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoxyOpenResult:
    profile_id: str
    raw: dict[str, Any]
    connect_url: str
    debugger_address: str = ""
    ws_endpoint: str = ""
    webdriver_url: str = ""
    created_by_run: bool = False


def _join_url(base: str, path: str) -> str:
    return f"{str(base or '').rstrip('/')}/{str(path or '').lstrip('/')}"


def _dig(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first(payload: dict[str, Any], paths: list[tuple[str, ...]]) -> str:
    for path in paths:
        value = _dig(payload, *path)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _method(config: dict[str, Any], key: str, default: str = "POST") -> str:
    return str(config.get(key) or default).strip().upper()


def _bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_or_text(value: Any) -> Any:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else text


def _normalize_debugger_address(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith(("ws://", "wss://", "http://", "https://")):
        return text
    text = text.replace("http://", "").replace("https://", "").strip("/")
    if text.startswith(":") and text[1:].isdigit():
        return f"http://127.0.0.1{text}"
    if text.isdigit():
        return f"http://127.0.0.1:{text}"
    if ":" in text:
        return f"http://{text}"
    return text


def _proxy_url_to_roxy_info(proxy_url: str, check_channel: str = "") -> dict[str, Any]:
    text = str(proxy_url or "").strip()
    if not text:
        raise ValueError("proxy is empty")
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https", "socks5", "socks5h"}:
        raise ValueError(f"unsupported proxy protocol for Roxy: {scheme or '-'}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("proxy must include host and port")
    protocol = {"http": "HTTP", "https": "HTTPS", "socks5": "SOCKS5", "socks5h": "SOCKS5"}[scheme]
    info: dict[str, Any] = {
        "moduleId": 0,
        "proxyMethod": "custom",
        "proxyCategory": protocol,
        "ipType": "IPV4",
        "protocol": protocol,
        "host": parsed.hostname,
        "port": str(parsed.port),
    }
    if parsed.username:
        info["proxyUserName"] = unquote(parsed.username)
    if parsed.password:
        info["proxyPassword"] = unquote(parsed.password)
    if check_channel:
        info["checkChannel"] = check_channel
    return info


class RoxyBrowserClient:
    def __init__(
        self,
        config: dict | None = None,
        *,
        request_func: Callable[..., Any] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.config = cfg
        self.api_base = str(cfg.get("api_base") or "http://127.0.0.1:50100").strip().rstrip("/")
        self.api_token = str(cfg.get("api_token") or cfg.get("token") or "").strip()
        self.profile_id = str(cfg.get("profile_id") or "").strip()
        self.workspace_id = str(cfg.get("workspace_id") or "").strip()
        self.project_id = str(cfg.get("project_id") or "").strip()
        self.open_path = str(cfg.get("open_path") or "/browser/open")
        self.close_path = str(cfg.get("close_path") or "/browser/close")
        self.create_path = str(cfg.get("create_path") or "/browser/create")
        self.delete_path = str(cfg.get("delete_path") or "/browser/delete")
        self.open_headless = _bool(cfg, "open_headless", False)
        self.keep_browser_open = _bool(cfg, "keep_browser_open", False)
        self.one_profile_per_account = _bool(cfg, "one_profile_per_account", False)
        self.delete_profile_after_run = _bool(cfg, "delete_profile_after_run", False)
        self.create_use_proxy = _bool(cfg, "create_use_proxy", False)
        self.proxy_check_channel = str(cfg.get("proxy_check_channel") or "").strip()
        self.timeout = max(5, int(cfg.get("timeout") or 90))
        self.retries = max(1, int(cfg.get("api_retries") or 3))
        self.retry_delay = max(0.2, float(cfg.get("api_retry_delay") or 1.0))
        self.created_profile_id = ""
        self.last_opened: RoxyOpenResult | None = None
        self._request = request_func or requests.request
        self._sleep = sleep_func

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_token:
            headers["token"] = self.api_token
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def request(self, method: str, path: str, *, params: dict | None = None, json_body: dict | None = None) -> dict[str, Any]:
        url = _join_url(self.api_base, path)
        method_u = str(method or "GET").upper()
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._request(
                    method_u,
                    url,
                    headers=self.headers(),
                    params=params or None,
                    json=json_body if json_body is not None else None,
                    timeout=self.timeout,
                )
                text = str(getattr(response, "text", "") or "")
                try:
                    payload = response.json()
                except Exception:
                    payload = {"raw": text}
                status_code = int(getattr(response, "status_code", 0) or 0)
                if not 200 <= status_code < 300:
                    raise RuntimeError(f"Roxy API HTTP {status_code} {method_u} {path}: {text[:500]}")
                if isinstance(payload, dict):
                    code = payload.get("code")
                    ok = payload.get("ok")
                    success = payload.get("success")
                    if code not in (None, 0, 200, "0", "200") and ok is not True and success is not True:
                        message = payload.get("msg") or payload.get("message") or payload.get("error") or json.dumps(payload, ensure_ascii=False)[:500]
                        raise RuntimeError(f"Roxy API failed {method_u} {path}: {message}")
                    return payload
                return {"data": payload}
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                self._sleep(self.retry_delay * attempt)
        raise last_error or RuntimeError(f"Roxy API failed {method_u} {path}")

    def create_profile(self, proxy: str = "") -> str:
        body = self._create_profile_payload(proxy)
        result = self.request(_method(self.config, "create_method", "POST"), self.create_path, json_body=body)
        profile_id = _first(
            result,
            [
                ("id",), ("dirId",), ("dir_id",), ("profileId",), ("profile_id",),
                ("data", "id"), ("data", "dirId"), ("data", "dir_id"), ("data", "profileId"), ("data", "profile_id"),
            ],
        )
        if not profile_id:
            raise RuntimeError(f"Roxy create profile did not return profile id: {result}")
        self.created_profile_id = profile_id
        return profile_id

    def _create_profile_payload(self, proxy: str) -> dict[str, Any]:
        payload = dict(self.config.get("create_payload") if isinstance(self.config.get("create_payload"), dict) else {})
        if self.workspace_id:
            payload.setdefault("workspaceId", _int_or_text(self.workspace_id))
        if self.project_id:
            payload.setdefault("projectId", _int_or_text(self.project_id))
        payload.setdefault("os", str(self.config.get("default_os") or "macOS"))
        if self.config.get("default_os_version"):
            payload.setdefault("osVersion", str(self.config.get("default_os_version")))
        proxy_url = str(proxy or self.config.get("proxy") or "").strip()
        if self.create_use_proxy and proxy_url:
            payload["proxyInfo"] = _proxy_url_to_roxy_info(proxy_url, self.proxy_check_channel)
        return payload

    def open_session(self, proxy: str = "") -> CloudBrowserSession:
        profile_id = self.profile_id
        created_by_run = False
        if self.one_profile_per_account and profile_id:
            raise RuntimeError("Roxy one_profile_per_account=true requires empty profile_id")
        if not profile_id:
            profile_id = self.create_profile(proxy)
            created_by_run = True
        body = dict(self.config.get("open_extra_params") if isinstance(self.config.get("open_extra_params"), dict) else {})
        if self.workspace_id:
            body.setdefault("workspaceId", _int_or_text(self.workspace_id))
        body.setdefault("dirId", _int_or_text(profile_id))
        body.setdefault("args", [])
        body.setdefault("forceOpen", True)
        body["headless"] = self.open_headless
        method = _method(self.config, "open_method", "POST")
        result = self.request(
            method,
            self.open_path.format(profile_id=profile_id),
            params=body if method == "GET" else None,
            json_body=body if method != "GET" else None,
        )
        opened = self._open_result(profile_id, result, created_by_run)
        self.last_opened = opened
        return CloudBrowserSession(
            connect_url=opened.connect_url,
            provider="roxy",
            api_key_present=bool(self.api_token),
            profile_id=opened.profile_id,
            session_id=opened.profile_id,
            raw={
                "debugger_address": opened.debugger_address,
                "ws_endpoint": opened.ws_endpoint,
                "webdriver_url": opened.webdriver_url,
                "created_by_run": opened.created_by_run,
                "response": opened.raw,
            },
        )

    def _open_result(self, profile_id: str, result: dict[str, Any], created_by_run: bool) -> RoxyOpenResult:
        debugger_address = self._extract_debugger_address(result)
        ws_endpoint = _first(
            result,
            [
                ("ws",), ("wsEndpoint",), ("ws_endpoint",), ("debuggerWsUrl",),
                ("data", "ws"), ("data", "wsEndpoint"), ("data", "ws_endpoint"), ("data", "debuggerWsUrl"),
            ],
        )
        webdriver_url = _first(
            result,
            [
                ("webdriver",), ("webDriver",), ("webdriver_url",), ("webdriverUrl",),
                ("selenium",), ("selenium_url",), ("seleniumUrl",),
                ("data", "webdriver"), ("data", "webDriver"), ("data", "webdriver_url"), ("data", "webdriverUrl"),
                ("data", "selenium"), ("data", "selenium_url"), ("data", "seleniumUrl"),
            ],
        )
        connect_url = str(ws_endpoint or _normalize_debugger_address(debugger_address)).strip()
        if not connect_url:
            raise RuntimeError(f"Roxy opened profile but did not return CDP endpoint: {result}")
        return RoxyOpenResult(
            profile_id=profile_id,
            raw=result,
            connect_url=connect_url,
            debugger_address=debugger_address,
            ws_endpoint=ws_endpoint,
            webdriver_url=webdriver_url,
            created_by_run=created_by_run,
        )

    @staticmethod
    def _extract_debugger_address(payload: dict[str, Any]) -> str:
        value = _first(
            payload,
            [
                ("debuggerAddress",), ("debugger_address",), ("debugAddress",),
                ("debuggingPortUrl",), ("debugging_port_url",),
                ("remoteDebuggingAddress",), ("remote_debugging_address",),
                ("http",), ("debugHttp",), ("debug_http",),
                ("data", "debuggerAddress"), ("data", "debugger_address"), ("data", "debugAddress"),
                ("data", "debuggingPortUrl"), ("data", "debugging_port_url"),
                ("data", "remoteDebuggingAddress"), ("data", "remote_debugging_address"),
                ("data", "http"), ("data", "debugHttp"), ("data", "debug_http"),
            ],
        )
        if value:
            normalized = _normalize_debugger_address(value)
            return normalized.replace("http://", "").replace("https://", "").strip("/") if normalized.startswith(("http://", "https://")) else normalized
        port = _first(payload, [("debuggingPort",), ("debugging_port",), ("debug_port",), ("port",), ("data", "debuggingPort"), ("data", "debugging_port"), ("data", "debug_port"), ("data", "port")])
        if port:
            text = str(port).strip().lstrip(":")
            if text.isdigit():
                return f"127.0.0.1:{text}"
        return ""

    def close_session(self, session: CloudBrowserSession | None = None) -> None:
        opened = self.last_opened
        profile_id = str((session.profile_id if session else "") or (opened.profile_id if opened else "") or "").strip()
        if not profile_id:
            return
        if not self.keep_browser_open:
            self.close_profile(profile_id)
        if opened and opened.created_by_run and self.delete_profile_after_run and not self.keep_browser_open:
            self.delete_profile(profile_id)

    def close_profile(self, profile_id: str) -> None:
        body = self._profile_body(profile_id)
        method = _method(self.config, "close_method", "POST")
        self.request(
            method,
            self.close_path.format(profile_id=profile_id),
            params=body if method == "GET" else None,
            json_body=body if method != "GET" else None,
        )

    def delete_profile(self, profile_id: str) -> None:
        body = self._profile_body(profile_id)
        body["dirIds"] = [body.pop("dirId")]
        method = _method(self.config, "delete_method", "POST")
        self.request(
            method,
            self.delete_path.format(profile_id=profile_id),
            params=body if method == "GET" else None,
            json_body=body if method != "GET" else None,
        )

    def _profile_body(self, profile_id: str) -> dict[str, Any]:
        body = {"dirId": _int_or_text(profile_id)}
        if self.workspace_id:
            body["workspaceId"] = _int_or_text(self.workspace_id)
        return body
