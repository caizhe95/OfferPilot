"""Tests for harness system: Rules, Hooks, Budget, Output Checker."""

import pytest
from app.harness.harness import (
    load_rules,
    load_diagnosis_rules,
    HookPoint,
    HookContext,
    HookManager,
    create_default_hook_manager,
    BudgetConfig,
    BudgetTracker,
    check_output,
)


class TestRulesLoader:
    """Tests for rules loading."""

    def test_load_rules(self):
        rules = load_rules()
        assert "global" in rules
        assert "diagnosis" in rules
        assert "audio" in rules
        assert len(rules["global"]) > 50

    def test_load_fixed_diagnosis_rules(self):
        rules = load_diagnosis_rules()
        assert "Global Rules" in rules or "诊断" in rules
        # Should include global + diagnosis
        assert "Scoring" in rules or "评分" in rules


class TestHookManager:
    """Tests for hook system."""

    def test_register_and_run_hook(self):
        mgr = HookManager()
        called = []

        def my_hook(ctx):
            called.append(ctx.session_id)
            return ctx

        mgr.register(HookPoint.PRE_INPUT, my_hook)
        ctx = HookContext(session_id="test-1")
        mgr.run(HookPoint.PRE_INPUT, ctx)
        assert called == ["test-1"]

    def test_default_hooks_validate_input(self):
        mgr = create_default_hook_manager()
        ctx = HookContext(session_id="test", input_text="   ")

        with pytest.raises(ValueError, match="empty"):
            mgr.run(HookPoint.PRE_INPUT, ctx)

    def test_default_hooks_accept_valid_input(self):
        mgr = create_default_hook_manager()
        ctx = HookContext(session_id="test", input_text="Hello world")
        result = mgr.run(HookPoint.PRE_INPUT, ctx)
        assert result is not None

    def test_duplicate_tool_blocked(self):
        mgr = create_default_hook_manager()
        ctx1 = HookContext(session_id="test", tool_name="search_knowledge", tool_params={"q": "x"})
        mgr.run(HookPoint.PRE_TOOL, ctx1)  # First call ok

        ctx2 = HookContext(session_id="test", tool_name="search_knowledge", tool_params={"q": "x"})
        with pytest.raises(ValueError, match="Duplicate"):
            mgr.run(HookPoint.PRE_TOOL, ctx2)

    def test_output_validation_adds_issues(self):
        mgr = create_default_hook_manager()
        ctx = HookContext(session_id="test", output_text="No dimensions here")
        result = mgr.run(HookPoint.POST_OUTPUT, ctx)
        assert "Output issues" in result.output_text


class TestBudgetTracker:
    """Tests for budget tracking."""

    def test_initial_budget(self):
        budget = BudgetTracker()
        assert budget.steps_remaining == 6
        assert budget.tool_calls_remaining == 3
        assert budget.can_continue()
        assert budget.can_call_tool()

    def test_step_recording(self):
        budget = BudgetTracker()
        budget.record_step()
        assert budget.steps_taken == 1
        assert budget.steps_remaining == 5

    def test_budget_exhausted(self):
        budget = BudgetTracker(BudgetConfig(max_steps=1, max_tool_calls=1))
        budget.record_step()
        assert not budget.can_continue()

    def test_tool_call_exhausted(self):
        budget = BudgetTracker(BudgetConfig(max_tool_calls=1))
        budget.record_tool_call()
        assert not budget.can_call_tool()

    def test_output_budget(self):
        budget = BudgetTracker(BudgetConfig(max_output_chars=100))
        budget.record_output(50)
        assert not budget.is_output_over_budget()
        budget.record_output(60)
        assert budget.is_output_over_budget()

    def test_get_status(self):
        budget = BudgetTracker()
        budget.record_step()
        budget.record_tool_call()
        status = budget.get_status()
        assert status["steps_taken"] == 1
        assert status["tool_calls_made"] == 1
        assert status["output_chars"] == 0


class TestOutputChecker:
    """Tests for output checking."""

    def test_valid_output(self):
        # A properly formatted output should pass
        output = """# 面试诊断报告

## 原始信息
**面试题：** 什么是 ReAct？

## 内容维度评分
concept_accuracy: 8/10
structure_completeness: 7/10
engineering_depth: 6/10
example_quality: 5/10
question_alignment: 8/10

## 语音维度评分
fluency: 7/10
filler_words: 6/10
redundancy: 7/10
spoken_clarity: 8/10
answer_pacing: 7/10

## 改进建议
"""
        result = check_output(output)
        assert result["valid"] is True

    def test_missing_voice_dimensions(self):
        output = "concept_accuracy: 8/10"
        result = check_output(output)
        assert result["valid"] is False
        assert any("voice" in issue.lower() or "语音" in issue for issue in result["issues"])

    def test_missing_all_dimensions(self):
        output = "No scores here"
        result = check_output(output)
        assert result["valid"] is False
        assert len(result["issues"]) >= 2  # Missing content + voice

    def test_partial_dimensions(self):
        output = "concept_accuracy: 5/10, structure_completeness: 6/10"
        result = check_output(output)
        assert not result["valid"]

