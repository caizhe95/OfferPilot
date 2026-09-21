"""Tests for the diagnosis context character budget."""

from offerpilot.diagnosis import context_builder


def test_context_never_exceeds_max_chars(monkeypatch):
    monkeypatch.setattr(context_builder, "_build_system_prompt", lambda: "system")
    monkeypatch.setattr(context_builder, "load_diagnosis_rules", lambda: "rules")
    monkeypatch.setattr(context_builder, "_summary_context", lambda _session_id: "summary " * 50)
    monkeypatch.setattr(context_builder, "_memory_context", lambda _profile_id: "memory " * 50)
    monkeypatch.setattr(context_builder, "_history_context", lambda _session_id, _recent_n: "history " * 50)

    result = context_builder.build_context(
        session_id="session",
        profile_id="profile",
        user_input="面试题：ReAct\n回答：推理和行动循环",
        knowledge_results=[{"kind": "interview_qa", "title": "参考", "question": "问题", "expert_answer": "答案 " * 100}],
        max_chars=300,
    )

    assert len(result) <= 300
    assert "面试题：ReAct\n回答：推理和行动循环" in result


def test_context_accounts_for_truncation_suffix(monkeypatch):
    monkeypatch.setattr(context_builder, "_build_system_prompt", lambda: "system")
    monkeypatch.setattr(context_builder, "load_diagnosis_rules", lambda: "rules")
    monkeypatch.setattr(context_builder, "_summary_context", lambda _session_id: "summary")
    monkeypatch.setattr(context_builder, "_memory_context", lambda _profile_id: "memory")
    monkeypatch.setattr(context_builder, "_history_context", lambda _session_id, _recent_n: "history")

    result = context_builder.build_context(
        session_id="session",
        profile_id="profile",
        user_input="input",
        knowledge_results=[{"kind": "interview_qa", "title": "参考", "expert_answer": "x" * 100}],
        max_chars=120,
    )

    assert len(result) <= 120
