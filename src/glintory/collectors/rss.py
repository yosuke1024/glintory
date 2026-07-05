import calendar
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import feedparser
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from glintory.collectors.base import (
    CollectionContext,
    CollectionError,
    CollectionResult,
    CollectionWarning,
    Collector,
    RawItem,
)
from glintory.domain.enums import SignalType
from glintory.infrastructure.http import (
    HttpRequestError,
    HttpResponseError,
    validate_url_safety,
)
from glintory.services.html_to_text import html_to_plain_text
from glintory.services.url_normalization import normalize_url

logger = logging.getLogger(__name__)


# Exceptions
class RSSCollectorError(Exception):
    pass


class RSSConfigurationError(RSSCollectorError):
    pass


class RSSFetchError(RSSCollectorError):
    pass


class RSSParseError(RSSCollectorError):
    pass


class RSSInvalidFeedError(RSSCollectorError):
    pass


class RSSInvalidEntryError(RSSCollectorError):
    pass


class RSSMissingEntryLinkError(RSSCollectorError):
    pass


class RSSSourceConfig(BaseModel):
    feed_url: str

    max_items: int = 50
    max_entries_to_scan: int = 200
    lookback_days: int | None = 180
    include_undated: bool = True

    signal_type: SignalType = SignalType.TREND
    default_tags: list[str] = Field(default_factory=list)
    default_categories: list[str] = Field(
        default_factory=lambda: ["rss"]
    )

    strict_parsing: bool = False
    use_content_fallback: bool = True

    model_config = {
        "extra": "forbid",
    }

    @field_validator("feed_url")
    @classmethod
    def validate_feed_url(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("feed_url must be a non-empty string")

        parsed = urlparse(v)
        # Re-build URL without fragment to reject or strip it
        cleaned_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            ""
        ))

        try:
            validate_url_safety(cleaned_url)
        except Exception as e:
            # Mask the original query / host info if it contains credentials or sensitive parts in validation error
            raise ValueError(f"URL is not allowed: {str(e)}") from e

        return cleaned_url

    @field_validator("max_items")
    @classmethod
    def validate_max_items(cls, v: int) -> int:
        if not (1 <= v <= 200):
            raise ValueError("max_items must be between 1 and 200")
        return v

    @field_validator("max_entries_to_scan")
    @classmethod
    def validate_max_entries_to_scan(cls, v: int) -> int:
        if not (1 <= v <= 1000):
            raise ValueError("max_entries_to_scan must be between 1 and 1000")
        return v

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 3650):
            raise ValueError("lookback_days must be between 1 and 3650 or null")
        return v

    @field_validator("default_tags")
    @classmethod
    def validate_default_tags(cls, v: list[str]) -> list[str]:
        if len(v) > 50:
            raise ValueError("default_tags cannot exceed 50 items")
        for tag in v:
            if len(tag) > 100:
                raise ValueError("each tag must be 100 characters or less")
        return v

    @field_validator("default_categories")
    @classmethod
    def validate_default_categories(cls, v: list[str]) -> list[str]:
        if len(v) > 20:
            raise ValueError("default_categories cannot exceed 20 items")
        for cat in v:
            if len(cat) > 100:
                raise ValueError("each category must be 100 characters or less")
        return v

    @field_validator("signal_type")
    @classmethod
    def validate_signal_type(cls, v: SignalType) -> SignalType:
        if v == SignalType.MANUAL:
            raise ValueError("SignalType.MANUAL is not allowed for RSS sources")
        return v

    @model_validator(mode="after")
    def validate_scan_gte_items(self) -> "RSSSourceConfig":
        if self.max_entries_to_scan < self.max_items:
            raise ValueError("max_entries_to_scan must be greater than or equal to max_items")
        return self


def struct_time_to_datetime(parsed_time: Any) -> datetime | None:
    if not parsed_time:
        return None
    try:
        return datetime.fromtimestamp(
            calendar.timegm(parsed_time),
            tz=UTC,
        )
    except Exception:
        return None


