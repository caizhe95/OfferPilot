"""Harness system: Rules loader, Hooks, Budget, and Output Checker.

The Harness is the engineering constraint layer that controls Agent behavior
boundaries, tool call permissions, output quality, and observability.
"""

import re
from pathlib import Path
from typing import Callable, Any
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Rules Loader
# ---------------------------------------------------------------------------

def _find_rules_dir() -> Path:
    current = Path(__file__).resolve().parent
    # Go up from apps/api/app/harness -> lite-offerpilot root
    root = current.parent.parent.parent.parent
    return root / "harness" / "rules"


def load_rules() -> dict[str, str]:
    """Load all rules files from harness/rules/."""
    rules_dir = _find_rules_dir()
    rules = {}
    if not rules_dir.exists():
        return rules

    for rule_file in sorted(rules_dir.glob("*.rules.md")):
        name = rule_file.stem.replace(".rules", "")
        rules[name] = rule_file.read_text(encoding="utf-8")

    return rules


def load_rules_for_skill(skill_name: str) -> str:
    """Load rules relevant to a specific skill."""
    all_rules = load_rules()
    parts = [all_rules.get("global", "")]

    # Diagnosis skill gets diagnosis rules
    if skill_name in ("interview-diagnosis", "answer-rewrite", "followup-coaching"):
        parts.append(all_rules.get("diagnosis", ""))

    # Audio skill gets audio rules
    if skill_name == "audio-diagnosis":
        parts.append(all_rules.get("audio", ""))
        parts.append(all_rules.get("diagnosis", ""))

    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

class HookPoint(str, Enum):
    PRE_INPUT = "pre_input"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    POST_OUTPUT = "post_output"


@dataclass
class HookContext:
    session_id: str = ""
    skill_name: str = ""
    step: int = 0
    tool_name: str = ""
    tool_params: dict = field(default_factory=dict)
    tool_result: Any = None
    input_text: str = ""
    output_text: str = ""


HookFunc = Callable[[HookContext], HookContext | None]


class HookManager:
    """Manages lifecycle hooks for Agent execution."""

    def __init__(self):
        self._hooks: dict[HookPoint, list[HookFunc]] = {
            HookPoint.PRE_INPUT: [],
            HookPoint.PRE_TOOL: [],
            HookPoint.POST_TOOL: [],
            HookPoint.POST_OUTPUT: [],
        }

    def register(self, point: HookPoint, hook: HookFunc) -> None:
        self._hooks[point].append(hook)

    def run(self, point: HookPoint, ctx: HookContext) -> HookContext:
        """Run all hooks for a point. Hooks can modify context."""
        for hook in self._hooks[point]:
            result = hook(ctx)
            if result is not None:
                ctx = result
        return ctx


def create_default_hook_manager() -> HookManager:
    """Create a HookManager with default built-in hooks."""
    mgr = HookManager()

    # Pre-input: validate input is non-empty
    def validate_input(ctx: HookContext) -> HookContext | None:
        if not ctx.input_text.strip():
            raise ValueError("Input must not be empty")
        return None

    # Pre-tool: check for duplicate tool calls
    _tool_call_history: list[str] = []

    def check_duplicate_tool(ctx: HookContext) -> HookContext | None:
        call_key = f"{ctx.tool_name}:{str(ctx.tool_params)}"
        if call_key in _tool_call_history:
            raise ValueError(f"Duplicate tool call blocked: {ctx.tool_name}")
        _tool_call_history.append(call_key)
        return None

    # Post-output: validate output structure
    def validate_output(ctx: HookContext) -> HookContext | None:
        output = ctx.output_text
        issues = []

        # Must contain content dimension scores
        if "内容维度" not in output and "content" not in output.lower():
            issues.append("Output missing content dimension scores")

        # Must contain voice dimension scores
        if "语音维度" not in output and "voice" not in output.lower():
            issues.append("Output missing voice dimension scores")

        if issues:
            ctx.output_text += f"\n\n<!-- Output issues: {'; '.join(issues)} -->"
        return ctx

    mgr.register(HookPoint.PRE_INPUT, validate_input)
    mgr.register(HookPoint.PRE_TOOL, check_duplicate_tool)
    mgr.register(HookPoint.POST_OUTPUT, validate_output)

    return mgr


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@dataclass
class BudgetConfig:
    max_steps: int = 6
    max_tool_calls: int = 3
    max_output_chars: int = 2500
    top_k_retrieval: int = 5


