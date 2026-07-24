from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from services.account_service import account_service
from services.codex_oauth_browser_service import codex_oauth_browser_runner
from services.cpa_service import cpa_config, request_codex_auth_url, submit_codex_oauth_callback
from utils.helper import anonymize_token


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_id() -> str:
    return uuid.uuid4().hex


def _unique_tokens(tokens: list[str]) -> list[str]:
    return list(dict.fromkeys(str(token or "").strip() for token in tokens if str(token or "").strip()))


def _safe_error(exc: BaseException, token: str = "", *extra_secrets: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for value in (str(token or "").strip(), *(str(secret or "").strip() for secret in extra_secrets)):
        if value:
            replacement = anonymize_token(value) if value == str(token or "").strip() else "[REDACTED]"
            text = text.replace(value, replacement)
    return text[:500]


class CodexOAuthRetryService:
    """后台生成 Codex OAuth CPA 授权地址，并把 pending 状态写回账号池。"""

    def __init__(
        self,
        *,
        accounts=account_service,
        pools=cpa_config,
        auth_url_getter: Callable[[dict], dict] = request_codex_auth_url,
        callback_submitter: Callable[..., dict] = submit_codex_oauth_callback,
        browser_runner=codex_oauth_browser_runner,
    ) -> None:
        self.accounts = accounts
        self.pools = pools
        self.auth_url_getter = auth_url_getter
        self.callback_submitter = callback_submitter
        self.browser_runner = browser_runner
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._stop_requested: set[str] = set()

    def start(self, access_tokens: list[str], cpa_pool_id: str) -> dict[str, Any]:
        tokens = _unique_tokens(access_tokens)
        if not tokens:
            raise ValueError("access_tokens is required")
        pool_id = str(cpa_pool_id or "").strip()
        if not pool_id:
            raise ValueError("cpa_pool_id is required")
        pool = self.pools.get_pool(pool_id)
        if pool is None:
            raise ValueError(f"CPA pool not found: {pool_id}")

        job = {
            "job_id": _job_id(),
            "status": "queued",
            "pool_id": pool_id,
            "created_at": _now(),
            "updated_at": _now(),
            "total": len(tokens),
            "completed": 0,
            "succeeded": 0,
            "pending_callback": 0,
            "failed": 0,
            "stopped": 0,
            "results": [],
        }
        with self._lock:
            self._jobs[job["job_id"]] = dict(job)
        for token in tokens:
            self._update_account(token, {
                "status": "queued",
                "provider": "cpa",
                "pool_id": pool_id,
                "queued_at": _now(),
            })
        thread = threading.Thread(
            target=self._run,
            args=(job["job_id"], tokens, pool),
            daemon=True,
            name=f"codex-oauth-retry-{job['job_id'][:8]}",
        )
        thread.start()
        return self.get(job["job_id"]) or job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            return dict(job) if job else None

    def stop(self, job_id: str) -> dict[str, Any] | None:
        job_id = str(job_id or "").strip()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.get("status") in {"done", "failed", "stopped"}:
                return dict(job)
            self._stop_requested.add(job_id)
            job["status"] = "stopping"
            job["updated_at"] = _now()
            return dict(job)

    def recover_interrupted(self) -> int:
        recovered = 0
        interrupted_statuses = {"queued", "running", "capturing_callback", "submitting_callback"}
        for account in self.accounts.list_accounts():
            token = str(account.get("access_token") or "").strip()
            codex_oauth = account.get("codex_oauth") if isinstance(account.get("codex_oauth"), dict) else {}
            if not token or str(codex_oauth.get("status") or "").lower() not in interrupted_statuses:
                continue
            self._update_account(
                token,
                {
                    **codex_oauth,
                    "status": "failed",
                    "error": "服务重启导致 Codex OAuth 任务中断，请重新补跑",
                    "updated_at": _now(),
                    "recovered_interrupted": True,
                },
            )
            recovered += 1
        return recovered

    def finish_callback(self, access_token: str, callback_url: str, cpa_pool_id: str = "") -> dict[str, Any]:
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("access_token is required")
        callback = str(callback_url or "").strip()
        if not callback:
            raise ValueError("callback_url is required")
        account = self.accounts.get_account(token)
        if not account:
            raise ValueError("account not found")
        codex_oauth = account.get("codex_oauth") if isinstance(account.get("codex_oauth"), dict) else {}
        pool_id = str(cpa_pool_id or codex_oauth.get("pool_id") or "").strip()
        if not pool_id:
            raise ValueError("cpa_pool_id is required")
        pool = self.pools.get_pool(pool_id)
        if pool is None:
            raise ValueError(f"CPA pool not found: {pool_id}")

        self._update_account(token, {
            **codex_oauth,
            "status": "submitting_callback",
            "provider": "cpa",
            "pool_id": pool_id,
            "updated_at": _now(),
        })
        try:
            result = self.callback_submitter(pool, callback, import_account=True)
        except Exception as exc:
            error = _safe_error(exc, token, callback)
            self._update_account(token, {
                **codex_oauth,
                "status": "callback_failed",
                "provider": "cpa",
                "pool_id": pool_id,
                "error": error,
                "updated_at": _now(),
            })
            raise RuntimeError(error) from exc

        import_result = result.get("import_result") if isinstance(result.get("import_result"), dict) else {}
        auth_json = result.get("auth_json") if isinstance(result.get("auth_json"), dict) else {}
        imported_summary = {
            "added": int(import_result.get("added") or 0),
            "skipped": int(import_result.get("skipped") or 0),
        }
        self._update_account(token, {
            **codex_oauth,
            "status": "success",
            "provider": "cpa",
            "pool_id": pool_id,
            "auth_url": str(codex_oauth.get("auth_url") or ""),
            "state": str(codex_oauth.get("state") or ""),
            "callback_submitted_at": _now(),
            "updated_at": _now(),
            "import_result": imported_summary,
            "auth_email": str(auth_json.get("email") or ""),
        })
        try:
            items = self.accounts.list_accounts()
        except Exception:
            items = import_result.get("items") if isinstance(import_result, dict) else None
        return {
            "ok": True,
            "access_token": anonymize_token(token),
            "pool_id": pool_id,
            "auth_json": auth_json,
            "import_result": import_result or None,
            "items": items,
        }

    def capture_callback(
        self,
        access_token: str,
        provider: str,
        *,
        cpa_pool_id: str = "",
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("access_token is required")
        provider_name = str(provider or "").strip().lower().replace("-", "_")
        if not provider_name:
            raise ValueError("provider is required")
        account = self.accounts.get_account(token)
        if not account:
            raise ValueError("account not found")
        codex_oauth = account.get("codex_oauth") if isinstance(account.get("codex_oauth"), dict) else {}
        auth_url = str(codex_oauth.get("auth_url") or "").strip()
        if not auth_url:
            raise ValueError("account codex_oauth.auth_url is required")
        pool_id = str(cpa_pool_id or codex_oauth.get("pool_id") or "").strip()
        if not pool_id:
            raise ValueError("cpa_pool_id is required")
        if self.pools.get_pool(pool_id) is None:
            raise ValueError(f"CPA pool not found: {pool_id}")

        self._update_account(token, {
            **codex_oauth,
            "status": "capturing_callback",
            "provider": "cpa",
            "pool_id": pool_id,
            "browser_provider": provider_name,
            "updated_at": _now(),
        })
        try:
            browser_result = self.browser_runner.run(
                provider=provider_name,
                auth_url=auth_url,
                email=str(account.get("email") or ""),
                proxy=str(account.get("proxy") or ""),
                timeout_seconds=timeout_seconds,
            )
            callback_url = str(browser_result.get("callback_url") or "").strip()
            if not callback_url:
                raise RuntimeError("browser did not return callback_url")
            result = self.finish_callback(token, callback_url, pool_id)
            result["browser"] = {
                "provider": browser_result.get("provider") or provider_name,
                **(browser_result.get("browser") if isinstance(browser_result.get("browser"), dict) else {}),
            }
            return result
        except Exception as exc:
            error = _safe_error(exc, token, auth_url)
            self._update_account(token, {
                **codex_oauth,
                "status": "browser_capture_failed",
                "provider": "cpa",
                "pool_id": pool_id,
                "browser_provider": provider_name,
                "error": error,
                "updated_at": _now(),
            })
            raise RuntimeError(error) from exc

    def _run(self, job_id: str, tokens: list[str], pool: dict) -> None:
        self._set_job_status(job_id, "running")
        for token in tokens:
            if self._is_stop_requested(job_id):
                self._append_result(job_id, {"token": anonymize_token(token), "status": "stopped", "error": "stop requested"})
                self._update_account(token, {"status": "stopped", "provider": "cpa", "pool_id": pool.get("id"), "updated_at": _now()})
                continue

            account = self.accounts.get_account(token)
            if not account:
                self._append_result(job_id, {"token": anonymize_token(token), "status": "failed", "error": "account not found"})
                continue

            self._update_account(token, {"status": "running", "provider": "cpa", "pool_id": pool.get("id"), "updated_at": _now()})
            try:
                auth = self.auth_url_getter(pool)
                oauth_state = {
                    "status": "pending_callback",
                    "provider": "cpa",
                    "pool_id": str(pool.get("id") or ""),
                    "auth_url": str(auth.get("auth_url") or ""),
                    "state": str(auth.get("state") or ""),
                    "created_at": _now(),
                    "updated_at": _now(),
                }
                self._update_account(token, oauth_state)
                self._append_result(job_id, {
                    "token": anonymize_token(token),
                    "email": account.get("email"),
                    "status": "pending_callback",
                    "auth_url": oauth_state["auth_url"],
                    "state": oauth_state["state"],
                })
            except Exception as exc:
                error = _safe_error(exc, token)
                self._update_account(token, {
                    "status": "failed",
                    "provider": "cpa",
                    "pool_id": str(pool.get("id") or ""),
                    "error": error,
                    "updated_at": _now(),
                })
                self._append_result(job_id, {"token": anonymize_token(token), "email": account.get("email"), "status": "failed", "error": error})

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if self._is_stop_requested(job_id):
                job["status"] = "stopped"
            elif int(job.get("failed") or 0) > 0 and int(job.get("succeeded") or 0) == 0 and int(job.get("pending_callback") or 0) == 0:
                job["status"] = "failed"
            else:
                job["status"] = "done"
            job["updated_at"] = _now()
            self._stop_requested.discard(job_id)

    def _update_account(self, token: str, codex_oauth: dict[str, Any]) -> None:
        try:
            self.accounts.update_account(token, {"codex_oauth": codex_oauth}, quiet=True)
        except Exception:
            pass

    def _set_job_status(self, job_id: str, status: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = status
                job["updated_at"] = _now()

    def _is_stop_requested(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._stop_requested

    def _append_result(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            status = str(result.get("status") or "")
            job["results"] = [*job.get("results", []), result]
            job["completed"] = int(job.get("completed") or 0) + 1
            if status == "success":
                job["succeeded"] = int(job.get("succeeded") or 0) + 1
            elif status == "pending_callback":
                job["pending_callback"] = int(job.get("pending_callback") or 0) + 1
            elif status == "stopped":
                job["stopped"] = int(job.get("stopped") or 0) + 1
            else:
                job["failed"] = int(job.get("failed") or 0) + 1
            job["updated_at"] = _now()


codex_oauth_retry_service = CodexOAuthRetryService()
