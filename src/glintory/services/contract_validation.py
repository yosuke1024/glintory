import hashlib
import json
import os
import glob
import re
from datetime import datetime
from typing import Any, Literal, cast
import jsonschema

from glintory.domain.public_contract import (
    JuryPressFeedV1,
    PublicManifestV1,
    PublicOpportunityDetailV1,
    PublicOpportunityListV1,
)
from glintory.services.content_hashing import (
    calculate_opportunity_detail_canonical_hash,
)


def raise_json_constant_error(c: str) -> Any:
    raise ValueError(f"Forbidden JSON constant detected: {c}")


def scan_security_violations(data: Any, path: str = "") -> list[str]:
    errors = []
    # Forbidden key names (case-insensitive)
    forbidden_keys = {
        "token", "authorization", "password", "secret", "database_url",
        "raw_metadata", "review_note", "stack_trace"
    }

    # Strict regex pattern checks for value values
    security_patterns = [
        (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "GitHub Token"),
        (re.compile(r"sqlite:///[^\s]+"), "SQLite URL"),
        (re.compile(r"file:///[^\s]+"), "Local File URL"),
        (re.compile(r"/Users/[a-zA-Z0-9_\.-]+/[^\s]+"), "Mac Local Path"),
        (re.compile(r"/home/[a-zA-Z0-9_\.-]+/[^\s]+"), "Linux Local Path"),
        (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"), "PEM Private Key"),
    ]

    if isinstance(data, dict):
        for k, v in data.items():
            k_lower = k.lower()
            current_path = f"{path}.{k}" if path else k
            for fk in forbidden_keys:
                if fk in k_lower:
                    errors.append(f"Security violation: key '{current_path}' contains forbidden term '{fk}'")
            errors.extend(scan_security_violations(v, current_path))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            errors.extend(scan_security_violations(item, f"{path}[{idx}]"))
    elif isinstance(data, str):
        for pattern, desc in security_patterns:
            if pattern.search(data):
                errors.append(f"Security violation: value at '{path}' matches credential pattern '{desc}'")
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
        "jurypress-feed.schema.json"
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
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            content.encode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"File {os.path.basename(filepath)} is not valid UTF-8")
            return None

        try:
            data = json.loads(content, parse_constant=raise_json_constant_error)
        except Exception as e:
            errors.append(f"Failed to parse JSON for {os.path.basename(filepath)}: {e}")
            return None

        # Verify using JSON Schema
        try:
            jsonschema.validate(instance=data, schema=schema)
        except Exception as e:
            errors.append(f"JSON Schema validation failed for {os.path.basename(filepath)}: {e}")

        # Verify using Pydantic (extra="forbid")
        try:
            model_instance = pydantic_model.model_validate(data)
        except Exception as e:
            errors.append(f"Pydantic model validation failed for {os.path.basename(filepath)}: {e}")
            return None

        # Security Scan
        sec_errors = scan_security_violations(data)
        for se in sec_errors:
            errors.append(f"{os.path.basename(filepath)} {se}")

        return model_instance

    # Validate manifest.json
    manifest = load_and_verify_json(manifest_path, PublicManifestV1, manifest_schema)
    if not manifest:
        return errors

    # Validate opportunities.json
    opp_list = load_and_verify_json(opp_list_path, PublicOpportunityListV1, opp_list_schema)
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

    # Glob all detailed JSON files to process untracked file validation
    detail_files = glob.glob(os.path.join(opps_dir, "*.json"))
    observed_ids = set()

    # Track components for independent Dataset Hash rebuilding
    scanned_active_summaries = []
    scanned_jurypress_feed_items = []
    scanned_aliases = []
    scanned_tombstones = []

    for df in detail_files:
        filename = os.path.basename(df)
        m = re.match(r"^(opp_[0-9a-f]{32})\.json$", filename)
        if not m:
            errors.append(f"Stray file found in opportunities folder: {filename}")
            continue

        public_id = m.group(1)
        observed_ids.add(public_id)

        # Parse detailed Opportunity JSON
        detail = load_and_verify_json(df, PublicOpportunityDetailV1, detail_schema)
        if not detail:
            continue

        if detail.public_id != public_id:
            errors.append(f"Public ID mismatch in detailed JSON: filename has {public_id}, but JSON has {detail.public_id}")
            continue

        # 1. Independent Content Hash verification
        recalculated_hash = calculate_opportunity_detail_canonical_hash(detail)
        if detail.content_hash != recalculated_hash:
            errors.append(f"Content hash integrity failure for {public_id}. Detail claims {detail.content_hash}, but re-calculated is {recalculated_hash}")

        # Basic constraints validation
        if detail.evidence:
            for ev in detail.evidence:
                if ev.excerpt and len(ev.excerpt) > 500:
                    errors.append(f"Detail {public_id} evidence {ev.signal_id} has excerpt exceeding 500 characters")
                if not (ev.url.startswith("http://") or ev.url.startswith("https://")):
                    errors.append(f"Detail {public_id} evidence {ev.signal_id} has invalid URL format: '{ev.url}'")

        # 2. Lifecycle logic validation
        if detail.public_lifecycle == "active":
            # Active Readiness validation
            if detail.score is None or detail.gate is None or detail.jurypress is None:
                errors.append(f"Active Opportunity {public_id} is missing score, gate or readiness details")
                continue

            # Independent Readiness re-calculation
            min_score = int(os.environ.get("GLINTORY_JURYPRESS_MIN_SCORE", "60"))
            reasons_recalc = []

            if detail.score.version != "v2":
                reasons_recalc.append("INVALID_SCORING_VERSION")
            if detail.gate.status != "passed":
                reasons_recalc.append("GATE_REJECTED")
            # In detailed output, low confidence is not published active (so if active it must be medium/high)
            if detail.score.confidence not in ("medium", "high"):
                reasons_recalc.append("LOW_CONFIDENCE")
            if detail.score.total < min_score:
                reasons_recalc.append("SCORE_BELOW_THRESHOLD")
            if detail.score.independent_evidence_count < 2:
                reasons_recalc.append("INSUFFICIENT_INDEPENDENT_EVIDENCE")
            if detail.score.demand_evidence_count < 1:
                reasons_recalc.append("INSUFFICIENT_DEMAND_EVIDENCE")

            if detail.enrichment_status not in ("completed", "succeeded"):
                reasons_recalc.append("ENRICHMENT_MISSING")

            # Check localizations
            if detail.translation_status != "completed" or (detail.localization and detail.localization.ja.status != "completed"):
                reasons_recalc.append("JAPANESE_LOCALIZATION_MISSING")
            else:
                if detail.localization is None:
                    reasons_recalc.append("JAPANESE_LOCALIZATION_MISSING")
                else:
                    ja = detail.localization.ja
                    ja_fields = [ja.title, ja.summary, ja.problem, ja.target_user, ja.current_workaround, ja.existing_solution_gap, ja.mvp_direction, ja.why_selected, ja.risks]
                    if any(f is None or len(f.strip()) == 0 for f in ja_fields):
                        reasons_recalc.append("JAPANESE_LOCALIZATION_MISSING")

            if detail.translation_status != "completed" or (detail.localization and detail.localization.en.status != "completed"):
                reasons_recalc.append("ENGLISH_LOCALIZATION_MISSING")
            else:
                if detail.localization is None:
                    reasons_recalc.append("ENGLISH_LOCALIZATION_MISSING")
                else:
                    en = detail.localization.en
                    en_fields = [en.title, en.summary, en.problem, en.target_user, en.current_workaround, en.existing_solution_gap, en.mvp_direction, en.why_selected, en.risks]
                    if any(f is None or len(f.strip()) == 0 for f in en_fields):
                        reasons_recalc.append("ENGLISH_LOCALIZATION_MISSING")

            # Evidence summary checks
            has_ev_summary = False
            if detail.evidence:
                for ev in detail.evidence:
                    if (ev.summary_ja and len(ev.summary_ja.strip()) > 0) or (ev.summary_en and len(ev.summary_en.strip()) > 0):
                        has_ev_summary = True
                        break
            if not has_ev_summary:
                reasons_recalc.append("EVIDENCE_SUMMARY_MISSING")

            seen_re = set()
            unique_re = []
            for r in reasons_recalc:
                if r not in seen_re:
                    seen_re.add(r)
                    unique_re.append(r)

            ready_recalc = (len(unique_re) == 0)

            # Match recalculated values
            if detail.jurypress.ready != ready_recalc:
                errors.append(f"JuryPress readiness mismatch on detail {public_id}: recalc={ready_recalc}, detail={detail.jurypress.ready}")
            if sorted(detail.jurypress.reasons) != sorted(unique_re):
                errors.append(f"JuryPress reasons mismatch on detail {public_id}: recalc={unique_re}, detail={detail.jurypress.reasons}")

            # Collect active detail info for list validations
            scanned_active_summaries.append(detail)
            if ready_recalc:
                scanned_jurypress_feed_items.append(detail)

        elif detail.public_lifecycle == "retired":
            if detail.retired_at is None or detail.retired_reason is None:
                errors.append(f"Retired detail {public_id} is missing retired_at or retired_reason")
            scanned_tombstones.append(detail)

        elif detail.public_lifecycle == "merged":
            if detail.canonical_public_id is None or detail.canonical_detail_url is None:
                errors.append(f"Merged detail {public_id} is missing canonical target links")
            scanned_aliases.append(detail)

        else:
            errors.append(f"Invalid public_lifecycle value on detail {public_id}: {detail.public_lifecycle}")

    # Validate active summary list alignment
    active_ids_from_list = {item.public_id for item in opp_list.items}
    active_ids_from_scanned = {d.public_id for d in scanned_active_summaries}

    if active_ids_from_list != active_ids_from_scanned:
        diff_list = active_ids_from_list - active_ids_from_scanned
        diff_scanned = active_ids_from_scanned - active_ids_from_list
        errors.append(
            f"Active Opportunity IDs mismatch. "
            f"Present in opportunities.json but not scanned active: {list(diff_list)}. "
            f"Scanned active but missing in opportunities.json: {list(diff_scanned)}."
        )

    # Validate html files existence for opportunities.json active items
    for item in opp_list.items:
        # Check list content hash matches details
        detail_match = next((d for d in scanned_active_summaries if d.public_id == item.public_id), None)
        if detail_match:
            if item.content_hash != detail_match.content_hash:
                errors.append(f"Content hash mismatch for {item.public_id} between opportunities.json list ({item.content_hash}) and detail ({detail_match.content_hash})")

            # Check readiness consistency
            if item.jurypress.ready != detail_match.jurypress.ready:
                errors.append(f"JuryPress readiness flag mismatch for {item.public_id} between list ({item.jurypress.ready}) and detail ({detail_match.jurypress.ready})")

            # Check detail_url consistency
            expected_detail_url = f"{base_path}/data/v1/opportunities/{item.public_id}.json"
            if item.detail_url != expected_detail_url:
                errors.append(f"Invalid detail_url for opportunity {item.public_id}: expected '{expected_detail_url}', got '{item.detail_url}'")

        # HTML file existence check
        html_suffix = item.html_url
        if base_path and html_suffix.startswith(base_path):
            html_suffix = html_suffix[len(base_path):]
        dist_dir = os.path.dirname(os.path.dirname(data_dir))
        html_path = os.path.join(dist_dir, html_suffix.lstrip("/"), "index.html")
        if not os.path.exists(html_path):
            errors.append(f"Missing html file for opportunity {item.public_id} at expected path: '{html_path}'")

    # Validate JuryPress feed item consistency
    ready_ids_from_feed = {item.public_id for item in jurypress.items}
    ready_ids_from_scanned = {d.public_id for d in scanned_jurypress_feed_items}

    if ready_ids_from_feed != ready_ids_from_scanned:
        diff_feed = ready_ids_from_feed - ready_ids_from_scanned
        diff_scanned = ready_ids_from_scanned - ready_ids_from_feed
        errors.append(
            f"JuryPress Feed items mismatch. "
            f"Present in feeds/jurypress.json but not scanned ready: {list(diff_feed)}. "
            f"Scanned ready but missing from feeds/jurypress.json: {list(diff_scanned)}."
        )

    # Validate each feed item hash
    for feed_item in jurypress.items:
        detail_match = next((d for d in scanned_jurypress_feed_items if d.public_id == feed_item.public_id), None)
        if detail_match and feed_item.content_hash != detail_match.content_hash:
            errors.append(f"Content hash mismatch for {feed_item.public_id} between JuryPress Feed ({feed_item.content_hash}) and detail ({detail_match.content_hash})")

    # Counts validation
    if opp_list.count != len(opp_list.items):
        errors.append(f"opportunities.json count mismatch: count field is {opp_list.count}, but items list length is {len(opp_list.items)}")

    if jurypress.count != len(jurypress.items):
        errors.append(f"feeds/jurypress.json count mismatch: count field is {jurypress.count}, but items list length is {len(jurypress.items)}")

    if manifest.counts.published_opportunities != len(opp_list.items):
        errors.append(f"Manifest published_opportunities count mismatch: manifest says {manifest.counts.published_opportunities}, list has {len(opp_list.items)}")

    if manifest.counts.jurypress_ready != len(jurypress.items):
        errors.append(f"Manifest jurypress_ready count mismatch: manifest says {manifest.counts.jurypress_ready}, feed has {len(jurypress.items)}")

    # Check for untracked / stray detail files
    registered_ids = active_ids_from_list | {d.public_id for d in scanned_tombstones} | {d.public_id for d in scanned_aliases}
    untracked_ids = observed_ids - registered_ids
    if untracked_ids:
        errors.append(f"Stray detailed Opportunity JSONs detected: {list(untracked_ids)}")

    # 3. Manifest Dataset Hash verification
    dataset = {
        "opportunities": [],
        "jurypress_feed": [],
        "aliases": [],
        "tombstones": [],
    }

    # Populate active opportunities
    for item in sorted(opp_list.items, key=lambda x: x.public_id):
        dataset["opportunities"].append(
            {
                "public_id": item.public_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
                "public_lifecycle": item.public_lifecycle,
                "jurypress_ready": item.jurypress.ready,
                "jurypress_reasons": [str(r) for r in item.jurypress.reasons],
            }
        )

    # Populate JuryPress ready feed items
    for item in sorted(jurypress.items, key=lambda x: x.public_id):
        dataset["jurypress_feed"].append(
            {
                "public_id": item.public_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
            }
        )

    # Populate aliases (merged)
    for alias in sorted(scanned_aliases, key=lambda x: x.public_id):
        dataset["aliases"].append(
            {
                "old_public_id": alias.public_id,
                "canonical_public_id": alias.canonical_public_id,
            }
        )

    # Populate retired (tombstones)
    for op in sorted(scanned_tombstones, key=lambda x: x.public_id):
        dataset["tombstones"].append(
            {
                "public_id": op.public_id,
                "revision": op.revision,
                "content_hash": op.content_hash,
                "retired_at": op.retired_at.isoformat()
                if isinstance(op.retired_at, datetime)
                else str(op.retired_at),
                "retired_reason": op.retired_reason,
            }
        )

    dataset_serialized = json.dumps(
        dataset, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    expected_manifest_hash = hashlib.sha256(dataset_serialized.encode("utf-8")).hexdigest()

    if manifest.content_hash != expected_manifest_hash:
        errors.append(f"Manifest dataset content_hash mismatch. Expected {expected_manifest_hash}, got {manifest.content_hash}")

    if jurypress.content_hash != manifest.content_hash:
        errors.append(f"feeds/jurypress.json content_hash mismatch: feed has {jurypress.content_hash}, manifest has {manifest.content_hash}")

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
        title = item.localization.ja.title if item.localization.ja and item.localization.ja.title else item.public_id
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
