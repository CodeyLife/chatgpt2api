import unittest
from unittest.mock import patch

from services import sub2api_service


class FakeResponse:
    ok = True

    def json(self):
        return {
            "code": 0,
            "data": {
                "accounts": [
                    {
                        "id": "account-1",
                        "credentials": {
                            "access_token": "access-token",
                            "refresh_token": "refresh-token",
                            "id_token": "id-token",
                            "email": "user@example.com",
                        },
                    }
                ]
            },
        }


class FakeSession:
    def __init__(self, **kwargs):
        pass

    def get(self, *args, **kwargs):
        return FakeResponse()

    def close(self):
        pass


class Sub2APIImportTests(unittest.TestCase):
    @patch("services.sub2api_service.Session", FakeSession)
    def test_export_preserves_refresh_and_id_tokens_for_import(self) -> None:
        payloads, errors = sub2api_service._fetch_account_payloads_for_accounts(
            {"base_url": "https://sub2api.example", "api_key": "test-key"},
            ["account-1"],
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            payloads,
            [
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "email": "user@example.com",
                    "account_id": "account-1",
                    "source_type": "codex",
                }
            ],
        )

    def test_export_preserves_agent_identity_for_import(self) -> None:
        payload = sub2api_service._account_import_payload(
            {
                "id": "account-identity",
                "credentials": {
                    "access_token": "access-token",
                    "email": "agent@example.com",
                    "plan_type": "plus",
                    "agent_identity": {
                        "agent_runtime_id": "runtime_123",
                        "agent_private_key": "private-key",
                        "account_id": "acct_123",
                        "chatgpt_user_id": "user_123",
                        "email": "agent@example.com",
                        "plan_type": "plus",
                        "chatgpt_account_is_fedramp": False,
                    },
                },
            }
        )

        self.assertEqual(payload["access_token"], "access-token")
        self.assertEqual(payload["source_type"], "codex")
        self.assertEqual(payload["export_type"], "codex_agent_identity")
        self.assertEqual(payload["plan_type"], "plus")
        self.assertEqual(payload["agent_identity"]["agent_runtime_id"], "runtime_123")

    def test_export_preserves_agent_identity_from_auth_json_for_import(self) -> None:
        payload = sub2api_service._account_import_payload(
            {
                "access_token": "access-token",
                "auth_mode": "agent_identity",
                "agent_identity": {
                    "agent_runtime_id": "runtime_123",
                    "agent_private_key": "private-key",
                },
            }
        )

        self.assertEqual(payload["access_token"], "access-token")
        self.assertEqual(payload["export_type"], "codex_agent_identity")
        self.assertEqual(payload["agent_identity"]["agent_private_key"], "private-key")


if __name__ == "__main__":
    unittest.main()
