from collections.abc import Sequence

from glintory.domain.enums import SignalRole, SignalType


def _classify_github_issue(
    title: str,
    excerpt: str | None,
    labels: Sequence[str],
) -> SignalType:
    labels_lower = {label.lower() for label in labels}

    # 1. Complaint labels check
    complaint_labels = {"bug", "regression", "broken", "defect"}
    if any(label in complaint_labels for label in labels_lower):
        return SignalType.COMPLAINT

    # 2. Request labels check
    request_labels = {
        "feature",
        "feature request",
        "enhancement",
        "proposal",
        "request",
    }
    if any(label in request_labels for label in labels_lower):
        return SignalType.REQUEST

    # 3. Pain phrases check
    pain_phrases = [
        "too expensive",
        "hard to use",
        "hard to configure",
        "too complex",
        "manual process",
        "privacy concern",
        "missing support",
        "looking for an alternative",
        "self-hosted alternative",
        "doesn't work",
        "does not work",
        "frustrating",
    ]

    search_text = title.lower()
    if excerpt:
        search_text += " " + excerpt.lower()

    if any(phrase in search_text for phrase in pain_phrases):
        return SignalType.PAIN

    return SignalType.REQUEST


def _classify_hn_ask(title: str, excerpt: str | None) -> SignalType:
    pain_phrases = [
        "too expensive",
        "hard to use",
        "hard to configure",
        "too complex",
        "manual process",
        "privacy concern",
        "missing support",
        "looking for an alternative",
        "self-hosted alternative",
        "doesn't work",
        "does not work",
        "frustrating",
    ]
    search_text = title.lower()
    if excerpt:
        search_text += " " + excerpt.lower()

    if any(phrase in search_text for phrase in pain_phrases):
        return SignalType.PAIN
    return SignalType.REQUEST


def classify_signal(
    item_type: str | None,
    title: str,
    excerpt: str | None,
    labels: Sequence[str],
) -> SignalType:
    if not item_type:
        raise ValueError("unsupported_item_type")

    item_type_lower = item_type.lower()

    if item_type_lower == "repository":
        return SignalType.PROJECT

    if item_type_lower == "issue":
        return _classify_github_issue(title, excerpt, labels)

    if item_type_lower == "hn_ask":
        return _classify_hn_ask(title, excerpt)

    if item_type_lower == "hn_show":
        return SignalType.LAUNCH

    if item_type_lower == "hn_story":
        return SignalType.TREND

    if item_type_lower == "hn_job":
        return SignalType.JOB_DEMAND

    raise ValueError("unsupported_item_type")


def classify_signal_role(
    source_type: str,
    signal_type: SignalType,
    title: str,
    excerpt: str | None,
) -> SignalRole:
    source_type_lower = (source_type or "").lower()
    title_lower = (title or "").lower()

    if source_type_lower == "hackernews":
        if title_lower.startswith("show hn:") or signal_type == SignalType.LAUNCH:
            return SignalRole.SUPPLY
        if (
            title_lower.startswith("ask hn:")
            or signal_type == SignalType.REQUEST
            or "ask hn" in title_lower
        ):
            return SignalRole.DEMAND
    elif source_type_lower == "github":
        if signal_type == SignalType.PROJECT:
            return SignalRole.SUPPLY
        if signal_type in (SignalType.REQUEST, SignalType.PAIN, SignalType.COMPLAINT):
            return SignalRole.DEMAND

    if signal_type == SignalType.TREND:
        return SignalRole.CONTEXT

    if source_type_lower == "rss":
        if signal_type in (SignalType.REQUEST, SignalType.PAIN, SignalType.COMPLAINT):
            return SignalRole.DEMAND
        if signal_type in (
            sa_type
            for sa_type in (
                SignalType.PROJECT,
                SignalType.LAUNCH,
                SignalType.HACKATHON_PROJECT,
            )
        ):
            return SignalRole.SUPPLY
        if signal_type == SignalType.TREND:
            return SignalRole.CONTEXT

    return SignalRole.UNKNOWN


def _classify_rss_entry(
    title: str,
    excerpt: str | None,
    default_hint: SignalType,
) -> SignalType:
    import re
    search_text = title.lower()
    if excerpt:
        search_text += " " + excerpt.lower()

    def has_word(pattern: str, text: str) -> bool:
        if pattern.replace(" ", "").isalnum() and pattern.isascii():
            return bool(re.search(rf"\b{re.escape(pattern)}\b", text))
        return pattern in text

    pain_phrases = [
        "too expensive", "hard to use", "hard to configure", "too complex",
        "manual process", "privacy concern", "missing support", "looking for an alternative",
        "self-hosted alternative", "doesn't work", "does not work", "frustrating"
    ]
    launch_phrases = [
        "announcing", "introducing", "released", "launching", "created a",
        "showcase", "my project", "my tool"
    ]

    if any(has_word(phrase, search_text) for phrase in pain_phrases):
        return SignalType.PAIN
    if any(has_word(phrase, search_text) for phrase in launch_phrases):
        return SignalType.LAUNCH

    return default_hint
