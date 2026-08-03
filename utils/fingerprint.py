"""浏览器指纹 Profile 管理模块。

统一管理注册/重登/刷新全流程的浏览器指纹特征，确保：
1. 同一账号全生命周期指纹一致（注册、重登、token 刷新使用相同 profile）；
2. 全量贴近当前成功浏览器样本，不再保留历史 profile。

设计要点：
- `impersonate` 必须与 `user_agent` 中声明的 Chrome 大版本匹配，否则 TLS 指纹与 UA 矛盾；
- Profile 使用成功样本中的 `zh-CN,zh;q=0.9`；
- `pick_profile(seed)` 基于种子（如 email）确定性选择，保证同一账号每次拿到相同 profile；
- `random_profile()` 用于新账号注册时从当前可用 Profile 中选择。
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
    accept_language: str
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
    return f'"Not;A=Brand";v="8", "Chromium";v="{major}", "Google Chrome";v="{major}"'


def _sec_ch_ua_full_version_list(major: int) -> str:
    # 品牌串与 _sec_ch_ua 保持一致（成功样本使用 "Not;A=Brand"）
    return f'"Chromium";v="{major}.0.0.0", "Not;A=Brand";v="99.0.0.0", "Google Chrome";v="{major}.0.0.0"'


# ── 预置 Profile ──────────────────────────────────────────────
# TODO P2: 以下 profile 字段（Chrome 大版本、平台版本、分辨率、CPU 数、语言）均来自
# 2026-08 成功注册样本的抓包，属样本驱动决策。样本扩充或上游策略变化后需参数化为
# 可配置项，避免单点漂移。
_PROFILE_CHROME152_WIN = BrowserProfile(
    name="chrome152_win",
    impersonate="chrome",
    user_agent=_chrome_ua(152, "Windows"),
    sec_ch_ua=_sec_ch_ua(152),
    sec_ch_ua_full_version_list=_sec_ch_ua_full_version_list(152),
    sec_ch_ua_platform='"Windows"',
    sec_ch_ua_platform_version='"10.0.0"',
    sec_ch_ua_arch='"x86_64"',
    sec_ch_ua_bitness='"64"',
    accept_language="zh-CN,zh;q=0.9",
    screen_resolution="1920x1080",
    hardware_concurrency=16,
)

PROFILES: list[BrowserProfile] = [
    _PROFILE_CHROME152_WIN,
]

# name -> profile 索引表
_PROFILE_MAP: dict[str, BrowserProfile] = {p.name: p for p in PROFILES}

# 默认 Profile（用于新流程兜底）
DEFAULT_PROFILE: BrowserProfile = _PROFILE_CHROME152_WIN


def pick_profile(seed: str = "") -> BrowserProfile:
    """根据 seed（如 email）确定性选择 profile。

    同一 seed 永远返回相同 profile，保证重登/刷新时指纹与注册时一致。
    """
    if not seed:
        return DEFAULT_PROFILE
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return PROFILES[h % len(PROFILES)]


def random_profile() -> BrowserProfile:
    # TODO P3: 当前仅一个 profile，random.choice 无随机意义；后续扩充 REGISTRATION 池时恢复随机性。
    """随机选择新注册 profile。"""
    return random.choice(PROFILES)


def get_profile_by_name(name: str) -> BrowserProfile:
    """根据持久化的 profile name 还原 profile。

    找不到时回退到 DEFAULT_PROFILE，保证异常情况下也能工作。
    """
    if not name:
        return DEFAULT_PROFILE
    return _PROFILE_MAP.get(name) or DEFAULT_PROFILE


def build_common_headers(profile: BrowserProfile) -> dict[str, str]:
    """构建 JSON API 请求头。"""
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
    """构建页面导航请求头。"""
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
