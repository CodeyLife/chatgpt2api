from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from services.register.browser_automation import CloudBrowserSessionConnector
from services.register.cloak_browser import CloakBrowserClient, CloakBrowserSession
from services.register.cloud_browser import BrowserUseClient, CloudBrowserSession, SkyvernClient
from services.register.roxy_browser import RoxyBrowserClient
from services.register import sms_provider
from services.register import mail_provider
from services.register.account_diagnostics import account_unusable_message, detect_account_unusable_text


CALLBACK_HOSTS = {"localhost", "127.0.0.1"}
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
CODEX_CONSENT_SELECTORS = [
    "button:has-text('Select')",
    "button:has-text('Use workspace')",
    "button:has-text('Confirm')",
    "button:has-text('Authorize')",
    "button:has-text('Allow')",
    "button:has-text('Continue')",
    "button:has-text('Choose')",
    "button:has-text('選択')",
    "button:has-text('許可')",
    "button:has-text('続行')",
    "button:has-text('选择')",
    "button:has-text('允许')",
    "button:has-text('授权')",
    "button:has-text('继续')",
    "button:has-text('确认')",
    "button[type='submit']",
    "input[type='submit']",
    "[role='button']:has-text('Authorize')",
    "[role='button']:has-text('Continue')",
]
PHONE_INPUT_SELECTORS = [
    "input[type='tel']",
    "input[name*='phone' i]",
    "input[autocomplete='tel']",
    "input[aria-label*='phone' i]",
    "input[placeholder*='phone' i]",
    "input[aria-label*='電話']",
    "input[placeholder*='電話']",
    "input[aria-label*='手机号']",
    "input[placeholder*='手机号']",
]
OTP_INPUT_SELECTORS = [
    "input[autocomplete='one-time-code']",
    "input[name='code']",
    "input[name='otp']",
    "input[inputmode='numeric']",
    "input[aria-label*='code' i]",
    "input[placeholder*='code' i]",
    "input[aria-label*='验证码']",
    "input[placeholder*='验证码']",
]
PHONE_CONTINUE_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Continue')",
    "button:has-text('Send')",
    "button:has-text('Next')",
    "button:has-text('Verify')",
    "button:has-text('続行')",
    "button:has-text('送信')",
    "button:has-text('次へ')",
    "button:has-text('確認')",
    "button:has-text('继续')",
    "button:has-text('发送')",
    "button:has-text('下一步')",
    "button:has-text('验证')",
    "form button",
]
EMAIL_INPUT_SELECTORS = [
    "input[type='email']",
    "input[name='email']",
    "input[name*='email' i]",
    "input[autocomplete='email']",
    "input[aria-label*='email' i]",
    "input[placeholder*='email' i]",
    "input[aria-label*='邮箱']",
    "input[placeholder*='邮箱']",
]
EMAIL_CONTINUE_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Continue')",
    "button:has-text('Next')",
    "button:has-text('Submit')",
    "button:has-text('继续')",
    "button:has-text('下一步')",
    "button:has-text('提交')",
    "form button",
]


class CodexOAuthBrowserError(RuntimeError):
    pass


def is_codex_callback_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except Exception:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in CALLBACK_HOSTS
        and parsed.port == CALLBACK_PORT
        and parsed.path == CALLBACK_PATH
        and bool(parsed.query)
    )


def _page_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def _all_frames(page: Any) -> list[Any]:
    frames = [page]
    try:
        frames.extend([item for item in list(page.frames or []) if item not in frames])
    except Exception:
        pass
    return frames


def _visible_locator_any_frame(page: Any, selectors: list[str], timeout_ms: int = 700) -> Any:
    for frame in _all_frames(page):
        for selector in selectors:
            try:
                loc = frame.locator(selector).first
                if loc.count() == 0 or not loc.is_visible(timeout=timeout_ms):
                    continue
                return loc
            except Exception:
                continue
    return None


def _click_first_any_frame(page: Any, selectors: list[str], timeout_ms: int = 2500) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        loc = _visible_locator_any_frame(page, selectors, timeout_ms=500)
        if loc is not None:
            try:
                loc.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                loc.click(timeout=2000)
                return True
            except Exception:
                try:
                    loc.evaluate("el => el.click()")
                    return True
                except Exception:
                    pass
        time.sleep(0.2)
    return False


