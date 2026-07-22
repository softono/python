"""FastAPI dependencies: DB session (re-exported) + authenticated user."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import COOKIE_SESSION
from app.models.models import STATUS_ACTIVE, User, UserSession
from app.services.session_service import validate_session


class Principal:
    def __init__(self, user: User, session: UserSession):
        self.user = user
        self.session = session


async def get_current_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    token = request.cookies.get(COOKIE_SESSION, "")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    result = await validate_session(db, token)
    if result is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if result.user.status != STATUS_ACTIVE:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return Principal(user=result.user, session=result.session)
