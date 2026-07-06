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
    - GitHub: extract owner/repo.
    - Hacker News: return "hackernews".
    - RSS: return the host domain.
    """
    source_type_lower = source_type.lower()
    if "github" in source_type_lower:
        parsed = urllib.parse.urlparse(canonical_url)
        path = parsed.path.strip("/")
        parts = path.split("/")
        if "repos" in parts and len(parts) >= 3:
            idx = parts.index("repos")
            return f"{parts[idx+1]}/{parts[idx+2]}"
        elif len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return "github"
    elif "hackernews" in source_type_lower or "hacker_news" in source_type_lower:
        return "hackernews"
    else:
        parsed = urllib.parse.urlparse(canonical_url)
        netloc = parsed.netloc
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc or "generic"
