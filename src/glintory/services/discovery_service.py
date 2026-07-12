import html
import logging
import re
import urllib.parse
from datetime import UTC, date, datetime
from typing import Any, Callable

import httpx
from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.enums import (
    SignalDocumentKind,
    SignalRole,
    SignalType,
    SourceSpecificity,
)
from glintory.domain.models import (
    DiscoveryLead,
    DiscoveryLeadOccurrence,
    DiscoveryReport,
    Signal,
    Source,
)
from glintory.services.signal_ingestion import SignalIngestionService
from glintory.services.url_normalization import (
    InvalidSignalUrlError,
    SignalUrlTooLongError,
    normalize_url,
)

from glintory.collectors.base import RawItem
from glintory.collectors.github import parse_utc_datetime

logger = logging.getLogger(__name__)

# Regular expressions
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
GH_REPO_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$"
)
GH_ISSUE_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)$"
)
HN_ITEM_RE = re.compile(
    r"^https://news\.ycombinator\.com/item\?id=(\d+)$"
)


def clean_url_string(url: str) -> str:
    # Remove zero-width spaces
    url = re.sub(r"[\u200b-\u200d\ufeff]", "", url)
    # Decode HTML Entities
    url = html.unescape(url)
    return url


class AgentsRadarDiscoveryService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.http_client = http_client or httpx.AsyncClient(timeout=15.0)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.ingestion_service = SignalIngestionService(session_factory)

    async def fetch_manifest(self) -> dict | None:
        try:
            resp = await self.http_client.get(
                "https://duanyytop.github.io/agents-radar/manifest.json"
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch AgentsRadar manifest: {e}")
        return None

    async def fetch_report_markdown(self, path: str) -> str | None:
        url = f"https://raw.githubusercontent.com/duanyytop/agents-radar/main/{path}"
        try:
            resp = await self.http_client.get(url)
            if resp.status_code == 200:
                # 1MB limit
                max_bytes = 1024 * 1024
                content_len = resp.headers.get("Content-Length")
                if content_len and int(content_len) > max_bytes:
                    logger.warning(
                        f"Report {path} size exceeds limit, skipping."
                    )
                    return None
                if len(resp.content) > max_bytes:
                    logger.warning(
                        f"Report {path} size exceeds limit, skipping."
                    )
                    return None
                return resp.text
        except Exception as e:
            logger.error(f"Failed to fetch report markdown {path}: {e}")
        return None

    def extract_leads(self, markdown_content: str) -> list[tuple[str, str]]:
        extracted = []
        for match in LINK_RE.finditer(markdown_content):
            title = match.group(1).strip()
            url_str = clean_url_string(match.group(2).strip())

            # Prevent recursion
            if "github.com/duanyytop/agents-radar" in url_str:
                continue

            try:
                norm_url = normalize_url(url_str)
                extracted.append((title, norm_url))
            except (InvalidSignalUrlError, SignalUrlTooLongError) as e:
                logger.debug(f"Skipping invalid URL {url_str}: {e}")
                continue
        return extracted

    async def run_discovery(self) -> dict:
        manifest = await self.fetch_manifest()
        if not manifest or "digests" not in manifest:
            return {"status": "skipped", "reason": "manifest_fetch_failed"}

        session = self.session_factory()
        reports_processed = 0
        leads_processed = 0
        signals_created = 0

        # Sort digests chronologically by date
        digests = sorted(manifest["digests"], key=lambda x: x.get("date", ""))

        for dig in digests:
            digest_date_str = dig.get("date")
            digest_path = dig.get("path")
            if not digest_date_str or not digest_path:
                continue

            try:
                manifest_date = date.fromisoformat(digest_date_str)
            except ValueError:
                continue

            # Check if already processed
            existing_report = (
                session.query(DiscoveryReport)
                .filter(DiscoveryReport.manifest_date == manifest_date)
                .first()
            )
            if existing_report and existing_report.status == "processed":
                continue

            # Fetch report Markdown
            md_content = await self.fetch_report_markdown(digest_path)
            if not md_content:
                continue

            # Create or update report record
            if not existing_report:
                report = DiscoveryReport(
                    manifest_date=manifest_date,
                    fetched_at=self.clock(),
                    report_count=0,
                    status="running",
                )
                session.add(report)
                session.flush()
            else:
                report = existing_report
                report.status = "running"
                report.fetched_at = self.clock()
                session.flush()

            # Extract URLs
            raw_leads = self.extract_leads(md_content)
            report.report_count = len(raw_leads)
            session.flush()

            occurrences_created = 0
            for title, norm_url in raw_leads:
                # Get or create DiscoveryLead
                lead = (
                    session.query(DiscoveryLead)
                    .filter(DiscoveryLead.target_url == norm_url)
                    .first()
                )
                now = self.clock()
                if not lead:
                    lead = DiscoveryLead(
                        target_url=norm_url,
                        first_discovered_at=now,
                        last_seen_at=now,
                        occurrence_count=1,
                        verification_status="pending",
                        dispatch_status="pending",
                    )
                    session.add(lead)
                    session.flush()
                else:
                    lead.last_seen_at = now
                    lead.occurrence_count += 1
                    session.flush()

                # Record DiscoveryLeadOccurrence
                occ = DiscoveryLeadOccurrence(
                    lead_id=lead.id,
                    report_id=report.id,
                    raw_title=title[:512] if title else None,
                    discovered_at=now,
                )
                session.add(occ)
                session.flush()
                occurrences_created += 1

                # Dispatch and resolve primary source
                resolved_sig = await self.dispatch_lead(session, lead)
                if resolved_sig:
                    lead.resolved_signal_id = resolved_sig.id
                    lead.verification_status = "verified"
                    lead.dispatch_status = "dispatched"
                    signals_created += 1
                else:
                    lead.dispatch_status = "failed"
                session.flush()

            report.status = "processed"
            session.commit()
            reports_processed += 1
            leads_processed += occurrences_created

        session.close()
        return {
            "status": "success",
            "reports_processed": reports_processed,
            "leads_processed": leads_processed,
            "signals_created": signals_created,
        }

    async def dispatch_lead(
        self, session: Session, lead: DiscoveryLead
    ) -> Signal | None:
        url = lead.target_url
        # 1. GitHub Issue/PR
        issue_match = GH_ISSUE_RE.match(url)
        if issue_match:
            owner, repo, num = issue_match.groups()
            return await self._fetch_github_issue(session, lead, owner, repo, int(num))

        # 2. GitHub Repository
        repo_match = GH_REPO_RE.match(url)
        if repo_match:
            owner, repo = repo_match.groups()
            return await self._fetch_github_repo(session, lead, owner, repo)

        # 3. Hacker News Item
        hn_match = HN_ITEM_RE.match(url)
        if hn_match:
            item_id = hn_match.group(1)
            return await self._fetch_hn_item(session, lead, item_id)

        # Unsupported domains
        return None

    async def _fetch_github_issue(
        self, session: Session, lead: DiscoveryLead, owner: str, repo: str, num: int
    ) -> Signal | None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": settings.github_api_version,
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        api_url = f"{settings.github_api_url}/repos/{owner}/{repo}/issues/{num}"
        try:
            resp = await self.http_client.get(api_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title", f"GitHub Issue #{num}")
                body = data.get("body") or ""
                
                source = self._get_or_create_source(session, "github", "https://github.com")
                
                raw_item = RawItem(
                    external_id=f"gh-issue-{owner}-{repo}-{num}",
                    url=lead.target_url,
                    title=title[:500],
                    excerpt=body[:5000],
                    author=data.get("user", {}).get("login"),
                    published_at=parse_utc_datetime(data.get("created_at")),
                    item_type="issue",
                    metadata=data,
                    document_kind=SignalDocumentKind.STANDALONE_DEMAND.value,
                    opportunity_anchor=True,
                    discovery_eligible=True,
                    source_specificity=SourceSpecificity.MEDIUM.value,
                )
                
                result = self.ingestion_service.ingest(
                    source_id=source.id,
                    source_type=source.source_type,
                    collection_run_id="",
                    raw_items=[raw_item],
                    collected_at=self.clock(),
                )
                if result.signal_ids:
                    return session.get(Signal, result.signal_ids[0])
        except Exception as e:
            logger.error(f"Failed to fetch GitHub issue {owner}/{repo}#{num}: {e}")
        return None

    async def _fetch_github_repo(
        self, session: Session, lead: DiscoveryLead, owner: str, repo: str
    ) -> Signal | None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": settings.github_api_version,
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        api_url = f"{settings.github_api_url}/repos/{owner}/{repo}"
        try:
            resp = await self.http_client.get(api_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("full_name", f"{owner}/{repo}")
                description = data.get("description") or ""
                
                source = self._get_or_create_source(session, "github", "https://github.com")
                
                raw_item = RawItem(
                    external_id=f"gh-repo-{owner}-{repo}",
                    url=lead.target_url,
                    title=title[:500],
                    excerpt=description[:5000],
                    author=owner,
                    published_at=parse_utc_datetime(data.get("created_at")),
                    item_type="repository",
                    metadata=data,
                    document_kind=SignalDocumentKind.STANDALONE_DEMAND.value,
                    opportunity_anchor=False,
                    discovery_eligible=True,
                    source_specificity=SourceSpecificity.MEDIUM.value,
                )
                
                result = self.ingestion_service.ingest(
                    source_id=source.id,
                    source_type=source.source_type,
                    collection_run_id="",
                    raw_items=[raw_item],
                    collected_at=self.clock(),
                )
                if result.signal_ids:
                    return session.get(Signal, result.signal_ids[0])
        except Exception as e:
            logger.error(f"Failed to fetch GitHub repo {owner}/{repo}: {e}")
        return None

    async def _fetch_hn_item(
        self, session: Session, lead: DiscoveryLead, item_id: str
    ) -> Signal | None:
        api_url = f"{settings.hn_api_url}/item/{item_id}.json"
        try:
            resp = await self.http_client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                if not data:
                    return None
                
                title = data.get("title", f"Hacker News Item #{item_id}")
                text = data.get("text") or ""
                
                source = self._get_or_create_source(session, "hackernews", "https://news.ycombinator.com")
                
                raw_item = RawItem(
                    external_id=f"hn-item-{item_id}",
                    url=lead.target_url,
                    title=title[:500],
                    excerpt=text[:5000],
                    author=data.get("by"),
                    published_at=datetime.fromtimestamp(data.get("time"), UTC) if data.get("time") else None,
                    item_type="hn_story",
                    metadata=data,
                    document_kind=SignalDocumentKind.STANDALONE_DEMAND.value,
                    opportunity_anchor=True,
                    discovery_eligible=True,
                    source_specificity=SourceSpecificity.LOW.value,
                )
                
                result = self.ingestion_service.ingest(
                    source_id=source.id,
                    source_type=source.source_type,
                    collection_run_id="",
                    raw_items=[raw_item],
                    collected_at=self.clock(),
                )
                if result.signal_ids:
                    return session.get(Signal, result.signal_ids[0])
        except Exception as e:
            logger.error(f"Failed to fetch Hacker News item {item_id}: {e}")
        return None

    def _get_or_create_source(
        self, session: Session, source_type: str, base_url: str
    ) -> Source:
        source = (
            session.query(Source)
            .filter(Source.source_type == source_type)
            .first()
        )
        if not source:
            source = Source(
                name=f"Discovery {source_type.capitalize()}",
                source_type=source_type,
                base_url=base_url,
                enabled=True,
            )
            session.add(source)
            session.flush()
        return source
