import pytest

from glintory.domain.enums import SignalType
from glintory.services.signal_classification import classify_signal


def test_classify_repository():
    assert (
        classify_signal("repository", "some-repo", "some excerpt", [])
        == SignalType.PROJECT
    )


def test_classify_issue_complaint():
    # Bug label triggers COMPLAINT
    assert (
        classify_signal("issue", "Some title", "Some excerpt", ["BUG"])
        == SignalType.COMPLAINT
    )
    assert (
        classify_signal("issue", "Some title", "Some excerpt", ["regression"])
        == SignalType.COMPLAINT
    )
    assert (
        classify_signal("issue", "Some title", "Some excerpt", ["broken", "other"])
        == SignalType.COMPLAINT
    )


def test_classify_issue_request():
    # Feature label triggers REQUEST
    assert (
        classify_signal("issue", "Some title", "Some excerpt", ["feature"])
        == SignalType.REQUEST
    )
    assert (
        classify_signal("issue", "Some title", "Some excerpt", ["Enhancement"])
        == SignalType.REQUEST
    )
    # Feature wins over Pain phrase because label takes precedence over text
    assert (
        classify_signal("issue", "It doesn't work", "too expensive", ["proposal"])
        == SignalType.REQUEST
    )


def test_classify_issue_pain():
    # Pain phrases trigger PAIN
    assert (
        classify_signal("issue", "This is too expensive", "Some excerpt", [])
        == SignalType.PAIN
    )
    assert (
        classify_signal("issue", "Help", "It is hard to configure", [])
        == SignalType.PAIN
    )
    assert (
        classify_signal("issue", "It doesn't work", "Some excerpt", [])
        == SignalType.PAIN
    )


def test_classify_issue_default():
    assert (
        classify_signal("issue", "General question", "Some excerpt", [])
        == SignalType.REQUEST
    )


def test_classify_unknown_type():
    with pytest.raises(ValueError) as exc_info:
        classify_signal("unknown_item", "Title", "Excerpt", [])
    assert str(exc_info.value) == "unsupported_item_type"


def test_classify_unsupported_types():
    # pull_request is unsupported
    with pytest.raises(ValueError) as exc_info:
        classify_signal("pull_request", "Title", "Excerpt", [])
    assert str(exc_info.value) == "unsupported_item_type"

    # discussion is unsupported
    with pytest.raises(ValueError) as exc_info:
        classify_signal("discussion", "Title", "Excerpt", [])
    assert str(exc_info.value) == "unsupported_item_type"


def test_classify_no_url_inference():
    # item_type is issue, classify_signal does not accept URL.
    # It must evaluate strictly using the labels and content
    assert (
        classify_signal("issue", "Title indicating issue", "Excerpt", [])
        == SignalType.REQUEST
    )

    # Pain phrase still triggers pain
    assert (
        classify_signal("issue", "This is too expensive", "Excerpt", [])
        == SignalType.PAIN
    )
