#!/usr/bin/env python3
"""诊断 chatgpt.com / auth.openai.com 访问失败问题。

复用项目内的指纹 profile 与 navigate headers，逐链路探测：

1. 环境检查：curl_cffi 版本、chrome146 impersonate 支持、代理出口
2. chatgpt.com 链路：
   - bootstrap 首页预热（拿 cf_clearance cookie，过 Cloudflare WAF）
   - /backend-api/me、/backend-api/models、accounts/check（需 access_token）
   - /backend-anon/models（匿名链路，无需 token）
   - sentinel/chat-requirements prepare/finalize（PoW token）
3. auth.openai.com 链路：
   - /api/accounts/authorize（注册流程入口，CF managed challenge 高发）

用法:
    python scripts/diagnose_access.py                         # 全自动，读项目 config 代理
    python scripts/diagnose_access.py --proxy socks5h://127.0.0.1:1080
    python scripts/diagnose_access.py --token eyJhbGciOi...  # 测 backend-api
    python scripts/diagnose_access.py --email test@xxx.com   # 测 authorize login_hint
    python scripts/diagnose_access.py --skip-auth            # 跳过 auth.openai.com
    python scripts/diagnose_access.py --skip-backend          # 跳过 backend-api

定位思路：
- 若 chatgpt.com bootstrap 拿不到 cf_clearance → 代理 IP 质量差或被 CF 标记，换高质量 IP
- 若 bootstrap 成功但 backend-api 仍 403 → cf_clearance 未随请求带上（cookie domain 问题）
- 若 auth.openai.com/authorize 返回 CF managed challenge → IP 信誉问题，需 FlareSolverr 或高质量 IP
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Optional
from urllib.parse import urlencode

# 把项目根目录加入 sys.path，使脚本能 import 项目内模块
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from curl_cffi import requests  # noqa: E402

# ── 复用项目指纹 profile 与请求头构造 ──────────────────────────────
from utils.fingerprint import (  # noqa: E402
    DEFAULT_PROFILE,
    build_common_headers,
    build_navigate_headers,
)

# ── 常量：项目内硬编码的关键 URL（来自 openai_backend_api.py / openai_register.py / openai_oauth.py）
CHATGPT_BASE = "https://chatgpt.com"
AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
PLATFORM_OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
PLATFORM_OAUTH_REDIRECT_URI = f"{PLATFORM_BASE}/auth/callback"
PLATFORM_OAUTH_AUDIENCE = "https://api.openai.com/v1"
PLATFORM_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
DEFAULT_POW_SCRIPT = "https://chatgpt.com/backend-api/sentinel/sdk.js"

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}[INFO]{RESET} {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")


# ── Cloudflare 挑战检测（复用 register/openai_register.py 的 _is_cloudflare_challenge 逻辑）
def is_cloudflare_challenge(resp: Any) -> bool:
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


def build_proxy_kwargs(proxy_arg: Optional[str]) -> dict[str, Any]:
    """构建 curl_cffi Session 的代理参数。

    优先级：命令行 --proxy > 项目 proxy_settings 配置 > 无代理
    """
    if proxy_arg:
        return {"proxy": proxy_arg, "impersonate": DEFAULT_PROFILE.impersonate, "verify": True}

    # 尝试复用项目 proxy_settings（读取 config 中的代理运行时配置）
    try:
        from services.proxy_service import proxy_settings

        kwargs = proxy_settings.build_session_kwargs(
            account={},
            impersonate=DEFAULT_PROFILE.impersonate,
            verify=True,
        )
        runtime = proxy_settings.get_runtime_status()
        info(
            f"项目代理配置: egress_mode={runtime.get('egress_mode')}, "
            f"has_proxy={runtime.get('has_proxy')}, "
            f"clearance_mode={runtime.get('clearance_mode')}"
        )
        return kwargs
    except Exception as exc:
        warn(f"读取项目 proxy_settings 失败（{exc!r}），回退到无代理直连")
        return {"impersonate": DEFAULT_PROFILE.impersonate, "verify": True}


def get_proxy_display(kwargs: dict[str, Any]) -> str:
    proxy = kwargs.get("proxy")
    if proxy:
        # 隐藏代理用户名密码，只显示 host:port
        if "@" in str(proxy):
            scheme_part = str(proxy).split("://", 1)
            if len(scheme_part) == 2:
                scheme, rest = scheme_part
                cred_host = rest.rsplit("@", 1)
                if len(cred_host) == 2:
                    return f"{scheme}://{cred_host[1]}"
        return str(proxy)
    return "(直连，无代理)"


def cookie_jar_has(session: requests.Session, name: str, domain_part: str = "") -> bool:
    # curl_cffi 的 Cookies 继承自 MutableMapping：`name in cookies` 检查 key 是否存在，
    # 迭代返回的是 cookie name 字符串（非 Cookie 对象），故不能访问 .name/.domain 属性。
    try:
        if name not in session.cookies:
            return False
    except Exception:
        return False
    if not domain_part:
        return True
    # 需要校验 domain 时，走底层 http.cookiejar（curl_cffi Cookies 内部用 CookieJar）
    try:
        jar = getattr(session.cookies, "jar", None) or session.cookies
        for cookie in jar:
            if getattr(cookie, "name", None) == name and domain_part in (getattr(cookie, "domain", "") or ""):
                return True
    except Exception:
        # domain 校验失败时，仅凭 name 存在即认为通过（诊断脚本容错）
        return True
    return False


# ────────────────────────────────────────────────────────────────────
# 1. 环境检查
# ────────────────────────────────────────────────────────────────────
def check_environment() -> dict[str, Any]:
    section("1. 环境检查")
    result: dict[str, Any] = {}

    # curl_cffi 版本
    try:
        import curl_cffi

        version = getattr(curl_cffi, "__version__", "unknown")
        info(f"curl_cffi 版本: {version}")
        result["curl_cffi_version"] = version
    except Exception as exc:
        fail(f"curl_cffi 导入失败: {exc!r}")
        result["curl_cffi_version"] = None

    # impersonate 支持：尝试创建 chrome146 session 探活
    impersonate = DEFAULT_PROFILE.impersonate
    info(f"目标 impersonate: {impersonate}")
    try:
        test_session = requests.Session(impersonate=impersonate, verify=True)
        test_session.close()
        ok(f"impersonate={impersonate} 可用")
        result["impersonate_ok"] = True
    except Exception as exc:
        fail(f"impersonate={impersonate} 不支持: {exc!r}")
        warn("若 curl_cffi 版本过低，需升级: pip install -U curl_cffi")
        result["impersonate_ok"] = False

    # profile 指纹
    info(f"指纹 profile: {DEFAULT_PROFILE.name}")
    info(f"  User-Agent: {DEFAULT_PROFILE.user_agent}")
    info(f"  sec-ch-ua-platform: {DEFAULT_PROFILE.sec_ch_ua_platform}")
    info(f"  accept-language: {DEFAULT_PROFILE.accept_language}")

    return result


# ────────────────────────────────────────────────────────────────────
# 2. 代理出口探测
# ────────────────────────────────────────────────────────────────────
def check_proxy_egress(kwargs: dict[str, Any]) -> None:
    section("2. 代理出口探测")
    info(f"代理: {get_proxy_display(kwargs)}")

    if not kwargs.get("proxy"):
        warn("未配置代理，直连 chatgpt.com 可能因 IP 信誉问题被 CF 拦截")
        return

    # 用 ipinfo.io 探测出口 IP（轻量、不被 CF 拦截）
    try:
        session = requests.Session(**{k: v for k, v in kwargs.items() if k in ("proxy", "impersonate", "verify")})
        resp = session.get("https://ipinfo.io/json", timeout=20)
        session.close()
        if resp.status_code == 200:
            data = resp.json()
            ok(
                f"出口 IP: {data.get('ip')} ({data.get('country')}/{data.get('region')} "
                f"{data.get('org', '')})"
            )
        else:
            fail(f"出口探测失败: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"出口探测异常: {exc!r}")
        warn("代理不可用或配置错误，后续访问将直接失败")


# ────────────────────────────────────────────────────────────────────
# 3. chatgpt.com 链路探测
# ────────────────────────────────────────────────────────────────────
def check_chatgpt_chain(
    kwargs: dict[str, Any],
    access_token: str = "",
    skip_backend: bool = False,
) -> None:
    section("3. chatgpt.com 链路探测")

    session = requests.Session(**kwargs)
    # 设备 cookie：与 openai_backend_api.py __init__ 对齐
    session.headers.update({
        "User-Agent": DEFAULT_PROFILE.user_agent,
        "Origin": CHATGPT_BASE,
        "Referer": CHATGPT_BASE + "/",
        "Accept-Language": DEFAULT_PROFILE.accept_language,
        "Sec-Ch-Ua": DEFAULT_PROFILE.sec_ch_ua,
        "Sec-Ch-Ua-Arch": DEFAULT_PROFILE.sec_ch_ua_arch,
        "Sec-Ch-Ua-Bitness": DEFAULT_PROFILE.sec_ch_ua_bitness,
        "Sec-Ch-Ua-Full-Version-List": DEFAULT_PROFILE.sec_ch_ua_full_version_list,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": DEFAULT_PROFILE.sec_ch_ua_platform,
        "Sec-Ch-Ua-Platform-Version": DEFAULT_PROFILE.sec_ch_ua_platform_version,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })

    # ── 3.1 bootstrap 首页预热（最关键：拿 cf_clearance 过 WAF）──
    print(f"\n{BOLD}[3.1] bootstrap 首页预热 GET https://chatgpt.com/{RESET}")
    bootstrap_headers = build_navigate_headers(DEFAULT_PROFILE)
    # 与 _bootstrap_headers 一致：首次导航 sec-fetch-site: none
    bootstrap_headers["sec-fetch-site"] = "none"
    bootstrap_ok = False
    try:
        resp = session.get(CHATGPT_BASE + "/", headers=bootstrap_headers, timeout=30)
        info(f"HTTP {resp.status_code}, len={len(resp.text)}")
        if resp.status_code == 200:
            ok("首页 200，检查 cf_clearance cookie...")
            if cookie_jar_has(session, "cf_clearance"):
                ok("已获取 cf_clearance cookie（WAF 通过）")
                bootstrap_ok = True
            else:
                warn("首页 200 但无 cf_clearance cookie（可能未被该 IP 触发挑战，或已放行）")
                bootstrap_ok = True  # 200 仍算通过
        elif is_cloudflare_challenge(resp):
            fail(f"首页被 Cloudflare 拦截 (HTTP {resp.status_code})")
            warn("原因：IP 信誉不足，CF 下发 managed challenge（需 JS 执行）")
            warn("解决：更换高质量住宅/机房 IP，或配置 FlareSolverr clearance")
        else:
            fail(f"首页异常: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"首页请求异常: {exc!r}")
        warn("网络层不可达：检查代理 / DNS / 防火墙")

    if not bootstrap_ok:
        warn("bootstrap 未通过，backend-api 链路大概率也会 403，但继续探测以定位")

    # ── 3.2 backend-anon/models（匿名链路，无需 token）──
    print(f"\n{BOLD}[3.2] 匿名模型列表 GET /backend-anon/models{RESET}")
    path = "/backend-anon/models?iim=false&is_gizmo=false"
    try:
        resp = session.get(CHATGPT_BASE + path, headers={
            "X-OpenAI-Target-Path": "/backend-anon/models",
            "X-OpenAI-Target-Route": "/backend-anon/models",
        }, timeout=30)
        info(f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            model_count = len(data.get("models", []))
            ok(f"匿名模型列表正常，共 {model_count} 个模型")
        elif resp.status_code == 403:
            if is_cloudflare_challenge(resp):
                fail("匿名接口被 CF 拦截 → bootstrap 未拿到有效 cf_clearance")
            else:
                fail("匿名接口 403（非 CF challenge，可能是 sentinel/header 校验）")
        else:
            fail(f"匿名接口异常: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"匿名接口请求异常: {exc!r}")

    if skip_backend or not access_token:
        if not access_token:
            info("未提供 --token，跳过 backend-api（需登录）链路")
        return

    # ── 3.3 backend-api（需 access_token）──
    print(f"\n{BOLD}[3.3] backend-api/me（需 token）GET /backend-api/me{RESET}")
    session.headers["Authorization"] = f"Bearer {access_token}"
    path = "/backend-api/me"
    me_ok = False
    try:
        resp = session.get(CHATGPT_BASE + path, headers={
            "X-OpenAI-Target-Path": path,
            "X-OpenAI-Target-Route": path,
        }, timeout=20)
        info(f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            ok(f"/me 正常: email={data.get('email')}, id={data.get('id')}")
            me_ok = True
        elif resp.status_code == 401:
            fail("/me 401 → access_token 已失效，需刷新（refresh_token 重刷或密码重登）")
        elif resp.status_code == 403:
            if is_cloudflare_challenge(resp):
                fail("/me 被 CF 拦截 → cf_clearance 未随请求带上或已失效")
                warn("排查：确认 bootstrap 真的拿到了 cf_clearance（cookie domain=.chatgpt.com）")
            else:
                fail("/me 403（非 CF）→ 可能缺少 sentinel/header 或 token 类型不对")
        else:
            fail(f"/me 异常: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"/me 请求异常: {exc!r}")

    # ── 3.4 accounts/check（额度相关）──
    print(f"\n{BOLD}[3.4] 账号检查 GET /backend-api/accounts/check/v4-2023-04-27{RESET}")
    path = "/backend-api/accounts/check/v4-2023-04-27"
    try:
        resp = session.get(
            CHATGPT_BASE + path + "?timezone_offset_min=-480",
            headers={"X-OpenAI-Target-Path": path, "X-OpenAI-Target-Route": path},
            timeout=20,
        )
        info(f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            payload = resp.json()
            default = ((payload.get("accounts") or {}).get("default") or {}).get("account") or {}
            entitlement = (payload.get("accounts") or {}).get("default", {}).get("entitlement", {})
            ok(
                f"账号检查正常: plan={default.get('plan_type')}, "
                f"sub={entitlement.get('subscription_plan')}, "
                f"active={entitlement.get('has_active_subscription')}"
            )
        elif resp.status_code == 403:
            fail("/accounts/check 403 → 同 /me，cf_clearance 问题")
        else:
            fail(f"/accounts/check 异常: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"/accounts/check 请求异常: {exc!r}")

    # ── 3.5 sentinel/chat-requirements（PoW，图片/对话必经）──
    print(f"\n{BOLD}[3.5] sentinel chat-requirements prepare POST /backend-api/sentinel/chat-requirements/prepare{RESET}")
    path = "/backend-api/sentinel/chat-requirements/prepare"
    try:
        # PoW p token 这里用空串探活（真实流程需 build_legacy_requirements_token）
        resp = session.post(
            CHATGPT_BASE + path,
            headers={
                "X-OpenAI-Target-Path": path,
                "X-OpenAI-Target-Route": path,
                "Content-Type": "application/json",
            },
            json={"p": ""},
            timeout=30,
        )
        info(f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            pow_required = (data.get("proofofwork") or {}).get("required", False)
            arkose_required = (data.get("arkose") or {}).get("required", False)
            ok(f"prepare 正常: pow_required={pow_required}, arkose_required={arkose_required}")
            if arkose_required:
                warn("上游要求 arkose token（项目未实现，注册/对话会被拒）")
        elif resp.status_code == 403:
            if is_cloudflare_challenge(resp):
                fail("prepare 被 CF 拦截")
            else:
                fail("prepare 403（非 CF）→ p token 无效或 header 缺失")
        else:
            fail(f"prepare 异常: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"prepare 请求异常: {exc!r}")

    session.close()


# ────────────────────────────────────────────────────────────────────
# 4. auth.openai.com 链路探测（注册流程入口）
# ────────────────────────────────────────────────────────────────────
def check_auth_chain(kwargs: dict[str, Any], email: str = "") -> None:
    section("4. auth.openai.com 注册入口探测")

    info("auth.openai.com 的 Cloudflare 风控比 chatgpt.com 更严，managed challenge 高发")
    info("此步骤仅探测 authorize 落地，不真正注册")

    session = requests.Session(**kwargs)
    import secrets

    device_id = secrets.token_urlsafe(16)
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")

    # 构造 authorize URL（复用 openai_register.py _platform_authorize 的参数）
    params = {
        "issuer": AUTH_BASE,
        "client_id": PLATFORM_OAUTH_CLIENT_ID,
        "audience": PLATFORM_OAUTH_AUDIENCE,
        "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
        "device_id": device_id,
        "screen_hint": "signup",
        "max_age": "0",
        "login_hint": email or "probe@example.com",
        "scope": "openid profile email offline_access",
        "response_type": "code",
        "response_mode": "query",
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        # code_challenge 仅探活时用占位（真实流程需 PKCE，此处不影响 CF 检测）
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1At8n8hSX5JBKgJhA",
        "code_challenge_method": "S256",
        "auth0Client": PLATFORM_AUTH0_CLIENT,
    }
    target_url = f"{AUTH_BASE}/api/accounts/authorize?{urlencode(params)}"
    # 导航头：referer 指向 platform.openai.com（跨站请求）
    headers = build_navigate_headers(DEFAULT_PROFILE)
    headers["referer"] = f"{PLATFORM_BASE}/"

    print(f"\n{BOLD}[4.1] authorize GET {AUTH_BASE}/api/accounts/authorize{RESET}")
    info(f"login_hint={params['login_hint']}")
    try:
        resp = session.get(target_url, headers=headers, allow_redirects=True, timeout=30, verify=False)
        info(f"HTTP {resp.status_code}, final_url={str(getattr(resp, 'url', '') or '')[:120]}")
        if resp.status_code == 200:
            text_lower = resp.text.lower()
            if "create-account" in text_lower or "password" in text_lower:
                ok("authorize 200 且落到注册/密码页 → CF 未拦截该 IP")
            elif "login" in text_lower:
                warn("authorize 200 但落到登录页 → 该邮箱被识别为已存在账号")
            else:
                warn(f"authorize 200 但页面特征不明，len={len(resp.text)}（建议人工查看）")
        elif is_cloudflare_challenge(resp):
            fail(f"authorize 被 Cloudflare managed challenge 拦截 (HTTP {resp.status_code})")
            warn("根因：auth.openai.com 域名 IP 信誉风控极严，代码层无法绕过（需 JS 执行）")
            warn("解决1：更换高质量住宅 IP（机房 IP 多数被标记）")
            warn("解决2：配置 FlareSolverr clearance（项目已支持 clearance_mode=flaresolverr）")
            warn("解决3：用已注册账号的 access_token 走 chatgpt.com 链路，绕开注册流程")
        elif resp.status_code in (301, 302, 303, 307, 308):
            warn(f"authorize 重定向 {resp.status_code}（allow_redirects=True 应已跟随，检查 final_url）")
        else:
            fail(f"authorize 异常: HTTP {resp.status_code}")
    except Exception as exc:
        fail(f"authorize 请求异常: {exc!r}")
        warn("auth.openai.com 不可达：检查代理 / DNS / 防火墙")

    session.close()


# ────────────────────────────────────────────────────────────────────
# 5. 总结建议
# ────────────────────────────────────────────────────────────────────
def print_summary() -> None:
    section("5. 定位与解决建议")
    print(f"""
{BOLD}常见失败模式速查：{RESET}

