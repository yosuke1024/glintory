from glintory.collectors.base import Collector


class CollectorRegistryError(Exception):
    """Base exception for CollectorRegistry errors."""

    pass


class CollectorAlreadyRegisteredError(CollectorRegistryError):
    """Raised when a collector is already registered for a source type."""

    pass


class CollectorNotFoundError(CollectorRegistryError):
    """Raised when a collector is not found for a source type."""

    pass


class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: dict[str, Collector] = {}

    def register(self, collector: Collector) -> None:
        source_type = collector.source_type
        if source_type in self._collectors:
            raise CollectorAlreadyRegisteredError(
                f"Collector for source type '{source_type}' is already registered."
            )
        self._collectors[source_type] = collector

    def get(self, source_type: str) -> Collector:
        if source_type not in self._collectors:
            raise CollectorNotFoundError(
                f"No collector registered for source type '{source_type}'."
            )
        return self._collectors[source_type]
