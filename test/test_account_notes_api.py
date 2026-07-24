import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.accounts import create_router


class FakeAccountService:
    def __init__(self):
        self.calls = []

    def update_account_notes(self, access_tokens, note):
        self.calls.append({"access_tokens": access_tokens, "note": note})
        return {"updated": len(access_tokens), "skipped": 0, "items": [], "skipped_items": []}


class AccountNotesAPITests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(create_router())
        self.client = TestClient(app)

    def test_bulk_notes_requires_admin_and_updates_unique_tokens(self):
        fake_service = FakeAccountService()
        with (
            patch("api.accounts.require_admin", return_value={"role": "admin"}) as require_admin,
            patch("api.accounts.account_service", fake_service),
        ):
            response = self.client.post(
                "/api/accounts/notes",
                headers={"Authorization": "Bearer test"},
                json={"access_tokens": ["token-1", "token-1", "token-2"], "note": "batch"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)
        self.assertEqual(fake_service.calls, [{"access_tokens": ["token-1", "token-2"], "note": "batch"}])
        require_admin.assert_called_once_with("Bearer test")


if __name__ == "__main__":
    unittest.main()
