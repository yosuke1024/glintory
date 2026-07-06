from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from glintory.domain.enums import SignalType


@dataclass(frozen=True, slots=True)
class SignalSearchFilters:
    query: str | None = None
    source_id: str | None = None
    signal_type: SignalType | None = None
    published_from: date | None = None
    published_to: date | None = None
    page: int = 1
    per_page: int = 25


@dataclass(frozen=True, slots=True)
class SignalSearchItem:
    id: str
    title: str
    excerpt: str
    author: str | None
    canonical_url: str
    source_id: str
    source_name: str
    source_type: str
    signal_type: SignalType
    published_at: datetime | None
    collected_at: datetime
    freshness_score: float
    rank: float | None


@dataclass(frozen=True, slots=True)
class SignalSearchPage:
    items: Sequence[SignalSearchItem]
    total_count: int
    page: int
    per_page: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class SignalDetail:
    id: str
    source_id: str
    source_name: str
    source_type: str
    collection_run_id: str | None
    external_id: str | None
    canonical_url: str
    title: str
    excerpt: str
    author: str | None
    published_at: datetime | None
    collected_at: datetime
    language: str | None
    signal_type: SignalType
    categories: Sequence[str]
    tags: Sequence[str]
    metrics: Mapping[str, object]
    raw_metadata: Mapping[str, object]
    content_hash: str
    freshness_score: float
    source_quality_score: float
    created_at: datetime
    updated_at: datetime
