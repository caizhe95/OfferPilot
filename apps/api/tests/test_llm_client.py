"""Tests for the structured LLM adapter."""

from app.core.config import settings
from app.llm import llm_client


def test_structured_completion_uses_mock_without_network(monkeypatch):
    monkeypatch.setattr(settings, "mock_agent", True)
    monkeypatch.setattr(settings, "openai_api_key", "sk-real")

    result = llm_client.structured_json_completion(
        task_name="unit_mock",
        system_prompt="Return JSON.",
        user_payload={"input": "x"},
        fallback_factory=lambda: {"ok": True},
    )

    assert result.source == "mock"
    assert result.data == {"ok": True}


def test_structured_completion_falls_back_on_invalid_json(monkeypatch):
    monkeypatch.setattr(settings, "mock_agent", False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-real")
    monkeypatch.setattr(llm_client, "_call_openai_chat_json", lambda **kwargs: "not json")

    result = llm_client.structured_json_completion(
        task_name="unit_bad_json",
        system_prompt="Return JSON.",
        user_payload={"input": "x"},
        fallback_factory=lambda: {"fallback": True},
    )

    assert result.source == "fallback"
    assert result.data == {"fallback": True}
    assert result.error


def test_structured_completion_falls_back_on_validator_failure(monkeypatch):
    monkeypatch.setattr(settings, "mock_agent", False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-real")
    monkeypatch.setattr(llm_client, "_call_openai_chat_json", lambda **kwargs: '{"ok": false}')

    result = llm_client.structured_json_completion(
        task_name="unit_bad_schema",
        system_prompt="Return JSON.",
        user_payload={"input": "x"},
        fallback_factory=lambda: {"ok": True},
        validator=lambda data: data.get("ok") is True,
    )

    assert result.source == "fallback"
    assert result.data == {"ok": True}


def test_structured_completion_accepts_valid_json(monkeypatch):
    monkeypatch.setattr(settings, "mock_agent", False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-real")
    monkeypatch.setattr(llm_client, "_call_openai_chat_json", lambda **kwargs: '{"ok": true}')

    result = llm_client.structured_json_completion(
        task_name="unit_good_json",
        system_prompt="Return JSON.",
        user_payload={"input": "x"},
        fallback_factory=lambda: {"ok": False},
        validator=lambda data: data.get("ok") is True,
    )

    assert result.source == "llm"
    assert result.data == {"ok": True}
