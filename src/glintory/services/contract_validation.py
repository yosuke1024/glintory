import hashlib
import json
import os
import re
from typing import Any

import jsonschema

from glintory.domain.public_contract import (
    JuryPressFeedV1,
    PublicManifestV1,
    PublicOpportunityDetailV1,
    PublicOpportunityListV1,
)


def raise_json_constant_error(c: str) -> Any:
    raise ValueError(f"Forbidden JSON constant detected: {c}")


def recursive_security_check(data: Any, path: str = "") -> list[str]:
    errors = []
    forbidden_keys = {
        "token",
        "authorization",
        "password",
        "secret",
        "database_url",
        "sqlite://",
        "file://",
        "/users/",
        "/home/",
        "stack_trace",
        "raw_metadata",
        "review_note",
    }

    if isinstance(data, dict):
        for k, v in data.items():
            k_lower = k.lower()
            current_path = f"{path}.{k}" if path else k
            for f in forbidden_keys:
                if f in k_lower:
                    errors.append(
                        f"Security violation: key '{current_path}' contains forbidden term '{f}'"
                    )
            errors.extend(recursive_security_check(v, current_path))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            errors.extend(recursive_security_check(item, f"{path}[{idx}]"))
    elif isinstance(data, str):
        val_lower = data.lower()
        for f in forbidden_keys:
            if f in val_lower:
                errors.append(
                    f"Security violation: value at '{path}' contains forbidden term '{f}'"
                )
    return errors


