import unittest

from services.register.cloud_browser import BrowserUseClient, SkyvernClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class CloudBrowserTests(unittest.TestCase):
    def test_browser_use_builds_redacted_cdp_connect_url(self):
        session = BrowserUseClient(
            {
                "api_key": "sk-browser-use-secret",
                "cdp_base": "wss://connect.example",
                "proxy_country_code": "US",
                "profile_id": "profile-1",
                "session_timeout": 999,
            }
        ).open_session()

        self.assertEqual(session.provider, "browser_use")
        self.assertIn("apiKey=sk-browser-use-secret", session.connect_url)
        self.assertIn("proxyCountryCode=us", session.connect_url)
        self.assertIn("profileId=profile-1", session.connect_url)
        self.assertIn("timeout=240", session.connect_url)
        self.assertEqual(session.raw["query"]["apiKey"], "sk-b***cret")

    def test_browser_use_requires_api_key(self):
        with self.assertRaisesRegex(RuntimeError, "API key"):
            BrowserUseClient({}).open_session()

    def test_skyvern_open_session_polls_until_browser_address_available(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(("POST", url, kwargs))
            return FakeResponse(payload={"browser_session_id": "session-1"})

        def fake_get(url, **kwargs):
            calls.append(("GET", url, kwargs))
            return FakeResponse(payload={"browser_session_id": "session-1", "browser_address": "wss://skyvern.example/devtools"})

        client = SkyvernClient(
            {
                "api_key": "skyvern-secret",
                "api_base": "https://skyvern.example",
                "proxy_location": "jp",
                "browser_profile_id": "profile-1",
                "browser_session_timeout": 60,
            },
            post=fake_post,
            get=fake_get,
            sleep_func=lambda _seconds: None,
        )
        session = client.open_session()

        self.assertEqual(session.provider, "skyvern")
        self.assertEqual(session.session_id, "session-1")
        self.assertEqual(session.connect_url, "wss://skyvern.example/devtools")
        self.assertEqual(session.proxy_country_code, "RESIDENTIAL_JP")
        self.assertEqual(calls[0][2]["json"]["proxy_location"], "RESIDENTIAL_JP")
        self.assertEqual(session.cdp_headers["Authorization"], "Bearer skyvern-secret")

    def test_skyvern_create_error_is_reported(self):
        def fake_post(url, **kwargs):
            return FakeResponse(status_code=401, payload={"error": "bad key"})

        client = SkyvernClient({"api_key": "bad", "api_base": "https://skyvern.example"}, post=fake_post)

        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            client.create_browser_session()


if __name__ == "__main__":
    unittest.main()
