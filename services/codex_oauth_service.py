from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi.requests import Session

from services.account_service import AccountService, account_service
from services.proxy_service import proxy_settings


CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTH_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_SCOPE = "openid email profile offline_access"


class CodexOAuthError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(96))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorize_url(
    *,
    state: str | None = None,
    code_challenge: str | None = None,
    prompt: str = "login",
) -> dict[str, str]:
    code_verifier, generated_challenge = generate_pkce()
    state_value = str(state or generate_state()).strip()
    challenge_value = str(code_challenge or generated_challenge).strip()
    prompt_value = str(prompt or "login").strip() or "login"
    params = {
        "client_id": CODEX_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CODEX_REDIRECT_URI,
        "scope": CODEX_SCOPE,
        "state": state_value,
        "code_challenge": challenge_value,
        "code_challenge_method": "S256",
        "prompt": prompt_value,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return {
        "auth_url": f"{CODEX_AUTH_URL}?{urlencode(params)}",
        "state": state_value,
        "code_verifier": code_verifier,
        "code_challenge": challenge_value,
        "redirect_uri": CODEX_REDIRECT_URI,
    }


def parse_callback_code(callback_url: str, *, expected_state: str | None = None) -> dict[str, str]:
    raw = str(callback_url or "").strip()
    if not raw:
        raise CodexOAuthError("callback_url is required")
    if "://" not in raw and "?" not in raw and "&" not in raw:
        return {"code": raw, "state": ""}
    parsed = urlparse(raw)
    params = parse_qs(parsed.query, keep_blank_values=True)
    code = str((params.get("code") or [""])[0] or "").strip()
    state = str((params.get("state") or [""])[0] or "").strip()
    error = str((params.get("error") or [""])[0] or "").strip()
    if error:
        description = str((params.get("error_description") or [""])[0] or "").strip()
        raise CodexOAuthError(f"OAuth callback error: {error}{': ' + description if description else ''}")
    if not code:
        raise CodexOAuthError("OAuth callback missing code")
    expected = str(expected_state or "").strip()
    if expected and state != expected:
        raise CodexOAuthError("OAuth callback state mismatch")
    return {"code": code, "state": state}


def _json_response(response: Any, action: str) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        data = {}
    if int(getattr(response, "status_code", 0) or 0) != 200:
        message = ""
        if isinstance(data, dict):
            message = str(data.get("error_description") or data.get("error") or data.get("message") or "")
        text = str(getattr(response, "text", "") or "")
        raise CodexOAuthError(f"{action} failed: HTTP {response.status_code} {(message or text[:300]).strip()}")
    if not isinstance(data, dict):
        raise CodexOAuthError(f"{action} response is not a JSON object")
    return data


def exchange_code(code: str, code_verifier: str, *, session: Any | None = None) -> dict[str, Any]:
    code = str(code or "").strip()
    code_verifier = str(code_verifier or "").strip()
    if not code:
        raise CodexOAuthError("code is required")
    if not code_verifier:
        raise CodexOAuthError("code_verifier is required")
    own_session = session is None
    session = session or Session(**proxy_settings.build_session_kwargs(verify=True))
    try:
        response = session.post(
            CODEX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data=urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": CODEX_CLIENT_ID,
                    "code": code,
                    "redirect_uri": CODEX_REDIRECT_URI,
                    "code_verifier": code_verifier,
                }
            ),
            timeout=30,
        )
        payload = _json_response(response, "Codex OAuth token exchange")
    finally:
        if own_session:
            session.close()
    if not str(payload.get("access_token") or "").strip():
        raise CodexOAuthError("Codex OAuth token exchange response missing access_token")
    return payload


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id_token_claims(id_token: str) -> dict[str, Any]:
    payload = AccountService._decode_jwt_payload(id_token)
    auth = payload.get("https://api.openai.com/auth")
    auth = auth if isinstance(auth, dict) else {}
    profile = payload.get("https://api.openai.com/profile")
    profile = profile if isinstance(profile, dict) else {}
    return {
        "email": str(payload.get("email") or profile.get("email") or "").strip(),
        "account_id": str(
            payload.get("account_id")
            or payload.get("https://api.openai.com/account_id")
            or auth.get("chatgpt_account_id")
            or ""
        ).strip(),
        "user_id": str(payload.get("sub") or auth.get("chatgpt_user_id") or "").strip(),
        "plan_type": str(auth.get("chatgpt_plan_type") or payload.get("plan_type") or "").strip(),
    }


def build_auth_json(token_response: dict[str, Any]) -> dict[str, Any]:
    access_token = str(token_response.get("access_token") or "").strip()
    refresh_token = str(token_response.get("refresh_token") or "").strip()
    id_token = str(token_response.get("id_token") or "").strip()
    claims = _id_token_claims(id_token)
    expires_in = int(token_response.get("expires_in") or 0)
    now = datetime.now(timezone.utc)
    auth_json = {
        "type": "codex",
        "source_type": "codex",
        "export_type": "codex",
        "email": claims["email"],
        "account_id": claims["account_id"],
        "user_id": claims["user_id"],
        "plan_type": claims["plan_type"],
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "last_refresh": _iso_z(now),
    }
    if expires_in > 0:
        auth_json["expired"] = _iso_z(now + timedelta(seconds=expires_in))
    return auth_json


def finish_oauth_callback(
    callback_url: str,
    code_verifier: str,
    *,
    expected_state: str | None = None,
    import_account: bool = True,
    session: Any | None = None,
) -> dict[str, Any]:
    callback = parse_callback_code(callback_url, expected_state=expected_state)
    token_response = exchange_code(callback["code"], code_verifier, session=session)
    auth_json = build_auth_json(token_response)
    import_result = None
    if import_account:
        import_result = account_service.add_account_items([auth_json])
    return {
        "ok": True,
        "callback_url": str(callback_url or "").strip(),
        "state": callback["state"],
        "auth_json": auth_json,
        "token_response": {
            key: value
            for key, value in token_response.items()
            if key not in {"access_token", "refresh_token", "id_token"}
        },
        "import_result": import_result,
    }


class CodexOAuthService:
    build_authorize_url = staticmethod(build_authorize_url)
    parse_callback_code = staticmethod(parse_callback_code)
    exchange_code = staticmethod(exchange_code)
    build_auth_json = staticmethod(build_auth_json)
    finish_oauth_callback = staticmethod(finish_oauth_callback)


codex_oauth_service = CodexOAuthService()
