"""Notes router — full CRUD, ports express src/modules/note."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import Principal, get_current_principal
from app.models.models import Note
from app.utils.dates import date_time_format, get_client_timezone
from app.utils.pagination import PageOptions, paginate, parse_query
from app.utils.response import ApiResult, send_error, send_result

router = APIRouter()

_SORT_MAP = {"title": "title", "created_at": "created_at", "updated_at": "updated_at"}


@router.get("")
async def list_notes(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    page_in = parse_query(request.url.query)
    base_query = "SELECT id, title, note, created_at, updated_at FROM notes WHERE user_id = :user_id"
    params: dict = {"user_id": principal.user.id}
    if page_in.search:
        base_query += " AND title ILIKE :search"
        params["search"] = f"%{page_in.search}%"

    tz = get_client_timezone(request)

    def map_row(row: dict) -> dict:
        row["created_at"] = date_time_format(row["created_at"], tz)
        row["updated_at"] = date_time_format(row["updated_at"], tz)
        return row

    result = await paginate(db, base_query, params, page_in, PageOptions(
        default_sort_field="created_at", default_sort_dir="desc", sort_map=_SORT_MAP, map_row=map_row,
    ))
    return send_result(ApiResult(200, 1, "Data retrieved successfully", result))


@router.post("")
async def create_note(
    request: Request, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    body = await request.json()
    title = body.get("title", "")
    if not title:
        return send_result(ApiResult(422, 0, "Title is required", {"errors": {"title": "Title is required"}}))

    now = datetime.now(timezone.utc)
    note = Note(user_id=principal.user.id, title=title, note=body.get("note"), created_at=now, updated_at=now)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return send_result(ApiResult(201, 1, "Note created successfully", {
        "id": note.id, "user_id": note.user_id, "title": note.title, "note": note.note,
        "created_at": note.created_at, "updated_at": note.updated_at,
    }))


@router.patch("/{note_id}")
async def update_note(
    note_id: int, request: Request, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    body = await request.json()
    note = (await db.execute(select(Note).where(Note.id == note_id, Note.user_id == principal.user.id))).scalar_one_or_none()
    if not note:
        return send_error(404, "Note not found")
    if "title" in body:
        note.title = body["title"]
    if "note" in body:
        note.note = body["note"]
    note.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(note)
    return send_result(ApiResult(200, 1, "Note updated successfully", {
        "id": note.id, "user_id": note.user_id, "title": note.title, "note": note.note,
        "created_at": note.created_at, "updated_at": note.updated_at,
    }))


@router.delete("/{note_id}")
async def delete_note(
    note_id: int, db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_current_principal)
):
    note = (await db.execute(select(Note).where(Note.id == note_id, Note.user_id == principal.user.id))).scalar_one_or_none()
    if not note:
        return send_error(404, "Note not found")
    await db.execute(delete(Note).where(Note.id == note_id))
    await db.commit()
    return send_result(ApiResult(200, 1, "Note deleted successfully", {
        "id": note.id, "user_id": note.user_id, "title": note.title, "note": note.note,
    }))
