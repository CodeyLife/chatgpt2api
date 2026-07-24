import json
import unittest
from types import SimpleNamespace
from unittest import mock

from services.register import openai_register  # noqa: F401
from services.register.chatgpt_web import bootstrap


class FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class FakeRegistrar:
    session = object()
    profile = SimpleNamespace(user_agent="Mozilla/5.0", timezone_offset_min="-")

    def _chatgpt_headers(self, referer: str = "") -> dict[str, str]:
        return {"referer": referer}


class ChatGPTWebBootstrapTests(unittest.TestCase):
    def _capture_requests(self, fn):
        calls = []

        def fake_request(_session, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse(), None

        with (
            mock.patch("services.register.chatgpt_web.bootstrap.base.request_with_local_retry", side_effect=fake_request),
            mock.patch("services.register.chatgpt_web.bootstrap.base.step"),
        ):
            fn(FakeRegistrar(), index=1, strict=True)

        return calls

    def test_anonymous_bootstrap_sends_integer_timezone_offset(self) -> None:
        calls = self._capture_requests(bootstrap.anonymous_bootstrap)

        init_call = next(call for call in calls if call[1].endswith("/conversation/init"))
        payload = json.loads(init_call[2]["data"])
        check_url = next(url for _method, url, _kwargs in calls if "/accounts/check/" in url)

        self.assertEqual(payload["timezone_offset_min"], 0)
        self.assertIn("timezone_offset_min=0", check_url)

    def test_authenticated_bootstrap_sends_integer_timezone_offset(self) -> None:
        calls = self._capture_requests(lambda registrar, **kwargs: bootstrap.authenticated_bootstrap(registrar, "token", **kwargs))

        init_call = next(call for call in calls if call[1].endswith("/conversation/init"))
        payload = json.loads(init_call[2]["data"])
        check_url = next(url for _method, url, _kwargs in calls if "/accounts/check/" in url)

        self.assertEqual(payload["timezone_offset_min"], 0)
        self.assertIn("timezone_offset_min=0", check_url)


if __name__ == "__main__":
    unittest.main()
