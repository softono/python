"""Public router — blogs, pages, contact, settings/public, seo. Ports
express src/modules/{blog,public,setting}."""
from __future__ import annotations

import os
from base64 import b64decode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.cookies import get_session_token
from app.core.db import get_db
from app.core.deps import get_current_principal
from app.models.models import Blog, ContactMessage, Page, Seo
from app.services import session_service
from app.services.setting_service import get_public_settings, get_setting
from app.utils.dates import date_format, date_time_format, get_client_timezone
from app.utils.pagination import PageOptions, paginate, parse_query
from app.utils.response import ApiResult, send_error, send_result

router = APIRouter()


@router.get("/settings/public")
async def public_settings(db: AsyncSession = Depends(get_db)):
    return send_result(ApiResult(200, 1, "ok", await get_public_settings(db)))


@router.get("/seo")
async def seo(request: Request, db: AsyncSession = Depends(get_db)):
    url = request.query_params.get("url", "").strip("/")
    candidates = [url, f"/{url}"] if url else ["home", "/home"]
    row = None
    for cand in candidates:
        row = (await db.execute(select(Seo).where(Seo.url == cand))).scalar_one_or_none()
        if row:
            break
    if not row:
        return send_result(ApiResult(200, 1, "ok", {}))

    title = row.meta_title or row.title or ""
    description = row.meta_description or row.description or ""
    images = [row.image] if row.image else []
    data: dict = {
        "title": title, "description": description,
        "openGraph": {"title": title, "description": description, "images": images},
        "twitter": {"card": "summary_large_image", "title": title, "description": description, "images": images},
    }
    if row.canonical:
        data["alternates"] = {"canonical": row.canonical}
    return send_result(ApiResult(200, 1, "ok", data))


@router.get("/blogs")
async def list_blogs(request: Request, db: AsyncSession = Depends(get_db)):
    page_in = parse_query(request.url.query)
    base_query = "SELECT id, slug, title, excerpt, category, image, created_at FROM blogs WHERE status = 'active'"
    params: dict = {}
    if page_in.search:
        base_query += " AND title ILIKE :search"
        params["search"] = f"%{page_in.search}%"

    tz = get_client_timezone(request)

    def map_row(row: dict) -> dict:
        row["image"] = f"{settings.filesystem_url}/assets/images/{row['image']}" if row.get("image") else ""
        row["created_at"] = date_format(row["created_at"], tz)
        return row

    result = await paginate(db, base_query, params, page_in, PageOptions(
        default_sort_field="created_at", default_sort_dir="desc",
        sort_map={"title": "title", "category": "category", "created_at": "created_at"}, map_row=map_row,
    ))
    return send_result(ApiResult(200, 1, "Data retrieved successfully", result))


@router.get("/blogs/{slug}")
async def get_blog(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    blog = (await db.execute(select(Blog).where(Blog.slug == slug, Blog.status == "active"))).scalar_one_or_none()
    if not blog:
        return send_error(404, "Blog not found")
    tz = get_client_timezone(request)
    image_url = f"{settings.filesystem_url}/assets/images/{blog.image}" if blog.image else ""
    return send_result(ApiResult(200, 1, "Ok", {
        "id": blog.id, "slug": blog.slug, "title": blog.title, "excerpt": blog.excerpt, "body": blog.body,
        "category": blog.category, "image": image_url, "meta_title": blog.meta_title,
        "meta_description": blog.meta_description, "created_at": date_format(blog.created_at, tz),
        "updated_at": date_time_format(blog.updated_at, tz),
    }))


@router.get("/page/{slug}")
async def get_page(slug: str, db: AsyncSession = Depends(get_db)):
    if not slug:
        return send_error(400, "Missing slug")
    page = (await db.execute(select(Page).where(Page.slug == slug, Page.status == "active"))).scalar_one_or_none()
    if not page:
        return send_error(404, "Page not found")
    return send_result(ApiResult(200, 1, "Page retrieved", {"title": page.title, "slug": page.slug, "body": page.body}))


@router.post("/contact")
async def contact(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.cache import incr_cache
    from app.utils.client_info import get_client_ip

    count, _ = await incr_cache(f"contact:{get_client_ip(request)}", 300)
    if count > 5:
        return send_error(429, "Too many requests")

    body = await request.json()
    email, subject, message = body.get("email", ""), body.get("subject", ""), body.get("message", "")

    dup = (
        await db.execute(select(ContactMessage).where(
            ContactMessage.to_user == email, ContactMessage.subject == subject, ContactMessage.message == message,
        ))
    ).scalar_one_or_none()
    if dup:
        return send_error(409, "You have already submitted this message")

    user_id = None
    token = get_session_token(request)
    if token:
        result = await session_service.validate_session(db, token)
        if result:
            user_id = result.user.id

    db.add(ContactMessage(user_id=user_id, to_user=email, subject=subject, message=message))
    await db.commit()
    return send_result(ApiResult(200, 1, "Thank you for contacting us. We will get back to you soon.", None))


@router.get("/file")
async def private_file(request: Request, principal=Depends(get_current_principal)):
    encoded = request.query_params.get("p", "")
    if not encoded:
        return send_error(400, "Missing file parameter")
    try:
        decoded = b64decode(encoded).decode()
    except Exception:
        return send_error(400, "Invalid file parameter")

    storage_root = os.path.abspath("public")
    allowed_prefix = os.path.join(storage_root, settings.filesystem_path)
    resolved = os.path.join(storage_root, decoded)
    if resolved != allowed_prefix and not resolved.startswith(allowed_prefix + os.sep):
        return send_error(403, "Access denied")
    if not os.path.isfile(resolved):
        return send_error(404, "File not found")
    return FileResponse(resolved, headers={"Cache-Control": "private, no-store"})
