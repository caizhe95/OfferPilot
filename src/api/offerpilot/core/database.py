"""SQLite database connection and schema initialization."""

import sqlite3
from pathlib import Path
from offerpilot.core.config import settings

_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    profile_id TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    profile_id TEXT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT DEFAULT '',
    dimension TEXT DEFAULT '',
    kind TEXT DEFAULT 'interview_qa',
    category TEXT DEFAULT '',
    question TEXT DEFAULT '',
    novice_answer TEXT DEFAULT '',
    expert_answer TEXT DEFAULT '',
    exam_points TEXT DEFAULT '[]',
    common_gaps TEXT DEFAULT '[]',
    followups TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title,
    question,
    expert_answer,
    novice_answer,
    exam_points,
    common_gaps,
    tags,
    category,
    source,
    content=knowledge,
    content_rowid=id
);

CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    step_index INTEGER,
    data TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    action TEXT NOT NULL,
    params TEXT DEFAULT '{}',
    result TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    trace_id TEXT DEFAULT '',
    run_kind TEXT DEFAULT '',
    state TEXT NOT NULL,
    progress TEXT DEFAULT '[]',
    messages TEXT DEFAULT '[]',
    knowledge TEXT DEFAULT '[]',
    memory_keys TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS progress_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS diagnosis_reports (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    content_scores TEXT DEFAULT '{}',
    voice_scores TEXT DEFAULT '{}',
    overall_score REAL,
    report_markdown TEXT DEFAULT '',
    diagnosis_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    eval_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    results TEXT DEFAULT '[]',
    summary TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS coach_runs (
    session_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    status TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    run_kind TEXT NOT NULL DEFAULT 'coach',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    flow_kind TEXT NOT NULL DEFAULT 'legacy',
    trace_id TEXT DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}',
    public_params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT DEFAULT '',
    expires_at TEXT DEFAULT '',
    executed_at TEXT DEFAULT '',
    error TEXT DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audio_uploads (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    storage_name TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT DEFAULT '',
    error TEXT DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    CHECK (status IN ('uploading', 'pending_approval', 'transcribing', 'completed', 'failed', 'denied', 'expired', 'deleted'))
);

CREATE TABLE IF NOT EXISTS permission_grants (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memories_profile ON memories(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_knowledge ON knowledge_embeddings(knowledge_id, embedding_model);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
CREATE INDEX IF NOT EXISTS idx_trace_events_trace ON trace_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_session ON audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id);
CREATE INDEX IF NOT EXISTS idx_progress_events_session ON progress_events(session_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_reports_session ON diagnosis_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_approval_requests_session ON approval_requests(session_id, status);
CREATE INDEX IF NOT EXISTS idx_audio_uploads_owner_status ON audio_uploads(session_id, profile_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_coach_runs_status_updated ON coach_runs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_permission_grants_session ON permission_grants(session_id, profile_id, tool_name);
""".strip()


def get_db() -> sqlite3.Connection:
    """Get a new database connection."""
    db_path = settings.db_path
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Initialize database schema. Safe to call multiple times."""
    conn = get_db()
    try:
        # WAL is a database-wide mode. Setting it for every request connection
        # can contend with concurrent readers and writers.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA_SQL)
        _migrate_knowledge_schema(conn)
        _migrate_diagnosis_reports(conn)
        _migrate_profile_ownership(conn)
        _migrate_runtime_state(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_knowledge_schema(conn: sqlite3.Connection) -> None:
    """Add first-round knowledge columns and rebuild outdated FTS schema."""
    rows = conn.execute("PRAGMA table_info(knowledge)").fetchall()
    existing = {row["name"] for row in rows}
    columns = {
        "kind": "TEXT DEFAULT 'interview_qa'",
        "category": "TEXT DEFAULT ''",
        "question": "TEXT DEFAULT ''",
        "novice_answer": "TEXT DEFAULT ''",
        "expert_answer": "TEXT DEFAULT ''",
        "exam_points": "TEXT DEFAULT '[]'",
        "common_gaps": "TEXT DEFAULT '[]'",
        "followups": "TEXT DEFAULT '[]'",
        "tags": "TEXT DEFAULT '[]'",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE knowledge ADD COLUMN {name} {ddl}")

    if "category" not in existing and "dimension" in existing:
        conn.execute("UPDATE knowledge SET category = COALESCE(NULLIF(dimension, ''), category)")

    fts_rows = conn.execute("PRAGMA table_info(knowledge_fts)").fetchall()
    fts_columns = {row["name"] for row in fts_rows}
    required_fts = {
        "title", "question", "expert_answer", "novice_answer",
        "exam_points", "common_gaps", "tags", "category", "source",
    }
    if fts_columns and not required_fts.issubset(fts_columns):
        conn.execute("DROP TABLE IF EXISTS knowledge_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                title,
                question,
                expert_answer,
                novice_answer,
                exam_points,
                common_gaps,
                tags,
                category,
                source,
                content=knowledge,
                content_rowid=id
            )
            """
        )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_kind_category ON knowledge(kind, category)")

    checkpoint_rows = conn.execute("PRAGMA table_info(checkpoints)").fetchall()
    checkpoint_existing = {row["name"] for row in checkpoint_rows}
    checkpoint_columns = {
        "trace_id": "TEXT DEFAULT ''",
        "run_kind": "TEXT DEFAULT ''",
    }
    for name, ddl in checkpoint_columns.items():
        if name not in checkpoint_existing:
            conn.execute(f"ALTER TABLE checkpoints ADD COLUMN {name} {ddl}")


def _migrate_diagnosis_reports(conn: sqlite3.Connection) -> None:
    """Add structured diagnosis persistence to existing databases."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(diagnosis_reports)").fetchall()}
    if "diagnosis_json" not in columns:
        conn.execute("ALTER TABLE diagnosis_reports ADD COLUMN diagnosis_json TEXT DEFAULT '{}'")


def _migrate_profile_ownership(conn: sqlite3.Connection) -> None:
    """Add anonymous profile ownership without assigning old records."""
    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "profile_id" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN profile_id TEXT")

    memory_columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "profile_id" not in memory_columns:
        conn.execute("ALTER TABLE memories ADD COLUMN profile_id TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_profile ON memories(profile_id, created_at)")


def _migrate_runtime_state(conn: sqlite3.Connection) -> None:
    """Extend existing run and approval tables without rewriting user data."""
    coach_columns = {row["name"] for row in conn.execute("PRAGMA table_info(coach_runs)").fetchall()}
    for name, ddl in {
        "run_kind": "TEXT NOT NULL DEFAULT 'coach'",
        "started_at": "TEXT DEFAULT ''",
        "completed_at": "TEXT DEFAULT ''",
    }.items():
        if name not in coach_columns:
            conn.execute(f"ALTER TABLE coach_runs ADD COLUMN {name} {ddl}")
    conn.execute("UPDATE coach_runs SET started_at = COALESCE(NULLIF(started_at, ''), updated_at)")

    approval_columns = {row["name"] for row in conn.execute("PRAGMA table_info(approval_requests)").fetchall()}
    for name, ddl in {
        "flow_kind": "TEXT NOT NULL DEFAULT 'legacy'",
        "trace_id": "TEXT DEFAULT ''",
        "public_params": "TEXT NOT NULL DEFAULT '{}'",
        "expires_at": "TEXT DEFAULT ''",
        "executed_at": "TEXT DEFAULT ''",
        "error": "TEXT DEFAULT ''",
    }.items():
        if name not in approval_columns:
            conn.execute(f"ALTER TABLE approval_requests ADD COLUMN {name} {ddl}")

    # Older rows did not record their origin or redact execution parameters.
    # They must not be resumable, because they cannot be authenticated safely.
    conn.execute(
        """
        UPDATE approval_requests
        SET status = 'expired', resolved_at = datetime('now'), error = 'legacy approval cannot be verified'
        WHERE flow_kind = 'legacy' AND status IN ('pending', 'approved')
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS permission_grants (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT DEFAULT '',
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coach_runs_status_updated ON coach_runs(status, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_permission_grants_session ON permission_grants(session_id, profile_id, tool_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_requests_flow ON approval_requests(session_id, profile_id, flow_kind, status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_uploads (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            storage_name TEXT NOT NULL UNIQUE,
            original_filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT DEFAULT '',
            error TEXT DEFAULT '',
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            CHECK (status IN ('uploading', 'pending_approval', 'transcribing', 'completed', 'failed', 'denied', 'expired', 'deleted'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audio_uploads_owner_status ON audio_uploads(session_id, profile_id, status, updated_at)")


def get_table_names() -> list[str]:
    """Return list of all table names for verification."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]
    finally:
        conn.close()
