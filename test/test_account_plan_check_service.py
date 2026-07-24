import unittest
from unittest.mock import patch

from services.account_plan_check_service import AccountPlanCheckService


class FakeAccountService:
    def __init__(self):
        self.accounts = {
            "token-1": {"access_token": "token-1", "email": "a@example.com", "proxy": "http://proxy-1"},
            "token-2": {"access_token": "token-2", "email": "b@example.com"},
        }

    def get_account(self, token):
        item = self.accounts.get(token)
        return dict(item) if item else None

    def update_account(self, token, updates, quiet=False):
        if token not in self.accounts:
            return None
        self.accounts[token].update(updates)
        return dict(self.accounts[token])

    def list_accounts(self):
        return [dict(item) for item in self.accounts.values()]


class FakePlanService:
    def __init__(self, result):
        self.results = result if isinstance(result, list) else [result]
        self.calls = []

    def check_account_plan(self, token, proxy="", **kwargs):
        self.calls.append({"token": token, "proxy": proxy, **kwargs})
        if len(self.results) > 1:
            return dict(self.results.pop(0))
        return dict(self.results[0])

    def account_payload_from_plan_result(self, result):
        if not result.get("ok"):
            return {}
        return {
            "type": result.get("current_plan_type"),
            "plan_type": result.get("current_plan_type"),
            "account_id": result.get("account_id"),
            "email": result.get("email"),
        }


class FakeSemaphore:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


class AccountPlanCheckServiceTests(unittest.TestCase):
    def test_run_one_updates_account_with_successful_plan_result(self):
        accounts = FakeAccountService()
        plan = FakePlanService(
            {
                "ok": True,
                "email": "a@example.com",
                "account_id": "acct-1",
                "current_plan_type": "plus",
                "plus_trial_eligible": False,
            }
        )
        service = AccountPlanCheckService(workers=1, queue_limit=1, min_interval=0, jitter=0, registration_recheck_delay=0)
        service._semaphore = FakeSemaphore()

        with patch("services.account_plan_check_service.account_service", accounts), patch(
            "services.account_plan_check_service.chatgpt_plan_service", plan
        ):
            service._run_one(token="token-1", proxy="", job_id="job-1")

        account = accounts.accounts["token-1"]
        self.assertEqual(account["type"], "plus")
        self.assertEqual(account["account_id"], "acct-1")
        self.assertEqual(account["chatgpt_plan_check"]["status"], "success")
        self.assertEqual(plan.calls[0]["token"], "token-1")
        self.assertEqual(plan.calls[0]["proxy"], "http://proxy-1")
        self.assertEqual(plan.calls[0]["account"]["email"], "a@example.com")
        self.assertEqual(service._semaphore.released, 1)

    def test_run_one_stores_failed_plan_result(self):
        accounts = FakeAccountService()
        plan = FakePlanService({"ok": False, "error": "HTTP 401"})
        service = AccountPlanCheckService(workers=1, queue_limit=1, min_interval=0, jitter=0, registration_recheck_delay=0)
        service._semaphore = FakeSemaphore()

        with patch("services.account_plan_check_service.account_service", accounts), patch(
            "services.account_plan_check_service.chatgpt_plan_service", plan
        ):
            service._run_one(token="token-2", proxy="http://override", job_id="job-2")

        check = accounts.accounts["token-2"]["chatgpt_plan_check"]
        self.assertEqual(check["status"], "failed")
        self.assertEqual(check["error"], "HTTP 401")
        self.assertEqual(plan.calls[0]["token"], "token-2")
        self.assertEqual(plan.calls[0]["proxy"], "http://override")

    def test_registration_auto_rechecks_free_without_plus_trial(self):
        accounts = FakeAccountService()
        plan = FakePlanService(
            [
                {
                    "ok": True,
                    "email": "a@example.com",
                    "account_id": "acct-1",
                    "current_plan_type": "free",
                    "plus_trial_eligible": False,
                },
                {
                    "ok": True,
                    "email": "a@example.com",
                    "account_id": "acct-1",
                    "current_plan_type": "free",
                    "plus_trial_eligible": True,
                },
            ]
        )
        service = AccountPlanCheckService(workers=1, queue_limit=1, min_interval=0, jitter=0, registration_recheck_delay=0.01)
        service._semaphore = FakeSemaphore()

        with patch("services.account_plan_check_service.account_service", accounts), patch(
            "services.account_plan_check_service.chatgpt_plan_service", plan
        ):
            service._run_one(token="token-1", proxy="", job_id="job-1", trigger="registration_auto")

        check = accounts.accounts["token-1"]["chatgpt_plan_check"]
        self.assertEqual(check["status"], "success")
        self.assertTrue(check["plus_trial_eligible"])
        self.assertTrue(check["registration_rechecked"])
        self.assertEqual(len(plan.calls), 2)
        self.assertEqual(plan.calls[1]["max_attempts"], 1)

    def test_recover_interrupted_marks_queued_and_running_failed(self):
        accounts = FakeAccountService()
        accounts.accounts["token-1"]["chatgpt_plan_check"] = {"status": "queued", "job_id": "job-1"}
        accounts.accounts["token-2"]["chatgpt_plan_check"] = {"status": "running", "job_id": "job-2"}
        service = AccountPlanCheckService(workers=1, queue_limit=1, min_interval=0, jitter=0, registration_recheck_delay=0)

        with patch("services.account_plan_check_service.account_service", accounts):
            recovered = service.recover_interrupted()

        self.assertEqual(recovered, 2)
        self.assertEqual(accounts.accounts["token-1"]["chatgpt_plan_check"]["status"], "failed")
        self.assertTrue(accounts.accounts["token-1"]["chatgpt_plan_check"]["recovered_interrupted"])
        self.assertIn("中断", accounts.accounts["token-2"]["chatgpt_plan_check"]["error"])

    def test_status_snapshot_returns_plan_and_extract_fields(self):
        accounts = FakeAccountService()
        accounts.accounts["token-1"]["chatgpt_plan_check"] = {
            "status": "success",
            "current_plan_type": "free",
            "plus_trial_eligible": True,
            "job_id": "job-1",
        }
        accounts.accounts["token-1"]["extract_link"] = {
            "status": "success",
            "ok": True,
            "long_url": "https://pay.example/link",
        }
        service = AccountPlanCheckService(workers=1, queue_limit=1, min_interval=0, jitter=0, registration_recheck_delay=0)

        with patch("services.account_plan_check_service.account_service", accounts):
            snapshot = service.status_snapshot()

        self.assertEqual(snapshot["total"], 2)
        first = snapshot["items"][0]
        self.assertEqual(first["plan_check_status"], "success")
        self.assertEqual(first["current_plan_type"], "free")
        self.assertTrue(first["plus_trial_eligible"])
        self.assertEqual(first["extract_link_status"], "success")
        self.assertEqual(first["extract_link_long_url"], "https://pay.example/link")


if __name__ == "__main__":
    unittest.main()