def _click_codex_consent_fast(page: Any) -> bool:
    script = r"""
    () => {
      const visible = (el) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s && s.visibility !== 'hidden' && s.display !== 'none' && r.width > 5 && r.height > 5;
      };
      const nodes = [...document.querySelectorAll('button,input[type=submit],a,[role=button]')].filter(visible);
      const score = (el) => {
        const text = [el.innerText, el.textContent, el.value, el.getAttribute('aria-label')].join(' ').toLowerCase();
        if (/(authorize|allow|continue|confirm|select|use workspace|choose)/i.test(text)) return 100;
        if (/(授权|允许|继续|确认|选择|許可|続行|選択)/i.test(text)) return 100;
        if ((el.type || '').toLowerCase() === 'submit') return 80;
        return 0;
      };
      const target = nodes.map(el => [score(el), el]).filter(x => x[0] > 0).sort((a,b) => b[0] - a[0])[0]?.[1];
      if (!target) return false;
      target.scrollIntoView({block:'center'});
      target.click();
      return true;
    }
    """
    for frame in _all_frames(page):
        try:
            result = frame.evaluate(script)
            if result is True:
                return True
        except Exception:
            continue
    return False


def _body_text(page: Any, limit: int = 1200) -> str:
    chunks: list[str] = []
    for frame in _all_frames(page):
        try:
            text = str(frame.locator("body").inner_text(timeout=700) or "")
            text = " ".join(text.split())
            if text:
                chunks.append(text)
        except Exception:
            continue
    return " | ".join(chunks)[:limit]


def _phone_digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_e164(value: object) -> str:
    digits = _phone_digits(value)
    return f"+{digits}" if digits else ""


def _has_phone_prompt(page: Any) -> bool:
    url = _page_url(page).lower()
    if any(part in url for part in ("phone", "add-phone", "phone-verification", "sms")):
        return True
    body = _body_text(page, 1000).lower()
    return any(part in body for part in ("phone number", "verify your phone", "text message", "sms", "手机号", "电话号码", "短信", "電話", "携帯"))


def _has_phone_code_input(page: Any) -> bool:
    if _visible_locator_any_frame(page, OTP_INPUT_SELECTORS, timeout_ms=500) is None:
        return False
    url = _page_url(page).lower()
    body = _body_text(page, 1000).lower()
    return any(part in url for part in ("phone", "sms", "verification")) or any(part in body for part in ("phone", "sms", "text message", "手机", "短信", "電話", "携帯"))


def _has_email_prompt(page: Any) -> bool:
    if _visible_locator_any_frame(page, EMAIL_INPUT_SELECTORS, timeout_ms=500) is not None:
        return True
    body = _body_text(page, 1000).lower()
    return any(part in body for part in ("email address", "enter your email", "邮箱", "电子邮件"))


def _has_email_code_input(page: Any) -> bool:
    if _visible_locator_any_frame(page, OTP_INPUT_SELECTORS, timeout_ms=500) is None:
        return False
    body = _body_text(page, 1000).lower()
    return any(part in body for part in ("email", "verification code", "one-time code", "邮箱", "验证码", "認証コード"))


def _read_input_value(page: Any, selectors: list[str]) -> str:
    loc = _visible_locator_any_frame(page, selectors, timeout_ms=500)
    if loc is None:
        return ""
    try:
        return str(loc.input_value(timeout=800) or "")
    except Exception:
        try:
            return str(loc.evaluate("el => el.value || ''") or "")
        except Exception:
            return ""


