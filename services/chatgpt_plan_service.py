from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import Session

from services.account_service import AccountService
from services.proxy_service import proxy_settings
from utils.fingerprint import build_common_headers, pick_profile, get_profile_by_name


ACCOUNTS_CHECK_PATH = "/backend-api/accounts/check/v4-2023-04-27"
RETRYABLE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_token(token: str) -> str:
    value = str(token or "").strip().strip('"').strip("'")
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


def decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    token = normalize_token(token)
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}


def token_claims(token: str) -> dict[str, Any]:
    payload = decode_jwt_payload_unverified(token)
    auth = payload.get("https://api.openai.com/auth")
    auth = auth if isinstance(auth, dict) else {}
    profile = payload.get("https://api.openai.com/profile")
    profile = profile if isinstance(profile, dict) else {}
    exp = payload.get("exp")
    token_expired = None
    token_expires_at = None
    if isinstance(exp, (int, float)):
        token_expired = datetime.now(timezone.utc).timestamp() >= float(exp)
        token_expires_at = datetime.fromtimestamp(float(exp), timezone.utc).isoformat()
    return {
        "email": profile.get("email"),
        "user_name": profile.get("name"),
        "user_id": auth.get("chatgpt_user_id") or auth.get("user_id"),
        "account_id": auth.get("chatgpt_account_id"),
        "claim_plan_type": auth.get("chatgpt_plan_type"),
        "exp": exp,
        "token_expires_at": token_expires_at,
        "token_expired": token_expired,
    }


def parse_accounts_check(data: dict[str, Any], *, token: str = "") -> dict[str, Any]:
    claims = token_claims(token) if token else {}
    claim_account_id = claims.get("account_id")
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict):
        raise ValueError("response missing accounts object")
    item = None
    account_key = None
    if claim_account_id and isinstance(accounts.get(claim_account_id), dict):
        item = accounts.get(claim_account_id)
        account_key = claim_account_id
    elif isinstance(accounts.get("default"), dict):
        item = accounts.get("default")
        account_key = str(((item or {}).get("account") or {}).get("account_id") or "default")
    else:
        for key, value in accounts.items():
            if key != "default" and isinstance(value, dict):
                item = value
                account_key = key
                break
    if not isinstance(item, dict):
        raise ValueError("no parseable account item found")
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    entitlement = item.get("entitlement") if isinstance(item.get("entitlement"), dict) else {}
    entitlement_discount = entitlement.get("discount") if isinstance(entitlement.get("discount"), dict) else {}
    last_sub = item.get("last_active_subscription") if isinstance(item.get("last_active_subscription"), dict) else {}
    eligible_promo_campaigns = item.get("eligible_promo_campaigns") if isinstance(item.get("eligible_promo_campaigns"), dict) else {}
    plus_campaign = eligible_promo_campaigns.get("plus") if isinstance(eligible_promo_campaigns.get("plus"), dict) else None
    plus_meta = plus_campaign.get("metadata") if isinstance(plus_campaign, dict) and isinstance(plus_campaign.get("metadata"), dict) else {}
    discount = plus_meta.get("discount") if isinstance(plus_meta.get("discount"), dict) else {}
    duration = plus_meta.get("duration") if isinstance(plus_meta.get("duration"), dict) else {}
    plan_type = account.get("plan_type") or claims.get("claim_plan_type") or ""
    subscription_plan = entitlement.get("subscription_plan") or ""
    is_free = str(plan_type).lower() == "free" or str(subscription_plan).lower() == "chatgptfreeplan"
    offers = ((item.get("eligible_offers") or {}).get("offers") or []) if isinstance(item.get("eligible_offers"), dict) else []
    result = {
        "ok": True,
        "checked_at": now_iso(),
        "account_id": account.get("account_id") or account_key or claim_account_id,
        "account_user_role": account.get("account_user_role"),
        "current_plan_type": plan_type,
        "subscription_plan": subscription_plan,
        "has_active_subscription": bool(entitlement.get("has_active_subscription")),
        "is_active_subscription_gratis": bool(entitlement.get("is_active_subscription_gratis")),
        "expires_at": entitlement.get("expires_at"),
        "renews_at": entitlement.get("renews_at"),
        "cancels_at": entitlement.get("cancels_at"),
        "billing_period": entitlement.get("billing_period"),
        "billing_currency": entitlement.get("billing_currency"),
        "is_delinquent": bool(entitlement.get("is_delinquent")),
        "discount_type": entitlement_discount.get("discount_type"),
        "discount_amount": entitlement_discount.get("amount"),
        "discount_duration_num_periods": entitlement_discount.get("duration_num_periods"),
        "discount_expires_at": entitlement_discount.get("discount_expires_at"),
        "discount_cancellation_policy": entitlement_discount.get("cancellation_policy"),
        "discount_promo_campaign_id": entitlement_discount.get("promo_campaign_id"),
        "last_purchase_origin_platform": last_sub.get("purchase_origin_platform"),
        "last_will_renew": bool(last_sub.get("will_renew")),
        "plus_trial_eligible": bool(is_free and plus_campaign),
        "plus_trial_campaign_id": (plus_campaign or {}).get("id") if isinstance(plus_campaign, dict) else None,
        "plus_trial_title": plus_meta.get("title"),
        "plus_trial_summary": plus_meta.get("summary"),
        "plus_trial_discount_percentage": discount.get("percentage"),
        "plus_trial_duration_num_periods": duration.get("num_periods"),
        "plus_trial_duration_period": duration.get("period"),
        "plus_trial_promotion_type_label": plus_meta.get("promotion_type_label"),
        "eligible_offer_ids": [offer.get("id") for offer in offers if isinstance(offer, dict) and offer.get("id")],
        "features_count": len(item.get("features") or []),
        "can_access_with_session": bool(item.get("can_access_with_session")),
        "raw_account_plan_type": account.get("plan_type"),
    }
    result.update({key: value for key, value in claims.items() if value is not None})
    return result


