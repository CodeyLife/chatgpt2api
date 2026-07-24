from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

from services.register.cloud_browser import CloudBrowserSession
from utils.chromium_sentinel import _find_chrome


class BrowserAutomationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaywrightRuntimeStatus:
    available: bool
    version: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "version": self.version, "error": self.error}


@dataclass(frozen=True)
class SentinelRuntimeStatus:
    available: bool
    chrome_path: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "chrome_path": self.chrome_path, "error": self.error}


def _module_version(module: Any) -> str:
    version = str(getattr(module, "__version__", "") or "").strip()
    if version:
        return version
    try:
        metadata = importlib.import_module("importlib.metadata")
        return str(metadata.version("playwright"))
    except Exception:
        return ""


def detect_playwright_runtime(
    importer: Callable[[str], Any] = importlib.import_module,
) -> PlaywrightRuntimeStatus:
    try:
        module = importer("playwright.sync_api")
    except Exception as exc:
        return PlaywrightRuntimeStatus(available=False, error=str(exc) or exc.__class__.__name__)
    if not hasattr(module, "sync_playwright"):
        return PlaywrightRuntimeStatus(available=False, error="playwright.sync_api missing sync_playwright")
    return PlaywrightRuntimeStatus(available=True, version=_module_version(module))


def require_playwright_sync_api(
    importer: Callable[[str], Any] = importlib.import_module,
) -> Any:
    status = detect_playwright_runtime(importer)
    if not status.available:
        raise BrowserAutomationUnavailable(f"Playwright runtime is not available: {status.error}")
    return importer("playwright.sync_api")


def detect_sentinel_runtime(chrome_path: str = "") -> SentinelRuntimeStatus:
    try:
        resolved = _find_chrome(chrome_path)
    except Exception as exc:
        return SentinelRuntimeStatus(available=False, error=str(exc) or exc.__class__.__name__)
    return SentinelRuntimeStatus(available=True, chrome_path=resolved)


@dataclass
class PlaywrightCloudBrowser:
    provider: str
    browser: Any
    playwright: Any
    manager: Any
    session: CloudBrowserSession

    def close(self) -> None:
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            self.browser = None
            manager = self.manager
            self.manager = None
            if manager is not None:
                manager.stop()


class CloudBrowserSessionConnector:
    def __init__(
        self,
        *,
        importer: Callable[[str], Any] = importlib.import_module,
        timeout_ms: int = 30000,
    ) -> None:
        self.importer = importer
        self.timeout_ms = max(1000, int(timeout_ms or 30000))

    def connect(self, session: CloudBrowserSession) -> PlaywrightCloudBrowser:
        sync_api = require_playwright_sync_api(self.importer)
        manager = sync_api.sync_playwright()
        playwright = manager.start()
        try:
            kwargs: dict[str, Any] = {"timeout": self.timeout_ms}
            if session.cdp_headers:
                kwargs["headers"] = session.cdp_headers
            browser = playwright.chromium.connect_over_cdp(session.connect_url, **kwargs)
        except Exception:
            manager.stop()
            raise
        return PlaywrightCloudBrowser(
            provider=session.provider,
            browser=browser,
            playwright=playwright,
            manager=manager,
            session=session,
        )


def browser_automation_status(config: dict | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    return {
        "playwright": detect_playwright_runtime().as_dict(),
        "sentinel": detect_sentinel_runtime(str(cfg.get("sentinel_browser_chrome_path") or "")).as_dict(),
    }
