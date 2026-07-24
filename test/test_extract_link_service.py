import unittest
from unittest.mock import patch

from services.extract_link_service import ExtractLinkError, ExtractLinkService, _parse_sse_lines


class FakeAccountService:
    def __init__(self):
        self.accounts = {
            "token-1": {
                "access_token": "token-1",
                "email": "user@example.com",
                "status": "正常",
                "type": "free",
                "quota": 0,
                "chatgpt_plan_check": {
                    "status": "success",
                    "current_plan_type": "free",
                    "plus_trial_eligible": True,
                },
            }
        }
        self.updates = []

    def get_account(self, token):
        item = self.accounts.get(token)
        return dict(item) if item else None

    def update_account(self, token, updates, quiet=False):
        current = self.accounts.get(token)
        if current is None:
            return None
        current.update(updates)
        self.updates.append({"token": token, "updates": updates, "quiet": quiet})
        return dict(current)

    def list_accounts(self):
        return [dict(item) for item in self.accounts.values()]


class FakeConfig:
    def __init__(self, settings):
        self._settings = settings

    def get_extract_link_settings(self):
        return dict(self._settings)


class FakeSemaphore:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


class SuccessfulExtractLinkService(ExtractLinkService):
    def _create_remote_job(self, *, token, link_type, cdk):
        return {"job_id": "remote-1", "cdk_remaining": 9}

    def _iter_remote_events(self, *, remote_job_id, cdk):
        yield "log", {"message": "step 1"}
        yield "result", {"result": {"url": "https://pay.example/link", "payment_method": "pix", "expires_at": "2026-08-01T00:00:00Z"}}


class FailingExtractLinkService(ExtractLinkService):
    def _create_remote_job(self, *, token, link_type, cdk):
        raise ExtractLinkError("remote failed")


class ExtractLinkServiceTests(unittest.TestCase):
    def test_parse_sse_lines_extracts_log_and_result(self):
        events = list(_parse_sse_lines([
            "event: log",
            'data: {"message":"hello"}',
            "",
            "event: result",
            'data: {"result":{"url":"https://example.com"}}',
            "",
        ]))

        self.assertEqual(events[0], ("log", {"message": "hello"}))
        self.assertEqual(events[1][0], "result")
        self.assertEqual(events[1][1]["result"]["url"], "https://example.com")

    def test_start_rejects_disabled_service(self):
        service = ExtractLinkService()
        with patch("services.extract_link_service.config", FakeConfig({
            "enabled": False,
            "api_base": "",
            "cdk": "",
            "link_type": "pix",
        })):
            with self.assertRaisesRegex(ExtractLinkError, "未启用"):
                service.start(["token-1"])

    def test_start_skips_accounts_without_plus_trial_eligibility(self):
        fake_accounts = FakeAccountService()
        fake_accounts.accounts["token-1"]["chatgpt_plan_check"]["plus_trial_eligible"] = False
        service = ExtractLinkService()

        with patch("services.extract_link_service.account_service", fake_accounts), patch(
            "services.extract_link_service.config",
            FakeConfig({
                "enabled": True,
                "api_base": "https://extract.example",
                "cdk": "cdk-1",
                "link_type": "pix",
                "request_timeout": 30,
                "event_timeout": 180,
                "workers": 1,
                "queue_limit": 10,
            }),
        ):
            result = service.start(["token-1"])

        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertIn("Plus试用", result["skipped_items"][0]["reason"])
        self.assertEqual(fake_accounts.updates, [])

    def test_recover_interrupted_marks_queued_and_running_failed(self):
        fake_accounts = FakeAccountService()
        fake_accounts.accounts["token-1"]["extract_link"] = {"status": "running", "job_id": "job-1"}
        fake_accounts.accounts["token-2"] = {
            "access_token": "token-2",
            "email": "b@example.com",
            "extract_link": {"status": "queued", "job_id": "job-2"},
        }
        service = ExtractLinkService()

        with patch("services.extract_link_service.account_service", fake_accounts):
            recovered = service.recover_interrupted()

        self.assertEqual(recovered, 2)
        self.assertEqual(fake_accounts.accounts["token-1"]["extract_link"]["status"], "failed")
        self.assertTrue(fake_accounts.accounts["token-1"]["extract_link"]["recovered_interrupted"])
        self.assertIn("中断", fake_accounts.accounts["token-2"]["extract_link"]["error"])

    def test_run_one_persists_success_result(self):
        fake_accounts = FakeAccountService()
        semaphore = FakeSemaphore()
        service = SuccessfulExtractLinkService()

        with patch("services.extract_link_service.account_service", fake_accounts), patch(
            "services.extract_link_service.config",
            FakeConfig({
                "enabled": True,
                "api_base": "https://extract.example",
                "cdk": "cdk-1",
                "link_type": "pix",
                "request_timeout": 30,
                "event_timeout": 180,
                "workers": 1,
                "queue_limit": 10,
            }),
        ):
            service._run_one(
                token="token-1",
                email="user@example.com",
                link_type="pix",
                cdk="cdk-1",
                job_id="job-1",
                semaphore=semaphore,
            )

        extract = fake_accounts.accounts["token-1"]["extract_link"]
        self.assertTrue(extract["ok"])
        self.assertEqual(extract["status"], "success")
        self.assertEqual(extract["remote_job_id"], "remote-1")
        self.assertEqual(extract["result"]["url"], "https://pay.example/link")
        self.assertEqual(extract["url"], "https://pay.example/link")
        self.assertEqual(extract["payment_method"], "pix")
        self.assertEqual(extract["expires_at"], "2026-08-01T00:00:00Z")
        self.assertEqual(semaphore.released, 1)

    def test_run_one_persists_failure_result(self):
        fake_accounts = FakeAccountService()
        semaphore = FakeSemaphore()
        service = FailingExtractLinkService()

        with patch("services.extract_link_service.account_service", fake_accounts), patch(
            "services.extract_link_service.config",
            FakeConfig({
                "enabled": True,
                "api_base": "https://extract.example",
                "cdk": "cdk-1",
                "link_type": "pix",
                "request_timeout": 30,
                "event_timeout": 180,
                "workers": 1,
                "queue_limit": 10,
            }),
        ):
            service._run_one(
                token="token-1",
                email="user@example.com",
                link_type="pix",
                cdk="cdk-1",
                job_id="job-1",
                semaphore=semaphore,
            )

        extract = fake_accounts.accounts["token-1"]["extract_link"]
        self.assertFalse(extract["ok"])
        self.assertEqual(extract["status"], "failed")
        self.assertIn("remote failed", extract["error"])
        self.assertEqual(semaphore.released, 1)


if __name__ == "__main__":
    unittest.main()
