import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.accounts import create_router


class FakeAccountPlanCheckService:
    def __init__(self):
        self.calls = []

    def start(self, access_tokens, proxy="", trigger="manual"):
        self.calls.append({"access_tokens": access_tokens, "proxy": proxy, "trigger": trigger})
        return {"accepted": len(access_tokens), "skipped": 0, "jobs": [], "skipped_items": [], "items": []}

    def status_snapshot(self, limit=5000):
        self.calls.append({"status_snapshot_limit": limit})
        return {"items": [{"email": "a@example.com", "plan_check_status": "success"}], "total": 1}

    def queue_settings(self):
        return {"min_interval": 0.4}


class FakeChatGPTPlanService:
    def __init__(self):
        self.calls = []

    def check_account_plan(self, access_token, **kwargs):
        self.calls.append({"access_token": access_token, **kwargs})
        return {"ok": True, "current_plan_type": "free"}


class FakeAccountService:
    def get_account(self, token):
        return {"access_token": token, "email": "a@example.com"}


class AccountPlanCheckAPITests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(create_router())
        self.client = TestClient(app)

    def test_batch_plan_check_requires_admin_and_deduplicates_tokens(self):
        service = FakeAccountPlanCheckService()
        with (
            patch("api.accounts.require_admin", return_value={"role": "admin"}) as require_admin,
            patch("api.accounts.account_plan_check_service", service),
        ):
            response = self.client.post(
                "/api/accounts/plan-check/batch",
                headers={"Authorization": "Bearer test"},
                json={"access_tokens": ["token-1", "token-1", "token-2"], "proxy": "http://proxy", "trigger": "registration_auto"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accepted"], 2)
        self.assertEqual(service.calls, [{"access_tokens": ["token-1", "token-2"], "proxy": "http://proxy", "trigger": "registration_auto"}])
        require_admin.assert_called_once_with("Bearer test")

    def test_single_plan_check_passes_account_context(self):
        plan = FakeChatGPTPlanService()
        with (
            patch("api.accounts.require_admin", return_value={"role": "admin"}),
            patch("api.accounts.chatgpt_plan_service", plan),
            patch("api.accounts.account_service", FakeAccountService()),
        ):
            response = self.client.post(
                "/api/accounts/plan-check",
                headers={"Authorization": "Bearer test"},
                json={"access_token": "token-1", "proxy": "http://proxy"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(plan.calls[0]["access_token"], "token-1")
        self.assertEqual(plan.calls[0]["proxy"], "http://proxy")
        self.assertEqual(plan.calls[0]["account"]["email"], "a@example.com")

    def test_plan_check_status_returns_snapshot_and_queue(self):
        service = FakeAccountPlanCheckService()
        with (
            patch("api.accounts.require_admin", return_value={"role": "admin"}),
            patch("api.accounts.account_plan_check_service", service),
        ):
            response = self.client.get(
                "/api/accounts/plan-check-status?limit=10",
                headers={"Authorization": "Bearer test"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["items"][0]["plan_check_status"], "success")
        self.assertEqual(data["queue"]["min_interval"], 0.4)
        self.assertIn({"status_snapshot_limit": 10}, service.calls)


if __name__ == "__main__":
    unittest.main()
