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


if __name__ == "__main__":
    unittest.main()
