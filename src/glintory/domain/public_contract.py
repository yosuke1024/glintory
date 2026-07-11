from datetime import datetime

from pydantic import BaseModel, Field


class PublicEvidenceV1(BaseModel):
    signal_id: str
    role: str
    source_type: str
    source_name: str
    title: str
    url: str
    published_at: datetime
    relevance_score: float
    summary_ja: str | None = None
    summary_en: str | None = None
    excerpt: str | None = None


class PublicOpportunityLocalizationItemV1(BaseModel):
    status: str
    title: str
    summary: str


class PublicOpportunityLocalizationListV1(BaseModel):
    ja: PublicOpportunityLocalizationItemV1
    en: PublicOpportunityLocalizationItemV1


class PublicOpportunityLocalizationDetailItemV1(BaseModel):
    status: str
    title: str
    summary: str
    target_user: str | None = None
    problem: str | None = None
    current_workaround: str | None = None
    existing_solution_gap: str | None = None
    mvp_direction: str | None = None
    why_selected: str | None = None
    risks: str | None = None


class PublicOpportunityLocalizationDetailV1(BaseModel):
    ja: PublicOpportunityLocalizationDetailItemV1
    en: PublicOpportunityLocalizationDetailItemV1


class PublicOpportunityScoreListV1(BaseModel):
    total: int
    confidence: str
    version: str


class PublicOpportunityEvidenceMetricsV1(BaseModel):
    total: int
    independent: int
    demand: int
    source_types: int
    source_domains: int


class JuryPressReadinessV1(BaseModel):
    ready: bool
    reasons: list[str] = Field(default_factory=list)


class PublicOpportunitySummaryV1(BaseModel):
    public_id: str
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


class PublicOpportunityListV1(BaseModel):
    schema_version: str = "1.0.0"
    generated_at: datetime
    count: int
    items: list[PublicOpportunitySummaryV1]


class ScoreComponentV1(BaseModel):
    name: str
    score: int
    maximum: int
    explanation: str


class PublicOpportunityScoreDetailV1(BaseModel):
    total: int
    evidence: int
    feasibility: int
    penalty: int
    confidence: str
    version: str
    components: list[ScoreComponentV1] = Field(default_factory=list)


class PublicOpportunityGateV1(BaseModel):
    version: str
    status: str
    reason: str


class PublicOpportunityDetailV1(BaseModel):
    schema_version: str = "1.0.0"
    public_id: str
    revision: int
    content_hash: str
    first_published_at: datetime | None = None
    last_published_at: datetime | None = None
    localization: PublicOpportunityLocalizationDetailV1
    score: PublicOpportunityScoreDetailV1
    gate: PublicOpportunityGateV1
    evidence: list[PublicEvidenceV1]
    jurypress: JuryPressReadinessV1


class JuryPressFeedItemV1(BaseModel):
    public_id: str
    revision: int
    content_hash: str
    score: int
    confidence: str
    title_ja: str
    title_en: str
    detail_url: str


class JuryPressFeedV1(BaseModel):
    schema_version: str = "1.0.0"
    generated_at: datetime
    content_hash: str
    count: int
    items: list[JuryPressFeedItemV1]


class PublicManifestCountsV1(BaseModel):
    published_opportunities: int
    jurypress_ready: int


class PublicManifestEndpointsV1(BaseModel):
    opportunities: str
    jurypress: str


class PublicManifestV1(BaseModel):
    contract: str = "glintory-public-data"
    schema_version: str = "1.0.0"
    generated_at: datetime
    dataset_revision: str
    source_commit: str
    content_hash: str
    counts: PublicManifestCountsV1
    endpoints: PublicManifestEndpointsV1
