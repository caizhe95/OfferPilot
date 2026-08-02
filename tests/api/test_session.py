"""Tests for session state machine, progress events, and checkpoints."""

import pytest
from offerpilot.core.database import init_db
from offerpilot.session.session import (
    create_session,
    get_session,
    transition_session,
    add_message,
    get_recent_messages,
    add_progress_event,
    get_progress_events,
    save_checkpoint,
    get_checkpoint,
    get_latest_checkpoint,
    list_sessions,
)


class TestSessionLifecycle:
    """Tests for session creation and state transitions."""

    def test_create_session(self):
        init_db()
        session = create_session()
        assert "id" in session
        assert session["status"] == "ready"
        assert "created_at" in session

    def test_get_session(self):
        init_db()
        session = create_session()
        fetched = get_session(session["id"])
        assert fetched is not None
        assert fetched["id"] == session["id"]
        assert fetched["status"] == "ready"

    def test_get_nonexistent_session(self):
        init_db()
        assert get_session("nonexistent-id") is None

    def test_valid_transitions(self):
        init_db()
        session = create_session()

        # ready -> running
        s = transition_session(session["id"], "running")
        assert s is not None
        assert s["status"] == "running"

        # running -> waiting_approval
        s = transition_session(session["id"], "waiting_approval")
        assert s is not None
        assert s["status"] == "waiting_approval"

        # waiting_approval -> ready
        s = transition_session(session["id"], "ready")
        assert s is not None
        assert s["status"] == "ready"

    def test_invalid_transition_raises(self):
        init_db()
        session = create_session()

        # ready -> completed (invalid)
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_session(session["id"], "completed")

        # ready -> waiting_approval (invalid)
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_session(session["id"], "waiting_approval")

    def test_failed_status_no_transition(self):
        init_db()
        session = create_session()
        transition_session(session["id"], "running")
        transition_session(session["id"], "failed")

        # failed -> anything should fail
        with pytest.raises(ValueError):
            transition_session(session["id"], "running")

    def test_cancelled_from_ready(self):
        init_db()
        session = create_session()
        s = transition_session(session["id"], "cancelled")
        assert s is not None
        assert s["status"] == "cancelled"

    def test_ready_after_turn(self):
        init_db()
        session = create_session()
        transition_session(session["id"], "running")
        s = transition_session(session["id"], "ready")
        assert s is not None
        assert s["status"] == "ready"
        s = transition_session(session["id"], "running")
        assert s is not None
        assert s["status"] == "running"


class TestMessages:
    """Tests for message management."""

    def test_add_message(self):
        init_db()
        session = create_session()
        msg = add_message(session["id"], "user", "Hello")
        assert msg is not None
        assert msg["role"] == "user"
        assert msg["content"] == "Hello"
        assert msg["session_id"] == session["id"]

    def test_get_recent_messages(self):
        init_db()
        session = create_session()
        for i in range(15):
            add_message(session["id"], "user", f"Message {i}")

        msgs = get_recent_messages(session["id"], n=5)
        assert len(msgs) == 5
        # Should be most recent 5
        assert msgs[-1]["content"] == "Message 14"

    def test_recent_messages_window_truncation(self):
        init_db()
        session = create_session()
        for i in range(20):
            add_message(session["id"], "user", f"Message {i}")

        msgs = get_recent_messages(session["id"], n=10)
        assert len(msgs) == 10
        assert msgs[-1]["content"] == "Message 19"
        assert msgs[0]["content"] == "Message 10"


class TestProgressEvents:
    """Tests for progress event tracking."""

    def test_add_valid_progress(self):
        init_db()
        session = create_session()
        event = add_progress_event(session["id"], "input_received")
        assert event is not None
        assert event["stage"] == "input_received"
        assert event["session_id"] == session["id"]

    def test_add_invalid_progress_raises(self):
        init_db()
        session = create_session()
        with pytest.raises(ValueError, match="Invalid progress stage"):
            add_progress_event(session["id"], "invalid_stage")

    def test_get_progress_events(self):
        init_db()
        session = create_session()
        add_progress_event(session["id"], "input_received")
        add_progress_event(session["id"], "qa_extracted")
        add_progress_event(session["id"], "knowledge_retrieved")

        events = get_progress_events(session["id"])
        assert len(events) == 3
        assert [e["stage"] for e in events] == ["input_received", "qa_extracted", "knowledge_retrieved"]


class TestCheckpoints:
    """Tests for checkpoint save/restore."""

    def test_save_and_get_checkpoint(self):
        init_db()
        session = create_session()
        cp = save_checkpoint(
            session["id"],
            state="running",
            progress=["input_received", "qa_extracted"],
            messages=[{"role": "user", "content": "test"}],
            knowledge=["context-window"],
            memory_keys=["weakness"],
            trace_id="trace-123",
            run_kind="diagnose",
        )
        assert cp is not None
        assert cp["state"] == "running"
        assert cp["progress"] == ["input_received", "qa_extracted"]
        assert cp["trace_id"] == "trace-123"
        assert cp["run_kind"] == "diagnose"

        retrieved = get_checkpoint(cp["id"])
        assert retrieved is not None
        assert retrieved["state"] == "running"
        assert retrieved["progress"] == ["input_received", "qa_extracted"]
        assert retrieved["knowledge"] == ["context-window"]
        assert retrieved["trace_id"] == "trace-123"
        assert retrieved["run_kind"] == "diagnose"

    def test_checkpoint_trace_attribution(self):
        init_db()
        session = create_session()
        cp = save_checkpoint(
            session["id"],
            state="completed",
            trace_id="trace-abc",
            run_kind="chat",
        )
        assert cp["trace_id"] == "trace-abc"
        assert cp["run_kind"] == "chat"
        latest = get_latest_checkpoint(session["id"])
        assert latest["trace_id"] == "trace-abc"
        assert latest["run_kind"] == "chat"

    def test_get_latest_checkpoint(self):
        init_db()
        session = create_session()
        cp1 = save_checkpoint(session["id"], state="running")
        cp2 = save_checkpoint(session["id"], state="waiting_approval")
        assert cp2 is not None

        latest = get_latest_checkpoint(session["id"])
        assert latest is not None
        assert latest["id"] == cp2["id"]
        assert latest["state"] == "waiting_approval"

    def test_get_nonexistent_checkpoint(self):
        init_db()
        assert get_checkpoint("nonexistent") is None

    def test_no_checkpoint_for_session(self):
        init_db()
        assert get_latest_checkpoint("nonexistent") is None


class TestListSessions:
    """Tests for session listing."""

    def test_list_sessions(self):
        init_db()
        s1 = create_session()
        s2 = create_session()

        sessions = list_sessions()
        assert sessions is not None
        ids = [s["id"] for s in sessions]
        assert s2["id"] in ids  # most recent first
        assert s1["id"] in ids

    def test_list_sessions_by_status(self):
        init_db()
        create_session()
        s = create_session()
        transition_session(s["id"], "running")

        running = list_sessions(status="running")
        assert running is not None
        assert len(running) == 1
        assert running[0]["status"] == "running"
