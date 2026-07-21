"""Tests for the strict structured LLM adapter."""

import pytest

from app.core.config import settings
from app.llm.llm_client import LLMUnavailableError, structured_json_completion


def test_structured_completion_requires_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")

    with pytest.raises(LLMUnavailableError):
        structured_json_completion(
            task_name="unit_missing_key",
            system_prompt="Return JSON only.",
            user_payload={"input": "x"},
            validator=lambda data: True,
        )


def test_structured_completion_accepts_real_json(monkeypatch):
    def fake_call_openai_chat_json(**kwargs):
        return '{"ok": true}'

    import app.llm.llm_client as llm_client_module
    monkeypatch.setattr(llm_client_module, "_call_openai_chat_json", fake_call_openai_chat_json)

    result = structured_json_completion(
        task_name="unit_real_json",
        system_prompt="只输出 JSON：{\"ok\": true}。",
        user_payload={"input": "请返回 ok=true"},
        validator=lambda data: data.get("ok") is True,
        temperature=0.0,
    )

    assert result.source == "llm"
    assert result.data["ok"] is True
