#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.codex_oauth_service import CodexOAuthError, codex_oauth_service  # noqa: E402
from services.cpa_service import cpa_config, request_codex_auth_url, submit_codex_oauth_callback  # noqa: E402


def _mask_token(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _mask_token(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_token(item) for item in value]
    if isinstance(value, str) and len(value) > 32:
        return f"{value[:10]}...{value[-6:]}"
    return value


def _pool(pool_id: str) -> dict:
    pool_id = str(pool_id or "").strip()
    pools = cpa_config.list_pools()
    if not pool_id:
        if len(pools) == 1:
            return pools[0]
        raise RuntimeError("--cpa-pool-id is required when configured pool count is not 1")
    pool = cpa_config.get_pool(pool_id)
    if not pool:
        raise RuntimeError(f"CPA pool not found: {pool_id}")
    return pool


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Codex OAuth flows using this project's local PKCE or configured CPA pool.",
    )
    parser.add_argument("--mode", choices=["local", "cpa"], default="local")
    parser.add_argument("--cpa-pool-id", default="")
    parser.add_argument("--callback-url", default="", help="Callback URL/code returned by the Codex OAuth login")
    parser.add_argument("--code-verifier", default="", help="Required with --mode local and --callback-url")
    parser.add_argument("--expected-state", default="")
    parser.add_argument("--prompt", default="login")
    parser.add_argument("--no-import", action="store_true", help="Do not import returned auth JSON into account pool")
    parser.add_argument("--show-secrets", action="store_true", help="Print full tokens/auth JSON")
    args = parser.parse_args()

    try:
        if args.mode == "cpa":
            pool = _pool(args.cpa_pool_id)
            if args.callback_url:
                result = submit_codex_oauth_callback(pool, args.callback_url, import_account=not args.no_import)
            else:
                result = request_codex_auth_url(pool)
        elif args.callback_url:
            result = codex_oauth_service.finish_oauth_callback(
                args.callback_url,
                args.code_verifier,
                expected_state=args.expected_state,
                import_account=not args.no_import,
            )
        else:
            result = codex_oauth_service.build_authorize_url(prompt=args.prompt)
    except (CodexOAuthError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    output = result if args.show_secrets else _mask_token(result)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
