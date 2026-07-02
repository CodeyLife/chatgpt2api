"""浏览器指纹 Profile 管理模块。

统一管理注册/重登/刷新全流程的浏览器指纹特征，确保：
1. 同一账号全生命周期指纹一致（注册、重登、token 刷新使用相同 profile）；
2. 不同账号指纹差异化（分散指纹聚类，降低批量注册被识别风险）。

设计要点：
- `impersonate` 必须与 `user_agent` 中声明的 Chrome 大版本匹配，否则 TLS 指纹与 UA 矛盾；
- 所有 Profile 的 `accept_language` 统一为 `en-US,en;q=0.9`，避免重登暴露真实语言；
- `pick_profile(seed)` 基于种子（如 email）确定性选择，保证同一账号每次拿到相同 profile；
- `random_profile()` 用于新账号注册时随机选择。
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class BrowserProfile:
    """完整的浏览器指纹 Profile。"""

    name: str  # profile 唯一标识，用于持久化到账号信息
    impersonate: str  # curl_cffi 指纹标识，如 "chrome", "chrome120", "chrome131"
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_full_version_list: str
    sec_ch_ua_platform: str  # 如 '"Windows"' / '"macOS"'
    sec_ch_ua_platform_version: str  # 如 '"10.0.0"' / '"14.0.0"'
    sec_ch_ua_arch: str  # 如 '"x86_64"' / '""' (macOS 不发 arch)
    sec_ch_ua_bitness: str  # 如 '"64"'
    accept_language: str  # 统一 "en-US,en;q=0.9"
    screen_resolution: str  # sentinel/pow 用，如 "1920x1080"
    hardware_concurrency: int  # sentinel config 用

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_macos(self) -> bool:
        return "macOS" in self.sec_ch_ua_platform


def _chrome_ua(major: int, platform: str) -> str:
    if platform == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36"
        )
    # macOS
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def _sec_ch_ua(major: int) -> str:
    return f'"Google Chrome";v="{major}", "Not?A_Brand";v="8", "Chromium";v="{major}"'


def _sec_ch_ua_full_version_list(major: int) -> str:
    return f'"Chromium";v="{major}.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="{major}.0.0.0"'


# ── 预置 Profile ──────────────────────────────────────────────
# Chrome 145 / Windows（与历史默认一致，作为兜底，impersonate="chrome" 为 curl_cffi 最新版指纹）
_PROFILE_CHROME145_WIN = BrowserProfile(
    name="chrome145_win",
    impersonate="chrome",
    user_agent=_chrome_ua(145, "Windows"),
    sec_ch_ua=_sec_ch_ua(145),
    sec_ch_ua_full_version_list=_sec_ch_ua_full_version_list(145),
    sec_ch_ua_platform='"Windows"',
    sec_ch_ua_platform_version='"10.0.0"',
    sec_ch_ua_arch='"x86_64"',
    sec_ch_ua_bitness='"64"',
    accept_language="en-US,en;q=0.9",
    screen_resolution="1920x1080",
    hardware_concurrency=8,
)

_PROFILE_CHROME131_WIN = BrowserProfile(
    name="chrome131_win",
    impersonate="chrome131",
    user_agent=_chrome_ua(131, "Windows"),
    sec_ch_ua=_sec_ch_ua(131),
    sec_ch_ua_full_version_list=_sec_ch_ua_full_version_list(131),
    sec_ch_ua_platform='"Windows"',
    sec_ch_ua_platform_version='"10.0.0"',
    sec_ch_ua_arch='"x86_64"',
    sec_ch_ua_bitness='"64"',
    accept_language="en-US,en;q=0.9",
    screen_resolution="2560x1440",
    hardware_concurrency=12,
)

_PROFILE_CHROME131_MAC = BrowserProfile(
    name="chrome131_mac",
    impersonate="chrome131",
    user_agent=_chrome_ua(131, "macOS"),
    sec_ch_ua=_sec_ch_ua(131),
    sec_ch_ua_full_version_list=_sec_ch_ua_full_version_list(131),
    sec_ch_ua_platform='"macOS"',
    sec_ch_ua_platform_version='"14.0.0"',
    sec_ch_ua_arch='""',
    sec_ch_ua_bitness='"64"',
    accept_language="en-US,en;q=0.9",
    screen_resolution="2560x1600",
    hardware_concurrency=10,
)

_PROFILE_CHROME124_WIN = BrowserProfile(
    name="chrome124_win",
    impersonate="chrome124",
    user_agent=_chrome_ua(124, "Windows"),
    sec_ch_ua=_sec_ch_ua(124),
    sec_ch_ua_full_version_list=_sec_ch_ua_full_version_list(124),
    sec_ch_ua_platform='"Windows"',
    sec_ch_ua_platform_version='"10.0.0"',
    sec_ch_ua_arch='"x86_64"',
    sec_ch_ua_bitness='"64"',
    accept_language="en-US,en;q=0.9",
    screen_resolution="1920x1080",
    hardware_concurrency=16,
)

_PROFILE_CHROME120_WIN = BrowserProfile(
    name="chrome120_win",
    impersonate="chrome120",
    user_agent=_chrome_ua(120, "Windows"),
    sec_ch_ua=_sec_ch_ua(120),
    sec_ch_ua_full_version_list=_sec_ch_ua_full_version_list(120),
    sec_ch_ua_platform='"Windows"',
    sec_ch_ua_platform_version='"10.0.0"',
    sec_ch_ua_arch='"x86_64"',
    sec_ch_ua_bitness='"64"',
    accept_language="en-US,en;q=0.9",
    screen_resolution="1366x768",
    hardware_concurrency=4,
)

PROFILES: list[BrowserProfile] = [
    _PROFILE_CHROME145_WIN,
    _PROFILE_CHROME131_WIN,
    _PROFILE_CHROME131_MAC,
    _PROFILE_CHROME124_WIN,
    _PROFILE_CHROME120_WIN,
]

# name -> profile 索引表
_PROFILE_MAP: dict[str, BrowserProfile] = {p.name: p for p in PROFILES}

# 默认 Profile（与历史硬编码值完全一致，确保向后兼容）
DEFAULT_PROFILE: BrowserProfile = _PROFILE_CHROME145_WIN


def pick_profile(seed: str = "") -> BrowserProfile:
    """根据 seed（如 email）确定性选择 profile。

    同一 seed 永远返回相同 profile，保证重登/刷新时指纹与注册时一致。
    """
    if not seed:
        return DEFAULT_PROFILE
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return PROFILES[h % len(PROFILES)]


def random_profile() -> BrowserProfile:
    """随机选择 profile，用于新账号注册。"""
    return random.choice(PROFILES)


def get_profile_by_name(name: str) -> BrowserProfile:
    """根据持久化的 profile name 还原 profile。

    找不到时回退到 DEFAULT_PROFILE，保证老账号（无 profile 字段）也能工作。
    """
    if not name:
        return DEFAULT_PROFILE
    return _PROFILE_MAP.get(name) or DEFAULT_PROFILE


def build_common_headers(profile: BrowserProfile) -> dict[str, str]:
    """构建 JSON API 请求头（与 openai_register.py 原有 common_headers 对齐）。"""
    return {
        "accept": "application/json",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": profile.accept_language,
        "cache-control": "no-cache",
        "connection": "keep-alive",
        "content-type": "application/json",
        "dnt": "1",
        "origin": "https://auth.openai.com",
        "priority": "u=1, i",
        "sec-gpc": "1",
        "sec-ch-ua": profile.sec_ch_ua,
        "sec-ch-ua-arch": profile.sec_ch_ua_arch,
        "sec-ch-ua-bitness": profile.sec_ch_ua_bitness,
        "sec-ch-ua-full-version-list": profile.sec_ch_ua_full_version_list,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": profile.sec_ch_ua_platform,
        "sec-ch-ua-platform-version": profile.sec_ch_ua_platform_version,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": profile.user_agent,
    }


def build_navigate_headers(profile: BrowserProfile) -> dict[str, str]:
    """构建页面导航请求头（与 openai_register.py 原有 navigate_headers 对齐）。"""
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": profile.accept_language,
        "cache-control": "max-age=0",
        "connection": "keep-alive",
        "dnt": "1",
        "sec-gpc": "1",
        "sec-ch-ua": profile.sec_ch_ua,
        "sec-ch-ua-arch": profile.sec_ch_ua_arch,
        "sec-ch-ua-bitness": profile.sec_ch_ua_bitness,
        "sec-ch-ua-full-version-list": profile.sec_ch_ua_full_version_list,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": profile.sec_ch_ua_platform,
        "sec-ch-ua-platform-version": profile.sec_ch_ua_platform_version,
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": profile.user_agent,
    }
