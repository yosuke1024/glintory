import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

HTML_PATTERN = re.compile(r"<[^>]*>|&[#\w]+;")
URL_PATTERN = re.compile(r"https?://[^\s/$.?#].[^\s]*", re.IGNORECASE)
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_string_safety(v: str) -> str:
    if not v or not v.strip():
        raise ValueError("String cannot be empty or only whitespace")
    if HTML_PATTERN.search(v):
        raise ValueError("HTML tags or entities are not allowed")
    if URL_PATTERN.search(v):
        raise ValueError("URLs are not allowed")
    if CONTROL_CHAR_PATTERN.search(v):
        raise ValueError("Control characters are not allowed")
    return v


class BriefBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(..., max_length=150)
    summary: str = Field(..., max_length=500)
    target_user: str = Field(..., max_length=500)
    problem: str = Field(..., max_length=500)
    current_workaround: str = Field(..., max_length=500)
    existing_solution_gap: str = Field(..., max_length=500)
    mvp_direction: str = Field(..., max_length=500)
    why_selected: str = Field(..., max_length=500)
    risks: str = Field(..., max_length=500)

    @field_validator(
        "title",
        "summary",
        "target_user",
        "problem",
        "current_workaround",
        "existing_solution_gap",
        "mvp_direction",
        "why_selected",
        "risks",
    )
    @classmethod
    def check_strings(cls, v: str) -> str:
        return validate_string_safety(v)


class EnglishBrief(BriefBase):
    pass


class JapaneseBrief(BriefBase):
    pass


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    summary_en: str = Field(..., max_length=500)
    summary_ja: str = Field(..., max_length=500)

    @field_validator("id", "summary_en", "summary_ja")
    @classmethod
    def check_strings(cls, v: str) -> str:
        return validate_string_safety(v)


class BilingualOpportunityBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    english: EnglishBrief
    japanese: JapaneseBrief
    evidence_summaries: list[EvidenceSummary] = Field(..., min_length=1)
    evidence_refs: list[str] = Field(..., min_length=1)
    confidence: str

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: str) -> str:
        if v not in ("low", "medium", "high"):
            raise ValueError("confidence must be one of low, medium, or high")
        return v

    @field_validator("evidence_refs")
    @classmethod
    def check_evidence_refs(cls, v: list[str]) -> list[str]:
        for ref in v:
            validate_string_safety(ref)
        return v