{YELLOW}A. chatgpt.com bootstrap 拿不到 cf_clearance（首页即 403/503）{RESET}
   → IP 信誉不足，Cloudflare 下发挑战。换高质量 IP 或配 FlareSolverr。

{YELLOW}B. bootstrap 成功（200）但 backend-api/me 仍 403{RESET}
   → cf_clearance cookie 未随请求带上。检查 cookie domain（应为 .chatgpt.com）。
   → 确认 get_user_info 已调用 _bootstrap() 预热（项目已修复，见 openai_backend_api.py:369）。

{YELLOW}C. backend-api/me 401{RESET}
   → access_token 失效。走 refresh_token 刷新或密码重登兜底（见 access_token过期恢复方案.md）。

{YELLOW}D. auth.openai.com/authorize 返回 CF managed challenge{RESET}
   → auth 域名风控比 chatgpt.com 更严，代码层无法绕过。
   → 解决：换高质量住宅 IP / FlareSolverr / 直接用已有 token 走 chatgpt.com 链路。

{YELLOW}E. prepare 阶段 403（非 CF）{RESET}
   → p token 无效或 PoW 脚本源未更新。检查 pow_script_sources 是否从 bootstrap 正确提取。

{YELLOW}F. prepare 要求 arkose token{RESET}
   → 上游对该账号/IP 开启 arkose 校验，项目未实现 arkose 求解，需换号或换 IP。
