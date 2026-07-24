from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

from services.register import mail_provider
from services.register import openai_register as base
from . import bootstrap
from utils.fingerprint import build_common_headers, build_navigate_headers


CHATGPT_BASE = "https://chatgpt.com"
PASSKEY_CLIENT_CAPABILITIES = "11111"
CC_CAPS = "login_methods"
CHATGPT_CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
CHATGPT_REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"


def _ensure_authorize_context(authorize_url: str, registrar: base.PlatformRegistrar, email: str) -> str:
    try:
        parsed = urlparse(authorize_url)
        if not parsed.netloc.endswith("auth.openai.com"):
            return authorize_url
        params = parse_qs(parsed.query, keep_blank_values=True)
        required = {
            "client_id": CHATGPT_CLIENT_ID,
            "redirect_uri": CHATGPT_REDIRECT_URI,
            "ext-oai-did": registrar.device_id,
            "auth_session_logging_id": registrar.auth_session_logging_id,
            "ext-passkey-client-capabilities": PASSKEY_CLIENT_CAPABILITIES,
            "screen_hint": "login_or_signup",
            "login_hint": email,
            "ccaps": CC_CAPS,
        }
        changed = False
        for key, value in required.items():
            if not params.get(key):
                params[key] = [value]
                changed = True
        if not changed:
            return authorize_url
        return parsed._replace(query=urlencode(params, doseq=True)).geturl()
    except Exception:
        return authorize_url


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


