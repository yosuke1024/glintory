import re
from pydantic import BaseModel, Field, ConfigDict, field_validator

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

    title: str
    summary: str
    problem_statement: str
    target_users: list[str]
    why_now: str
    evidence_synthesis: str
    build_direction: str
    risks: list[str]
    tags: list[str]

    @field_validator("title", "summary", "problem_statement", "why_now", "evidence_synthesis", "build_direction")
    @classmethod
    def check_strings(cls, v: str) -> str:
        return validate_string_safety(v)

    @field_validator("target_users", "risks", "tags")
    @classmethod
    def check_lists(cls, v: list[str]) -> list[str]:
        for item in v:
            validate_string_safety(item)
        return v


class EnglishBrief(BriefBase):
    title: str = Field(..., max_length=100)
    summary: str = Field(..., max_length=500)
    problem_statement: str = Field(..., max_length=500)
    why_now: str = Field(..., max_length=500)
    evidence_synthesis: str = Field(..., max_length=800)
    build_direction: str = Field(..., max_length=500)
    target_users: list[str] = Field(..., max_length=5)
    risks: list[str] = Field(..., max_length=5)
    tags: list[str] = Field(..., max_length=8)


class JapaneseBrief(BriefBase):
    title: str = Field(..., max_length=100)
    summary: str = Field(..., max_length=500)
    problem_statement: str = Field(..., max_length=500)
    why_now: str = Field(..., max_length=500)
    evidence_synthesis: str = Field(..., max_length=800)
    build_direction: str = Field(..., max_length=500)
    target_users: list[str] = Field(..., max_length=5)
    risks: list[str] = Field(..., max_length=5)
    tags: list[str] = Field(..., max_length=8)


class BilingualOpportunityBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    english: EnglishBrief
    japanese: JapaneseBrief
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
