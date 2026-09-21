"""Signed anonymous Profile and network boundary contracts."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from offerpilot.core.config import settings
from offerpilot.profiles.cookies import PROFILE_COOKIE, make_profile_cookie
from offerpilot.main import app

PROFILE_ONE = "00000000-0000-4000-8000-000000000011"
PROFILE_TWO = "00000000-0000-4000-8000-000000000022"


def _set_profile(client: TestClient, profile_id: str, *, expires_at: int | None = None) -> None:
    client.cookies.set(PROFILE_COOKIE, make_profile_cookie(profile_id, expires_at=expires_at))


def test_bootstrap_issues_http_only_signed_cookie(client):
    client.cookies.clear()
    response = client.post("/api/profile/bootstrap", json={})

    assert response.status_code == 200
    assert response.json()["profile_id"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert client.get("/api/sessions").status_code == 200


def test_tampered_and_expired_cookies_are_rejected(client):
    client.cookies.clear()
    client.cookies.set(PROFILE_COOKIE, f"{PROFILE_ONE}.9999999999.invalid")
    assert client.get("/api/sessions").status_code == 401

    client.cookies.set(PROFILE_COOKIE, make_profile_cookie(PROFILE_ONE, expires_at=int(time.time()) - 1))
    assert client.get("/api/sessions").status_code == 401


def test_profile_cannot_read_another_profiles_session():
    with TestClient(app) as owner, TestClient(app) as other:
        owner.headers.update({"Origin": "http://localhost:3000"})
        other.headers.update({"Origin": "http://localhost:3000"})
        _set_profile(owner, PROFILE_ONE)
        created = owner.post("/api/sessions", json={})
        assert created.status_code == 200
        _set_profile(other, PROFILE_TWO)
        assert other.get(f"/api/sessions/{created.json()['id']}").status_code == 404


def test_legacy_header_never_authenticates_a_business_request(client):
    client.cookies.clear()
    response = client.get("/api/sessions", headers={"X-OfferPilot-Profile-Id": PROFILE_ONE})
    assert response.status_code == 401


def test_legacy_bootstrap_input_is_rejected(client):
    client.cookies.clear()
    response = client.post(
        "/api/profile/bootstrap",
        json={"legacy_profile_id": PROFILE_ONE},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 422


def test_all_write_requests_require_a_trusted_origin(client):
    _set_profile(client, PROFILE_ONE)

    client.headers.pop("Origin")
    missing = client.post("/api/sessions", json={})
    rejected = client.post("/api/sessions", json={}, headers={"Origin": "https://untrusted.example"})
    accepted = client.post("/api/sessions", json={}, headers={"Origin": "http://localhost:3000"})

    assert missing.status_code == 403
    assert rejected.status_code == 403
    assert accepted.status_code == 200


def test_bootstrap_is_anonymous_but_still_requires_a_trusted_origin(client):
    client.cookies.clear()
    client.headers.pop("Origin")
    assert client.post("/api/profile/bootstrap", json={}).status_code == 403
    assert client.post(
        "/api/profile/bootstrap", json={}, headers={"Origin": "https://untrusted.example"}
    ).status_code == 403


def test_production_profile_cookie_is_secure(client, monkeypatch):
    client.cookies.clear()
    monkeypatch.setattr(settings, "debug", False)
    response = client.post("/api/profile/bootstrap", json={}, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_session_delete_and_profile_reset_are_hard_deletes(client):
    session = client.post("/api/sessions", json={})
    assert session.status_code == 200
    session_id = session.json()["id"]
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    another = client.post("/api/sessions", json={}).json()["id"]
    assert client.post("/api/profile/reset", json={"confirmation": "RESET"}).status_code == 200
    assert client.get(f"/api/sessions/{another}").status_code == 404
