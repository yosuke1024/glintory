import asyncio

from glintory.collectors.base import (
    CollectionContext,
    CollectionError,
    CollectionResult,
    CollectionWarning,
    Collector,
    RawItem,
)


class BaseFakeCollector(Collector):
    def __init__(self, source_type: str = "fake") -> None:
        self.source_type = source_type
        self.call_count = 0
        self.last_context: CollectionContext | None = None

    def _record_call(self, context: CollectionContext) -> None:
        self.call_count += 1
        self.last_context = context


class SuccessfulFakeCollector(BaseFakeCollector):
    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        return CollectionResult(
            items=[
                RawItem(
                    external_id="1",
                    url="http://example.com/1",
                    title="Item 1",
                    excerpt="Excerpt 1",
                ),
                RawItem(
                    external_id="2",
                    url="http://example.com/2",
                    title="Item 2",
                    excerpt="Excerpt 2",
                ),
            ],
            warnings=(),
            errors=(),
        )


class EmptySuccessfulFakeCollector(BaseFakeCollector):
    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        return CollectionResult(
            items=(),
            warnings=(),
            errors=(),
        )


class WarningFakeCollector(BaseFakeCollector):
    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        return CollectionResult(
            items=[
                RawItem(
                    external_id="1",
                    url="http://example.com/1",
                    title="Item 1",
                )
            ],
            warnings=[
                CollectionWarning(
                    code="WARN_001",
                    message="Something minor occurred",
                    item_external_id="1",
                )
            ],
            errors=(),
        )


class PartialFakeCollector(BaseFakeCollector):
    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        return CollectionResult(
            items=[
                RawItem(
                    external_id="1",
                    url="http://example.com/1",
                    title="Item 1",
                )
            ],
            warnings=(),
            errors=[
                CollectionError(
                    code="ERR_001",
                    message="Failed to fetch item 2",
                    retryable=True,
                    item_external_id="2",
                )
            ],
        )


class FailedResultFakeCollector(BaseFakeCollector):
    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        return CollectionResult(
            items=(),
            warnings=(),
            errors=[
                CollectionError(
                    code="ERR_FATAL",
                    message="Failed to connect to source",
                    retryable=False,
                )
            ],
        )


class ExceptionFakeCollector(BaseFakeCollector):
    def __init__(
        self, exception: Exception | None = None, source_type: str = "fake"
    ) -> None:
        super().__init__(source_type)
        self.exception = exception or RuntimeError("Collector fatal exception")

    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        raise self.exception


class CancelledFakeCollector(BaseFakeCollector):
    async def collect(self, context: CollectionContext) -> CollectionResult:
        self._record_call(context)
        raise asyncio.CancelledError()
