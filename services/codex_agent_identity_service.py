from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from services.account_service import AccountService


AUTHAPI_BASE = "https://auth.openai.com/api/accounts"
IMPERSONATE = "chrome"
CHROME_VERSION = "146"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36"
)

AGENT_VERSION = "0.138.0-alpha.6"
AGENT_HARNESS_ID = "codex-cli"
RUNNING_LOCATION = "local"


class CodexAgentIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexAgentIdentityResult:
    auth_json: dict[str, Any]
    account_payload: dict[str, Any]
    verify_warning: str = ""


def extract_access_token(value: object) -> str:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            value = json.loads(raw)
        except Exception:
            return raw
    if not isinstance(value, dict):
        return ""
    token = value.get("access_token") or value.get("accessToken")
    return str(token or "").strip()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value.strip())
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _first_clean(*values: object) -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _session_metadata_claims(metadata: object) -> dict[str, Any]:
    data = _as_dict(metadata)
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    id_payload = AccountService._decode_jwt_payload(
        _first_clean(data.get("id_token"), data.get("idToken"))
    )

    plan_obj = account.get("plan") if isinstance(account.get("plan"), dict) else {}
    plan_type = _first_clean(
        data.get("plan_type"),
        data.get("planType"),
        data.get("account_plan"),
        account.get("plan_type"),
        account.get("planType"),
        plan_obj.get("type"),
        plan_obj.get("name"),
    )

    return {
        "account_id": _first_clean(
            data.get("account_id"),
            data.get("accountId"),
            data.get("chatgpt_account_id"),
            account.get("account_id"),
            account.get("accountId"),
            account.get("id"),
        ),
        "chatgpt_user_id": _first_clean(
            data.get("chatgpt_user_id"),
            data.get("user_id"),
            data.get("userId"),
            user.get("id"),
            user.get("user_id"),
            id_payload.get("sub"),
        ),
        "email": _first_clean(
            data.get("email"),
            user.get("email"),
            profile.get("email"),
            id_payload.get("email"),
        ),
        "plan_type": plan_type or "free",
        "exp": data.get("exp") or data.get("expires_at") or id_payload.get("exp"),
        "iat": data.get("iat") or data.get("issued_at") or id_payload.get("iat"),
        "chatgpt_account_is_fedramp": bool(
            data.get("chatgpt_account_is_fedramp")
            or account.get("chatgpt_account_is_fedramp")
            or account.get("is_fedramp")
        ),
    }


def access_token_claims(access_token: str) -> dict[str, Any]:
    payload = AccountService._decode_jwt_payload(access_token)
    auth_info = payload.get("https://api.openai.com/auth")
    auth_info = auth_info if isinstance(auth_info, dict) else {}
    profile = payload.get("https://api.openai.com/profile")
    profile = profile if isinstance(profile, dict) else {}
    return {
        "account_id": str(auth_info.get("chatgpt_account_id") or "").strip(),
        "chatgpt_user_id": str(auth_info.get("chatgpt_user_id") or "").strip(),
        "email": str(profile.get("email") or "").strip(),
        "plan_type": str(auth_info.get("chatgpt_plan_type") or "free").strip() or "free",
        "exp": payload.get("exp"),
        "iat": payload.get("iat"),
        "chatgpt_account_is_fedramp": bool(auth_info.get("chatgpt_account_is_fedramp")),
    }


def _require_chatgpt_session_delegator(access_token: str, token_claims: dict[str, Any]) -> None:
    if token_claims.get("account_id") and token_claims.get("chatgpt_user_id"):
        return
    token_kind = "Platform OAuth opaque token" if access_token.startswith("token:") else "token missing ChatGPT account claims"
    raise CodexAgentIdentityError(
        "Codex Agent Identity requires a ChatGPT Web session accessToken from "
        "https://chatgpt.com/api/auth/session; current token is "
        f"{token_kind}. The platform.openai.com OAuth token used by the auto-register flow "
        "is rejected by AuthAPI as unsupported_agent_delegator."
    )


