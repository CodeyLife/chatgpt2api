from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.codex import create_router


class FakeCodexManagementService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def list(self):
        return {"summary": {"total": 1, "exported": 0, "unexported": 1, "retrying": 0}, "accounts": [{"filename": "codex-a.json", "email": "a@example.com"}]}

    def read_credential(self, filename: str):
        self.calls.append(("read_credential", filename))
        return b'{"email":"a@example.com"}\n', filename

    def mark_exported(self, filename: str):
        self.calls.append(("mark_exported", filename))

    def download_bulk(self, filenames: list[str]):
        self.calls.append(("download_bulk", filenames))
        return b"{}\n", "codex-bulk.json", "application/json"

    def request_stop(self, email: str):
        self.calls.append(("request_stop", email))
        return {"ok": True, "running": False}

    def reset_retrying(self, email: str, status: str):
        self.calls.append(("reset_retrying", {"email": email, "status": status}))
        return {"ok": True, "status": status}

    def retry(self, email: str, *, provider: str = "", cpa_pool_id: str = ""):
        self.calls.append(("retry", {"email": email, "provider": provider, "cpa_pool_id": cpa_pool_id}))
        return {"ok": True, "message": "started"}

    def retry_bulk(self, emails: list[str], *, workers: int = 1, provider: str = "", cpa_pool_id: str = ""):
        self.calls.append(("retry_bulk", {"emails": emails, "workers": workers, "provider": provider, "cpa_pool_id": cpa_pool_id}))
        return {"ok": True, "started_count": len(emails)}

    def read_retry_log(self, email: str):
        self.calls.append(("read_retry_log", email))
        return {"ok": True, "log": "hello", "running": False}


class FakeAccountService:
    def __init__(self) -> None:
        self.accounts = [
            {"id": 7, "access_token": "token-1", "email": "a@example.com"},
            {"id": 8, "access_token": "token-2", "email": "b@example.com"},
        ]

    def get_account(self, token: str):
        for account in self.accounts:
            if account["access_token"] == token:
                return account
        return None

    def list_accounts(self):
        return self.accounts


class CodexAPITests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(create_router())
        self.client = TestClient(app)
        self.service = FakeCodexManagementService()
        self.accounts = FakeAccountService()
        self.patchers = [
            patch("api.codex.require_admin", return_value={"role": "admin"}),
            patch("api.codex.codex_management_service", self.service),
            patch("api.codex.account_service", self.accounts),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_list_and_download_mark_exported(self) -> None:
        listed = self.client.get("/api/codex", headers={"Authorization": "Bearer test"})
        downloaded = self.client.get("/api/codex/download/codex-a.json", headers={"Authorization": "Bearer test"})

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["summary"]["total"], 1)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b'{"email":"a@example.com"}\n')
        self.assertIn(("read_credential", "codex-a.json"), self.service.calls)
        self.assertIn(("mark_exported", "codex-a.json"), self.service.calls)

    def test_retry_bulk_resolves_access_tokens_to_emails(self) -> None:
        response = self.client.post(
            "/api/codex/retry-bulk",
            headers={"Authorization": "Bearer test"},
            json={"access_tokens": ["token-1", "token-2"], "workers": 3, "cpa_pool_id": "pool-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.service.calls[-1],
            ("retry_bulk", {"emails": ["a@example.com", "b@example.com"], "workers": 3, "provider": "", "cpa_pool_id": "pool-1"}),
        )

    def test_log_stop_reset_and_retry_routes(self) -> None:
        log_response = self.client.get("/api/codex/retry-log?email=a@example.com", headers={"Authorization": "Bearer test"})
        stop_response = self.client.post("/api/codex/stop", headers={"Authorization": "Bearer test"}, json={"email": "a@example.com"})
        reset_response = self.client.post("/api/codex/reset-retrying", headers={"Authorization": "Bearer test"}, json={"email": "a@example.com", "status": "failed"})
        retry_response = self.client.post("/api/codex/retry", headers={"Authorization": "Bearer test"}, json={"email": "a@example.com"})

        self.assertEqual(log_response.json()["log"], "hello")
        self.assertEqual(stop_response.status_code, 200)
        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(retry_response.status_code, 200)
        self.assertIn(("request_stop", "a@example.com"), self.service.calls)
        self.assertIn(("reset_retrying", {"email": "a@example.com", "status": "failed"}), self.service.calls)
        self.assertIn(("retry", {"email": "a@example.com", "provider": "", "cpa_pool_id": ""}), self.service.calls)


if __name__ == "__main__":
    unittest.main()
