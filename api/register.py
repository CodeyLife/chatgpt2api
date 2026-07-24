from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.support import require_admin
from services.register.browser_automation import browser_automation_status
from services.register import manual_otp
from services.register_service import register_service


class RegisterConfigRequest(BaseModel):
    mail: dict | None = None
    proxy: str | None = None
    registration_driver: str | None = None
    total: int | None = None
    threads: int | None = None
    mode: str | None = None
    target_quota: int | None = None
    target_available: int | None = None
    check_interval: int | None = None
    register_interval_min: float | None = None
    register_interval_max: float | None = None
    sentinel_browser_enabled: bool | None = None
    sentinel_browser_headless: bool | None = None
    sentinel_browser_timeout: float | None = None
    sentinel_browser_chrome_path: str | None = None
    sentinel_browser_sdk_url: str | None = None
    sentinel_browser_fallback: bool | None = None
    codex_agent_identity_enabled: bool | None = None
    codex_agent_identity_verify_task: bool | None = None
    codex_oauth_enabled: bool | None = None
    codex_oauth_via_cpa: bool | None = None
    codex_oauth_cpa_pool_id: str | None = None
    humanize: dict | None = None
    profile: dict | None = None
    flow_trigger: dict | None = None
    browser_use: dict | None = None
    skyvern: dict | None = None
    roxy: dict | None = None
    cloak: dict | None = None
    sms: dict | None = None
    new_account_warmup_minutes: int | None = None
    new_account_verify_delay_seconds: int | None = None
    new_account_max_verify_workers: int | None = None


class OutlookPoolResetRequest(BaseModel):
    scope: str | None = None


class ManualOTPSubmitRequest(BaseModel):
    email: str = ""
    code: str = ""


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/register")
    async def get_register_config(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.get()}

    @router.post("/api/register")
    async def update_register_config(body: RegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.update(body.model_dump(exclude_none=True))}

    @router.get("/api/register/runtime")
    async def get_register_runtime(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        register = register_service.get()
        return {
            "runtime": browser_automation_status(register),
            "drivers": register.get("drivers") or [],
            "registration_driver": register.get("registration_driver") or "platform_oauth",
        }

    @router.post("/api/register/start")
    async def start_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.start()}

    @router.post("/api/register/stop")
    async def stop_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.stop()}

    @router.post("/api/register/reset")
    async def reset_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset()}

    @router.post("/api/register/outlook-pool/reset")
    async def reset_outlook_pool(body: OutlookPoolResetRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset_outlook_pool(body.scope or "all")}

    @router.get("/api/register/manual-otp")
    async def list_manual_otp_waiting(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"waiting": manual_otp.list_waiting()}

    @router.post("/api/register/manual-otp")
    async def submit_manual_otp(body: ManualOTPSubmitRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return manual_otp.submit_manual_otp(body.email, body.code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/register/events")
    async def register_events(token: str = ""):
        require_admin(f"Bearer {token}")

        async def stream():
            last = ""
            while True:
                payload = json.dumps(register_service.get(), ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
