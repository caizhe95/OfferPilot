"""Minimal SQLite initialization contracts for the Demo application."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from offerpilot.core.config import settings
from offerpilot.database.connection import get_db, init_db


@pytest.fixture
def database_path():
    path = Path.cwd() / "data" / f"database-test-{uuid.uuid4().hex}.db"
    yield path
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _set_database(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(path))


def test_empty_database_is_initialized_with_current_schema(monkeypatch, database_path):
    _set_database(monkeypatch, database_path)
    init_db()

    conn = sqlite3.connect(database_path)
    try:
        names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
        assert {
            "profiles",
            "sessions",
            "runs",
            "run_events",
            "messages",
            "approvals",
            "diagnosis_reports",
            "knowledge",
            "knowledge_fts",
            "knowledge_embeddings",
            "audio_uploads",
            "operation_logs",
        }.issubset(names)
        assert "migration_history" not in names
        assert "schema_meta" not in names
        assert "permission_grants" not in names
        assert "memory_events" not in names
        assert "eval_runs" not in names
    finally:
        conn.close()


def test_initialization_is_idempotent_and_enables_wal(monkeypatch, database_path):
    _set_database(monkeypatch, database_path)
    init_db()
    init_db()

    conn = get_db()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    finally:
        conn.close()


def test_schema_constraints_reject_orphan_rows_and_invalid_approval_status(monkeypatch, database_path):
    _set_database(monkeypatch, database_path)
    init_db()

    conn = get_db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions(id, profile_id, created_at, updated_at) VALUES('s', 'missing', 'now', 'now')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO approvals(id, run_id, trace_id, session_id, profile_id, tool_name, risk_level, flow_kind, status, created_at) "
                "VALUES('a', 'r', 't', 's', 'p', 'tool', 'high', 'coach', 'cancelled', 'now')"
            )
    finally:
        conn.close()