def _account_profile(account: dict[str, Any] | None, claims: dict[str, Any]) -> Any:
    account = account if isinstance(account, dict) else {}
    profile_name = str(account.get("browser_profile") or account.get("fingerprint_profile") or "").strip()
    if profile_name:
        return get_profile_by_name(profile_name)
    seed = str(account.get("email") or claims.get("email") or claims.get("user_id") or claims.get("account_id") or "")
    return pick_profile(seed)


def _device_id(account: dict[str, Any] | None, claims: dict[str, Any], token: str) -> str:
    account = account if isinstance(account, dict) else {}
    for key in ("device_id", "oai_device_id", "chatgpt_device_id"):
        value = str(account.get(key) or "").strip()
        if value:
            return value
    seed = str(claims.get("user_id") or claims.get("account_id") or claims.get("email") or token[-32:])
    encoded = base64.urlsafe_b64encode(seed.encode("utf-8")).decode("ascii").rstrip("=")
    return f"codex-{encoded[:48]}"


def _plan_check_headers(token: str, *, account: dict[str, Any] | None, claims: dict[str, Any], target_url: str, proxy: str) -> dict[str, object]:
    profile = _account_profile(account, claims)
    headers = build_common_headers(profile)
    headers.pop("content-type", None)
    headers.update(
        {
            "accept": "*/*",
            "authorization": f"Bearer {token}",
            "origin": "https://chatgpt.com",
            "referer": "https://chatgpt.com/",
            "oai-device-id": _device_id(account, claims, token),
            "oai-language": "zh-CN",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-openai-target-path": ACCOUNTS_CHECK_PATH,
            "x-openai-target-route": ACCOUNTS_CHECK_PATH,
        }
    )
    return proxy_settings.build_headers(headers, target_url=target_url, account=account, proxy=proxy, upstream=True)


def _coerce_timeout(value: object, default: float = 15.0) -> float:
    try:
        timeout = float(value)
    except (OverflowError, TypeError, ValueError):
        timeout = default
    return max(1.0, min(60.0, timeout))


def _coerce_attempts(value: object, default: int = 2) -> int:
    try:
        attempts = int(value)
    except (OverflowError, TypeError, ValueError):
        attempts = default
    return max(1, min(5, attempts))


def _coerce_retry_delay(value: object, default: float = 1.0) -> float:
    try:
        delay = float(value)
    except (OverflowError, TypeError, ValueError):
        delay = default
    return max(0.0, min(30.0, delay))


def _retry_after_seconds(response: Any, fallback: float) -> float:
    headers = getattr(response, "headers", None) or {}
    value = ""
    try:
        value = str(headers.get("retry-after") or headers.get("Retry-After") or "")
    except Exception:
        value = ""
    try:
        return max(0.0, min(60.0, float(value)))
    except (OverflowError, TypeError, ValueError):
        return fallback


