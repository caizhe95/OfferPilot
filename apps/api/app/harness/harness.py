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


def load_diagnosis_rules() -> str:
    """Load the fixed rules for the single-question diagnosis workflow."""
    all_rules = load_rules()
    return "\n\n".join(p for p in (all_rules.get("global", ""), all_rules.get("diagnosis", "")) if p)


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
    max_output_chars: int = 5000
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
        "search_knowledge": {"question"},
        "diagnose_interview": {"question", "answer", "reference_answers", "context_instruction", "timeout"},
        "save_memory": {"session_id", "key", "value"},
        "transcribe_audio": {"filepath"},
        "export_report": {"report_id"},
    }

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.hooks = create_default_hook_manager()
        self.budget = BudgetTracker()

    def pre_input(self, input_text: str) -> tuple[str, dict]:
        cleaned = re.sub(r"\s+", " ", input_text).strip()
        ctx = HookContext(
            session_id=self.session_id,
            input_text=cleaned,
        )
        self.hooks.run(HookPoint.PRE_INPUT, ctx)
        self.budget.record_step()
        qa_extracted = extract_diagnosis_qa(cleaned)
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
            tool_name=tool_name,
            tool_result=normalized,
        )
        self.hooks.run(HookPoint.POST_TOOL, ctx)
        return ctx.tool_result

    def post_output(self, output_text: str | dict) -> tuple[str, dict]:
        """Run post-output hooks and validate output.

        Accepts both raw text and structured report dict.
        """
        if isinstance(output_text, dict):
            # Structured contract validation
            result = validate_report_structure(output_text)
            text = render_report_markdown(output_text)
        else:
            # Legacy text-based check
            text = output_text
            result = check_output(output_text)

        self.budget.record_output(len(text))
        ctx = HookContext(
            session_id=self.session_id,
            output_text=text,
        )
        self.hooks.run(HookPoint.POST_OUTPUT, ctx)
        if self.budget.is_output_over_budget():
            result["valid"] = False
            result["issues"].append("Output budget exceeded")
        return ctx.output_text, result


# ---------------------------------------------------------------------------
# Output Checker (structural contract)
# ---------------------------------------------------------------------------

REQUIRED_REPORT_FIELDS = [
    "question",
    "answer",
    "overall_score",
    "content_scores",
    "voice_scores",
    "followups",
    "sources",
    "user_covered",
    "user_missing",
    "exam_points",
]

REQUIRED_REPORT_SECTIONS = [
    "原始面试题",
    "参考答案对标",
    "用户已覆盖",
    "用户缺失",
    "内容维度评分",
    "语音维度评分",
    "改进建议",
    "可能追问",
    "知识来源",
]


