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
    from glintory.services.signal_facets import extract_signal_facets
    facets = extract_signal_facets(title, excerpt, source_type, signal_type)
    return facets["signal_role"]


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
        "self-hosted alternative", "frustrating", "wish there was", "self-hosted version",
        "offline support", "currently using a spreadsheet", "doing this manually",
        "would pay for", "missing support for", "looking for alternative",
        "ユーザー", "顧客", "開発者", "使いづらい", "複雑", "代替", "手動", "スプレッドシート", "エクセル"
    ]
    migration_phrases = [
        "migrate away", "migration", "import from", "export to", "transition", "move from",
        "移行", "乗り換え"
    ]
    complaint_phrases = [
        "broken", "fail", "error", "bug", "doesn't work", "does not work", "defect",
        "バグ", "エラー", "不具合", "壊れ"
    ]
    launch_phrases = [
        "announcing", "introducing", "released", "launching", "created a", "showcase",
        "my project", "my tool", "github.com", "open source", "repository"
    ]

    if any(has_word(phrase, search_text) for phrase in pain_phrases):
        return SignalType.PAIN
    if any(has_word(phrase, search_text) for phrase in migration_phrases):
        return SignalType.MIGRATION
    if any(has_word(phrase, search_text) for phrase in complaint_phrases):
        return SignalType.COMPLAINT
    if any(has_word(phrase, search_text) for phrase in launch_phrases):
        return SignalType.LAUNCH

    return default_hint
