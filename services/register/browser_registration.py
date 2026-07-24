from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from services.register import mail_provider
from services.register import openai_register as base
from services.register.browser_automation import CloudBrowserSessionConnector
from services.register.cloak_browser import CloakBrowserClient, CloakBrowserSession
from services.register.cloud_browser import BrowserUseClient, CloudBrowserSession, SkyvernClient
from services.register.account_diagnostics import (
    account_unusable_message,
    detect_account_unusable_payload,
    detect_account_unusable_text,
)
from services.register.humanize import Humanizer, from_runtime_config
from services.register.roxy_browser import RoxyBrowserClient


CHATGPT_LOGIN_URL = "https://chatgpt.com/auth/login"
CHATGPT_SESSION_URL = "https://chatgpt.com/api/auth/session"


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _page_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def _sleep(seconds: float) -> None:
    time.sleep(max(0.05, seconds))


class ChatGPTBrowserRegistrationFlow:
    def __init__(
        self,
        page: Any,
        context: Any,
        *,
        index: int,
        provider_label: str,
        start_url: str = CHATGPT_LOGIN_URL,
        timeout_seconds: int = 90,
        humanizer: Humanizer | None = None,
    ) -> None:
        self.page = page
        self.context = context
        self.index = index
        self.provider_label = provider_label
        self.start_url = str(start_url or CHATGPT_LOGIN_URL).strip()
        self.timeout_ms = max(1000, int(timeout_seconds or 90) * 1000)
        self.humanizer = humanizer or Humanizer({"enabled": False})

    def run(self, *, email: str, password: str, name: str, birthday: str, mailbox: dict) -> dict:
        base.step(self.index, f"{self.provider_label} 打开 ChatGPT 登录页")
        self.page.set_default_timeout(self.timeout_ms)
        self.page.set_default_navigation_timeout(self.timeout_ms)
        self.page.goto(self.start_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self.humanizer.delay("navigate")
        self._maybe_accept_cookies()
        self._submit_email(email)
        self._handle_password_if_present(password)
        base.step(self.index, f"{self.provider_label} 等待邮箱验证码")
        code = base.wait_for_code(mailbox, register_proxy=base.config.get("proxy") or "")
        if not code:
            raise RuntimeError("等待注册验证码超时")
        base.step(self.index, f"{self.provider_label} 收到注册验证码: {code}")
        self.humanizer.delay("otp_input")
        self._submit_otp(code)
        self._complete_profile(name, birthday)
        return self._fetch_chatgpt_session()

    def _locator(self, selectors: list[str], timeout_ms: int = 1200):
        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                if loc.is_visible(timeout=timeout_ms):
                    return loc
            except Exception:
                continue
        return None

    def _fill_first(self, selectors: list[str], value: str, timeout_ms: int = 12000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            loc = self._locator(selectors, timeout_ms=800)
            if loc is not None:
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                    loc.click(timeout=1500)
                    self.humanizer.delay("form", maximum=1.2)
                    loc.fill(value, timeout=4000)
                    return True
                except Exception as exc:
                    last_error = exc
                    try:
                        loc.evaluate(
                            """(el, value) => {
                              const proto = el.tagName === 'TEXTAREA'
                                ? window.HTMLTextAreaElement.prototype
                                : window.HTMLInputElement.prototype;
                              const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                              if (setter) setter.call(el, value); else el.value = value;
                              el.dispatchEvent(new Event('input', {bubbles:true}));
                              el.dispatchEvent(new Event('change', {bubbles:true}));
                            }""",
                            value,
                        )
                        return True
                    except Exception as fallback_error:
                        last_error = fallback_error
            _sleep(0.2)
        if last_error:
            base.log(f"{self.provider_label} 输入失败: {last_error}", "yellow")
        return False

    def _click_first(self, selectors: list[str], timeout_ms: int = 8000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            loc = self._locator(selectors, timeout_ms=800)
            if loc is not None:
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                    loc.click(timeout=2500)
                    self.humanizer.delay("api", maximum=0.8)
                    return True
                except Exception:
                    try:
                        loc.evaluate("el => el.click()")
                        return True
                    except Exception:
                        pass
            _sleep(0.2)
        return False

    def _body_text(self) -> str:
        try:
            return str(self.page.locator("body").inner_text(timeout=1000) or "")
        except Exception:
            return ""

    def _state(self) -> str:
        url = _page_url(self.page).lower()
        body = self._body_text().lower()
        if detect_account_unusable_text(body):
            return "account_unusable"
        if "/log-in/password" in url or ("password" in body and "forgot" in body):
            return "login_password"
        if any(part in url for part in ("/password", "/signup/password", "/create-account/password")):
            return "password"
        if any(part in url for part in ("email-verification", "verify-email")) or "one-time" in body or "verification code" in body:
            return "email_verification"
        if any(part in url for part in ("about-you", "profile", "create-account/about")) or any(text in body for text in ("birthday", "birth date", "age", "name")):
            return "profile"
        if "chatgpt.com" in url and "/auth/" not in url:
            return "chatgpt"
        return "other"

    def _maybe_accept_cookies(self) -> None:
        self._click_first(
            [
                "button:has-text('Accept')",
                "button:has-text('Accept all')",
                "button:has-text('同意')",
                "button:has-text('接受')",
            ],
            timeout_ms=2500,
        )

    def _submit_email(self, email: str) -> None:
        self._click_first(
            [
                "button[data-testid*='email' i]",
                "button[data-provider='email']",
                "button:has-text('Continue with email')",
                "button:has-text('Sign up with email')",
                "button:has-text('Log in with email')",
                "button:has-text('Email')",
                "button:has-text('使用邮箱')",
                "a:has-text('Continue with email')",
            ],
            timeout_ms=4000,
        )
        if not self._fill_first(
            [
                "input[type='email']",
                "input[name='email']",
                "input[name='username']",
                "input[autocomplete='email']",
                "input[autocomplete='username']",
                "input[id*='email' i]",
                "input[placeholder*='email' i]",
            ],
            email,
            timeout_ms=18000,
        ):
            raise RuntimeError("找不到邮箱输入框")
        self._click_or_enter()
        state = self._wait_for_state({"password", "email_verification", "profile", "chatgpt", "login_password", "account_unusable"}, timeout_seconds=24)
        if state == "account_unusable":
            code = detect_account_unusable_text(self._body_text()) or "account_unusable"
            raise RuntimeError(account_unusable_message(code))
        if state == "login_password":
            raise RuntimeError(f"邮箱进入登录密码页，按已注册/不可用处理: url={_page_url(self.page)}")

    def _handle_password_if_present(self, password: str) -> None:
        state = self._state()
        if state != "password":
            return
        if not self._fill_first(
            [
                "input[type='password']",
                "input[name='password']",
                "input[autocomplete='new-password']",
                "input[id*='password' i]",
            ],
            password,
            timeout_ms=12000,
        ):
            raise RuntimeError("找不到密码输入框")
        self._click_or_enter()
        self._wait_for_state({"email_verification", "profile", "chatgpt"}, timeout_seconds=30)

    def _submit_otp(self, code: str) -> None:
        state = self._wait_for_state({"email_verification", "profile", "chatgpt", "account_unusable"}, timeout_seconds=45)
        if state == "account_unusable":
            code = detect_account_unusable_text(self._body_text()) or "account_unusable"
            raise RuntimeError(account_unusable_message(code))
        if state in {"profile", "chatgpt"}:
            return
        self._clear_otp_inputs()
        if not self._fill_first(
            [
                "input[name='code']",
                "input[autocomplete='one-time-code']",
                "input[name='otp']",
                "input[inputmode='numeric']",
                "input[aria-label*='code' i]",
                "input[placeholder*='code' i]",
            ],
            code,
            timeout_ms=8000,
        ):
            boxes = self.page.locator("input[maxlength='1'], input[data-index], input[aria-label*='digit' i]")
            try:
                count = boxes.count()
            except Exception:
                count = 0
            if count < len(code):
                raise RuntimeError("找不到 OTP 输入框")
            for index, char in enumerate(code):
                boxes.nth(index).fill(char)
        self._click_or_enter()
        outcome = self._wait_for_state({"profile", "chatgpt", "email_verification", "account_unusable"}, timeout_seconds=18)
        if outcome == "account_unusable":
            code = detect_account_unusable_text(self._body_text()) or "account_unusable"
            raise RuntimeError(account_unusable_message(code))
        if outcome == "email_verification":
            body = self._body_text().lower()
            if any(text in body for text in ("incorrect", "invalid", "expired", "错误", "过期", "无效")):
                raise RuntimeError("邮箱验证码错误或过期")

    def _clear_otp_inputs(self) -> None:
        try:
            self.page.evaluate(
                """() => {
                  for (const el of document.querySelectorAll('input')) {
                    const t = (el.type || '').toLowerCase();
                    const n = (el.name || '').toLowerCase();
                    const a = (el.autocomplete || '').toLowerCase();
                    if (t === 'tel' || t === 'number' || t === 'text' || n.includes('code') || n.includes('otp') || a.includes('one-time')) {
                      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                      if (setter) setter.call(el, ''); else el.value = '';
                      el.dispatchEvent(new Event('input', {bubbles:true}));
                      el.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                  }
                }"""
            )
        except Exception:
            pass

    def _complete_profile(self, name: str, birthday: str) -> None:
        if self._state() == "chatgpt":
            return
        deadline = time.monotonic() + 60
        submitted = False
        while time.monotonic() < deadline:
            state = self._state()
            if state == "account_unusable":
                code = detect_account_unusable_text(self._body_text()) or "account_unusable"
                raise RuntimeError(account_unusable_message(code))
            if state == "chatgpt":
                return
            if state == "profile":
                self.humanizer.delay("form")
                info = self.page.evaluate(
                    """({name, birthday}) => {
                      const setValue = (el, value) => {
                        if (!el) return false;
                        const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(el, value); else el.value = value;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                        return true;
                      };
                      const [year, month, day] = String(birthday).split('-');
                      let filled = 0;
                      const nameInput = document.querySelector("input[name*='name' i], input[autocomplete='name'], input[placeholder*='name' i]");
                      if (setValue(nameInput, name)) filled++;
                      for (const [needle, value] of [['year', year], ['month', month], ['day', day]]) {
                        const el = document.querySelector(`input[name*='${needle}' i], input[aria-label*='${needle}' i], select[name*='${needle}' i]`);
                        if (el && el.tagName === 'SELECT') {
                          el.value = value;
                          el.dispatchEvent(new Event('change', {bubbles:true}));
                          filled++;
                        } else if (setValue(el, value)) filled++;
                      }
                      const submit = [...document.querySelectorAll('button,input[type=submit]')].find((el) => {
                        const text = ((el.innerText || el.value || '') + '').toLowerCase();
                        return !el.disabled && (el.type === 'submit' || text.includes('continue') || text.includes('submit') || text.includes('next') || text.includes('继续'));
                      });
                      if (submit) submit.click();
                      return {filled, submitted: Boolean(submit)};
                    }""",
                    {"name": name, "birthday": birthday},
                )
                submitted = bool(isinstance(info, dict) and info.get("submitted")) or submitted
            elif submitted:
                _sleep(0.6)
            else:
                _sleep(0.5)
            if submitted:
                _sleep(1.0)
                if self._state() != "profile":
                    return
        if self._state() == "profile":
            raise RuntimeError("资料页提交后仍未跳转")

    def _fetch_chatgpt_session(self, timeout_seconds: int = 90) -> dict:
        self.humanizer.delay("post_auth")
        deadline = time.monotonic() + timeout_seconds
        last: Any = None
        opened_home = False
        while time.monotonic() < deadline:
            data = self._read_session_via_context()
            last = data
            code = detect_account_unusable_payload(data) if isinstance(data, dict) else ""
            if code:
                raise RuntimeError(account_unusable_message(code))
            if isinstance(data, dict) and data.get("accessToken"):
                return data
            if "chatgpt.com" in _page_url(self.page).lower():
                data = self._read_session_via_page()
                last = data
                code = detect_account_unusable_payload(data) if isinstance(data, dict) else ""
                if code:
                    raise RuntimeError(account_unusable_message(code))
                if isinstance(data, dict) and data.get("accessToken"):
                    return data
            elif not opened_home and self._state() != "profile":
                try:
                    self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=self.timeout_ms)
                    opened_home = True
                except Exception as exc:
                    last = f"goto_chatgpt_failed: {exc}"
            _sleep(1.5)
        raise RuntimeError(f"等待 /api/auth/session accessToken 超时，最后响应: {str(last)[:800]}")

    def _read_session_via_context(self) -> dict | None:
        try:
            resp = self.context.request.get(
                CHATGPT_SESSION_URL,
                timeout=min(self.timeout_ms, 9000),
                headers={"accept": "application/json", "referer": "https://chatgpt.com/", "cache-control": "no-cache"},
            )
            try:
                data = resp.json()
            except Exception:
                data = {"status": getattr(resp, "status", None), "text": (resp.text() or "")[:500]}
            if isinstance(data, dict):
                data.setdefault("_http_status", getattr(resp, "status", None))
            return data
        except Exception as exc:
            return {"_error": f"{type(exc).__name__}: {exc}"}

    def _read_session_via_page(self) -> dict | None:
        try:
            return self.page.evaluate(
                """async () => {
                  const r = await fetch('/api/auth/session', {credentials: 'include', cache: 'no-store', headers: {'accept': 'application/json'}});
                  const j = await r.json().catch(async () => ({text: await r.text()}));
                  if (j && typeof j === 'object') j._http_status = r.status;
                  return j;
                }"""
            )
        except Exception as exc:
            return {"_error": f"{type(exc).__name__}: {exc}"}

    def _wait_for_state(self, states: set[str], timeout_seconds: int) -> str:
        deadline = time.monotonic() + timeout_seconds
        last = "other"
        while time.monotonic() < deadline:
            last = self._state()
            if last in states:
                return last
            _sleep(0.4)
        return last

    def _click_or_enter(self) -> None:
        if not self._click_first(
            [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button:has-text('Submit')",
                "button:has-text('Verify')",
                "button:has-text('继续')",
                "form button",
            ],
            timeout_ms=6000,
        ):
            self.page.keyboard.press("Enter")
            self.humanizer.delay("api", maximum=0.8)


class CloudBrowserRegistrationDriver:
    def __init__(
        self,
        runtime_config: dict,
        *,
        provider: str,
        cloud_client_factory: Callable[[dict], Any] | None = None,
        connector_factory: Callable[[], CloudBrowserSessionConnector] | None = None,
        flow_class: type[ChatGPTBrowserRegistrationFlow] = ChatGPTBrowserRegistrationFlow,
    ) -> None:
        self.runtime_config = runtime_config
        self.provider = str(provider or "browser_use").strip().lower()
        self.provider_label = {"skyvern": "Skyvern", "roxy": "RoxyBrowser"}.get(self.provider, "BrowserUse")
        default_client = {"skyvern": SkyvernClient, "roxy": RoxyBrowserClient}.get(self.provider, BrowserUseClient)
        self.cloud_client_factory = cloud_client_factory or default_client
        self.connector_factory = connector_factory or (lambda: CloudBrowserSessionConnector())
        self.flow_class = flow_class
        self.humanizer = Humanizer(from_runtime_config(runtime_config))
        self.cloud_client: Any = None
        self.cloud_session: CloudBrowserSession | None = None
        self.last_cloud_session: CloudBrowserSession | None = None
        self.connected: Any = None

    def register(self, index: int) -> dict:
        base.step(index, f"{self.provider_label} 云浏览器注册启动")
        mailbox = base.create_mailbox(register_proxy=str(self.runtime_config.get("proxy") or ""))
        email = str(mailbox.get("address") or "").strip()
        if not email:
            mail_provider.release_mailbox(mailbox)
            raise RuntimeError("邮箱服务未返回 address")
        base.step(index, f"邮箱创建完成[{mailbox.get('label') or mailbox.get('provider') or 'mail'}]: {email}")
        password = base._random_password()
        first_name, last_name = base._random_name()
        birthday = base._random_birthdate()
        try:
            cfg = self.runtime_config.get(self.provider) if isinstance(self.runtime_config.get(self.provider), dict) else {}
            self.cloud_client = self.cloud_client_factory(cfg)
            if self.provider == "roxy":
                self.cloud_session = self.cloud_client.open_session(str(self.runtime_config.get("proxy") or ""))
            else:
                self.cloud_session = self.cloud_client.open_session()
            self.connected = self.connector_factory().connect(self.cloud_session)
            context = self._context(self.connected.browser)
            page = self._page(context)
            start_url = str(cfg.get("start_url") or CHATGPT_LOGIN_URL)
            timeout_seconds = int(cfg.get("timeout") or cfg.get("navigation_timeout") or 90)
            session_json = self.flow_class(
                page,
                context,
                index=index,
                provider_label=self.provider_label,
                start_url=start_url,
                timeout_seconds=timeout_seconds,
                humanizer=self.humanizer,
            ).run(email=email, password=password, name=f"{first_name} {last_name}", birthday=birthday, mailbox=mailbox)
        except Exception as error:
            mail_provider.mark_mailbox_result(mailbox, success=False, error=error)
            raise
        finally:
            self.close(index)

        mail_provider.mark_mailbox_result(mailbox, success=True)
        return self._account_payload(session_json, email=email, password=password)

    def close(self, index: int | None = None) -> None:
        if self.connected is not None:
            try:
                self.connected.close()
            finally:
                self.connected = None
        if self.cloud_client is not None and self.cloud_session is not None:
            try:
                if self.provider == "skyvern" and self.cloud_session.session_id:
                    self.cloud_client.close_browser_session(self.cloud_session.session_id)
                elif hasattr(self.cloud_client, "close_session"):
                    self.cloud_client.close_session(self.cloud_session)
            except Exception as exc:
                if index is not None:
                    base.step(index, f"{self.provider_label} browser session 关闭失败: {exc}", "yellow")
        self.last_cloud_session = self.cloud_session or self.last_cloud_session
        self.cloud_session = None

    @staticmethod
    def _context(browser: Any) -> Any:
        contexts = list(getattr(browser, "contexts", []) or [])
        return contexts[0] if contexts else browser.new_context()

    @staticmethod
    def _page(context: Any) -> Any:
        pages = list(getattr(context, "pages", []) or [])
        return pages[0] if pages else context.new_page()

    def _account_payload(self, session_json: dict, *, email: str, password: str) -> dict:
        user = session_json.get("user") if isinstance(session_json.get("user"), dict) else {}
        account = session_json.get("account") if isinstance(session_json.get("account"), dict) else {}
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("ChatGPT browser session missing accessToken")
        plan_type = _first_text(account.get("planType"), account.get("plan_type"), account.get("plan"), "free")
        return {
            "email": _first_text(user.get("email"), account.get("email"), email),
            "password": password,
            "access_token": access_token,
            "refresh_token": str(session_json.get("refreshToken") or session_json.get("refresh_token") or "").strip(),
            "id_token": str(session_json.get("idToken") or session_json.get("id_token") or "").strip(),
            "source_type": self.provider,
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
            "fingerprint_profile": "cloud_browser",
            "cloud_browser": {
                "provider": self.provider,
                "session_id": (self.cloud_session or self.last_cloud_session).session_id if (self.cloud_session or self.last_cloud_session) else "",
                "proxy_country_code": (self.cloud_session or self.last_cloud_session).proxy_country_code if (self.cloud_session or self.last_cloud_session) else "",
                "profile_id": (self.cloud_session or self.last_cloud_session).profile_id if (self.cloud_session or self.last_cloud_session) else "",
            },
            **base._new_account_health_metadata(),
        }


def create_browser_use_driver(runtime_config: dict) -> CloudBrowserRegistrationDriver:
    return CloudBrowserRegistrationDriver(runtime_config, provider="browser_use")


def create_skyvern_driver(runtime_config: dict) -> CloudBrowserRegistrationDriver:
    return CloudBrowserRegistrationDriver(runtime_config, provider="skyvern")


def create_roxy_driver(runtime_config: dict) -> CloudBrowserRegistrationDriver:
    return CloudBrowserRegistrationDriver(runtime_config, provider="roxy")


class CloakRegistrationDriver:
    def __init__(
        self,
        runtime_config: dict,
        *,
        client_factory: Callable[[dict], CloakBrowserClient] | None = None,
        flow_class: type[ChatGPTBrowserRegistrationFlow] = ChatGPTBrowserRegistrationFlow,
    ) -> None:
        self.runtime_config = runtime_config
        self.client_factory = client_factory or CloakBrowserClient
        self.flow_class = flow_class
        self.humanizer = Humanizer(from_runtime_config(runtime_config))
        self.client: CloakBrowserClient | None = None
        self.session: CloakBrowserSession | None = None
        self.last_session: CloakBrowserSession | None = None

    def register(self, index: int) -> dict:
        base.step(index, "CloakBrowser 本地浏览器注册启动")
        mailbox = base.create_mailbox(register_proxy=str(self.runtime_config.get("proxy") or ""))
        email = str(mailbox.get("address") or "").strip()
        if not email:
            mail_provider.release_mailbox(mailbox)
            raise RuntimeError("邮箱服务未返回 address")
        base.step(index, f"邮箱创建完成[{mailbox.get('label') or mailbox.get('provider') or 'mail'}]: {email}")
        password = base._random_password()
        first_name, last_name = base._random_name()
        birthday = base._random_birthdate()
        try:
            cfg = self.runtime_config.get("cloak") if isinstance(self.runtime_config.get("cloak"), dict) else {}
            self.client = self.client_factory(cfg)
            self.session = self.client.open_browser(str(self.runtime_config.get("proxy") or ""))
            session_json = self.flow_class(
                self.session.page,
                self.session.context,
                index=index,
                provider_label="CloakBrowser",
                start_url=str(cfg.get("start_url") or CHATGPT_LOGIN_URL),
                timeout_seconds=int(cfg.get("timeout") or 90),
                humanizer=self.humanizer,
            ).run(email=email, password=password, name=f"{first_name} {last_name}", birthday=birthday, mailbox=mailbox)
        except Exception as error:
            mail_provider.mark_mailbox_result(mailbox, success=False, error=error)
            raise
        finally:
            self.close(index)

        mail_provider.mark_mailbox_result(mailbox, success=True)
        return self._account_payload(session_json, email=email, password=password)

    def close(self, index: int | None = None) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception as exc:
                if index is not None:
                    base.step(index, f"CloakBrowser session 关闭失败: {exc}", "yellow")
            finally:
                self.last_session = self.session
                self.session = None

    def _account_payload(self, session_json: dict, *, email: str, password: str) -> dict:
        user = session_json.get("user") if isinstance(session_json.get("user"), dict) else {}
        account = session_json.get("account") if isinstance(session_json.get("account"), dict) else {}
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("CloakBrowser ChatGPT session missing accessToken")
        plan_type = _first_text(account.get("planType"), account.get("plan_type"), account.get("plan"), "free")
        return {
            "email": _first_text(user.get("email"), account.get("email"), email),
            "password": password,
            "access_token": access_token,
            "refresh_token": str(session_json.get("refreshToken") or session_json.get("refresh_token") or "").strip(),
            "id_token": str(session_json.get("idToken") or session_json.get("id_token") or "").strip(),
            "source_type": "cloak",
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
            "fingerprint_profile": "cloakbrowser",
            "cloakbrowser": {
                "profile_id": (self.session or self.last_session).profile_id if (self.session or self.last_session) else "cloakbrowser",
                "raw": (self.session or self.last_session).raw if (self.session or self.last_session) else {},
            },
            **base._new_account_health_metadata(),
        }


def create_cloak_driver(runtime_config: dict) -> CloakRegistrationDriver:
    return CloakRegistrationDriver(runtime_config)
