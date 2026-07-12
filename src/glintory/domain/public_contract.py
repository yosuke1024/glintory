from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BasePublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


SignalRolePublicV1 = Literal["demand", "supply", "context", "unknown"]


class PublicEvidenceV1(BasePublicModel):
    signal_id: str
    role: SignalRolePublicV1
    source_type: str
    source_name: str
    title: str
    url: str
    published_at: datetime
    relevance_score: float
    summary_ja: str | None = None
    summary_en: str | None = None
    excerpt: str | None = None


class PublicOpportunityLocalizationItemV1(BasePublicModel):
    status: Literal["pending", "completed", "failed"]
    title: str | None = None
    summary: str | None = None


class PublicOpportunityLocalizationListV1(BasePublicModel):
    ja: PublicOpportunityLocalizationItemV1
    en: PublicOpportunityLocalizationItemV1


class PublicOpportunityLocalizationDetailItemV1(BasePublicModel):
    status: Literal["pending", "completed", "failed"]
    title: str | None = None
    summary: str | None = None
    target_user: str | None = None
    problem: str | None = None
    current_workaround: str | None = None
    existing_solution_gap: str | None = None
    mvp_direction: str | None = None
    why_selected: str | None = None
    risks: str | None = None


class PublicOpportunityLocalizationDetailV1(BasePublicModel):
    ja: PublicOpportunityLocalizationDetailItemV1
    en: PublicOpportunityLocalizationDetailItemV1


class PublicOpportunityScoreListV1(BasePublicModel):
    total: int
    confidence: Literal["low", "medium", "high"]
    version: Literal["v2"] = "v2"


class PublicOpportunityEvidenceMetricsV1(BasePublicModel):
    total: int
    independent: int
    demand: int
    source_types: int
    source_domains: int


JuryPressReasonCode = Literal[
    "INVALID_SCORING_VERSION",
    "GATE_REJECTED",
    "STATUS_EXCLUDED",
    "LOW_CONFIDENCE",
    "SCORE_BELOW_THRESHOLD",
    "INSUFFICIENT_INDEPENDENT_EVIDENCE",
    "INSUFFICIENT_DEMAND_EVIDENCE",
    "ENRICHMENT_MISSING",
    "ENRICHMENT_STALE",
    "JAPANESE_LOCALIZATION_MISSING",
    "ENGLISH_LOCALIZATION_MISSING",
    "EVIDENCE_SUMMARY_MISSING",
]


class JuryPressReadinessV1(BasePublicModel):
    ready: bool
    reasons: list[JuryPressReasonCode] = Field(default_factory=list)


class PublicOpportunitySummaryV1(BasePublicModel):
    public_id: str = Field(pattern=r"^opp_[0-9a-f]{32}$")
    public_lifecycle: Literal["active", "merged", "retired"] = "active"
    revision: int
    content_hash: str
    first_published_at: datetime | None = None
    last_published_at: datetime | None = None
    localization: PublicOpportunityLocalizationListV1
    score: PublicOpportunityScoreListV1
    evidence: PublicOpportunityEvidenceMetricsV1
    jurypress: JuryPressReadinessV1
    detail_url: str
    html_url: str


class PublicOpportunityListV1(BasePublicModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    generated_at: datetime
    count: int
    items: list[PublicOpportunitySummaryV1]


class ScoreComponentV1(BasePublicModel):
    name: str
    score: int
    maximum: int
    explanation: str


class PublicOpportunityScoreDetailV1(BasePublicModel):
    total: int
    evidence: int
    feasibility: int
    penalty: int
    confidence: Literal["low", "medium", "high"]
    version: Literal["v2"] = "v2"
    components: list[ScoreComponentV1] = Field(default_factory=list)
    independent_evidence_count: int = 0
    demand_evidence_count: int = 0


class PublicOpportunityGateV1(BasePublicModel):
    version: Literal["v2"] = "v2"
    status: Literal["passed", "rejected", "failed"]
    reason: str


class PublicOpportunityDetailV1(BasePublicModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    public_id: str = Field(pattern=r"^opp_[0-9a-f]{32}$")
    public_lifecycle: Literal["active", "merged", "retired"] = "active"
    revision: int
    content_hash: str
    first_published_at: datetime | None = None
    last_published_at: datetime | None = None
    localization: PublicOpportunityLocalizationDetailV1 | None = None
    score: PublicOpportunityScoreDetailV1 | None = None
    gate: PublicOpportunityGateV1 | None = None
    evidence: list[PublicEvidenceV1] | None = None
    jurypress: JuryPressReadinessV1 | None = None

    # Readiness validation attributes (SSOT for independent verification)
    enrichment_status: Literal["pending", "completed", "failed"] | None = None
    translation_status: Literal["pending", "completed", "failed"] | None = None

    # Tombstone/Redirection metadata
    retired_at: datetime | None = None
    retired_reason: str | None = None
    canonical_public_id: str | None = None
    canonical_detail_url: str | None = None


class JuryPressFeedItemV1(BasePublicModel):
    public_id: str = Field(pattern=r"^opp_[0-9a-f]{32}$")
    revision: int
    content_hash: str
    score: int
    confidence: Literal["low", "medium", "high"]
    title_ja: str
    title_en: str
    detail_url: str


class JuryPressFeedV1(BasePublicModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    generated_at: datetime
    content_hash: str
    count: int
    items: list[JuryPressFeedItemV1]


class PublicManifestCountsV1(BasePublicModel):
    published_opportunities: int
    jurypress_ready: int


class PublicManifestEndpointsV1(BasePublicModel):
    opportunities: str
    jurypress: str


class PublicManifestV1(BasePublicModel):
    contract: Literal["glintory-public-data"] = "glintory-public-data"
    schema_version: Literal["1.0.0"] = "1.0.0"
    generated_at: datetime
    dataset_revision: str
    source_commit: str
    content_hash: str
    counts: PublicManifestCountsV1
    endpoints: PublicManifestEndpointsV1


def to_public_completion_status(
    internal_status: str | None,
) -> Literal["pending", "completed", "failed"]:
    if internal_status in ("succeeded", "completed"):
        return "completed"
    if internal_status == "failed":
        return "failed"
    return "pending"
