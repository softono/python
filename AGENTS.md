# Python User API (port 4300)

Idiomatic FastAPI port of `express/express` (Node/Express user API). Same shared Postgres DB, same wire contract, same React frontend (`react`) via `VITE_API_ORIGIN`. **Express owns all migrations — this project runs none.**

## Stack

FastAPI + `uvicorn`, SQLAlchemy 2.0 async + `asyncpg`, Pydantic v2 (validation only where used — see note below), `argon2-cffi`, `pyotp`, `webauthn` (py_webauthn), `authlib` (low-level OIDC client), `redis` (or in-memory fallback), `aiosmtplib`, `python-multipart`, `cryptography`, `httpx`, `cachetools`.

## Structure

```
app/main.py                  FastAPI app assembly, router mounting
app/core/                    config, db (async engine/session), cache, cookies, security, middleware
app/models/models.py          SQLAlchemy declarative models (Iprefixed types not used here — Python
                               uses SQLAlchemy model classes directly: User, UserSession, etc.)
app/services/                 business logic per domain: auth_service, session_service, otp_service,
                               tfa_service, passkey_service, login_link_service, google_oauth_service,
                               device_service, mailer_service, challenge_service, common.py (shared
                               presentation/activity-log helpers)
app/api/routes/                route modules mirroring express's route files: auth.py, tfa.py,
                               login_link.py, oauth.py, passkey.py, account.py, notes.py, public.py
app/utils/                    client_info, dates, pagination, response, validate
```

Handlers return raw `JSONResponse` (via `send_result`/`send_error`), never FastAPI's `response_model` — that would force the envelope's extra top-level keys into a nested shape.

Mirror this layout in `python_admin`, adding `app/api/routes/admin.py` + `app/services/admin_service.py` + a permission-gate dependency. `python_admin` copies (does not import) `python`'s core libs/models — the two are separate deployables, duplicated deliberately like express/express_admin.

## Wire contract — MUST match express exactly

1. **Envelope**: `{status:0|1, message, data(default []), ...extras}` — extras spread at top level. `send_result()` in `app/utils/response.py` builds this; always call it, never return a bare dict/model.
2. **422 validation shape**: `{status:0, message:"<joined messages>", data:{errors:{field: msg}}}` — built by `Validator` (`app/utils/validate.py`), not FastAPI/Pydantic's default 422 body.
3. **Encryption**: AES-256-GCM, key = SHA-256(`ENCRYPTION_KEY`), ciphertext = std-base64 of `IV(12)||TAG(16)||ciphertext`. Python's `cryptography` `AESGCM.encrypt` appends the tag after ciphertext by default — reorder explicitly to tag-before-ciphertext. `decrypt` is fail-open (return input unchanged on any error). Encrypted settings: `google_client_secret`, `smtp_password`, `google_recaptcha_secret_key`.
4. **Cookies**: `next_session_token`, `next_device_uid` (1y), `next_tfa` (600s), `next_wac` (300s), `next_oauth` (600s). `HttpOnly; Path=/; SameSite=Lax`, `Secure` only in prod. Signed cookies: `payload + "." + base64url(HMAC-SHA256(ENCRYPTION_KEY, payload))`, split on the LAST dot, constant-time compare (`hmac.compare_digest`).
   - **Known pitfall**: if a route declares `response: Response` to set cookies but then returns a different `Response`/`JSONResponse` object, FastAPI does NOT merge headers — the injected `response`'s `Set-Cookie` is silently dropped. Always pass the injected `response` into `send_result(result, response)` so its `set-cookie` headers get copied onto the returned object (see `app/utils/response.py`).