def _merge_claims(token_claims: dict[str, Any], metadata_claims: dict[str, Any]) -> dict[str, Any]:
    token_has_account_claims = bool(token_claims.get("account_id") or token_claims.get("chatgpt_user_id"))
    return {
        "account_id": _first_clean(token_claims.get("account_id"), metadata_claims.get("account_id")),
        "chatgpt_user_id": _first_clean(token_claims.get("chatgpt_user_id"), metadata_claims.get("chatgpt_user_id")),
        "email": _first_clean(token_claims.get("email"), metadata_claims.get("email")),
        "plan_type": (
            _first_clean(token_claims.get("plan_type"), metadata_claims.get("plan_type"))
            if token_has_account_claims
            else _first_clean(metadata_claims.get("plan_type"), token_claims.get("plan_type")) or "free"
        ),
        "exp": token_claims.get("exp") if token_claims.get("exp") is not None else metadata_claims.get("exp"),
        "iat": token_claims.get("iat") if token_claims.get("iat") is not None else metadata_claims.get("iat"),
        "chatgpt_account_is_fedramp": bool(
            token_claims.get("chatgpt_account_is_fedramp")
            or metadata_claims.get("chatgpt_account_is_fedramp")
        ),
    }


def generate_ed25519_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    pkcs8_der = private_key.private_bytes(
        encoding=Encoding.DER,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    private_key_b64 = base64.b64encode(pkcs8_der).decode("ascii")

    pub_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    ssh_header = b"ssh-ed25519"
    blob = bytearray()
    blob.extend(len(ssh_header).to_bytes(4, "big"))
    blob.extend(ssh_header)
    blob.extend(len(pub_bytes).to_bytes(4, "big"))
    blob.extend(pub_bytes)
    public_key_ssh = f"ssh-ed25519 {base64.b64encode(bytes(blob)).decode('ascii')}"
    return private_key_b64, public_key_ssh


def _raise_for_upstream_error(response: Any, action: str) -> None:
    if response.status_code == 200:
        return
    body = _redact_sensitive(str(getattr(response, "text", "") or ""))
    if len(body) > 600:
        body = body[:600] + "..."
    raise CodexAgentIdentityError(f"{action} failed: {response.status_code} {body}")


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if isinstance(headers, dict):
        return str(headers.get("content-type") or headers.get("Content-Type") or "").strip()
    try:
        return str(headers.get("content-type") or headers.get("Content-Type") or "").strip()
    except Exception:
        return ""


def _response_text_snippet(response: Any, limit: int = 600) -> str:
    body = str(getattr(response, "text", "") or "").strip()
    if not body:
        raw = getattr(response, "content", b"")
        if isinstance(raw, (bytes, bytearray)):
            try:
                body = bytes(raw).decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
    body = _redact_sensitive(body)
    if len(body) > limit:
        body = body[:limit] + "..."
    return body


def _parse_json_response(response: Any, action: str) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:
        status = int(getattr(response, "status_code", 0) or 0)
        content_type = _response_content_type(response) or "unknown"
        body = _response_text_snippet(response)
        raise CodexAgentIdentityError(
            f"{action} returned invalid JSON: status={status} content_type={content_type} body={body or '[empty]'}"
        ) from exc
    if not isinstance(data, dict):
        raise CodexAgentIdentityError(f"{action} returned non-object JSON")
    return data


def _redact_sensitive(value: str) -> str:
    patterns = (
        r'(?i)("?(?:access_token|accessToken|agent_private_key|signature|authorization)"?\s*[:=]\s*)"[^"\n\r&]{6,}"',
        r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}",
    )
    redacted = value
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted


def register_agent(access_token: str, public_key_ssh: str) -> str:
    response = requests.post(
        f"{AUTHAPI_BASE}/v1/agent/register",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        },
        json={
            "abom": {
                "agent_version": AGENT_VERSION,
                "agent_harness_id": AGENT_HARNESS_ID,
                "running_location": RUNNING_LOCATION,
            },
            "agent_public_key": public_key_ssh,
        },
        impersonate=IMPERSONATE,
        timeout=15,
    )
    _raise_for_upstream_error(response, "Agent registration")
    data = _parse_json_response(response, "Agent registration")
    agent_runtime_id = str(data.get("agent_runtime_id") or "").strip()
    if not agent_runtime_id:
        body = _response_text_snippet(response) or "[empty]"
        raise CodexAgentIdentityError(
            f"Agent registration response missing agent_runtime_id: {body}"
        )
    return agent_runtime_id


