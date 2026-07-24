from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import services.codex_oauth_browser_service as browser_service
from services.codex_oauth_browser_service import (
    CodexOAuthBrowserRunner,
    extract_callback_url_from_context,
    extract_callback_url_from_page,
    is_codex_callback_url,
)
from services.register.sms_provider import SmsActivation
from services.register.cloud_browser import CloudBrowserSession


CALLBACK_URL = "http://localhost:1455/auth/callback?code=abc&state=s1"


class FakeLocator:
    def __init__(self, visible: bool = False, on_click=None) -> None:
        self.first = self
        self.visible = visible
        self.on_click = on_click
        self.clicks = 0

    def count(self) -> int:
        return 1 if self.visible else 0

    def is_visible(self, timeout: int = 0) -> bool:
        return self.visible

    def click(self, timeout: int = 0) -> None:
        self.clicks += 1
        if self.on_click:
            self.on_click()
        return None

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        return None

    def evaluate(self, script: str) -> None:
        self.click()
        return None

    def inner_text(self, timeout: int = 0) -> str:
        return ""


class FakePage:
    def __init__(self, url: str = "https://auth.openai.com/oauth") -> None:
        self.url = url
        self.goto_calls: list[str] = []
        self.default_timeout = 0
        self.frames: list[Any] = []

    def set_default_timeout(self, value: int) -> None:
        self.default_timeout = value

    def set_default_navigation_timeout(self, value: int) -> None:
        self.default_timeout = value

    def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_calls.append(url)
        self.url = url

    def evaluate(self, script: str) -> list[str]:
        return ["https://auth.openai.com/oauth", CALLBACK_URL]

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator()


class TextLocator(FakeLocator):
    def __init__(self, text: str) -> None:
        super().__init__(visible=True)
        self.text = text

    def inner_text(self, timeout: int = 0) -> str:
        return self.text


class TextPage(FakePage):
    def __init__(self, text: str) -> None:
        super().__init__("https://auth.openai.com/oauth")
        self.text = text

    def evaluate(self, script: str) -> list[str] | bool:
        if "performance.getEntries" in script:
            return []
        return False

    def locator(self, selector: str) -> FakeLocator:
        return TextLocator(self.text)


class FakeFrame:
    def __init__(self, locator: FakeLocator | None = None) -> None:
        self._locator = locator or FakeLocator()

    def evaluate(self, script: str) -> bool:
        return False

    def locator(self, selector: str) -> FakeLocator:
        return self._locator


class DelayedCallbackPage(FakePage):
    def __init__(self) -> None:
        super().__init__("https://auth.openai.com/oauth")
        self.clicks = 0
        self.frames = [FakeFrame(FakeLocator(True, self._clicked))]

    def _clicked(self) -> None:
        self.clicks += 1
        if self.clicks >= 2:
            self.url = CALLBACK_URL

    def evaluate(self, script: str) -> list[str] | bool:
        if "performance.getEntries" in script:
            return []
        return False


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.contexts = [context]

    def new_context(self) -> FakeContext:
        context = FakeContext(FakePage())
        self.contexts.append(context)
        return context


class FakeConnected:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, connected: FakeConnected) -> None:
        self.connected = connected

    def connect(self, session: CloudBrowserSession) -> FakeConnected:
        return self.connected


class FakeBrowserUseClient:
    def __init__(self, config: dict | None = None) -> None:
        self.closed_session: CloudBrowserSession | None = None

    def open_session(self) -> CloudBrowserSession:
        return CloudBrowserSession(
            connect_url="wss://browser.example",
            provider="browser_use",
            api_key_present=True,
            session_id="session-1",
        )

    def close_session(self, session: CloudBrowserSession) -> None:
        self.closed_session = session