class BudgetTracker:
    """Tracks and enforces Agent execution budget."""

    def __init__(self, config: BudgetConfig | None = None):
        self.config = config or BudgetConfig()
        self.steps_taken = 0
        self.tool_calls_made = 0
        self.output_chars = 0

    @property
    def steps_remaining(self) -> int:
        return max(0, self.config.max_steps - self.steps_taken)

    @property
    def tool_calls_remaining(self) -> int:
        return max(0, self.config.max_tool_calls - self.tool_calls_made)

    def can_continue(self) -> bool:
        return self.steps_remaining > 0

    def can_call_tool(self) -> bool:
        return self.tool_calls_remaining > 0

    def record_step(self) -> None:
        self.steps_taken += 1

    def record_tool_call(self) -> None:
        self.tool_calls_made += 1

    def record_output(self, chars: int) -> None:
        self.output_chars += chars

    def is_output_over_budget(self) -> bool:
        return self.output_chars > self.config.max_output_chars

    def get_status(self) -> dict:
        return {
            "steps_taken": self.steps_taken,
            "steps_max": self.config.max_steps,
            "tool_calls_made": self.tool_calls_made,
            "tool_calls_max": self.config.max_tool_calls,
            "output_chars": self.output_chars,
            "output_max": self.config.max_output_chars,
        }


class HarnessRunner:
    """Runtime harness used by FastAPI orchestration paths."""

    TOOL_REQUIRED_PARAMS = {
        "search_knowledge": {"query"},
        "score_answer": {"question", "answer"},
        "analyze_voice_text": {"transcript"},
        "generate_followup": {"question", "answer"},
        "save_memory": {"session_id", "key", "value"},
        "transcribe_audio": {"filepath"},
        "export_report": {"report_id"},
    }

    def __init__(self, session_id: str, skill_name: str = ""):
        self.session_id = session_id
        self.skill_name = skill_name
        self.hooks = create_default_hook_manager()
        self.budget = BudgetTracker()

    def pre_input(self, input_text: str) -> tuple[str, dict]:
        cleaned = re.sub(r"\s+", " ", input_text).strip()
        ctx = HookContext(
            session_id=self.session_id,
            skill_name=self.skill_name,
            input_text=cleaned,
        )
        self.hooks.run(HookPoint.PRE_INPUT, ctx)
        self.budget.record_step()
        qa_extracted = {
            "has_question": any(token in cleaned for token in ["面试题", "问题", "question", "？", "?"]),
            "has_answer": any(token in cleaned for token in ["回答", "answer", "候选"]),
        }
        return ctx.input_text, qa_extracted

    def record_step(self) -> None:
        if not self.budget.can_continue():
            raise ValueError("Agent step budget exceeded")
        self.budget.record_step()

    def pre_tool(self, tool_name: str, params: dict) -> dict:
        if tool_name not in self.TOOL_REQUIRED_PARAMS:
            raise ValueError(f"Tool not allowed: {tool_name}")
        missing = self.TOOL_REQUIRED_PARAMS[tool_name] - set(params.keys())
        if missing:
            raise ValueError(f"Missing required params for {tool_name}: {sorted(missing)}")
        if not self.budget.can_call_tool():
            raise ValueError("Tool call budget exceeded")
        ctx = HookContext(
            session_id=self.session_id,
            skill_name=self.skill_name,
            tool_name=tool_name,
            tool_params=params,
        )
        self.hooks.run(HookPoint.PRE_TOOL, ctx)
        self.budget.record_tool_call()
        return ctx.tool_params

    def post_tool(self, tool_name: str, result: Any) -> Any:
        normalized = result
        if isinstance(result, str) and len(result) > 2000:
            normalized = result[:2000] + "...(tool result truncated)"
        elif isinstance(result, dict):
            normalized = dict(result)
            if "results" in normalized and isinstance(normalized["results"], list):
                for item in normalized["results"]:
                    if isinstance(item, dict) and "source" not in item:
                        item["source"] = item.get("source_file", item.get("title", "unknown"))
            text = str(normalized)
            if len(text) > 4000:
                normalized = {"truncated": True, "tool_name": tool_name, "summary": text[:2000]}
        else:
            normalized = {"result": result}

        ctx = HookContext(
            session_id=self.session_id,
            skill_name=self.skill_name,
            tool_name=tool_name,
            tool_result=normalized,
        )
        self.hooks.run(HookPoint.POST_TOOL, ctx)
        return ctx.tool_result

    def post_output(self, output_text: str) -> tuple[str, dict]:
        self.budget.record_output(len(output_text))
        ctx = HookContext(
            session_id=self.session_id,
            skill_name=self.skill_name,
            output_text=output_text,
        )
        self.hooks.run(HookPoint.POST_OUTPUT, ctx)
        result = check_output(ctx.output_text)
        if self.budget.is_output_over_budget():
            result["valid"] = False
            result["issues"].append("Output budget exceeded")
        return ctx.output_text, result


