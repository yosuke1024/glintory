from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from glintory.collectors.base import RawItem
from glintory.config import settings
from glintory.domain.enums import SignalType
from glintory.domain.signals import (
    NormalizedSignal,
    SignalNormalizationError,
    SignalNormalizationResult,
    SignalNormalizationWarning,
)
from glintory.services.content_hashing import generate_content_hash
from glintory.services.json_safety import (
    SignalMetadataTooLargeError,
    sanitize_metadata,
)
from glintory.services.signal_classification import (
    classify_signal,
    classify_signal_role,
)
from glintory.services.text_normalization import (
    normalize_excerpt,
    normalize_optional_text,
    normalize_string_list,
    normalize_title,
)
from glintory.services.url_normalization import (
    InvalidSignalUrlError,
    SignalUrlTooLongError,
    normalize_url,
)


def calculate_freshness_score(
    published_at: datetime | None, collected_at: datetime
) -> float:
    if published_at is None:
        return 0.50

    if (
        published_at.tzinfo is None
        or published_at.tzinfo.utcoffset(published_at) is None
    ):
        raise ValueError("Naive datetime is not allowed for published_at")

    if published_at > collected_at:
        return 1.00

    diff = collected_at - published_at
    days = diff.total_seconds() / 86400.0

    if days <= 7.0:
        return 1.00

    thresholds = [(30.0, 0.85), (90.0, 0.65), (365.0, 0.40)]
    for limit, score in thresholds:
        if days <= limit:
            return score
    return 0.20


