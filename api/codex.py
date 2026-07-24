from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.support import require_admin
from services.account_service import account_service
from services.codex_management_service import CodexManagementError, codex_management_service


class CodexFilenameRequest(BaseModel):
    filename: str = ""


class CodexFilenamesRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list)


class CodexEmailRequest(BaseModel):
    email: str = ""
    status: str = "failed"
    provider: str = ""
    cpa_pool_id: str = ""


class CodexBulkRetryRequest(BaseModel):
    emails: list[str] = Field(default_factory=list)
    access_tokens: list[str] = Field(default_factory=list)
    account_ids: list[int | str] = Field(default_factory=list)
    ids: list[int | str] = Field(default_factory=list)
    workers: int = 1
    provider: str = ""
    cpa_pool_id: str = ""


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/codex")
    async def list_codex(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return codex_management_service.list()

    @router.get("/api/codex/download/{filename:path}")
    async def download_codex(filename: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            content, real_name = codex_management_service.read_credential(filename)
            codex_management_service.mark_exported(real_name)
            return Response(
                content,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{real_name}"'},
            )
        except CodexManagementError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc

    @router.get("/api/codex/download-from-cpa/{filename:path}")
    async def download_codex_from_cpa(filename: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            content, real_name = codex_management_service.download_from_cpa(filename)
            return Response(
                content,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{real_name}"'},
            )
        except CodexManagementError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"{type(exc).__name__}: {exc}"}) from exc

    @router.post("/api/codex/download-bulk")
    async def download_codex_bulk(body: CodexFilenamesRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            content, filename, media_type = codex_management_service.download_bulk(body.filenames)
            return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        except CodexManagementError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/codex/download-bulk-from-cpa")
    async def download_codex_bulk_from_cpa(body: CodexFilenamesRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            content, filename, media_type = codex_management_service.download_bulk_from_cpa(body.filenames)
            return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        except CodexManagementError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"{type(exc).__name__}: {exc}"}) from exc

    @router.post("/api/codex/reset-export")
    async def reset_codex_export(body: CodexFilenameRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            codex_management_service.reset_exported(body.filename)
            return {"ok": True}
        except CodexManagementError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/codex/delete")
    async def delete_codex(body: CodexFilenameRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            deleted = codex_management_service.delete_credential(body.filename)
        except CodexManagementError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "凭证文件不存在"})
        return {"ok": True, "deleted": body.filename}

    @router.post("/api/codex/delete-bulk")
    async def delete_codex_bulk(body: CodexFilenamesRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        deleted: list[str] = []
        skipped: list[dict[str, str]] = []
        seen: set[str] = set()
        for filename in body.filenames:
            name = str(filename or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                if codex_management_service.delete_credential(name):
                    deleted.append(name)
                else:
                    skipped.append({"filename": name, "reason": "文件不存在"})
            except Exception as exc:
                skipped.append({"filename": name, "reason": f"{type(exc).__name__}: {exc}"})
        return {"ok": True, "deleted": deleted, "deleted_count": len(deleted), "skipped": skipped}

    @router.post("/api/codex/stop")
    async def stop_codex_retry(body: CodexEmailRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        result = codex_management_service.request_stop(body.email)
        status = int(result.pop("status", 200) or 200)
        if status >= 400:
            raise HTTPException(status_code=status, detail={"error": result.get("error") or "停止失败"})
        return result

    @router.post("/api/codex/stop-bulk")
    async def stop_codex_retry_bulk(body: CodexBulkRetryRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        emails = _resolve_emails(body)
        if not emails:
            raise HTTPException(status_code=400, detail={"error": "emails 或 access_tokens 必须是非空数组"})
        stopped: list[dict[str, object]] = []
        skipped: list[dict[str, str]] = []
        for email in emails:
            result = codex_management_service.request_stop(email)
            if result.get("ok"):
                stopped.append({"email": email, "running": result.get("running"), "injected": result.get("injected")})
            else:
                skipped.append({"email": email, "reason": str(result.get("error") or "停止失败")})
        return {"ok": True, "stopped": stopped, "stopped_count": len(stopped), "skipped": skipped}

    @router.post("/api/codex/reset-retrying")
    async def reset_codex_retrying(body: CodexEmailRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return codex_management_service.reset_retrying(body.email, body.status)
        except CodexManagementError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/codex/retry")
    async def retry_codex(body: CodexEmailRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return codex_management_service.retry(body.email, provider=body.provider, cpa_pool_id=body.cpa_pool_id)
        except CodexManagementError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc

    @router.post("/api/codex/retry-bulk")
    async def retry_codex_bulk(body: CodexBulkRetryRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        emails = _resolve_emails(body)
        if not emails:
            raise HTTPException(status_code=400, detail={"error": "emails 或 access_tokens 必须是非空数组"})
        try:
            return codex_management_service.retry_bulk(
                emails,
                workers=body.workers,
                provider=body.provider,
                cpa_pool_id=body.cpa_pool_id,
            )
        except CodexManagementError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc

    @router.get("/api/codex/retry-log")
    async def codex_retry_log(email: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not str(email or "").strip():
            raise HTTPException(status_code=400, detail={"error": "email 为空"})
        return codex_management_service.read_retry_log(email)

    return router


def _resolve_emails(body: CodexBulkRetryRequest) -> list[str]:
    values = [str(item or "").strip() for item in body.emails if str(item or "").strip()]
    if values:
        return list(dict.fromkeys(values))
    if body.access_tokens:
        emails: list[str] = []
        for token in body.access_tokens:
            account = account_service.get_account(str(token or "").strip())
            if account and str(account.get("email") or "").strip():
                emails.append(str(account.get("email") or "").strip())
        return list(dict.fromkeys(emails))
    ids = body.account_ids or body.ids
    if ids:
        wanted = {str(item) for item in ids}
        emails = []
        for account in account_service.list_accounts():
            if str(account.get("id") or "") in wanted and str(account.get("email") or "").strip():
                emails.append(str(account.get("email") or "").strip())
        return list(dict.fromkeys(emails))
    return []
