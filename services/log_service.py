from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from services.config import DATA_DIR
from services.protocol.error_response import anthropic_error_response, openai_error_response
from services.timezone import beijing_from_timestamp_string, beijing_now_string
from utils.helper import anthropic_sse_stream, sse_json_stream

LOG_TYPE_CALL = "call"
LOG_TYPE_ACCOUNT = "account"
INTERNAL_RESPONSE_KEYS = {"_account_email", "_conversation_id"}
TEXT_RESPONSE_ENDPOINTS = {"/v1/chat/completions", "/v1/responses", "/v1/messages", "/v1/search"}
RESPONSE_PREVIEW_LIMIT = 1000
_UNSET = object()


def _close_iterator(items) -> None:
    close = getattr(items, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _prepend_item(first, items):
    try:
        yield first
        yield from items
    finally:
        _close_iterator(items)


class LogService:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _legacy_id(raw_line: str, line_number: int) -> str:
        payload = f"{line_number}:{raw_line}".encode("utf-8", errors="ignore")
        return hashlib.sha1(payload).hexdigest()[:24]

    def _parse_line(self, raw_line: str, line_number: int) -> dict[str, Any] | None:
        try:
            item = json.loads(raw_line)
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        parsed = dict(item)
        parsed["id"] = str(parsed.get("id") or self._legacy_id(raw_line, line_number))
        return parsed

    @staticmethod
    def _serialize_item(item: dict[str, Any]) -> str:
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _matches_filters(item: dict[str, Any], *, type: str = "", start_date: str = "", end_date: str = "") -> bool:
        t = str(item.get("time") or "")
        day = t[:10]
        if type and item.get("type") != type:
            return False
        if start_date and day < start_date:
            return False
        if end_date and day > end_date:
            return False
        return True

    def add(self, type: str, summary: str = "", detail: dict[str, Any] | None = None, **data: Any) -> None:
        item = {
            "id": uuid4().hex,
            "time": beijing_now_string(),
            "type": type,
            "summary": summary,
            "detail": detail or data,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(self._serialize_item(item) + "\n")

    def list(self, type: str = "", start_date: str = "", end_date: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for line_number in range(len(lines) - 1, -1, -1):
            item = self._parse_line(lines[line_number], line_number)
            if item is None:
                continue
            if not self._matches_filters(item, type=type, start_date=start_date, end_date=end_date):
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def delete(self, ids: list[str]) -> dict[str, int]:
        target_ids = {str(item or "").strip() for item in ids if str(item or "").strip()}
        if not self.path.exists() or not target_ids:
            return {"removed": 0}
        lines = self.path.read_text(encoding="utf-8").splitlines()
        kept_lines: list[str] = []
        removed = 0
        for line_number, raw_line in enumerate(lines):
            item = self._parse_line(raw_line, line_number)
            if item is None:
                kept_lines.append(raw_line)
                continue
            if str(item.get("id") or "") in target_ids:
                removed += 1
                continue
            kept_lines.append(self._serialize_item(item))
        content = "\n".join(kept_lines)
        if content:
            content += "\n"
        self.path.write_text(content, encoding="utf-8")
        return {"removed": removed}


log_service = LogService(DATA_DIR / "logs.jsonl")


def _collect_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                urls.append(item)
            elif key == "urls" and isinstance(item, list):
                urls.extend(str(url) for url in item if isinstance(url, str))
            else:
                urls.extend(_collect_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item))
    return urls


def _collect_account_emails(value: object) -> list[str]:
    emails: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"_account_email", "account_email"} and isinstance(item, str) and item.strip():
                emails.append(item.strip())
            else:
                emails.extend(_collect_account_emails(item))
    elif isinstance(value, list):
        for item in value:
            emails.extend(_collect_account_emails(item))
    return emails


def _collect_conversation_ids(value: object) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "_conversation_id" and isinstance(item, str) and item.strip():
                ids.append(item.strip())
            else:
                ids.extend(_collect_conversation_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.extend(_collect_conversation_ids(item))
    return ids


def _strip_internal_response_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_internal_response_fields(item)
            for key, item in value.items()
            if key not in INTERNAL_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_response_fields(item) for item in value]
    return value


def _request_excerpt(text: object, limit: int = 1000) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


@dataclass
class ResponseTextPreview:
    limit: int = RESPONSE_PREVIEW_LIMIT
    length: int = 0
    seen: bool = False
    _parts: list[str] = field(default_factory=list)

    def add(self, text: object, *, mark_seen: bool = True) -> None:
        if mark_seen:
            self.seen = True
        if not isinstance(text, str):
            return
        self.length += len(text)
        current = sum(len(part) for part in self._parts)
        remaining = self.limit - current
        if remaining <= 0:
            return
        self._parts.append(text[:remaining])

    def preview(self) -> str:
        return _request_excerpt("".join(self._parts), self.limit)


def _append_content_text(acc: ResponseTextPreview, content: object) -> None:
    if isinstance(content, str):
        acc.add(content)
        return
    if not isinstance(content, list):
        return
    for part in content:
        if isinstance(part, str):
            acc.add(part)
            continue
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"text", "input_text", "output_text", "text_delta"}:
            acc.add(part.get("text") or part.get("content") or part.get("delta") or "")