def validate_report_structure(report: dict) -> dict:
    """Validate a structured diagnosis report against the internal contract.

    Accepts a structured report dict (not Markdown text).
    Returns dict with valid, issues, recommendations.
    """
    issues: list[str] = []
    recommendations: list[str] = []

    if not isinstance(report, dict):
        return {
            "valid": False,
            "issues": ["Report is not a structured object"],
            "recommendations": ["Generate a structured report object first"],
        }

    # Required top-level fields
    for field in REQUIRED_REPORT_FIELDS:
        value = report.get(field)
        if value is None:
            issues.append(f"Missing required field: {field}")

    # Content scores
    cs = report.get("content_scores")
    if isinstance(cs, dict):
        dims = cs.get("dimensions")
        if isinstance(dims, dict):
            expected_dims = {"concept_accuracy", "structure_completeness",
                            "engineering_depth", "example_quality", "question_alignment"}
            missing = expected_dims - set(dims.keys())
            if missing:
                issues.append(f"Missing content dimensions: {missing}")
            for k, v in dims.items():
                if not isinstance(v, dict) or "score" not in v or "explanation" not in v:
                    issues.append(f"Invalid content dimension entry: {k}")
        else:
            issues.append("content_scores.dimensions is missing or not a dict")
    else:
        issues.append("content_scores is missing")

    # Voice scores
    vs = report.get("voice_scores")
    if isinstance(vs, dict):
        dims = vs.get("dimensions")
        if isinstance(dims, dict):
            expected_dims = {"fluency", "filler_words", "redundancy",
                            "spoken_clarity", "answer_pacing"}
            missing = expected_dims - set(dims.keys())
            if missing:
                issues.append(f"Missing voice dimensions: {missing}")
            for k, v in dims.items():
                if not isinstance(v, dict) or "score" not in v or "explanation" not in v:
                    issues.append(f"Invalid voice dimension entry: {k}")
        else:
            issues.append("voice_scores.dimensions is missing or not a dict")
    else:
        issues.append("voice_scores is missing")

    # Overall score
    os_ = report.get("overall_score")
    if not isinstance(os_, (int, float)):
        issues.append("overall_score must be a number")

    # Followups
    fu = report.get("followups")
    if not isinstance(fu, list):
        issues.append("followups must be a list")
    else:
        for i, item in enumerate(fu):
            if not isinstance(item, dict) or "question" not in item or "why" not in item:
                issues.append(f"Invalid followup entry at index {i}")

    # Sources
    src = report.get("sources")
    if not isinstance(src, list):
        issues.append("sources must be a list")

    points = report.get("exam_points")
    if not isinstance(points, list):
        issues.append("exam_points must be a list")
    else:
        for item in points:
            if not isinstance(item, dict) or item.get("status") not in {"covered", "partial", "missing"}:
                issues.append("Invalid exam point entry")
                continue
            evidence = item.get("evidence")
            if item["status"] in {"covered", "partial"} and not isinstance(evidence, str):
                issues.append("Covered or partial exam points require evidence")
            if item["status"] == "missing" and evidence:
                issues.append("Missing exam points cannot contain evidence")

    # Sections (Markdown sections if present)
    sections = report.get("sections")
    if isinstance(sections, dict):
        for section in REQUIRED_REPORT_SECTIONS:
            if section not in sections:
                issues.append(f"Missing report section: {section}")
    elif isinstance(sections, list):
        found = set(sections)
        missing = set(REQUIRED_REPORT_SECTIONS) - found
        if missing:
            issues.append(f"Missing report sections: {missing}")

    valid = len(issues) == 0
    if not valid:
        recommendations.append("Ensure all required fields and sections are present")

    return {
        "valid": valid,
        "issues": issues,
        "recommendations": recommendations,
    }


def check_output(output: str) -> dict:
    """Legacy text-based output check (deprecated for new code).

    Prefer validate_report_structure() for new diagnosis flows.
    """
    issues = []
    recommendations = []

    content_dims = ["concept_accuracy", "structure_completeness",
                    "engineering_depth", "example_quality", "question_alignment"]
    content_found = []
    for dim in content_dims:
        if dim in output.lower():
            content_found.append(dim)
    if len(content_found) < 5:
        missing = set(content_dims) - set(content_found)
        issues.append(f"Missing content dimensions: {missing}")

    voice_dims = ["fluency", "filler_words", "redundancy",
                  "spoken_clarity", "answer_pacing"]
    voice_found = []
    for dim in voice_dims:
        if dim in output.lower():
            voice_found.append(dim)
    if len(voice_found) < 5:
        missing = set(voice_dims) - set(voice_found)
        issues.append(f"Missing voice dimensions: {missing}")

    if "面试题" not in output and "原始" not in output:
        issues.append("Missing original question")

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


