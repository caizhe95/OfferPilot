"""Minimal SQLite initialization contracts for the Demo application."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from offerpilot.core.config import settings
from offerpilot.database.connection import get_db, init_db
from offerpilot.runs.repository import create_run
from offerpilot.sessions.repository import create_session


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
            "run_calls",
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


def test_composite_ownership_constraints_reject_cross_profile_resources(monkeypatch, database_path):
    _set_database(monkeypatch, database_path)
    init_db()
    profile_a = "00000000-0000-4000-8000-0000000000a1"
    profile_b = "00000000-0000-4000-8000-0000000000b1"
    session_a = create_session(profile_a)
    create_session(profile_b)
    run_a, _ = create_run(profile_a, session_a["id"], "coach", {}, "ownership-test")
    now = "2026-01-01T00:00:00+00:00"
    conn = get_db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs(id, trace_id, session_id, profile_id, run_type, status, idempotency_key, created_at, updated_at, heartbeat_at) "
                "VALUES(?, ?, ?, ?, 'coach', 'pending', ?, ?, ?, ?)",
                (str(uuid.uuid4()), str(uuid.uuid4()), session_a["id"], profile_b, "cross-run", now, now, now),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_events(run_id, session_id, profile_id, sequence, event_type, created_at) VALUES(?, ?, ?, 99, 'forged', ?)",
                (run_a["id"], session_a["id"], profile_b, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_calls(run_id, session_id, profile_id, logical_call_id, call_type, started_at) VALUES(?, ?, ?, 'forged', 'llm', ?)",
                (run_a["id"], session_a["id"], profile_b, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages(session_id, profile_id, content, created_at) VALUES(?, ?, 'forged', ?)",
                (session_a["id"], profile_b, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO approvals(id, run_id, trace_id, session_id, profile_id, tool_name, risk_level, flow_kind, created_at) "
                "VALUES(?, ?, ?, ?, ?, 'tool', 'medium', 'coach', ?)",
                (str(uuid.uuid4()), run_a["id"], run_a["id"], session_a["id"], profile_b, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO diagnosis_reports(id, run_id, session_id, profile_id, question, answer, created_at) VALUES(?, ?, ?, ?, 'Q', 'A', ?)",
                (str(uuid.uuid4()), run_a["id"], session_a["id"], profile_b, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audio_uploads(id, session_id, profile_id, storage_name, original_filename, content_type, status, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, 'audio.wav', 'audio/wav', 'uploading', ?, ?)",
                (str(uuid.uuid4()), session_a["id"], profile_b, f"{uuid.uuid4().hex}.wav", now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO operation_logs(profile_id, session_id, event_type, created_at) VALUES(?, ?, 'forged', ?)",
                (profile_b, session_a["id"], now),
            )
    finally:
        conn.close()
