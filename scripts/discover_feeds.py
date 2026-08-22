#!/usr/bin/env python3
"""Probe candidate sites for usable feeds and measure what they actually publish.

Phase 0 tool. Source selection must be driven by measurement, not by guessing
which feeds exist: run this where the network is unrestricted (GitHub Actions)
and pick sources from the reported volume and sample headlines.

Standard library only, so it needs no install step.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

USER_AGENT = "GlintoryBot/2.0 (feed discovery probe; +https://github.com/yosuke1024/glintory)"
TIMEOUT = 20
POLITE_DELAY = 1.5
COMMON_FEED_PATHS = (
    "/rss.xml",
    "/feed",
    "/feed/",
    "/rss",
    "/index.rdf",
    "/atom.xml",
    "/rss/index.xml",
    "/feed.xml",
)
SAMPLE_TITLES = 6


FEEDY_HREF = re.compile(r"(\.(xml|rdf|rss)$|/(rss|feed|atom)(/|$))", re.I)


class FeedLinkParser(HTMLParser):
    """Collect feed URLs from <link rel=alternate> and feed-looking <a href>.

    Several Japanese publishers advertise their feeds only on a human-facing
    "RSS一覧" page, so anchors matter as much as head links.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.anchors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        href = a.get("href", "")
        if not href:
            return
        if tag == "link":
            if "alternate" not in a.get("rel", "").lower():
                return
            if any(t in a.get("type", "").lower() for t in ("rss", "atom", "rdf")):
                self.links.append(href)
        elif tag == "a" and FEEDY_HREF.search(href) and href not in self.anchors:
            self.anchors.append(href)


def fetch(url: str) -> bytes | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(5_000_000)
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body
    except (urllib.error.URLError, OSError, ValueError, gzip.BadGzipFile) as exc:
        print(f"    fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def parse_date(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(UTC)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(body: bytes) -> dict | None:
    """Extract entries from RSS 2.0, RDF/RSS 1.0 or Atom without external deps."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None

    root_tag = local(root.tag)
    if root_tag == "rss":
        kind, item_tag = "RSS2.0", "item"
    elif root_tag == "RDF":
        kind, item_tag = "RDF/RSS1.0", "item"
    elif root_tag == "feed":
        kind, item_tag = "Atom", "entry"
    else:
        return None

    entries = []
    for el in root.iter():
        if local(el.tag) != item_tag:
            continue
        title, published, raw_date = "", None, ""
        for child in el:
            name = local(child.tag)
            if name == "title" and child.text:
                title = " ".join(child.text.split())
            elif name in ("pubDate", "date", "published", "updated") and child.text:
                raw_date = raw_date or child.text.strip()
                published = published or parse_date(child.text)
        entries.append({"title": title, "published": published, "raw_date": raw_date})

    return {"format": kind, "entries": entries} if entries else None


def measure(entries: list[dict]) -> dict:
    """Estimate publishing volume from the timestamps present in the feed."""
    dated = sorted((e["published"] for e in entries if e["published"]), reverse=True)
    result: dict = {"items": len(entries), "dated_items": len(dated)}
    if not dated:
        return result | {"per_day": None, "newest_age_hours": None}

    now = datetime.now(UTC)
    span_hours = (dated[0] - dated[-1]).total_seconds() / 3600
    result["newest_age_hours"] = round((now - dated[0]).total_seconds() / 3600, 1)
    result["oldest_age_days"] = round((now - dated[-1]).total_seconds() / 86400, 1)
    # A feed is a fixed-size window, so items/span is a lower bound on real volume.
    result["per_day"] = round(len(dated) / (span_hours / 24), 1) if span_hours > 1 else None
    return result


def discover(site: dict) -> list[dict]:
    """Return every feed found for one candidate site, with measurements."""
    name, url = site["name"], site["url"]
    print(f"\n=== {name} ({url})")

    candidates: list[str] = []
    if site.get("feed_url"):
        candidates.append(site["feed_url"])
    candidates += site.get("extra_paths", [])

    if not site.get("feed_url") or site.get("probe_anyway"):
        html = fetch(url)
        if html:
            parser = FeedLinkParser()
            try:
                parser.feed(html.decode("utf-8", errors="replace"))
            except Exception as exc:  # malformed markup should not kill the run
                print(f"    html parse failed: {exc}", file=sys.stderr)
            host = urlparse(url).netloc
            anchors = [
                a for a in (urljoin(url, h) for h in parser.anchors)
                if urlparse(a).netloc == host
            ]
            candidates += [urljoin(url, href) for href in parser.links] + anchors[:12]
            print(f"    <link>: {len(parser.links)}  feed-ish <a>: {len(anchors)}")
        time.sleep(POLITE_DELAY)
        candidates += [urljoin(url, p) for p in COMMON_FEED_PATHS]

    seen, results = set(), []
    for feed_url in candidates:
        if feed_url in seen:
            continue
        seen.add(feed_url)

        body = fetch(feed_url)
        time.sleep(POLITE_DELAY)
        if not body:
            continue
        parsed = parse_feed(body)
        if not parsed:
            continue

        entries = parsed["entries"]
        row = {
            "site": name,
            "category": site.get("category", ""),
            "feed_url": feed_url,
            "format": parsed["format"],
            "samples": [e["title"] for e in entries[:SAMPLE_TITLES] if e["title"]],
            **measure(entries),
        }
        row["raw_date_sample"] = next(
            (e["raw_date"] for e in entries if e.get("raw_date")), ""
        )
        results.append(row)
        print(
            f"    FOUND {feed_url} [{row['format']}] "
            f"items={row['items']} per_day={row['per_day']} "
            f"newest_age_h={row.get('newest_age_hours')} "
            f"raw_date={row['raw_date_sample']!r}"
        )
        for title in row["samples"][:3]:
            print(f"      - {title}")
        # One good feed per site is enough; keep probing only if it looks empty.
        if row["items"] >= 5:
            break

    if not results:
        print("    NO FEED FOUND")
    return results


def render_markdown(rows: list[dict]) -> str:
    lines = [
        "# Feed discovery results",
        "",
        f"Probed at {datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
        "| Site | Category | Feed | Format | Items | Est/day | Newest (h) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: -(r["per_day"] or 0)):
        lines.append(
            f"| {r['site']} | {r['category']} | `{r['feed_url']}` | {r['format']} | "
            f"{r['items']} | {r['per_day'] or '?'} | {r.get('newest_age_hours') or '?'} |"
        )
    lines += ["", "## Sample headlines", ""]
    for r in sorted(rows, key=lambda r: -(r["per_day"] or 0)):
        lines.append(f"**{r['site']}** — `{r['feed_url']}`")
        lines += [f"- {t}" for t in r["samples"]] or ["- (no titles)"]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    candidates_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config/feed_candidates.json")
    sites = json.loads(candidates_path.read_text(encoding="utf-8"))["candidates"]
    print(f"Probing {len(sites)} candidate sites\n")

    rows: list[dict] = []
    for site in sites:
        try:
            rows += discover(site)
        except Exception as exc:  # never let one site abort the sweep
            print(f"    ERROR {site['name']}: {type(exc).__name__}: {exc}", file=sys.stderr)

    found = {r["site"] for r in rows}
    missing = [s["name"] for s in sites if s["name"] not in found]
    print(f"\n\nFeeds found for {len(found)}/{len(sites)} sites")
    if missing:
        print("No feed: " + ", ".join(missing))

    Path("feed_discovery.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = render_markdown(rows)
    Path("feed_discovery.md").write_text(markdown, encoding="utf-8")
    print("\n" + markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
