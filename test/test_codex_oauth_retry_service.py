from __future__ import annotations

import time
import unittest
from typing import Any
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.accounts as accounts_module
from services.account_service import AccountService
from services.codex_oauth_retry_service import CodexOAuthRetryService
from test.test_codex_agent_identity import AUTH_HEADERS, MemoryStorage


class FakePools:
    def __init__(self, pool: dict | None = None):
        self.pool = pool

    def get_pool(self, pool_id: str) -> dict | None:
        if self.pool and self.pool.get("id") == pool_id:
            return dict(self.pool)
        return None


class FakeBrowserRunner:
    def __init__(self, callback_url: str = "http://localhost:1455/auth/callback?code=abc") -> None:
        self.callback_url = callback_url
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"ok": True, "provider": kwargs.get("provider"), "callback_url": self.callback_url, "browser": {"session_id": "session-1"}}


class CodexOAuthRetryServiceTests(unittest.TestCase):
    def test_retry_job_writes_pending_callback_to_accounts(self) -> None:
        token = "access-token"
        accounts = AccountService(MemoryStorage([{"access_token": token, "email": "a@example.com"}]))
        pools = FakePools({"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"})

        def auth_url_getter(pool: dict[str, Any]) -> dict[str, str]:
            return {"auth_url": "https://auth.openai.com/oauth?state=s1", "state": "s1", "pool_id": pool["id"]}

        service = CodexOAuthRetryService(accounts=accounts, pools=pools, auth_url_getter=auth_url_getter)
        job = service.start([token], "pool-1")
        final = self._wait_job(service, job["job_id"])

        self.assertEqual(final["status"], "done")
        self.assertEqual(final["succeeded"], 0)
        self.assertEqual(final["pending_callback"], 1)
        account = accounts.get_account(token)
        self.assertEqual(account["codex_oauth"]["status"], "pending_callback")
        self.assertEqual(account["codex_oauth"]["auth_url"], "https://auth.openai.com/oauth?state=s1")

    def test_retry_job_records_redacted_failure(self) -> None:
        token = "access-token-secret"
        accounts = AccountService(MemoryStorage([{"access_token": token, "email": "a@example.com"}]))
        pools = FakePools({"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"})

        def auth_url_getter(pool: dict[str, Any]) -> dict[str, str]:
            raise RuntimeError(f"bad token {token}")

        service = CodexOAuthRetryService(accounts=accounts, pools=pools, auth_url_getter=auth_url_getter)
        job = service.start([token], "pool-1")
        final = self._wait_job(service, job["job_id"])

        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["failed"], 1)
        self.assertNotIn(token, final["results"][0]["error"])
        self.assertNotIn(token, accounts.get_account(token)["codex_oauth"]["error"])

    def test_start_validates_pool(self) -> None:
        accounts = AccountService(MemoryStorage())
        service = CodexOAuthRetryService(accounts=accounts, pools=FakePools())

        with self.assertRaisesRegex(ValueError, "CPA pool not found"):
            service.start(["token"], "missing")

    def test_recover_interrupted_marks_running_states_failed(self) -> None:
        running_token = "running-token"
        pending_token = "pending-token"
        accounts = AccountService(MemoryStorage([
            {"access_token": running_token, "codex_oauth": {"status": "capturing_callback", "auth_url": "https://auth.example"}},
            {"access_token": pending_token, "codex_oauth": {"status": "pending_callback", "auth_url": "https://auth.example"}},
        ]))
        service = CodexOAuthRetryService(accounts=accounts, pools=FakePools())

        recovered = service.recover_interrupted()

        self.assertEqual(recovered, 1)
        self.assertEqual(accounts.get_account(running_token)["codex_oauth"]["status"], "failed")
        self.assertTrue(accounts.get_account(running_token)["codex_oauth"]["recovered_interrupted"])
        self.assertEqual(accounts.get_account(pending_token)["codex_oauth"]["status"], "pending_callback")

    def test_finish_callback_submits_to_cpa_and_marks_source_account_success(self) -> None:
        token = "web-access-token"
        accounts = AccountService(MemoryStorage([
            {
                "access_token": token,
                "email": "web@example.com",
                "codex_oauth": {"status": "pending_callback", "pool_id": "pool-1", "auth_url": "https://auth.example"},
            }
        ]))
        pools = FakePools({"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"})
        submitted = []

        def callback_submitter(pool: dict[str, Any], callback_url: str, import_account: bool = True) -> dict[str, Any]:
            submitted.append({"pool": pool, "callback_url": callback_url, "import_account": import_account})
            return {
                "auth_json": {"access_token": "codex-token", "email": "codex@example.com"},
                "import_result": {"added": 1, "skipped": 0, "items": []},
            }

        service = CodexOAuthRetryService(accounts=accounts, pools=pools, callback_submitter=callback_submitter)
        result = service.finish_callback(token, "http://localhost:1455/auth/callback?code=abc")

        self.assertTrue(result["ok"])
        self.assertEqual(submitted[0]["pool"]["id"], "pool-1")
        self.assertTrue(submitted[0]["import_account"])
        codex_oauth = accounts.get_account(token)["codex_oauth"]
        self.assertEqual(codex_oauth["status"], "success")
        self.assertEqual(codex_oauth["auth_email"], "codex@example.com")
        self.assertEqual(codex_oauth["import_result"], {"added": 1, "skipped": 0})

    def test_finish_callback_redacts_callback_url_on_failure(self) -> None:
        token = "web-access-token-secret"
        callback_url = "http://localhost:1455/auth/callback?code=secret-code"
        accounts = AccountService(MemoryStorage([{"access_token": token, "codex_oauth": {"pool_id": "pool-1"}}]))
        pools = FakePools({"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"})

        def callback_submitter(pool: dict[str, Any], callback_url: str, import_account: bool = True) -> dict[str, Any]:
            raise RuntimeError(f"bad callback {callback_url} for {token}")

        service = CodexOAuthRetryService(accounts=accounts, pools=pools, callback_submitter=callback_submitter)

        with self.assertRaises(RuntimeError) as ctx:
            service.finish_callback(token, callback_url)

        self.assertNotIn(token, str(ctx.exception))
        self.assertNotIn("secret-code", str(ctx.exception))
        self.assertEqual(accounts.get_account(token)["codex_oauth"]["status"], "callback_failed")
        self.assertNotIn("secret-code", accounts.get_account(token)["codex_oauth"]["error"])

    def test_capture_callback_uses_browser_runner_then_submits_to_cpa(self) -> None:
        token = "web-access-token"
        auth_url = "https://auth.openai.com/oauth?state=s1"
        accounts = AccountService(MemoryStorage([
            {
                "access_token": token,
                "email": "web@example.com",
                "proxy": "http://127.0.0.1:7890",
                "codex_oauth": {"status": "pending_callback", "pool_id": "pool-1", "auth_url": auth_url, "state": "s1"},
            }
        ]))
        pools = FakePools({"id": "pool-1", "base_url": "https://cpa.example", "secret_key": "secret"})
        browser_runner = FakeBrowserRunner()
        submitted = []

        def callback_submitter(pool: dict[str, Any], callback_url: str, import_account: bool = True) -> dict[str, Any]:
            submitted.append({"pool": pool, "callback_url": callback_url})
            return {
                "auth_json": {"access_token": "codex-token", "email": "codex@example.com"},
                "import_result": {"added": 1, "skipped": 0, "items": []},
            }

        service = CodexOAuthRetryService(
            accounts=accounts,
            pools=pools,
            callback_submitter=callback_submitter,
            browser_runner=browser_runner,
        )
        result = service.capture_callback(token, "browser-use", timeout_seconds=15)

        self.assertTrue(result["ok"])
        self.assertEqual(result["browser"]["provider"], "browser_use")
        self.assertEqual(browser_runner.calls[0]["provider"], "browser_use")
        self.assertEqual(browser_runner.calls[0]["auth_url"], auth_url)
        self.assertEqual(browser_runner.calls[0]["email"], "web@example.com")
        self.assertEqual(browser_runner.calls[0]["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(browser_runner.calls[0]["timeout_seconds"], 15)
        self.assertEqual(submitted[0]["callback_url"], browser_runner.callback_url)
        self.assertEqual(accounts.get_account(token)["codex_oauth"]["status"], "success")

    @staticmethod
    def _wait_job(service: CodexOAuthRetryService, job_id: str) -> dict[str, Any]:
        for _ in range(100):
            job = service.get(job_id)
            if job and job["status"] in {"done", "failed", "stopped"}:
                return job
            time.sleep(0.01)
        raise AssertionError("job did not finish")


class CodexOAuthRetryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = mock.Mock()
        self.service.start.return_value = {"job_id": "job-1", "status": "queued"}
        self.service.get.return_value = {"job_id": "job-1", "status": "done"}
        self.service.stop.return_value = {"job_id": "job-1", "status": "stopping"}
        self.patcher = mock.patch.object(accounts_module, "codex_oauth_retry_service", self.service)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        app = FastAPI()
        app.include_router(accounts_module.create_router())
        self.client = TestClient(app)

    def test_retry_requires_admin_auth(self) -> None:
        response = self.client.post("/api/accounts/codex-oauth/retry", json={"access_tokens": ["token"], "cpa_pool_id": "pool-1"})

        self.assertEqual(response.status_code, 401)

    def test_retry_start_calls_service(self) -> None:
        response = self.client.post(
            "/api/accounts/codex-oauth/retry",
            headers=AUTH_HEADERS,
            json={"access_tokens": ["token"], "cpa_pool_id": "pool-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_id"], "job-1")
        self.service.start.assert_called_once_with(["token"], "pool-1")

    def test_retry_status_and_stop(self) -> None:
        status_response = self.client.get("/api/accounts/codex-oauth/retry/job-1", headers=AUTH_HEADERS)
        stop_response = self.client.post("/api/accounts/codex-oauth/retry/job-1/stop", headers=AUTH_HEADERS)

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(stop_response.status_code, 200)
        self.service.get.assert_called_once_with("job-1")
        self.service.stop.assert_called_once_with("job-1")

    def test_callback_calls_service(self) -> None:
        self.service.finish_callback.return_value = {"ok": True, "access_token": "token:abc"}

        response = self.client.post(
            "/api/accounts/codex-oauth/callback",
            headers=AUTH_HEADERS,
            json={"access_token": "token", "callback_url": "http://localhost:1455/auth/callback?code=abc", "cpa_pool_id": "pool-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.service.finish_callback.assert_called_once_with("token", "http://localhost:1455/auth/callback?code=abc", "pool-1")

    def test_browser_callback_calls_service(self) -> None:
        self.service.capture_callback.return_value = {"ok": True, "access_token": "token:abc", "browser": {"provider": "browser_use"}}

        response = self.client.post(
            "/api/accounts/codex-oauth/browser-callback",
            headers=AUTH_HEADERS,
            json={"access_token": "token", "provider": "browser_use", "cpa_pool_id": "pool-1", "timeout_seconds": 30},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.service.capture_callback.assert_called_once_with("token", "browser_use", cpa_pool_id="pool-1", timeout_seconds=30)


if __name__ == "__main__":
    unittest.main()
