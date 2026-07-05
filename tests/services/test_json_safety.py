from datetime import UTC, datetime
from enum import Enum

import pytest

from glintory.config import settings
from glintory.services.json_safety import (
    SignalMetadataTooLargeError,
    sanitize_metadata,
)


class DummyEnum(Enum):
    VAL1 = "value1"
    VAL2 = 2


def test_sanitize_metadata_success():
    dt = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    data = {
        "str": "hello",
        "int": 42,
        "float": 3.14,
        "bool": True,
        "none": None,
        "list": [1, 2, 3],
        "tuple": (4, 5),
        "dict": {"a": "b"},
        "datetime": dt,
        "enum": DummyEnum.VAL1,
        "enum_int": DummyEnum.VAL2,
    }

    sanitized = sanitize_metadata(data)

    assert sanitized["str"] == "hello"
    assert sanitized["int"] == 42
    assert sanitized["float"] == 3.14
    assert sanitized["bool"] is True
    assert sanitized["none"] is None
    assert sanitized["list"] == [1, 2, 3]
    assert sanitized["tuple"] == [4, 5]  # tuple converted to list
    assert sanitized["dict"] == {"a": "b"}
    assert sanitized["datetime"] == "2026-07-05T12:00:00+00:00"
    assert sanitized["enum"] == "value1"
    assert sanitized["enum_int"] == 2


def test_sanitize_metadata_dict_key_stringification():
    data = {
        1: "int_key",
        DummyEnum.VAL1: "enum_key",
    }
    sanitized = sanitize_metadata(data)
    assert sanitized["1"] == "int_key"
    assert sanitized["value1"] == "enum_key"


def test_sanitize_metadata_invalid_types():
    # custom objects or bytes should be rejected
    with pytest.raises(ValueError):
        sanitize_metadata({"bytes": b"hello"})

    class CustomObj:
        pass

    with pytest.raises(ValueError):
        sanitize_metadata({"obj": CustomObj()})


def test_sanitize_metadata_floats():
    data = {
        "nan": float("nan"),
        "inf": float("inf"),
        "neginf": float("-inf"),
    }
    sanitized = sanitize_metadata(data)
    # NaN, Infinity, -Infinity converted to None
    assert sanitized["nan"] is None
    assert sanitized["inf"] is None
    assert sanitized["neginf"] is None


def test_sanitize_metadata_too_large(monkeypatch):
    monkeypatch.setattr(settings, "signal_metadata_max_bytes", 20)
    data = {"large": "a" * 100}
    with pytest.raises(SignalMetadataTooLargeError) as exc_info:
        sanitize_metadata(data)
    # The error message must not contain the actual metadata
    assert "large" not in str(exc_info.value)
