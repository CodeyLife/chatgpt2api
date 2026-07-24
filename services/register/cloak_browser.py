from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable


class CloakBrowserUnavailable(RuntimeError):
    pass


@dataclass
class CloakBrowserSession:
    browser: Any
    context: Any
    page: Any
    profile_id: str = "cloakbrowser"
    raw: dict[str, Any] | None = None
    keep_open: bool = False

    def close(self) -> None:
        if self.keep_open:
            return
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser is not None and self.browser is not self.context:
                self.browser.close()
        except Exception:
            pass


def _bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_proxy(proxy: str) -> str:
    return str(proxy or "").strip().replace("socks5h://", "socks5://")


class CloakBrowserClient:
    def __init__(
        self,
        config: dict | None = None,
        *,
        importer: Callable[[str], Any] = importlib.import_module,
    ) -> None:
        self.config = config if isinstance(config, dict) else {}
        self.importer = importer

    def open_browser(self, proxy: str = "") -> CloakBrowserSession:
        try:
            cloakbrowser = self.importer("cloakbrowser")
        except Exception as exc:
            raise CloakBrowserUnavailable("未安装 cloakbrowser，请先安装 cloakbrowser[geoip]") from exc
        launch = getattr(cloakbrowser, "launch", None)
        launch_persistent_context = getattr(cloakbrowser, "launch_persistent_context", None)
        if launch is None or launch_persistent_context is None:
            raise CloakBrowserUnavailable("cloakbrowser 缺少 launch/launch_persistent_context")

        opts = self._launch_options(proxy)
        context_kwargs = self._context_options()
        user_data_dir = str(self.config.get("user_data_dir") or "").strip()
        if user_data_dir:
            context = launch_persistent_context(user_data_dir, **opts)
            browser = getattr(context, "browser", None) or context
        else:
            browser = launch(**opts)
            context = browser.new_context(**context_kwargs)
        page = context.new_page()
        timeout_ms = max(1000, int(self.config.get("timeout") or self.config.get("selenium_timeout") or 90) * 1000)
        try:
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
        except Exception:
            pass
        return CloakBrowserSession(
            browser=browser,
            context=context,
            page=page,
            raw={"options": {key: value for key, value in opts.items() if key != "license_key"}, "context": context_kwargs},
            keep_open=_bool(self.config, "keep_browser_open", False),
        )

    def _launch_options(self, proxy: str) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "headless": _bool(self.config, "headless", True),
            "humanize": _bool(self.config, "humanize", True),
            "geoip": _bool(self.config, "geoip", True),
        }
        proxy_url = _normalize_proxy(proxy) if _bool(self.config, "use_proxy", True) else ""
        if proxy_url:
            opts["proxy"] = proxy_url
        locale = str(self.config.get("locale") or "").strip()
        timezone = str(self.config.get("timezone") or "").strip()
        if locale:
            opts["locale"] = locale
        if timezone:
            opts["timezone"] = timezone
        license_key = str(self.config.get("license_key") or "").strip()
        if license_key:
            opts["license_key"] = license_key
        args = list(self.config.get("extra_args") if isinstance(self.config.get("extra_args"), list) else [])
        seed = str(self.config.get("fingerprint_seed") or "").strip()
        if seed:
            args.append(f"--fingerprint={seed}")
        if args:
            opts["args"] = args
        return opts

    def _context_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        locale = str(self.config.get("locale") or "").strip()
        timezone = str(self.config.get("timezone") or "").strip()
        accept_language = str(self.config.get("accept_language") or "").strip()
        if locale:
            options["locale"] = locale
        if timezone:
            options["timezone_id"] = timezone
        if accept_language:
            options["extra_http_headers"] = {"Accept-Language": accept_language}
        return options
