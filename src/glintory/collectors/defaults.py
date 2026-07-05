from glintory.collectors.github import GitHubCollector
from glintory.collectors.hackernews import HackerNewsCollector
from glintory.collectors.registry import CollectorRegistry
from glintory.collectors.rss import RSSCollector
from glintory.config import Settings


def build_default_collector_registry(
    settings: Settings,
) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(GitHubCollector(settings))
    registry.register(HackerNewsCollector(settings))
    registry.register(RSSCollector(settings))
    return registry
