from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

import requests


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudBrowserSession:
    connect_url: str
    provider: str
    api_key_present: bool
    proxy_country_code: str = ""
    profile_id: str = ""
    session_id: str = ""
    cdp_headers: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _redact_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


class BrowserUseClient:
    def __init__(self, config: dict | None = None) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.api_key = str(cfg.get("api_key") or "").strip()
        self.cdp_base = str(cfg.get("cdp_base") or "wss://connect.browser-use.com").strip().rstrip("?&")
        self.proxy_country_code = str(cfg.get("proxy_country_code") or "").strip().lower()
        self.profile_id = str(cfg.get("profile_id") or "").strip()
        self.session_timeout = max(1, min(240, int(cfg.get("session_timeout") or 240)))
        self.extra_query = cfg.get("extra_query") if isinstance(cfg.get("extra_query"), dict) else {}

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError("Browser Use API key is required")
        return self.api_key

    def open_session(self) -> CloudBrowserSession:
        api_key = self.require_api_key()
        query: dict[str, str] = {"apiKey": api_key, "timeout": str(self.session_timeout)}
        if self.proxy_country_code:
            query["proxyCountryCode"] = self.proxy_country_code
        if self.profile_id:
            query["profileId"] = self.profile_id
        for key, value in self.extra_query.items():
            text = str(value or "").strip()
            if text:
                query[str(key)] = text
        safe_query = dict(query)
        safe_query["apiKey"] = _redact_secret(api_key)
        logger.info(
            "Browser Use session connect URL built: base=%s proxy=%s profile=%s timeout=%s",
            self.cdp_base,
            self.proxy_country_code or "-",
            self.profile_id or "-",
            self.session_timeout,
        )
        return CloudBrowserSession(
            connect_url=f"{self.cdp_base}?{urlencode(query)}",
            provider="browser_use",
            api_key_present=True,
            proxy_country_code=self.proxy_country_code,
            profile_id=self.profile_id,
            raw={"base": self.cdp_base, "query": safe_query},
        )


class SkyvernClient:
    def __init__(
        self,
        config: dict | None = None,
        *,
        post: Callable[..., Any] | None = None,
        get: Callable[..., Any] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.api_key = str(cfg.get("api_key") or "").strip()
        self.api_base = str(cfg.get("api_base") or "https://api.skyvern.com").strip().rstrip("/")
        self.proxy_location = self.normalize_proxy_location(str(cfg.get("proxy_location") or ""))
        self.profile_id = str(cfg.get("browser_profile_id") or "").strip()
        self.session_timeout = max(1, int(cfg.get("browser_session_timeout") or 60))
        self.browser_type = self.normalize_browser_type(str(cfg.get("browser_type") or ""))
        self.generate_browser_profile = bool(cfg.get("generate_browser_profile", False))
        self.ad_blocker = bool(cfg.get("ad_blocker", True))
        self._post = post or requests.post
        self._get = get or requests.get
        self._sleep = sleep_func

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError("Skyvern API key is required")
        return self.api_key

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.require_api_key(),
            "content-type": "application/json",
            "accept": "application/json",
        }

    def cdp_headers(self) -> dict[str, str]:
        api_key = self.require_api_key()
        return {"x-api-key": api_key, "Authorization": f"Bearer {api_key}"}

    @staticmethod
    def normalize_proxy_location(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        upper = text.upper().replace("-", "_")
        aliases = {
            "JP": "RESIDENTIAL_JP",
            "JA": "RESIDENTIAL_JP",
            "JAPAN": "RESIDENTIAL_JP",
            "US": "RESIDENTIAL",
            "USA": "RESIDENTIAL",
            "GB": "RESIDENTIAL_GB",
            "UK": "RESIDENTIAL_GB",
            "IN": "RESIDENTIAL_IN",
            "DE": "RESIDENTIAL_DE",
            "FR": "RESIDENTIAL_FR",
            "AU": "RESIDENTIAL_AU",
            "CA": "RESIDENTIAL_CA",
            "KR": "RESIDENTIAL_KR",
            "NONE": "NONE",
        }
        if upper in aliases:
            return aliases[upper]
        if len(upper) == 2:
            return f"RESIDENTIAL_{upper}"
        return upper

    @staticmethod
    def normalize_browser_type(value: str) -> str:
        text = str(value or "").strip().lower().replace("_", "-")
        aliases = {
            "": "stealth-chromium",
            "chromium": "stealth-chromium",
            "chromium-headful": "stealth-chromium",
            "headful": "stealth-chromium",
            "stealth": "stealth-chromium",
            "stealth-chrome": "stealth-chromium",
            "edge": "msedge",
            "microsoft-edge": "msedge",
        }
        return aliases.get(text, text)

    @staticmethod
    def _json(response, label: str) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception:
            data = {"text": str(getattr(response, "text", "") or "")[:1000]}
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code >= 400:
            raise RuntimeError(f"Skyvern {label} HTTP {status_code}: {data}")
        if not isinstance(data, dict):
            raise RuntimeError(f"Skyvern {label} response is not an object: {data!r}")
        return data

    @staticmethod
    def session_id(data: dict[str, Any]) -> str:
        return str(data.get("browser_session_id") or data.get("session_id") or data.get("id") or "").strip()

    @staticmethod
    def browser_address(data: dict[str, Any]) -> str:
        return str(data.get("browser_address") or data.get("cdp_url") or data.get("connect_url") or data.get("ws_endpoint") or "").strip()

    def create_browser_session(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timeout": self.session_timeout,
            "browser_type": self.browser_type,
            "generate_browser_profile": self.generate_browser_profile,
            "ad_blocker": self.ad_blocker,
        }
        if self.profile_id:
            payload["browser_profile_id"] = self.profile_id
        if self.proxy_location:
            payload["proxy_location"] = self.proxy_location
        logger.info(
            "Creating Skyvern browser session: base=%s proxy=%s profile=%s timeout=%s",
            self.api_base,
            self.proxy_location or "-",
            self.profile_id or "-",
            self.session_timeout,
        )
        response = self._post(f"{self.api_base}/v1/browser_sessions", headers=self.headers(), json=payload, timeout=30)
        return self._json(response, "create browser session")

    def get_browser_session(self, session_id: str) -> dict[str, Any]:
        response = self._get(f"{self.api_base}/v1/browser_sessions/{session_id}", headers=self.headers(), timeout=20)
        return self._json(response, "get browser session")

    def close_browser_session(self, session_id: str) -> dict[str, Any]:
        response = self._post(f"{self.api_base}/v1/browser_sessions/{session_id}/close", headers=self.headers(), json={}, timeout=20)
        return self._json(response, "close browser session")

    def open_session(self) -> CloudBrowserSession:
        data = self.create_browser_session()
        session_id = self.session_id(data)
        address = self.browser_address(data)
        latest = data
        if session_id and not address:
            for _ in range(10):
                self._sleep(1)
                latest = self.get_browser_session(session_id)
                address = self.browser_address(latest)
                if address:
                    data = {**data, "latest": latest}
                    break
        if not session_id:
            session_id = self.session_id(latest)
        if not address:
            raise RuntimeError(f"Skyvern browser session missing browser_address/cdp_url: {latest}")
        return CloudBrowserSession(
            connect_url=address,
            provider="skyvern",
            api_key_present=True,
            proxy_country_code=self.proxy_location,
            profile_id=self.profile_id,
            session_id=session_id,
            cdp_headers=self.cdp_headers(),
            raw=data,
        )
