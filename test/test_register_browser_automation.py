import unittest
from unittest.mock import patch

from services.register.browser_automation import (
    BrowserAutomationUnavailable,
    CloudBrowserSessionConnector,
    detect_playwright_runtime,
    detect_sentinel_runtime,
    require_playwright_sync_api,
)
from services.register.cloud_browser import CloudBrowserSession


class BrowserAutomationTests(unittest.TestCase):
    def test_detect_playwright_runtime_reports_missing_dependency(self):
        def importer(name):
            raise ModuleNotFoundError(name)

        status = detect_playwright_runtime(importer)

        self.assertFalse(status.available)
        self.assertIn("playwright.sync_api", status.error)
        with self.assertRaisesRegex(BrowserAutomationUnavailable, "Playwright runtime"):
            require_playwright_sync_api(importer)

    def test_connects_cloud_browser_session_with_cdp_headers(self):
        calls = []

        class FakeBrowser:
            def close(self):
                calls.append(("browser.close",))

        class FakeChromium:
            def connect_over_cdp(self, url, **kwargs):
                calls.append(("connect", url, kwargs))
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakeManager:
            def start(self):
                calls.append(("start",))
                return FakePlaywright()

            def stop(self):
                calls.append(("stop",))

        class FakeModule:
            __version__ = "1.49.0"

            @staticmethod
            def sync_playwright():
                return FakeManager()

        def importer(name):
            self.assertEqual(name, "playwright.sync_api")
            return FakeModule

        session = CloudBrowserSession(
            connect_url="wss://browser.example/devtools",
            provider="skyvern",
            api_key_present=True,
            cdp_headers={"Authorization": "Bearer secret"},
        )

        connected = CloudBrowserSessionConnector(importer=importer, timeout_ms=500).connect(session)
        self.assertEqual(connected.provider, "skyvern")
        self.assertEqual(calls[0], ("start",))
        self.assertEqual(calls[1][0], "connect")
        self.assertEqual(calls[1][1], "wss://browser.example/devtools")
        self.assertEqual(calls[1][2]["headers"], {"Authorization": "Bearer secret"})
        self.assertEqual(calls[1][2]["timeout"], 1000)

        connected.close()

        self.assertEqual(calls[-2:], [("browser.close",), ("stop",)])

    def test_detect_sentinel_runtime_uses_configured_chrome_path(self):
        with patch("services.register.browser_automation._find_chrome", return_value="C:/Chrome/chrome.exe") as mocked:
            status = detect_sentinel_runtime("C:/custom/chrome.exe")

        self.assertTrue(status.available)
        self.assertEqual(status.chrome_path, "C:/Chrome/chrome.exe")
        mocked.assert_called_once_with("C:/custom/chrome.exe")

    def test_detect_sentinel_runtime_reports_error(self):
        with patch("services.register.browser_automation._find_chrome", side_effect=RuntimeError("missing chrome")):
            status = detect_sentinel_runtime()

        self.assertFalse(status.available)
        self.assertIn("missing chrome", status.error)


if __name__ == "__main__":
    unittest.main()
