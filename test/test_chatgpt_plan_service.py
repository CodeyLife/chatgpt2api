import base64
import json
import unittest

from services import chatgpt_plan_service


def make_jwt(payload):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(payload)}.sig"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def close(self):
        self.closed = True


class ChatGPTPlanServiceTests(unittest.TestCase):
    def test_parse_accounts_check_extracts_plan_and_trial_fields(self):
        token = make_jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct-1",
                    "chatgpt_user_id": "user-1",
                    "chatgpt_plan_type": "free",
                },
                "https://api.openai.com/profile": {"email": "user@example.com"},
            }
        )
        result = chatgpt_plan_service.parse_accounts_check(
            {
                "accounts": {
                    "acct-1": {
                        "account": {"account_id": "acct-1", "plan_type": "free"},
                        "entitlement": {
                            "subscription_plan": "chatgptfreeplan",
                            "discount": {
                                "discount_type": "promo",
                                "amount": 1000,
                                "duration_num_periods": 1,
                                "discount_expires_at": "2026-08-01T00:00:00Z",
                                "cancellation_policy": "standard",
                                "promo_campaign_id": "promo-1",
                            },
                        },
                        "eligible_promo_campaigns": {
                            "plus": {
                                "id": "campaign-1",
                                "metadata": {
                                    "title": "Plus trial",
                                    "promotion_type_label": "Trial",
                                    "discount": {"percentage": 100},
                                    "duration": {"num_periods": 1, "period": "month"},
                                },
                            }
                        },
                        "eligible_offers": {"offers": [{"id": "offer-1"}]},
                        "features": ["a", "b"],
                    }
                }
            },
            token=token,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["account_id"], "acct-1")
        self.assertEqual(result["current_plan_type"], "free")
        self.assertTrue(result["plus_trial_eligible"])
        self.assertEqual(result["plus_trial_campaign_id"], "campaign-1")
        self.assertEqual(result["plus_trial_promotion_type_label"], "Trial")
        self.assertEqual(result["discount_type"], "promo")
        self.assertEqual(result["discount_amount"], 1000)
        self.assertEqual(result["discount_promo_campaign_id"], "promo-1")
        self.assertEqual(result["eligible_offer_ids"], ["offer-1"])
        self.assertEqual(result["email"], "user@example.com")

    def test_check_account_plan_uses_chatgpt_endpoint(self):
        token = make_jwt(
            {
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"},
                "https://api.openai.com/profile": {"email": "user@example.com"},
            }
        )
        session = FakeSession(
            FakeResponse(
                payload={
                    "accounts": {
                        "acct-1": {
                            "account": {"account_id": "acct-1", "plan_type": "plus"},
                            "entitlement": {"subscription_plan": "chatgptplusplan", "has_active_subscription": True},
                        }
                    }
                }
            )
        )

        result = chatgpt_plan_service.check_account_plan(
            token,
            session=session,
            account={"email": "user@example.com", "device_id": "device-1", "browser_profile": "chrome152_win"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["current_plan_type"], "plus")
        self.assertEqual(result["http_status"], 200)
        self.assertIn("/backend-api/accounts/check", session.calls[0]["url"])
        self.assertIn("timezone_offset_min=0", session.calls[0]["url"])
        self.assertEqual(session.calls[0]["headers"]["authorization"], f"Bearer {token}")
        self.assertEqual(session.calls[0]["headers"]["oai-device-id"], "device-1")
        self.assertIn("user-agent", session.calls[0]["headers"])
        self.assertFalse(session.closed)

    def test_check_account_plan_retries_rate_limited_response(self):
        token = make_jwt(
            {
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"},
                "https://api.openai.com/profile": {"email": "user@example.com"},
            }
        )
        session = FakeSession(
            [
                FakeResponse(status_code=429, text="rate limited"),
                FakeResponse(
                    payload={
                        "accounts": {
                            "acct-1": {
                                "account": {"account_id": "acct-1", "plan_type": "free"},
                                "entitlement": {"subscription_plan": "chatgptfreeplan"},
                            }
                        }
                    }
                ),
            ]
        )

        result = chatgpt_plan_service.check_account_plan(token, session=session, max_attempts=2, retry_delay=0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["attempt_count"], 2)
        self.assertEqual(len(session.calls), 2)

    def test_account_payload_from_plan_result_keeps_raw_diagnostics(self):
        payload = chatgpt_plan_service.account_payload_from_plan_result(
            {
                "ok": True,
                "current_plan_type": "plus",
                "account_id": "acct-1",
                "user_id": "user-1",
                "email": "user@example.com",
            }
        )

        self.assertEqual(payload["type"], "plus")
        self.assertEqual(payload["account_id"], "acct-1")
        self.assertIn("chatgpt_plan_check", payload)


if __name__ == "__main__":
    unittest.main()
