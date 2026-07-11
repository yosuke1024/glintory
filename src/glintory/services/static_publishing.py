import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.models import (
    Opportunity,
    OpportunitySignal,
    ScheduleExecution,
    Signal,
    Source,
    SourceSchedule,
    OpportunityEnrichment,
    OpportunityEnrichmentLocalization,
)
from glintory.infrastructure.opportunity_query import check_stale
from glintory.infrastructure.opportunity_enrichment_repository import OpportunityEnrichmentRepository
from glintory.services.publishing_templates import (
    CSS_CONTENT,
    INDEX_TEMPLATE,
    LIST_TEMPLATE,
    DETAIL_TEMPLATE,
)


def validate_site_url(url: str | None) -> str:
    if not url:
        raise ValueError("SITE_URL_REQUIRED")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("INVALID_SITE_URL_SCHEME")
    if not parsed.netloc:
        raise ValueError("INVALID_SITE_URL_NETLOC")
    if parsed.username or parsed.password:
        raise ValueError("INVALID_SITE_URL_CREDENTIALS")
    if parsed.query:
        raise ValueError("INVALID_SITE_URL_QUERY")
    if parsed.fragment:
        raise ValueError("INVALID_SITE_URL_FRAGMENT")
    return url


def safe_url(url: str | None) -> str:
    if not url:
        return "#"
    url_lower = url.lower().strip()
    if url_lower.startswith(("http://", "https://")):
        return url
    return "#"


def format_datetime(val: Any) -> str:
    if not val:
        return "N/A"
    if isinstance(val, str):
        return val
    return val.isoformat()


def get_localized_data(op: Opportunity, enrichment: OpportunityEnrichment | None, locale: str, alt_loc_record: OpportunityEnrichmentLocalization | None) -> dict:
    def val(field_name: str, default_val: Any = None) -> Any:
        if alt_loc_record:
            v = getattr(alt_loc_record, field_name, None)
            if v is not None and (not isinstance(v, str) or v.strip()):
                return v
        if enrichment:
            v = getattr(enrichment, field_name, None)
            if v is not None and (not isinstance(v, str) or v.strip()):
                return v
        return default_val

    target_users = val("target_users")
    if target_users is None:
        target_users = []

    risks = val("risks")
    if risks is None:
        risks = []

    tags = val("tags")
    if tags is None:
        tags = []

    return {
        "title": val("generated_title", op.title),
        "summary": val("generated_summary", op.proposed_solution),
        "problem_statement": val("problem_statement"),
        "target_users": target_users,
        "why_now": val("why_now"),
        "evidence_synthesis": val("evidence_synthesis"),
        "build_direction": val("build_direction"),
        "risks": risks,
        "tags": tags,
    }


