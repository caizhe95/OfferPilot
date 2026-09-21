"""HTTP contracts and validation for durable Run creation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from offerpilot.core.errors import AppError


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["coach", "diagnosis", "audio_transcription", "report_export"]
    input: dict[str, Any]


def required_text(value: object, *, required_code: str, max_length: int, too_long_code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("Run input is required", code=required_code, status_code=422)
    if len(value) > max_length:
        raise AppError("Run input exceeds its maximum length", code=too_long_code, status_code=422)
    return value


def validate_run_input(run_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    if run_type == "coach":
        if set(raw) != {"message"}:
            raise AppError("Invalid coach input", code="invalid_run_input", status_code=422)
        return {
            "message": required_text(
                raw.get("message"),
                required_code="coach_message_required",
                max_length=12000,
                too_long_code="coach_message_too_long",
            )
        }
    if run_type == "diagnosis":
        allowed = {"question", "answer", "followup_id"}
        if not set(raw).issubset(allowed):
            raise AppError("Invalid diagnosis input", code="invalid_run_input", status_code=422)
        validated: dict[str, Any] = {
            "question": required_text(
                raw.get("question"),
                required_code="diagnosis_question_required",
                max_length=4000,
                too_long_code="diagnosis_question_too_long",
            ),
            "answer": required_text(
                raw.get("answer"),
                required_code="diagnosis_answer_required",
                max_length=12000,
                too_long_code="diagnosis_answer_too_long",
            ),
        }
        if "followup_id" in raw:
            followup_id = raw["followup_id"]
            if not isinstance(followup_id, str) or not followup_id.strip() or len(followup_id) > 64:
                raise AppError("Invalid follow-up", code="invalid_run_input", status_code=422)
            validated["followup_id"] = followup_id
        return validated
    key = "upload_id" if run_type == "audio_transcription" else "report_id"
    if set(raw) != {key}:
        raise AppError("Invalid run input", code="invalid_run_input", status_code=422)
    return {
        key: required_text(
            raw.get(key),
            required_code="invalid_run_input",
            max_length=64,
            too_long_code="invalid_run_input",
        )
    }
