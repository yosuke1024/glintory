import pytest

from glintory.domain.enums import SignalType
from glintory.services.signal_classification import classify_signal


def test_hn_classification():
    # hn_ask + pain phrase
    assert (
        classify_signal(
            "hn_ask",
            "Ask HN: is this tool too expensive?",
            "I'm looking for an alternative.",
            [],
        )
        == SignalType.PAIN
    )
    # hn_ask normal
    assert (
        classify_signal(
            "hn_ask", "Ask HN: What is your favorite editor?", "Just curious.", []
        )
        == SignalType.REQUEST
    )

    # hn_show
    assert (
        classify_signal(
            "hn_show", "Show HN: My New App", "I built this over the weekend.", []
        )
        == SignalType.LAUNCH
    )

    # hn_story
    assert (
        classify_signal("hn_story", "Breaking News in Tech", None, [])
        == SignalType.TREND
    )

    # hn_job
    assert (
        classify_signal("hn_job", "We are hiring!", None, []) == SignalType.JOB_DEMAND
    )

    # Invalid HN item type
    with pytest.raises(ValueError, match="unsupported_item_type"):
        classify_signal("hn_unknown", "Title", None, [])


def test_existing_github_classification_not_broken():
    # Verify GitHub classification still works
    assert classify_signal("repository", "A cool repo", None, []) == SignalType.PROJECT
    assert (
        classify_signal("issue", "bug in main", None, ["bug"]) == SignalType.COMPLAINT
    )
    assert (
        classify_signal("issue", "I need a feature", None, ["feature"])
        == SignalType.REQUEST
    )
    assert (
        classify_signal("issue", "This is too complex to configure", None, [])
        == SignalType.PAIN
    )
