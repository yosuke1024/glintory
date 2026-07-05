from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RawItem:
    external_id: str | None
    url: str
    title: str
    excerpt: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    item_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionWarning:
    code: str
    message: str
    item_external_id: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionError:
    code: str
    message: str
    retryable: bool
    item_external_id: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionResult:
    items: Sequence[RawItem]
    warnings: Sequence[CollectionWarning] = ()
    errors: Sequence[CollectionError] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


# HTTP Client Protocol definitions to avoid circular dependencies
class HttpTextResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str
    text: str


class HttpJsonResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str

    def json(self) -> Any: ...


class HttpClientProtocol(Protocol):
    async def get_text(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpTextResponse: ...

    async def get_json(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpJsonResponse: ...


@dataclass(frozen=True, slots=True)
class CollectionContext:
    source_id: str
    source_name: str
    source_type: str
    source_config: Mapping[str, Any]
    max_items: int
    http: HttpClientProtocol


class Collector(Protocol):
    source_type: str

    async def collect(
        self,
        context: CollectionContext,
    ) -> CollectionResult: ...