def check_account_plan(
    access_token: str,
    *,
    proxy: str = "",
    timeout: float | None = 15.0,
    session: Any | None = None,
    account: dict[str, Any] | None = None,
    timezone_offset_min: int | str = 0,
    max_attempts: int | None = None,
    retry_delay: float | None = None,
) -> dict[str, Any]:
    token = normalize_token(access_token)
    if not token:
        return {"ok": False, "checked_at": now_iso(), "error": "token is required"}
    claims = token_claims(token)
    if claims.get("token_expired") is True:
        return {"ok": False, "checked_at": now_iso(), "error": "token expired", **claims}
    own_session = session is None
    profile = _account_profile(account, claims)
    session = session or Session(
        **proxy_settings.build_session_kwargs(
            account=account,
            proxy=proxy,
            verify=True,
            upstream=True,
            impersonate=profile.impersonate,
        )
    )
    attempts = _coerce_attempts(max_attempts, default=2)
    request_timeout = _coerce_timeout(timeout, default=15.0)
    base_retry_delay = _coerce_retry_delay(retry_delay, default=1.0)
    tz_value = str(timezone_offset_min if timezone_offset_min not in {None, ""} else 0)
    target_url = f"https://chatgpt.com{ACCOUNTS_CHECK_PATH}?timezone_offset_min={quote(tz_value, safe='')}"
    last_failure: dict[str, Any] | None = None
    try:
        for attempt in range(1, attempts + 1):
            try:
                headers = _plan_check_headers(token, account=account, claims=claims, target_url=target_url, proxy=proxy)
                response = session.get(
                    target_url,
                    headers=headers,
                    allow_redirects=False,
                    timeout=request_timeout,
                )
                text = str(getattr(response, "text", "") or "")
                status = int(getattr(response, "status_code", 0) or 0)
                retryable = status in RETRYABLE_STATUSES
                if not (200 <= status < 300):
                    last_failure = {
                        "ok": False,
                        "checked_at": now_iso(),
                        "http_status": status,
                        "error": f"HTTP {status}",
                        "response_preview": text[:500],
                        "attempt_count": attempt,
                        "max_attempts": attempts,
                        "request_timeout": request_timeout,
                        "retryable": retryable,
                        **claims,
                    }
                    if retryable and attempt < attempts:
                        time.sleep(_retry_after_seconds(response, base_retry_delay))
                        continue
                    return last_failure
                try:
                    data = response.json()
                except Exception:
                    data = json.loads(text) if text.strip().startswith(("{", "[")) else None
                if not isinstance(data, dict):
                    return {
                        "ok": False,
                        "checked_at": now_iso(),
                        "http_status": status,
                        "error": "response is not JSON object",
                        "response_preview": text[:500],
                        "attempt_count": attempt,
                        "max_attempts": attempts,
                        "request_timeout": request_timeout,
                        "retryable": False,
                        **claims,
                    }
                parsed = parse_accounts_check(data, token=token)
                parsed.update(
                    {
                        "http_status": status,
                        "attempt_count": attempt,
                        "max_attempts": attempts,
                        "request_timeout": request_timeout,
                        "retryable": False,
                    }
                )
                return parsed
            except Exception as exc:
                retryable = attempt < attempts
                last_failure = {
                    "ok": False,
                    "checked_at": now_iso(),
                    "error": f"{type(exc).__name__}: {exc}",
                    "attempt_count": attempt,
                    "max_attempts": attempts,
                    "request_timeout": request_timeout,
                    "retryable": retryable,
                    **claims,
                }
                if retryable:
                    time.sleep(base_retry_delay)
                    continue
                return last_failure
        return last_failure or {"ok": False, "checked_at": now_iso(), "error": "plan check failed", **claims}
    except Exception as exc:
        return {"ok": False, "checked_at": now_iso(), "error": f"{type(exc).__name__}: {exc}", **claims}
    finally:
        if own_session:
            session.close()


def account_payload_from_plan_result(plan_result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan_result, dict) or not plan_result.get("ok"):
        return {}
    payload: dict[str, Any] = {}
    if plan_result.get("current_plan_type"):
        payload["plan_type"] = str(plan_result.get("current_plan_type") or "")
        payload["type"] = payload["plan_type"]
    if plan_result.get("account_id"):
        payload["account_id"] = str(plan_result.get("account_id") or "")
    if plan_result.get("user_id"):
        payload["user_id"] = str(plan_result.get("user_id") or "")
    if plan_result.get("email"):
        payload["email"] = str(plan_result.get("email") or "")
    payload["chatgpt_plan_check"] = dict(plan_result)
    return payload


class ChatGPTPlanService:
    parse_accounts_check = staticmethod(parse_accounts_check)
    check_account_plan = staticmethod(check_account_plan)
    account_payload_from_plan_result = staticmethod(account_payload_from_plan_result)


chatgpt_plan_service = ChatGPTPlanService()