def register_task(access_token: str, agent_runtime_id: str, private_key_pkcs8_b64: str) -> str:
    pkcs8_der = base64.b64decode(private_key_pkcs8_b64)
    pem = b"-----BEGIN PRIVATE KEY-----\n" + base64.encodebytes(pkcs8_der) + b"-----END PRIVATE KEY-----\n"
    private_key = load_pem_private_key(pem, password=None)

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = f"{agent_runtime_id}:{timestamp}"
    signature_b64 = base64.b64encode(private_key.sign(payload.encode("utf-8"))).decode("ascii")
    response = requests.post(
        f"{AUTHAPI_BASE}/v1/agent/{agent_runtime_id}/task/register",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        },
        json={
            "timestamp": timestamp,
            "signature": signature_b64,
        },
        impersonate=IMPERSONATE,
        timeout=15,
    )
    _raise_for_upstream_error(response, "Task registration")
    data = _parse_json_response(response, "Task registration")
    return str(data.get("encrypted_task_id") or "").strip()


def generate_auth_json(agent_runtime_id: str, private_key_pkcs8_b64: str, claims: dict[str, Any]) -> dict[str, Any]:
    return {
        "auth_mode": "agent_identity",
        "agent_identity": {
            "agent_runtime_id": agent_runtime_id,
            "agent_private_key": private_key_pkcs8_b64,
            "account_id": str(claims.get("account_id") or ""),
            "chatgpt_user_id": str(claims.get("chatgpt_user_id") or ""),
            "email": str(claims.get("email") or ""),
            "plan_type": str(claims.get("plan_type") or "free"),
            "chatgpt_account_is_fedramp": bool(claims.get("chatgpt_account_is_fedramp")),
        },
    }


def create_agent_identity(
        access_token: str,
        verify_task: bool = True,
        metadata: object | None = None,
) -> CodexAgentIdentityResult:
    access_token = str(access_token or "").strip()
    if not access_token:
        raise CodexAgentIdentityError("access_token is required")

    token_claims = access_token_claims(access_token)
    _require_chatgpt_session_delegator(access_token, token_claims)
    claims = _merge_claims(token_claims, _session_metadata_claims(metadata))

    private_key_b64, public_key_ssh = generate_ed25519_keypair()
    agent_runtime_id = register_agent(access_token, public_key_ssh)
    verify_warning = ""
    if verify_task:
        try:
            register_task(access_token, agent_runtime_id, private_key_b64)
        except Exception as exc:
            verify_warning = str(exc)

    auth_json = generate_auth_json(agent_runtime_id, private_key_b64, claims)
    account_payload = {
        "access_token": access_token,
        "source_type": "codex",
        "export_type": "codex_agent_identity",
        "email": claims["email"],
        "account_id": claims["account_id"],
        "user_id": claims["chatgpt_user_id"],
        "plan_type": claims["plan_type"],
        "agent_identity": dict(auth_json["agent_identity"]),
    }
    if claims.get("exp") is not None:
        account_payload["expires_at"] = claims["exp"]
    if claims.get("iat") is not None:
        account_payload["issued_at"] = claims["iat"]
    return CodexAgentIdentityResult(
        auth_json=auth_json,
        account_payload=account_payload,
        verify_warning=verify_warning,
    )


class CodexAgentIdentityService:
    extract_access_token = staticmethod(extract_access_token)
    access_token_claims = staticmethod(access_token_claims)
    generate_ed25519_keypair = staticmethod(generate_ed25519_keypair)
    register_agent = staticmethod(register_agent)
    register_task = staticmethod(register_task)
    generate_auth_json = staticmethod(generate_auth_json)
    create_agent_identity = staticmethod(create_agent_identity)


codex_agent_identity_service = CodexAgentIdentityService()