def render_report_markdown(report: dict) -> str:
    """Render a structured report dict to Markdown."""
    lines = [
        "# 面试回答诊断报告",
        "",
        f"**总分：{report.get('overall_score', 0)}/10**",
        "",
        "## 原始面试题",
        report.get("question", ""),
        "",
        "## 用户回答摘要",
        str(report.get("answer", ""))[:300] + ("..." if len(str(report.get("answer", ""))) > 300 else ""),
        "",
        "## 参考答案对标",
    ]

    # Reference alignment (from sources/knowledge)
    ref_info = report.get("reference_alignment", "")
    if ref_info:
        lines.append(ref_info)
    else:
        lines.append("- 见下方知识来源")

    # Content scores
    lines.extend(["", "## 内容维度评分", "", "| 维度 | 评分 | 说明 |", "|------|------|------|"])
    cs = report.get("content_scores", {})
    dim_labels = {
        "concept_accuracy": "概念准确性",
        "structure_completeness": "结构完整性",
        "engineering_depth": "工程深度",
        "example_quality": "示例质量",
        "question_alignment": "问题契合度",
    }
    for dim_name, dim_data in cs.get("dimensions", {}).items():
        label = dim_labels.get(dim_name, dim_name)
        lines.append(f"| {label} ({dim_name}) | {dim_data.get('score', 0)}/10 | {dim_data.get('explanation', '')} |")
    lines.append(f"| **内容总分** | **{cs.get('total', 0)}/{cs.get('max_total', 50)}** | |")

    # Voice scores
    lines.extend(["", "## 语音维度评分", "", "| 维度 | 评分 | 说明 |", "|------|------|------|"])
    vs = report.get("voice_scores", {})
    voice_labels = {
        "fluency": "流畅度",
        "filler_words": "口头禅控制",
        "redundancy": "冗余度",
        "spoken_clarity": "口语清晰度",
        "answer_pacing": "回答节奏",
    }
    for dim_name, dim_data in vs.get("dimensions", {}).items():
        label = voice_labels.get(dim_name, dim_name)
        lines.append(f"| {label} ({dim_name}) | {dim_data.get('score', 0)}/10 | {dim_data.get('explanation', '')} |")
    lines.append(f"| **语音总分** | **{vs.get('total', 0)}/{vs.get('max_total', 50)}** | |")

    # Candidate/reference comparison is rendered independently from score tables.
    lines.extend(["", "## 用户已覆盖"])
    user_covered = report.get("user_covered", [])
    if user_covered:
        for item in user_covered[:5]:
            if isinstance(item, dict):
                lines.append(f"- **{item.get('point', '')}**：{item.get('explanation', '')}（回答证据：{item.get('evidence', '')}）")
            else:
                lines.append(f"- {item}")
    else:
        lines.append("- 未识别到明确的已覆盖要点")

    lines.extend(["", "## 用户缺失"])
    user_missing = report.get("user_missing", [])
    if user_missing:
        for item in user_missing[:5]:
            if isinstance(item, dict):
                evidence = f"（已提及：{item['evidence']}）" if item.get("evidence") else ""
                lines.append(f"- **{item.get('point', '')}**：{item.get('explanation', '')}{evidence}")
            else:
                lines.append(f"- {item}")
    else:
        lines.append("- 未识别到明显缺失要点")

    # Improvement
    lines.extend(["", "## 改进建议"])
    improvements = report.get("improvements", ["1. 针对薄弱维度进行专项练习", "2. 使用 STAR 原则组织回答结构"])
    lines.extend(improvements[:8])

    # Follow-ups
    followups = report.get("followups", [])
    lines.extend(["", "## 可能追问"])
    if followups:
        for i, fq in enumerate(followups[:5], 1):
            lines.append(f"{i}. {fq.get('question', '')}（{fq.get('why', '')}）")
    else:
        lines.append("- 暂无")

    sources = report.get("sources", [])
    lines.extend(["", "## 知识来源"])
    if sources:
        lines.extend(f"- {s}" for s in sources[:5])
    else:
        lines.append("- 未命中知识来源")

    return "\n".join(lines)


def extract_diagnosis_qa(text: str) -> dict:
    """Extract interview question and candidate answer from diagnosis text."""
    cleaned = (text or "").strip()
    question = ""
    answer = ""
    patterns = [
        r"面试题[：:]\s*(?P<question>.*?)(?:回答|我的回答|候选人回答)[：:]\s*(?P<answer>.*)$",
        r"问题[：:]\s*(?P<question>.*?)(?:回答|我的回答|候选人回答)[：:]\s*(?P<answer>.*)$",
        r"Question[：:]\s*(?P<question>.*?)(?:Answer|回答)[：:]\s*(?P<answer>.*)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE | re.DOTALL)
        if match:
            question = match.group("question").strip()
            answer = match.group("answer").strip()
            break
    has_question = bool(question) or any(token in cleaned for token in ["面试题", "问题", "question", "？", "?"])
    has_answer = bool(answer) or any(token in cleaned for token in ["回答", "answer", "候选"])
    return {
        "has_question": has_question and bool(question or cleaned),
        "has_answer": has_answer and bool(answer or cleaned),
        "question": question,
        "answer": answer,
    }
