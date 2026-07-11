from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from glintory.domain.enums import SignalRole, SignalType


@dataclass(frozen=True, slots=True)
class NormalizedSignal:
    source_id: str
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
    signal_role: SignalRole
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    metrics: Mapping[str, int | float | str | bool | None]
    raw_metadata: Mapping[str, object]
    content_hash: str
    freshness_score: float
    source_quality_score: float


@dataclass(frozen=True, slots=True)
class SignalNormalizationWarning:
    code: str
    message: str
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalNormalizationError:
    code: str
    message: str
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalNormalizationResult:
    signals: Sequence[NormalizedSignal]
    warnings: Sequence[SignalNormalizationWarning] = ()
    errors: Sequence[SignalNormalizationError] = ()


@dataclass(frozen=True, slots=True)
class SignalPersistenceWarning:
    code: str
    message: str
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalPersistenceError:
    code: str
    message: str
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalPersistenceResult:
    inserted_count: int
    updated_count: int
    duplicate_count: int
    signal_ids: Sequence[str]
    warnings: Sequence[SignalPersistenceWarning] = ()
    errors: Sequence[SignalPersistenceError] = ()


class SignalIdentityCollisionError(ValueError):
    pass
