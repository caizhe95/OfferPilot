CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    title_source TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    UNIQUE(id, profile_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    run_type TEXT NOT NULL CHECK(run_type IN ('coach', 'diagnosis', 'audio_transcription', 'report_export')),
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled', 'interrupted')),
    idempotency_key TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    cancel_requested_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    UNIQUE(id, profile_id),
    UNIQUE(id, session_id, profile_id),
    UNIQUE(profile_id, session_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    run_id TEXT,
    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE,
    UNIQUE(id, profile_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    flow_kind TEXT NOT NULL CHECK(flow_kind IN ('coach', 'audio', 'export')),
    params TEXT NOT NULL DEFAULT '{}',
    public_params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'denied', 'executing', 'executed', 'failed', 'expired')),
    decision TEXT NOT NULL DEFAULT '' CHECK(decision IN ('', 'approve', 'deny')),
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    executed_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    UNIQUE(id, run_id, session_id, profile_id)
);

CREATE TABLE IF NOT EXISTS profile_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    source_session_id TEXT,
    source_run_id TEXT,
    source_report_id TEXT,
    approval_id TEXT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    FOREIGN KEY (source_session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (source_run_id, source_session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (source_report_id, source_session_id, profile_id) REFERENCES diagnosis_reports(id, session_id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id, source_run_id, source_session_id, profile_id)
        REFERENCES approvals(id, run_id, session_id, profile_id) ON DELETE CASCADE,
    UNIQUE(approval_id)
);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    summary_version INTEGER NOT NULL DEFAULT 0,
    source_message_id INTEGER,
    summary_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (source_message_id, profile_id) REFERENCES messages(id, profile_id) ON DELETE CASCADE,
    UNIQUE(session_id, profile_id)
);

CREATE TABLE IF NOT EXISTS session_followups (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    source_report_id TEXT,
    exam_point_id TEXT,
    question TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'answered')),
    linked_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (source_report_id, session_id, profile_id) REFERENCES diagnosis_reports(id, session_id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (linked_run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS profile_growth_summaries (
    profile_id TEXT PRIMARY KEY,
    summary_version INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS diagnosis_reports (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    content_scores TEXT NOT NULL DEFAULT '{}',
    voice_scores TEXT NOT NULL DEFAULT '{}',
    overall_score REAL,
    report_markdown TEXT NOT NULL DEFAULT '',
    diagnosis_json TEXT NOT NULL DEFAULT '{}',
    sources TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    UNIQUE(id, profile_id),
    UNIQUE(id, session_id, profile_id)
);

CREATE TABLE IF NOT EXISTS diagnosis_point_results (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    exam_point_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('covered', 'partial', 'missing')),
    evidence TEXT,
    explanation TEXT NOT NULL DEFAULT '',
    source_knowledge_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (report_id, profile_id) REFERENCES diagnosis_reports(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (source_knowledge_id) REFERENCES knowledge(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    dimension TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'interview_qa',
    category TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    novice_answer TEXT NOT NULL DEFAULT '',
    expert_answer TEXT NOT NULL DEFAULT '',
    exam_points TEXT NOT NULL DEFAULT '[]',
    common_gaps TEXT NOT NULL DEFAULT '[]',
    followups TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, question, expert_answer, novice_answer, exam_points,
    common_gaps, tags, category, source,
    content=knowledge, content_rowid=id
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

CREATE TABLE IF NOT EXISTS knowledge_exam_points (
    id TEXT PRIMARY KEY,
    knowledge_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    source_key TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id) ON DELETE CASCADE,
    UNIQUE(knowledge_id, source_key)
);

CREATE TABLE IF NOT EXISTS audio_uploads (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    run_id TEXT,
    storage_name TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT,
    session_id TEXT,
    run_id TEXT,
    audience TEXT NOT NULL DEFAULT 'machine' CHECK(audience IN ('machine', 'human')),
    level TEXT NOT NULL DEFAULT 'info',
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id, profile_id) REFERENCES sessions(id, profile_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, session_id, profile_id) REFERENCES runs(id, session_id, profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_runs_trace_id ON runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_trace ON approvals(trace_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_profile ON profile_memories(profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_followups_session ON session_followups(session_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_reports_session ON diagnosis_reports(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_point_results_report ON diagnosis_point_results(report_id, exam_point_id);
CREATE INDEX IF NOT EXISTS idx_audio_owner ON audio_uploads(session_id, profile_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_operation_logs_run ON operation_logs(run_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_run_per_session
    ON runs(session_id)
    WHERE status IN ('pending', 'running', 'waiting_approval');
CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_transcript_per_run
    ON messages(run_id, kind)
    WHERE run_id IS NOT NULL AND kind = 'audio_transcript';