5. **Sessions**: opaque 43-char base64url token, stored plaintext in `user_sessions`. Cache keys `auth:session:<token>` / `auth:user:<id>`, TTL 300s. Sliding refresh when `updated_at` >24h old.
6. **Rate limiting**: prefix-order — `/api/auth/login` (+admin) → 10/15min checked FIRST; register/otp/login-otp/tfa/forgot-password/reset-password/verify-account → 5/15min. Key `${path}:${clientIp}`. Fail-closed. 429 body `{status:0,message:"Too many requests, please try again later.",data:[]}` + `Retry-After`/`X-RateLimit-*` headers. Counter = cache INCR + EXPIRE-on-first.
7. **Client IP**: replicate `clientInfo.ts` exactly — parse `x-forwarded-for` right-to-left, first public IP.
8. **Pagination**: limit clamp `[1,100]` default 20; explicit `offset` wins over `page`; count via subquery before sort/limit; sortMap whitelist; response `data:{list,pagination:{page,limit,total,pages(min 1),count:"Showing X-Y of Z items"|"No items"}}`; message `"Data retrieved successfully"`.
9. **Passwords/OTP/TOTP**: argon2id `m=65536,t=3,p=4` via `argon2-cffi` (native PHC string support). OTP: 6-digit, argon2-hashed, `user_verifications`, identifier `<purpose>:<email>`, TTL 600s, max 6 attempts. TOTP (`pyotp`): base32 secret, AES-GCM-encrypted at rest in `user_two_factors`, SHA1/6-digit/30s, ±1 window. Backup codes: 10× 8-hex-uppercase, argon2-hashed.
10. **Challenge cache keys**: `auth:tfa:<handle>`, `auth:tfa:attempts:<handle>`, `account:email-change:<handle>`, `webauthn:chal:<handle>`; `setting:all` cached 3600s.
11. **Passkeys** (`user_passkeys`) — read AND write exactly: `credential_id` = base64url of RAW credential ID bytes; `public_key` = base64url of RAW COSE bytes (`py_webauthn`'s `VerifiedRegistration.credential_id`/`.credential_public_key` are already raw bytes — confirmed correct, do not re-encode). `counter` int; `device_type` `"singleDevice"|"multiDevice"`; `backed_up` bool; `transports` comma-joined. Registration `userID` = UTF-8 bytes of user UUID string. RP ID = hostname of `WEBAUTHN_ORIGIN`; expected origin = frontend URL (5173).
12. **Google OAuth**: OIDC discovery + PKCE(S256) + `state` + `nonce` in the signed `next_oauth` cookie (via `authlib`'s low-level API, not its session-based high-level flow). Credentials from `settings` table (decrypted), not env. `redirect_uri = <API_URL>/api/auth/google/callback`. Callback 302s to frontend with `Set-Cookie` on the `RedirectResponse`.
13. **Dates**: client tz from `next_tz` cookie (+alias map); format `yyyy-MM-dd HH:mm:ss` / `MMMM d, yyyy` server-side before every response — never send a raw `datetime`.
14. **Files**: extension from sniffed magic bytes, not client-supplied extension; filename = 24-char alnum from base64(16 random bytes) + ext; path `public/<type-path>/<YYYY/MM>/<name>`, identical to express.
15. **Mailer**: SMTP (via `aiosmtplib`) built from decrypted `settings` values; HTML wrapped with the same layout template + `email_templates` lookups.

## SQLAlchemy-specific pitfalls (learned the hard way — see model comments)

- **Naive vs tz-aware columns**: only `users`, `user_sessions`, `user_accounts`, `user_verifications`, and `user_passkeys.created_at` are plain `timestamp` (naive) in the real migrated schema — everything else is `timestamptz`. Handled via the `UTCNaiveDateTime` `TypeDecorator` in `app/models/models.py`, which strips/re-adds UTC tzinfo at the DB boundary so app code can uniformly use `datetime.now(timezone.utc)`. Do not special-case this per call site — extend the decorator instead if a new naive column shows up.
- **ORM insert sends explicit NULL for unset columns** — SQLAlchemy does NOT fall back to the DB's own `DEFAULT now()` unless the column has a Python-side `default=` or `server_default`. Any new insert path must explicitly set `created_at`/`updated_at` (see `log_activity`/`log_activity_data` in `app/services/common.py`, and the equivalent fix in `setting_service.py`'s `update_setting`). Audit new insert code for this before shipping.

## Conventions

- No inline schemas — validation lives in `app/utils/validate.py`'s `Validator`.
- Route → parse body → `Validator` → 422 early-return → service call → `send_result(result, response)`.
- Route inventory must match express's `src/modules/{auth,account,note,public}/**.routes.ts` 1:1.
- Verify by booting `uvicorn` and curling against the live shared Postgres DB: register (422 shape) → login (cookie persists) → session → 2fa/status → logout; 2FA/passkeys/login-links/OAuth per their respective service; compare envelope/pagination output against `:4000`.
- **Test**: `.venv/Scripts/python.exe -m pytest tests/ -v` — black-box HTTP integration tests in `tests/test_integration.py` (fixtures in `tests/conftest.py`). Boots the real app via `uvicorn` on port 4399 against the live shared Postgres DB, drives it purely over HTTP, and cleans up every row it creates (`%@integration.local` emails, cascades via FK). Add new endpoint coverage here as modules grow; keep the `@integration.local` marker convention so cleanup stays exhaustive.
- **Query**: `.venv/Scripts/python.exe -m app.db_query --file <name>` (reads `db/sql/<name>`) or `.venv/Scripts/python.exe -m app.db_query --sql "<query>"` — ad-hoc SQL against the live shared DB, mirroring express's `npm run db:query`. Inspection/manual-fix only, never migrations (express still owns schema changes).
