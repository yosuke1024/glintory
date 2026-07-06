import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from glintory.collectors.base import (
    CollectionContext,
    CollectionError,
    CollectionResult,
    CollectionWarning,
    Collector,
    RawItem,
)
from glintory.infrastructure.http import (
    HttpInvalidJsonError,
    HttpRequestError,
    HttpResponseError,
)


# Exceptions
class GitHubCollectorError(Exception):
    pass


class GitHubConfigurationError(GitHubCollectorError):
    pass


class GitHubAuthenticationError(GitHubCollectorError):
    pass


class GitHubRateLimitError(GitHubCollectorError):
    pass


class GitHubResponseError(GitHubCollectorError):
    pass


class GitHubRepositoryQueryConfig(BaseModel):
    query: str
    sort: Literal["stars", "forks", "help-wanted-issues", "updated"] | None = None
    order: Literal["asc", "desc"] = "desc"
    max_items: int = 25

    @field_validator("query", mode="before")
    @classmethod
    def strip_and_validate_query(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("query must be a string")
        stripped = v.strip()
        if not stripped:
            raise ValueError("query cannot be empty")
        return stripped

    @field_validator("max_items")
    @classmethod
    def validate_max_items(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_items must be between 1 and 100")
        return v


class GitHubIssueQueryConfig(BaseModel):
    query: str
    sort: (
        Literal["comments", "reactions", "interactions", "created", "updated"] | None
    ) = None
    order: Literal["asc", "desc"] = "desc"
    max_items: int = 25

    @field_validator("query", mode="before")
    @classmethod
    def strip_and_validate_query(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("query must be a string")
        stripped = v.strip()
        if not stripped:
            raise ValueError("query cannot be empty")

        if re.search(r"\bis:pr\b", stripped, re.IGNORECASE):
            raise ValueError("Pull Request queries (is:pr) are not allowed.")

        return stripped

    @field_validator("max_items")
    @classmethod
    def validate_max_items(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_items must be between 1 and 100")
        return v


class GitHubSourceConfig(BaseModel):
    repository_queries: list[GitHubRepositoryQueryConfig] = Field(default_factory=list)
    issue_queries: list[GitHubIssueQueryConfig] = Field(default_factory=list)
    per_page: int = 50
    max_pages_per_query: int = 3
    include_forks: bool = False
    include_archived: bool = False

    model_config = {
        "extra": "forbid",
    }

    @model_validator(mode="after")
    def validate_queries(self) -> "GitHubSourceConfig":
        if not self.repository_queries and not self.issue_queries:
            raise ValueError(
                "At least one repository_query or issue_query must be provided"
            )
        return self

    @field_validator("per_page")
    @classmethod
    def validate_per_page(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("per_page must be between 1 and 100")
        return v

    @field_validator("max_pages_per_query")
    @classmethod
    def validate_max_pages_per_query(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("max_pages_per_query must be between 1 and 10")
        return v


# Helpers
def parse_utc_datetime(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        cleaned = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def normalize_text(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    text = text.replace("\0", "")
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text.strip()


def parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    parts = link_header.split(",")
    for part in parts:
        match = re.search(r'<\s*([^>]+)\s*>;\s*rel="next"', part, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


class GitHubCollector(Collector):
    source_type = "github"

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def validate_config(
        self,
        config: Mapping[str, object],
    ) -> Mapping[str, object]:
        validated = GitHubSourceConfig.model_validate(config)
        return validated.model_dump(mode="json")

    def get_config_summary(
        self,
        config: Mapping[str, Any],
    ) -> str:
        cfg = GitHubSourceConfig.model_validate(config)
        rep_queries = len(cfg.repository_queries)
        is_queries = len(cfg.issue_queries)
        return f"Repository queries: {rep_queries}\nIssue queries: {is_queries}\nPer page: {cfg.per_page}"

    @classmethod
    def normalize_repository_query(cls, query: str, config: GitHubSourceConfig) -> str:
        parts = [query]
        q_lower = query.lower()
        if not config.include_forks and "fork:" not in q_lower:
            parts.append("fork:false")
        if not config.include_archived and "archived:" not in q_lower:
            parts.append("archived:false")
        return " ".join(parts)

    @classmethod
    def normalize_issue_query(cls, query: str) -> str:
        parts = [query]
        q_lower = query.lower()
        if "is:issue" not in q_lower and "is:pr" not in q_lower:
            parts.append("is:issue")
        return " ".join(parts)

    async def collect(self, context: CollectionContext) -> CollectionResult:
        try:
            config = GitHubSourceConfig(**context.source_config)
        except ValidationError as e:
            # Mask sensitive data by not including raw input config
            raise GitHubConfigurationError(
                f"Invalid GitHub source configuration. Errors: {e.errors()}"
            ) from e

        # Setup base headers
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.settings.github_api_version,
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"

        base_url = self.settings.github_api_url
        max_excerpt = self.settings.github_excerpt_max_chars

        items: list[RawItem] = []
        warnings: list[CollectionWarning] = []
        errors: list[CollectionError] = []

        seen_keys: set[str] = set()
        duplicate_count = 0
        page_count = 0

        rate_limit_info: dict[str, Any] = {}

        def update_rate_limit(resp_headers: dict[str, str]) -> None:
            # Normalize key lookups
            normalized_headers = {k.lower(): v for k, v in resp_headers.items()}
            if "x-ratelimit-limit" in normalized_headers:
                reset_epoch = normalized_headers.get("x-ratelimit-reset")
                reset_str = None
                if reset_epoch:
                    try:
                        dt = datetime.fromtimestamp(int(reset_epoch), UTC)
                        reset_str = dt.isoformat().replace("+00:00", "Z")
                    except Exception:
                        pass
                rate_limit_info.update(
                    {
                        "limit": int(normalized_headers["x-ratelimit-limit"]),
                        "remaining": int(
                            normalized_headers.get("x-ratelimit-remaining", 0)
                        ),
                        "used": int(normalized_headers.get("x-ratelimit-used", 0)),
                        "reset_at": reset_str,
                        "resource": normalized_headers.get(
                            "x-ratelimit-resource", "search"
                        ),
                    }
                )

        # Parse API base URL host for verification
        base_host = urlparse(base_url).netloc

        # 1. Process Repository Queries
        for q_idx, req_q in enumerate(config.repository_queries):
            query_str = self.normalize_repository_query(req_q.query, config)
            params = {
                "q": query_str,
                "per_page": config.per_page,
            }
            if req_q.sort:
                params["sort"] = req_q.sort
            if req_q.order:
                params["order"] = req_q.order

            url = f"{base_url.rstrip('/')}/search/repositories"
            query_items_count = 0
            query_pages = 0
            visited_urls: set[str] = set()

            while url:
                if query_pages >= config.max_pages_per_query:
                    break
                if len(items) >= context.max_items:
                    break
                if query_items_count >= req_q.max_items:
                    break

                if url in visited_urls:
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_PAGINATION_LOOP",
                            message=f"Pagination loop detected at: {url}",
                        )
                    )
                    break
                visited_urls.add(url)

                # URL Validation
                parsed_url = urlparse(url)
                if parsed_url.scheme not in ("http", "https"):
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_INVALID_SCHEME",
                            message=f"Rejected URL scheme: {parsed_url.scheme}",
                        )
                    )
                    break
                if parsed_url.netloc != base_host:
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_INVALID_HOST",
                            message=f"Rejected host transition from {base_host} to {parsed_url.netloc}",
                        )
                    )
                    break

                try:
                    resp = await context.http.get_json(
                        url, headers=headers, params=params
                    )
                except HttpResponseError as e:
                    update_rate_limit(dict(e.headers))
                    if e.status_code == 401:
                        raise GitHubAuthenticationError(
                            "GitHub authentication failed. Check credentials."
                        ) from e
                    if e.status_code == 403:
                        rem = e.headers.get("x-ratelimit-remaining")
                        if rem == "0" or e.headers.get("retry-after"):
                            reset_t = e.headers.get("x-ratelimit-reset")
                            raise GitHubRateLimitError(
                                f"GitHub rate limit exceeded. Reset epoch: {reset_t}"
                            ) from e
                        raise GitHubResponseError(
                            f"GitHub access forbidden (403). Status: {e.status_code}"
                        ) from e
                    if e.status_code == 422:
                        errors.append(
                            CollectionError(
                                code="GITHUB_QUERY_422",
                                message=f"Unprocessable query (422) for repo query index {q_idx}",
                                retryable=False,
                            )
                        )
                        break
                    errors.append(
                        CollectionError(
                            code="GITHUB_HTTP_ERROR",
                            message=f"HTTP status {e.status_code} for repo query index {q_idx}",
                            retryable=True,
                        )
                    )
                    break
                except (HttpInvalidJsonError, HttpRequestError) as e:
                    errors.append(
                        CollectionError(
                            code="GITHUB_CLIENT_ERROR",
                            message=f"HTTP client error for repo query index {q_idx}: {e}",
                            retryable=True,
                        )
                    )
                    break

                page_count += 1
                query_pages += 1
                update_rate_limit(dict(resp.headers))

                # Verify payload
                try:
                    data = resp.json()
                except Exception as e:
                    errors.append(
                        CollectionError(
                            code="GITHUB_JSON_PARSE",
                            message=f"Failed to parse response body as JSON: {e}",
                            retryable=False,
                        )
                    )
                    break

                if (
                    not isinstance(data, dict)
                    or "items" not in data
                    or not isinstance(data["items"], list)
                ):
                    errors.append(
                        CollectionError(
                            code="GITHUB_INVALID_PAYLOAD",
                            message="Invalid search response payload structure",
                            retryable=False,
                        )
                    )
                    break

                if data.get("incomplete_results") is True:
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_INCOMPLETE_RESULTS",
                            message="Search results are incomplete due to GitHub timeout",
                        )
                    )

                for raw in data["items"]:
                    if (
                        len(items) >= context.max_items
                        or query_items_count >= req_q.max_items
                    ):
                        break

                    # Validate item requirements
                    item_id = raw.get("id")
                    html_url = raw.get("html_url")
                    full_name = raw.get("full_name")
                    owner = raw.get("owner")
                    created_at = raw.get("created_at")

                    if (
                        item_id is None
                        or not html_url
                        or not full_name
                        or not owner
                        or not isinstance(owner, dict)
                        or not owner.get("login")
                        or not created_at
                    ):
                        errors.append(
                            CollectionError(
                                code="GITHUB_ITEM_MISSING_FIELDS",
                                message="Repository item skipped due to missing required fields",
                                retryable=False,
                                item_external_id=f"github:repository:{item_id}"
                                if item_id
                                else None,
                            )
                        )
                        continue

                    dup_key = f"repository:github:repository:{item_id}"
                    if dup_key in seen_keys:
                        duplicate_count += 1
                        warnings.append(
                            CollectionWarning(
                                code="GITHUB_DUPLICATE_ITEM",
                                message=f"Duplicate repository item skipped: {dup_key}",
                                item_external_id=f"github:repository:{item_id}",
                            )
                        )
                        continue
                    seen_keys.add(dup_key)

                    published = parse_utc_datetime(created_at)
                    if published is None:
                        errors.append(
                            CollectionError(
                                code="GITHUB_ITEM_INVALID_DATE",
                                message=f"Invalid date format: {created_at}",
                                retryable=False,
                                item_external_id=f"github:repository:{item_id}",
                            )
                        )
                        continue

                    # Extract metadata whitelist
                    metadata = {}
                    for field_name in (
                        "node_id",
                        "full_name",
                        "created_at",
                        "updated_at",
                        "pushed_at",
                        "stargazers_count",
                        "watchers_count",
                        "forks_count",
                        "open_issues_count",
                        "language",
                        "topics",
                        "archived",
                        "fork",
                        "homepage",
                        "default_branch",
                        "visibility",
                        "score",
                    ):
                        if field_name in raw:
                            metadata[field_name] = raw[field_name]
                    metadata["github_id"] = item_id
                    metadata["owner_login"] = owner["login"]
                    if "license" in raw and isinstance(raw["license"], dict):
                        metadata["license_spdx_id"] = raw["license"].get("spdx_id")

                    items.append(
                        RawItem(
                            external_id=f"github:repository:{item_id}",
                            url=html_url,
                            title=full_name,
                            excerpt=normalize_text(raw.get("description"), max_excerpt),
                            author=owner["login"],
                            published_at=published,
                            item_type="repository",
                            metadata=metadata,
                        )
                    )
                    query_items_count += 1

                # Set next page
                params = None
                url = parse_next_link(
                    resp.headers.get("link") or resp.headers.get("Link")
                )

        # 2. Process Issue Queries
        for q_idx, req_q in enumerate(config.issue_queries):
            query_str = self.normalize_issue_query(req_q.query)
            params = {
                "q": query_str,
                "per_page": config.per_page,
            }
            if req_q.sort:
                params["sort"] = req_q.sort
            if req_q.order:
                params["order"] = req_q.order

            url = f"{base_url.rstrip('/')}/search/issues"
            query_items_count = 0
            query_pages = 0
            visited_urls = set()

            while url:
                if query_pages >= config.max_pages_per_query:
                    break
                if len(items) >= context.max_items:
                    break
                if query_items_count >= req_q.max_items:
                    break

                if url in visited_urls:
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_PAGINATION_LOOP",
                            message=f"Pagination loop detected at: {url}",
                        )
                    )
                    break
                visited_urls.add(url)

                parsed_url = urlparse(url)
                if parsed_url.scheme not in ("http", "https"):
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_INVALID_SCHEME",
                            message=f"Rejected URL scheme: {parsed_url.scheme}",
                        )
                    )
                    break
                if parsed_url.netloc != base_host:
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_INVALID_HOST",
                            message=f"Rejected host transition from {base_host} to {parsed_url.netloc}",
                        )
                    )
                    break

                try:
                    resp = await context.http.get_json(
                        url, headers=headers, params=params
                    )
                except HttpResponseError as e:
                    update_rate_limit(dict(e.headers))
                    if e.status_code == 401:
                        raise GitHubAuthenticationError(
                            "GitHub authentication failed. Check credentials."
                        ) from e
                    if e.status_code == 403:
                        rem = e.headers.get("x-ratelimit-remaining")
                        if rem == "0" or e.headers.get("retry-after"):
                            reset_t = e.headers.get("x-ratelimit-reset")
                            raise GitHubRateLimitError(
                                f"GitHub rate limit exceeded. Reset epoch: {reset_t}"
                            ) from e
                        raise GitHubResponseError(
                            f"GitHub access forbidden (403). Status: {e.status_code}"
                        ) from e
                    if e.status_code == 422:
                        errors.append(
                            CollectionError(
                                code="GITHUB_QUERY_422",
                                message=f"Unprocessable query (422) for issue query index {q_idx}",
                                retryable=False,
                            )
                        )
                        break
                    errors.append(
                        CollectionError(
                            code="GITHUB_HTTP_ERROR",
                            message=f"HTTP status {e.status_code} for issue query index {q_idx}",
                            retryable=True,
                        )
                    )
                    break
                except (HttpInvalidJsonError, HttpRequestError) as e:
                    errors.append(
                        CollectionError(
                            code="GITHUB_CLIENT_ERROR",
                            message=f"HTTP client error for issue query index {q_idx}: {e}",
                            retryable=True,
                        )
                    )
                    break

                page_count += 1
                query_pages += 1
                update_rate_limit(dict(resp.headers))

                try:
                    data = resp.json()
                except Exception as e:
                    errors.append(
                        CollectionError(
                            code="GITHUB_JSON_PARSE",
                            message=f"Failed to parse response body as JSON: {e}",
                            retryable=False,
                        )
                    )
                    break

                if (
                    not isinstance(data, dict)
                    or "items" not in data
                    or not isinstance(data["items"], list)
                ):
                    errors.append(
                        CollectionError(
                            code="GITHUB_INVALID_PAYLOAD",
                            message="Invalid search response payload structure",
                            retryable=False,
                        )
                    )
                    break

                if data.get("incomplete_results") is True:
                    warnings.append(
                        CollectionWarning(
                            code="GITHUB_INCOMPLETE_RESULTS",
                            message="Search results are incomplete due to GitHub timeout",
                        )
                    )

                for raw in data["items"]:
                    if (
                        len(items) >= context.max_items
                        or query_items_count >= req_q.max_items
                    ):
                        break

                    if "pull_request" in raw:
                        continue

                    item_id = raw.get("id")
                    html_url = raw.get("html_url")
                    title = raw.get("title")
                    user = raw.get("user")
                    created_at = raw.get("created_at")

                    if (
                        item_id is None
                        or not html_url
                        or not title
                        or not user
                        or not isinstance(user, dict)
                        or not user.get("login")
                        or not created_at
                    ):
                        errors.append(
                            CollectionError(
                                code="GITHUB_ITEM_MISSING_FIELDS",
                                message="Issue item skipped due to missing required fields",
                                retryable=False,
                                item_external_id=f"github:issue:{item_id}"
                                if item_id
                                else None,
                            )
                        )
                        continue

                    dup_key = f"issue:github:issue:{item_id}"
                    if dup_key in seen_keys:
                        duplicate_count += 1
                        warnings.append(
                            CollectionWarning(
                                code="GITHUB_DUPLICATE_ITEM",
                                message=f"Duplicate issue item skipped: {dup_key}",
                                item_external_id=f"github:issue:{item_id}",
                            )
                        )
                        continue
                    seen_keys.add(dup_key)

                    published = parse_utc_datetime(created_at)
                    if published is None:
                        errors.append(
                            CollectionError(
                                code="GITHUB_ITEM_INVALID_DATE",
                                message=f"Invalid date format: {created_at}",
                                retryable=False,
                                item_external_id=f"github:issue:{item_id}",
                            )
                        )
                        continue

                    metadata = {}
                    for field_name in (
                        "node_id",
                        "number",
                        "state",
                        "state_reason",
                        "created_at",
                        "updated_at",
                        "closed_at",
                        "comments",
                        "locked",
                        "author_association",
                        "repository_url",
                        "score",
                    ):
                        if field_name in raw:
                            metadata[field_name] = raw[field_name]
                    metadata["github_id"] = item_id

                    if "labels" in raw and isinstance(raw["labels"], list):
                        metadata["labels"] = [
                            lbl["name"]
                            for lbl in raw["labels"]
                            if isinstance(lbl, dict) and "name" in lbl
                        ]

                    if "reactions" in raw and isinstance(raw["reactions"], dict):
                        metadata["reactions_total_count"] = raw["reactions"].get(
                            "total_count", 0
                        )

                    items.append(
                        RawItem(
                            external_id=f"github:issue:{item_id}",
                            url=html_url,
                            title=title,
                            excerpt=normalize_text(raw.get("body"), max_excerpt),
                            author=user["login"],
                            published_at=published,
                            item_type="issue",
                            metadata=metadata,
                        )
                    )
                    query_items_count += 1

                params = None
                url = parse_next_link(
                    resp.headers.get("link") or resp.headers.get("Link")
                )

        repo_item_count = sum(1 for i in items if i.item_type == "repository")
        issue_item_count = sum(1 for i in items if i.item_type == "issue")

        result_metadata = {
            "collector": "github",
            "repository_query_count": len(config.repository_queries),
            "issue_query_count": len(config.issue_queries),
            "repository_item_count": repo_item_count,
            "issue_item_count": issue_item_count,
            "duplicate_count": duplicate_count,
            "page_count": page_count,
        }
        if rate_limit_info:
            result_metadata["github_rate_limit"] = rate_limit_info

        return CollectionResult(
            items=items,
            warnings=warnings,
            errors=errors,
            metadata=result_metadata,
        )
