from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import quote, urlencode

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - dependency is present in normal runtime
    curl_requests = None

from services.account_service import account_service
from services.config import config
from utils.helper import anonymize_token


class ExtractLinkError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_secret(value: object) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= 8:
        return "***"
    return f"{raw[:4]}...{raw[-4:]}"


def _normalize_link_type(value: object, default: str = "pix") -> str:
    link_type = str(value or default or "pix").strip().lower()
    if link_type not in {"pix", "upi"}:
        raise ExtractLinkError("提链类型无效，仅支持 pix / upi")
    return link_type


def _is_extract_eligible(account: dict[str, Any] | None) -> bool:
    account = account if isinstance(account, dict) else {}
    check = account.get("chatgpt_plan_check") if isinstance(account.get("chatgpt_plan_check"), dict) else {}
    plan = str(
        check.get("current_plan_type")
        or account.get("current_plan_type")
        or account.get("plan_type")
        or account.get("type")
        or ""
    ).strip().lower()
    plus_trial_eligible = bool(check.get("plus_trial_eligible") or account.get("plus_trial_eligible"))
    return plan == "free" and plus_trial_eligible


def _flatten_result_fields(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    flattened: dict[str, Any] = {}
    for key in (
        "url",
        "long_url",
        "copy_paste",
        "image_url_png",
        "image_url_svg",
        "qr_url",
        "payment_link",
        "payment_method",
        "payment_link_type",
        "expires_at",
        "upi_url",
        "pix_code",
        "upi_id",
    ):
        value = result.get(key)
        if value:
            flattened[key] = value
    return flattened


def _parse_sse_lines(lines: Iterator[object]) -> Iterator[tuple[str, dict[str, Any]]]:
    event = "message"
    data_lines: list[str] = []
    for raw in lines:
        if raw is None:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")
        if line == "":
            if data_lines:
                text = "\n".join(data_lines)
                try:
                    data = json.loads(text)
                except Exception:
                    data = {"raw": text}
                yield event, data if isinstance(data, dict) else {"data": data}
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        text = "\n".join(data_lines)
        try:
            data = json.loads(text)
        except Exception:
            data = {"raw": text}
        yield event, data if isinstance(data, dict) else {"data": data}


class ExtractLinkService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._semaphore: threading.BoundedSemaphore | None = None
        self._executor_shape: tuple[int, int] | None = None

    def settings(self) -> dict[str, Any]:
        return dict(config.get_extract_link_settings())

    def public_settings(self) -> dict[str, Any]:
        settings = self.settings()
        settings["cdk"] = ""
        settings["has_cdk"] = bool(str(config.get_extract_link_settings().get("cdk") or "").strip())
        return settings

    def recover_interrupted(self) -> int:
        recovered = 0
        for account in account_service.list_accounts():
            token = str(account.get("access_token") or "").strip()
            current = account.get("extract_link") if isinstance(account.get("extract_link"), dict) else {}
            if not token or str(current.get("status") or "").lower() not in {"queued", "running"}:
                continue
            account_service.update_account(
                token,
                {
                    "extract_link": {
                        **current,
                        "ok": False,
                        "status": "failed",
                        "error": "服务重启导致提链任务中断，请重新提链",
                        "finished_at": _now_iso(),
                        "recovered_interrupted": True,
                    }
                },
                quiet=True,
            )
            recovered += 1
        return recovered

    def _runtime(self) -> tuple[ThreadPoolExecutor, threading.BoundedSemaphore]:
        settings = self.settings()
        workers = int(settings.get("workers") or 3)
        queue_limit = int(settings.get("queue_limit") or 500)
        shape = (workers, queue_limit)
        with self._lock:
            if self._executor is None or self._semaphore is None or self._executor_shape != shape:
                self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract-link")
                self._semaphore = threading.BoundedSemaphore(queue_limit)
                self._executor_shape = shape
            return self._executor, self._semaphore

    def _validate_settings(self, settings: dict[str, Any], *, cdk: str = "", link_type: str = "") -> tuple[str, str, str]:
        if not bool(settings.get("enabled")):
            raise ExtractLinkError("Plus 提链服务未启用")
        api_base = str(settings.get("api_base") or "").strip().rstrip("/")
        if not api_base:
            raise ExtractLinkError("提链服务地址未配置")
        cdk_value = str(cdk or settings.get("cdk") or "").strip()
        if not cdk_value:
            raise ExtractLinkError("提链 CDK 未配置")
        return api_base, cdk_value, _normalize_link_type(link_type or settings.get("link_type") or "pix")

    @staticmethod
    def _session():
        if curl_requests is None:
            raise ExtractLinkError("缺少 curl_cffi，无法请求提链服务")
        return curl_requests.Session()

    def query_cdk(self, *, cdk: str = "") -> dict[str, Any]:
        settings = self.settings()
        api_base, cdk_value, _ = self._validate_settings(settings, cdk=cdk)
        timeout = int(settings.get("request_timeout") or 30)
        session = self._session()
        try:
            response = session.get(
                f"{api_base}/api/cdk?{urlencode({'code': cdk_value})}",
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            try:
                payload = response.json()
            except Exception:
                payload = {"error": (response.text or "")[:300]}
            if response.status_code < 200 or response.status_code >= 300:
                raise ExtractLinkError(str((payload or {}).get("error") or f"HTTP {response.status_code}"))
            return payload if isinstance(payload, dict) else {}
        finally:
            session.close()

    def start(self, access_tokens: list[str], *, link_type: str = "", cdk: str = "", trigger: str = "manual") -> dict[str, Any]:
        tokens = list(dict.fromkeys(str(token or "").strip() for token in access_tokens if str(token or "").strip()))
        if not tokens:
            raise ExtractLinkError("access_tokens is required")
        settings = self.settings()
        _, cdk_value, normalized_link_type = self._validate_settings(settings, cdk=cdk, link_type=link_type)
        executor, semaphore = self._runtime()
        accepted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for token in tokens:
            account = account_service.get_account(token)
            if not account:
                skipped.append({"access_token": token, "reason": "account not found"})
                continue
            current = account.get("extract_link") if isinstance(account.get("extract_link"), dict) else {}
            if str(current.get("status") or "").lower() in {"queued", "running"}:
                skipped.append({"access_token": token, "reason": "extract link already running"})
                continue
            if not _is_extract_eligible(account):
                skipped.append({"access_token": token, "reason": "不是 free(可Plus试用) 账号，请先查询套餐确认资格"})
                continue
            if not semaphore.acquire(blocking=False):
                skipped.append({"access_token": token, "reason": "extract link queue is full"})
                continue
            job_id = str(uuid.uuid4())
            email = str(account.get("email") or "")
            state = {
                "ok": False,
                "status": "queued",
                "job_id": job_id,
                "link_type": normalized_link_type,
                "trigger": str(trigger or "manual"),
                "queued_at": _now_iso(),
                "message": "提链任务已入队",
            }
            account_service.update_account(token, {"extract_link": state}, quiet=True)
            executor.submit(
                self._run_one,
                token=token,
                email=email,
                link_type=normalized_link_type,
                cdk=cdk_value,
                job_id=job_id,
                semaphore=semaphore,
            )
            accepted.append({"access_token": token, "job_id": job_id, "email": email})
        return {
            "accepted": len(accepted),
            "skipped": len(skipped),
            "items": account_service.list_accounts(),
            "jobs": accepted,
            "skipped_items": skipped,
        }

    def _create_remote_job(self, *, token: str, link_type: str, cdk: str) -> dict[str, Any]:
        settings = self.settings()
        api_base, cdk_value, _ = self._validate_settings(settings, cdk=cdk, link_type=link_type)
        timeout = int(settings.get("request_timeout") or 30)
        session = self._session()
        try:
            response = session.post(
                f"{api_base}/api/extract",
                json={"link_type": link_type, "cdk": cdk_value, "token": token},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            try:
                payload = response.json()
            except Exception:
                payload = {"error": (response.text or "")[:300]}
            if response.status_code < 200 or response.status_code >= 300:
                raise ExtractLinkError(str((payload or {}).get("error") or f"HTTP {response.status_code}"))
            if not isinstance(payload, dict) or not payload.get("job_id"):
                raise ExtractLinkError(f"提链服务未返回 job_id: {payload}")
            return payload
        finally:
            session.close()

    def _iter_remote_events(self, *, remote_job_id: str, cdk: str) -> Iterator[tuple[str, dict[str, Any]]]:
        settings = self.settings()
        api_base, cdk_value, _ = self._validate_settings(settings, cdk=cdk)
        timeout = int(settings.get("event_timeout") or 180)
        session = self._session()
        try:
            response = session.get(
                f"{api_base}/api/jobs/{quote(remote_job_id, safe='')}/events?{urlencode({'cdk': cdk_value})}",
                headers={"Accept": "text/event-stream"},
                timeout=timeout,
                stream=True,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise ExtractLinkError(f"监听提链事件失败 HTTP {response.status_code}: {(response.text or '')[:300]}")
            yield from _parse_sse_lines(response.iter_lines())
        finally:
            session.close()

    def _run_one(
        self,
        *,
        token: str,
        email: str,
        link_type: str,
        cdk: str,
        job_id: str,
        semaphore: threading.BoundedSemaphore,
    ) -> None:
        logs: list[str] = []
        try:
            account_service.update_account(
                token,
                {
                    "extract_link": {
                        "ok": False,
                        "status": "running",
                        "job_id": job_id,
                        "link_type": link_type,
                        "started_at": _now_iso(),
                        "message": "正在创建远端提链任务",
                    }
                },
                quiet=True,
            )
            remote_job = self._create_remote_job(token=token, link_type=link_type, cdk=cdk)
            remote_job_id = str(remote_job.get("job_id") or "")
            account_service.update_account(
                token,
                {
                    "extract_link": {
                        "ok": False,
                        "status": "running",
                        "job_id": job_id,
                        "remote_job_id": remote_job_id,
                        "link_type": link_type,
                        "started_at": _now_iso(),
                        "message": "提链任务已创建，等待结果",
                        "cdk_remaining": remote_job.get("cdk_remaining"),
                    }
                },
                quiet=True,
            )

            last_event: dict[str, Any] | None = None
            for event, data in self._iter_remote_events(remote_job_id=remote_job_id, cdk=cdk):
                last_event = {"event": event, "data": data}
                if event == "log":
                    message = str((data or {}).get("message") or "")[:300]
                    if message:
                        logs.append(message)
                        account_service.update_account(
                            token,
                            {
                                "extract_link": {
                                    "ok": False,
                                    "status": "running",
                                    "job_id": job_id,
                                    "remote_job_id": remote_job_id,
                                    "link_type": link_type,
                                    "message": message,
                                    "logs": logs[-20:],
                                }
                            },
                            quiet=True,
                        )
                elif event == "result":
                    result = data.get("result") if isinstance(data, dict) else {}
                    if not isinstance(result, dict):
                        result = {}
                    flattened = _flatten_result_fields(result)
                    account_service.update_account(
                        token,
                        {
                            "extract_link": {
                                "ok": True,
                                "status": "success",
                                "job_id": job_id,
                                "remote_job_id": remote_job_id,
                                "link_type": link_type,
                                "finished_at": _now_iso(),
                                "result": result,
                                **flattened,
                                "logs": logs[-20:],
                            }
                        },
                        quiet=True,
                    )
                    return
                elif event == "error":
                    error_obj = data.get("error") if isinstance(data, dict) else None
                    message = error_obj.get("message") if isinstance(error_obj, dict) else None
                    raise ExtractLinkError(str(message or "提链任务失败"))
                elif event == "done":
                    break
            raise ExtractLinkError(f"提链事件流结束但未返回 result: {last_event}")
        except Exception as exc:
            account_service.update_account(
                token,
                {
                    "extract_link": {
                        "ok": False,
                        "status": "failed",
                        "job_id": job_id,
                        "link_type": link_type,
                        "finished_at": _now_iso(),
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                        "logs": logs[-20:],
                    }
                },
                quiet=True,
            )
            print(
                f"[extract-link] failed token={anonymize_token(token)} email={email or '-'} cdk={_mask_secret(cdk)} error={type(exc).__name__}: {str(exc)[:180]}",
                flush=True,
            )
        finally:
            semaphore.release()


extract_link_service = ExtractLinkService()