class RSSCollector(Collector):
    source_type = "rss"

    def __init__(self, settings: Any, time_func=None) -> None:
        self.settings = settings
        self._time_func = time_func or (lambda: datetime.now(UTC))

    async def collect(self, context: CollectionContext) -> CollectionResult:
        # 1. Parse and Validate Configuration
        try:
            config = RSSSourceConfig(**context.source_config)
        except ValidationError as e:
            # Mask sensitive data by removing input value or detailed query context
            clean_errors = []
            for err in e.errors():
                loc = ".".join(str(loc_val) for loc_val in err.get("loc", []))
                msg = err.get("msg", "Validation error")
                clean_errors.append(f"{loc}: {msg}")
            raise RSSConfigurationError(
                f"Invalid RSS source configuration. Errors: {', '.join(clean_errors)}"
            ) from e

        collected_at = self._time_func()
        warnings: list[CollectionWarning] = []
        errors: list[CollectionError] = []

        # 2. Fetch Feed Bytes
        headers = {
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1"
        }
        try:
            response = await context.http.get_bytes(config.feed_url, headers=headers)
        except (HttpResponseError, HttpRequestError) as e:
            # Remove feed URL query parameter from the error message for safety
            safe_msg = str(e)
            if "?" in safe_msg:
                safe_msg = safe_msg.split("?", maxsplit=1)[0]
            raise RSSFetchError(f"rss_fetch_failed: {safe_msg}") from e

        # Check content type
        content_type = response.headers.get("content-type", "").lower()
        allowed_types = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
        if not any(allowed in content_type for allowed in allowed_types):
            warnings.append(
                CollectionWarning(
                    code="unexpected_content_type",
                    message=f"Expected feed Content-Type but got: {content_type}",
                )
            )

        # 3. Parse Feed
        # feedparser.parse can accept response_headers, which is useful for resolving relative URIs.
        # href parameter specifies the base URI of the feed.
        try:
            parser_headers = dict(response.headers)
            parser_headers["content-location"] = response.url
            parsed = feedparser.parse(
                response.content,
                sanitize_html=True,
                resolve_relative_uris=True,
                response_headers=parser_headers,
            )
        except Exception as e:
            if isinstance(e, Warning):
                raise e
            raise RSSParseError(f"rss_parse_failed: Failed to parse feed content: {e}") from e

        # Handle version / format
        version = parsed.version or ""
        expected_formats = {"rss20", "rss10", "atom10"}
        if not version:
            warnings.append(
                CollectionWarning(
                    code="empty_feed_version",
                    message="Feed version could not be determined.",
                )
            )
        elif version not in expected_formats:
            warnings.append(
                CollectionWarning(
                    code="unknown_feed_version",
                    message=f"Unknown feed version format: {version}",
                )
            )

        # 4. Bozo Check
        if parsed.bozo:
            exc = parsed.bozo_exception
            exc_name = type(exc).__name__ if exc else "UnknownException"
            # Safely sanitize message to 500 chars
            exc_msg = str(exc)[:500] if exc else "Bozo parsing error"
            if "?" in exc_msg:
                exc_msg = exc_msg.split("?")[0]

            if config.strict_parsing:
                raise RSSInvalidFeedError(
                    f"rss_invalid_feed: Bozo error under strict parsing: {exc_name} - {exc_msg}"
                )

            # Non-strict: check if entries exist
            if not parsed.entries:
                raise RSSInvalidFeedError(
                    f"rss_invalid_feed: Bozo error and no entries could be extracted: {exc_name} - {exc_msg}"
                )

            # Warning
            warnings.append(
                CollectionWarning(
                    code="bozo_warning",
                    message=f"Bozo parser exception: {exc_name} - {exc_msg}",
                )
            )

        # 5. Extract Feed Metadata
        feed_title = html_to_plain_text(parsed.feed.get("title"), max_chars=500) if parsed.feed.get("title") else ""
        feed_home_url = parsed.feed.get("link") or ""
        # Validate feed_home_url scheme
        if feed_home_url:
            pu = urlparse(feed_home_url)
            if pu.scheme not in ("http", "https"):
                feed_home_url = ""
        feed_language = parsed.feed.get("language") or ""
        feed_host = urlparse(response.url).netloc or ""

        # 6. Process entries
        raw_items = []
        
        # Statistics
        fetched_entry_count = len(parsed.entries)
        scanned_entry_count = 0
        accepted_entry_count = 0
        duplicate_entry_count = 0
        skipped_old_count = 0
        skipped_undated_count = 0
        skipped_missing_title_count = 0
        skipped_missing_link_count = 0
        invalid_entry_count = 0

        # Maintain order of elements in feed
        entries_to_process = parsed.entries[:config.max_entries_to_scan]
        scanned_entry_count = len(entries_to_process)

        # Deduplication helpers
        seen_ids = set()
        seen_urls = set()

        for entry in entries_to_process:
            try:
                # A. Title
                raw_title = entry.get("title")
                if not raw_title:
                    errors.append(
                        CollectionError(
                            code="rss_missing_title",
                            message="Entry is missing a title.",
                            retryable=False,
                        )
                    )
                    skipped_missing_title_count += 1
                    continue

                plain_title = html_to_plain_text(raw_title, max_chars=500)
                if not plain_title:
                    errors.append(
                        CollectionError(
                            code="rss_missing_title",
                            message="Entry title is empty after plain text conversion.",
                            retryable=False,
                        )
                    )
                    skipped_missing_title_count += 1
                    continue

                # B. Canonical URL
                canonical_entry_url = ""
                # Priority 1: entry.link
                link_candidate = entry.get("link")
                if link_candidate:
                    canonical_entry_url = link_candidate
                else:
                    # Priority 2: links alternate
                    links = entry.get("links") or []
                    for link in links:
                        if link.get("rel") == "alternate" and link.get("href"):
                            canonical_entry_url = link.get("href")
                            break
                    
                    # Priority 3: entry.id if valid http/https URL
                    if not canonical_entry_url:
                        entry_id_candidate = entry.get("id")
                        if entry_id_candidate:
                            pu = urlparse(entry_id_candidate)
                            if pu.scheme in ("http", "https"):
                                canonical_entry_url = entry_id_candidate

                if not canonical_entry_url:
                    errors.append(
                        CollectionError(
                            code="rss_missing_link",
                            message="Entry is missing a valid canonical link.",
                            retryable=False,
                        )
                    )
                    skipped_missing_link_count += 1
                    continue

                # Resolve relative URL using response.url
                canonical_entry_url = urljoin(response.url, canonical_entry_url)
                
                # Check Scheme/Host validation
                p_entry_url = urlparse(canonical_entry_url)
                if p_entry_url.scheme not in ("http", "https") or not p_entry_url.hostname:
                    errors.append(
                        CollectionError(
                            code="rss_invalid_entry",
                            message="Entry canonical link has invalid scheme or host.",
                            retryable=False,
                        )
                    )
                    invalid_entry_count += 1
                    continue

                # Normalize URL using existing normalize_url
                try:
                    canonical_entry_url = normalize_url(canonical_entry_url)
                except Exception as e:
                    errors.append(
                        CollectionError(
                            code="rss_invalid_entry",
                            message=f"Failed to normalize entry URL: {e}",
                            retryable=False,
                        )
                    )
                    invalid_entry_count += 1
                    continue

                # C. External ID
                raw_id = entry.get("id") or entry.get("guid") or ""
                raw_id = raw_id.strip()
                external_id = None
                if raw_id:
                    # Sha256 hash lowercase hex
                    sha = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
                    external_id = f"feed:entry:{sha}"

                # D. Author
                author_detail = entry.get("author_detail") or {}
                raw_author = author_detail.get("name") or entry.get("author") or ""
                author = html_to_plain_text(raw_author, max_chars=255) if raw_author else None

                # E. Excerpt / Summary / Content Fallback
                excerpt_raw = entry.get("summary") or ""
                if not excerpt_raw and config.use_content_fallback:
                    content_list = entry.get("content") or []
                    for c in content_list:
                        if c.get("value"):
                            excerpt_raw = c.get("value")
                            break

                plain_excerpt = html_to_plain_text(excerpt_raw, max_chars=5000)

                # F. Published / Updated At
                published_time = None
                if "published_parsed" in entry:
                    published_time = entry["published_parsed"]
                elif "updated_parsed" in entry:
                    published_time = entry["updated_parsed"]
                published_at = struct_time_to_datetime(published_time)

                updated_time = None
                if "updated_parsed" in entry:
                    updated_time = entry["updated_parsed"]
                updated_at_dt = struct_time_to_datetime(updated_time)
                updated_at_iso = updated_at_dt.isoformat() if updated_at_dt else None

                # G. Lookback Filter
                if published_at:
                    if config.lookback_days is not None:
                        threshold = collected_at - timedelta(days=config.lookback_days)
                        if published_at < threshold:
                            skipped_old_count += 1
                            continue
                    
                    # Future date warning
                    if published_at > collected_at + timedelta(hours=24):
                        warnings.append(
                            CollectionWarning(
                                code="future_published_at",
                                message=f"Published date {published_at.isoformat()} is 24+ hours in the future.",
                                item_external_id=external_id,
                            )
                        )
                elif not config.include_undated:
                    skipped_undated_count += 1
                    continue

                # H. Categories & Tags
                entry_tags = []
                tags_list = entry.get("tags") or []
                for t in tags_list:
                    term = t.get("term")
                    if term and isinstance(term, str):
                        term_cleaned = term.strip()
                        if term_cleaned:
                            entry_tags.append(term_cleaned)

                # I. In-run Deduplication
                is_duplicate = False
                if external_id and external_id in seen_ids or canonical_entry_url in seen_urls:
                    is_duplicate = True

                if is_duplicate:
                    duplicate_entry_count += 1
                    continue

                if external_id:
                    seen_ids.add(external_id)
                seen_urls.add(canonical_entry_url)

                # J. Build RawItem
                entry_language = entry.get("language") or feed_language or None

                item = RawItem(
                    external_id=external_id,
                    url=canonical_entry_url,
                    title=plain_title,
                    excerpt=plain_excerpt,
                    author=author,
                    published_at=published_at,
                    item_type="feed_entry",
                    metadata={
                        "signal_type_hint": config.signal_type.value,
                        "feed_format": version or None,
                        "entry_id": raw_id[:2048] if raw_id else None,
                        "entry_updated_at": updated_at_iso,
                        "entry_language": entry_language,
                        "entry_tags": entry_tags,
                        "default_tags": config.default_tags,
                        "default_categories": config.default_categories,
                    },
                )
                raw_items.append(item)
                accepted_entry_count += 1

            except Exception as e:
                if isinstance(e, Warning):
                    raise e
                errors.append(
                    CollectionError(
                        code="rss_invalid_entry",
                        message=f"Failed to process entry: {e}",
                        retryable=False,
                    )
                )
                invalid_entry_count += 1

        # Check duplicate warnings
        if duplicate_entry_count > 0:
            warnings.append(
                CollectionWarning(
                    code="duplicate_entries_ignored",
                    message=f"Ignored {duplicate_entry_count} duplicate entries within this feed run.",
                )
            )

        # Apply final items limit (min of RSSSourceConfig.max_items and context.max_items)
        limit = min(config.max_items, context.max_items)
        raw_items = raw_items[:limit]

        result_metadata = {
            "collector": "rss",
            "feed_host": feed_host or None,
            "feed_format": version or None,
            "feed_title": feed_title or None,
            "feed_language": feed_language or None,
            "fetched_entry_count": fetched_entry_count,
            "scanned_entry_count": scanned_entry_count,
            "accepted_entry_count": accepted_entry_count,
            "duplicate_entry_count": duplicate_entry_count,
            "skipped_old_count": skipped_old_count,
            "skipped_undated_count": skipped_undated_count,
            "skipped_missing_title_count": skipped_missing_title_count,
            "skipped_missing_link_count": skipped_missing_link_count,
            "invalid_entry_count": invalid_entry_count,
            "bozo": bool(parsed.bozo),
        }

        return CollectionResult(
            items=raw_items,
            warnings=warnings,
            errors=errors,
            metadata=result_metadata,
        )
