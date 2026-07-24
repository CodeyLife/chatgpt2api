import unittest

from services.register.roxy_browser import RoxyBrowserClient, _proxy_url_to_roxy_info


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self.payload


class RoxyBrowserClientTests(unittest.TestCase):
    def test_proxy_url_to_roxy_info(self):
        info = _proxy_url_to_roxy_info("socks5://user:pass@example.com:1080", "IPRust.io")

        self.assertEqual(info["protocol"], "SOCKS5")
        self.assertEqual(info["host"], "example.com")
        self.assertEqual(info["port"], "1080")
        self.assertEqual(info["proxyUserName"], "user")
        self.assertEqual(info["proxyPassword"], "pass")
        self.assertEqual(info["checkChannel"], "IPRust.io")

    def test_open_session_creates_profile_and_normalizes_debugger_port(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/browser/create"):
                return FakeResponse({"code": 0, "data": {"dirId": 123}})
            if url.endswith("/browser/open"):
                return FakeResponse({"code": 0, "data": {"debuggingPort": 9222}})
            raise AssertionError(url)

        client = RoxyBrowserClient(
            {
                "api_base": "http://127.0.0.1:50100",
                "api_token": "roxy-token",
                "workspace_id": "90143",
                "project_id": "97471",
                "one_profile_per_account": True,
                "open_headless": True,
                "create_use_proxy": True,
                "proxy_check_channel": "IPRust.io",
            },
            request_func=fake_request,
            sleep_func=lambda _seconds: None,
        )

        session = client.open_session("http://user:pass@proxy.example:8080")

        self.assertEqual(session.provider, "roxy")
        self.assertEqual(session.profile_id, "123")
        self.assertEqual(session.connect_url, "http://127.0.0.1:9222")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][2]["json"]["proxyInfo"]["host"], "proxy.example")
        self.assertEqual(calls[1][2]["json"]["workspaceId"], 90143)
        self.assertEqual(calls[1][2]["json"]["dirId"], 123)
        self.assertTrue(calls[1][2]["json"]["headless"])
        self.assertEqual(calls[1][2]["headers"]["token"], "roxy-token")

    def test_open_session_uses_ws_endpoint_when_returned(self):
        def fake_request(method, url, **kwargs):
            return FakeResponse({"code": 0, "data": {"wsEndpoint": "ws://127.0.0.1:9222/devtools/browser/1"}})

        client = RoxyBrowserClient(
            {"profile_id": "profile-1", "api_base": "http://roxy.local"},
            request_func=fake_request,
        )

        session = client.open_session()

        self.assertEqual(session.connect_url, "ws://127.0.0.1:9222/devtools/browser/1")
        self.assertEqual(session.profile_id, "profile-1")

    def test_close_session_closes_and_deletes_created_profile(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/browser/create"):
                return FakeResponse({"code": 0, "id": "profile-1"})
            if url.endswith("/browser/open"):
                return FakeResponse({"code": 0, "debuggerAddress": "127.0.0.1:9222"})
            return FakeResponse({"code": 0})

        client = RoxyBrowserClient(
            {
                "api_base": "http://roxy.local",
                "workspace_id": "90143",
                "one_profile_per_account": True,
                "delete_profile_after_run": True,
            },
            request_func=fake_request,
            sleep_func=lambda _seconds: None,
        )

        session = client.open_session()
        client.close_session(session)

        self.assertTrue(any(call[1].endswith("/browser/close") for call in calls))
        self.assertTrue(any(call[1].endswith("/browser/delete") for call in calls))


if __name__ == "__main__":
    unittest.main()
