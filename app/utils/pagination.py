"""Generic list executor — mirrors express src/lib/pagination/index.ts:
limit clamp [1,100], offset-wins-over-page, whitelist sort, count subquery,
{list, pagination:{page,limit,total,pages,count}} envelope."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class PageInput:
    page: int = 0
    offset: int | None = None
    limit: int = 0
    sort_field: str = ""
    sort_dir: str = ""
    search: str = ""


def parse_query(query_string: str) -> PageInput:
    q = parse_qs(query_string)

    def first(key: str, default: str = "") -> str:
        return q.get(key, [default])[0]

    offset_raw = first("offset")
    return PageInput(
        page=int(first("page") or 0),
        offset=int(offset_raw) if offset_raw else None,
        limit=int(first("limit") or 0),
        sort_field=first("sortField"),
        sort_dir=first("sortDir"),
        search=first("search"),
    )


@dataclass
class PageOptions:
    default_sort_field: str = ""
    default_sort_dir: str = "desc"
    sort_map: dict[str, str] = field(default_factory=dict)
    map_row: Callable[[dict[str, Any]], dict[str, Any]] | None = None


async def paginate(
    db: AsyncSession, base_query: str, params: dict[str, Any], page_in: PageInput, opts: PageOptions
) -> dict[str, Any]:
    limit = page_in.limit or 20
    limit = max(1, min(limit, 100))

    if page_in.offset is not None:
        offset = max(page_in.offset, 0)
        page = offset // limit + 1
    else:
        page = max(page_in.page or 1, 1)
        offset = (page - 1) * limit

    total = (await db.execute(text(f"SELECT count(*)::int FROM ({base_query}) sub"), params)).scalar_one()

    field_name = opts.default_sort_field
    direction = opts.default_sort_dir or "desc"
    if page_in.sort_field and page_in.sort_field in opts.sort_map:
        field_name = opts.sort_map[page_in.sort_field]
    elif opts.default_sort_field:
        field_name = opts.sort_map.get(opts.default_sort_field, opts.default_sort_field)
    if page_in.sort_dir in ("asc", "desc"):
        direction = page_in.sort_dir

    query = base_query
    if field_name:
        safe_dir = "ASC" if direction.lower() == "asc" else "DESC"
        query += f" ORDER BY {field_name} {safe_dir}"
    query += f" LIMIT {limit} OFFSET {offset}"

    rows = (await db.execute(text(query), params)).mappings().all()
    list_ = [dict(r) for r in rows]
    if opts.map_row:
        list_ = [opts.map_row(r) for r in list_]

    pages = max(1, math.ceil(total / limit)) if total > 0 else 1
    if total == 0:
        count = "No items"
    else:
        start, end = offset + 1, min(offset + limit, total)
        count = f"Showing {start}-{end} of {total} items"

    return {
        "list": list_,
        "pagination": {"page": page, "limit": limit, "total": total, "pages": pages, "count": count},
    }
