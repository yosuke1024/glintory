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


import re
from collections.abc import Mapping

# Redact patterns for string values
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.\~=]+")
AUTH_HEADER_PATTERN = re.compile(r"(?i)authorization\s*:\s*[^\s]+")
QUERY_SECRET_PATTERN = re.compile(
    r"(?i)(api_key|token|auth|password|secret|key)=[^&\s\?]+"
)
DB_URL_PATTERN = re.compile(
    r"(?i)(sqlite|postgresql|mysql|mssql|mongodb|redis|amqp|odbc):\/\/[^\s]+"
)


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    if "db_url" in k or "database_url" in k:
        return True
    return (
        any(
            s in k
            for s in (
                "token",
                "auth",
                "cookie",
                "password",
                "secret",
                "config",
                "database",
                "sql",
                "body",
                "xml",
                "trace",
                "stack",
                "excerpt",
                "issue",
            )
        )
        or "api_key" in k
        or "secret_key" in k
        or k == "key"
    )


def _sanitize_run_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, str):
        # Mask secrets and sensitive strings
        val = BEARER_PATTERN.sub("Bearer [MASKED]", val)
        val = AUTH_HEADER_PATTERN.sub("Authorization: [MASKED]", val)
        val = QUERY_SECRET_PATTERN.sub(r"\1=[MASKED]", val)
        val = DB_URL_PATTERN.sub(r"\1://[MASKED]", val)

        # Check if value itself looks like SQL or has SQL keywords
        if (
            "select " in val.lower()
            or "insert " in val.lower()
            or "update " in val.lower()
        ):
            return "[SQL MASKED]"

        return val
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Enum):
        return _sanitize_run_value(val.value)
    if isinstance(val, (list, tuple)):
        return [_sanitize_run_value(item) for item in val]
    if isinstance(val, dict):
        sanitized_dict = {}
        for k, v in val.items():
            key_str = str(k.value) if isinstance(k, Enum) else str(k)
            if _is_sensitive_key(key_str):
                sanitized_dict[key_str] = "[REDACTED]"
            else:
                sanitized_dict[key_str] = _sanitize_run_value(v)
        return sanitized_dict
    return None  # Ignore arbitrary objects, bytes, etc.


def sanitize_run_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    if not metadata:
        return {}, False

    sanitized = _sanitize_run_value(dict(metadata))
    if not isinstance(sanitized, dict):
        return {}, False

    # Check UTF-8 byte size
    try:
        serialized = json.dumps(sanitized, separators=(",", ":"))
        byte_size = len(serialized.encode("utf-8"))
    except Exception:
        # If serialization fails, return empty
        return {}, False

    # 64KiB = 65536 bytes
    if byte_size > 65536:
        # Simplify metadata
        truncated = {
            "warning": "Metadata exceeded 64KiB limit and was truncated.",
            "original_keys": list(sanitized.keys()),
        }
        return truncated, True

    return sanitized, False
