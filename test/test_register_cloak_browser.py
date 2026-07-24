import unittest

from services.register.cloak_browser import CloakBrowserClient, CloakBrowserUnavailable


class FakePage:
    def __init__(self):
        self.timeout_ms = 0

    def set_default_timeout(self, timeout_ms):
        self.timeout_ms = timeout_ms

    def set_default_navigation_timeout(self, timeout_ms):
        self.navigation_timeout_ms = timeout_ms


class FakeContext:
    def __init__(self, browser=None):
        self.browser = browser
        self.pages = []
        self.closed = False

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        context = FakeContext(self)
        self.contexts.append(context)
        return context

    def close(self):
        self.closed = True


class CloakBrowserClientTests(unittest.TestCase):
    def test_missing_cloakbrowser_reports_optional_dependency(self):
        def importer(name):
            raise ModuleNotFoundError(name)

        client = CloakBrowserClient({}, importer=importer)

        with self.assertRaisesRegex(CloakBrowserUnavailable, "cloakbrowser"):
            client.open_browser()

    def test_open_browser_builds_launch_and_context_options(self):
        calls = {}

        class FakeModule:
            @staticmethod
            def launch(**kwargs):
                calls["launch"] = kwargs
                return FakeBrowser()

            @staticmethod
            def launch_persistent_context(user_data_dir, **kwargs):
                raise AssertionError("not persistent")

        client = CloakBrowserClient(
            {
                "headless": False,
                "humanize": True,
                "geoip": False,
                "use_proxy": True,
                "locale": "ja-JP",
                "timezone": "Asia/Tokyo",
                "accept_language": "ja-JP,ja;q=0.9",
                "license_key": "license-secret",
                "fingerprint_seed": "seed-1",
                "timeout": 12,
            },
            importer=lambda name: FakeModule,
        )

        session = client.open_browser("socks5h://127.0.0.1:1080")

        self.assertFalse(calls["launch"]["headless"])
        self.assertEqual(calls["launch"]["proxy"], "socks5://127.0.0.1:1080")
        self.assertEqual(calls["launch"]["locale"], "ja-JP")
        self.assertEqual(calls["launch"]["timezone"], "Asia/Tokyo")
        self.assertEqual(calls["launch"]["license_key"], "license-secret")
        self.assertIn("--fingerprint=seed-1", calls["launch"]["args"])
        self.assertEqual(session.context.browser.context_kwargs["locale"], "ja-JP")
        self.assertEqual(session.context.browser.context_kwargs["timezone_id"], "Asia/Tokyo")
        self.assertEqual(session.context.browser.context_kwargs["extra_http_headers"]["Accept-Language"], "ja-JP,ja;q=0.9")
        self.assertEqual(session.page.timeout_ms, 12000)

    def test_persistent_context_is_supported(self):
        calls = {}

        class FakeModule:
            @staticmethod
            def launch(**kwargs):
                raise AssertionError("should use persistent")

            @staticmethod
            def launch_persistent_context(user_data_dir, **kwargs):
                calls["persistent"] = (user_data_dir, kwargs)
                return FakeContext()

        client = CloakBrowserClient({"user_data_dir": "profiles/default"}, importer=lambda name: FakeModule)
        session = client.open_browser()

        self.assertEqual(calls["persistent"][0], "profiles/default")
        self.assertIs(session.browser, session.context)


if __name__ == "__main__":
    unittest.main()
