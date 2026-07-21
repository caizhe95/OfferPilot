"""Tests for database initialization and schema."""

from app.core.database import init_db, get_db, get_table_names


def test_init_db_creates_tables():
    """Database initialization should create all expected tables."""
    init_db()
    tables = get_table_names()

    expected_tables = {
        "sessions",
        "messages",
        "memories",
        "knowledge",
        "knowledge_fts",
        "traces",
        "trace_events",
        "audit_log",
        "checkpoints",
        "progress_events",
        "diagnosis_reports",
        "eval_runs",
    }

    assert expected_tables.issubset(set(tables))


def test_init_db_is_idempotent():
    """Calling init_db multiple times should not raise errors."""
    init_db()
    init_db()
    init_db()
    # Should not raise
    assert True


def test_get_db_returns_connection():
    """get_db should return a working connection."""
    init_db()
    conn = get_db()
    try:
        result = conn.execute("SELECT 1").fetchone()
        assert result[0] == 1
    finally:
        conn.close()


def test_sessions_table_schema():
    """Sessions table should have correct columns."""
    init_db()
    conn = get_db()
    try:
        info = conn.execute("PRAGMA table_info(sessions)").fetchall()
        columns = {row["name"] for row in info}
        assert {"id", "profile_id", "status", "created_at", "updated_at", "metadata"}.issubset(columns)
    finally:
        conn.close()


def test_memories_table_has_profile_ownership_column():
    init_db()
    conn = get_db()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        assert "profile_id" in columns
    finally:
        conn.close()


def test_messages_foreign_key():
    """Messages should enforce foreign key constraint."""
    init_db()
    conn = get_db()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        import pytest

        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                ("nonexistent", "user", "hello"),
            )
    finally:
        conn.close()


def test_knowledge_fts_table():
    """Knowledge FTS table should be created."""
    init_db()
    conn = get_db()
    try:
        # FTS tables appear in sqlite_master
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()
