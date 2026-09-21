"""Structured diagnosis output contracts and dimension names."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

EvidenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
PointExplanation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
DimensionExplanation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ImprovementText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]

class ExamPointAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point_id: str = Field(min_length=3, max_length=80)
    status: Literal["covered", "partial", "missing"]
    evidence: EvidenceText | None
    explanation: PointExplanation

class DimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int | float = Field(ge=1, le=10)
    explanation: DimensionExplanation
    @field_validator("score", mode="before")
    @classmethod
    def reject_boolean_score(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("score must be numeric")
        return value

class ContentDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept_accuracy: DimensionAssessment
    structure_completeness: DimensionAssessment
    engineering_depth: DimensionAssessment
    example_quality: DimensionAssessment
    question_alignment: DimensionAssessment

class VoiceDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fluency: DimensionAssessment
    filler_words: DimensionAssessment
    redundancy: DimensionAssessment
    spoken_clarity: DimensionAssessment
    answer_pacing: DimensionAssessment

class DiagnosisModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam_points: list[ExamPointAssessment] = Field(min_length=1, max_length=12)
    content_dimensions: ContentDimensions
    voice_dimensions: VoiceDimensions
    improvements: list[ImprovementText] = Field(max_length=3)

__all__ = [
    "ContentDimensions",
    "DiagnosisModelOutput",
    "DimensionAssessment",
    "ExamPointAssessment",
    "VoiceDimensions",
]
