import json
import unittest
from unittest.mock import patch

from services.register.sentinel_service import SentinelOptions, build_sentinel_headers, challenge_cookie_from_sentinel_header
from utils.fingerprint import DEFAULT_PROFILE


class FakeBrowserResult:
    token = json.dumps({"c": "challenge", "id": "device-1", "flow": "flow"})
    so_token = "so-token"


class FakeBrowserProvider:
    def token(self, *, flow: str, device_id: str):
        self.flow = flow
        self.device_id = device_id
        return FakeBrowserResult()


class SentinelServiceTests(unittest.TestCase):
    def test_challenge_cookie_from_sentinel_header(self):
        self.assertEqual(challenge_cookie_from_sentinel_header(json.dumps({"c": "abc"})), "0abc")
        self.assertEqual(challenge_cookie_from_sentinel_header("not-json"), "")

    def test_browser_provider_headers_are_used_when_available(self):
        provider = FakeBrowserProvider()

        headers = build_sentinel_headers(
            object(),
            "device-1",
            "username_password_create",
            DEFAULT_PROFILE,
            options=SentinelOptions(browser_enabled=True),
            browser_provider=provider,
        )

        self.assertEqual(headers["openai-sentinel-token"], FakeBrowserResult.token)
        self.assertEqual(headers["openai-sentinel-so-token"], "so-token")
        self.assertEqual(provider.flow, "username_password_create")
        self.assertEqual(provider.device_id, "device-1")

    def test_browser_failure_falls_back_to_pow(self):
        logs = []

        class FailingProvider:
            def token(self, *, flow: str, device_id: str):
                raise RuntimeError("browser failed")

        with patch(
            "services.register.sentinel_service.build_sentinel_tokens",
            return_value=("pow-token", "cookie", ""),
        ) as build_pow:
            headers = build_sentinel_headers(
                object(),
                "device-1",
                "flow",
                DEFAULT_PROFILE,
                options=SentinelOptions(browser_enabled=True, browser_fallback=True),
                browser_provider=FailingProvider(),
                log=lambda message, color: logs.append((message, color)),
            )

        self.assertEqual(headers, {"openai-sentinel-token": "pow-token"})
        self.assertIn("Chromium Sentinel 获取失败", logs[0][0])
        self.assertEqual(logs[0][1], "yellow")
        build_pow.assert_called_once()

    def test_browser_failure_without_fallback_raises(self):
        class FailingProvider:
            def token(self, *, flow: str, device_id: str):
                raise RuntimeError("browser failed")

        with self.assertRaisesRegex(RuntimeError, "chromium_sentinel_failed"):
            build_sentinel_headers(
                object(),
                "device-1",
                "flow",
                DEFAULT_PROFILE,
                options=SentinelOptions(browser_enabled=True, browser_fallback=False),
                browser_provider=FailingProvider(),
            )


if __name__ == "__main__":
    unittest.main()
