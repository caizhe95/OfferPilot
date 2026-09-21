"""Coach context-budget regressions."""

import pytest

from offerpilot.coach.context import HISTORY_CHARS, bounded_history, classify_coach_intent, tool_names_for_turn


def test_coach_history_keeps_the_complete_latest_input_within_budget():
    current_input = "x" * HISTORY_CHARS
    history = bounded_history([
        {"role": "assistant", "content": "earlier response"},
        {"role": "user", "content": current_input},
    ])
    assert history == [{"role": "user", "content": current_input}]


@pytest.mark.parametrize("text", ["给我的回答评分", "你觉得几分", "请评价一下", "evaluate this answer", "rate my answer", "请做诊断"])
def test_coach_intent_recognizes_formal_diagnosis_requests(text):
    assert classify_coach_intent(text) == "diagnosis_request"
    assert tool_names_for_turn([{"role": "user", "content": text}]) == set()
