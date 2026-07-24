from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from utils.chromium_sentinel import (
    ChromiumSentinelSession,
    DEFAULT_SENTINEL_SDK_URL as DEFAULT_CHROMIUM_SENTINEL_SDK_URL,
    build_chromium_sentinel_token,
)
from utils.fingerprint import BrowserProfile
from utils.sentinel import build_sentinel_tokens


LogCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class SentinelOptions:
    browser_enabled: bool = False
    browser_headless: bool = True
    browser_timeout: float = 35.0
    browser_chrome_path: str = ""
    browser_sdk_url: str = ""
    browser_fallback: bool = True

    @classmethod
    def from_runtime_config(cls, config: dict | None) -> "SentinelOptions":
        cfg = config if isinstance(config, dict) else {}
        return cls(
            browser_enabled=_truthy(cfg.get("sentinel_browser_enabled"), True),
            browser_headless=_truthy(cfg.get("sentinel_browser_headless"), True),
            browser_timeout=max(5.0, _float_value(cfg.get("sentinel_browser_timeout"), 35.0)),
            browser_chrome_path=str(cfg.get("sentinel_browser_chrome_path") or "").strip(),
            browser_sdk_url=str(cfg.get("sentinel_browser_sdk_url") or "").strip(),
            browser_fallback=_truthy(cfg.get("sentinel_browser_fallback"), True),
        )

    def as_openai_register_kwargs(self) -> dict[str, Any]:
        return {
            "sentinel_browser_enabled": self.browser_enabled,
            "sentinel_browser_headless": self.browser_headless,
            "sentinel_browser_timeout": self.browser_timeout,
            "sentinel_browser_chrome_path": self.browser_chrome_path,
            "sentinel_browser_sdk_url": self.browser_sdk_url,
            "sentinel_browser_fallback": self.browser_fallback,
        }


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float_value(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def challenge_cookie_from_sentinel_header(header_value: str) -> str:
    try:
        data = json.loads(header_value)
    except Exception:
        return ""
    c_value = str(data.get("c") or "").strip() if isinstance(data, dict) else ""
    return f"0{c_value}" if c_value else ""


def create_chromium_sentinel_session(
    profile: BrowserProfile,
    options: SentinelOptions,
    *,
    session_factory: Callable[..., ChromiumSentinelSession] = ChromiumSentinelSession,
) -> ChromiumSentinelSession | None:
    if not options.browser_enabled:
        return None
    return session_factory(
        user_agent=profile.user_agent,
        sdk_url=options.browser_sdk_url or DEFAULT_CHROMIUM_SENTINEL_SDK_URL,
        screen_resolution=profile.screen_resolution,
        headless=options.browser_headless,
        chrome_path=options.browser_chrome_path,
        timeout=options.browser_timeout,
    )


def build_sentinel_headers(
    session: Any,
    device_id: str,
    flow: str,
    profile: BrowserProfile,
    *,
    options: SentinelOptions | None = None,
    browser_provider: Any = None,
    browser_session: ChromiumSentinelSession | None = None,
    pow_builder: Callable[..., tuple[str, str, str]] | None = None,
    log: LogCallback | None = None,
) -> dict[str, str]:
    sentinel_options = options or SentinelOptions()
    if sentinel_options.browser_enabled:
        try:
            if browser_session is not None:
                browser_result = browser_session.get_token(flow=flow, device_id=device_id)
            elif browser_provider is not None:
                browser_result = browser_provider.token(flow=flow, device_id=device_id)
            else:
                browser_result = build_chromium_sentinel_token(
                    flow=flow,
                    device_id=device_id,
                    user_agent=profile.user_agent,
                    sdk_url=sentinel_options.browser_sdk_url or DEFAULT_CHROMIUM_SENTINEL_SDK_URL,
                    screen_resolution=profile.screen_resolution,
                    headless=sentinel_options.browser_headless,
                    chrome_path=sentinel_options.browser_chrome_path,
                    timeout=sentinel_options.browser_timeout,
                )
            headers = {"openai-sentinel-token": browser_result.token}
            if browser_result.so_token:
                headers["openai-sentinel-so-token"] = browser_result.so_token
            if not challenge_cookie_from_sentinel_header(browser_result.token):
                raise RuntimeError("Chromium Sentinel token missing c field")
            return headers
        except Exception as error:
            if not sentinel_options.browser_fallback:
                raise RuntimeError(f"chromium_sentinel_failed: {error}") from error
            if log is not None:
                log(f"Chromium Sentinel 获取失败，回退后端 PoW: {error}", "yellow")

    builder = pow_builder or build_sentinel_tokens
    sentinel_val, _oai_sc_val, so_val = builder(
        session,
        device_id,
        flow,
        user_agent=profile.user_agent,
        sec_ch_ua=profile.sec_ch_ua,
        screen_resolution=profile.screen_resolution,
        hardware_concurrency=profile.hardware_concurrency,
        sec_ch_ua_platform=profile.sec_ch_ua_platform,
        sdk_url=sentinel_options.browser_sdk_url,
    )
    headers = {"openai-sentinel-token": sentinel_val}
    if so_val:
        headers["openai-sentinel-so-token"] = so_val
    return headers
