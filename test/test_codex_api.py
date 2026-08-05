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


class CodexAPITests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(create_router())
        self.client = TestClient(app)
        self.service = FakeCodexManagementService()
        self.patchers = [
            patch("api.codex.require_admin", return_value={"role": "admin"}),
            patch("api.codex.codex_management_service", self.service),
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


if __name__ == "__main__":
    unittest.main()