class CodexOAuthBrowserServiceTests(unittest.TestCase):
    def test_is_codex_callback_url_requires_localhost_callback(self) -> None:
        self.assertTrue(is_codex_callback_url(CALLBACK_URL))
        self.assertTrue(is_codex_callback_url("https://127.0.0.1:1455/auth/callback?code=abc"))
        self.assertFalse(is_codex_callback_url("https://localhost:1455/auth/callback"))
        self.assertFalse(is_codex_callback_url("https://example.com/auth/callback?code=abc"))
        self.assertFalse(is_codex_callback_url("https://localhost:9999/auth/callback?code=abc"))

    def test_extract_callback_url_from_current_page_or_performance_entries(self) -> None:
        self.assertEqual(extract_callback_url_from_page(FakePage(CALLBACK_URL)), CALLBACK_URL)
        self.assertEqual(extract_callback_url_from_page(FakePage("https://auth.openai.com/oauth")), CALLBACK_URL)
        self.assertEqual(extract_callback_url_from_context(FakeContext(FakePage(CALLBACK_URL))), CALLBACK_URL)

    def test_runner_opens_auth_url_and_returns_callback(self) -> None:
        page = FakePage()
        connected = FakeConnected(FakeBrowser(FakeContext(page)))
        runner = CodexOAuthBrowserRunner(
            browser_use_factory=lambda cfg: FakeBrowserUseClient(cfg),
            connector_factory=lambda: FakeConnector(connected),
            sleep_func=lambda seconds: None,
        )

        result = runner.run(provider="browser_use", auth_url="https://auth.openai.com/oauth?state=s1", timeout_seconds=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["callback_url"], CALLBACK_URL)
        self.assertEqual(result["provider"], "browser_use")
        self.assertEqual(page.goto_calls, ["https://auth.openai.com/oauth?state=s1"])
        self.assertTrue(connected.closed)

    def test_authorize_click_searches_iframes(self) -> None:
        clicked = {"value": False}
        page = FakePage()
        page.frames = [FakeFrame(FakeLocator(True, lambda: clicked.__setitem__("value", True)))]

        self.assertTrue(CodexOAuthBrowserRunner._maybe_click_authorize(page))
        self.assertTrue(clicked["value"])

    def test_wait_for_callback_clicks_multiple_consent_steps(self) -> None:
        page = DelayedCallbackPage()
        runner = CodexOAuthBrowserRunner(sleep_func=lambda seconds: None)

        callback = runner._wait_for_callback(FakeContext(page), page, timeout=5)

        self.assertEqual(callback, CALLBACK_URL)
        self.assertGreaterEqual(page.clicks, 2)

    def test_wait_for_callback_detects_unusable_account_page(self) -> None:
        page = TextPage("Your account has been deactivated.")
        runner = CodexOAuthBrowserRunner(sleep_func=lambda seconds: None)

        with self.assertRaisesRegex(browser_service.CodexOAuthBrowserError, "account_deactivated"):
            runner._wait_for_callback(FakeContext(page), page, timeout=1)

    def test_handle_phone_verification_uses_sms_provider(self) -> None:
        page = FakePage("https://auth.openai.com/add-phone")
        sms_cfg = {"enabled": True, "provider": "grizzly"}
        runner = CodexOAuthBrowserRunner(sleep_func=lambda seconds: None)

        with (
            mock.patch.object(browser_service, "_has_phone_prompt", return_value=True),
            mock.patch.object(browser_service, "_clear_inputs") as clear_inputs,
            mock.patch.object(browser_service, "_set_input_value", return_value=True) as set_input,
            mock.patch.object(browser_service, "_read_input_value", return_value="+16195550123"),
            mock.patch.object(browser_service, "_click_phone_continue", return_value=True) as click_continue,
            mock.patch.object(browser_service, "_wait_for_phone_code_page", return_value="code_page"),
            mock.patch.object(browser_service, "_wait_after_phone_otp", return_value="accepted"),
            mock.patch.object(browser_service.sms_provider, "acquire_number", return_value=SmsActivation("act-1", "16195550123", "grizzly")) as acquire,
            mock.patch.object(browser_service.sms_provider, "set_status") as set_status,
            mock.patch.object(browser_service.sms_provider, "wait_for_sms_code", return_value="123456") as wait_sms,
            mock.patch.object(browser_service.sms_provider, "complete") as complete,
            mock.patch.object(browser_service.sms_provider, "cancel") as cancel,
        ):
            runner._handle_phone_verification(page, sms_cfg)

        acquire.assert_called_once_with(sms_cfg)
        set_status.assert_called_once_with("act-1", 1, sms_cfg)
        wait_sms.assert_called_once()
        complete.assert_called_once_with("act-1", sms_cfg)
        cancel.assert_not_called()
        self.assertEqual(set_input.call_args_list[0].args[2], "+16195550123")
        self.assertEqual(set_input.call_args_list[1].args[2], "123456")
        self.assertGreaterEqual(click_continue.call_count, 2)
        self.assertEqual(clear_inputs.call_args_list[0].args[1], "phone")
        self.assertEqual(clear_inputs.call_args_list[1].args[1], "otp")

    def test_handle_phone_verification_cancels_activation_on_failure(self) -> None:
        page = FakePage("https://auth.openai.com/add-phone")
        sms_cfg = {"enabled": True, "provider": "grizzly", "max_retries": 1}
        runner = CodexOAuthBrowserRunner(sleep_func=lambda seconds: None)

        with (
            mock.patch.object(browser_service, "_has_phone_prompt", return_value=True),
            mock.patch.object(browser_service, "_clear_inputs"),
            mock.patch.object(browser_service, "_set_input_value", return_value=True),
            mock.patch.object(browser_service, "_read_input_value", return_value="+16195550123"),
            mock.patch.object(browser_service, "_click_phone_continue", return_value=True),
            mock.patch.object(browser_service, "_wait_for_phone_code_page", return_value="rejected"),
            mock.patch.object(browser_service.sms_provider, "acquire_number", return_value=SmsActivation("act-1", "16195550123", "grizzly")),
            mock.patch.object(browser_service.sms_provider, "cancel") as cancel,
        ):
            with self.assertRaisesRegex(browser_service.CodexOAuthBrowserError, "phone verification failed"):
                runner._handle_phone_verification(page, sms_cfg)

        cancel.assert_called_once_with("act-1", sms_cfg)

    def test_handle_email_verification_uses_existing_mailbox(self) -> None:
        page = FakePage("https://auth.openai.com/login")
        runner = CodexOAuthBrowserRunner(sleep_func=lambda seconds: None)
        mailbox = {"provider": "cloudflare_temp_email", "address": "web@example.com"}

        with (
            mock.patch("services.register.openai_register._mail_config", return_value={"wait_timeout": 1}) as mail_config,
            mock.patch.object(browser_service.mail_provider, "get_existing_mailbox", return_value=mailbox) as get_mailbox,
            mock.patch.object(browser_service.mail_provider, "wait_for_code", return_value="123456") as wait_code,
            mock.patch.object(browser_service, "_clear_inputs") as clear_inputs,
            mock.patch.object(browser_service, "_set_input_value", return_value=True) as set_input,
            mock.patch.object(browser_service, "_read_input_value", return_value="web@example.com"),
            mock.patch.object(browser_service, "_click_email_continue", return_value=True) as click_continue,
            mock.patch.object(browser_service, "_wait_for_email_code_page", return_value="code_page"),
        ):
            runner._handle_email_verification(page, "web@example.com", "http://proxy")

        mail_config.assert_called_once_with("http://proxy")
        get_mailbox.assert_called_once_with({"wait_timeout": 1}, "web@example.com")
        wait_code.assert_called_once()
        self.assertEqual(set_input.call_args_list[0].args[2], "web@example.com")
        self.assertEqual(set_input.call_args_list[1].args[2], "123456")
        self.assertEqual(clear_inputs.call_args_list[0].args[1], "email")
        self.assertEqual(clear_inputs.call_args_list[1].args[1], "otp")
        self.assertGreaterEqual(click_continue.call_count, 2)


if __name__ == "__main__":
    unittest.main()
