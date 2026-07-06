import json
import pathlib
from typing import Any


class ConfigLoadError(Exception):
    pass


def load_json_object(path_str: str) -> dict[str, Any]:
    path = pathlib.Path(path_str)
    if not path.exists():
        raise ConfigLoadError(f"Configuration file does not exist: {path_str}")
    if path.is_dir():
        raise ConfigLoadError(f"Specified path is a directory, not a file: {path_str}")

    try:
        size = path.stat().st_size
    except Exception as e:
        raise ConfigLoadError(f"Failed to get file stats: {e}") from e

    if size > 65536:
        raise ConfigLoadError(
            f"Configuration file size exceeds the 64KiB limit ({size} bytes)."
        )

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ConfigLoadError(
            f"Failed to decode configuration file as UTF-8: {e}"
        ) from e
    except Exception as e:
        raise ConfigLoadError(f"Failed to read file: {e}") from e

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ConfigLoadError(
            f"Invalid JSON format. Parse error at line {e.lineno}, column {e.colno}: {e.msg}"
        ) from e

    if not isinstance(data, dict):
        raise ConfigLoadError("Configuration root must be a JSON object.")

    return data
