"""Rules, structural validation, and Markdown rendering for diagnosis reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from offerpilot.core.config import settings

_CONTENT = {"concept_accuracy", "structure_completeness", "engineering_depth", "example_quality", "question_alignment"}
_VOICE = {"fluency", "filler_words", "redundancy", "spoken_clarity", "answer_pacing"}


def rules_directory() -> Path:
    return settings.resolved_rules_dir


def load_diagnosis_rules() -> str:
    rules: list[str] = []
    for name in ("global.rules.md", "diagnosis.rules.md"):
        path = rules_directory() / name
        if path.is_file():
            rules.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(rules)


def build_report(question: str, answer: str, diagnosis: dict[str, Any], overall: float, knowledge: list[dict[str, Any]]) -> dict[str, Any]:
    points = diagnosis.get("exam_points", [])
    return {
        "question": question, "answer": answer, "overall_score": overall,
        "content_scores": diagnosis["content_scores"], "voice_scores": diagnosis["voice_scores"],
        "followups": diagnosis.get("followups", []),
        "sources": [item.get("source", item.get("title", "")) for item in knowledge],
        "exam_points": points,
        "user_covered": [item for item in points if item.get("status") == "covered"],
        "user_missing": [item for item in points if item.get("status") != "covered"],
        "improvements": diagnosis.get("improvements") or ["请补充具体工程场景和边界条件。"],
        "reference_alignment": "\n".join(f"- {item.get('title', '')}" for item in knowledge[:5]) or "- 未命中参考资料",
    }


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    required = {"question", "answer", "overall_score", "content_scores", "voice_scores", "followups", "sources", "user_covered", "user_missing", "exam_points"}
    if not isinstance(report, dict):
        return {"valid": False, "issues": ["Report is not a structured object"]}
    issues.extend(f"Missing required field: {name}" for name in required if report.get(name) is None)
    if not isinstance(report.get("overall_score"), (int, float)):
        issues.append("overall_score must be a number")
    _validate_dimensions(report.get("content_scores"), _CONTENT, "content_scores", issues)
    _validate_dimensions(report.get("voice_scores"), _VOICE, "voice_scores", issues)
    if not isinstance(report.get("followups"), list):
        issues.append("followups must be a list")
    if not isinstance(report.get("sources"), list):
        issues.append("sources must be a list")
    points = report.get("exam_points")
    if not isinstance(points, list):
        issues.append("exam_points must be a list")
    else:
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("point_id"), str) or point.get("status") not in {"covered", "partial", "missing"}:
                issues.append("Invalid exam point entry")
            elif point["status"] in {"covered", "partial"} and not isinstance(point.get("evidence"), str):
                issues.append("Covered or partial exam points require evidence")
            elif point["status"] == "missing" and point.get("evidence") is not None:
                issues.append("Missing exam points cannot contain evidence")
    return {"valid": not issues, "issues": issues}


def _validate_dimensions(scores: object, expected: set[str], field: str, issues: list[str]) -> None:
    dimensions = scores.get("dimensions") if isinstance(scores, dict) else None
    if not isinstance(dimensions, dict):
        issues.append(f"{field}.dimensions is missing or not a dict")
        return
    missing = expected - set(dimensions)
    if missing:
        issues.append(f"Missing {field} dimensions: {sorted(missing)}")
    for value in dimensions.values():
        if not isinstance(value, dict) or not isinstance(value.get("score"), (int, float)) or not isinstance(value.get("explanation"), str):
            issues.append(f"Invalid {field} dimension entry")


def render_report_markdown(report: dict[str, Any]) -> str:
    lines = ["# 面试回答诊断报告", "", f"**总分：{report.get('overall_score', 0)}/10**", "", "## 原始面试题", str(report.get("question", ""))[:600], "", "## 用户回答摘要", str(report.get("answer", ""))[:300], "", "## 参考答案对标", str(report.get("reference_alignment", "- 未命中参考资料"))]
    for section, scores in (("内容维度评分", report.get("content_scores", {})), ("语音维度评分", report.get("voice_scores", {}))):
        lines.extend(["", f"## {section}", "", "| 维度 | 评分 | 说明 |", "|------|------|------|"])
        for name, value in scores.get("dimensions", {}).items():
            lines.append(f"| {name} | {value.get('score', 0)}/10 | {str(value.get('explanation', ''))[:120]} |")
    for title, points in (("用户已覆盖", report.get("user_covered", [])), ("用户缺失", report.get("user_missing", []))):
        lines.extend(["", f"## {title}"])
        lines.extend(f"- **{str(item.get('point', ''))[:120]}**：{str(item.get('explanation', ''))[:120]}" for item in points[:3] if isinstance(item, dict))
        if not points: lines.append("- 暂无")
    lines.extend(["", "## 改进建议", *[f"- {str(item)[:220]}" for item in report.get("improvements", [])[:3]], "", "## 可能追问"])
    lines.extend(f"- {str(item.get('question', ''))[:180]}" for item in report.get("followups", [])[:5] if isinstance(item, dict))
    lines.extend(["", "## 知识来源", *[f"- {str(source)[:180]}" for source in report.get("sources", [])[:5]]])
    return "\n".join(lines)[:5000]


def render_and_validate(report: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    result = validate_report(report)
    return render_report_markdown(report), result
