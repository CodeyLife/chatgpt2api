import unittest
from unittest.mock import patch

from services import cpa_service


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    responses = []
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "data": data, "timeout": timeout})
        return self.responses.pop(0)

    def close(self):
        pass


class FakeProxySettings:
    def build_session_kwargs(self, **kwargs):
        return {"verify": kwargs.get("verify")}


class CPACodexOAuthTests(unittest.TestCase):
    def setUp(self):
        FakeSession.responses = []
        FakeSession.calls = []

    def test_request_codex_auth_url_parses_state_from_payload_or_url(self):
        FakeSession.responses = [
            FakeResponse(payload={"data": {"authUrl": "https://auth.openai.com/oauth?state=from-url"}})
        ]
        pool = {"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"}

        with patch.object(cpa_service, "Session", FakeSession), patch.object(cpa_service, "proxy_settings", FakeProxySettings()):
            result = cpa_service.request_codex_auth_url(pool)

        self.assertEqual(result["auth_url"], "https://auth.openai.com/oauth?state=from-url")
        self.assertEqual(result["state"], "from-url")
        self.assertEqual(FakeSession.calls[0]["method"], "GET")
        self.assertEqual(FakeSession.calls[0]["url"], "https://cpa.example/v0/management/codex-auth-url")
        self.assertEqual(FakeSession.calls[0]["headers"]["Authorization"], "Bearer secret")

    def test_submit_codex_oauth_callback_imports_auth_json_when_present(self):
        FakeSession.responses = [
            FakeResponse(
                payload={
                    "auth_json": {
                        "access_token": "access-token",
                        "type": "codex_agent_identity",
                        "email": "agent@example.com",
                        "agent_identity": {
                            "agent_runtime_id": "runtime-1",
                            "agent_private_key": "private-key",
                        },
                    }
                }
            )
        ]
        added_payloads = []

        class FakeAccountService:
            def add_account_items(self, items):
                added_payloads.extend(items)
                return {"added": len(items), "skipped": 0, "items": items}

        pool = {"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"}
        with (
            patch.object(cpa_service, "Session", FakeSession),
            patch.object(cpa_service, "proxy_settings", FakeProxySettings()),
            patch.object(cpa_service, "account_service", FakeAccountService()),
        ):
            result = cpa_service.submit_codex_oauth_callback(pool, "https://callback.example/?code=abc")

        self.assertTrue(result["ok"])
        self.assertEqual(result["import_result"]["added"], 1)
        self.assertEqual(added_payloads[0]["access_token"], "access-token")
        self.assertEqual(added_payloads[0]["source_type"], "codex")
        self.assertEqual(added_payloads[0]["export_type"], "codex_agent_identity")
        self.assertEqual(FakeSession.calls[0]["method"], "POST")
        self.assertIn("https://callback.example", FakeSession.calls[0]["data"])


if __name__ == "__main__":
    unittest.main()
