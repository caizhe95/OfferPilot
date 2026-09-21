"""Small deterministic asynchronous regression evaluation suite."""

from __future__ import annotations

from typing import Any

from offerpilot.diagnosis.reporting import validate_report
from offerpilot.diagnosis.scoring import diagnose_interview, select_scorable_points
from offerpilot.knowledge.retrieval import search_knowledge

EVAL_CASES: list[dict[str, Any]] = [
    {"name": "diagnosis_structure", "kind": "diagnosis", "question": "什么是 ReAct 模式？", "answer": "ReAct 将推理、工具行动和观察循环结合，并设置超时、重试和日志边界。", "expect": {"valid_dimensions": True, "grounded_evidence": True}},
    {"name": "retrieval_contract", "kind": "retrieval", "question": "Tool Calling 机制如何设计？", "expect": {"has_results": True}},
    {"name": "harness_output_contract", "kind": "harness", "expect": {"rejects_invalid_report": True}},
]


async def run_eval(name: str) -> dict[str, Any]:
    case = next((item for item in EVAL_CASES if item["name"] == name), None)
    if case is None:
        return {"eval_name": name, "passed": False, "error": "eval_case_not_found"}
    if case["kind"] == "retrieval":
        return await _run_retrieval(case)
    if case["kind"] == "harness":
        return _run_harness(case)
    return await _run_diagnosis(case)


async def _run_diagnosis(case: dict[str, Any]) -> dict[str, Any]:
    knowledge = await search_knowledge(question=case["question"], limit=3)
    scorable_points = select_scorable_points(knowledge)
    if not scorable_points:
        return {"eval_name": case["name"], "passed": False, "error": "no_reference_exam_points"}
    diagnosis = await diagnose_interview(question=case["question"], answer=case["answer"], scorable_points=scorable_points, context_instruction="只验证正式诊断结构与证据规则。", timeout=10)
    content, voice = diagnosis["content_scores"]["dimensions"], diagnosis["voice_scores"]["dimensions"]
    grounded = all(point["status"] == "missing" or str(point.get("evidence") or "") in case["answer"] for point in diagnosis["exam_points"])
    passed = len(content) == 5 and len(voice) == 5 and grounded
    return {"eval_name": case["name"], "passed": passed, "question": case["question"], "details": {"content_dimensions": sorted(content), "voice_dimensions": sorted(voice), "grounded_evidence": grounded}, "expectations": case["expect"], "reasons": ["diagnosis_contract_valid" if passed else "diagnosis_contract_invalid"]}


async def _run_retrieval(case: dict[str, Any]) -> dict[str, Any]:
    results = await search_knowledge(question=case["question"], limit=5)
    passed = bool(results)
    return {"eval_name": case["name"], "passed": passed, "question": case["question"], "details": {"result_count": len(results), "sources": [item.get("source", "") for item in results]}, "expectations": case["expect"], "reasons": ["retrieval_available" if passed else "retrieval_empty"]}


def _run_harness(case: dict[str, Any]) -> dict[str, Any]:
    result = validate_report({"question": "incomplete"})
    passed = result["valid"] is False
    return {"eval_name": case["name"], "passed": passed, "details": {"issues": result.get("issues", [])}, "expectations": case["expect"], "reasons": ["invalid_report_rejected" if passed else "invalid_report_accepted"]}


async def run_all_evals() -> dict[str, Any]:
    results = [await run_eval(case["name"]) for case in EVAL_CASES]
    passed = sum(1 for item in results if item["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "pass_rate": passed / len(results) if results else 0, "results": results}
