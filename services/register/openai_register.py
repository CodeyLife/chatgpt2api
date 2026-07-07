from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import secrets
import string
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import requests

from services.account_service import account_service
from services.proxy_service import ClearanceBundle, proxy_settings
from services.register import mail_provider
from utils.fingerprint import BrowserProfile, DEFAULT_PROFILE, build_common_headers, build_navigate_headers, random_profile

base_dir = Path(__file__).resolve().parent
config = {
    "mail": {
        "request_timeout": 30,
        "wait_timeout": 30,
        "wait_interval": 2,
        "api_use_register_proxy": True,
        "providers": [],
    },
    "proxy": "",
    "total": 10,
    "threads": 3,
}
register_config_file = base_dir.parents[1] / "data" / "register.json"
register_failure_dir = register_config_file.parent / "register_failures"
try:
    saved_config = json.loads(register_config_file.read_text(encoding="utf-8"))
    config.update({key: saved_config[key] for key in ("mail", "proxy", "total", "threads") if key in saved_config})
except Exception:
    pass

auth_base = "https://auth.openai.com"
platform_base = "https://platform.openai.com"
platform_oauth_client_id = "app_2SKx67EdpoN0G6j64rFvigXD"
platform_oauth_redirect_uri = f"{platform_base}/auth/callback"
platform_oauth_audience = "https://api.openai.com/v1"
platform_auth0_client = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
sec_ch_ua = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
sec_ch_ua_full_version_list = '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.0.0"'
default_timeout = 30
print_lock = threading.Lock()
stats_lock = threading.Lock()
stats = {"done": 0, "success": 0, "fail": 0, "start_time": 0.0}
register_log_sink = None


class RegistrationStepError(RuntimeError):
    """注册流程单步失败，携带本地抓包诊断目录。"""

    def __init__(self, message: str, *, artifact_path: str = "", diagnosis: str = "") -> None:
        super().__init__(message)
        self.artifact_path = artifact_path
        self.diagnosis = diagnosis

common_headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "content-type": "application/json",
    "dnt": "1",
    "origin": auth_base,
    "priority": "u=1, i",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": user_agent,
}

navigate_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "connection": "keep-alive",
    "dnt": "1",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": user_agent,
}


def log(text: str, color: str = "") -> None:
    colors = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m"}
    if register_log_sink:
        try:
            register_log_sink(text, color)
        except Exception:
            pass
    with print_lock:
        prefix = colors.get(color, "")
        suffix = "\033[0m" if prefix else ""
        print(f"{prefix}{datetime.now().strftime('%H:%M:%S')} {text}{suffix}")


def step(index: int, text: str, color: str = "") -> None:
    log(f"[任务{index}] {text}", color)


