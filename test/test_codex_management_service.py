from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.codex_management_service as module
from services.codex_management_service import CodexManagementService


class FakeAccounts:
    def __init__(self) -> None:
        self.accounts = [
            {
                "access_token": "token-1",
                "email": "a@example.com",
                "codex_oauth": {"status": "success"},
            }
        ]

    def list_accounts(self):
        return self.accounts

    def get_account(self, token: str):
        for account in self.accounts:
            if account.get("access_token") == token:
                return account
        return None

    def update_account(self, token: str, updates: dict, quiet: bool = False):
        account = self.get_account(token)
        if account is None:
            return None
        account.update(updates)
        return account


class FakeOAuthRetry:
    def __init__(self) -> None:
        self.finished = False
        self.captured = False

    def finish_callback(self, token: str, callback_url: str, pool_id: str = ""):
        self.finished = True
        return {"ok": True, "pool_id": pool_id, "auth_json": {"email": "a@example.com", "access_token": token}}

    def capture_callback(self, token: str, provider: str, *, cpa_pool_id: str = "", timeout_seconds: int | None = None):
        self.captured = True
        return {
            "ok": True,
            "pool_id": cpa_pool_id,
            "browser": {"provider": provider},
            "auth_json": {"email": "a@example.com", "access_token": token},
        }


class FakePools:
    def list_pools(self):
        return [{"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"}]

    def get_pool(self, pool_id: str):
        if pool_id == "pool-1":
            return self.list_pools()[0]
        return None


class CodexManagementServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patchers = [
            patch.object(module, "CODEX_CREDENTIAL_DIR", root / "credentials"),
            patch.object(module, "CODEX_LOG_DIR", root / "logs"),
            patch.object(module, "CODEX_INDEX_FILE", root / "credentials" / "index.json"),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.accounts = FakeAccounts()
        self.oauth = FakeOAuthRetry()
        self.service = CodexManagementService(accounts=self.accounts, oauth_retry=self.oauth, pools=FakePools())

    def test_save_list_download_reset_and_delete_credential(self) -> None:
        filename = self.service.save_credential({"email": "a@example.com", "access_token": "token-1"}, filename="codex-a.json")

        listing = self.service.list()
        self.assertEqual(filename, "codex-a.json")
        self.assertEqual(listing["summary"]["total"], 1)
        self.assertEqual(listing["accounts"][0]["codex_status"], "success")

        content, real_name = self.service.read_credential(filename)
        self.assertEqual(real_name, filename)
        self.assertEqual(json.loads(content)["email"], "a@example.com")

        bundle, bundle_name, media_type = self.service.download_bulk([filename])
        payload = json.loads(bundle)
        self.assertTrue(bundle_name.startswith("codex-bulk-"))
        self.assertEqual(media_type, "application/json")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(self.service.list()["summary"]["exported"], 1)

        self.service.reset_exported(filename)
        self.assertEqual(self.service.list()["summary"]["exported"], 0)
        self.assertTrue(self.service.delete_credential(filename))
        self.assertEqual(self.service.list()["summary"]["total"], 0)

    def test_stop_and_reset_retrying_update_account_status_and_log(self) -> None:
        self.assertTrue(self.service.reserve("a@example.com"))

        stopped = self.service.request_stop("a@example.com")
        self.assertTrue(stopped["ok"])
        self.assertTrue(stopped["running"])
        self.assertEqual(self.accounts.accounts[0]["codex_oauth"]["status"], "stopped")

        reset = self.service.reset_retrying("a@example.com", "failed")
        self.assertTrue(reset["ok"])
        self.assertFalse(self.service.is_retrying("a@example.com"))
        self.assertEqual(self.accounts.accounts[0]["codex_oauth"]["status"], "failed")
        self.assertIn("手动", self.service.read_retry_log("a@example.com")["log"])

    def test_finish_and_capture_callback_save_credential_file(self) -> None:
        finished = self.service.finish_callback_and_save("token-1", "http://localhost/callback?code=1", "pool-1")
        captured = self.service.capture_callback_and_save("token-1", "browser_use", cpa_pool_id="pool-1")

        self.assertTrue(self.oauth.finished)
        self.assertTrue(self.oauth.captured)
        self.assertTrue(finished["credential_filename"].endswith(".json"))
        self.assertTrue(captured["credential_filename"].endswith(".json"))
        self.assertEqual(self.service.list()["summary"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