class ChatGPTWebRegistrar(base.PlatformRegistrar):
    """ChatGPT NextAuth registration driver.

    This driver intentionally reuses the existing OpenAI account creation steps
    and only swaps the OAuth shell: it starts through chatgpt.com NextAuth and
    ends by reading /api/auth/session, so the saved token is a ChatGPT Web
    session accessToken rather than a Platform OAuth token.
    """

    def __init__(self, proxy: str = "", *, bootstrap_enabled: bool = True, bootstrap_strict: bool = False) -> None:
        super().__init__(proxy)
        self.bootstrap_enabled = bool(bootstrap_enabled)
        self.bootstrap_strict = bool(bootstrap_strict)

    def _chatgpt_headers(self, referer: str = f"{CHATGPT_BASE}/") -> dict[str, str]:
        headers = build_common_headers(self.profile)
        headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "referer": referer,
                "origin": CHATGPT_BASE,
                "oai-device-id": self.device_id,
            }
        )
        return headers

    def _chatgpt_navigate_headers(self, referer: str = f"{CHATGPT_BASE}/") -> dict[str, str]:
        headers = build_navigate_headers(self.profile)
        headers["referer"] = referer
        return headers

    def _set_chatgpt_context_cookies(self) -> None:
        for domain in (".chatgpt.com", "chatgpt.com", ".auth.openai.com", "auth.openai.com"):
            self.session.cookies.set("oai-did", self.device_id, domain=domain)

    def _get_nextauth_json(self, index: int, path: str, step_name: str) -> dict:
        url = f"{CHATGPT_BASE}{path}"
        headers = base._headers_with_clearance(
            self._chatgpt_headers(),
            url,
            self.proxy,
            self.clearance_user_agent,
        )
        resp, error = base.request_with_local_retry(self.session, "get", url, headers=headers, verify=False)
        if base._is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(CHATGPT_BASE, index)
            if bundle is None:
                base._raise_step_failure(index, f"{step_name}_cloudflare", "GET", url, resp, error or self.clearance_failure_reason, headers, prefix=step_name)
            headers = base._headers_with_clearance(self._chatgpt_headers(), url, self.proxy, self.clearance_user_agent)
            resp, error = base.request_with_local_retry(self.session, "get", url, headers=headers, verify=False)
        if resp is None or resp.status_code != 200:
            base._raise_step_failure(index, step_name, "GET", url, resp, error, headers, prefix=step_name)
        data = base._response_json(resp)
        if not isinstance(data, dict):
            raise RuntimeError(f"{step_name} returned invalid JSON")
        return data

    def _chatgpt_authorize(self, email: str, index: int) -> None:
        base.step(index, "开始 ChatGPT NextAuth authorize")
        self._set_chatgpt_context_cookies()
        providers = self._get_nextauth_json(index, "/api/auth/providers", "chatgpt_providers")
        if "openai" not in providers:
            raise RuntimeError("ChatGPT NextAuth providers missing openai")
        csrf = str(self._get_nextauth_json(index, "/api/auth/csrf", "chatgpt_csrf").get("csrfToken") or "").strip()
        if not csrf:
            raise RuntimeError("ChatGPT NextAuth csrfToken is empty")

        query = {
            "prompt": "login",
            "ext-oai-did": self.device_id,
            "auth_session_logging_id": self.auth_session_logging_id,
            "ext-passkey-client-capabilities": PASSKEY_CLIENT_CAPABILITIES,
            "screen_hint": "login_or_signup",
            "login_hint": email,
            "ccaps": CC_CAPS,
        }
        url = f"{CHATGPT_BASE}/api/auth/signin/openai?{urlencode(query)}"
        headers = base._headers_with_clearance(
            self._chatgpt_headers(),
            url,
            self.proxy,
            self.clearance_user_agent,
        )
        headers["content-type"] = "application/x-www-form-urlencoded"
        body = urlencode({"callbackUrl": f"{CHATGPT_BASE}/", "csrfToken": csrf, "json": "true"})
        resp, error = base.request_with_local_retry(self.session, "post", url, data=body, headers=headers, verify=False)
        if base._is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(CHATGPT_BASE, index)
            if bundle is None:
                base._raise_step_failure(index, "chatgpt_signin_cloudflare", "POST", url, resp, error or self.clearance_failure_reason, headers, prefix="chatgpt_signin")
            headers = base._headers_with_clearance(self._chatgpt_headers(), url, self.proxy, self.clearance_user_agent)
            headers["content-type"] = "application/x-www-form-urlencoded"
            resp, error = base.request_with_local_retry(self.session, "post", url, data=body, headers=headers, verify=False)
        if resp is None or resp.status_code != 200:
            base._raise_step_failure(index, "chatgpt_signin", "POST", url, resp, error, headers, {"login_hint": email}, prefix="chatgpt_signin")
        authorize_url = _ensure_authorize_context(str(base._response_json(resp).get("url") or "").strip(), self, email)
        if not authorize_url:
            raise RuntimeError("ChatGPT NextAuth signin did not return authorize URL")

        nav_headers = base._headers_with_clearance(
            self._chatgpt_navigate_headers(f"{CHATGPT_BASE}/"),
            authorize_url,
            self.proxy,
            self.clearance_user_agent,
        )
        resp, error = base.request_with_local_retry(
            self.session,
            "get",
            authorize_url,
            headers=nav_headers,
            allow_redirects=True,
            verify=False,
        )
        if base._is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(base.auth_base, index)
            if bundle is None:
                base._raise_step_failure(index, "chatgpt_authorize_cloudflare", "GET", authorize_url, resp, error or self.clearance_failure_reason, nav_headers, prefix="chatgpt_authorize")
            nav_headers = base._headers_with_clearance(self._chatgpt_navigate_headers(f"{CHATGPT_BASE}/"), authorize_url, self.proxy, self.clearance_user_agent)
            resp, error = base.request_with_local_retry(self.session, "get", authorize_url, headers=nav_headers, allow_redirects=True, verify=False)
        if resp is None or resp.status_code >= 400:
            base._raise_step_failure(index, "chatgpt_authorize", "GET", authorize_url, resp, error, nav_headers, prefix="chatgpt_authorize")
        base.step(index, f"ChatGPT NextAuth authorize 完成 url={str(getattr(resp, 'url', '') or '')[:160]}")

    def _follow_chatgpt_callback(self, index: int) -> None:
        continue_url = str(self.continue_url or "").strip()
        if not continue_url:
            raise RuntimeError("create_account did not return continue_url")
        headers = self._chatgpt_navigate_headers(f"{base.auth_base}/about-you") if continue_url.startswith(CHATGPT_BASE) else self._navigate_headers(f"{base.auth_base}/about-you")
        headers = base._headers_with_clearance(headers, continue_url, self.proxy, self.clearance_user_agent)
        resp, error = base.request_with_local_retry(
            self.session,
            "get",
            continue_url,
            headers=headers,
            allow_redirects=True,
            verify=False,
        )
        if resp is None or resp.status_code >= 400:
            base._raise_step_failure(index, "chatgpt_callback", "GET", continue_url, resp, error, headers, prefix="chatgpt_callback")
        base.step(index, f"ChatGPT OAuth callback 完成 url={str(getattr(resp, 'url', '') or '')[:160]}")

    def _fetch_chatgpt_session(self, index: int) -> dict:
        data = self._get_nextauth_json(index, "/api/auth/session", "chatgpt_session")
        access_token = str(data.get("accessToken") or data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("ChatGPT session missing accessToken")
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        account = data.get("account") if isinstance(data.get("account"), dict) else {}
        base.step(
            index,
            "ChatGPT session 获取完成 "
            f"email={_first_text(user.get('email'), account.get('email')) or '?'} "
            f"plan={_first_text(account.get('planType'), account.get('plan_type')) or 'free'}",
        )
        return data

    def register(self, index: int) -> dict:
        base.step(index, "开始创建邮箱")
        mailbox = base.create_mailbox(register_proxy=self.proxy)
        email = str(mailbox.get("address") or "").strip()
        if not email:
            mail_provider.release_mailbox(mailbox)
            raise RuntimeError("邮箱服务未返回 address")
        label = str(mailbox.get("label") or "")
        base.step(index, f"邮箱创建完成[{label}]: {email}")
        try:
            password = base._random_password()
            first_name, last_name = base._random_name()
            if self.bootstrap_enabled:
                bootstrap.anonymous_bootstrap(self, index=index, strict=self.bootstrap_strict)
            self._chatgpt_authorize(email, index)
            self._register_user(email, password, index)
            self._send_otp(index)
            base.step(index, "开始等待注册验证码")
            code = base.wait_for_code(mailbox, register_proxy=self.proxy)
            if not code:
                raise RuntimeError("等待注册验证码超时")
            base.step(index, f"收到注册验证码: {code}")
            self._validate_otp(code, index)
            self._create_account(f"{first_name} {last_name}", base._random_birthdate(), index)
            self._follow_chatgpt_callback(index)
            session_json = self._fetch_chatgpt_session(index)
            if self.bootstrap_enabled:
                bootstrap.authenticated_bootstrap(
                    self,
                    str(session_json.get("accessToken") or session_json.get("access_token") or "").strip(),
                    index=index,
                    strict=self.bootstrap_strict,
                )
        except Exception as error:
            mail_provider.mark_mailbox_result(mailbox, success=False, error=error)
            raise

        mail_provider.mark_mailbox_result(mailbox, success=True)
        user = session_json.get("user") if isinstance(session_json.get("user"), dict) else {}
        account = session_json.get("account") if isinstance(session_json.get("account"), dict) else {}
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "").strip()
        plan_type = _first_text(account.get("planType"), account.get("plan_type"), account.get("plan"), "free")
        return {
            "email": _first_text(user.get("email"), account.get("email"), email),
            "password": password,
            "access_token": access_token,
            "refresh_token": str(session_json.get("refreshToken") or session_json.get("refresh_token") or "").strip(),
            "id_token": str(session_json.get("idToken") or session_json.get("id_token") or "").strip(),
            "source_type": "chatgpt_web",
            "export_type": "chatgpt_web",
            "account_id": _first_text(account.get("id"), account.get("account_id")),
            "user_id": _first_text(user.get("id"), user.get("user_id")),
            "plan_type": plan_type,
            "type": plan_type,
            "session_expires": str(session_json.get("expires") or ""),
            "chatgpt_session": {
                "expires": str(session_json.get("expires") or ""),
                "user": user,
                "account": account,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "device_id": self.device_id,
            "auth_session_logging_id": self.auth_session_logging_id,
            "fingerprint_profile": self.profile.name,
            **base._new_account_health_metadata(),
        }


def create_chatgpt_web_driver(runtime_config: dict) -> ChatGPTWebRegistrar:
    cfg = runtime_config.get("chatgpt_web") if isinstance(runtime_config.get("chatgpt_web"), dict) else {}
    return ChatGPTWebRegistrar(
        str(runtime_config.get("proxy") or ""),
        bootstrap_enabled=base._truthy(cfg.get("bootstrap_enabled"), True),
        bootstrap_strict=base._truthy(cfg.get("bootstrap_strict"), False),
    )
