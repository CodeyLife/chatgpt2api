from __future__ import annotations

import base64
import json
import unittest
from typing import Any
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.accounts as accounts_module
from services.account_service import AccountService
from services.codex_agent_identity_service import (
    access_token_claims,
    create_agent_identity,
    extract_access_token,
    generate_ed25519_keypair,
    register_task,
)


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}


def make_jwt(payload: dict[str, Any]) -> str:
    def encode(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f'{encode({"alg": "none", "typ": "JWT"})}.{encode(payload)}.sig'


def access_token_payload() -> dict[str, Any]:
    return {
        "exp": 123,
        "iat": 45,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct_123",
            "chatgpt_user_id": "user_123",
            "chatgpt_plan_type": "plus",
        },
        "https://api.openai.com/profile": {"email": "test@example.com"},
    }


class FakeResponse:
    def __init__(self, status_code: int = 200, data: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return dict(self._data)


class MemoryStorage:
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self.accounts = list(accounts or [])

    def load_accounts(self) -> list[dict[str, Any]]:
        return list(self.accounts)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = list(accounts)

    def load_auth_keys(self) -> list[dict[str, Any]]:
        return []

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        pass

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def get_backend_info(self) -> dict[str, Any]:
        return {"type": "memory"}


class CodexAgentIdentityServiceTests(unittest.TestCase):
    def test_extract_access_token_accepts_session_json_or_plain_token(self) -> None:
        self.assertEqual(extract_access_token({"accessToken": " token-1 "}), "token-1")
        self.assertEqual(extract_access_token('{"accessToken":"token-2"}'), "token-2")
        self.assertEqual(extract_access_token("token-3"), "token-3")

    def test_access_token_claims_handles_valid_invalid_and_missing_values(self) -> None:
        token = make_jwt(access_token_payload())

        claims = access_token_claims(token)

        self.assertEqual(claims["account_id"], "acct_123")
        self.assertEqual(claims["chatgpt_user_id"], "user_123")
        self.assertEqual(claims["email"], "test@example.com")
        self.assertEqual(claims["plan_type"], "plus")
        self.assertEqual(access_token_claims("not-a-jwt")["plan_type"], "free")
        self.assertEqual(access_token_claims(make_jwt({}))["account_id"], "")

    def test_generate_ed25519_keypair_returns_pkcs8_private_key_and_ssh_public_key(self) -> None:
        private_key_b64, public_key_ssh = generate_ed25519_keypair()

        self.assertTrue(base64.b64decode(private_key_b64))
        self.assertTrue(public_key_ssh.startswith("ssh-ed25519 "))

    def test_register_task_signs_runtime_id_and_timestamp_payload(self) -> None:
        private_key_b64, _public_key = generate_ed25519_keypair()
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> FakeResponse:
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeResponse(data={"encrypted_task_id": "task_123"})

        with mock.patch("services.codex_agent_identity_service.requests.post", side_effect=fake_post):
            task_id = register_task("access-token", "runtime_123", private_key_b64)

        self.assertEqual(task_id, "task_123")
        self.assertIn("/v1/agent/runtime_123/task/register", captured["url"])
        self.assertRegex(captured["json"]["timestamp"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertTrue(base64.b64decode(captured["json"]["signature"]))

    def test_create_agent_identity_returns_warning_when_task_verification_fails(self) -> None:
        token = make_jwt(access_token_payload())

        with (
            mock.patch("services.codex_agent_identity_service.register_agent", return_value="runtime_123"),
            mock.patch("services.codex_agent_identity_service.register_task", side_effect=RuntimeError("task failed")),
        ):
            result = create_agent_identity(token, verify_task=True)

        self.assertEqual(result.auth_json["auth_mode"], "agent_identity")
        self.assertEqual(result.auth_json["agent_identity"]["agent_runtime_id"], "runtime_123")
        self.assertEqual(result.account_payload["access_token"], token)
        self.assertEqual(result.account_payload["source_type"], "codex")
        self.assertEqual(result.account_payload["agent_identity"]["agent_runtime_id"], "runtime_123")
        self.assertEqual(result.verify_warning, "task failed")

    def test_create_agent_identity_allows_opaque_token_and_uses_metadata(self) -> None:
        with mock.patch("services.codex_agent_identity_service.register_agent", return_value="runtime_123"):
            result = create_agent_identity(
                "token:4183315650",
                verify_task=False,
                metadata={
                    "email": "new@example.com",
                    "account_id": "acct_123",
                    "user_id": "user_123",
                    "plan_type": "plus",
                },
            )

        self.assertEqual(result.account_payload["access_token"], "token:4183315650")
        self.assertEqual(result.account_payload["export_type"], "codex_agent_identity")
        self.assertEqual(result.account_payload["email"], "new@example.com")
        self.assertEqual(result.account_payload["account_id"], "acct_123")
        self.assertEqual(result.account_payload["user_id"], "user_123")
        self.assertEqual(result.auth_json["agent_identity"]["plan_type"], "plus")

    def test_create_agent_identity_allows_missing_account_metadata(self) -> None:
        with mock.patch("services.codex_agent_identity_service.register_agent", return_value="runtime_123"):
            result = create_agent_identity("token:4183315650", verify_task=False)

        self.assertEqual(result.account_payload["access_token"], "token:4183315650")
        self.assertEqual(result.account_payload["account_id"], "")
        self.assertEqual(result.account_payload["user_id"], "")
        self.assertEqual(result.account_payload["agent_identity"]["agent_runtime_id"], "runtime_123")

    def test_account_import_preserves_agent_identity_and_plan_type_rule(self) -> None:
        token = make_jwt(access_token_payload())
        service = AccountService(MemoryStorage())

        result = service.add_account_items(
            [
                {
                    "access_token": token,
                    "source_type": "codex",
                    "export_type": "codex",
                    "plan_type": "plus",
                    "agent_identity": {
                        "agent_runtime_id": "runtime_123",
                        "agent_private_key": "private-key",
                    },
                }
            ]
        )

        account = service.get_account(token)
        self.assertEqual(result["added"], 1)
        self.assertIsNotNone(account)
        self.assertEqual(account["type"], "plus")
        self.assertEqual(account["source_type"], "codex")
        self.assertEqual(account["agent_identity"]["agent_private_key"], "private-key")


class CodexAgentIdentityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AccountService(MemoryStorage())
        self.account_patcher = mock.patch.object(accounts_module, "account_service", self.service)
        self.account_patcher.start()
        self.addCleanup(self.account_patcher.stop)
        app = FastAPI()
        app.include_router(accounts_module.create_router())
        self.client = TestClient(app)

    def test_codex_agent_identity_requires_admin_auth(self) -> None:
        response = self.client.post("/api/accounts/codex-agent-identity", json={"access_token": "token"})

        self.assertEqual(response.status_code, 401)

    def test_codex_agent_identity_accepts_session_json_and_imports_account(self) -> None:
        token = make_jwt(access_token_payload())

        with (
            mock.patch("services.codex_agent_identity_service.register_agent", return_value="runtime_123"),
            mock.patch("services.codex_agent_identity_service.register_task", return_value="task_123"),
        ):
            response = self.client.post(
                "/api/accounts/codex-agent-identity",
                headers=AUTH_HEADERS,
                json={"session_json": {"accessToken": token}},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["auth_json"]["auth_mode"], "agent_identity")
        self.assertEqual(payload["account_payload"]["agent_identity"]["agent_runtime_id"], "runtime_123")
        self.assertEqual(payload["added"], 1)
        self.assertEqual(self.service.get_account(token)["agent_identity"]["agent_runtime_id"], "runtime_123")

    def test_codex_agent_identity_accepts_opaque_token_session_json_and_imports_account(self) -> None:
        token = "token:4183315650"

        with (
            mock.patch("services.codex_agent_identity_service.register_agent", return_value="runtime_123"),
            mock.patch("services.codex_agent_identity_service.register_task", return_value="task_123"),
        ):
            response = self.client.post(
                "/api/accounts/codex-agent-identity",
                headers=AUTH_HEADERS,
                json={
                    "session_json": {
                        "accessToken": token,
                        "user": {"email": "new@example.com", "id": "user_123"},
                        "account": {"account_id": "acct_123", "plan_type": "plus"},
                    }
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["account_payload"]["export_type"], "codex_agent_identity")
        self.assertEqual(payload["account_payload"]["email"], "new@example.com")
        self.assertEqual(payload["account_payload"]["account_id"], "acct_123")
        self.assertEqual(self.service.get_account(token)["agent_identity"]["chatgpt_user_id"], "user_123")

    def test_codex_agent_identity_redacts_agent_registration_errors(self) -> None:
        token = make_jwt(access_token_payload())

        with mock.patch(
            "services.codex_agent_identity_service.requests.post",
            return_value=FakeResponse(400, text='{"access_token":"secret-token-value"}'),
        ):
            response = self.client.post(
                "/api/accounts/codex-agent-identity",
                headers=AUTH_HEADERS,
                json={"access_token": token, "verify_task": False},
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("secret-token-value", response.text)
        self.assertIn("[REDACTED]", response.text)


if __name__ == "__main__":
    unittest.main()