class SignalNormalizer:
    def normalize_batch(
        self,
        *,
        source_id: str,
        source_type: str,
        collection_run_id: str,
        items: Sequence[RawItem],
        collected_at: datetime,
    ) -> SignalNormalizationResult:
        signals: list[NormalizedSignal] = []
        warnings: list[SignalNormalizationWarning] = []
        errors: list[SignalNormalizationError] = []

        if (
            collected_at.tzinfo is None
            or collected_at.tzinfo.utcoffset(collected_at) is None
        ):
            collected_at = collected_at.replace(tzinfo=UTC)

        for item in items:
            ext_id = item.external_id
            try:
                # 1. URL Normalization
                canonical_url = normalize_url(item.url)

                # 2. Text Normalization
                title = normalize_title(item.title)
                excerpt = normalize_excerpt(item.excerpt)

                author = normalize_optional_text(item.author)
                if author:
                    author = author[:255]

                pub_at = item.published_at
                if pub_at is not None:
                    if pub_at.tzinfo is None or pub_at.tzinfo.utcoffset(pub_at) is None:
                        raise ValueError(
                            "Naive datetime is not allowed for published_at"
                        )
                    # Warning for future datetime (24+ hours ahead)
                    if pub_at > collected_at + timedelta(hours=24):
                        warnings.append(
                            SignalNormalizationWarning(
                                code="future_published_at",
                                message=f"Published date {pub_at.isoformat()} is 24+ hours in the future.",
                                external_id=ext_id,
                            )
                        )

                freshness_score = calculate_freshness_score(pub_at, collected_at)

                item_type = item.item_type or ""
                item_type_lower = item_type.lower()

                categories = ()
                tags_raw: Sequence[str] = ()
                metrics_raw = item.metadata
                filtered_metrics = {}
                raw_metadata_to_sanitize = dict(item.metadata)
                signal_type = None
                language = None

                if source_type == "rss":
                    if item_type_lower != "feed_entry":
                        raise ValueError("unsupported_item_type")

                    hint_val = item.metadata.get("signal_type_hint")
                    if not hint_val:
                        raise ValueError("missing_signal_type_hint")

                    try:
                        default_hint = SignalType(hint_val)
                    except ValueError as e:
                        raise ValueError("invalid_signal_type_hint") from e

                    if default_hint == SignalType.MANUAL:
                        raise ValueError("manual_signal_type_not_allowed")

                    from glintory.services.signal_classification import (
                        _classify_rss_entry,
                    )

                    signal_type = _classify_rss_entry(
                        item.title, item.excerpt, default_hint
                    )

                    default_categories = item.metadata.get("default_categories") or ()
                    default_tags = item.metadata.get("default_tags") or ()
                    entry_tags = item.metadata.get("entry_tags") or ()

                    categories, cat_warnings = normalize_string_list(default_categories)
                    all_tags = list(default_tags) + list(entry_tags)
                    tags_raw = all_tags
                    for tw in cat_warnings:
                        warnings.append(
                            SignalNormalizationWarning(
                                code="invalid_tag",
                                message=tw,
                                external_id=ext_id,
                            )
                        )

                    whitelist = {
                        "signal_type_hint",
                        "feed_format",
                        "entry_id",
                        "entry_updated_at",
                        "entry_language",
                        "entry_tags",
                        "default_tags",
                        "default_categories",
                    }
                    raw_metadata_to_sanitize = {
                        k: v for k, v in item.metadata.items() if k in whitelist
                    }
                    language = item.metadata.get("entry_language") or None

                else:
                    if item_type_lower == "repository":
                        tags_raw = item.metadata.get("topics") or ()
                        for k in (
                            "stargazers_count",
                            "watchers_count",
                            "forks_count",
                            "open_issues_count",
                            "score",
                        ):
                            if k in metrics_raw:
                                filtered_metrics[k] = metrics_raw[k]
                        raw_metadata_to_sanitize.pop("topics", None)

                    elif item_type_lower == "issue":
                        tags_raw = item.metadata.get("labels") or ()
                        for k in ("comments", "reactions_total_count", "score"):
                            if k in metrics_raw:
                                filtered_metrics[k] = metrics_raw[k]
                        raw_metadata_to_sanitize.pop("labels", None)

                    elif item_type_lower in ("hn_ask", "hn_show", "hn_story", "hn_job"):
                        categories = ("hacker-news",)
                        if item_type_lower == "hn_ask":
                            tags_raw = ("ask-hn",)
                        elif item_type_lower == "hn_show":
                            tags_raw = ("show-hn",)
                        elif item_type_lower == "hn_job":
                            tags_raw = ("hn-job",)
                        else:
                            tags_raw = ()

                        for k in ("score", "descendants", "kids_count"):
                            if k in metrics_raw:
                                filtered_metrics[k] = metrics_raw[k]

                    raw_labels = item.metadata.get("labels") or ()
                    signal_type = classify_signal(
                        item_type, item.title, item.excerpt, raw_labels
                    )

                signal_role = classify_signal_role(
                    source_type, signal_type, item.title, item.excerpt, item.url
                )

                tags, tag_warnings = normalize_string_list(tags_raw)
                for tw in tag_warnings:
                    warnings.append(
                        SignalNormalizationWarning(
                            code="invalid_tag",
                            message=tw,
                            external_id=ext_id,
                        )
                    )

                sanitized_metadata = sanitize_metadata(raw_metadata_to_sanitize)

                content_hash = generate_content_hash(
                    hash_version=settings.signal_hash_version,
                    source_type=source_type,
                    item_type=item_type,
                    canonical_url=canonical_url,
                    title=title,
                    excerpt=excerpt,
                    author=author,
                    published_at=pub_at,
                    metadata=sanitized_metadata,
                )

                doc_kind = item.document_kind or "STANDALONE_DEMAND"
                opp_anchor = item.opportunity_anchor
                if opp_anchor is None:
                    from glintory.domain.enums import SignalRole as SR, SignalType as ST
                    opp_anchor = (signal_role == SR.DEMAND and signal_type in (ST.PAIN, ST.MANUAL))
                disc_eligible = item.discovery_eligible if item.discovery_eligible is not None else True
                src_spec = item.source_specificity or "unknown"

                normalized_signal = NormalizedSignal(
                    source_id=source_id,
                    collection_run_id=collection_run_id,
                    external_id=ext_id,
                    canonical_url=canonical_url,
                    title=title,
                    excerpt=excerpt,
                    author=author,
                    published_at=pub_at,
                    collected_at=collected_at,
                    language=language,
                    signal_type=signal_type,
                    signal_role=signal_role,
                    categories=categories,
                    tags=tags,
                    metrics=filtered_metrics,
                    raw_metadata=sanitized_metadata,
                    content_hash=content_hash,
                    freshness_score=freshness_score,
                    source_quality_score=settings.signal_default_source_quality_score,
                    document_kind=doc_kind,
                    opportunity_anchor=opp_anchor,
                    discovery_eligible=disc_eligible,
                    source_specificity=src_spec,
                )

                signals.append(normalized_signal)

            except (InvalidSignalUrlError, SignalUrlTooLongError) as e:
                errors.append(
                    SignalNormalizationError(
                        code="invalid_url",
                        message=str(e),
                        external_id=ext_id,
                    )
                )
            except SignalMetadataTooLargeError as e:
                errors.append(
                    SignalNormalizationError(
                        code="metadata_too_large",
                        message=str(e),
                        external_id=ext_id,
                    )
                )
            except ValueError as e:
                msg = str(e)
                code = "normalization_error"
                if msg == "unsupported_item_type":
                    code = "unsupported_item_type"
                elif "naive datetime" in msg.lower():
                    code = "naive_datetime_not_allowed"
                elif "title cannot be empty" in msg.lower():
                    code = "empty_title"

                errors.append(
                    SignalNormalizationError(
                        code=code,
                        message=msg,
                        external_id=ext_id,
                    )
                )
            except Exception as e:
                errors.append(
                    SignalNormalizationError(
                        code="normalization_error",
                        message=str(e),
                        external_id=ext_id,
                    )
                )

        return SignalNormalizationResult(
            signals=signals,
            warnings=warnings,
            errors=errors,
        )
