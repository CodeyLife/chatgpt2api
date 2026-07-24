import base64
import json
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from services import codex_oauth_service


def make_jwt(payload):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(payload)}.sig"


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
    def __init__(self, response):
        self.response = response
        self.posts = []
        self.closed = False

    def post(self, url, headers=None, data=None, timeout=None):
        self.posts.append({"url": url, "headers": headers or {}, "data": data, "timeout": timeout})
        return self.response

    def close(self):
        self.closed = True


class CodexOAuthServiceTests(unittest.TestCase):
    def test_build_authorize_url_contains_pkce_and_codex_cli_params(self):
        result = codex_oauth_service.build_authorize_url(state="state-1", code_challenge="challenge-1")
        params = parse_qs(urlparse(result["auth_url"]).query)

        self.assertEqual(result["state"], "state-1")
        self.assertEqual(result["code_challenge"], "challenge-1")
        self.assertTrue(result["code_verifier"])
        self.assertEqual(params["client_id"], [codex_oauth_service.CODEX_CLIENT_ID])
        self.assertEqual(params["redirect_uri"], [codex_oauth_service.CODEX_REDIRECT_URI])
        self.assertEqual(params["codex_cli_simplified_flow"], ["true"])

    def test_parse_callback_code_validates_state(self):
        result = codex_oauth_service.parse_callback_code(
            "http://localhost:1455/auth/callback?code=code-1&state=state-1",
            expected_state="state-1",
        )

        self.assertEqual(result, {"code": "code-1", "state": "state-1"})

        with self.assertRaisesRegex(codex_oauth_service.CodexOAuthError, "state mismatch"):
            codex_oauth_service.parse_callback_code(
                "http://localhost:1455/auth/callback?code=code-1&state=state-2",
                expected_state="state-1",
            )

        with self.assertRaisesRegex(codex_oauth_service.CodexOAuthError, "state mismatch"):
            codex_oauth_service.parse_callback_code(
                "http://localhost:1455/auth/callback?code=code-1",
                expected_state="state-1",
            )

    def test_exchange_code_posts_form_payload(self):
        response = FakeResponse(payload={"access_token": "access-token", "expires_in": 3600})
        session = FakeSession(response)

        payload = codex_oauth_service.exchange_code("code-1", "verifier-1", session=session)

        self.assertEqual(payload["access_token"], "access-token")
        self.assertEqual(session.posts[0]["url"], codex_oauth_service.CODEX_TOKEN_URL)
        self.assertIn("grant_type=authorization_code", session.posts[0]["data"])
        self.assertIn("code_verifier=verifier-1", session.posts[0]["data"])
        self.assertFalse(session.closed)

    def test_finish_oauth_callback_builds_and_imports_codex_auth_json(self):
        id_token = make_jwt(
            {
                "sub": "user-1",
                "email": "agent@example.com",
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct-1",
                    "chatgpt_plan_type": "plus",
                },
            }
        )
        response = FakeResponse(
            payload={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "id_token": id_token,
                "expires_in": 3600,
            }
        )
        session = FakeSession(response)
        imported = []

        class FakeAccountService:
            def add_account_items(self, items):
                imported.extend(items)
                return {"added": len(items), "skipped": 0, "items": items}

        with patch.object(codex_oauth_service, "account_service", FakeAccountService()):
            result = codex_oauth_service.finish_oauth_callback(
                "http://localhost:1455/auth/callback?code=code-1&state=state-1",
                "verifier-1",
                expected_state="state-1",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["auth_json"]["type"], "codex")
        self.assertEqual(result["auth_json"]["email"], "agent@example.com")
        self.assertEqual(result["auth_json"]["account_id"], "acct-1")
        self.assertEqual(result["auth_json"]["plan_type"], "plus")
        self.assertEqual(imported[0]["access_token"], "access-token")
        self.assertNotIn("access_token", result["token_response"])
        self.assertNotIn("refresh_token", result["token_response"])
        self.assertNotIn("id_token", result["token_response"])


if __name__ == "__main__":
    unittest.main()
