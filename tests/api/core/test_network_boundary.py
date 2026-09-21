"""Strict CORS parsing and production transport contracts."""

from __future__ import annotations

import pytest

from offerpilot.core.config import Settings


@pytest.mark.parametrize(
    "origins",
    [
        "",
        "*",
        "null",
        "file:///tmp/client",
        "ftp://example.com",
        "https://example.com/app",
        "https://example.com?next=/app",
        "https://user@example.com",
        "https://example.com:bad-port",
    ],
)
def test_cors_origins_reject_non_origin_values(origins: str):
    settings = Settings(cors_origins=origins, debug=True)
    with pytest.raises(RuntimeError, match="CORS"):
        _ = settings.allowed_origins


def test_cors_origins_normalize_an_explicit_http_and_https_whitelist():
    settings = Settings(cors_origins="http://LOCALHOST:3000/, https://app.example.com", debug=True)
    assert settings.allowed_origins == ["http://localhost:3000", "https://app.example.com"]


def test_production_requires_https_origin_and_signing_key():
    insecure = Settings(
        cors_origins="http://localhost:3000",
        debug=False,
        profile_signing_key="phase6-production-signing-key-with-32-bytes",
    )
    with pytest.raises(RuntimeError, match="HTTPS"):
        insecure.validate_runtime_security()

    secure = Settings(
        cors_origins="https://app.example.com",
        debug=False,
        profile_signing_key="phase6-production-signing-key-with-32-bytes",
    )
    secure.validate_runtime_security()
