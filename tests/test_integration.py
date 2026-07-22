"""Black-box HTTP integration tests for the Python user API. Drives the real
server purely over HTTP; see conftest.py for server bootstrap + cleanup."""
from __future__ import annotations

import httpx

from conftest import insert_verified_user, unique_email


def test_health(client: httpx.Client):
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_not_found(client: httpx.Client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["status"] == 0


def test_public_settings(client: httpx.Client):
    resp = client.get("/api/settings/public")
    assert resp.status_code == 200
    assert resp.json()["status"] == 1


def test_register_validation(client: httpx.Client):
    resp = client.post("/api/auth/register", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == 0
    errors = body["data"]["errors"]
    assert isinstance(errors, dict) and len(errors) > 0


def test_register_and_duplicate(client: httpx.Client):
    email = unique_email("register")
    body = {
        "first_name": "PyTest",
        "last_name": "Tester",
        "email": email,
        "phone": "9000000001",
        "password": "Sup3rSecret!123",
        "recaptcha_token": "test-token",
    }
    resp = client.post("/api/auth/register", json=body)
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["status"] == 1

    resp2 = client.post("/api/auth/register", json=body)
    assert resp2.status_code not in (200, 201), resp2.text
    assert resp2.json()["status"] == 0


def test_login_wrong_password(client: httpx.Client):
    email = unique_email("wrongpw")
    insert_verified_user("CorrectHorseBattery1!", email)
    resp = client.post("/api/auth/login", json={"email": email, "password": "WrongPassword1!"})
    assert resp.status_code != 200
    assert resp.json()["status"] == 0


def test_notes_require_auth(client: httpx.Client):
    resp = client.get("/api/notes")
    assert resp.status_code == 401


def test_authenticated_flow(client: httpx.Client):
    password = "CorrectHorseBattery1!"
    email = unique_email("verified")
    insert_verified_user(password, email)

    # Login
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    parsed = resp.json()
    assert parsed["status"] == 1
    assert any(c == "next_session_token" for c in resp.cookies)

    # Session
    resp = client.get("/api/auth/session")
    assert resp.status_code == 200
    assert resp.json()["status"] == 1

    # Create note
    resp = client.post("/api/notes", json={"title": "Integration test note", "note": "hello"})
    assert resp.status_code == 201, resp.text
    parsed = resp.json()
    assert parsed["status"] == 1
    note_id = parsed["data"]["id"]
    assert note_id

    # List notes
    resp = client.get("/api/notes")
    assert resp.status_code == 200
    parsed = resp.json()
    assert parsed["status"] == 1
    assert "list" in parsed["data"]
    assert "pagination" in parsed["data"]

    # Update note
    resp = client.patch(f"/api/notes/{note_id}", json={"title": "Updated title"})
    assert resp.status_code == 200
    assert resp.json()["status"] == 1

    # Delete note
    resp = client.delete(f"/api/notes/{note_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == 1

    # Logout
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["status"] == 1

    # Session must now be rejected
    resp = client.get("/api/auth/session")
    assert resp.status_code == 401


def test_login_rate_limit(client: httpx.Client):
    email = unique_email("ratelimit")
    last_resp = None
    for _ in range(11):
        last_resp = client.post("/api/auth/login", json={"email": email, "password": "WhateverWrong1!"})
    assert last_resp.status_code == 429
    assert last_resp.headers.get("Retry-After")
