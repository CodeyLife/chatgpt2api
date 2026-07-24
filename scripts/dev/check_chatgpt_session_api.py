#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from curl_cffi.requests import Session  # noqa: E402

from services import chatgpt_plan_service  # noqa: E402
from services.proxy_service import proxy_settings  # noqa: E402


SUBSCRIPTIONS_PATH = "/backend-api/subscriptions"


def _mask(value: str, left: int = 14, right: int = 8) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= left + right:
        return "***"
    return f"{text[:left]}...{text[-right:]}"


def _read_token_file(path: str, index: int) -> str:
    items: list[str] = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        token = ""
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    token = str(data.get("access_token") or data.get("accessToken") or data.get("token") or "")
            except Exception:
                token = ""
        if not token:
            token = line.rsplit("----", 1)[-1].rsplit("\t", 1)[-1]
        token = chatgpt_plan_service.normalize_token(token)
        if token:
            items.append(token)
    if not items:
        raise RuntimeError(f"token file has no usable token: {path}")
    if index < 0 or index >= len(items):
        raise RuntimeError(f"token index out of range: {index}, token_count={len(items)}")
    return items[index]


def _subscriptions(token: str, *, account_id: str = "", proxy: str = "", timeout: float = 15.0) -> dict[str, Any]:
    claims = chatgpt_plan_service.token_claims(token)
    account_id = str(account_id or claims.get("account_id") or "").strip()
    if not account_id:
        return {"ok": False, "error": "subscriptions endpoint requires --account-id or token chatgpt_account_id", **claims}
    session = Session(**proxy_settings.build_session_kwargs(proxy=proxy, verify=True, upstream=True))
    try:
        url = f"https://chatgpt.com{SUBSCRIPTIONS_PATH}?account_id={quote(account_id)}"
        headers = {
            "accept": "*/*",
            "authorization": f"Bearer {token}",
            "referer": "https://chatgpt.com/",
            "x-openai-target-path": SUBSCRIPTIONS_PATH,
            "x-openai-target-route": SUBSCRIPTIONS_PATH,
        }
        started = time.time()
        response = session.get(url, headers=headers, allow_redirects=False, timeout=max(1.0, timeout))
        text = str(getattr(response, "text", "") or "")
        try:
            payload = response.json()
        except Exception:
            payload = None
        return {
            "ok": 200 <= int(response.status_code) < 300,
            "http_status": int(response.status_code),
            "elapsed_ms": round((time.time() - started) * 1000),
            "account_id": account_id,
            "content_type": response.headers.get("content-type"),
            "payload": payload if isinstance(payload, (dict, list)) else None,
            "response_preview": "" if isinstance(payload, (dict, list)) else text[:500],
            **claims,
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", **claims}
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a ChatGPT Web accessToken against backend-api endpoints.")
    parser.add_argument("--token", default="", help="ChatGPT Web accessToken; Bearer prefix is accepted")
    parser.add_argument("--token-file", default="", help="Read token from a local file")
    parser.add_argument("--token-index", type=int, default=0)
    parser.add_argument("--endpoint", choices=["accounts-check", "subscriptions"], default="accounts-check")
    parser.add_argument("--account-id", default="")
    parser.add_argument("--proxy", default="", help="Optional proxy for this probe")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--show-token", action="store_true", help="Print the full token in output")
    args = parser.parse_args()

    token = chatgpt_plan_service.normalize_token(args.token)
    if not token and args.token_file:
        token = _read_token_file(args.token_file, args.token_index)
    if not token:
        print("missing --token or --token-file", file=sys.stderr)
        return 2

    if args.endpoint == "accounts-check":
        result = chatgpt_plan_service.check_account_plan(token, proxy=args.proxy, timeout=args.timeout)
    else:
        result = _subscriptions(token, account_id=args.account_id, proxy=args.proxy, timeout=args.timeout)
    result["token"] = token if args.show_token else _mask(token)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
