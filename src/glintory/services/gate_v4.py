import urllib.parse
import logging
from typing import Any

from glintory.domain.clustering import calculate_evidence_origin
from glintory.domain.enums import SignalRole, SignalType
from glintory.services.signal_facets import extract_signal_facets

logger = logging.getLogger(__name__)


def check_contextual_negative(text: str) -> dict[str, bool]:
    """Context-aware negative keyword matching to avoid false positives."""
    text_lower = text.lower()

    heavy_backend = False
    enterprise_sales = False
    recurring_ai_cost = False
    solo_unsuitable = False

    backend_words = [
        "heavy backend",
        "complex backend",
        "microservices",
        "kubernetes",
        "k8s",
        "large scale database",
        "heavy server",
        "マイクロサービス",
    ]
    sales_words = [
        "enterprise sales",
        "sales cycle",
        "sales team",
        "b2b sales",
        "エンタープライズ営業",
        "営業チーム",
    ]
    ai_cost_words = [
        "heavy api cost",
        "expensive api",
        "expensive ai",
        "high hosting cost",
        "high running cost",
        "ai費用",
        "高額なホスティング",
    ]
    solo_unsuitable_words = [
        "enterprise-grade",
        "multi-tenant",
        "collaboration",
        "rbac",
        "salesforce integration",
        "large scale",
        "組織向け",
        "共同編集",
        "権限管理",
    ]

    negative_contexts = [
        "too complex",
        "too complicated",
        "complicated",
        "alternative",
        "simpl",
        "pain",
        "hate",
        "migration",
        "instead of",
        "replace",
        "avoid",
        "difficult",
        "too hard",
        "not want",
        "don't want",
        "away from",
        "overkill",
        "expensive",
        "slow",
    ]

    def is_in_negative_context(word: str) -> bool:
        idx = text_lower.find(word)
        if idx == -1:
            return False
        start = max(0, idx - 100)
        end = min(len(text_lower), idx + len(word) + 100)
        context_area = text_lower[start:end]
        return any(neg in context_area for neg in negative_contexts)

    # Heavy Backend Check
    has_backend_word = any(w in text_lower for w in backend_words)
    if has_backend_word and not any(
        is_in_negative_context(w) for w in backend_words if w in text_lower
    ):
        heavy_backend = True

    # Enterprise Sales Check
    has_sales_word = any(w in text_lower for w in sales_words)
    if has_sales_word and not any(
        is_in_negative_context(w) for w in sales_words if w in text_lower
    ):
        enterprise_sales = True

    # AI Cost Check
    has_ai_cost_word = any(w in text_lower for w in ai_cost_words)
    if has_ai_cost_word and not any(
        is_in_negative_context(w) for w in ai_cost_words if w in text_lower
    ):
        recurring_ai_cost = True

    # Solo Unsuitable Check
    has_solo_word = any(w in text_lower for w in solo_unsuitable_words)
    if has_solo_word and not any(
        is_in_negative_context(w) for w in solo_unsuitable_words if w in text_lower
    ):
        solo_unsuitable = True

    return {
        "heavy_backend": heavy_backend,
        "enterprise_sales": enterprise_sales,
        "recurring_ai_cost": recurring_ai_cost,
        "solo_unsuitable": solo_unsuitable,
    }


