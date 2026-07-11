import json
import os
from typing import Any

from sqlalchemy.orm import Session

from glintory.collectors.registry import CollectorNotFoundError, CollectorRegistry
from glintory.domain.models import Source, SourceSchedule


def check_secret_keys_recursive(d: Any, path: str = "") -> None:
    if isinstance(d, dict):
        for k, v in d.items():
            k_lower = k.lower()
            for forbidden in [
                "token",
                "secret",
                "password",
                "authorization",
                "cookie",
                "api_key",
                "apikey",
                "private_key",
                "credential",
            ]:
                if forbidden in k_lower:
                    raise ValueError(f"Secret-like key '{k}' found at config '{path}'")
            check_secret_keys_recursive(v, f"{path}.{k}" if path else k)
    elif isinstance(d, list):
        for idx, item in enumerate(d):
            check_secret_keys_recursive(item, f"{path}[{idx}]")


def sync_manifest_file(
    session: Session, registry: CollectorRegistry, manifest_path: str
) -> dict:
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    manifest_dir = os.path.dirname(os.path.abspath(manifest_path))

    with open(manifest_path) as f:
        try:
            manifest_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in manifest file: {e}")

    # Validate schema
    if (
        not isinstance(manifest_data, dict)
        or "version" not in manifest_data
        or "sources" not in manifest_data
    ):
        raise ValueError(
            "Invalid manifest structure: must contain 'version' and 'sources' keys."
        )

    if manifest_data["version"] != 1:
        raise ValueError(f"Unsupported manifest version: {manifest_data['version']}")

    sources_to_sync = manifest_data["sources"]
    if not isinstance(sources_to_sync, list):
        raise ValueError("'sources' key in manifest must be a list.")

    validated_sources = []

    # 1. Validation phase (Read configs, validate secret-like keys, validate via collector)
    for src_def in sources_to_sync:
        if not isinstance(src_def, dict):
            raise ValueError("Each source definition must be an object.")

        name = src_def.get("name")
        source_type = src_def.get("source_type")
        enabled = src_def.get("enabled", True)
        config_file_rel = src_def.get("config_file")
        schedule_def = src_def.get("schedule", {})

        if not name or not source_type or not config_file_rel:
            raise ValueError(
                "Source definition must contain 'name', 'source_type', and 'config_file'."
            )

        # Resolve config file path
        config_file_path = os.path.abspath(os.path.join(manifest_dir, config_file_rel))
        if not os.path.exists(config_file_path):
            raise FileNotFoundError(f"Config file not found: {config_file_rel}")

        with open(config_file_path) as f:
            try:
                config_data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Invalid JSON in config file '{config_file_rel}': {e}"
                )

        # Check secret-like keys in config
        check_secret_keys_recursive(config_data)

        # Validate via collector
        try:
            collector = registry.get(source_type)
        except CollectorNotFoundError:
            raise ValueError(f"Collector not found for source type: {source_type}")

        try:
            validated_config = collector.validate_config(config_data)
        except Exception as e:
            raise ValueError(
                f"Config validation failed for source '{name}' via collector '{source_type}': {e}"
            )

        # Schedule validation
        schedule_enabled = schedule_def.get("enabled", True)
        interval_minutes = schedule_def.get("interval_minutes")
        if interval_minutes is None:
            raise ValueError(
                f"Schedule interval_minutes is required for source '{name}'"
            )
        if not isinstance(interval_minutes, int) or interval_minutes <= 0:
            raise ValueError(
                f"Schedule interval_minutes must be a positive integer for source '{name}'"
            )

        validated_sources.append(
            {
                "name": name,
                "source_type": source_type,
                "enabled": enabled,
                "config": validated_config,
                "schedule_enabled": schedule_enabled,
                "interval_minutes": interval_minutes,
            }
        )

    # 2. Database Upsert phase (All or nothing)
    created_count = 0
    updated_count = 0

    try:
        for v_src in validated_sources:
            # Check existing by name
            existing_source = (
                session.query(Source).filter_by(name=v_src["name"]).first()
            )

            if existing_source:
                if existing_source.source_type != v_src["source_type"]:
                    raise ValueError(
                        f"Cannot change source type for source '{v_src['name']}' "
                        f"from '{existing_source.source_type}' to '{v_src['source_type']}'."
                    )
                existing_source.config = v_src["config"]
                existing_source.enabled = v_src["enabled"]
                source_id = existing_source.id
                updated_count += 1
            else:
                import uuid

                source_id = str(uuid.uuid4())
                new_src = Source(
                    id=source_id,
                    name=v_src["name"],
                    source_type=v_src["source_type"],
                    config=v_src["config"],
                    enabled=v_src["enabled"],
                )
                session.add(new_src)
                created_count += 1

            # Upsert Schedule
            existing_schedule = (
                session.query(SourceSchedule).filter_by(source_id=source_id).first()
            )
            if existing_schedule:
                existing_schedule.interval_minutes = v_src["interval_minutes"]
                existing_schedule.enabled = v_src["schedule_enabled"]
            else:
                from datetime import UTC, datetime

                new_sched = SourceSchedule(
                    source_id=source_id,
                    interval_minutes=v_src["interval_minutes"],
                    enabled=v_src["schedule_enabled"],
                    next_run_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(new_sched)

        session.commit()
    except Exception:
        session.rollback()
        raise

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "total_sources": len(validated_sources),
    }
