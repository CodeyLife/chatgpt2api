import unittest
from unittest.mock import patch

from services.register import openai_register
from services.register.browser_registration import ChatGPTBrowserRegistrationFlow, CloudBrowserRegistrationDriver
from services.register.chatgpt_web import create_chatgpt_web_driver
from services.register.cloud_browser import CloudBrowserSession


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def text(self):
        return ""


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class FakeContext:
    def __init__(self, payload=None):
        self.request = FakeRequest(payload or {})
        self.pages = [FakePage()]

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class FakePage:
    url = "https://chatgpt.com/"

    def evaluate(self, *args, **kwargs):
        return {}


class FakeBodyLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout: int = 0) -> str:
        return self.text


class TextPage(FakePage):
    def __init__(self, text: str, url: str = "https://auth.openai.com/login") -> None:
        super().__init__()
        self.url = url
        self.text = text

    def locator(self, selector: str):
        return FakeBodyLocator(self.text)


class FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]


class FakeConnected:
    def __init__(self, context):
        self.browser = FakeBrowser(context)
        self.closed = False

    def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self, connected):
        self.connected = connected
        self.session = None

    def connect(self, session):
        self.session = session
        return self.connected


class BrowserRegistrationTests(unittest.TestCase):
    def test_fetch_session_prefers_context_request(self):
        payload = {"accessToken": "web-token", "user": {"email": "u@example.com"}}
        context = FakeContext(payload)
        flow = ChatGPTBrowserRegistrationFlow(
            FakePage(),
            context,
            index=1,
            provider_label="BrowserUse",
        )

        session = flow._fetch_chatgpt_session(timeout_seconds=1)

        self.assertEqual(session["accessToken"], "web-token")
        self.assertEqual(context.request.calls[0][0], "https://chatgpt.com/api/auth/session")

    def test_state_detects_account_unusable_page(self):
        flow = ChatGPTBrowserRegistrationFlow(
            TextPage("Your account has been deactivated."),
            FakeContext({}),
            index=1,
            provider_label="BrowserUse",
        )

        self.assertEqual(flow._state(), "account_unusable")

    def test_cloud_browser_driver_imports_account_payload_and_closes_session(self):
        payload = {
            "accessToken": "web-token",
            "expires": "2026-07-24T00:00:00Z",
            "user": {"id": "user-1", "email": "u@example.com"},
            "account": {"id": "account-1", "planType": "plus"},
        }
        context = FakeContext(payload)
        connected = FakeConnected(context)
        connector = FakeConnector(connected)
        cloud_session = CloudBrowserSession(
            connect_url="wss://browser.example",
            provider="browser_use",
            api_key_present=True,
            proxy_country_code="us",
            profile_id="profile-1",
            session_id="session-1",
        )

        class FakeClient:
            def __init__(self, cfg):
                self.cfg = cfg

            def open_session(self):
                return cloud_session

        seen_flow_kwargs = {}

        class FakeFlow:
            def __init__(self, page, context, **kwargs):
                seen_flow_kwargs.update(kwargs)

            def run(self, **kwargs):
                self.run_kwargs = kwargs
                return payload

        driver = CloudBrowserRegistrationDriver(
            {"browser_use": {"api_key": "key"}, "proxy": "http://proxy", "humanize": {"enabled": False, "factor": 0.25}},
            provider="browser_use",
            cloud_client_factory=FakeClient,
            connector_factory=lambda: connector,
            flow_class=FakeFlow,
        )

        with (
            patch("services.register.browser_registration.base.create_mailbox", return_value={"address": "u@example.com", "provider": "manual", "label": "manual"}) as create_mailbox,
            patch("services.register.browser_registration.base._random_password", return_value="Password1!"),
            patch("services.register.browser_registration.base._random_name", return_value=("Test", "User")),
            patch("services.register.browser_registration.base._random_birthdate", return_value="2000-01-02"),
            patch("services.register.browser_registration.base._new_account_health_metadata", return_value={"warmup_until": None}),
            patch("services.register.browser_registration.mail_provider.mark_mailbox_result") as mark_result,
        ):
            account = driver.register(7)

        create_mailbox.assert_called_once_with(register_proxy="http://proxy")
        self.assertEqual(account["access_token"], "web-token")
        self.assertEqual(account["source_type"], "browser_use")
        self.assertEqual(account["export_type"], "chatgpt_web")
        self.assertEqual(account["email"], "u@example.com")
        self.assertEqual(account["account_id"], "account-1")
        self.assertEqual(account["user_id"], "user-1")
        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["cloud_browser"]["session_id"], "session-1")
        self.assertFalse(seen_flow_kwargs["humanizer"].config.enabled)
        self.assertEqual(seen_flow_kwargs["humanizer"].config.factor, 0.25)
        self.assertTrue(connected.closed)
        mark_result.assert_called_once()

    def test_skyvern_driver_closes_remote_browser_session(self):
        payload = {"accessToken": "web-token", "user": {}, "account": {}}
        connected = FakeConnected(FakeContext(payload))
        connector = FakeConnector(connected)
        closed_sessions = []
        cloud_session = CloudBrowserSession(
            connect_url="wss://skyvern.example",
            provider="skyvern",
            api_key_present=True,
            session_id="session-1",
        )

        class FakeSkyvernClient:
            def __init__(self, cfg):
                pass

            def open_session(self):
                return cloud_session

            def close_browser_session(self, session_id):
                closed_sessions.append(session_id)
                return {}

        class FakeFlow:
            def __init__(self, page, context, **kwargs):
                pass

            def run(self, **kwargs):
                return payload

        driver = CloudBrowserRegistrationDriver(
            {"skyvern": {}, "proxy": ""},
            provider="skyvern",
            cloud_client_factory=FakeSkyvernClient,
            connector_factory=lambda: connector,
            flow_class=FakeFlow,
        )

        with (
            patch("services.register.browser_registration.base.create_mailbox", return_value={"address": "u@example.com", "provider": "manual"}),
            patch("services.register.browser_registration.base._random_password", return_value="Password1!"),
            patch("services.register.browser_registration.base._random_name", return_value=("Test", "User")),
            patch("services.register.browser_registration.base._random_birthdate", return_value="2000-01-02"),
            patch("services.register.browser_registration.base._new_account_health_metadata", return_value={}),
            patch("services.register.browser_registration.mail_provider.mark_mailbox_result"),
        ):
            account = driver.register(8)

        self.assertEqual(account["source_type"], "skyvern")
        self.assertEqual(closed_sessions, ["session-1"])

    def test_browser_drivers_are_registered_as_chatgpt_web_capable(self):
        browser_use = openai_register.get_driver_info("browser_use")
        skyvern = openai_register.get_driver_info("skyvern")
        roxy = openai_register.get_driver_info("roxy")

        self.assertIsNotNone(browser_use)
        self.assertIsNotNone(skyvern)
        self.assertIsNotNone(roxy)
        self.assertTrue(browser_use.supports_agent_identity)
        self.assertTrue(browser_use.supports_codex_oauth)
        self.assertTrue(skyvern.supports_agent_identity)
        self.assertTrue(skyvern.supports_codex_oauth)
        self.assertTrue(roxy.supports_agent_identity)
        self.assertTrue(roxy.supports_codex_oauth)

    def test_chatgpt_web_driver_factory_parses_bootstrap_booleans(self):
        with patch("services.register.openai_register.PlatformRegistrar.__init__", return_value=None):
            driver = create_chatgpt_web_driver(
                {
                    "proxy": "",
                    "chatgpt_web": {
                        "bootstrap_enabled": "0",
                        "bootstrap_strict": "yes",
                    },
                }
            )

        self.assertFalse(driver.bootstrap_enabled)
        self.assertTrue(driver.bootstrap_strict)


if __name__ == "__main__":
    unittest.main()
