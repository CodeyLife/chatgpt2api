from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.support import require_admin
from services.codex_management_service import CodexManagementError, codex_management_service


class CodexFilenameRequest(BaseModel):
    filename: str = ""


class CodexFilenamesRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list)


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

    return router