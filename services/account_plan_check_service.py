from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from random import random
from typing import Any

from services.account_service import account_service
from services.chatgpt_plan_service import chatgpt_plan_service
from utils.helper import anonymize_token


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountPlanCheckService:
    def __init__(
        self,
        *,
        workers: int = 3,
        queue_limit: int = 500,
        min_interval: float = 0.4,
        jitter: float = 0.3,
        registration_recheck_delay: float = 2.0,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="account-plan-check")
        self._semaphore = threading.BoundedSemaphore(max(max(1, workers), queue_limit))
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0
        self._min_interval = max(0.0, float(min_interval or 0.0))
        self._jitter = max(0.0, float(jitter or 0.0))
        self._registration_recheck_delay = max(0.0, float(registration_recheck_delay or 0.0))

    def queue_settings(self) -> dict[str, Any]:
        return {
            "min_interval": self._min_interval,
            "jitter": self._jitter,
            "registration_recheck_delay": self._registration_recheck_delay,
        }

    def recover_interrupted(self) -> int:
        recovered = 0
        for account in account_service.list_accounts():
            token = str(account.get("access_token") or "").strip()
            current = account.get("chatgpt_plan_check") if isinstance(account.get("chatgpt_plan_check"), dict) else {}
            if not token or str(current.get("status") or "").lower() not in {"queued", "running"}:
                continue
            account_service.update_account(
                token,
                {
                    "chatgpt_plan_check": {
                        **current,
                        "ok": False,
                        "status": "failed",
                        "error": "服务重启导致套餐查询中断，请重新查询",
                        "finished_at": _now_iso(),
                        "recovered_interrupted": True,
                    }
                },
                quiet=True,
            )
            recovered += 1
        return recovered

    def status_snapshot(self, *, limit: int = 5000) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for account in account_service.list_accounts()[: max(1, min(5000, int(limit or 5000)))]:
            check = account.get("chatgpt_plan_check") if isinstance(account.get("chatgpt_plan_check"), dict) else {}
            extract = account.get("extract_link") if isinstance(account.get("extract_link"), dict) else {}
            items.append(
                {
                    "access_token": account.get("access_token"),
                    "email": account.get("email") or "",
                    "updated_at": account.get("updated_at") or "",
                    "plan_type": account.get("plan_type") or account.get("type") or "",
                    "current_plan_type": check.get("current_plan_type") or account.get("current_plan_type") or "",
                    "plan_check_status": check.get("status") or "",
                    "plan_check_trigger": check.get("trigger") or "",
                    "plan_check_job_id": check.get("job_id") or "",
                    "plan_check_queued_at": check.get("queued_at") or "",
                    "plan_check_started_at": check.get("started_at") or "",
                    "plan_check_completed_at": check.get("finished_at") or "",
                    "plan_check_ok": bool(check.get("ok")),
                    "plan_check_error": check.get("error") or "",
                    "plan_checked_at": check.get("checked_at") or "",
                    "plan_last_success_at": check.get("plan_last_success_at") or "",
                    "plus_trial_eligible": bool(check.get("plus_trial_eligible") or account.get("plus_trial_eligible")),
                    "plan_check_network_route": check.get("network_route") or "",
                    "extract_link_status": extract.get("status") or "",
                    "extract_link_ok": bool(extract.get("ok")),
                    "extract_link_type": extract.get("link_type") or "",
                    "extract_link_job_id": extract.get("job_id") or "",
                    "extract_link_message": extract.get("message") or "",
                    "extract_link_error": extract.get("error") or "",
                    "extract_link_long_url": extract.get("long_url") or "",
                    "extract_link_copy_paste": extract.get("copy_paste") or "",
                    "extract_link_image_url_png": extract.get("image_url_png") or "",
                    "extract_link_image_url_svg": extract.get("image_url_svg") or "",
                    "extract_link_expires_at": extract.get("expires_at") or "",
                    "extract_link_payment_method": extract.get("payment_method") or "",
                    "extract_link_payment_link_type": extract.get("payment_link_type") or "",
                    "extract_link_checked_at": extract.get("checked_at") or "",
                    "extract_link_completed_at": extract.get("finished_at") or "",
                }
            )
        return {"items": items, "total": len(items)}

    def start(self, access_tokens: list[str], *, proxy: str = "", trigger: str = "manual") -> dict[str, Any]:
        tokens = list(dict.fromkeys(str(token or "").strip() for token in access_tokens if str(token or "").strip()))
        if not tokens:
            raise ValueError("access_tokens is required")
        jobs: list[dict[str, str]] = []
        skipped_items: list[dict[str, str]] = []
        for token in tokens:
            account = account_service.get_account(token)
            if not account:
                skipped_items.append({"access_token": token, "reason": "account not found"})
                continue
            current = account.get("chatgpt_plan_check") if isinstance(account.get("chatgpt_plan_check"), dict) else {}
            if str(current.get("status") or "").lower() in {"queued", "running"}:
                skipped_items.append({"access_token": token, "reason": "plan check already running"})
                continue
            if not self._semaphore.acquire(blocking=False):
                skipped_items.append({"access_token": token, "reason": "plan check queue is full"})
                continue
            job_id = str(uuid.uuid4())
            account_service.update_account(
                token,
                {
                    "chatgpt_plan_check": {
                        "ok": False,
                        "status": "queued",
                        "job_id": job_id,
                        "queued_at": _now_iso(),
                        "message": "套餐复查已入队",
                    }
                },
                quiet=True,
            )
            self._executor.submit(self._run_one, token=token, proxy=proxy, job_id=job_id, trigger=trigger)
            jobs.append({"access_token": token, "job_id": job_id, "email": str(account.get("email") or "")})
        return {
            "accepted": len(jobs),
            "skipped": len(skipped_items),
            "jobs": jobs,
            "skipped_items": skipped_items,
            "items": account_service.list_accounts(),
        }

    def _wait_for_rate_slot(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._min_interval + (random() * self._jitter)
        if delay > 0:
            time.sleep(delay)

    def _run_one(self, *, token: str, proxy: str, job_id: str, trigger: str = "manual") -> None:
        try:
            account = account_service.get_account(token) or {}
            selected_proxy = str(proxy or account.get("proxy") or "").strip()
            account_service.update_account(
                token,
                {
                    "chatgpt_plan_check": {
                        "ok": False,
                        "status": "running",
                        "job_id": job_id,
                        "started_at": _now_iso(),
                        "message": "正在查询 ChatGPT 套餐和 Plus 资格",
                    }
                },
                quiet=True,
            )
            self._wait_for_rate_slot()
            result = chatgpt_plan_service.check_account_plan(token, proxy=selected_proxy, account=account)
            if (
                str(trigger or "").lower() == "registration_auto"
                and result.get("ok")
                and str(result.get("current_plan_type") or "").lower() == "free"
                and not bool(result.get("plus_trial_eligible"))
                and self._registration_recheck_delay > 0
            ):
                time.sleep(self._registration_recheck_delay)
                account = account_service.get_account(token) or account
                self._wait_for_rate_slot()
                recheck = chatgpt_plan_service.check_account_plan(
                    token,
                    proxy=selected_proxy,
                    account=account,
                    max_attempts=1,
                )
                if recheck.get("ok"):
                    result = {
                        **recheck,
                        "registration_rechecked": True,
                        "registration_first_check": result,
                    }
                else:
                    result = {
                        **result,
                        "registration_rechecked": True,
                        "registration_recheck_error": recheck.get("error"),
                        "registration_recheck_http_status": recheck.get("http_status"),
                    }
            payload = chatgpt_plan_service.account_payload_from_plan_result(result)
            if result.get("ok"):
                plan_status = {
                    **result,
                    "status": "success",
                    "job_id": job_id,
                    "finished_at": _now_iso(),
                }
                account_service.update_account(token, {**payload, "chatgpt_plan_check": plan_status}, quiet=True)
            else:
                account_service.update_account(
                    token,
                    {
                        "chatgpt_plan_check": {
                            **result,
                            "ok": False,
                            "status": "failed",
                            "job_id": job_id,
                            "finished_at": _now_iso(),
                        }
                    },
                    quiet=True,
                )
        except Exception as exc:
            account_service.update_account(
                token,
                {
                    "chatgpt_plan_check": {
                        "ok": False,
                        "status": "failed",
                        "job_id": job_id,
                        "finished_at": _now_iso(),
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                },
                quiet=True,
            )
            print(f"[account-plan-check] failed token={anonymize_token(token)} error={type(exc).__name__}: {str(exc)[:180]}", flush=True)
        finally:
            self._semaphore.release()


account_plan_check_service = AccountPlanCheckService()
