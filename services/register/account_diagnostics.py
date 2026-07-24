from __future__ import annotations

import json


ACCOUNT_UNUSABLE_CODES = frozenset({
    "account_deactivated",
    "account_deleted",
    "account_banned",
})

ACCOUNT_UNUSABLE_TEXT_MARKERS = (
    "account_deactivated",
    "account_deleted",
    "account_banned",
    "account deactivated",
    "account deleted",
    "account banned",
    "account has been deactivated",
    "account has been deleted",
    "account was deactivated",
    "account was deleted",
    "your account has been deactivated",
    "your account has been deleted",
    "your account was deactivated",
    "your account was deleted",
    "账号已停用",
    "账号已禁用",
    "账号已删除",
    "账户已停用",
    "账户已禁用",
    "账户已删除",
)


class AccountUnusableError(RuntimeError):
    def __init__(self, message: str, error_code: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code


def detect_account_unusable_text(text: str) -> str:
    lowered = str(text or "").lower()
    for code in ACCOUNT_UNUSABLE_CODES:
        if code in lowered:
            return code
    if not any(marker in lowered for marker in ACCOUNT_UNUSABLE_TEXT_MARKERS):
        return ""
    if "delete" in lowered or "删除" in lowered:
        return "account_deleted"
    if "ban" in lowered or "封" in lowered:
        return "account_banned"
    return "account_deactivated"


def detect_account_unusable_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    code = ""
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
    if not code:
        code = str(payload.get("code") or payload.get("error_code") or "").strip()
    return code if code in ACCOUNT_UNUSABLE_CODES else ""


def detect_account_unusable_response_body(body: str) -> str:
    try:
        payload = json.loads(body or "")
    except Exception:
        return ""
    return detect_account_unusable_payload(payload)


def account_unusable_message(error_code: str) -> str:
    code = str(error_code or "account_unusable").strip()
    return f"账号不可用，已识别为 {code}"
