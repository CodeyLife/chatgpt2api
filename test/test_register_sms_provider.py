import unittest

from services.register import sms_provider


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, params=None, **kwargs):
        self.calls.append(("GET", url, params or {}, kwargs))
        return self.responses.pop(0)

    def post(self, url, headers=None, data=None, **kwargs):
        self.calls.append(("POST", url, headers or {}, data, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class RegisterSmsProviderTests(unittest.TestCase):
    def test_acquire_grizzly_number_parses_activation_and_phone(self):
        http = FakeHttp([FakeResponse(text="ACCESS_NUMBER:act-1:+16195550123")])

        activation = sms_provider.acquire_number(
            {"provider": "grizzly", "api_key": "key", "service": "ot", "country": "187"},
            http=http,
        )

        self.assertEqual(activation.activation_id, "act-1")
        self.assertEqual(activation.phone, "16195550123")
        self.assertEqual(activation.provider, "grizzly")
        self.assertEqual(http.calls[0][2]["action"], "getNumber")

    def test_grizzly_no_balance_raises_specific_error(self):
        http = FakeHttp([FakeResponse(text="NO_BALANCE")])

        with self.assertRaises(sms_provider.SmsNoBalanceError):
            sms_provider.acquire_number({"provider": "grizzly", "api_key": "key"}, http=http)

    def test_wait_for_grizzly_sms_code_polls_until_status_ok(self):
        http = FakeHttp(
            [
                FakeResponse(text="STATUS_WAIT_CODE"),
                FakeResponse(text="STATUS_OK:123456"),
            ]
        )

        code = sms_provider.wait_for_sms_code(
            "act-1",
            {"provider": "grizzly", "api_key": "key", "wait_timeout": 3, "poll_interval": 0},
            http=http,
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(code, "123456")
        self.assertEqual(len(http.calls), 2)

    def test_l_provider_take_phone_uses_management_api_and_prefix(self):
        http = FakeHttp([FakeResponse(payload={"item": {"id": "l-1", "phone": "84995550123"}})])

        activation = sms_provider.acquire_number(
            {
                "provider": "l",
                "l_api_base": "https://local.example",
                "l_admin_auth_code": "secret",
                "l_phone_prefix": "84",
                "service": "ot",
                "country": "vn",
            },
            http=http,
        )

        self.assertEqual(activation.activation_id, "l-1")
        self.assertEqual(activation.phone, "84995550123")
        self.assertEqual(http.calls[0][1], "https://local.example/api/admin/l/take-phone")
        self.assertNotIn("secret", http.calls[0][3])

    def test_h_provider_defaults_to_reusable_phone_endpoint(self):
        http = FakeHttp([FakeResponse(payload={"item": {"id": "h-1", "phone": "+12025550123"}})])

        activation = sms_provider.acquire_number(
            {
                "provider": "h",
                "h_api_base": "https://h.example",
                "h_admin_auth_code": "secret",
                "service": "project-1",
                "country": "US",
            },
            http=http,
        )

        self.assertEqual(activation.activation_id, "h-1")
        self.assertEqual(activation.phone, "12025550123")
        self.assertEqual(http.calls[0][1], "https://h.example/api/admin/h/take-reusable-phone")

    def test_cancel_l_provider_releases_number(self):
        http = FakeHttp([FakeResponse(payload={"released": 1})])

        sms_provider.cancel(
            "l-1",
            {"provider": "l", "l_api_base": "https://local.example", "l_admin_auth_code": "secret"},
            http=http,
        )

        self.assertEqual(http.calls[0][1], "https://local.example/api/admin/l/release")


if __name__ == "__main__":
    unittest.main()