def _make_trace_headers() -> dict[str, str]:
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    return {
        "traceparent": f"00-{uuid.uuid4().hex}-{format(int(parent_id), '016x')}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


from utils.pkce import generate_pkce as _generate_pkce  # noqa: F401


def _random_password(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    value = list(
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%")
        + "".join(secrets.choice(chars) for _ in range(max(0, length - 4)))
    )
    random.shuffle(value)
    return "".join(value)


def _random_name() -> tuple[str, str]:
    first_names = [
        "James", "Robert", "John", "Michael", "David", "Mary", "Emma", "Olivia",
        "William", "Richard", "Joseph", "Thomas", "Charles", "Christopher", "Daniel",
        "Matthew", "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua",
        "Kenneth", "Kevin", "Brian", "George", "Edward", "Ronald", "Timothy",
        "Sarah", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica",
        "Margaret", "Lisa", "Nancy", "Karen", "Betty", "Helen", "Sandra", "Donna",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson",
        "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez",
        "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
        "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen",
        "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera",
    ]
    return random.choice(first_names), random.choice(last_names)


def _random_birthdate() -> str:
    return f"{random.randint(1996, 2006):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"


def _response_json(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _response_debug_detail(resp, limit: int = 800) -> str:
    if resp is None:
        return ""
    data = _response_json(resp)
    parts = [
        f"url={str(getattr(resp, 'url', '') or '')[:300]}",
        f"content_type={str(getattr(resp, 'headers', {}).get('content-type') or '')}",
    ]
    for key in ("cf-ray", "x-request-id", "openai-processing-ms"):
        value = str(getattr(resp, "headers", {}).get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    if data:
        parts.append(f"json={json.dumps(data, ensure_ascii=False)[:limit]}")
    else:
        parts.append(f"body={str(getattr(resp, 'text', '') or '')[:limit]}")
    return ", ".join(parts)


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "refresh_token",
    "access_token",
    "id_token",
    "client_secret",
    "openai-sentinel-token",
    "openai-sentinel-so-token",
    "cf_clearance",
    "jwt",
    "token",
    "code",
}


def _redact_obj(value: Any, key_hint: str = "") -> Any:
    key = str(key_hint or "").lower()
    if key in _SENSITIVE_KEYS or any(part in key for part in ("password", "token", "secret", "authorization", "cookie")):
        return "***redacted***" if value not in ("", None) else value
    if isinstance(value, dict):
        return {str(k): _redact_obj(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_obj(item, key_hint) for item in value]
    return value


def _redact_text(text: str) -> str:
    redacted = str(text or "")
    # JSON / form 风格的敏感字段
    redacted = re.sub(
        r'(?i)("?(?:access_token|refresh_token|id_token|password|client_secret|openai-sentinel-token|openai-sentinel-so-token|authorization|cookie)"?\s*[:=]\s*)"[^"\n\r&]{6,}"',
        r'\1"***redacted***"',
        redacted,
    )
    # 常见 Bearer / Cookie 片段
    redacted = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***redacted***", redacted)
    redacted = re.sub(r"(?i)((?:cf_clearance|__Secure-[^=;]+|oai-[^=;]+)=)[^;\s]{8,}", r"\1***redacted***", redacted)
    return redacted


def _safe_artifact_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("._")[:80] or "unknown"


def _extract_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", str(text or ""), re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _classify_failure(step_name: str, resp, error: str = "") -> str:
    if resp is None:
        return f"网络请求失败或本地代理异常：{error}" if error else "网络请求失败或本地代理异常"
    status_code = int(getattr(resp, "status_code", 0) or 0)
    data = _response_json(resp)
    text = str(getattr(resp, "text", "") or "")
    lowered = text.lower()
    if _is_cloudflare_challenge(resp):
        return "Cloudflare/风控挑战未通过：需要检查出口 IP、FlareSolverr clearance、代理一致性"
    err = data.get("error") if isinstance(data, dict) else None
    code = ""
    message = ""
    if isinstance(err, dict):
        code = str(err.get("code") or err.get("error") or "").strip()
        message = str(err.get("message") or "").strip()
    elif isinstance(err, str):
        code = err
    message = message or str(data.get("message") or data.get("detail") or "").strip()
    joined = f"{code} {message} {text[:500]}".lower()
    if "invalid_auth_step" in joined:
        return "注册步骤不匹配：authorize 可能落入登录分支、会话状态失效，或 screen_hint/login_hint 未被上游接受"
    if "failed to create account" in joined:
        return "上游拒绝创建账号：常见原因是邮箱域名信誉差、IP/代理风险高或该邮箱已被上游限制"
    if "sentinel" in joined or "proof" in joined or "arkose" in joined:
        return "风控校验失败：sentinel/proof 参数或浏览器指纹可能已过期，需要重新抓取网页参数"
    if "rate" in joined or status_code == 429:
        return "请求被限流：降低线程数/注册频率，或更换出口 IP 后重试"
    if status_code in (401, 403):
        return "请求被拒绝：检查代理出口、clearance/cookie、UA 指纹和会话状态"
    content_type = str(getattr(resp, "headers", {}).get("content-type") or "").lower()
    if "html" in content_type or "<html" in lowered:
        title = _extract_title(text)
        return f"接口返回 HTML 页面而非 JSON：{title or '可能是登录页/拦截页/上游页面结构变化'}"
    if status_code >= 500:
        return "上游服务端异常或代理网关异常，可稍后重试并对照本地抓包目录"
    return f"{step_name} 返回 HTTP {status_code}，需要查看本地抓包目录中的 metadata.json 和 response_body.*"


def _dump_failure_artifact(
    index: int,
    step_name: str,
    method: str,
    url: str,
    resp=None,
    error: str = "",
    request_headers: dict[str, Any] | None = None,
    request_body: Any = None,
) -> str:
    """把失败请求的响应与诊断信息保存到 data/register_failures，便于后续网页逆向。"""
    try:
        status = str(getattr(resp, "status_code", "no_response") if resp is not None else "no_response")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        artifact_dir = register_failure_dir / f"{stamp}_task{index}_{_safe_artifact_name(step_name)}_{_safe_artifact_name(status)}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        response_headers = dict(getattr(resp, "headers", {}) or {}) if resp is not None else {}
        body = str(getattr(resp, "text", "") or "") if resp is not None else ""
        content_type = str(response_headers.get("content-type") or "").lower()
        body_name = "response_body.html" if "html" in content_type or "<html" in body.lower() else "response_body.json" if "json" in content_type else "response_body.txt"
        (artifact_dir / body_name).write_text(_redact_text(body[:200_000]), encoding="utf-8", errors="replace")
        metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_index": index,
            "step": step_name,
            "method": method.upper(),
            "url": url,
            "status_code": getattr(resp, "status_code", None) if resp is not None else None,
            "final_url": str(getattr(resp, "url", "") or "") if resp is not None else "",
            "error": str(error or ""),
            "diagnosis": _classify_failure(step_name, resp, error),
            "request_headers": _redact_obj(request_headers or {}),
            "request_body": _redact_obj(request_body),
            "response_headers": _redact_obj(response_headers),
            "response_debug": _redact_text(_response_debug_detail(resp, limit=2000)),
            "response_body_file": body_name,
        }
        (artifact_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(artifact_dir)
    except Exception as dump_error:
        log(f"写入注册失败抓包目录失败: {dump_error}", "yellow")
        return ""


def _raise_step_failure(
    index: int,
    step_name: str,
    method: str,
    url: str,
    resp=None,
    error: str = "",
    request_headers: dict[str, Any] | None = None,
    request_body: Any = None,
    prefix: str = "",
) -> None:
    artifact = _dump_failure_artifact(
        index,
        step_name,
        method,
        url,
        resp=resp,
        error=error,
        request_headers=request_headers,
        request_body=request_body,
    )
    diagnosis = _classify_failure(step_name, resp, error)
    status = getattr(resp, "status_code", "unknown") if resp is not None else "no_response"
    detail = _response_debug_detail(resp)
    message = f"{prefix or step_name}_http_{status}; status={status}; 诊断={diagnosis}"
    if error:
        message += f"; error={error}"
    if artifact:
        message += f"; 抓包目录={artifact}"
    if detail:
        message += f"; {detail}"
    raise RegistrationStepError(message, artifact_path=artifact, diagnosis=diagnosis)


def _is_cloudflare_challenge(resp) -> bool:
    if resp is None:
        return False
    try:
        status_code = int(getattr(resp, "status_code", 0) or 0)
    except (TypeError, ValueError):
        status_code = 0
    if status_code not in (403, 503):
        return False
    text = str(getattr(resp, "text", "") or "").lower()
    return (
        "<title>just a moment" in text
        or "<title>attention required! | cloudflare" in text
        or "cf-chl-" in text
        or "__cf_chl_" in text
        or "cf-browser-verification" in text
    )


def _truthy(value: object, fallback: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _mail_config(register_proxy: str = "") -> dict:
    mail = config["mail"] if isinstance(config.get("mail"), dict) else {}
    use_register_proxy = _truthy(mail.get("api_use_register_proxy"), True)
    proxy = str(register_proxy or "").strip() if use_register_proxy else ""
    return {**mail, "api_use_register_proxy": use_register_proxy, "proxy": proxy}


def _authorize_landed_page(resp) -> str:
    """诊断用：粗判 authorize 之后落在哪个页面。返回 signup / login / "" 仅供日志。

    注意：email-verification / email_otp_verification 在注册和登录流程里都会出现，
    无法据此可靠区分，所以这里只用于打日志，绝不据此中断注册流程。
    """
    if resp is None:
        return ""
    final_url = str(getattr(resp, "url", "") or "").lower()
    data = _response_json(resp)
    page_type = ""
    page = data.get("page") if isinstance(data, dict) else None
    if isinstance(page, dict):
        page_type = str(page.get("type") or "").lower()
    if "create-account" in final_url or "signup" in final_url or "create_account" in page_type:
        return "signup"
    if "/log-in" in final_url or "/login" in final_url or page_type in {"login", "password_verification"}:
        return "login"
    return ""


def create_mailbox(username: str | None = None, register_proxy: str = "") -> dict:
    return mail_provider.create_mailbox(_mail_config(register_proxy), username)


def wait_for_code(mailbox: dict, register_proxy: str = "") -> str | None:
    return mail_provider.wait_for_code(_mail_config(register_proxy), mailbox)


from utils.sentinel import (  # noqa: F401
    SentinelTokenGenerator,
    build_sentinel_token as _build_sentinel_token_tuple,
    build_sentinel_tokens as _build_sentinel_tokens_tuple,
)


def build_sentinel_token(session: requests.Session, device_id: str, flow: str, profile: BrowserProfile | None = None) -> str:
    """请求 sentinel token，返回 sentinel header 字符串（兼容旧接口）。

    传入 profile 时使用 profile 的指纹特征（UA/分辨率/CPU 等），保证同账号全生命周期一致；
    不传时回退到模块级默认值（Chrome 145 / Windows）。
    """
    if profile is not None:
        sentinel_val, _oai_sc_val = _build_sentinel_token_tuple(
            session,
            device_id,
            flow,
            user_agent=profile.user_agent,
            sec_ch_ua=profile.sec_ch_ua,
            screen_resolution=profile.screen_resolution,
            hardware_concurrency=profile.hardware_concurrency,
            sec_ch_ua_platform=profile.sec_ch_ua_platform,
        )
    else:
        sentinel_val, _oai_sc_val = _build_sentinel_token_tuple(session, device_id, flow, user_agent=user_agent, sec_ch_ua=sec_ch_ua)
    return sentinel_val


def build_sentinel_headers(session: requests.Session, device_id: str, flow: str, profile: BrowserProfile | None = None) -> dict[str, str]:
    """构造注册接口需要的 Sentinel headers。

    2026-07 抓到的成功 create_account 请求同时携带 openai-sentinel-token 与
    openai-sentinel-so-token；后者与前者共享 c/id/flow，仅在 sentinel 后端返回 so 时发送。
    老 sentinel 响应没有 so 时保持兼容，只发送 openai-sentinel-token。
    """
    if profile is not None:
        sentinel_val, _oai_sc_val, so_val = _build_sentinel_tokens_tuple(
            session,
            device_id,
            flow,
            user_agent=profile.user_agent,
            sec_ch_ua=profile.sec_ch_ua,
            screen_resolution=profile.screen_resolution,
            hardware_concurrency=profile.hardware_concurrency,
            sec_ch_ua_platform=profile.sec_ch_ua_platform,
        )
    else:
        sentinel_val, _oai_sc_val, so_val = _build_sentinel_tokens_tuple(
            session,
            device_id,
            flow,
            user_agent=user_agent,
            sec_ch_ua=sec_ch_ua,
        )
    headers = {"openai-sentinel-token": sentinel_val}
    if so_val:
        headers["openai-sentinel-so-token"] = so_val
    return headers


def create_session(proxy: str = "", impersonate: str = "chrome") -> Any:
    kwargs = proxy_settings.build_session_kwargs(
        proxy=proxy,
        upstream=True,
        impersonate=impersonate,
        verify=False,
    )
    return requests.Session(**kwargs)


def _apply_clearance_to_session(session: requests.Session, bundle: ClearanceBundle | None) -> None:
    if bundle is None:
        return
    if bundle.user_agent:
        session.headers["User-Agent"] = bundle.user_agent
        session.headers["user-agent"] = bundle.user_agent
    for name, value in bundle.cookies.items():
        try:
            session.cookies.set(name, value, domain=f".{bundle.target_host or 'openai.com'}")
            session.cookies.set(name, value, domain=bundle.target_host or "auth.openai.com")
        except Exception:
            continue


def _headers_with_clearance(
    headers: dict[str, str],
    target_url: str,
    proxy: str = "",
    user_agent_override: str = "",
) -> dict[str, str]:
    merged = proxy_settings.build_headers(
        headers=headers,
        target_url=target_url,
        proxy=proxy,
        upstream=True,
    )
    normalized = {str(key): str(value) for key, value in merged.items()}
    if user_agent_override:
        ua_key = next((key for key in normalized if key.lower() == "user-agent"), "user-agent")
        normalized[ua_key] = user_agent_override
    return normalized


def _cloudflare_block_message(resp, prefix: str = "被 Cloudflare 拦截", reason: str = "") -> str:
    status = getattr(resp, "status_code", "unknown")
    debug = _response_debug_detail(resp)
    reason = reason or "clearance 刷新失败或重试后仍失败，请更换 IP/代理重试"
    return f"{prefix}，{reason}: status={status}, {debug}"


def request_with_local_retry(session: requests.Session, method: str, url: str, retry_attempts: int = 3, **kwargs):
    last_error = ""
    for _ in range(max(1, retry_attempts)):
        try:
            return session.request(method.upper(), url, timeout=default_timeout, **kwargs), ""
        except Exception as error:
            last_error = str(error)
            time.sleep(1)
    return None, last_error


def validate_otp(session: requests.Session, device_id: str, code: str, profile: BrowserProfile | None = None):
    headers = build_common_headers(profile) if profile else dict(common_headers)
    headers["referer"] = f"{auth_base}/email-verification"
    headers["oai-device-id"] = device_id
    headers.update(_make_trace_headers())
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False)
    if resp is not None and resp.status_code == 200:
        return resp, ""
    headers.update(build_sentinel_headers(session, device_id, "authorize_continue", profile=profile))
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False)
    return resp, error


def extract_oauth_callback_params_from_url(url: str) -> dict[str, str] | None:
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except Exception:
        return None
    code = str((params.get("code") or [""])[0]).strip()
    if not code:
        return None
    return {"code": code, "state": str((params.get("state") or [""])[0]).strip(), "scope": str((params.get("scope") or [""])[0]).strip()}


def request_platform_oauth_token(session: requests.Session, code: str, code_verifier: str, profile: BrowserProfile | None = None) -> dict | None:
    # 用 build_common_headers 作为基底，保证 sec-ch-ua-full-version-list / sec-ch-ua-arch /
    # sec-ch-ua-bitness / sec-ch-ua-platform-version / sec-ch-ua-model / dnt / sec-gpc /
    # connection / accept-encoding 等指纹 header 与注册流程完全一致，避免被风控区分设备
    p = profile or DEFAULT_PROFILE
    headers = build_common_headers(p)
    # 覆盖 OAuth token 接口的差异字段
    headers["accept"] = "*/*"
    headers["auth0-client"] = platform_auth0_client
    headers["origin"] = platform_base
    headers["pragma"] = "no-cache"
    headers["referer"] = f"{platform_base}/"
    headers["sec-fetch-site"] = "same-site"  # 跨站请求（platform.openai.com → auth.openai.com）
    resp = session.post(
        f"{auth_base}/api/accounts/oauth/token",
        headers=headers,
        json={
            "client_id": platform_oauth_client_id,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": platform_oauth_redirect_uri,
        },
        verify=False,
        timeout=60,
    )
    if resp.status_code != 200:
        print(resp.text)
        return None
    return _response_json(resp)


class PlatformRegistrar:
    def __init__(self, proxy: str = "", profile: BrowserProfile | None = None) -> None:
        self.proxy = str(proxy or "").strip()
        self.profile = profile or random_profile()
        self.session = create_session(self.proxy, impersonate=self.profile.impersonate)
        self.clearance_user_agent = ""
        self.clearance_failure_reason = ""
        self.device_id = str(uuid.uuid4())
        self.code_verifier = ""
        self.platform_auth_code = ""

    def close(self) -> None:
        self.session.close()

    def _navigate_headers(self, referer: str = "") -> dict[str, str]:
        headers = build_navigate_headers(self.profile)
        if referer:
            headers["referer"] = referer
        return headers

    def _json_headers(self, referer: str) -> dict[str, str]:
        headers = build_common_headers(self.profile)
        headers["referer"] = referer
        headers["oai-device-id"] = self.device_id
        headers.update(_make_trace_headers())
        return headers

    def _refresh_cloudflare_clearance(self, target_url: str, index: int) -> ClearanceBundle | None:
        self.clearance_failure_reason = ""
        profile = proxy_settings.get_profile(proxy=self.proxy, upstream=True)
        if not profile.clearance_enabled:
            self.clearance_failure_reason = (
                "可尝试使用 FlareSolverr 清障方式，注意需要 Docker 部署 flaresolverr、privoxy、warp-proxy 等相关容器"
            )
            step(index, f"检测到 Cloudflare 拦截，{self.clearance_failure_reason}", "yellow")
            return None
        step(index, "检测到 Cloudflare 拦截，尝试刷新 clearance", "yellow")
        bundle = proxy_settings.refresh_clearance(
            target_url=target_url,
            proxy=self.proxy,
            force=True,
            upstream=True,
        )
        if bundle is not None:
            _apply_clearance_to_session(self.session, bundle)
            self.clearance_user_agent = bundle.user_agent or self.clearance_user_agent
            step(index, "Cloudflare clearance 刷新完成，重试当前请求", "yellow")
        else:
            self.clearance_failure_reason = "clearance 刷新未返回可用 Cookie，请检查 FlareSolverr URL、代理和出口 IP"
            step(index, f"Cloudflare clearance 刷新失败：{self.clearance_failure_reason}", "yellow")
        return bundle

    def _platform_authorize(self, email: str, index: int) -> None:
        step(index, "开始 platform authorize")
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        self.code_verifier, code_challenge = _generate_pkce()
        params = {
            "issuer": auth_base,
            "client_id": platform_oauth_client_id,
            "audience": platform_oauth_audience,
            "redirect_uri": platform_oauth_redirect_uri,
            "device_id": self.device_id,
            # 注册流程显式声明 signup：throwaway 域名 OpenAI 会自动当新账号走注册，
            # 但 @outlook.com/@hotmail.com 这类真实消费邮箱会被 login_or_signup 路由到登录分支，
            # 后续 user/register 落在错误的 auth step 上报 invalid_auth_step。
            "screen_hint": "signup",
            "max_age": "0",
            "login_hint": email,
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "response_mode": "query",
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": platform_auth0_client,
        }
        target_url = f"{auth_base}/api/accounts/authorize?{urlencode(params)}"
        headers = self._navigate_headers(f"{platform_base}/")
        headers = _headers_with_clearance(headers, target_url, self.proxy, self.clearance_user_agent)
        resp, error = request_with_local_retry(self.session, "get", target_url, headers=headers, allow_redirects=True, verify=False)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(auth_base, index)
            if bundle is None:
                _raise_step_failure(index, "platform_authorize_cloudflare", "GET", target_url, resp, error or self.clearance_failure_reason, headers, prefix="platform_authorize")
            retry_headers = _headers_with_clearance(self._navigate_headers(f"{platform_base}/"), target_url, self.proxy, self.clearance_user_agent)
            resp, error = request_with_local_retry(self.session, "get", target_url, headers=retry_headers, allow_redirects=True, verify=False)
            if _is_cloudflare_challenge(resp):
                _raise_step_failure(index, "platform_authorize_cloudflare_retry", "GET", target_url, resp, error, retry_headers, prefix="platform_authorize")
        if resp is None or resp.status_code != 200:
            _raise_step_failure(index, "platform_authorize", "GET", target_url, resp, error, headers, prefix="platform_authorize")
        landed = _authorize_landed_page(resp)
        # 仅打日志，不据此中断：authorize 落地页无法可靠区分注册/登录，
        # 真正的判定交给 user/register（失败会 dump 完整响应）。
        step(index, f"platform authorize 完成[{landed or '?'}] url={str(getattr(resp, 'url', '') or '')[:160]}")

    def _register_user(self, email: str, password: str, index: int) -> None:
        step(index, "开始提交注册密码")
        url = f"{auth_base}/api/accounts/user/register"
        headers = self._json_headers(f"{auth_base}/create-account/password")
        headers.update(build_sentinel_headers(self.session, self.device_id, "username_password_create", profile=self.profile))
        headers = _headers_with_clearance(headers, url, self.proxy, self.clearance_user_agent)
        resp, error = request_with_local_retry(self.session, "post", url, json={"username": email, "password": password}, headers=headers, verify=False)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(auth_base, index)
            if bundle is None:
                _raise_step_failure(index, "user_register_cloudflare", "POST", url, resp, error or self.clearance_failure_reason, headers, {"username": email, "password": password}, prefix="user_register")
            headers = self._json_headers(f"{auth_base}/create-account/password")
            headers.update(build_sentinel_headers(self.session, self.device_id, "username_password_create", profile=self.profile))
            headers = _headers_with_clearance(headers, url, self.proxy, self.clearance_user_agent)
            resp, error = request_with_local_retry(self.session, "post", url, json={"username": email, "password": password}, headers=headers, verify=False)
            if _is_cloudflare_challenge(resp):
                _raise_step_failure(index, "user_register_cloudflare_retry", "POST", url, resp, error, headers, {"username": email, "password": password}, prefix="user_register")
        if resp is None or resp.status_code != 200:
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "注册失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            _raise_step_failure(index, "user_register", "POST", url, resp, error, headers, {"username": email, "password": password}, prefix="user_register")
        step(index, "提交注册密码完成")

    def _send_otp(self, index: int) -> None:
        step(index, "开始发送验证码")
        url = f"{auth_base}/api/accounts/email-otp/send"
        headers = _headers_with_clearance(self._navigate_headers(f"{auth_base}/create-account/password"), url, self.proxy, self.clearance_user_agent)
        resp, error = request_with_local_retry(self.session, "get", url, headers=headers, allow_redirects=True, verify=False)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(auth_base, index)
            if bundle is None:
                _raise_step_failure(index, "send_otp_cloudflare", "GET", url, resp, error or self.clearance_failure_reason, headers, prefix="send_otp")
            headers = _headers_with_clearance(self._navigate_headers(f"{auth_base}/create-account/password"), url, self.proxy, self.clearance_user_agent)
            resp, error = request_with_local_retry(self.session, "get", url, headers=headers, allow_redirects=True, verify=False)
            if _is_cloudflare_challenge(resp):
                _raise_step_failure(index, "send_otp_cloudflare_retry", "GET", url, resp, error, headers, prefix="send_otp")
        if resp is None or resp.status_code not in (200, 302):
            _raise_step_failure(index, "send_otp", "GET", url, resp, error, headers, prefix="send_otp")
        step(index, "发送验证码完成")

    def _validate_otp(self, code: str, index: int) -> None:
        step(index, f"开始校验验证码 {code}")
        resp, error = validate_otp(self.session, self.device_id, code, profile=self.profile)
        if resp is None or resp.status_code != 200:
            _raise_step_failure(index, "validate_otp", "POST", f"{auth_base}/api/accounts/email-otp/validate", resp, error, request_body={"code": code}, prefix="validate_otp")
        step(index, "验证码校验完成")

    def _create_account(self, name: str, birthdate: str, index: int) -> None:
        step(index, "开始创建账号资料")
        url = f"{auth_base}/api/accounts/create_account"
        headers = self._json_headers(f"{auth_base}/about-you")
        headers.update(build_sentinel_headers(self.session, self.device_id, "oauth_create_account", profile=self.profile))
        headers = _headers_with_clearance(headers, url, self.proxy, self.clearance_user_agent)
        resp, error = request_with_local_retry(self.session, "post", url, json={"name": name, "birthdate": birthdate}, headers=headers, verify=False)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(auth_base, index)
            if bundle is None:
                _raise_step_failure(index, "create_account_cloudflare", "POST", url, resp, error or self.clearance_failure_reason, headers, {"name": name, "birthdate": birthdate}, prefix="create_account")
            headers = self._json_headers(f"{auth_base}/about-you")
            headers.update(build_sentinel_headers(self.session, self.device_id, "oauth_create_account", profile=self.profile))
            headers = _headers_with_clearance(headers, url, self.proxy, self.clearance_user_agent)
            resp, error = request_with_local_retry(self.session, "post", url, json={"name": name, "birthdate": birthdate}, headers=headers, verify=False)
            if _is_cloudflare_challenge(resp):
                _raise_step_failure(index, "create_account_cloudflare_retry", "POST", url, resp, error, headers, {"name": name, "birthdate": birthdate}, prefix="create_account")
        if resp is None or resp.status_code not in (200, 302):
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "创建账号失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            _raise_step_failure(index, "create_account", "POST", url, resp, error, headers, {"name": name, "birthdate": birthdate}, prefix="create_account")
        data = _response_json(resp)
        callback_params = extract_oauth_callback_params_from_url(str(data.get("continue_url") or "").strip())
        self.platform_auth_code = str((callback_params or {}).get("code") or "").strip()
        step(index, "创建账号资料完成")

    def _exchange_registered_tokens(self, index: int) -> dict:
        step(index, "开始换 token")
        tokens = request_platform_oauth_token(self.session, self.platform_auth_code, self.code_verifier, profile=self.profile)
        if not tokens:
            raise RuntimeError("token换取失败")
        step(index, "token 换取完成")
        return tokens

    def register(self, index: int) -> dict:
        step(index, "开始创建邮箱")
        mailbox = create_mailbox(register_proxy=self.proxy)
        email = str(mailbox.get("address") or "").strip()
        if not email:
            mail_provider.release_mailbox(mailbox)
            raise RuntimeError("邮箱服务未返回 address")
        label = str(mailbox.get("label") or "")
        step(index, f"邮箱创建完成[{label}]: {email}")
        try:
            password = _random_password()
            first_name, last_name = _random_name()
            self._platform_authorize(email, index)
            self._register_user(email, password, index)
            self._send_otp(index)
            step(index, "开始等待注册验证码")
            code = wait_for_code(mailbox, register_proxy=self.proxy)
            if not code:
                raise RuntimeError("等待注册验证码超时")
            step(index, f"收到注册验证码: {code}")
            self._validate_otp(code, index)
            self._create_account(f"{first_name} {last_name}", _random_birthdate(), index)
            tokens = self._exchange_registered_tokens(index)
        except Exception as error:
            mail_provider.mark_mailbox_result(mailbox, success=False, error=error)
            raise
        mail_provider.mark_mailbox_result(mailbox, success=True)
        return {
            "email": email,
            "password": password,
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "id_token": str(tokens.get("id_token") or "").strip(),
            "source_type": "web",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "device_id": self.device_id,
            "fingerprint_profile": self.profile.name,
        }


def worker(index: int) -> dict:
    start = time.time()
    registrar = PlatformRegistrar(config["proxy"])
    try:
        step(index, "任务启动")
        result = registrar.register(index)
        cost = time.time() - start
        access_token = str(result["access_token"])
        account_service.add_account_items([result])
        refresh_result = account_service.refresh_accounts([access_token])
        if refresh_result.get("errors"):
            step(index, f"账号已保存，刷新状态暂未成功，稍后可重试: {refresh_result['errors']}", "yellow")
        with stats_lock:
            stats["done"] += 1
            stats["success"] += 1
            avg = (time.time() - stats["start_time"]) / stats["success"]
        log(f'{result["email"]} 注册成功，本次耗时{cost:.1f}s，全局平均每个号注册耗时{avg:.1f}s', "green")
        return {"ok": True, "index": index, "result": result}
    except Exception as e:
        cost = time.time() - start
        with stats_lock:
            stats["done"] += 1
            stats["fail"] += 1
        log(f"任务{index} 注册失败，本次耗时{cost:.1f}s，原因: {e}", "red")
        return {
            "ok": False,
            "index": index,
            "error": str(e),
            "diagnosis": getattr(e, "diagnosis", ""),
            "artifact_path": getattr(e, "artifact_path", ""),
        }
    finally:
        registrar.close()
