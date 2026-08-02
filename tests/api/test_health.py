"""Tests for health check endpoint."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient):
    """Health endpoint should return status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_health_content_type(client: TestClient):
    """Health endpoint should return JSON."""
    response = client.get("/health")
    assert response.headers["content-type"].startswith("application/json")
