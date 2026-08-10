import unittest
from types import SimpleNamespace
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
    def test_classify_create_account_unsupported_email(self):
        response = FakeResponse(
            status_code=400,
            text='{"error":{"message":"The email you provided is not supported.","code":"unsupported_email"}}',
            headers={"content-type": "application/json"},
            url="https://auth.openai.com/api/accounts/create_account",
        )

        def response_json():
            return {"error": {"message": "The email you provided is not supported.", "code": "unsupported_email"}}

        response.json = response_json

        diagnosis = openai_register._classify_failure("create_account", response)

        self.assertIn("邮箱地址不被上游支持", diagnosis)

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
        self.assertEqual(fake_proxy.session_kwargs_calls[0]["impersonate"], "chrome146")
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

    def test_register_service_normalizes_codex_agent_identity_settings(self):
        with TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            snapshot = service.update(
                {
                    "codex_agent_identity_enabled": "yes",
                    "codex_agent_identity_verify_task": "0",
                }
            )

        self.assertTrue(snapshot["codex_agent_identity_enabled"])
        self.assertFalse(snapshot["codex_agent_identity_verify_task"])

    def test_register_service_normalizes_driver_cpa_and_nested_runtime_settings(self):
        with TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            snapshot = service.update(
                {
                    "registration_driver": "chatgpt_web",
                    "codex_oauth_enabled": "yes",
                    "codex_oauth_via_cpa": "0",
                    "codex_oauth_cpa_pool_id": " pool-1 ",
                    "chatgpt_web": {"bootstrap_enabled": "0", "bootstrap_strict": "yes"},
                    "flow_trigger": {"enabled": True, "bearer": "bearer-token", "cookie": "cookie-token"},
                    "browser_use": {"api_key": "browser-token"},
                    "skyvern": {"api_key": "skyvern-token"},
                    "roxy": {"api_token": "roxy-token"},
                    "cloak": {"license_key": "cloak-token"},
                    "sms": {
                        "api_key": "sms-token",
                        "provider": "h",
                        "l_admin_auth_code": "l-code",
                        "h_admin_auth_code": "h-code",
                    },
                }
            )

        self.assertEqual(snapshot["registration_driver"], "chatgpt_web")
        self.assertTrue(snapshot["codex_oauth_enabled"])
        self.assertFalse(snapshot["codex_oauth_via_cpa"])
        self.assertEqual(snapshot["codex_oauth_cpa_pool_id"], "pool-1")
        self.assertFalse(snapshot["chatgpt_web"]["bootstrap_enabled"])
        self.assertTrue(snapshot["chatgpt_web"]["bootstrap_strict"])
        self.assertEqual(snapshot["flow_trigger"]["bearer"], "")
        self.assertEqual(snapshot["flow_trigger"]["cookie"], "")
        self.assertTrue(snapshot["flow_trigger"]["has_bearer"])
        self.assertTrue(snapshot["flow_trigger"]["has_cookie"])
        self.assertEqual(snapshot["browser_use"]["api_key"], "")
        self.assertTrue(snapshot["browser_use"]["has_api_key"])
        self.assertIn("cdp_base", snapshot["browser_use"])
        self.assertEqual(snapshot["skyvern"]["api_key"], "")
        self.assertTrue(snapshot["skyvern"]["has_api_key"])
        self.assertIn("api_base", snapshot["skyvern"])
        self.assertEqual(snapshot["roxy"]["api_token"], "")
        self.assertTrue(snapshot["roxy"]["has_api_token"])
        self.assertEqual(snapshot["cloak"]["license_key"], "")
        self.assertTrue(snapshot["cloak"]["has_license_key"])
        self.assertEqual(snapshot["sms"]["api_key"], "")
        self.assertEqual(snapshot["sms"]["l_admin_auth_code"], "")
        self.assertEqual(snapshot["sms"]["h_admin_auth_code"], "")
        self.assertTrue(snapshot["sms"]["has_api_key"])
        self.assertTrue(snapshot["sms"]["has_l_admin_auth_code"])
        self.assertTrue(snapshot["sms"]["has_h_admin_auth_code"])
        self.assertEqual(snapshot["sms"]["provider"], "h")
        self.assertEqual(service._config["flow_trigger"]["bearer"], "bearer-token")
        self.assertEqual(service._config["flow_trigger"]["cookie"], "cookie-token")
        self.assertEqual(service._config["browser_use"]["api_key"], "browser-token")
        self.assertEqual(service._config["skyvern"]["api_key"], "skyvern-token")
        self.assertEqual(service._config["roxy"]["api_token"], "roxy-token")
        self.assertEqual(service._config["cloak"]["license_key"], "cloak-token")
        self.assertEqual(service._config["sms"]["api_key"], "sms-token")
        self.assertEqual(service._config["sms"]["l_admin_auth_code"], "l-code")
        self.assertEqual(service._config["sms"]["h_admin_auth_code"], "h-code")

    def test_register_service_preserves_redacted_runtime_secrets_on_update(self):
        with TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            service.update(
                {
                    "flow_trigger": {"bearer": "bearer-token", "cookie": "cookie-token"},
                    "browser_use": {"api_key": "browser-token"},
                    "skyvern": {"api_key": "skyvern-token"},
                    "roxy": {"api_token": "roxy-token"},
                    "cloak": {"license_key": "cloak-token"},
                    "sms": {
                        "api_key": "sms-token",
                        "l_admin_auth_code": "l-code",
                        "h_admin_auth_code": "h-code",
                    },
                }
            )
            snapshot = service.update(
                {
                    "flow_trigger": {"bearer": "", "has_bearer": True, "cookie": "", "has_cookie": True},
                    "browser_use": {"api_key": "", "has_api_key": True},
                    "skyvern": {"api_key": "", "has_api_key": True},
                    "roxy": {"api_token": "", "has_api_token": True},
                    "cloak": {"license_key": "", "has_license_key": True},
                    "sms": {
                        "api_key": "",
                        "has_api_key": True,
                        "l_admin_auth_code": "",
                        "has_l_admin_auth_code": True,
                        "h_admin_auth_code": "",
                        "has_h_admin_auth_code": True,
                    },
                }
            )

        self.assertEqual(snapshot["browser_use"]["api_key"], "")
        self.assertTrue(snapshot["browser_use"]["has_api_key"])
        self.assertEqual(service._config["flow_trigger"]["bearer"], "bearer-token")
        self.assertEqual(service._config["flow_trigger"]["cookie"], "cookie-token")
        self.assertEqual(service._config["browser_use"]["api_key"], "browser-token")
        self.assertEqual(service._config["skyvern"]["api_key"], "skyvern-token")
        self.assertEqual(service._config["roxy"]["api_token"], "roxy-token")
        self.assertEqual(service._config["cloak"]["license_key"], "cloak-token")
        self.assertEqual(service._config["sms"]["api_key"], "sms-token")
        self.assertEqual(service._config["sms"]["l_admin_auth_code"], "l-code")
        self.assertEqual(service._config["sms"]["h_admin_auth_code"], "h-code")
        self.assertNotIn("has_api_key", service._config["browser_use"])
        self.assertNotIn("has_h_admin_auth_code", service._config["sms"])

    def test_worker_saves_registered_account_with_codex_agent_identity_when_enabled(self):
        saved_items = []

        class FakeRegistrar:
            def __init__(self, proxy):
                self.proxy = proxy

            def register(self, index):
                return {
                    "email": "new@example.com",
                    "password": "password",
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "source_type": "web",
                    "device_id": "device-1",
                    "fingerprint_profile": "profile-1",
                }

            def close(self, index):
                pass

        class FakeAccountService:
            def add_account_items(self, items):
                saved_items.extend(items)
                return {"added": len(items), "skipped": 0, "items": items}

            def verify_new_accounts(self, tokens):
                return {"refreshed": 1, "errors": [], "items": saved_items}

        identity_result = SimpleNamespace(
            account_payload={
                "access_token": "access-token",
                "source_type": "codex",
                "export_type": "codex_agent_identity",
                "email": "new@example.com",
                "account_id": "acct_123",
                "user_id": "user_123",
                "plan_type": "plus",
                "agent_identity": {
                    "agent_runtime_id": "runtime_123",
                    "agent_private_key": "private-key",
                },
            },
            verify_warning="",
        )

        with (
            patch.object(openai_register, "config", {**openai_register.config, "registration_driver": "chatgpt_web", "codex_agent_identity_enabled": True, "codex_agent_identity_verify_task": False}),
            patch.object(openai_register, "create_driver", return_value=FakeRegistrar("")),
            patch.object(openai_register, "get_driver_info", return_value=SimpleNamespace(supports_agent_identity=True)),
            patch.object(openai_register, "account_service", FakeAccountService()),
            patch.object(openai_register.codex_agent_identity_service, "create_agent_identity", return_value=identity_result) as create_identity,
        ):
            result = openai_register.worker(1)

        self.assertTrue(result["ok"])
        self.assertEqual(saved_items[0]["source_type"], "codex")
        self.assertEqual(saved_items[0]["export_type"], "codex_agent_identity")
        self.assertEqual(saved_items[0]["refresh_token"], "refresh-token")
        self.assertEqual(saved_items[0]["agent_identity"]["agent_runtime_id"], "runtime_123")
        create_identity.assert_called_once()
        args, kwargs = create_identity.call_args
        self.assertEqual(args, ("access-token",))
        self.assertFalse(kwargs["verify_task"])
        self.assertEqual(kwargs["metadata"]["email"], "new@example.com")
        self.assertEqual(kwargs["metadata"]["id_token"], "id-token")

    def test_worker_preserves_registered_account_when_codex_agent_identity_fails(self):
        saved_items = []

        class FakeRegistrar:
            def __init__(self, proxy):
                self.proxy = proxy

            def register(self, index):
                return {
                    "email": "new@example.com",
                    "password": "password",
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "source_type": "web",
                    "device_id": "device-1",
                    "fingerprint_profile": "profile-1",
                }

            def close(self, index):
                pass

        class FakeAccountService:
            def add_account_items(self, items):
                saved_items.extend(items)
                return {"added": len(items), "skipped": 0, "items": items}

            def verify_new_accounts(self, tokens):
                return {"refreshed": 1, "errors": [], "items": saved_items}

        with (
            patch.object(openai_register, "config", {**openai_register.config, "registration_driver": "chatgpt_web", "codex_agent_identity_enabled": True, "codex_agent_identity_verify_task": False}),
            patch.object(openai_register, "create_driver", return_value=FakeRegistrar("")),
            patch.object(openai_register, "get_driver_info", return_value=SimpleNamespace(supports_agent_identity=True)),
            patch.object(openai_register, "account_service", FakeAccountService()),
            patch.object(openai_register.codex_agent_identity_service, "create_agent_identity", side_effect=RuntimeError("authapi unavailable")) as create_identity,
        ):
            result = openai_register.worker(1)

        self.assertTrue(result["ok"])
        self.assertEqual(saved_items[0]["source_type"], "web")
        self.assertEqual(saved_items[0]["access_token"], "access-token")
        self.assertEqual(saved_items[0]["refresh_token"], "refresh-token")
        self.assertEqual(saved_items[0]["codex_agent_identity_error"], "authapi unavailable")
        create_identity.assert_called_once()
        args, kwargs = create_identity.call_args
        self.assertEqual(args, ("access-token",))
        self.assertFalse(kwargs["verify_task"])
        self.assertEqual(kwargs["metadata"]["email"], "new@example.com")

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

    def test_sentinel_headers_prefer_reusable_chromium_session(self):
        class BrowserSession:
            def __init__(self):
                self.calls = []

            def get_token(self, *, flow, device_id):
                self.calls.append({"flow": flow, "device_id": device_id})
                return chromium_sentinel.ChromiumSentinelResult(
                    token='{"c":"browser-c","id":"device-1","flow":"oauth_create_account"}',
                    so_token='{"so":"browser-so","c":"browser-c"}',
                )

        browser = BrowserSession()
        with patch.object(
            openai_register,
            "build_chromium_sentinel_token",
            side_effect=AssertionError("one-shot browser provider must not be used"),
        ):
            headers = openai_register.build_sentinel_headers(
                FakeSession(),
                "device-1",
                "oauth_create_account",
                profile=fingerprint.DEFAULT_PROFILE,
                sentinel_browser_enabled=True,
                sentinel_browser_session=browser,
            )

        self.assertEqual(browser.calls, [{"flow": "oauth_create_account", "device_id": "device-1"}])
        self.assertIn("openai-sentinel-token", headers)
        self.assertIn("openai-sentinel-so-token", headers)

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

    def test_chromium_sentinel_session_reuses_one_browser_for_multiple_tokens(self):
        class AliveProcess:
            def poll(self):
                return None

        browser = chromium_sentinel.ChromiumSentinelSession(user_agent="test-agent")
        starts = []

        def fake_start(*, lightweight):
            starts.append(lightweight)
            browser._proc = AliveProcess()
            browser._lightweight = lightweight
            browser.start_count += 1

        result = chromium_sentinel.ChromiumSentinelResult(token='{"c":"token"}')
        with patch.object(browser, "_start", side_effect=fake_start), patch.object(
            browser,
            "_evaluate",
            return_value=result,
        ) as evaluate:
            first = browser.get_token(flow="username_password_create", device_id="device-1")
            second = browser.get_token(flow="oauth_create_account", device_id="device-1")

        self.assertIs(first, result)
        self.assertIs(second, result)
        self.assertEqual(starts, [True])
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(browser.stats["start_count"], 1)
        self.assertEqual(browser.stats["token_count"], 2)

    def test_chromium_sentinel_session_falls_back_to_full_page_once(self):
        class AliveProcess:
            def poll(self):
                return None

        browser = chromium_sentinel.ChromiumSentinelSession(user_agent="test-agent")
        starts = []

        def fake_start(*, lightweight):
            starts.append(lightweight)
            browser._proc = AliveProcess()
            browser._lightweight = lightweight
            browser.start_count += 1

        result = chromium_sentinel.ChromiumSentinelResult(token='{"c":"token"}')
        with patch.object(browser, "_start", side_effect=fake_start), patch.object(
            browser,
            "_evaluate",
            side_effect=[RuntimeError("lightweight failed"), result],
        ):
            actual = browser.get_token(flow="oauth_create_account", device_id="device-1")

        self.assertIs(actual, result)
        self.assertEqual(starts, [True, False])
        self.assertEqual(browser.stats["lightweight_fallback_count"], 1)

    def test_chromium_sentinel_session_rebuilds_dead_full_browser_in_full_mode(self):
        class DeadProcess:
            def poll(self):
                return 1

        class AliveProcess:
            def poll(self):
                return None

        browser = chromium_sentinel.ChromiumSentinelSession(user_agent="test-agent")
        browser._proc = DeadProcess()
        browser._lightweight = False
        browser.start_count = 1
        starts = []

        def fake_start(*, lightweight):
            starts.append(lightweight)
            browser._proc = AliveProcess()
            browser._lightweight = lightweight
            browser.start_count += 1

        result = chromium_sentinel.ChromiumSentinelResult(token='{"c":"token"}')
        with patch.object(browser, "_start", side_effect=fake_start), patch.object(
            browser,
            "_evaluate",
            return_value=result,
        ):
            actual = browser.get_token(flow="oauth_create_account", device_id="device-1")

        self.assertIs(actual, result)
        self.assertEqual(starts, [False])

    def test_chromium_sentinel_session_tracks_download_bytes_by_host(self):
        browser = chromium_sentinel.ChromiumSentinelSession(user_agent="test-agent")
        browser._handle_cdp_event(
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "request-1",
                    "response": {"url": "https://chatgpt.com/assets/app.js"},
                },
            }
        )
        browser._handle_cdp_event(
            {
                "method": "Network.loadingFinished",
                "params": {"requestId": "request-1", "encodedDataLength": 4096},
            }
        )

        self.assertEqual(browser.stats["download_bytes"], 4096)
        self.assertEqual(browser.stats["download_bytes_by_host"], {"chatgpt.com": 4096})

    def test_chromium_sentinel_session_close_cleans_process_and_profile(self):
        class AliveProcess:
            pass

        browser = chromium_sentinel.ChromiumSentinelSession(user_agent="test-agent")
        process = AliveProcess()
        profile = Path("sentinel-profile")
        browser._proc = process
        browser._user_data_dir = profile

        with patch.object(chromium_sentinel, "_cleanup_chrome_process_and_profile") as cleanup:
            browser.close()

        cleanup.assert_called_once_with(process, profile)
        self.assertIsNone(browser._proc)
        self.assertIsNone(browser._user_data_dir)

    def test_platform_registrars_keep_separate_sentinel_browser_sessions(self):
        first = openai_register.PlatformRegistrar(profile=fingerprint.DEFAULT_PROFILE)
        second = openai_register.PlatformRegistrar(profile=fingerprint.DEFAULT_PROFILE)
        try:
            self.assertIsNotNone(first.sentinel_browser_session)
            self.assertIsNotNone(second.sentinel_browser_session)
            self.assertIsNot(first.sentinel_browser_session, second.sentinel_browser_session)
            self.assertNotEqual(first.device_id, second.device_id)
        finally:
            first.close()
            second.close()

    def test_new_registration_profile_matches_successful_browser_sample(self):
        profile = fingerprint.random_profile()

        self.assertEqual(profile.name, "chrome146_win")
        self.assertIn("Chrome/146.0.0.0", profile.user_agent)
        self.assertEqual(profile.accept_language, "zh-CN,zh;q=0.9")
        self.assertEqual(
            profile.sec_ch_ua,
            '"Not;A=Brand";v="8", "Chromium";v="146", "Google Chrome";v="146"',
        )

if __name__ == "__main__":
    unittest.main()
