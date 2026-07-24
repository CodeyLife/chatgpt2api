from __future__ import annotations

import io
import json
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.account_service import account_service
from services.codex_oauth_retry_service import codex_oauth_retry_service
from services.config import DATA_DIR
from services.cpa_service import cpa_config, list_remote_files, request_codex_auth_url
from utils.helper import anonymize_token


CODEX_CREDENTIAL_DIR = DATA_DIR / "codex_credentials"
CODEX_LOG_DIR = DATA_DIR / "codex_retry_logs"
CODEX_INDEX_FILE = CODEX_CREDENTIAL_DIR / "index.json"


class CodexManagementError(RuntimeError):
    pass


class CodexRetryStopped(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(value: str) -> str:
    candidate = Path(str(value or "").strip()).name
    candidate = re.sub(r"[^A-Za-z0-9._@+-]+", "_", candidate).strip("._")
    if not candidate:
        raise CodexManagementError("filename 为空")
    if not candidate.endswith(".json"):
        raise CodexManagementError("仅支持 JSON 凭证文件")
    return candidate


def _safe_email_slug(email: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._@+-]+", "_", str(email or "").strip())[:120].strip("._")
    return slug or "unknown"


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class CodexManagementService:
    def __init__(self, *, accounts=account_service, oauth_retry=codex_oauth_retry_service, pools=cpa_config) -> None:
        self.accounts = accounts
        self.oauth_retry = oauth_retry
        self.pools = pools
        self._lock = threading.RLock()
        self._retrying: set[str] = set()
        self._stop_requested: set[str] = set()
        self._running_threads: dict[str, int] = {}

    def list(self) -> dict[str, Any]:
        entries = self._load_index()
        by_email = {str(item.get("email") or "").strip().lower(): item for item in self.accounts.list_accounts()}
        accounts: list[dict[str, Any]] = []
        for entry in sorted(entries.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True):
            email = str(entry.get("email") or "").strip()
            account = by_email.get(email.lower()) if email else None
            codex_oauth = account.get("codex_oauth") if isinstance(account, dict) and isinstance(account.get("codex_oauth"), dict) else {}
            accounts.append(
                {
                    **entry,
                    "codex_status": codex_oauth.get("status") or entry.get("codex_status") or "",
                    "codex_error": codex_oauth.get("error") or entry.get("codex_error") or "",
                    "retrying": self.is_retrying(email),
                    "account_present": bool(account),
                }
            )
        exported = sum(1 for item in accounts if int(item.get("export_count") or 0) > 0)
        return {
            "summary": {
                "total": len(accounts),
                "exported": exported,
                "unexported": max(0, len(accounts) - exported),
                "retrying": len(self._retrying),
            },
            "accounts": accounts,
        }

    def log_path(self, email: str) -> Path:
        CODEX_LOG_DIR.mkdir(parents=True, exist_ok=True)
        return CODEX_LOG_DIR / f"codex-retry-{_safe_email_slug(email)}.log"

    def read_retry_log(self, email: str, *, max_bytes: int = 50_000) -> dict[str, Any]:
        path = self.log_path(email)
        if not path.exists():
            return {"ok": True, "log": "", "running": self.is_retrying(email)}
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            content = handle.read().decode("utf-8", errors="replace")
        return {"ok": True, "log": content, "running": self.is_retrying(email)}

    def save_credential(
        self,
        auth_json: dict[str, Any],
        *,
        source: str = "local",
        cpa_pool_id: str = "",
        cpa_filename: str = "",
        filename: str = "",
    ) -> str:
        if not isinstance(auth_json, dict):
            raise CodexManagementError("auth_json 必须是对象")
        email = str(auth_json.get("email") or "").strip()
        name = _safe_filename(filename) if filename else self._new_credential_filename(email)
        CODEX_CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
        path = CODEX_CREDENTIAL_DIR / name
        path.write_bytes(_json_bytes(auth_json))
        with self._lock:
            index = self._load_index()
            existing = index.get(name) if isinstance(index.get(name), dict) else {}
            index[name] = {
                **existing,
                "filename": name,
                "email": email,
                "source": str(source or "local"),
                "cpa_pool_id": str(cpa_pool_id or existing.get("cpa_pool_id") or ""),
                "cpa_filename": str(cpa_filename or existing.get("cpa_filename") or ""),
                "created_at": existing.get("created_at") or _now(),
                "updated_at": _now(),
                "export_count": int(existing.get("export_count") or 0),
                "exported_at": existing.get("exported_at") or "",
                "size": path.stat().st_size,
            }
            self._save_index(index)
        return name

    def read_credential(self, filename: str) -> tuple[bytes, str]:
        name = _safe_filename(filename)
        path = CODEX_CREDENTIAL_DIR / name
        if not path.exists() or not path.is_file():
            raise CodexManagementError("凭证文件不存在")
        return path.read_bytes(), name

    def mark_exported(self, filename: str) -> None:
        name = _safe_filename(filename)
        with self._lock:
            index = self._load_index()
            entry = index.get(name) if isinstance(index.get(name), dict) else {"filename": name}
            entry["export_count"] = int(entry.get("export_count") or 0) + 1
            entry["exported_at"] = _now()
            entry["updated_at"] = _now()
            index[name] = entry
            self._save_index(index)

    def reset_exported(self, filename: str) -> None:
        name = _safe_filename(filename)
        with self._lock:
            index = self._load_index()
            if name not in index and not (CODEX_CREDENTIAL_DIR / name).exists():
                raise CodexManagementError("凭证文件不存在")
            entry = index.get(name) if isinstance(index.get(name), dict) else {"filename": name}
            entry["export_count"] = 0
            entry["exported_at"] = ""
            entry["updated_at"] = _now()
            index[name] = entry
            self._save_index(index)

    def delete_credential(self, filename: str) -> bool:
        name = _safe_filename(filename)
        path = CODEX_CREDENTIAL_DIR / name
        deleted = False
        if path.exists() and path.is_file():
            path.unlink()
            deleted = True
        with self._lock:
            index = self._load_index()
            deleted = bool(index.pop(name, None)) or deleted
            self._save_index(index)
        return deleted

    def download_bulk(self, filenames: list[str]) -> tuple[bytes, str, str]:
        selected = self._unique_filenames(filenames, limit=1000)
        bundle: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for name in selected:
            try:
                content, real_name = self.read_credential(name)
                bundle.append({"filename": real_name, "data": json.loads(content.decode("utf-8"))})
                self.mark_exported(real_name)
            except Exception as exc:
                errors.append({"filename": name, "error": f"{type(exc).__name__}: {exc}"})
        payload: dict[str, Any] = {
            "exported_at": _now(),
            "count": len(bundle),
            "credentials": bundle,
        }
        if errors:
            payload["errors"] = errors
        filename = f"codex-bulk-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        return _json_bytes(payload), filename, "application/json"

    def download_from_cpa(self, filename: str) -> tuple[bytes, str]:
        local_name = _safe_filename(filename)
        content, _ = self.read_credential(local_name)
        try:
            local = json.loads(content.decode("utf-8"))
        except Exception:
            local = {}
        with self._lock:
            entry = self._load_index().get(local_name) or {}
        pool = self._resolve_pool(str(entry.get("cpa_pool_id") or ""))
        remote_name = str(entry.get("cpa_filename") or "").strip() or self._match_cpa_filename(pool, str(local.get("email") or entry.get("email") or ""), local_name)
        text, cpa_name = self._fetch_remote_auth_text(pool, remote_name)
        self.mark_exported(local_name)
        return text.encode("utf-8"), cpa_name

    def download_bulk_from_cpa(self, filenames: list[str]) -> tuple[bytes, str, str]:
        selected = self._unique_filenames(filenames, limit=1000)
        errors: list[dict[str, str]] = []
        added: list[dict[str, str]] = []
        used_names: set[str] = set()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in selected:
                try:
                    content, cpa_name = self.download_from_cpa(name)
                    arcname = self._unique_archive_name(cpa_name, used_names)
                    archive.writestr(arcname, content)
                    added.append({"local_filename": name, "cpa_filename": cpa_name})
                except Exception as exc:
                    errors.append({"filename": name, "error": f"{type(exc).__name__}: {exc}"})
            archive.writestr(
                "manifest.json",
                json.dumps({"exported_at": _now(), "source": "cpa", "count": len(added), "files": added, "errors": errors}, ensure_ascii=False, indent=2) + "\n",
            )
        if not added:
            raise CodexManagementError("没有成功从 CPA 下载任何凭证")
        return buf.getvalue(), f"codex-cpa-bulk-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip", "application/zip"

    def reserve(self, email: str) -> bool:
        key = str(email or "").strip().lower()
        if not key:
            return False
        with self._lock:
            if key in self._retrying:
                return False
            self._stop_requested.discard(key)
            self._retrying.add(key)
            return True

    def release(self, email: str) -> None:
        key = str(email or "").strip().lower()
        with self._lock:
            self._retrying.discard(key)
            self._running_threads.pop(key, None)
            self._stop_requested.discard(key)

    def is_retrying(self, email: str) -> bool:
        with self._lock:
            return str(email or "").strip().lower() in self._retrying

    def request_stop(self, email: str) -> dict[str, Any]:
        key = str(email or "").strip().lower()
        if not key:
            return {"ok": False, "error": "email 为空", "status": 400}
        account = self._account_by_email(email)
        if account is None:
            return {"ok": False, "error": f"账号不存在: {email}", "status": 404}
        with self._lock:
            running = key in self._retrying
            self._stop_requested.add(key)
        self._update_codex_status(account, "stopped", "用户手动停止 Codex 补跑")
        self._append_log(email, "[WARNING] [Codex 补跑] 用户手动停止，已发送停止信号")
        if not running:
            return {"ok": True, "message": "未发现运行中的补跑，已标记为已停止", "state": "stopped", "running": False}
        return {"ok": True, "message": "已发送停止信号，当前步骤结束后停止", "state": "stopped", "running": True, "injected": False}

    def reset_retrying(self, email: str, status: str = "failed") -> dict[str, Any]:
        account = self._account_by_email(email)
        if account is None:
            raise CodexManagementError(f"账号不存在: {email}")
        raw_status = str(status or "failed").strip().lower()
        if raw_status in {"", "none", "null", "clear"}:
            raw_status = "empty"
        if raw_status not in {"failed", "skipped", "empty"}:
            raise CodexManagementError("status 仅支持 failed/skipped/empty")
        new_status = "" if raw_status == "empty" else raw_status
        self._update_codex_status(account, new_status, None if raw_status == "empty" else "用户手动重置补跑中状态")
        self.release(email)
        self._append_log(email, f"[WARNING] [Codex 补跑] 用户手动重置补跑中状态，当前状态={new_status or '空'}")
        return {"ok": True, "message": "已重置补跑中状态", "status": new_status}

    def retry(self, email: str, *, provider: str = "", cpa_pool_id: str = "", clear_log: bool = True) -> dict[str, Any]:
        account = self._account_by_email(email)
        if account is None:
            raise CodexManagementError(f"账号不存在: {email}")
        if not self.reserve(email):
            raise CodexManagementError("该账号正在补跑中，请稍候")
        if clear_log:
            self.log_path(email).write_text("", encoding="utf-8")
        self._update_codex_status(account, "retrying", None)
        thread = threading.Thread(
            target=self.run_worker,
            kwargs={"email": email, "provider": provider, "cpa_pool_id": cpa_pool_id, "clear_log": False},
            name=f"codex-retry-{email}",
            daemon=True,
        )
        thread.start()
        return {"ok": True, "message": "已在后台开始补跑，稍后刷新查看"}

    def retry_bulk(self, emails: list[str], *, workers: int = 1, provider: str = "", cpa_pool_id: str = "") -> dict[str, Any]:
        targets = [str(item or "").strip() for item in emails if str(item or "").strip()]
        if not targets:
            raise CodexManagementError("emails 必须是非空数组")
        if len(targets) > 500:
            raise CodexManagementError("单次最多选择 500 个账号")
        workers = max(1, min(16, int(workers or 1)))
        selected: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        seen: set[str] = set()
        for email in targets:
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            account = self._account_by_email(email)
            if account is None:
                skipped.append({"email": email, "reason": "账号不存在"})
                continue
            if not self.reserve(email):
                skipped.append({"email": email, "reason": "正在补跑中"})
                continue
            self._update_codex_status(account, "retrying", None)
            self.log_path(email).write_text(f"{datetime.now().strftime('%H:%M:%S')} [INFO] [Codex 批量补跑] 已加入批量任务\n", encoding="utf-8")
            selected.append({"email": email})
        if not selected:
            raise CodexManagementError("没有可补跑的账号")
        batch_id = datetime.now().strftime("%Y%m%d-%H%M%S")

        def runner() -> None:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"codex-bulk-{batch_id}") as executor:
                futures = [
                    executor.submit(self.run_worker, email=item["email"], provider=provider, cpa_pool_id=cpa_pool_id, batch_label=f"{batch_id} #{index}/{len(selected)}", clear_log=False)
                    for index, item in enumerate(selected, 1)
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass

        threading.Thread(target=runner, name=f"codex-bulk-dispatch-{batch_id}", daemon=True).start()
        return {"ok": True, "message": f"已开始批量补跑 {len(selected)} 个账号，并发 {workers}", "started": selected, "started_count": len(selected), "skipped": skipped, "batch_id": batch_id}

    def run_worker(self, *, email: str, provider: str = "", cpa_pool_id: str = "", batch_label: str | None = None, clear_log: bool = False) -> dict[str, Any]:
        key = str(email or "").strip().lower()
        result: dict[str, Any] = {"ok": False, "status": "failed", "message": "Codex 补跑未返回结果"}
        if clear_log:
            self.log_path(email).write_text("", encoding="utf-8")
        try:
            with self._lock:
                self._running_threads[key] = threading.get_ident()
            self._check_stop(email)
            account = self._account_by_email(email)
            if account is None:
                raise CodexManagementError(f"账号不存在: {email}")
            token = str(account.get("access_token") or "").strip()
            if not token:
                raise CodexManagementError("账号 access_token 为空")
            selected_provider = str(provider or self._default_browser_provider()).strip() or "browser_use"
            pool = self._resolve_pool(cpa_pool_id or str((account.get("codex_oauth") or {}).get("pool_id") or ""))
            self._append_log(email, f"[INFO] [Codex 补跑] 开始 email={email}")
            if batch_label:
                self._append_log(email, f"[INFO] [Codex 补跑] 批量任务 {batch_label}")
            codex_oauth = account.get("codex_oauth") if isinstance(account.get("codex_oauth"), dict) else {}
            if not str(codex_oauth.get("auth_url") or "").strip():
                auth = request_codex_auth_url(pool)
                codex_oauth = {
                    "status": "pending_callback",
                    "provider": "cpa",
                    "pool_id": str(pool.get("id") or ""),
                    "auth_url": str(auth.get("auth_url") or ""),
                    "state": str(auth.get("state") or ""),
                    "created_at": _now(),
                    "updated_at": _now(),
                }
                self.accounts.update_account(token, {"codex_oauth": codex_oauth}, quiet=True)
                self._append_log(email, "[INFO] [Codex 补跑] 已生成 CPA Codex 授权地址")
            self._check_stop(email)
            self._append_log(email, f"[INFO] [Codex 补跑] 使用浏览器驱动捕获 callback provider={selected_provider}")
            result = self.oauth_retry.capture_callback(token, selected_provider, cpa_pool_id=str(pool.get("id") or ""))
            auth_json = result.get("auth_json") if isinstance(result.get("auth_json"), dict) else {}
            filename = ""
            if auth_json:
                filename = self.save_credential(auth_json, source="cpa", cpa_pool_id=str(pool.get("id") or ""))
                self._append_log(email, f"[INFO] [Codex 补跑] 已保存凭证文件 {filename}")
            self._append_log(email, "[INFO] [Codex 补跑] 成功")
            return {**result, "status": "success", "credential_filename": filename}
        except CodexRetryStopped as exc:
            result = {"ok": False, "status": "stopped", "message": str(exc) or "用户手动停止 Codex 补跑"}
            account = self._account_by_email(email)
            if account:
                self._update_codex_status(account, "stopped", result["message"])
            self._append_log(email, f"[WARNING] [Codex 补跑] 已停止: {result['message']}")
            return result
        except Exception as exc:
            result = {"ok": False, "status": "failed", "message": f"{type(exc).__name__}: {str(exc)[:500]}"}
            account = self._account_by_email(email)
            if account:
                self._update_codex_status(account, "failed", result["message"])
            self._append_log(email, f"[ERROR] [Codex 补跑] 失败: {result['message']}")
            return result
        finally:
            self.release(email)

    def finish_callback_and_save(self, token: str, callback_url: str, pool_id: str = "") -> dict[str, Any]:
        result = self.oauth_retry.finish_callback(token, callback_url, pool_id)
        auth_json = result.get("auth_json") if isinstance(result.get("auth_json"), dict) else {}
        if auth_json:
            filename = self.save_credential(auth_json, source="cpa", cpa_pool_id=str(result.get("pool_id") or pool_id or ""))
            result["credential_filename"] = filename
        return result

    def capture_callback_and_save(
        self,
        token: str,
        provider: str,
        *,
        cpa_pool_id: str = "",
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        result = self.oauth_retry.capture_callback(
            token,
            provider,
            cpa_pool_id=cpa_pool_id,
            timeout_seconds=timeout_seconds,
        )
        auth_json = result.get("auth_json") if isinstance(result.get("auth_json"), dict) else {}
        if auth_json:
            filename = self.save_credential(auth_json, source="cpa", cpa_pool_id=str(result.get("pool_id") or cpa_pool_id or ""))
            result["credential_filename"] = filename
        return result

    def save_cpa_callback_result(self, result: dict[str, Any], pool_id: str = "") -> dict[str, Any]:
        auth_json = result.get("auth_json") if isinstance(result.get("auth_json"), dict) else {}
        if auth_json:
            filename = self.save_credential(auth_json, source="cpa", cpa_pool_id=str(pool_id or ""))
            result["credential_filename"] = filename
        return result

    def _account_by_email(self, email: str) -> dict[str, Any] | None:
        target = str(email or "").strip().lower()
        if not target:
            return None
        for account in self.accounts.list_accounts():
            if str(account.get("email") or "").strip().lower() == target:
                return account
        return None

    def _update_codex_status(self, account: dict[str, Any], status: str, error: str | None) -> None:
        token = str(account.get("access_token") or "").strip()
        if not token:
            return
        current = account.get("codex_oauth") if isinstance(account.get("codex_oauth"), dict) else {}
        updates = {**current, "status": status, "updated_at": _now()}
        if error is None:
            updates.pop("error", None)
        else:
            updates["error"] = error
        self.accounts.update_account(token, {"codex_oauth": updates}, quiet=True)

    def _append_log(self, email: str, message: str) -> None:
        path = self.log_path(email)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().strftime('%H:%M:%S')} {message}\n")

    def _check_stop(self, email: str) -> None:
        with self._lock:
            if str(email or "").strip().lower() in self._stop_requested:
                raise CodexRetryStopped("用户手动停止 Codex 补跑")

    def _resolve_pool(self, pool_id: str = "") -> dict[str, Any]:
        candidate = str(pool_id or "").strip()
        if candidate:
            pool = self.pools.get_pool(candidate)
            if pool is None:
                raise CodexManagementError(f"CPA pool not found: {candidate}")
            return pool
        try:
            from services.register_service import register_service

            cfg = register_service.get()
            candidate = str(cfg.get("codex_oauth_cpa_pool_id") or "").strip()
        except Exception:
            candidate = ""
        if candidate:
            pool = self.pools.get_pool(candidate)
            if pool is not None:
                return pool
        pools = self.pools.list_pools()
        if not pools:
            raise CodexManagementError("CPA pool not configured")
        return pools[0]

    def _default_browser_provider(self) -> str:
        return "browser_use"

    def _fetch_remote_auth_text(self, pool: dict[str, Any], file_name: str) -> tuple[str, str]:
        from urllib.parse import urlencode

        from services.cpa_service import _request_management_json

        name = str(file_name or "").strip()
        if not name:
            raise CodexManagementError("CPA 文件名为空")
        payload = _request_management_json(pool, "GET", f"/v0/management/auth-files/download?{urlencode({'name': name})}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        return text, name

    def _match_cpa_filename(self, pool: dict[str, Any], email: str, local_filename: str) -> str:
        target_email = str(email or "").strip().lower()
        files = list_remote_files(pool)
        for item in files:
            if target_email and str(item.get("email") or "").strip().lower() == target_email:
                return str(item.get("name") or "")
        for item in files:
            name = str(item.get("name") or "")
            if name == local_filename:
                return name
        raise CodexManagementError("未在 CPA auth-files 中匹配到凭证")

    def _new_credential_filename(self, email: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"codex-{_safe_email_slug(email)}-{stamp}-{uuid.uuid4().hex[:8]}.json"

    def _load_index(self) -> dict[str, dict[str, Any]]:
        CODEX_CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(CODEX_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        index = data if isinstance(data, dict) else {}
        for path in CODEX_CREDENTIAL_DIR.glob("*.json"):
            if path.name == CODEX_INDEX_FILE.name:
                continue
            if path.name not in index:
                email = ""
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    email = str(payload.get("email") or "").strip() if isinstance(payload, dict) else ""
                except Exception:
                    pass
                index[path.name] = {
                    "filename": path.name,
                    "email": email,
                    "source": "local",
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    "export_count": 0,
                    "exported_at": "",
                    "size": path.stat().st_size,
                }
        return {str(key): value for key, value in index.items() if isinstance(value, dict)}

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        CODEX_CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
        CODEX_INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _unique_filenames(self, filenames: list[str], *, limit: int) -> list[str]:
        if not isinstance(filenames, list) or not filenames:
            raise CodexManagementError("filenames 必须是非空数组")
        if len(filenames) > limit:
            raise CodexManagementError(f"单次最多 {limit} 个")
        result: list[str] = []
        seen: set[str] = set()
        for item in filenames:
            name = _safe_filename(str(item or ""))
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    @staticmethod
    def _unique_archive_name(name: str, used: set[str]) -> str:
        candidate = Path(str(name or "codex.json")).name or "codex.json"
        if candidate not in used:
            used.add(candidate)
            return candidate
        stem, dot, ext = candidate.rpartition(".")
        index = len(used) + 1
        next_name = f"{stem or candidate}-{index}{dot}{ext}" if dot else f"{candidate}-{index}"
        used.add(next_name)
        return next_name


codex_management_service = CodexManagementService()
