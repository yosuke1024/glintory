# ruff: noqa: PLR0911
import json
import math
from datetime import datetime
from enum import Enum
from typing import Any

from glintory.config import settings


class SignalMetadataTooLargeError(ValueError):
    pass


def _sanitize_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):  # Note: bool is a subclass of int, so check it first
        return val
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Enum):
        return _sanitize_value(val.value)
    if isinstance(val, (list, tuple)):
        return [_sanitize_value(item) for item in val]
    if isinstance(val, dict):
        sanitized_dict = {}
        for k, v in val.items():
            key_str = str(k.value) if isinstance(k, Enum) else str(k)
            sanitized_dict[key_str] = _sanitize_value(v)
        return sanitized_dict

    # Reject other types (bytes, custom classes, etc.)
    raise ValueError(f"Unsupported type for metadata: {type(val).__name__}")


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    # First recursive pass to sanitize and convert types
    sanitized = _sanitize_value(metadata)
    if not isinstance(sanitized, dict):
        raise ValueError("Metadata must be a dictionary")

    # Serialize to calculate UTF-8 byte size
    try:
        serialized = json.dumps(sanitized, separators=(",", ":"))
        byte_size = len(serialized.encode("utf-8"))
    except Exception as e:
        raise ValueError("Metadata could not be serialized to JSON") from e

    max_bytes = settings.signal_metadata_max_bytes
    if byte_size > max_bytes:
        raise SignalMetadataTooLargeError(
            "Metadata byte size exceeds the maximum allowed limit"
        )

    return sanitized
