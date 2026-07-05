from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from glintory.collectors.base import CollectionContext
from glintory.collectors.github import (
    GitHubAuthenticationError,
    GitHubCollector,
    GitHubIssueQueryConfig,
    GitHubRateLimitError,
    GitHubRepositoryQueryConfig,
    GitHubResponseError,
    GitHubSourceConfig,
)
from glintory.config import settings
from glintory.infrastructure.http import HttpxHttpClient


def test_github_settings():
    assert hasattr(settings, "github_token")
    assert hasattr(settings, "github_api_url")
    assert hasattr(settings, "github_api_version")
    assert hasattr(settings, "github_excerpt_max_chars")

    assert settings.github_api_url == "https://api.github.com"
    assert settings.github_api_version == "2026-03-10"
    assert settings.github_excerpt_max_chars == 2000


def test_github_source_config_valid():
    config = GitHubSourceConfig(
        repository_queries=[
            GitHubRepositoryQueryConfig(
                query="topic:self-hosted", sort="stars", max_items=10
            )
        ],
        issue_queries=[
            GitHubIssueQueryConfig(query="too expensive", sort="created", max_items=15)
        ],
        per_page=50,
        max_pages_per_query=2,
    )
    assert len(config.repository_queries) == 1
    assert config.repository_queries[0].query == "topic:self-hosted"
    assert config.repository_queries[0].sort == "stars"
    assert config.repository_queries[0].max_items == 10

    assert len(config.issue_queries) == 1
    assert config.issue_queries[0].query == "too expensive"
    assert config.issue_queries[0].sort == "created"
    assert config.issue_queries[0].max_items == 15
    assert config.per_page == 50
    assert config.max_pages_per_query == 2


def test_github_source_config_invalid_query():
    with pytest.raises(ValidationError):
        GitHubSourceConfig(repository_queries=[{"query": "   "}])  # type: ignore

    with pytest.raises(ValidationError):
        GitHubSourceConfig(issue_queries=[{"query": ""}])  # type: ignore

    with pytest.raises(ValidationError):
        GitHubSourceConfig(repository_queries=[], issue_queries=[])


def test_github_source_config_invalid_ranges():
    with pytest.raises(ValidationError):
        GitHubSourceConfig(repository_queries=[{"query": "test", "max_items": 0}])  # type: ignore
    with pytest.raises(ValidationError):
        GitHubSourceConfig(repository_queries=[{"query": "test", "max_items": 101}])  # type: ignore

    with pytest.raises(ValidationError):
        GitHubSourceConfig(
            repository_queries=cast(Any, [{"query": "test"}]), per_page=0
        )
    with pytest.raises(ValidationError):
        GitHubSourceConfig(
            repository_queries=cast(Any, [{"query": "test"}]), per_page=101
        )

    with pytest.raises(ValidationError):
        GitHubSourceConfig(
            repository_queries=cast(Any, [{"query": "test"}]), max_pages_per_query=0
        )
    with pytest.raises(ValidationError):
        GitHubSourceConfig(
            repository_queries=cast(Any, [{"query": "test"}]), max_pages_per_query=11
        )


def test_github_source_config_forbids_unknown():
    with pytest.raises(ValidationError):
        GitHubSourceConfig(
            repository_queries=[GitHubRepositoryQueryConfig(query="test")],
            unknown_key="value",  # type: ignore
        )


def test_github_query_normalization_repository():
    config_include_all = GitHubSourceConfig(
        repository_queries=[GitHubRepositoryQueryConfig(query="topic:self-hosted")],
        include_forks=True,
        include_archived=True,
    )
    assert (
        GitHubCollector.normalize_repository_query(
            "topic:self-hosted", config_include_all
        )
        == "topic:self-hosted"
    )

    config_exclude_all = GitHubSourceConfig(
        repository_queries=[GitHubRepositoryQueryConfig(query="topic:self-hosted")],
        include_forks=False,
        include_archived=False,
    )
    assert (
        GitHubCollector.normalize_repository_query(
            "topic:self-hosted", config_exclude_all
        )
        == "topic:self-hosted fork:false archived:false"
    )

    assert (
        GitHubCollector.normalize_repository_query(
            "topic:self-hosted fork:true", config_exclude_all
        )
        == "topic:self-hosted fork:true archived:false"
    )
    assert (
        GitHubCollector.normalize_repository_query(
            "topic:self-hosted archived:true", config_exclude_all
        )
        == "topic:self-hosted archived:true fork:false"
    )
    assert (
        GitHubCollector.normalize_repository_query(
            "topic:self-hosted fork:only archived:only", config_exclude_all
        )
        == "topic:self-hosted fork:only archived:only"
    )