# ---------------------------------------------------------------------------
# Output Checker
# ---------------------------------------------------------------------------

def check_output(output: str) -> dict:
    """Check Agent output against quality requirements.

    Returns dict with:
        valid: bool
        issues: list[str]
        recommendations: list[str]
    """
    issues = []
    recommendations = []

    # Check for content dimension scores
    content_dims = ["concept_accuracy", "structure_completeness",
                    "engineering_depth", "example_quality", "question_alignment"]
    content_found = []
    for dim in content_dims:
        if dim in output.lower():
            content_found.append(dim)
    if len(content_found) < 5:
        missing = set(content_dims) - set(content_found)
        issues.append(f"Missing content dimensions: {missing}")

    # Check for voice dimension scores
    voice_dims = ["fluency", "filler_words", "redundancy",
                  "spoken_clarity", "answer_pacing"]
    voice_found = []
    for dim in voice_dims:
        if dim in output.lower():
            voice_found.append(dim)
    if len(voice_found) < 5:
        missing = set(voice_dims) - set(voice_found)
        issues.append(f"Missing voice dimensions: {missing}")

    # Check for original question preservation
    if "面试题" not in output and "原始" not in output:
        issues.append("Missing original question")

    # Check for improvement advice
    if "改进" not in output and "建议" not in output and "improve" not in output.lower():
        issues.append("Missing improvement advice")

    valid = len(issues) == 0

    if not valid:
        recommendations.append("Consider adding missing sections before finalizing")

    return {
        "valid": valid,
        "issues": issues,
        "recommendations": recommendations,
    }


def generate_fallback_report(question: str = "", answer: str = "") -> str:
    """Generate a minimal fallback report when output checking fails."""
    return f"""# 面试诊断报告（部分）

## 原始信息

**面试题：** {question or "未提供"}

**候选回答：** {answer or "未提供"}

## 内容维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 概念准确性 | */10 | 自动评分未完成 |
| 结构完整性 | */10 | 自动评分未完成 |
| 工程深度 | */10 | 自动评分未完成 |
| 示例质量 | */10 | 自动评分未完成 |
| 问题契合度 | */10 | 自动评分未完成 |

## 语音维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 流畅度 | */10 | 自动评分未完成 |
| 口头禅控制 | */10 | 自动评分未完成 |
| 冗余度 | */10 | 自动评分未完成 |
| 口语清晰度 | */10 | 自动评分未完成 |
| 回答节奏 | */10 | 自动评分未完成 |

## 说明

自动诊断未能完成完整分析。请检查输入是否完整，或稍后重试。

---

*Report generated by OfferPilot Lite (fallback mode)*
"""
