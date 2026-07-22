"""Account router — view/update/sessions/activity. Ports express
src/modules/account/account.service.ts (core subset)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import get_session_token
from app.core.db import get_db
from app.core.deps import Principal, get_current_principal
from app.models.models import User, UserSession, activity_label
from app.services import session_service
from app.services.common import log_activity_data, to_safe_user
from app.utils.client_info import ClientInfo, device_name, get_client_info, get_device_uid
from app.utils.dates import date_time_format, get_client_timezone
from app.utils.pagination import PageOptions, paginate, parse_query
from app.utils.response import ApiResult, send_error, send_result

router = APIRouter()


@router.get("/view")
async def view(principal: Principal = Depends(get_current_principal)):
    return send_result(ApiResult(200, 1, "Account details retrieved", {"user": to_safe_user(principal.user)}))


@router.put("/update")
async def update(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    body = await request.json()
    user = principal.user
    old_data = {
        "first_name": user.first_name, "last_name": user.last_name, "country": user.country,
        "timezone": user.timezone, "phone": user.phone, "email": user.email,
    }

    new_email = body.get("email")
    email_changed = bool(new_email and new_email != user.email)
    if email_changed:
        existing = (await db.execute(select(User).where(User.email == new_email))).scalar_one_or_none()
        if existing and existing.id != user.id:
            return send_error(409, "Email already in use")

    for field in ("first_name", "last_name", "country", "timezone", "phone"):
        if field in body and body[field] is not None:
            setattr(user, field, body[field])
    if email_changed:
        user.email = new_email
        user.email_verified = False
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    await session_service.invalidate_user_cache(user.id)
    new_data = {
        "first_name": user.first_name, "last_name": user.last_name, "country": user.country,
        "timezone": user.timezone, "phone": user.phone, "email": user.email,
    }
    await log_activity_data(db, "ACCOUNT_UPDATE", user.id, get_client_info(request), old_data, new_data)

    return send_result(ApiResult(200, 1, "User profile updated successfully", to_safe_user(user)))


@router.delete("/deactivate")
async def deactivate(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    from app.models.models import STATUS_INACTIVE
    from app.services.common import log_activity

    principal.user.status = STATUS_INACTIVE
    principal.user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await session_service.invalidate_user_cache(principal.user.id)
    await log_activity(db, "ACCOUNT_DEACTIVATE", principal.user.id, get_client_info(request))
    return send_result(ApiResult(200, 1, "Account deactivated successfully", None))


@router.get("/session")
async def sessions(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    page_in = parse_query(request.url.query)
    current_device_uid = get_device_uid(request)
    tz = get_client_timezone(request)

    def map_row(row: dict) -> dict:
        is_current = row.get("device_uid") == current_device_uid
        client = device_name(row.get("user_agent"))
        if is_current:
            client += " (This Device)"
        return {
            "id": row["id"], "client": client, "ip": row.get("ip_address"),
            "last_activity": date_time_format(row["updated_at"], tz), "action": "" if is_current else "logout",
        }

    result = await paginate(
        db, "SELECT id, device_uid, user_agent, ip_address, created_at, updated_at FROM user_sessions WHERE user_id = :user_id",
        {"user_id": principal.user.id}, page_in,
        PageOptions(default_sort_field="created_at", default_sort_dir="desc",
                    sort_map={"device_uid": "device_uid", "user_agent": "user_agent",
                              "ip_address": "ip_address", "created_at": "created_at"}, map_row=map_row),
    )
    return send_result(ApiResult(200, 1, "Data retrieved successfully", result))


@router.post("/session/logout")
async def logout_device(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    body = await request.json()
    device_id = body.get("deviceId") or body.get("device_id")
    if not device_id:
        return send_error(400, "Device ID is required")

    row = (
        await db.execute(select(UserSession).where(UserSession.id == device_id, UserSession.user_id == principal.user.id))
    ).scalar_one_or_none()
    if not row:
        return send_error(404, "Session not found")
    token = row.token
    await db.execute(delete(UserSession).where(UserSession.id == device_id))
    await db.commit()
    await session_service.invalidate_session_cache(token)

    from app.services.common import log_activity

    await log_activity(db, "DEVICE_LOGGED_OUT", principal.user.id, get_client_info(request))
    return send_result(ApiResult(200, 1, "Device logged out successfully", None))


@router.get("/user-activity")
async def user_activity(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    page_in = parse_query(request.url.query)
    tz = get_client_timezone(request)

    def map_row(row: dict) -> dict:
        row["type"] = activity_label(row.get("type"))
        row["client"] = device_name(row.get("client"))
        row["created_at"] = date_time_format(row["created_at"], tz)
        return row

    result = await paginate(
        db, "SELECT id, user_id, type, ip, client, created_at FROM user_activities WHERE user_id = :user_id",
        {"user_id": principal.user.id}, page_in,
        PageOptions(default_sort_field="created_at", default_sort_dir="desc",
                    sort_map={"type": "type", "created_at": "created_at"}, map_row=map_row),
    )
    return send_result(ApiResult(200, 1, "Data retrieved successfully", result))