def test_github_query_normalization_issue():
    assert (
        GitHubCollector.normalize_issue_query("too expensive")
        == "too expensive is:issue"
    )
    assert (
        GitHubCollector.normalize_issue_query("too expensive is:issue")
        == "too expensive is:issue"
    )
    assert (
        GitHubCollector.normalize_issue_query("is:issue too expensive")
        == "is:issue too expensive"
    )


def test_github_query_reject_pr():
    with pytest.raises(ValidationError):
        GitHubSourceConfig(issue_queries=[{"query": "too expensive is:pr"}])  # type: ignore

    with pytest.raises(ValidationError):
        GitHubSourceConfig(issue_queries=[{"query": "is:pr too expensive"}])  # type: ignore


@pytest.mark.asyncio
async def test_github_headers_and_auth():
    request_headers = None

    def handler(request: httpx.Request):
        nonlocal request_headers
        request_headers = request.headers
        return httpx.Response(
            200, json={"total_count": 0, "incomplete_results": False, "items": []}
        )

    settings.github_token = "fake-token"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"repository_queries": [{"query": "test"}]},
        max_items=100,
        http=http_client,
    )

    collector = GitHubCollector(settings)
    await collector.collect(context)

    assert request_headers is not None
    assert request_headers.get("Accept") == "application/vnd.github+json"
    assert request_headers.get("X-GitHub-Api-Version") == "2026-03-10"
    assert request_headers.get("Authorization") == "Bearer fake-token"

    settings.github_token = None
    request_headers = None
    await collector.collect(context)
    assert request_headers is not None
    assert "Authorization" not in request_headers


@pytest.mark.asyncio
async def test_github_repository_mapping():
    repo_item = {
        "id": 12345,
        "node_id": "node_123",
        "name": "glintory",
        "full_name": "owner/glintory",
        "html_url": "https://github.com/owner/glintory",
        "description": "  A test repository description with\nnewlines.  ",
        "owner": {"login": "owner"},
        "created_at": "2026-07-06T00:00:00Z",
        "updated_at": "2026-07-06T01:00:00Z",
        "pushed_at": "2026-07-06T02:00:00Z",
        "stargazers_count": 100,
        "watchers_count": 100,
        "forks_count": 5,
        "open_issues_count": 2,
        "language": "Python",
        "topics": ["python", "ai"],
        "license": {"spdx_id": "MIT"},
        "archived": False,
        "fork": False,
        "homepage": "https://example.com",
        "default_branch": "main",
        "visibility": "public",
        "score": 1.0,
    }

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"total_count": 1, "incomplete_results": False, "items": [repo_item]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"repository_queries": [{"query": "test"}]},
        max_items=100,
        http=http_client,
    )

    collector = GitHubCollector(settings)
    result = await collector.collect(context)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.external_id == "github:repository:12345"
    assert item.url == "https://github.com/owner/glintory"
    assert item.title == "owner/glintory"
    assert item.excerpt == "A test repository description with newlines."
    assert item.author == "owner"
    assert item.published_at == datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)
    assert item.item_type == "repository"

    meta = item.metadata
    assert meta["github_id"] == 12345
    assert meta["node_id"] == "node_123"
    assert meta["full_name"] == "owner/glintory"
    assert meta["owner_login"] == "owner"
    assert meta["created_at"] == "2026-07-06T00:00:00Z"
    assert meta["stargazers_count"] == 100
    assert meta["topics"] == ["python", "ai"]
    assert meta["license_spdx_id"] == "MIT"
    assert "owner" not in meta


