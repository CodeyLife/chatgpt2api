#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import collections
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


SENSITIVE_KEYS = {
    "access_token",
    "access-token",
    "authorization",
    "callbackurl",
    "client_secret",
    "code",
    "cookie",
    "csrf",
    "csrftoken",
    "id_token",
    "openai-sentinel-so-token",
    "openai-sentinel-token",
    "password",
    "prepare_token",
    "refresh_token",
    "set-cookie",
    "signature",
    "state",
    "token",
}


def _sensitive_key(value: object) -> bool:
    key = str(value or "").strip().lower().replace("_", "-")
    compact = key.replace("-", "")
    return key in SENSITIVE_KEYS or compact in {item.replace("-", "").replace("_", "") for item in SENSITIVE_KEYS}


def _redacted(value: object) -> str:
    return f"<redacted:len={len(str(value or ''))}>"


def decode_sentinel_p(value: str) -> Any | None:
    text = str(value or "").strip()
    if not text:
        return None
    for prefix in ("gAAAAAC", "gAAAAAB"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.split("~", 1)[0]
    text += "=" * (-len(text) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8"))
    except Exception:
        return None


def redact_value(value: Any, key_hint: str = "") -> Any:
    if _sensitive_key(key_hint):
        return _redacted(value)
    if isinstance(value, dict):
        return {str(key): redact_value(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, key_hint) for item in value]
    return value


def short_body(text: str, limit: int = 260) -> Any | None:
    raw = str(text or "")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return raw[:limit]
    return redact_value(payload)


def header_dict(headers: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(headers, list):
        return out
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name") or "").strip()
        if not name:
            continue
        value = header.get("value")
        out[name] = _redacted(value) if _sensitive_key(name) else value
    return out


def sanitize_url(value: str) -> str:
    try:
        parsed = urlparse(str(value or ""))
    except Exception:
        return str(value or "")
    query = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, _redacted(val) if _sensitive_key(key) else val))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def classify(url: str) -> str:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc
    path = parsed.path
    if host == "browser-intake-datadoghq.com":
        return "datadog-rum"
    if "/ces/v1/" in path or host == "ab.chatgpt.com":
        return "frontend-telemetry"
    if "/api/auth/" in path:
        return "nextauth-oauth"
    if "auth.openai.com" in host:
        return "openai-auth"
    if "/sentinel/chat-requirements/" in path:
        return "chatgpt-sentinel"
    if "/backend-anon/" in path:
        return "chatgpt-anon-bootstrap"
    if "/backend-api/" in path:
        return "chatgpt-auth-bootstrap"
    if "codex" in path.lower():
        return "codex"
    return "other"


def _entries_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        log = payload.get("log")
        if isinstance(log, dict) and isinstance(log.get("entries"), list):
            return [entry for entry in log["entries"] if isinstance(entry, dict)]
        if isinstance(payload.get("entries"), list):
            return [entry for entry in payload["entries"] if isinstance(entry, dict)]
        if isinstance(payload.get("requests"), list):
            return [entry for entry in payload["requests"] if isinstance(entry, dict)]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def _request_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    request = entry.get("request")
    if isinstance(request, dict):
        return request
    return entry


def _response_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    response = entry.get("response")
    return response if isinstance(response, dict) else {}


def analyze_har_payload(payload: Any, *, source: str = "") -> dict[str, Any]:
    entries = _entries_from_payload(payload)
    domains: collections.Counter[str] = collections.Counter()
    classes: collections.Counter[str] = collections.Counter()
    summary: dict[str, Any] = {
        "source": source,
        "entry_count": len(entries),
        "domains": {},
        "classes": {},
        "requests": [],
        "fingerprints": [],
        "js_entrypoints": [],
    }
    js_seen: set[str] = set()
    for index, entry in enumerate(entries):
        request = _request_from_entry(entry)
        response = _response_from_entry(entry)
        url = str(request.get("url") or "")
        parsed = urlparse(url)
        request_class = classify(url)
        domains[parsed.netloc] += 1
        classes[request_class] += 1
        post_text = ""
        post_data = request.get("postData")
        if isinstance(post_data, dict):
            post_text = str(post_data.get("text") or "")
        elif isinstance(request.get("body"), str):
            post_text = str(request.get("body") or "")

        item = {
            "index": index,
            "class": request_class,
            "method": request.get("method"),
            "status": response.get("status"),
            "url": sanitize_url(url),
            "request_headers": header_dict(request.get("headers")),
            "post": short_body(post_text),
            "response_mime": (response.get("content") or {}).get("mimeType") if isinstance(response.get("content"), dict) else response.get("mimeType"),
            "response_size": len(str((response.get("content") or {}).get("text") or "")) if isinstance(response.get("content"), dict) else 0,
        }
        summary["requests"].append(item)

        try:
            body = json.loads(post_text) if post_text else None
        except Exception:
            body = None
        if isinstance(body, dict) and isinstance(body.get("p"), str):
            arr = decode_sentinel_p(body["p"])
            if isinstance(arr, list):
                summary["fingerprints"].append({"index": index, "source": "body.p", "url": sanitize_url(url), "array": arr})
                _append_js_entry(summary["js_entrypoints"], js_seen, arr)

        for header in request.get("headers") or []:
            if not isinstance(header, dict):
                continue
            if str(header.get("name") or "").lower() != "openai-sentinel-token":
                continue
            try:
                token = json.loads(str(header.get("value") or "{}"))
            except Exception:
                token = {}
            arr = decode_sentinel_p(str(token.get("p") or ""))
            if isinstance(arr, list):
                summary["fingerprints"].append({
                    "index": index,
                    "source": "openai-sentinel-token.p",
                    "url": sanitize_url(url),
                    "flow": token.get("flow"),
                    "array": arr,
                })
                _append_js_entry(summary["js_entrypoints"], js_seen, arr)

    summary["domains"] = dict(domains.most_common())
    summary["classes"] = dict(classes.most_common())
    return summary


def _append_js_entry(items: list[str], seen: set[str], fingerprint: list[Any]) -> None:
    if len(fingerprint) <= 5 or not isinstance(fingerprint[5], str):
        return
    value = fingerprint[5]
    if value in seen:
        return
    seen.add(value)
    items.append(value)


def analyze_har_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return analyze_har_payload(payload, source=str(path))


def write_summary(summary: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze ChatGPT/OpenAI HAR protocol traces with sensitive values redacted.")
    parser.add_argument("har", help="Path to HAR or Reqorder JSON export.")
    parser.add_argument("-o", "--output", default="docs/protocol_har_summary.json", help="Summary JSON output path.")
    args = parser.parse_args(argv)
    har_path = Path(args.har)
    output_path = Path(args.output)
    summary = analyze_har_file(har_path)
    write_summary(summary, output_path)
    print(
        f"wrote {output_path}: requests={len(summary['requests'])}, "
        f"fingerprints={len(summary['fingerprints'])}, js_entrypoints={len(summary['js_entrypoints'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
