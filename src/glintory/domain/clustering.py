import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpportunityClusteringConfig:
    similarity_threshold: float = 0.35
    cluster_version: str = "v1"
    min_signals_per_cluster: int = 1


def calculate_evidence_origin(source_type: str, canonical_url: str) -> str:
    """Calculate the deterministic evidence origin for a signal.

    Rules:
    - GitHub: extract owner/repo, prefixed with "github:".
    - Hacker News: return "hackernews:item:<item_id>".
    - RSS pointing to HN: same "hackernews:item:<item_id>".
    - Generic Web: host + normalized path (removing tracking query parameters).
    """
    import urllib.parse
    import re

    url = canonical_url or ""
    parsed = urllib.parse.urlparse(url)

    # Remove tracking query parameters and fragment
    q_params = urllib.parse.parse_qsl(parsed.query)
    cleaned_params = []
    for k, v in q_params:
        if k.lower().startswith("utm_") or k.lower() in ("ref", "source", "medium", "campaign"):
            continue
        cleaned_params.append((k, v))

    new_query = urllib.parse.urlencode(cleaned_params)
    normalized_url_parsed = parsed._replace(query=new_query, fragment="")
    normalized_url = urllib.parse.urlunparse(normalized_url_parsed)

    # 1. HN Item detection
    hn_match = re.search(r"news\.ycombinator\.com/item\?id=(\d+)", normalized_url)
    if hn_match:
        return f"hackernews:item:{hn_match.group(1)}"

    source_type_lower = source_type.lower()
    if "github" in source_type_lower or "github.com" in parsed.netloc:
        path = parsed.path.strip("/")
        parts = path.split("/")
        if "repos" in parts and len(parts) >= 3:
            idx = parts.index("repos")
            return f"github:{parts[idx + 1]}/{parts[idx + 2]}"
        if len(parts) >= 2:
            return f"github:{parts[0]}/{parts[1]}"
        return "github:generic"

    if "hackernews" in source_type_lower or "hacker_news" in source_type_lower:
        val_match = re.search(r"id=(\d+)", normalized_url)
        if val_match:
            return f"hackernews:item:{val_match.group(1)}"
        return "hackernews:generic"

    netloc = parsed.netloc
    if ":" in netloc:
        netloc = netloc.split(":")[0]
    netloc = netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path_normalized = parsed.path.rstrip("/")
    if not path_normalized:
        path_normalized = "/"

    return f"{netloc}{path_normalized}"