""")


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断 chatgpt.com / auth.openai.com 访问失败")
    parser.add_argument("--proxy", default=None, help="代理地址，如 socks5h://127.0.0.1:1080（默认读项目 config）")
    parser.add_argument("--token", default=None, help="access_token（测试 backend-api 链路）")
    parser.add_argument("--email", default=None, help="email（authorize login_hint，默认 probe@example.com）")
    parser.add_argument("--skip-auth", action="store_true", help="跳过 auth.openai.com 探测")
    parser.add_argument("--skip-backend", action="store_true", help="跳过 backend-api（需 token）探测")
    args = parser.parse_args()

    print(f"{BOLD}chatgpt2api 访问失败诊断脚本{RESET}")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. 环境检查
    check_environment()

    # 2. 代理构建
    kwargs = build_proxy_kwargs(args.proxy)

    # 3. 代理出口
    check_proxy_egress(kwargs)

    # 4. chatgpt.com 链路
    check_chatgpt_chain(
        kwargs,
        access_token=args.token or "",
        skip_backend=args.skip_backend,
    )

    # 5. auth.openai.com 链路
    if not args.skip_auth:
        check_auth_chain(kwargs, email=args.email or "")
    else:
        info("--skip-auth 已跳过 auth.openai.com 探测")

    # 6. 总结
    print_summary()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