def _append_output_item_text(acc: ResponseTextPreview, item: object) -> None:
    if not isinstance(item, dict):
        return
    _append_content_text(acc, item.get("content"))
    if isinstance(item.get("text"), str):
        acc.add(item.get("text"))


def _append_result_text(acc: ResponseTextPreview, value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _append_result_text(acc, item)
        return
    if not isinstance(value, dict):
        return

    choices = value.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            _append_content_text(acc, message.get("content"))
            _append_content_text(acc, delta.get("content"))
        return

    event_type = str(value.get("type") or "")
    if event_type == "response.output_text.delta":
        acc.add(value.get("delta") or "")
        return
    if event_type == "response.output_text.done":
        acc.add(value.get("text") or "")
        return
    if event_type == "response.completed":
        response = value.get("response")
        if isinstance(response, dict):
            _append_result_text(acc, response)
        return
    if event_type == "content_block_delta":
        delta = value.get("delta")
        if isinstance(delta, dict):
            acc.add(delta.get("text") or "")
        return

    output = value.get("output")
    if isinstance(output, list):
        for item in output:
            _append_output_item_text(acc, item)
        return

    if "content" in value:
        _append_content_text(acc, value.get("content"))


def _append_stream_item_text(acc: ResponseTextPreview, item: object) -> None:
    if not isinstance(item, dict):
        return

    choices = item.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if "content" in delta:
                _append_content_text(acc, delta.get("content"))
        return

    event_type = str(item.get("type") or "")
    if event_type == "response.output_text.delta":
        acc.add(item.get("delta") or "")
        return
    if event_type == "response.output_text.done":
        text = item.get("text") or ""
        if acc.length == 0:
            acc.add(text)
        else:
            acc.seen = True
        return
    if event_type == "content_block_delta":
        delta = item.get("delta")
        if isinstance(delta, dict) and "text" in delta:
            acc.add(delta.get("text") or "")


def _response_preview_from_result(endpoint: str, result: object) -> ResponseTextPreview:
    acc = ResponseTextPreview()
    if endpoint in TEXT_RESPONSE_ENDPOINTS and result is not None:
        acc.seen = True
    _append_result_text(acc, result)
    return acc


def _image_error_response(exc: Exception) -> JSONResponse:
    from services.protocol.conversation import public_image_error_message

    message = public_image_error_message(str(exc))
    if "no available image quota" in message.lower():
        return openai_error_response(
            {
                "error": {
                    "message": "no available image quota",
                    "type": "insufficient_quota",
                    "param": None,
                    "code": "insufficient_quota",
                }
            },
            429,
        )
    if hasattr(exc, "to_openai_error") and hasattr(exc, "status_code"):
        return JSONResponse(status_code=int(exc.status_code), content=exc.to_openai_error())
    return openai_error_response(message, 502)


def _protocol_error_response(exc: Exception, status_code: int, sse: str) -> JSONResponse:
    message = str(exc)
    if sse == "anthropic":
        return anthropic_error_response(message, status_code)
    return openai_error_response(message, status_code)


def _next_item(items):
    try:
        return True, next(items)
    except StopIteration:
        return False, None


@dataclass
class LoggedCall:
    identity: dict[str, object]
    endpoint: str
    model: str
    summary: str
    started: float = field(default_factory=time.time)
    request_text: str = ""
    request_shape: dict[str, int] | None = None

    async def run(self, handler, *args, sse: str = "openai"):
        from services.protocol.conversation import ImageGenerationError

        try:
            result = await run_in_threadpool(handler, *args)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     conversation_id=getattr(exc, "conversation_id", ""))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""))
            if self.endpoint.startswith("/v1/images"):
                return _image_error_response(exc)
            return _protocol_error_response(exc, int(getattr(exc, "status_code", 502) or 502), sse)

        if isinstance(result, dict):
            self.log("调用完成", result)
            response = dict(result)
            response.pop("_account_email", None)
            return response

        sender = anthropic_sse_stream if sse == "anthropic" else sse_json_stream
        try:
            has_first, first = await run_in_threadpool(_next_item, result)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     conversation_id=getattr(exc, "conversation_id", ""))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""))
            if self.endpoint.startswith("/v1/images"):
                return _image_error_response(exc)
            return _protocol_error_response(exc, int(getattr(exc, "status_code", 502) or 502), sse)
        if not has_first:
            self.log(
                "流式调用结束",
                response_text="",
                response_text_length=0,
                response_text_seen=self.endpoint in TEXT_RESPONSE_ENDPOINTS,
            )
            return StreamingResponse(sender(()), media_type="text/event-stream")
        return StreamingResponse(sender(self.stream(_prepend_item(first, result))), media_type="text/event-stream")

    def stream(self, items):
        urls: list[str] = []
        account_emails: list[str] = []
        conversation_ids: list[str] = []
        response_preview = ResponseTextPreview()
        if self.endpoint in TEXT_RESPONSE_ENDPOINTS:
            response_preview.seen = True
        failed = False
        try:
            for item in items:
                urls.extend(_collect_urls(item))
                account_emails.extend(_collect_account_emails(item))
                conversation_ids.extend(_collect_conversation_ids(item))
                _append_stream_item_text(response_preview, item)
                yield _strip_internal_response_fields(item)
        except Exception as exc:
            failed = True
            self.log(
                "流式调用失败",
                status="failed",
                error=str(exc),
                urls=urls,
                account_email=(account_emails[0] if account_emails else getattr(exc, "account_email", "")),
                conversation_id=(conversation_ids[0] if conversation_ids else getattr(exc, "conversation_id", "")),
                response_text=response_preview.preview(),
                response_text_length=response_preview.length,
                response_text_seen=response_preview.seen,
            )
            if self.endpoint.startswith("/v1/images") and not hasattr(exc, "to_openai_error"):
                from services.protocol.conversation import ImageGenerationError, public_image_error_message

                raise ImageGenerationError(public_image_error_message(str(exc))) from exc
            raise
        finally:
            _close_iterator(items)
            if not failed:
                self.log("流式调用结束", urls=urls, account_email=account_emails[0] if account_emails else "",
                         conversation_id=conversation_ids[0] if conversation_ids else "",
                         response_text=response_preview.preview(),
                         response_text_length=response_preview.length,
                         response_text_seen=response_preview.seen)

    def log(self, suffix: str, result: object = None, status: str = "success", error: str = "",
            urls: list[str] | None = None, account_email: str = "", conversation_id: str = "",
            response_text: object = _UNSET, response_text_length: int | None = None,
            response_text_seen: bool = False) -> None:
        detail = {
            "key_id": self.identity.get("id"),
            "key_name": self.identity.get("name"),
            "role": self.identity.get("role"),
            "endpoint": self.endpoint,
            "model": self.model,
            "started_at": beijing_from_timestamp_string(self.started),
            "ended_at": beijing_now_string(),
            "duration_ms": int((time.time() - self.started) * 1000),
            "status": status,
        }
        request_excerpt = _request_excerpt(self.request_text)
        if request_excerpt:
            detail["request_text"] = request_excerpt
        if self.request_shape:
            detail["request_shape"] = self.request_shape
        if error:
            detail["error"] = error
        if response_text is _UNSET:
            response_preview = _response_preview_from_result(self.endpoint, result)
        else:
            explicit_text = str(response_text or "")
            response_preview = ResponseTextPreview()
            response_preview.seen = bool(response_text_seen or explicit_text)
            response_preview.add(explicit_text, mark_seen=response_preview.seen)
            response_preview.length = int(response_text_length if response_text_length is not None else len(explicit_text))
        preview = response_preview.preview()
        if preview:
            detail["response_preview"] = preview
        if response_preview.seen:
            detail["response_text_length"] = response_preview.length
        email = str(account_email or "").strip()
        if not email:
            emails = _collect_account_emails(result)
            email = emails[0] if emails else ""
        if email:
            detail["account_email"] = email
        conv_id = str(conversation_id or "").strip()
        if not conv_id:
            conv_ids = _collect_conversation_ids(result)
            conv_id = conv_ids[0] if conv_ids else ""
        if conv_id:
            detail["conversation_id"] = conv_id
        collected_urls = [*(urls or []), *_collect_urls(result)]
        if collected_urls and not self.endpoint.startswith("/v1/search"):
            detail["urls"] = list(dict.fromkeys(collected_urls))
        log_service.add(LOG_TYPE_CALL, f"{self.summary}{suffix}", detail)
