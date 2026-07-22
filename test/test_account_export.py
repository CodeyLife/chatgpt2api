import base64
import json
import unittest
from typing import Any

from services.account_service import AccountService


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


def make_jwt(payload: dict[str, Any]) -> str:
    def encode(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f'{encode({"alg": "none", "typ": "JWT"})}.{encode(payload)}.sig'


class AccountExportTests(unittest.TestCase):
    def test_build_export_items_uses_codex_shape_and_jwt_claims(self) -> None:
        access_token = make_jwt(
            {
                "exp": 0,
                "iat": 3600,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"},
                "https://api.openai.com/profile": {"email": "test@example.com"},
            }
        )
        id_token = make_jwt({"email": "fallback@example.com"})
        service = AccountService(
            MemoryStorage(
                [
                    {
                        "access_token": access_token,
                        "id_token": id_token,
                        "refresh_token": "rt_test",
                    }
                ]
            )
        )

        [item] = service.build_export_items([access_token])

        self.assertEqual(item["type"], "codex")
        self.assertEqual(item["email"], "test@example.com")
        self.assertEqual(item["expired"], "1970-01-01T08:00:00+08:00")
        self.assertEqual(item["account_id"], "acct_123")
        self.assertEqual(item["access_token"], access_token)
        self.assertEqual(item["last_refresh"], "1970-01-01T09:00:00+08:00")
        self.assertEqual(item["id_token"], id_token)
        self.assertEqual(item["refresh_token"], "rt_test")

    def test_build_export_items_skips_accounts_missing_complete_tokens(self) -> None:
        complete_access_token = make_jwt({"exp": 0})
        complete_id_token = make_jwt({"email": "complete@example.com"})
        service = AccountService(
            MemoryStorage(
                [
                    {"access_token": "only_access"},
                    {"access_token": "missing_id", "refresh_token": "rt_missing_id"},
                    {"access_token": complete_access_token, "id_token": complete_id_token, "refresh_token": "rt_complete"},
                ]
            )
        )

        items = service.build_export_items()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["access_token"], complete_access_token)
        self.assertEqual(items[0]["id_token"], complete_id_token)
        self.assertEqual(items[0]["refresh_token"], "rt_complete")

    def test_build_export_items_exports_codex_agent_identity_accounts(self) -> None:
        service = AccountService(
            MemoryStorage(
                [
                    {
                        "access_token": "access-token",
                        "source_type": "codex",
                        "email": "agent@example.com",
                        "account_id": "acct_agent",
                        "user_id": "user_agent",
                        "type": "Plus",
                        "agent_identity": {
                            "agent_runtime_id": "runtime_123",
                            "agent_private_key": "private-key",
                            "account_id": "acct_agent",
                            "chatgpt_user_id": "user_agent",
                            "email": "agent@example.com",
                            "plan_type": "plus",
                            "chatgpt_account_is_fedramp": False,
                        },
                    }
                ]
            )
        )

        [item] = service.build_export_items(["access-token"])

        self.assertEqual(item["type"], "codex_agent_identity")
        self.assertEqual(item["auth_mode"], "agent_identity")
        self.assertEqual(item["source_type"], "codex")
        self.assertEqual(item["export_type"], "codex_agent_identity")
        self.assertEqual(item["access_token"], "access-token")
        self.assertEqual(item["agent_identity"]["agent_runtime_id"], "runtime_123")

    def test_add_account_items_preserves_export_fields_without_overwriting_plan_type(self) -> None:
        service = AccountService(MemoryStorage())

        result = service.add_account_items(
            [
                {
                    "type": "codex",
                    "access_token": "access_token_test",
                    "refresh_token": "rt_test",
                    "account_id": "acct_123",
                }
            ]
        )

        account = service.get_account("access_token_test")
        self.assertEqual(result["added"], 1)
        self.assertIsNotNone(account)
        self.assertEqual(account["type"], "free")
        self.assertEqual(account["export_type"], "codex")
        self.assertEqual(account["refresh_token"], "rt_test")
        self.assertEqual(account["account_id"], "acct_123")

    def test_add_account_items_treats_codex_agent_identity_type_as_export_format(self) -> None:
        service = AccountService(MemoryStorage())

        service.add_account_items(
            [
                {
                    "type": "codex_agent_identity",
                    "access_token": "access_token_test",
                    "plan_type": "plus",
                    "agent_identity": {
                        "agent_runtime_id": "runtime_123",
                        "agent_private_key": "private-key",
                    },
                }
            ]
        )

        account = service.get_account("access_token_test")
        self.assertEqual(account["type"], "plus")
        self.assertEqual(account["export_type"], "codex_agent_identity")
        self.assertEqual(account["source_type"], "codex")
        self.assertEqual(account["agent_identity"]["agent_runtime_id"], "runtime_123")


if __name__ == "__main__":
    unittest.main()
