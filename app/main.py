"""FastAPI app assembly: CORS, device-cookie + rate-limit middleware,
standard-envelope exception handling, route registration."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException

from app.api.routes import account, auth, login_link, notes, oauth, passkey, public, tfa
from app.core.config import settings
from app.core.middleware import DeviceCookieMiddleware, RateLimitMiddleware
from app.utils.response import send_error

app = FastAPI(title="Next API (Python)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(DeviceCookieMiddleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return send_error(exc.status_code, str(exc.detail))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return send_error(500, "Something went wrong")


@app.get("/api/health")
async def health():
    return {"status": 1, "message": "Ok", "data": []}


app.include_router(auth.router, prefix="/api/auth")
app.include_router(tfa.router, prefix="/api/auth")
app.include_router(login_link.router, prefix="/api/auth")
app.include_router(oauth.router, prefix="/api/auth")
app.include_router(passkey.router, prefix="/api/auth")
app.include_router(account.router, prefix="/api/account")
app.include_router(notes.router, prefix="/api/notes")
app.include_router(public.router, prefix="/api")