def validate_public_contract(data_dir: str) -> list[str]:
    errors = []

    # Paths setup
    manifest_path = os.path.join(data_dir, "manifest.json")
    opp_list_path = os.path.join(data_dir, "opportunities.json")
    jurypress_path = os.path.join(data_dir, "feeds", "jurypress.json")
    opps_dir = os.path.join(data_dir, "opportunities")
    schemas_dir = os.path.join(data_dir, "schemas")

    # 1. Essential files presence check
    for path in [manifest_path, opp_list_path, jurypress_path]:
        if not os.path.exists(path):
            errors.append(f"Missing required contract file: {os.path.basename(path)}")
            return errors

    # 2. Check JSON Schema files existence
    schema_files = [
        "manifest.schema.json",
        "opportunity-list.schema.json",
        "opportunity-detail.schema.json",
        "jurypress-feed.schema.json",
    ]
    for schema_file in schema_files:
        p = os.path.join(schemas_dir, schema_file)
        if not os.path.exists(p):
            errors.append(f"Missing JSON Schema file: {schema_file}")
            return errors

    # Load schemas
    try:
        with open(os.path.join(schemas_dir, "manifest.schema.json")) as f:
            manifest_schema = json.load(f)
        with open(os.path.join(schemas_dir, "opportunity-list.schema.json")) as f:
            opp_list_schema = json.load(f)
        with open(os.path.join(schemas_dir, "opportunity-detail.schema.json")) as f:
            detail_schema = json.load(f)
        with open(os.path.join(schemas_dir, "jurypress-feed.schema.json")) as f:
            jurypress_schema = json.load(f)
    except Exception as e:
        errors.append(f"Failed to load JSON schemas: {e}")
        return errors

    # Helper function to read, parse strictly, and validate with schema + Pydantic + security scan
    def load_and_verify_json(filepath: str, pydantic_model: Any, schema: dict) -> Any:
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            # Strict UTF-8 check
            content.encode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"File {os.path.basename(filepath)} is not valid UTF-8")
            return None

        try:
            # Prevent NaN/Infinity constants
            data = json.loads(content, parse_constant=raise_json_constant_error)
        except Exception as e:
            errors.append(f"Failed to parse JSON for {os.path.basename(filepath)}: {e}")
            return None

        # Verify using JSON Schema
        try:
            jsonschema.validate(instance=data, schema=schema)
        except Exception as e:
            errors.append(
                f"JSON Schema validation failed for {os.path.basename(filepath)}: {e}"
            )

        # Verify using Pydantic (extra="forbid")
        try:
            model_instance = pydantic_model.model_validate(data)
        except Exception as e:
            errors.append(
                f"Pydantic model validation failed for {os.path.basename(filepath)}: {e}"
            )
            return None

        # Security Scan
        sec_errors = recursive_security_check(data)
        for se in sec_errors:
            errors.append(f"{os.path.basename(filepath)} {se}")

        return model_instance

    # Validate manifest.json
    manifest = load_and_verify_json(manifest_path, PublicManifestV1, manifest_schema)
    if not manifest:
        return errors

    # Validate opportunities.json
    opp_list = load_and_verify_json(
        opp_list_path, PublicOpportunityListV1, opp_list_schema
    )
    if not opp_list:
        return errors

    # Validate feeds/jurypress.json
    jurypress = load_and_verify_json(jurypress_path, JuryPressFeedV1, jurypress_schema)
    if not jurypress:
        return errors

    # Determine base_path from detail_url if available
    base_path = ""
    if opp_list.items:
        first_url = opp_list.items[0].detail_url
        if "/data/v1/opportunities/" in first_url:
            base_path = first_url.split("/data/v1/opportunities/")[0]

    # Validate detail JSONs and check links
    detail_hashes = {}
    ready_ids_from_details = set()

    for item in opp_list.items:
        # Validate public_id regex
        if not re.match(r"^opp_[0-9a-f]{32}$", item.public_id):
            errors.append(
                f"Invalid public_id format in opportunities.json: '{item.public_id}'"
            )

        # 3. Check detail JSON file existence and schema validation
        detail_path = os.path.join(opps_dir, f"{item.public_id}.json")
        if not os.path.exists(detail_path):
            errors.append(f"Missing detail JSON file for opportunity: {item.public_id}")
            continue

        detail = load_and_verify_json(
            detail_path, PublicOpportunityDetailV1, detail_schema
        )
        if not detail:
            continue

        # Match public_id
        if detail.public_id != item.public_id:
            errors.append(
                f"Opportunity ID mismatch in detail {item.public_id}: got {detail.public_id}"
            )

        # Check content hash consistency between list and detail
        if item.content_hash != detail.content_hash:
            errors.append(
                f"Content hash mismatch for {item.public_id} between list ({item.content_hash}) and detail ({detail.content_hash})"
            )

        # Excerpt length verification
        for ev in detail.evidence:
            if ev.excerpt and len(ev.excerpt) > 500:
                errors.append(
                    f"Detail {item.public_id} evidence {ev.signal_id} has excerpt exceeding 500 characters"
                )

            # Check URL format
            if not (ev.url.startswith("http://") or ev.url.startswith("https://")):
                errors.append(
                    f"Detail {item.public_id} evidence {ev.signal_id} has invalid URL format: '{ev.url}'"
                )

        # Re-evaluate readiness in validator
        ready_recalc = detail.jurypress.ready
        if ready_recalc:
            ready_ids_from_details.add(item.public_id)

        detail_hashes[item.public_id] = detail.content_hash

        # Verify detail_url presence
        expected_suffix = f"/data/v1/opportunities/{item.public_id}.json"
        if not item.detail_url.endswith(expected_suffix):
            errors.append(
                f"Invalid detail_url format for {item.public_id}: '{item.detail_url}'"
            )

        # Verify html_url index.html existence
        # base_path mapping verification
        html_suffix = item.html_url
        if base_path and html_suffix.startswith(base_path):
            html_suffix = html_suffix[len(base_path) :]
        # data_dir = dist/data/v1, so parent's parent of data_dir is dist
        dist_dir = os.path.dirname(os.path.dirname(data_dir))
        html_path = os.path.join(dist_dir, html_suffix.lstrip("/"), "index.html")
        if not os.path.exists(html_path):
            errors.append(
                f"Missing html file for opportunity {item.public_id} at expected path: '{html_path}'"
            )

    # Count validations
    if opp_list.count != len(opp_list.items):
        errors.append(
            f"opportunities.json count mismatch: count field is {opp_list.count}, but items list length is {len(opp_list.items)}"
        )

    if jurypress.count != len(jurypress.items):
        errors.append(
            f"feeds/jurypress.json count mismatch: count field is {jurypress.count}, but items list length is {len(jurypress.items)}"
        )

    if manifest.counts.published_opportunities != len(opp_list.items):
        errors.append(
            f"Manifest published_opportunities count mismatch: manifest says {manifest.counts.published_opportunities}, list has {len(opp_list.items)}"
        )

    if manifest.counts.jurypress_ready != len(jurypress.items):
        errors.append(
            f"Manifest jurypress_ready count mismatch: manifest says {manifest.counts.jurypress_ready}, feed has {len(jurypress.items)}"
        )

    # manifest.json dataset hash check
    sorted_items = sorted(opp_list.items, key=lambda x: x.public_id)
    hash_payload = [
        f"{i.public_id}:{i.revision}:{i.content_hash}" for i in sorted_items
    ]
    manifest_raw_str = ",".join(hash_payload)
    expected_manifest_hash = hashlib.sha256(
        manifest_raw_str.encode("utf-8")
    ).hexdigest()

    if manifest.content_hash != expected_manifest_hash:
        errors.append(
            f"Manifest dataset content_hash mismatch. Expected {expected_manifest_hash}, got {manifest.content_hash}"
        )

    if jurypress.content_hash != manifest.content_hash:
        errors.append(
            f"feeds/jurypress.json content_hash mismatch: feed has {jurypress.content_hash}, manifest has {manifest.content_hash}"
        )

    # 4. Ready items list matching (Strict checking of JuryPress Feed set integration)
    ready_ids_from_list = {
        item.public_id for item in opp_list.items if item.jurypress.ready
    }
    ready_ids_from_feed = {item.public_id for item in jurypress.items}

    if ready_ids_from_list != ready_ids_from_feed:
        diff_list = ready_ids_from_list - ready_ids_from_feed
        diff_feed = ready_ids_from_feed - ready_ids_from_list
        errors.append(
            f"JuryPress Feed items do not match ready opportunities list. "
            f"Ready in opportunities.json but missing from feeds/jurypress.json: {list(diff_list)}. "
            f"Present in feeds/jurypress.json but not ready/active in opportunities.json: {list(diff_feed)}."
        )

    # Verify JuryPress feed item hashes and duplicate IDs
    seen_feed_ids = set()
    for feed_item in jurypress.items:
        if feed_item.public_id in seen_feed_ids:
            errors.append(
                f"Duplicate opportunity in feeds/jurypress.json: {feed_item.public_id}"
            )
        seen_feed_ids.add(feed_item.public_id)

        if feed_item.public_id not in detail_hashes:
            errors.append(
                f"JuryPress feed contains unknown opportunity: {feed_item.public_id}"
            )
            continue

        if feed_item.content_hash != detail_hashes[feed_item.public_id]:
            errors.append(
                f"JuryPress content hash mismatch for {feed_item.public_id}. Feed: {feed_item.content_hash}, Detail: {detail_hashes[feed_item.public_id]}"
            )

    return errors


def inspect_jurypress_feed(data_dir: str) -> dict[str, Any]:
    opp_list_path = os.path.join(data_dir, "opportunities.json")
    jurypress_path = os.path.join(data_dir, "feeds", "jurypress.json")

    result = {"ready": [], "excluded": []}

    if not os.path.exists(opp_list_path) or not os.path.exists(jurypress_path):
        return result

    try:
        with open(opp_list_path) as f:
            opp_list_data = json.load(f)
        opp_list = PublicOpportunityListV1.model_validate(opp_list_data)
    except Exception:
        return result

    for item in opp_list.items:
        title = (
            item.localization.ja.title
            if item.localization.ja and item.localization.ja.title
            else item.public_id
        )
        if item.jurypress.ready:
            result["ready"].append(
                {
                    "public_id": item.public_id,
                    "title": title,
                    "score": item.score.total,
                    "confidence": item.score.confidence,
                }
            )
        else:
            result["excluded"].append(
                {
                    "public_id": item.public_id,
                    "title": title,
                    "score": item.score.total,
                    "confidence": item.score.confidence,
                    "reasons": item.jurypress.reasons,
                }
            )

    return result
