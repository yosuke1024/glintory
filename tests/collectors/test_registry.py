from collections.abc import Mapping
from typing import Any

import pytest

from glintory.collectors.base import CollectionContext, CollectionResult
from glintory.collectors.defaults import build_default_collector_registry
from glintory.collectors.github import GitHubCollector
from glintory.collectors.registry import (
    CollectorAlreadyRegisteredError,
    CollectorNotFoundError,
    CollectorRegistry,
)
from glintory.config import settings


class DummyCollector:
    def __init__(self, source_type: str):
        self.source_type = source_type

    def validate_config(
        self,
        config: Mapping[str, object],
    ) -> Mapping[str, object]:
        return config

    def get_config_summary(
        self,
        config: Mapping[str, Any],
    ) -> str:
        _ = config
        return "Dummy config summary"

    async def collect(self, context: CollectionContext) -> CollectionResult:
        raise NotImplementedError()


def test_registry_register_and_get():
    registry = CollectorRegistry()
    collector = DummyCollector("test-type")

    registry.register(collector)
    assert registry.get("test-type") is collector


def test_registry_duplicate_registration_fails():
    registry = CollectorRegistry()
    collector1 = DummyCollector("test-type")
    collector2 = DummyCollector("test-type")

    registry.register(collector1)
    with pytest.raises(CollectorAlreadyRegisteredError):
        registry.register(collector2)


def test_registry_get_not_found_fails():
    registry = CollectorRegistry()
    with pytest.raises(CollectorNotFoundError):
        registry.get("unknown-type")


def test_registry_instances_are_isolated():
    registry1 = CollectorRegistry()
    registry2 = CollectorRegistry()
    collector = DummyCollector("test-type")

    registry1.register(collector)
    assert registry1.get("test-type") is collector

    with pytest.raises(CollectorNotFoundError):
        registry2.get("test-type")


def test_build_default_collector_registry():
    registry = build_default_collector_registry(settings)
    assert isinstance(registry, CollectorRegistry)

    collector = registry.get("github")
    assert isinstance(collector, GitHubCollector)
    assert collector.settings is settings