def _set_input_value(page: Any, selectors: list[str], value: str) -> bool:
    script = r"""
    ({value, kind}) => {
      const visible = (el) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s && s.visibility !== 'hidden' && s.display !== 'none' && r.width > 5 && r.height > 5;
      };
      const inputs = [...document.querySelectorAll('input')].filter(visible);
      const score = (el) => {
        const hay = [el.type, el.name, el.id, el.autocomplete, el.placeholder, el.getAttribute('aria-label')].join(' ').toLowerCase();
        if (kind === 'email' && /(email|邮箱|電子メール)/i.test(hay)) return 100;
        if (kind === 'phone' && /(phone|tel|mobile|sms|手机号|手机|電話|携帯)/i.test(hay)) return 100;
        if (kind === 'otp' && /(code|otp|one-time|verification|验证码|認証|確認|sms)/i.test(hay)) return 100;
        if (kind === 'email' && (el.type || '').toLowerCase() === 'email') return 90;
        if (kind === 'phone' && (el.type || '').toLowerCase() === 'tel') return 90;
        if (kind === 'otp' && /(tel|text|number)/i.test((el.type || '').toLowerCase())) return 20;
        return 0;
      };
      const target = inputs.map(el => [score(el), el]).filter(x => x[0] > 0).sort((a,b) => b[0]-a[0])[0]?.[1];
      if (!target) return false;
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
      target.focus();
      if (setter) setter.call(target, value); else target.value = value;
      target.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data: value}));
      target.dispatchEvent(new Event('change', {bubbles:true}));
      target.dispatchEvent(new Event('blur', {bubbles:true}));
      return true;
    }
    """
    kind = "phone" if selectors is PHONE_INPUT_SELECTORS else "email" if selectors is EMAIL_INPUT_SELECTORS else "otp"
    for frame in _all_frames(page):
        try:
            if frame.evaluate(script, {"value": value, "kind": kind}):
                return True
        except Exception:
            continue
    loc = _visible_locator_any_frame(page, selectors, timeout_ms=700)
    if loc is None:
        return False
    try:
        loc.scroll_into_view_if_needed(timeout=1000)
    except Exception:
        pass
    try:
        loc.fill(value, timeout=2000)
        return True
    except Exception:
        try:
            loc.evaluate(
                """(el, value) => {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                  if (setter) setter.call(el, value); else el.value = value;
                  el.dispatchEvent(new Event('input', {bubbles:true}));
                  el.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                value,
            )
            return True
        except Exception:
            return False


def _clear_inputs(page: Any, kind: str) -> None:
    script = r"""
    (kind) => {
      const inputs = [...document.querySelectorAll('input')];
      for (const el of inputs) {
        const hay = [el.type, el.name, el.id, el.autocomplete, el.placeholder, el.getAttribute('aria-label')].join(' ').toLowerCase();
        const match = kind === 'phone'
          ? /(phone|tel|mobile|sms|手机号|手机|電話|携帯)/i.test(hay) || (el.type || '').toLowerCase() === 'tel'
          : kind === 'email'
          ? /(email|邮箱|電子メール)/i.test(hay) || (el.type || '').toLowerCase() === 'email'
          : /(code|otp|one-time|verification|验证码|認証|確認|sms)/i.test(hay);
        if (!match) continue;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        if (setter) setter.call(el, ''); else el.value = '';
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
      }
    }
    """
    for frame in _all_frames(page):
        try:
            frame.evaluate(script, kind)
        except Exception:
            continue


def _click_phone_continue(page: Any) -> bool:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    script = r"""
    () => {
      const visible = (el) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s && s.visibility !== 'hidden' && s.display !== 'none' && r.width > 5 && r.height > 5;
      };
      const buttons = [...document.querySelectorAll('button,input[type=submit],[role=button]')].filter(visible);
      const score = (el) => {
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') return -1;
        const text = [el.innerText, el.textContent, el.value, el.getAttribute('aria-label')].join(' ').toLowerCase();
        if (/(continue|send|next|verify|submit|続行|送信|次へ|確認|继续|发送|下一步|验证)/i.test(text)) return 100;
        if ((el.type || '').toLowerCase() === 'submit') return 90;
        return 0;
      };
      const target = buttons.map(el => [score(el), el]).filter(x => x[0] > 0).sort((a,b) => b[0]-a[0])[0]?.[1];
      if (target) { target.scrollIntoView({block:'center'}); target.click(); return true; }
      const form = document.querySelector('form');
      if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return true; }
      return false;
    }
    """
    for frame in _all_frames(page):
        try:
            if frame.evaluate(script):
                return True
        except Exception:
            continue
    return _click_first_any_frame(page, PHONE_CONTINUE_SELECTORS, timeout_ms=2500)


def _click_email_continue(page: Any) -> bool:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return _click_first_any_frame(page, EMAIL_CONTINUE_SELECTORS, timeout_ms=2500)


def _wait_for_email_code_page(page: Any, timeout: int, sleep_func: Callable[[float], None]) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        callback = extract_callback_url_from_page(page)
        if callback:
            return "callback"
        if _has_email_code_input(page):
            return "code_page"
        body = _body_text(page, 900).lower()
        if any(word in body for word in ("invalid email", "account not found", "too many", "邮箱无效", "账户不存在")):
            return "rejected"
        last = f"url={_page_url(page)} body={body[:240]}"
        sleep_func(0.5)
    return f"unknown:{last[:260]}"


def _wait_for_phone_code_page(page: Any, timeout: int, sleep_func: Callable[[float], None]) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        callback = extract_callback_url_from_page(page)
        if callback:
            return "callback"
        if _has_phone_code_input(page):
            return "code_page"
        body = _body_text(page, 900).lower()
        if any(word in body for word in ("invalid phone", "not valid", "unsupported", "too many", "号码无效", "手机号无效", "無効")):
            return "rejected"
        last = f"url={_page_url(page)} body={body[:240]}"
        sleep_func(0.5)
    if _read_input_value(page, PHONE_INPUT_SELECTORS):
        return "still_form"
    return f"unknown:{last[:260]}"


def _wait_after_phone_otp(page: Any, timeout: int, sleep_func: Callable[[float], None]) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        callback = extract_callback_url_from_page(page)
        if callback:
            return "callback"
        url = _page_url(page).lower()
        if not any(part in url for part in ("phone", "otp", "verification", "sms")) and not _has_phone_code_input(page):
            return "accepted"
        body = _body_text(page, 900).lower()
        if any(word in body for word in ("incorrect", "invalid", "expired", "错误", "过期", "无效")):
            return "invalid"
        sleep_func(0.5)
    return "unknown"


def extract_callback_url_from_page(page: Any) -> str:
    current = _page_url(page)
    if is_codex_callback_url(current):
        return current
    try:
        urls = page.evaluate(
            """() => {
              const out = [];
              const push = v => { if (v && typeof v === 'string') out.push(v); };
              try { push(location.href); } catch (e) {}
              try { push(document.URL); } catch (e) {}
              try { push(document.documentURI); } catch (e) {}
              try { for (const e of performance.getEntriesByType('navigation')) push(e.name); } catch (e) {}
              try { for (const e of performance.getEntries()) push(e.name); } catch (e) {}
              return [...new Set(out)];
            }"""
        ) or []
    except Exception:
        urls = []
    for url in urls:
        text = str(url or "")
        if is_codex_callback_url(text):
            return text
    return ""


def extract_callback_url_from_context(context: Any, page: Any = None) -> str:
    pages = []
    if page is not None:
        pages.append(page)
    try:
        pages.extend([item for item in list(context.pages or []) if item not in pages])
    except Exception:
        pass
    for candidate in pages:
        callback = extract_callback_url_from_page(candidate)
        if callback:
            return callback
    return ""


def _context(browser: Any) -> Any:
    contexts = list(getattr(browser, "contexts", []) or [])
    return contexts[0] if contexts else browser.new_context()


def _page(context: Any) -> Any:
    pages = list(getattr(context, "pages", []) or [])
    return pages[0] if pages else context.new_page()


class CodexOAuthBrowserRunner:
    def __init__(
        self,
        runtime_config: dict | None = None,
        *,
        browser_use_factory: Callable[[dict], BrowserUseClient] = BrowserUseClient,
        skyvern_factory: Callable[[dict], SkyvernClient] = SkyvernClient,
        roxy_factory: Callable[[dict], RoxyBrowserClient] = RoxyBrowserClient,
        cloak_factory: Callable[[dict], CloakBrowserClient] = CloakBrowserClient,
        connector_factory: Callable[[], CloudBrowserSessionConnector] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runtime_config = runtime_config if isinstance(runtime_config, dict) else {}
        self.browser_use_factory = browser_use_factory
        self.skyvern_factory = skyvern_factory
        self.roxy_factory = roxy_factory
        self.cloak_factory = cloak_factory
        self.connector_factory = connector_factory or (lambda: CloudBrowserSessionConnector())
        self._sleep = sleep_func

    def run(
        self,
        *,
        provider: str,
        auth_url: str,
        email: str = "",
        proxy: str = "",
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        provider_name = str(provider or "").strip().lower().replace("-", "_")
        if provider_name == "browseruse":
            provider_name = "browser_use"
        if provider_name not in {"browser_use", "skyvern", "roxy", "cloak"}:
            raise CodexOAuthBrowserError(f"unsupported codex oauth browser provider: {provider or '-'}")
        url = str(auth_url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise CodexOAuthBrowserError("auth_url is required")
        timeout = max(5, int(timeout_seconds or self._provider_timeout(provider_name) or 180))

        opened = self._open(provider_name, proxy)
        try:
            page = opened["page"]
            context = opened["context"]
            try:
                page.set_default_timeout(timeout * 1000)
                page.set_default_navigation_timeout(timeout * 1000)
            except Exception:
                pass
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            callback = self._wait_for_callback(context, page, timeout, email=email, proxy=proxy)
            return {
                "ok": True,
                "provider": provider_name,
                "callback_url": callback,
                "browser": opened.get("metadata") or {},
            }
        finally:
            self._close(provider_name, opened)

    def _provider_timeout(self, provider: str) -> int:
        cfg = self._provider_config(provider)
        return int(cfg.get("codex_oauth_timeout") or cfg.get("timeout") or 180)

    def _provider_config(self, provider: str) -> dict[str, Any]:
        cfg = self.runtime_config.get(provider)
        return cfg if isinstance(cfg, dict) else {}

    def _sms_config(self) -> dict[str, Any]:
        cfg = self.runtime_config.get("sms")
        if not isinstance(cfg, dict):
            return {}
        return cfg if bool(cfg.get("enabled", False)) else {}

    def _open(self, provider: str, proxy: str) -> dict[str, Any]:
        cfg = self._provider_config(provider)
        if provider == "cloak":
            client = self.cloak_factory(cfg)
            session = client.open_browser(proxy)
            return {
                "client": client,
                "session": session,
                "context": session.context,
                "page": session.page,
                "metadata": {"provider": provider, "profile_id": session.profile_id, "raw": session.raw},
            }

        client: Any
        session: CloudBrowserSession
        if provider == "skyvern":
            client = self.skyvern_factory(cfg)
            session = client.open_session()
        elif provider == "roxy":
            client = self.roxy_factory(cfg)
            session = client.open_session(proxy)
        else:
            client = self.browser_use_factory(cfg)
            session = client.open_session()
        connected = self.connector_factory().connect(session)
        context = _context(connected.browser)
        page = _page(context)
        return {
            "client": client,
            "session": session,
            "connected": connected,
            "context": context,
            "page": page,
            "metadata": {
                "provider": provider,
                "session_id": session.session_id,
                "profile_id": session.profile_id,
                "proxy_country_code": session.proxy_country_code,
            },
        }

    def _close(self, provider: str, opened: dict[str, Any]) -> None:
        connected = opened.get("connected")
        if connected is not None:
            try:
                connected.close()
            except Exception:
                pass
        session = opened.get("session")
        client = opened.get("client")
        if provider == "cloak" and isinstance(session, CloakBrowserSession):
            try:
                session.close()
            except Exception:
                pass
            return
        if client is not None and session is not None:
            try:
                if provider == "skyvern" and getattr(session, "session_id", ""):
                    client.close_browser_session(session.session_id)
                elif hasattr(client, "close_session"):
                    client.close_session(session)
            except Exception:
                pass

    def _wait_for_callback(self, context: Any, page: Any, timeout: int, *, email: str = "", proxy: str = "") -> str:
        deadline = time.monotonic() + timeout
        last_url = ""
        last_click_at = 0.0
        handled_phone = False
        handled_email = False
        while time.monotonic() < deadline:
            callback = extract_callback_url_from_context(context, page)
            if callback:
                return callback
            current = _page_url(page)
            if current != last_url:
                last_url = current
            unusable_code = detect_account_unusable_text(_body_text(page, 1000))
            if unusable_code:
                raise CodexOAuthBrowserError(account_unusable_message(unusable_code))
            if not handled_email and str(email or "").strip() and _has_email_prompt(page):
                self._handle_email_verification(page, str(email or "").strip(), proxy)
                handled_email = True
                continue
            sms_cfg = self._sms_config()
            if not handled_phone and sms_cfg and _has_phone_prompt(page):
                self._handle_phone_verification(page, sms_cfg)
                handled_phone = True
                continue
            now = time.monotonic()
            if now - last_click_at >= 0.9 and self._maybe_click_authorize(page):
                last_click_at = now
            self._sleep(0.35)
        raise CodexOAuthBrowserError(f"waiting for Codex OAuth callback timed out, last_url={last_url or '-'}")

    @staticmethod
    def _maybe_click_authorize(page: Any) -> bool:
        return _click_codex_consent_fast(page) or _click_first_any_frame(page, CODEX_CONSENT_SELECTORS, timeout_ms=2500)

    def _handle_phone_verification(self, page: Any, sms_cfg: dict[str, Any]) -> None:
        if not _has_phone_prompt(page):
            return
        max_retries = max(1, int(sms_cfg.get("max_retries") or sms_cfg.get("sms_max_retries") or 3))
        last_error = ""
        for attempt in range(1, max_retries + 1):
            activation_id = ""
            try:
                activation = sms_provider.acquire_number(sms_cfg)
                activation_id = activation.activation_id
                phone_e164 = _phone_e164(activation.phone)
                if not phone_e164:
                    raise CodexOAuthBrowserError("SMS provider returned empty phone")
                _clear_inputs(page, "phone")
                if not _set_input_value(page, PHONE_INPUT_SELECTORS, phone_e164):
                    raise CodexOAuthBrowserError("phone input not found")
                actual = _read_input_value(page, PHONE_INPUT_SELECTORS)
                if _phone_digits(actual) != _phone_digits(phone_e164):
                    _set_input_value(page, PHONE_INPUT_SELECTORS, phone_e164)
                    actual = _read_input_value(page, PHONE_INPUT_SELECTORS)
                if _phone_digits(actual) != _phone_digits(phone_e164):
                    raise CodexOAuthBrowserError("phone input value verification failed")
                if not _click_phone_continue(page):
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                send_state = _wait_for_phone_code_page(page, 18, self._sleep)
                if send_state == "callback":
                    sms_provider.complete(activation_id, sms_cfg)
                    return
                if send_state != "code_page":
                    raise CodexOAuthBrowserError(f"phone submit did not reach SMS code page: {send_state}")
                try:
                    sms_provider.set_status(activation_id, 1, sms_cfg)
                except Exception:
                    pass
                sms_code = sms_provider.wait_for_sms_code(activation_id, sms_cfg, sleep_func=self._sleep)
                _clear_inputs(page, "otp")
                if not _set_input_value(page, OTP_INPUT_SELECTORS, str(sms_code)):
                    raise CodexOAuthBrowserError("SMS code input not found")
                _click_phone_continue(page)
                outcome = _wait_after_phone_otp(page, 25, self._sleep)
                if outcome in {"accepted", "callback", "unknown"}:
                    sms_provider.complete(activation_id, sms_cfg)
                    return
                raise CodexOAuthBrowserError(f"SMS code rejected: {outcome}")
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:220]}"
                if activation_id:
                    try:
                        sms_provider.cancel(activation_id, sms_cfg)
                    except Exception:
                        pass
                if attempt >= max_retries:
                    break
                self._sleep(min(1.0 + attempt, 4.0))
        raise CodexOAuthBrowserError(f"phone verification failed after {max_retries} attempts: {last_error}")

    def _handle_email_verification(self, page: Any, email: str, proxy: str = "") -> None:
        email = str(email or "").strip()
        if not email:
            return
        try:
            from services.register import openai_register

            mail_config = openai_register._mail_config(proxy)  # noqa: SLF001 - shared registration mailbox config
            mailbox = mail_provider.get_existing_mailbox(mail_config, email)
            mailbox["_code_not_before"] = datetime.now(timezone.utc)
        except Exception as exc:
            raise CodexOAuthBrowserError(f"无法获取账号邮箱用于 Codex OAuth 验证: {type(exc).__name__}: {str(exc)[:220]}") from exc

        _clear_inputs(page, "email")
        if not _set_input_value(page, EMAIL_INPUT_SELECTORS, email):
            raise CodexOAuthBrowserError("email input not found")
        actual = _read_input_value(page, EMAIL_INPUT_SELECTORS)
        if actual.strip().lower() != email.lower():
            _set_input_value(page, EMAIL_INPUT_SELECTORS, email)
            actual = _read_input_value(page, EMAIL_INPUT_SELECTORS)
        if actual.strip().lower() != email.lower():
            raise CodexOAuthBrowserError("email input value verification failed")
        if not _click_email_continue(page):
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        send_state = _wait_for_email_code_page(page, 20, self._sleep)
        if send_state == "callback":
            return
        if send_state != "code_page":
            raise CodexOAuthBrowserError(f"email submit did not reach code page: {send_state}")
        code = mail_provider.wait_for_code(mail_config, mailbox)
        if not code:
            raise CodexOAuthBrowserError("等待 Codex OAuth 邮箱验证码超时")
        _clear_inputs(page, "otp")
        if not _set_input_value(page, OTP_INPUT_SELECTORS, str(code)):
            raise CodexOAuthBrowserError("email code input not found")
        if not _click_email_continue(page):
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass


codex_oauth_browser_runner = CodexOAuthBrowserRunner()
