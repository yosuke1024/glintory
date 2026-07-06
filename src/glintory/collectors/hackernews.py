from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
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
from glintory.config import Settings
from glintory.infrastructure.http import (
    HttpInvalidJsonError,
    HttpRequestError,
    HttpResponseError,
)
from glintory.services.html_to_text import html_to_plain_text


class HackerNewsCollectorError(Exception):
    pass


class HackerNewsConfigurationError(HackerNewsCollectorError):
    pass


class HackerNewsSourceConfig(BaseModel):
    feeds: list[Literal["top", "new", "best", "ask", "show", "job"]] = Field(
        default_factory=lambda: ["ask", "show", "new"]
    )

    max_items_per_feed: int = 25
    include_jobs: bool = False
    include_dead: bool = False
    include_deleted: bool = False
    minimum_score: int = 0
    lookback_days: int | None = 90

    model_config = {
        "extra": "forbid",
    }

    @field_validator("feeds", mode="after")
    @classmethod
    def validate_feeds(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one feed must be specified")
        # Stable deduplication
        seen = set()
        deduped = []
        for feed in v:
            if feed not in seen:
                seen.add(feed)
                deduped.append(feed)
        return deduped

    @field_validator("max_items_per_feed")
    @classmethod
    def validate_max_items(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_items_per_feed must be between 1 and 100")
        return v

    @field_validator("minimum_score")
    @classmethod
    def validate_minimum_score(cls, v: int) -> int:
        if v < 0:
            raise ValueError("minimum_score must be 0 or greater")
        return v

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 3650):
            raise ValueError("lookback_days must be between 1 and 3650")
        return v

    @model_validator(mode="after")
    def validate_job_config(self) -> "HackerNewsSourceConfig":
        if "job" in self.feeds and not self.include_jobs:
            raise ValueError("include_jobs must be true if 'job' feed is selected")
        return self


class HackerNewsItem(BaseModel):
    id: int
    type: Literal[
        "job",
        "story",
        "comment",
        "poll",
        "pollopt",
    ]
    by: str | None = None
    time: int | None = None
    text: str | None = None
    dead: bool = False
    deleted: bool = False
    parent: int | None = None
    kids: list[int] = Field(default_factory=list)
    url: str | None = None
    score: int | None = None
    title: str | None = None
    descendants: int | None = None

    model_config = {
        "extra": "ignore",
    }

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("id must be a positive integer")
        return v

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("score cannot be negative")
        return v

    @field_validator("descendants")
    @classmethod
    def validate_descendants(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("descendants cannot be negative")
        return v


class HackerNewsCollector(Collector):
    source_type = "hackernews"

    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))

    def validate_config(
        self,
        config: Mapping[str, object],
    ) -> Mapping[str, object]:
        validated = HackerNewsSourceConfig.model_validate(config)
        return validated.model_dump(mode="json")

    def get_config_summary(
        self,
        config: Mapping[str, Any],
    ) -> str:
        cfg = HackerNewsSourceConfig.model_validate(config)
        feeds = ", ".join(cfg.feeds)
        return f"Feeds: {feeds}\nMax items per feed: {cfg.max_items_per_feed}\nLookback days: {cfg.lookback_days}"

    async def collect(self, context: CollectionContext) -> CollectionResult:
        collected_at = self.clock()
        if (
            collected_at.tzinfo is None
            or collected_at.tzinfo.utcoffset(collected_at) is None
        ):
            collected_at = collected_at.replace(tzinfo=UTC)

        try:
            config = HackerNewsSourceConfig(**context.source_config)
        except ValidationError as e:
            # Mask sensitive data by not including raw input config
            raise HackerNewsConfigurationError(
                f"Invalid Hacker News source configuration. Errors: {e.errors()}"
            ) from e

        api_url = self.settings.hn_api_url.rstrip("/")
        warnings: list[CollectionWarning] = []
        errors: list[CollectionError] = []

        feed_to_endpoint = {
            "top": "/topstories.json",
            "new": "/newstories.json",
            "best": "/beststories.json",
            "ask": "/askstories.json",
            "show": "/showstories.json",
            "job": "/jobstories.json",
        }

        # Track feed IDs and order
        unique_ids: list[int] = []
        id_to_feeds: dict[int, list[str]] = {}
        successful_feed_count = 0
        failed_feed_count = 0
        total_feed_id_count = 0
        duplicate_item_id_count = 0

        # 1. Fetch Feed lists
        for feed in config.feeds:
            endpoint = feed_to_endpoint[feed]
            url = f"{api_url}{endpoint}"
            try:
                response = await context.http.get_json(url)
                data = response.json()
                if not isinstance(data, list):
                    errors.append(
                        CollectionError(
                            code="malformed_feed_list",
                            message=f"Feed '{feed}' response is not a list.",
                            retryable=False,
                        )
                    )
                    failed_feed_count += 1
                    continue

                successful_feed_count += 1
                feed_ids = []
                for index, val in enumerate(data):
                    # Validate ID
                    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                        errors.append(
                            CollectionError(
                                code="invalid_id_type",
                                message=f"Feed '{feed}' at index {index} has invalid ID: {val}",
                                retryable=False,
                            )
                        )
                        continue
                    feed_ids.append(val)

                total_feed_id_count += len(feed_ids)
                # Keep first max_items_per_feed
                sliced_ids = feed_ids[: config.max_items_per_feed]

                for item_id in sliced_ids:
                    if item_id in id_to_feeds:
                        id_to_feeds[item_id].append(feed)
                        duplicate_item_id_count += 1
                    else:
                        id_to_feeds[item_id] = [feed]
                        unique_ids.append(item_id)

            except HttpResponseError as e:
                failed_feed_count += 1
                retryable = e.status_code is not None and e.status_code >= 500
                errors.append(
                    CollectionError(
                        code="feed_http_error",
                        message=f"HTTP error {e.status_code} fetching feed '{feed}'.",
                        retryable=retryable,
                    )
                )
            except (HttpInvalidJsonError, ValidationError) as e:
                failed_feed_count += 1
                errors.append(
                    CollectionError(
                        code="feed_json_error",
                        message=f"JSON parsing error fetching feed '{feed}': {e}",
                        retryable=False,
                    )
                )
            except HttpRequestError as e:
                failed_feed_count += 1
                errors.append(
                    CollectionError(
                        code="feed_request_error",
                        message=f"Request error fetching feed '{feed}': {e}",
                        retryable=True,
                    )
                )
            except Exception as e:
                failed_feed_count += 1
                errors.append(
                    CollectionError(
                        code="feed_unexpected_error",
                        message=f"Unexpected error fetching feed '{feed}': {e}",
                        retryable=False,
                    )
                )

        if len(config.feeds) > 0 and successful_feed_count == 0:
            # If all requested feeds failed
            return CollectionResult(
                items=[],
                warnings=warnings,
                errors=errors,
                metadata={
                    "collector": "hackernews",
                    "requested_feeds": config.feeds,
                    "successful_feed_count": successful_feed_count,
                    "failed_feed_count": failed_feed_count,
                    "feed_id_count": 0,
                    "unique_item_id_count": 0,
                    "duplicate_item_id_count": 0,
                    "fetched_item_count": 0,
                    "accepted_item_count": 0,
                    "skipped_dead_count": 0,
                    "skipped_deleted_count": 0,
                    "skipped_old_count": 0,
                    "skipped_low_score_count": 0,
                    "skipped_unsupported_type_count": 0,
                },
            )

        if duplicate_item_id_count > 0:
            warnings.append(
                CollectionWarning(
                    code="duplicate_items",
                    message=f"Deduplicated {duplicate_item_id_count} items across feeds.",
                )
            )

        # 2. Fetch items sequentially
        items: list[RawItem] = []
        fetched_item_count = 0
        accepted_item_count = 0
        skipped_dead_count = 0
        skipped_deleted_count = 0
        skipped_old_count = 0
        skipped_low_score_count = 0
        skipped_unsupported_type_count = 0

        for item_id in unique_ids:
            if accepted_item_count >= context.max_items:
                break

            fetched_item_count += 1
            item_url = f"{api_url}/item/{item_id}.json"
            try:
                response = await context.http.get_json(item_url)
                data = response.json()
                if data is None:
                    warnings.append(
                        CollectionWarning(
                            code="item_null_response",
                            message=f"Item {item_id} returned null.",
                            item_external_id=f"hackernews:item:{item_id}",
                        )
                    )
                    continue

                item = HackerNewsItem(**data)
            except HttpResponseError as e:
                retryable = e.status_code is not None and e.status_code >= 500
                errors.append(
                    CollectionError(
                        code="item_http_error",
                        message=f"HTTP error {e.status_code} fetching item {item_id}.",
                        retryable=retryable,
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue
            except (HttpInvalidJsonError, ValidationError) as e:
                errors.append(
                    CollectionError(
                        code="item_validation_error",
                        message=f"Validation/JSON error fetching item {item_id}: {e}",
                        retryable=False,
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue
            except HttpRequestError as e:
                errors.append(
                    CollectionError(
                        code="item_request_error",
                        message=f"Request error fetching item {item_id}: {e}",
                        retryable=True,
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue
            except Exception as e:
                errors.append(
                    CollectionError(
                        code="item_unexpected_error",
                        message=f"Unexpected error fetching item {item_id}: {e}",
                        retryable=False,
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue

            # Filtering & Verification
            # Supported type check
            is_job = item.type == "job"
            is_story = item.type == "story"

            if not (is_story or (is_job and config.include_jobs)):
                skipped_unsupported_type_count += 1
                warnings.append(
                    CollectionWarning(
                        code="unsupported_item_type",
                        message=f"Item {item_id} has unsupported type: {item.type}",
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue

            # Deleted check
            if item.deleted:
                if not config.include_deleted:
                    skipped_deleted_count += 1
                    warnings.append(
                        CollectionWarning(
                            code="item_deleted",
                            message=f"Item {item_id} is deleted.",
                            item_external_id=f"hackernews:item:{item_id}",
                        )
                    )
                    continue
                if not item.title:
                    # Ignore deleted items without title even if include_deleted is True
                    skipped_deleted_count += 1
                    warnings.append(
                        CollectionWarning(
                            code="item_deleted_no_title",
                            message=f"Item {item_id} is deleted and has no title.",
                            item_external_id=f"hackernews:item:{item_id}",
                        )
                    )
                    continue

            # Dead check
            if item.dead and not config.include_dead:
                skipped_dead_count += 1
                warnings.append(
                    CollectionWarning(
                        code="item_dead",
                        message=f"Item {item_id} is dead.",
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue
                # If include_dead is True, we require mandatory fields (title is validated below)

            # DateTime conversion
            published_at = None
            if item.time is not None:
                try:
                    if item.time <= 0:
                        raise ValueError("time must be positive")
                    published_at = datetime.fromtimestamp(item.time, tz=UTC)
                except Exception as e:
                    errors.append(
                        CollectionError(
                            code="item_invalid_time",
                            message=f"Invalid timestamp '{item.time}' for item {item_id}: {e}",
                            retryable=False,
                            item_external_id=f"hackernews:item:{item_id}",
                        )
                    )
                    continue

            # Lookback filter
            if config.lookback_days is not None and published_at is not None:
                cutoff = collected_at - timedelta(days=config.lookback_days)
                if published_at < cutoff:
                    skipped_old_count += 1
                    warnings.append(
                        CollectionWarning(
                            code="item_lookback_exceeded",
                            message=f"Item {item_id} published_at {published_at.isoformat()} exceeds lookback.",
                            item_external_id=f"hackernews:item:{item_id}",
                        )
                    )
                    continue

            # Minimum score filter
            item_score = item.score or 0
            if not is_job and item_score < config.minimum_score:
                skipped_low_score_count += 1
                continue

            # Resolve item type
            # Resolution order: job > ask > show > story
            feeds_found = id_to_feeds[item_id]
            is_ask_feed = "ask" in feeds_found
            is_show_feed = "show" in feeds_found
            is_job_feed = "job" in feeds_found

            title_str = item.title or ""
            title_lower = title_str.lower()
            is_ask_title = title_lower.startswith("ask hn:")
            is_show_title = title_lower.startswith("show hn:")

            resolved_item_type = "hn_story"
            if is_job or is_job_feed:
                resolved_item_type = "hn_job"
            elif is_ask_feed or is_ask_title:
                resolved_item_type = "hn_ask"
            elif is_show_feed or is_show_title:
                resolved_item_type = "hn_show"

            # Parse HTML content
            max_chars = self.settings.hn_text_max_chars
            plain_title = html_to_plain_text(item.title, max_chars=max_chars)
            if not plain_title:
                errors.append(
                    CollectionError(
                        code="empty_title",
                        message=f"Title of item {item_id} is empty after stripping HTML.",
                        retryable=False,
                        item_external_id=f"hackernews:item:{item_id}",
                    )
                )
                continue

            plain_text = html_to_plain_text(item.text, max_chars=max_chars)

            # Map to RawItem
            discussion_url = self.settings.hn_web_item_url_template.format(
                item_id=item_id
            )

            # Whitelisted metadata
            outbound_host = None
            if item.url:
                try:
                    parsed = urlparse(item.url)
                    outbound_host = parsed.netloc
                except Exception:
                    pass

            item_metadata = {
                "hn_id": item.id,
                "hn_item_type": item.type,
                "score": item.score,
                "descendants": item.descendants,
                "kids_count": len(item.kids),
                "outbound_url": item.url,
                "outbound_host": outbound_host,
                "is_ask": resolved_item_type == "hn_ask",
                "is_show": resolved_item_type == "hn_show",
                "is_job": resolved_item_type == "hn_job",
            }

            raw_item = RawItem(
                external_id=f"hackernews:item:{item_id}",
                url=discussion_url,
                title=plain_title,
                excerpt=plain_text if plain_text else None,
                author=item.by,
                published_at=published_at,
                item_type=resolved_item_type,
                metadata=item_metadata,
            )
            items.append(raw_item)
            accepted_item_count += 1

        metadata = {
            "collector": "hackernews",
            "requested_feeds": config.feeds,
            "successful_feed_count": successful_feed_count,
            "failed_feed_count": failed_feed_count,
            "feed_id_count": total_feed_id_count,
            "unique_item_id_count": len(unique_ids),
            "duplicate_item_id_count": duplicate_item_id_count,
            "fetched_item_count": fetched_item_count,
            "accepted_item_count": accepted_item_count,
            "skipped_dead_count": skipped_dead_count,
            "skipped_deleted_count": skipped_deleted_count,
            "skipped_old_count": skipped_old_count,
            "skipped_low_score_count": skipped_low_score_count,
            "skipped_unsupported_type_count": skipped_unsupported_type_count,
        }

        return CollectionResult(
            items=items,
            warnings=warnings,
            errors=errors,
            metadata=metadata,
        )
