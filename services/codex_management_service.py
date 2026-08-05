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
from services.config import DATA_DIR
from services.cpa_service import cpa_config, list_remote_files, request_codex_auth_url
from utils.helper import anonymize_token


CODEX_CREDENTIAL_DIR = DATA_DIR / "codex_credentials"
CODEX_INDEX_FILE = CODEX_CREDENTIAL_DIR / "index.json"


class CodexManagementError(RuntimeError):
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
    def __init__(self, *, accounts=account_service, pools=cpa_config) -> None:
        self.accounts = accounts
        self.pools = pools
        self._lock = threading.RLock()

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
                    "account_present": bool(account),
                }
            )
        exported = sum(1 for item in accounts if int(item.get("export_count") or 0) > 0)
        return {
            "summary": {
                "total": len(accounts),
                "exported": exported,
                "unexported": max(0, len(accounts) - exported),
            },
            "accounts": accounts,
        }

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

    def save_cpa_callback_result(self, result: dict[str, Any], pool_id: str = "") -> dict[str, Any]:
        auth_json = result.get("auth_json") if isinstance(result.get("auth_json"), dict) else {}
        if auth_json:
            filename = self.save_credential(auth_json, source="cpa", cpa_pool_id=str(pool_id or ""))
            result["credential_filename"] = filename
        return result

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