@pytest.mark.asyncio
async def test_github_issue_mapping():
    issue_item = {
        "id": 67890,
        "node_id": "node_678",
        "number": 42,
        "title": "A test issue title",
        "html_url": "https://github.com/owner/glintory/issues/42",
        "body": "  This is the body of the issue\nwith some text.  ",
        "user": {"login": "issue_author"},
        "state": "open",
        "state_reason": None,
        "created_at": "2026-07-06T03:00:00Z",
        "updated_at": "2026-07-06T04:00:00Z",
        "closed_at": None,
        "comments": 3,
        "locked": False,
        "author_association": "MEMBER",
        "repository_url": "https://api.github.com/repos/owner/glintory",
        "labels": [{"name": "bug"}, {"name": "high-priority"}],
        "reactions": {"total_count": 10},
        "score": 1.0,
    }

    pr_item = {
        "id": 99999,
        "title": "PR to skip",
        "html_url": "https://github.com/owner/glintory/pull/1",
        "user": {"login": "pr_author"},
        "created_at": "2026-07-06T05:00:00Z",
        "pull_request": {"url": "https://api.github.com/repos/owner/glintory/pulls/1"},
    }

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "incomplete_results": False,
                "items": [issue_item, pr_item],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"issue_queries": [{"query": "test"}]},
        max_items=100,
        http=http_client,
    )

    collector = GitHubCollector(settings)
    result = await collector.collect(context)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.external_id == "github:issue:67890"
    assert item.url == "https://github.com/owner/glintory/issues/42"
    assert item.title == "A test issue title"
    assert item.excerpt == "This is the body of the issue with some text."
    assert item.author == "issue_author"
    assert item.published_at == datetime(2026, 7, 6, 3, 0, 0, tzinfo=UTC)
    assert item.item_type == "issue"

    meta = item.metadata
    assert meta["github_id"] == 67890
    assert meta["node_id"] == "node_678"
    assert meta["labels"] == ["bug", "high-priority"]
    assert meta["reactions_total_count"] == 10
    assert "reactions" not in meta


@pytest.mark.asyncio
async def test_github_deduplication():
    repo_item = {
        "id": 12345,
        "full_name": "owner/glintory",
        "html_url": "https://github.com/owner/glintory",
        "owner": {"login": "owner"},
        "created_at": "2026-07-06T00:00:00Z",
    }

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "incomplete_results": False,
                "items": [repo_item, repo_item],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"repository_queries": [{"query": "test"}]},
        max_items=100,
        http=http_client,
    )

    collector = GitHubCollector(settings)
    result = await collector.collect(context)

    assert len(result.items) == 1
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "GITHUB_DUPLICATE_ITEM"
    assert result.warnings[0].item_external_id == "github:repository:12345"


@pytest.mark.asyncio
async def test_github_pagination():
    calls = []

    def handler(request: httpx.Request):
        calls.append(str(request.url))
        if "page=2" not in str(request.url):
            headers = {
                "Link": '<https://api.github.com/search/repositories?q=test&page=2>; rel="next"'
            }
            items = [
                {
                    "id": 1,
                    "full_name": "repo/1",
                    "html_url": "url/1",
                    "owner": {"login": "owner"},
                    "created_at": "2026-07-06T00:00:00Z",
                }
            ]
        else:
            headers = {}
            items = [
                {
                    "id": 2,
                    "full_name": "repo/2",
                    "html_url": "url/2",
                    "owner": {"login": "owner"},
                    "created_at": "2026-07-06T00:00:00Z",
                }
            ]

        return httpx.Response(
            200,
            json={"total_count": 2, "incomplete_results": False, "items": items},
            headers=headers,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={
            "repository_queries": [{"query": "test"}],
            "max_pages_per_query": 3,
            "per_page": 1,
        },
        max_items=100,
        http=http_client,
    )
    collector = GitHubCollector(settings)
    result = await collector.collect(context)
    assert len(result.items) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_github_errors_and_rate_limits():
    def handler_401(request: httpx.Request):
        return httpx.Response(401, text="Unauthorized")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler_401))
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"repository_queries": [{"query": "test"}]},
        max_items=100,
        http=HttpxHttpClient(client=client),
    )
    collector = GitHubCollector(settings)
    with pytest.raises(GitHubAuthenticationError):
        await collector.collect(context)

    def handler_403_rate(request: httpx.Request):
        return httpx.Response(
            403,
            text="Rate Limit Exceeded",
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1780000000"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler_403_rate))
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"repository_queries": [{"query": "test"}]},
        max_items=100,
        http=HttpxHttpClient(client=client),
    )
    with pytest.raises(GitHubRateLimitError):
        await collector.collect(context)

    def handler_403_forbidden(request: httpx.Request):
        return httpx.Response(403, text="Forbidden")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler_403_forbidden))
    context = CollectionContext(
        source_id="1",
        source_name="GitHub",
        source_type="github",
        source_config={"repository_queries": [{"query": "test"}]},
        max_items=100,
        http=HttpxHttpClient(client=client),
    )
    with pytest.raises(GitHubResponseError):
        await collector.collect(context)
