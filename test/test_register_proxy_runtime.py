import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from services.proxy_service import ClearanceBundle
from services.register import openai_register
from services.register_service import RegisterService
from utils import fingerprint
from utils import chromium_sentinel


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, url="https://auth.openai.com/test"):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url

    def json(self):
        return {}


class FakeCookieJar:
    def __init__(self):
        self.items = []

    def set(self, name, value, domain=None):
        self.items.append({"name": name, "value": value, "domain": domain})


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.headers = {}
        self.cookies = FakeCookieJar()
        self.closed = False

    def close(self):
        self.closed = True


class FakeProxySettings:
    def __init__(self, bundle=None):
        self.bundle = bundle
        self.refreshed = False
        self.session_kwargs_calls = []
        self.build_headers_calls = []
        self.refresh_calls = []

    def build_session_kwargs(self, **kwargs):
        self.session_kwargs_calls.append(kwargs)
        return dict(kwargs, proxy="http://runtime.example:8118")

    def build_headers(self, headers=None, target_url="", proxy="", upstream=True, **kwargs):
        self.build_headers_calls.append({"target_url": target_url, "proxy": proxy, "upstream": upstream})
        merged = dict(headers or {})
        if self.refreshed and self.bundle and self.bundle.cookies:
            merged["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.bundle.cookies.items())
        return merged

    def get_profile(self, **kwargs):
        class Profile:
            clearance_enabled = True

        return Profile()

    def refresh_clearance(self, target_url="", proxy="", force=False, upstream=True, **kwargs):
        self.refresh_calls.append({"target_url": target_url, "proxy": proxy, "force": force, "upstream": upstream})
        self.refreshed = self.bundle is not None
        return self.bundle


class RegisterProxyRuntimeTests(unittest.TestCase):
    def test_create_session_uses_proxy_settings_without_breaking_existing_proxy_argument(self):
        fake_proxy = FakeProxySettings()
        created = []

        def fake_session_factory(**kwargs):
            session = FakeSession(**kwargs)
            created.append(session)
            return session

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register.requests,
            "Session",
            side_effect=fake_session_factory,
        ):
            session = openai_register.create_session("http://legacy-register.example:8080")

        self.assertIs(session, created[0])
        self.assertEqual(fake_proxy.session_kwargs_calls[0]["proxy"], "http://legacy-register.example:8080")
        self.assertTrue(fake_proxy.session_kwargs_calls[0]["upstream"])
        self.assertEqual(fake_proxy.session_kwargs_calls[0]["impersonate"], "chrome")
        self.assertFalse(fake_proxy.session_kwargs_calls[0]["verify"])
        self.assertEqual(session.kwargs["proxy"], "http://runtime.example:8118")

    def test_cloudflare_without_clearance_keeps_clear_register_error(self):
        fake_proxy = FakeProxySettings(bundle=None)
        cf_response = FakeResponse(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
            headers={"server": "cloudflare", "content-type": "text/html"},
            url="https://auth.openai.com/api/accounts/authorize",
        )

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(openai_register, "request_with_local_retry", return_value=(cf_response, "")):
            registrar = openai_register.PlatformRegistrar(proxy="http://legacy-register.example:8080")
            with self.assertRaisesRegex(RuntimeError, "Cloudflare") as ctx:
                registrar._platform_authorize("user@example.com", 1)

        self.assertEqual(len(fake_proxy.refresh_calls), 1)
        self.assertIn("status=403", str(ctx.exception))
        self.assertIn("Just a moment", str(ctx.exception))

    def test_openai_html_behind_cloudflare_is_not_treated_as_challenge(self):
        response = FakeResponse(
            status_code=200,
            text="""
            <!DOCTYPE html><html lang=\"en-US\"><head>
            <title>Create a password - OpenAI</title>
            </head><body>OpenAI account page</body></html>
            """,
            headers={"server": "cloudflare", "content-type": "text/html; charset=utf-8"},
            url="https://auth.openai.com/create-account/password",
        )

        self.assertFalse(openai_register._is_cloudflare_challenge(response))

    def test_cloudflare_challenge_refreshes_clearance_and_retries_once_with_matching_headers(self):
        bundle = ClearanceBundle(
            target_host="auth.openai.com",
            proxy_url="http://runtime.example:8118",
            cookies={"cf_clearance": "flare-token"},
            user_agent="Flare UA",
        )
        fake_proxy = FakeProxySettings(bundle=bundle)
        responses = [
            FakeResponse(
                status_code=403,
                text="<html><title>Just a moment...</title></html>",
                headers={"server": "cloudflare", "content-type": "text/html"},
                url="https://auth.openai.com/api/accounts/authorize",
            ),
            FakeResponse(status_code=200, text="{}", headers={"content-type": "application/json"}),
        ]
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append({"method": method, "url": url, "headers": dict(kwargs.get("headers") or {})})
            return responses.pop(0), ""

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(openai_register, "request_with_local_retry", side_effect=fake_request):
            registrar = openai_register.PlatformRegistrar(proxy="http://legacy-register.example:8080")
            registrar._platform_authorize("user@example.com", 1)

        self.assertEqual(len(request_calls), 2)
        self.assertEqual(len(fake_proxy.refresh_calls), 1)
        retry_headers = {key.lower(): value for key, value in request_calls[1]["headers"].items()}
        self.assertEqual(retry_headers["user-agent"], "Flare UA")
        self.assertEqual(retry_headers["cookie"], "cf_clearance=flare-token")
        self.assertEqual(fake_proxy.refresh_calls[0]["target_url"], openai_register.auth_base)
        self.assertEqual(fake_proxy.refresh_calls[0]["proxy"], "http://legacy-register.example:8080")
        self.assertTrue(fake_proxy.refresh_calls[0]["force"])

    def test_refresh_failure_reports_cloudflare_detail_without_infinite_retry(self):
        fake_proxy = FakeProxySettings(bundle=None)
        cf_response = FakeResponse(
            status_code=403,
            text="<html><title>Just a moment...</title><body>challenge body</body></html>",
            headers={"server": "cloudflare", "content-type": "text/html"},
            url="https://auth.openai.com/api/accounts/authorize",
        )
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append({"method": method, "url": url})
            return cf_response, ""

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(openai_register, "request_with_local_retry", side_effect=fake_request):
            registrar = openai_register.PlatformRegistrar(proxy="")
            with self.assertRaisesRegex(RuntimeError, "Cloudflare") as ctx:
                registrar._platform_authorize("user@example.com", 1)

        self.assertEqual(len(request_calls), 1)
        self.assertEqual(len(fake_proxy.refresh_calls), 1)
        message = str(ctx.exception)
        self.assertIn("status=403", message)
        self.assertIn("challenge body", message)

    def test_step_failure_dumps_redacted_artifact_with_diagnosis(self):
        response = FakeResponse(
            status_code=400,
            text='{"message":"Failed to create account. Please try again.","access_token":"secret-token-value"}',
            headers={"content-type": "application/json", "set-cookie": "cf_clearance=secret-cookie"},
            url="https://auth.openai.com/api/accounts/user/register",
        )
        with TemporaryDirectory() as tmp:
            with patch.object(openai_register, "register_failure_dir", Path(tmp)):
                with self.assertRaises(openai_register.RegistrationStepError) as ctx:
                    openai_register._raise_step_failure(
                        7,
                        "user_register",
                        "POST",
                        "https://auth.openai.com/api/accounts/user/register",
                        response,
                        request_headers={"authorization": "Bearer secret", "user-agent": "UA"},
                        request_body={"username": "user@example.com", "password": "secret-password"},
                    )

            self.assertIn("上游拒绝创建账号", ctx.exception.diagnosis)
            artifact = Path(ctx.exception.artifact_path)
            self.assertTrue(artifact.exists())
            metadata = (artifact / "metadata.json").read_text(encoding="utf-8")
            body = (artifact / "response_body.json").read_text(encoding="utf-8")
            self.assertIn("user_register", metadata)
            self.assertIn("***redacted***", metadata)
            self.assertNotIn("secret-password", metadata)
            self.assertNotIn("secret-token-value", body)

    def test_sentinel_headers_include_so_token_when_backend_returns_so(self):
        class SentinelSession:
            def post(self, *args, **kwargs):
                return FakeResponse(
                    status_code=200,
                    text="{}",
                    headers={"content-type": "application/json"},
                    url="https://sentinel.openai.com/backend-api/sentinel/req",
                )

        with patch.object(FakeResponse, "json", return_value={"token": "challenge-token", "so": "so-token", "proofofwork": {"required": False}}):
            headers = openai_register.build_sentinel_headers(SentinelSession(), "device-1", "oauth_create_account", profile=fingerprint.DEFAULT_PROFILE)

        self.assertIn("openai-sentinel-token", headers)
        self.assertIn("openai-sentinel-so-token", headers)
        self.assertIn('"c":"challenge-token"', headers["openai-sentinel-so-token"])
        self.assertIn('"so":"so-token"', headers["openai-sentinel-so-token"])
        self.assertIn('"flow":"oauth_create_account"', headers["openai-sentinel-so-token"])

    def test_register_service_get_exposes_sanitized_realtime_logs(self):
        with TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            service._append_log(
                "任务5 注册失败，本次耗时8.4s，原因: create_account_http_400; status=400; "
                "诊断=create_account 返回 HTTP 400，需要查看本地抓包目录中的 metadata.json 和 response_body.*; "
                "抓包目录=C:\\chatgpt2api\\data\\register_failures\\20260707_task5_create_account_400; "
                "url=https://auth.openai.com/api/accounts/create_account, content_type=application/json, "
                "cf-ray=a1770a7b6ce55e6f-LAX, x-request-id=819ea02f-1f4b-45e7-9160-baff69339d5f, "
                "openai-processing-ms=391, json={\"error\": {\"code\": \"registration_disallowed\"}}",
                "red",
            )
            snapshot = service.get()

        self.assertIn("logs", snapshot)
        self.assertEqual(len(snapshot["logs"]), 1)
        log_text = snapshot["logs"][0]["text"]
        self.assertIn("create_account_http_400", log_text)
        self.assertNotIn("抓包目录", log_text)
        self.assertNotIn("metadata.json", log_text)
        self.assertNotIn("response_body", log_text)
        self.assertNotIn("register_failures", log_text)
        self.assertNotIn("url=https://auth.openai.com", log_text)
        self.assertNotIn("content_type=", log_text)
        self.assertNotIn("cf-ray=", log_text)
        self.assertNotIn("x-request-id=", log_text)
        self.assertNotIn("openai-processing-ms=", log_text)
        self.assertNotIn("json=", log_text)

    def test_sentinel_browser_is_enabled_by_default_for_registrar(self):
        with patch.object(openai_register, "config", {**openai_register.config, "sentinel_browser_enabled": None}):
            options = openai_register._sentinel_browser_options()

        self.assertTrue(options["sentinel_browser_enabled"])

    def test_register_service_normalizes_new_account_health_settings(self):
        with TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            snapshot = service.update(
                {
                    "new_account_warmup_minutes": 15,
                    "new_account_verify_delay_seconds": 45,
                    "new_account_max_verify_workers": 3,
                }
            )

        self.assertEqual(snapshot["new_account_warmup_minutes"], 15)
        self.assertEqual(snapshot["new_account_verify_delay_seconds"], 45)
        self.assertEqual(snapshot["new_account_max_verify_workers"], 3)

    def test_sentinel_headers_can_use_chromium_sdk_provider(self):
        class SentinelSession:
            def post(self, *args, **kwargs):
                raise AssertionError("browser provider should not call backend sentinel req")

        class BrowserResult:
            token = '{"p":"browser-p","t":"","c":"browser-c","id":"device-1","flow":"oauth_create_account"}'
            so_token = '{"so":"browser-so","c":"browser-c","id":"device-1","flow":"oauth_create_account"}'

        with patch.object(openai_register, "build_chromium_sentinel_token", return_value=BrowserResult()) as provider:
            headers = openai_register.build_sentinel_headers(
                SentinelSession(),
                "device-1",
                "oauth_create_account",
                profile=fingerprint.DEFAULT_PROFILE,
                sentinel_browser_enabled=True,
                sentinel_browser_timeout=7.0,
                sentinel_browser_chrome_path="C:/Chrome/chrome.exe",
                sentinel_browser_sdk_url="https://sentinel.openai.com/sentinel/test/sdk.js",
            )

        self.assertEqual(headers["openai-sentinel-token"], BrowserResult.token)
        self.assertEqual(headers["openai-sentinel-so-token"], BrowserResult.so_token)
        provider.assert_called_once()
        provider_kwargs = provider.call_args.kwargs
        self.assertEqual(provider_kwargs["flow"], "oauth_create_account")
        self.assertEqual(provider_kwargs["device_id"], "device-1")
        self.assertEqual(provider_kwargs["user_agent"], fingerprint.DEFAULT_PROFILE.user_agent)
        self.assertEqual(provider_kwargs["screen_resolution"], fingerprint.DEFAULT_PROFILE.screen_resolution)
        self.assertEqual(provider_kwargs["timeout"], 7.0)
        self.assertEqual(provider_kwargs["chrome_path"], "C:/Chrome/chrome.exe")
        self.assertEqual(provider_kwargs["sdk_url"], "https://sentinel.openai.com/sentinel/test/sdk.js")

    def test_sentinel_headers_reuse_chromium_session_provider(self):
        class SentinelSession:
            def post(self, *args, **kwargs):
                raise AssertionError("browser provider should not call backend sentinel req")

        class BrowserProvider:
            def __init__(self):
                self.calls = []

            def token(self, *, flow, device_id):
                self.calls.append((flow, device_id))
                return chromium_sentinel.ChromiumSentinelResult(
                    token=f'{{"p":"browser-p","t":"","c":"browser-c","id":"{device_id}","flow":"{flow}"}}',
                    so_token="",
                )

        provider = BrowserProvider()
        for flow in ("username_password_create", "oauth_create_account"):
            headers = openai_register.build_sentinel_headers(
                SentinelSession(),
                "device-1",
                flow,
                profile=fingerprint.DEFAULT_PROFILE,
                sentinel_browser_enabled=True,
                sentinel_browser_provider=provider,
            )
            self.assertIn(f'"flow":"{flow}"', headers["openai-sentinel-token"])

        self.assertEqual(
            provider.calls,
            [
                ("username_password_create", "device-1"),
                ("oauth_create_account", "device-1"),
            ],
        )

    def test_platform_registrar_owns_one_lazy_chromium_session(self):
        fake_http_session = FakeSession()

        class BrowserProvider:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.closed = False

            def close(self):
                self.closed = True

        with patch.object(openai_register, "create_session", return_value=fake_http_session), patch.object(
            openai_register,
            "ChromiumSentinelSession",
            BrowserProvider,
        ):
            registrar = openai_register.PlatformRegistrar(profile=fingerprint.DEFAULT_PROFILE)
            provider = registrar.sentinel_options["sentinel_browser_provider"]
            registrar.close()

        self.assertIsInstance(provider, BrowserProvider)
        self.assertEqual(provider.kwargs["user_agent"], fingerprint.DEFAULT_PROFILE.user_agent)
        self.assertEqual(provider.kwargs["screen_resolution"], fingerprint.DEFAULT_PROFILE.screen_resolution)
        self.assertTrue(provider.closed)
        self.assertTrue(fake_http_session.closed)

    def test_sentinel_headers_fallback_to_backend_when_chromium_provider_fails(self):
        with patch.object(openai_register, "build_chromium_sentinel_token", side_effect=RuntimeError("timeout")), patch.object(
            openai_register,
            "_build_sentinel_tokens_tuple",
            return_value=(
                '{"p":"backend-p","t":"","c":"backend-c","id":"device-1","flow":"oauth_create_account"}',
                "0backend-c",
                '{"so":"backend-so","c":"backend-c","id":"device-1","flow":"oauth_create_account"}',
            ),
        ) as backend:
            headers = openai_register.build_sentinel_headers(
                FakeSession(),
                "device-1",
                "oauth_create_account",
                profile=fingerprint.DEFAULT_PROFILE,
                sentinel_browser_enabled=True,
                sentinel_browser_fallback=True,
            )

        self.assertIn('"c":"backend-c"', headers["openai-sentinel-token"])
        self.assertIn('"so":"backend-so"', headers["openai-sentinel-so-token"])
        backend.assert_called_once()

    def test_sentinel_headers_can_disable_chromium_fallback(self):
        with patch.object(openai_register, "build_chromium_sentinel_token", side_effect=RuntimeError("timeout")):
            with self.assertRaisesRegex(RuntimeError, "chromium_sentinel_failed"):
                openai_register.build_sentinel_headers(
                    FakeSession(),
                    "device-1",
                    "oauth_create_account",
                    profile=fingerprint.DEFAULT_PROFILE,
                    sentinel_browser_enabled=True,
                    sentinel_browser_fallback=False,
                )

    def test_chromium_sentinel_accepts_chatgpt_auth_redirect_target(self):
        tabs = [
            {
                "type": "background_page",
                "url": "chrome-extension://example/background.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/background",
            },
            {
                "type": "page",
                "url": "https://chatgpt.com/auth/login_with?callback_path=/",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/chatgpt",
            },
        ]

        with patch.object(chromium_sentinel, "_json_get", return_value=tabs):
            page = chromium_sentinel._select_page(9222, 1)

        self.assertEqual(page["webSocketDebuggerUrl"], "ws://127.0.0.1/devtools/page/chatgpt")

    def test_chromium_sentinel_launches_blank_before_auth_navigation(self):
        args = chromium_sentinel._chrome_launch_args(
            chrome="C:/Chrome/chrome.exe",
            user_agent=fingerprint.DEFAULT_PROFILE.user_agent,
            screen_resolution="1920x1080",
            user_data_dir=Path("C:/sentinel-profile"),
            headless=True,
        )

        self.assertEqual(args[-1], "about:blank")
        self.assertNotIn("https://auth.openai.com/", args)
        self.assertIn("--blink-settings=imagesEnabled=false", args)

    def test_chromium_sentinel_blocks_heavy_resources_before_auth_navigation(self):
        calls = []
        client_state = {"closed": False}

        class FakeCDPClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                client_state["closed"] = True

            def call(self, method, params=None, timeout=None):
                calls.append((method, params or {}))
                return {"result": {}}

        provider = chromium_sentinel.ChromiumSentinelSession(
            user_agent=fingerprint.DEFAULT_PROFILE.user_agent,
        )
        provider._port = 9222
        page = {"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/blank"}
        with patch.object(chromium_sentinel, "_select_page", return_value=page), patch.object(
            chromium_sentinel,
            "_CDPClient",
            FakeCDPClient,
        ), patch.object(chromium_sentinel.time, "sleep"):
            provider._prepare_auth_page()

        methods = [method for method, _params in calls]
        self.assertLess(methods.index("Network.setBlockedURLs"), methods.index("Page.navigate"))
        blocked_urls = dict(calls)["Network.setBlockedURLs"]["urls"]
        self.assertIn("*://auth-cdn.oaistatic.com/*", blocked_urls)
        self.assertIn("*://chatgpt.com/*.js*", blocked_urls)
        self.assertEqual(dict(calls)["Page.navigate"]["url"], "https://auth.openai.com/")
        self.assertFalse(client_state["closed"])
        provider.close()
        self.assertTrue(client_state["closed"])

    def test_chromium_sentinel_detects_navigation_race_errors(self):
        self.assertTrue(
            chromium_sentinel._is_target_navigation_error(
                RuntimeError(
                    "CDP Runtime.evaluate failed: {'code': -32000, 'message': 'Inspected target navigated or closed'}"
                )
            )
        )
        self.assertTrue(chromium_sentinel._is_target_navigation_error(RuntimeError("Execution context was destroyed.")))
        self.assertTrue(chromium_sentinel._is_target_navigation_error(RuntimeError("Cannot find default execution context")))
        self.assertFalse(chromium_sentinel._is_target_navigation_error(RuntimeError("sentinel token timeout")))

    def test_chromium_sentinel_startup_error_includes_chrome_stderr(self):
        class FakeProcess:
            def poll(self):
                return 1

        with TemporaryDirectory() as tmp:
            stderr_path = Path(tmp) / "chrome-stderr.log"
            stderr_path.write_text("No usable sandbox! Update your kernel or use --no-sandbox", encoding="utf-8")
            error = chromium_sentinel._chrome_startup_error(
                TimeoutError("等待 Chrome DevToolsActivePort 超时"),
                FakeProcess(),
                "/usr/bin/google-chrome",
                stderr_path,
            )

        message = str(error)
        self.assertIn("等待 Chrome DevToolsActivePort 超时", message)
        self.assertIn("/usr/bin/google-chrome", message)
        self.assertIn("No usable sandbox", message)

    def test_chromium_sentinel_devtools_port_retries_permission_race(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "read_text",
            side_effect=[PermissionError("locked"), "9222\n/devtools/browser"],
        ):
            port = chromium_sentinel._read_devtools_port(Path("ignored"), 1)

        self.assertEqual(port, 9222)

    def test_chromium_sentinel_user_data_parent_defaults_inside_repo_data(self):
        parent = chromium_sentinel._chrome_user_data_parent()

        self.assertEqual(parent.name, "chromium_tmp")
        self.assertEqual(parent.parent.name, "data")
        self.assertTrue(parent.exists())

    def test_new_registration_profile_matches_successful_browser_sample(self):
        profile = fingerprint.random_profile()

        self.assertEqual(profile.name, "chrome150_win")
        self.assertIn("Chrome/150.0.0.0", profile.user_agent)
        self.assertEqual(profile.accept_language, "zh-CN,zh;q=0.9")
        self.assertEqual(
            profile.sec_ch_ua,
            '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
        )

if __name__ == "__main__":
    unittest.main()
