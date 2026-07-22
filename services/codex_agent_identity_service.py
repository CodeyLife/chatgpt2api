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
from utils.helper import anonymize_token


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
    data = response.json()
    agent_runtime_id = str(data.get("agent_runtime_id") or "").strip()
    if not agent_runtime_id:
        raise CodexAgentIdentityError("Agent registration response missing agent_runtime_id")
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
    data = response.json()
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
            "chatgpt_account_is_fedramp": False,
        },
    }


def create_agent_identity(access_token: str, verify_task: bool = True) -> CodexAgentIdentityResult:
    access_token = str(access_token or "").strip()
    if not access_token:
        raise CodexAgentIdentityError("access_token is required")

    claims = access_token_claims(access_token)
    if not claims["account_id"] or not claims["chatgpt_user_id"]:
        raise CodexAgentIdentityError(
            f"JWT missing required account claims for token {anonymize_token(access_token)}"
        )

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
        "export_type": "codex",
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
