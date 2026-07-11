import hashlib
import json
import os
from typing import Any

from glintory.domain.public_contract import (
    JuryPressFeedV1,
    PublicManifestV1,
    PublicOpportunityDetailV1,
    PublicOpportunityListV1,
)


def validate_public_contract(data_dir: str) -> list[str]:
    errors = []

    # Paths setup
    manifest_path = os.path.join(data_dir, "manifest.json")
    opp_list_path = os.path.join(data_dir, "opportunities.json")
    jurypress_path = os.path.join(data_dir, "feeds", "jurypress.json")
    opps_dir = os.path.join(data_dir, "opportunities")

    # Essential files presence check
    for path in [manifest_path, opp_list_path, jurypress_path]:
        if not os.path.exists(path):
            errors.append(f"Missing required contract file: {os.path.basename(path)}")
            return errors

    # Validate manifest.json
    try:
        with open(manifest_path) as f:
            manifest_data = json.load(f)
        manifest = PublicManifestV1.model_validate(manifest_data)
    except Exception as e:
        errors.append(f"manifest.json validation failed: {e}")
        return errors

    # Validate opportunities.json
    try:
        with open(opp_list_path) as f:
            opp_list_data = json.load(f)
        opp_list = PublicOpportunityListV1.model_validate(opp_list_data)
    except Exception as e:
        errors.append(f"opportunities.json validation failed: {e}")
        return errors

    # Validate feeds/jurypress.json
    try:
        with open(jurypress_path) as f:
            jurypress_data = json.load(f)
        jurypress = JuryPressFeedV1.model_validate(jurypress_data)
    except Exception as e:
        errors.append(f"feeds/jurypress.json validation failed: {e}")
        return errors

    # Validate each opportunity detail file and hash matching
    detail_hashes = {}
    for item in opp_list.items:
        detail_path = os.path.join(opps_dir, f"{item.public_id}.json")
        if not os.path.exists(detail_path):
            errors.append(f"Missing detail JSON file for opportunity: {item.public_id}")
            continue

        try:
            with open(detail_path) as f:
                detail_data = json.load(f)
            detail = PublicOpportunityDetailV1.model_validate(detail_data)
        except Exception as e:
            errors.append(f"Detail JSON validation failed for {item.public_id}: {e}")
            continue

        if item.content_hash != detail.content_hash:
            errors.append(f"Content hash mismatch for {item.public_id} between list ({item.content_hash}) and detail ({detail.content_hash})")

        detail_hashes[item.public_id] = detail.content_hash

    # Validate Manifest dataset hash consistency
    sorted_items = sorted(opp_list.items, key=lambda x: x.public_id)
    hash_payload = [f"{i.public_id}:{i.revision}:{i.content_hash}" for i in sorted_items]
    manifest_raw_str = ",".join(hash_payload)
    expected_manifest_hash = hashlib.sha256(manifest_raw_str.encode("utf-8")).hexdigest()

    if manifest.content_hash != expected_manifest_hash:
        errors.append(f"Manifest content_hash mismatch. Expected {expected_manifest_hash}, got {manifest.content_hash}")

    # Verify JuryPress feed item hashes
    for feed_item in jurypress.items:
        if feed_item.public_id not in detail_hashes:
            errors.append(f"JuryPress feed contains unknown opportunity: {feed_item.public_id}")
            continue
        if feed_item.content_hash != detail_hashes[feed_item.public_id]:
            errors.append(f"JuryPress content hash mismatch for {feed_item.public_id}. Feed: {feed_item.content_hash}, Detail: {detail_hashes[feed_item.public_id]}")

    return errors


def inspect_jurypress_feed(data_dir: str) -> dict[str, Any]:
    opp_list_path = os.path.join(data_dir, "opportunities.json")
    jurypress_path = os.path.join(data_dir, "feeds", "jurypress.json")

    result = {
        "ready": [],
        "excluded": []
    }

    if not os.path.exists(opp_list_path) or not os.path.exists(jurypress_path):
        return result

    try:
        with open(opp_list_path) as f:
            opp_list_data = json.load(f)
        opp_list = PublicOpportunityListV1.model_validate(opp_list_data)
    except Exception:
        return result

    for item in opp_list.items:
        title = item.localization.ja.title if item.localization.ja else item.public_id
        if item.jurypress.ready:
            result["ready"].append({
                "public_id": item.public_id,
                "title": title,
                "score": item.score.total,
                "confidence": item.score.confidence
            })
        else:
            result["excluded"].append({
                "public_id": item.public_id,
                "title": title,
                "score": item.score.total,
                "confidence": item.score.confidence,
                "reasons": item.jurypress.reasons
            })

    return result
