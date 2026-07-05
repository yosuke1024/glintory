import asyncio
import os
import sys

from glintory.collectors.base import CollectionContext
from glintory.collectors.github import GitHubCollector
from glintory.config import settings
from glintory.infrastructure.http import HttpxHttpClient


async def main():
    print(
        "WARNING: This script will initiate real network communication to the GitHub API."
    )
    print("Proceeding to fetch public data...")
    print("-" * 50)

    # Allow token from env
    token = os.environ.get("GLINTORY_GITHUB_TOKEN") or settings.github_token
    if token:
        print("Using GitHub Token: [MASKED]")
        settings.github_token = token
    else:
        print("Using GitHub API without Token (subject to lower rate limits).")

    # Create http client & context
    http_client = HttpxHttpClient()
    context = CollectionContext(
        source_id="smoke",
        source_name="smoke-github",
        source_type="github",
        source_config={
            "repository_queries": [{"query": "topic:self-hosted", "max_items": 2}],
            "issue_queries": [{"query": '"too expensive"', "max_items": 1}],
            "per_page": 5,
        },
        max_items=3,
        http=http_client,
    )

    collector = GitHubCollector(settings)
    try:
        result = await collector.collect(context)
    except Exception as e:
        print(f"Smoke test failed: {e}", file=sys.stderr)
        return

    print(f"Fetched {len(result.items)} items successfully.")
    for idx, item in enumerate(result.items, 1):
        print(f"Item {idx}:")
        print(f"  Type: {item.item_type}")
        print(f"  Title: {item.title}")
        print(f"  URL: {item.url}")

    if result.warnings:
        print(f"Warnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"  - [{w.code}] {w.message}")

    if result.errors:
        print(f"Errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - [{err.code}] {err.message} (retryable={err.retryable})")

    # Rate limit metadata
    rate = result.metadata.get("github_rate_limit")
    if rate:
        print(
            f"Rate Limit: {rate.get('remaining')}/{rate.get('limit')} (reset: {rate.get('reset_at')})"
        )


if __name__ == "__main__":
    asyncio.run(main())