def calculate_metrics_and_gate_v4(
    cluster_signals: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, bool, str]:
    """Analyze a cluster's signals to determine Gate v4 classification.

    Gate v4 Rules:
    - Condition B (Single detailed demand) is abolished.
    - Multiple independent demand evidence (>= 2) is required to be Published candidate.
    - Single demand evidence stays at Research candidate.
    """
    signals = [item["signal"] for item in cluster_signals]
    if not signals:
        return (
            {
                "independent_evidence_count": 0,
                "demand_evidence_count": 0,
                "source_type_count": 0,
                "source_domain_count": 0,
            },
            "rejected",
            False,
            "No signals in cluster.",
        )

    # 1. Group by Evidence Origin to count unique independent evidences (handling forks)
    origins = {}
    for sig in signals:
        src_type = (
            sig.source.source_type
            if (hasattr(sig, "source") and sig.source)
            else "generic"
        )
        
        # Fork parent detection
        parent_repo = None
        if hasattr(sig, "raw_metadata") and isinstance(sig.raw_metadata, dict):
            if sig.raw_metadata.get("fork"):
                parent_info = sig.raw_metadata.get("parent")
                if isinstance(parent_info, dict) and parent_info.get("full_name"):
                    parent_repo = parent_info.get("full_name")
        
        if parent_repo:
            origin = f"github:{parent_repo.lower()}"
        else:
            origin = calculate_evidence_origin(src_type, sig.canonical_url)
        
        # Avoid counting duplicate occurrences of identical copy / forks if they have parent info
        # Also exclude agents-radar itself from being counted as independent evidence origin
        if "duanyytop/agents-radar" in (sig.canonical_url or ""):
            continue
            
        origins.setdefault(origin, []).append(sig)

    independent_count = len(origins)

    # Count unique demand evidence origins (excluding duplicate authors)
    seen_authors = set()
    demand_count = 0
    for _origin, sigs_in_origin in origins.items():
        demands = [s for s in sigs_in_origin if s.signal_role == SignalRole.DEMAND]
        if not demands:
            continue
        
        valid_demand = False
        for s in demands:
            author = getattr(s, "author", None)
            if author:
                # If author already seen in another demand evidence, skip counting it as independent
                if author in seen_authors:
                    continue
                seen_authors.add(author)
            valid_demand = True
            
        if valid_demand:
            demand_count += 1

    source_types = {
        sig.source.source_type
        for sig in signals
        if (hasattr(sig, "source") and sig.source and sig.source.source_type)
    }
    source_type_count = len(source_types)

    domains = set()
    for sig in signals:
        if sig.canonical_url:
            parsed = urllib.parse.urlparse(sig.canonical_url)
            if parsed.netloc:
                domains.add(parsed.netloc)
    source_domain_count = len(domains)

    metrics = {
        "independent_evidence_count": independent_count,
        "demand_evidence_count": demand_count,
        "source_type_count": source_type_count,
        "source_domain_count": source_domain_count,
    }

    # Combined text for negative constraints
    combined_text = "\n".join(
        f"{sig.title or ''}\n{sig.excerpt or ''}" for sig in signals
    )
    combined_text_lower = combined_text.lower()

    # Determine Show HN single check
    is_single_show_hn = False
    if independent_count == 1:
        first_origin_sigs = list(origins.values())[0]
        is_single_show_hn = any(
            (
                sig.source
                and sig.source.source_type == "hackernews"
                and (sig.title or "").lower().startswith("show hn:")
            )
            for sig in first_origin_sigs
        )

    # 2. Hard Constraints Check (Exclude immediately to REJECTED)
    neg_results = check_contextual_negative(combined_text)

    is_spam = any(
        kw in combined_text_lower for kw in ["spam", "buy bitcoin", "casino online"]
    )

    is_rejected = False
    reject_reason = ""

    if demand_count == 0:
        is_rejected = True
        reject_reason = "Rejected: No demand evidence present."
    elif is_single_show_hn:
        is_rejected = True
        reject_reason = "Rejected: Single Show HN submission cannot be promoted."
    elif neg_results["solo_unsuitable"]:
        is_rejected = True
        reject_reason = "Rejected: Not suitable for solo developer."
    elif neg_results["heavy_backend"]:
        is_rejected = True
        reject_reason = "Rejected: Requires heavy backend."
    elif neg_results["recurring_ai_cost"]:
        is_rejected = True
        reject_reason = "Rejected: High continuous AI inference cost."
    elif neg_results["enterprise_sales"]:
        is_rejected = True
        reject_reason = "Rejected: Requires enterprise sales."
    elif is_spam:
        is_rejected = True
        reject_reason = "Rejected: Classified as spam or promotion only."

    if is_rejected:
        return metrics, "rejected", False, reject_reason

    # 3. Check for Published Opportunity (Condition A only in Gate v4)
    # Require at least 2 independent demand evidence origins
    if demand_count >= 2:
        return (
            metrics,
            "passed",
            True,
            "Passed Gate v4: Multiple independent demand evidences confirmed.",
        )

    # 4. Fallback to Research Candidate (Needs further validation, e.g. single demand source)
    return (
        metrics,
        "rejected", # In DB, research candidates have gate_status="rejected" but status=RESEARCH
        False,
        "Research Candidate: Needs further validation (structural completeness or more independent evidence).",
    )