def calculate_current_hash(op_id: str, score_hash: str | None, ev_signals: list) -> str:
    sorted_ev = sorted(ev_signals, key=lambda e: e[0].id)
    ev_hash_strs = []
    for sig, rel_score, _, _ in sorted_ev:
        ev_hash_strs.append(f"{sig.id}:{sig.content_hash}:{rel_score}")

    parts = [
        op_id,
        score_hash or "",
        ",".join(ev_hash_strs),
        settings.local_llm_model_file,
        settings.local_llm_model_revision,
        "v1",  # PROMPT_VERSION
        "v1",  # SCHEMA_VERSION
    ]
    raw_str = "|".join(parts)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def build_static_site(
    session: Session,
    output_dir: str,
    base_path: str = "",
    site_url: str | None = None,
    pixapps_url: str | None = None,
    generated_at: datetime | None = None,
) -> dict:
    valid_site_url = validate_site_url(site_url)
    gen_time = generated_at or datetime.now(UTC)

    if base_path:
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        if base_path.endswith("/"):
            base_path = base_path[:-1]
    else:
        base_path = ""

    target_parent = os.path.dirname(os.path.abspath(output_dir))
    if target_parent:
        os.makedirs(target_parent, exist_ok=True)

    temp_build_dir = os.path.join(target_parent, f".tmp-build-{uuid.uuid4().hex}")
    os.makedirs(temp_build_dir, exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "opportunities"), exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "assets"), exist_ok=True)

    try:
        sources = session.query(Source).all()
        schedules = session.query(SourceSchedule).all()

        latest_exec = (
            session.query(ScheduleExecution)
            .order_by(ScheduleExecution.started_at.desc(), ScheduleExecution.id.desc())
            .first()
        )

        opportunities = (
            session.query(Opportunity)
            .order_by(
                Opportunity.total_score.desc(),
                Opportunity.last_scored_at.desc(),
                Opportunity.id.desc(),
            )
            .all()
        )

        signals_data = (
            session.query(Signal, Source.name, Source.source_type)
            .join(Source, Signal.source_id == Source.id)
            .order_by(
                Signal.created_at.desc(),
                Signal.id.desc(),
            )
            .limit(20)
            .all()
        )

        top_ops = opportunities[:5]

        latest_signals = []
        for sig, src_name, src_type in signals_data[:10]:
            latest_signals.append(
                {
                    "title": sig.title,
                    "canonical_url": sig.canonical_url,
                    "source_name": src_name,
                    "source_type": src_type,
                    "published_at": sig.published_at if sig.published_at else sig.collected_at,
                }
            )

        latest_json_data = {
            "generated_at": gen_time.isoformat(),
            "scheduler": {
                "last_run": latest_exec.started_at.isoformat() if latest_exec else None,
                "status": latest_exec.status if latest_exec else "inactive",
            },
            "stats": {
                "total_sources": len(sources),
                "active_sources": len([s for s in schedules if s.enabled]),
                "total_opportunities": len(opportunities),
            },
            "top_opportunities": [
                {
                    "id": o.id,
                    "title": o.title,
                    "total_score": o.total_score,
                    "confidence": o.confidence.value if hasattr(o.confidence, "value") else str(o.confidence),
                }
                for o in top_ops
            ],
        }
        with open(os.path.join(temp_build_dir, "data", "latest.json"), "w") as f:
            json.dump(latest_json_data, f, indent=2)

        ops_json_list = []
        for o in opportunities:
            ops_json_list.append(
                {
                    "id": o.id,
                    "title": o.title,
                    "summary": o.proposed_solution,
                    "total_score": o.total_score,
                    "confidence": o.confidence.value if hasattr(o.confidence, "value") else str(o.confidence),
                    "status": o.status.value if hasattr(o.status, "value") else str(o.status),
                    "last_scored_at": o.last_scored_at.isoformat() if o.last_scored_at else None,
                }
            )
        with open(os.path.join(temp_build_dir, "data", "opportunities.json"), "w") as f:
            json.dump(ops_json_list, f, indent=2)

        with open(os.path.join(temp_build_dir, "assets", "app.css"), "w") as f:
            f.write(CSS_CONTENT.strip())

        repo_url = os.environ.get("GLINTORY_REPOSITORY_URL")

        env = Environment(autoescape=select_autoescape(["html", "xml"]))
        env.filters["safe_url"] = safe_url
        env.filters["format_datetime"] = format_datetime

        index_template = env.from_string(INDEX_TEMPLATE)
        list_template = env.from_string(LIST_TEMPLATE)
        detail_template = env.from_string(DETAIL_TEMPLATE)

        active_sources = [s for s in schedules if s.enabled]
        total_sources_count = len(sources)
        active_sources_count = len(active_sources)

        rendered_index = index_template.render(
            base_path=base_path,
            pixapps_url=pixapps_url,
            latest_exec_time=latest_exec.started_at if latest_exec else None,
            latest_exec_status=latest_exec.status if latest_exec else "inactive",
            active_sources_count=active_sources_count,
            total_sources_count=total_sources_count,
            total_ops_count=len(opportunities),
            top_ops=top_ops,
            latest_signals=latest_signals,
            repo_url=repo_url,
        )
        with open(os.path.join(temp_build_dir, "index.html"), "w") as f:
            f.write(rendered_index)

        op_list_data = []
        for op in opportunities:
            ev_count = (
                session.query(OpportunitySignal)
                .filter(OpportunitySignal.opportunity_id == op.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .count()
            )
            op_list_data.append(
                {
                    "op": op,
                    "evidence_count": ev_count,
                    "evidence_updated_at": op.evidence_updated_at,
                    "last_scored_at": op.last_scored_at,
                }
            )

        rendered_list = list_template.render(
            base_path=base_path,
            op_list_data=op_list_data,
            repo_url=repo_url,
        )
        with open(os.path.join(temp_build_dir, "opportunities", "index.html"), "w") as f:
            f.write(rendered_list)

        enrich_repo = OpportunityEnrichmentRepository(session)
        for op in opportunities:
            ev_signals = (
                session.query(
                    Signal,
                    OpportunitySignal.relevance_score,
                    Source.name,
                    Source.source_type,
                )
                .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
                .join(Source, Signal.source_id == Source.id)
                .filter(OpportunitySignal.opportunity_id == op.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .order_by(OpportunitySignal.relevance_score.desc())
                .all()
            )

            evidences = []
            for sig, rel_score, src_name, src_type in ev_signals:
                evidences.append(
                    {
                        "id": sig.id,
                        "title": sig.title,
                        "url": sig.canonical_url,
                        "excerpt": sig.excerpt,
                        "source_name": src_name,
                        "source_type": src_type,
                        "published_at": sig.published_at if sig.published_at else sig.collected_at,
                        "relevance_score": rel_score,
                    }
                )

            score_is_stale = check_stale(
                op.current_scoring_version,
                op.last_scored_at,
                op.evidence_updated_at,
            )

            enrichment = enrich_repo.get_latest_successful_enrichment(op.id)

            # Determine LLM stale status based on computed input hash
            llm_is_stale = False
            if enrichment:
                # Find the latest score snapshot to get its input hash
                from sqlalchemy import desc
                from glintory.domain.models import ScoreSnapshot
                snapshots = (
                    session.query(ScoreSnapshot)
                    .filter(ScoreSnapshot.opportunity_id == op.id)
                    .order_by(desc(ScoreSnapshot.created_at))
                    .all()
                )
                score_hash = snapshots[0].input_hash if snapshots else None

                current_llm_hash = calculate_current_hash(op.id, score_hash, ev_signals)
                if enrichment.input_hash != current_llm_hash:
                    llm_is_stale = True

            en_loc = None
            ja_loc = None
            if enrichment:
                en_loc = session.query(OpportunityEnrichmentLocalization).filter(
                    OpportunityEnrichmentLocalization.enrichment_id == enrichment.id,
                    OpportunityEnrichmentLocalization.locale == "en",
                ).first()
                ja_loc = session.query(OpportunityEnrichmentLocalization).filter(
                    OpportunityEnrichmentLocalization.enrichment_id == enrichment.id,
                    OpportunityEnrichmentLocalization.locale == "ja",
                ).first()

            # Render English (Default)
            loc_data_en = get_localized_data(op, enrichment, "en", en_loc)
            rendered_detail_en = detail_template.render(
                base_path=base_path,
                op=op,
                evidences=evidences,
                score_is_stale=score_is_stale,
                llm_is_stale=llm_is_stale,
                repo_url=repo_url,
                enrichment=enrichment,
                locale="en",
                loc_data=loc_data_en,
            )

            op_dir = os.path.join(temp_build_dir, "opportunities", op.id)
            os.makedirs(op_dir, exist_ok=True)
            with open(os.path.join(op_dir, "index.html"), "w") as f:
                f.write(rendered_detail_en)

            # Render Japanese
            loc_data_ja = get_localized_data(op, enrichment, "ja", ja_loc)
            rendered_detail_ja = detail_template.render(
                base_path=base_path,
                op=op,
                evidences=evidences,
                score_is_stale=score_is_stale,
                llm_is_stale=llm_is_stale,
                repo_url=repo_url,
                enrichment=enrichment,
                locale="ja",
                loc_data=loc_data_ja,
            )

            op_dir_ja = os.path.join(op_dir, "ja")
            os.makedirs(op_dir_ja, exist_ok=True)
            with open(os.path.join(op_dir_ja, "index.html"), "w") as f:
                f.write(rendered_detail_ja)

        robots_content = "User-agent: *\nAllow: /\n"
        with open(os.path.join(temp_build_dir, "robots.txt"), "w") as f:
            f.write(robots_content)

        with open(os.path.join(temp_build_dir, ".nojekyll"), "w") as f:
            f.write("")

        sitemap_items = []

        def get_now_str() -> str:
            return gen_time.strftime("%Y-%m-%d")

        target_site_url = valid_site_url.rstrip("/")

        def make_loc(path: str) -> str:
            full_url = f"{target_site_url}{path}"
            return xml_escape(full_url)

        sitemap_items.append(f"""  <url>
    <loc>{make_loc("/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""")

        sitemap_items.append(f"""  <url>
    <loc>{make_loc("/opportunities/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

        for op in opportunities:
            sitemap_items.append(f"""  <url>
    <loc>{make_loc(f"/opportunities/{op.id}/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>""")
            sitemap_items.append(f"""  <url>
    <loc>{make_loc(f"/opportunities/{op.id}/ja/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>""")

        sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{"\n".join(sitemap_items)}
</urlset>
"""
        with open(os.path.join(temp_build_dir, "sitemap.xml"), "w") as f:
            f.write(sitemap_xml.strip())

        if os.path.exists(output_dir):
            backup_dir = output_dir + f".bak-{uuid.uuid4().hex}"
            try:
                os.rename(output_dir, backup_dir)
                os.rename(temp_build_dir, output_dir)
                shutil.rmtree(backup_dir)
            except Exception:
                if os.path.exists(backup_dir) and not os.path.exists(output_dir):
                    os.rename(backup_dir, output_dir)
                raise
        else:
            os.rename(temp_build_dir, output_dir)

    except Exception:
        if os.path.exists(temp_build_dir):
            shutil.rmtree(temp_build_dir)
        raise

    return {
        "opportunities_generated": len(opportunities),
        "total_files": (len(opportunities) * 2) + 8,
    }
