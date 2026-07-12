import re
import urllib.parse
from typing import Any
from glintory.domain.enums import SignalRole, SignalType
from glintory.domain.clustering import calculate_evidence_origin
from glintory.services.signal_facets import extract_signal_facets

def check_contextual_negative(text: str) -> dict[str, bool]:
    """Context-aware negative keyword matching to avoid false positives."""
    text_lower = text.lower()
    
    heavy_backend = False
    enterprise_sales = False
    recurring_ai_cost = False
    solo_unsuitable = False

    backend_words = ["heavy backend", "complex backend", "microservices", "kubernetes", "k8s", "large scale database", "heavy server", "マイクロサービス"]
    sales_words = ["enterprise sales", "sales cycle", "sales team", "b2b sales", "エンタープライズ営業", "営業チーム"]
    ai_cost_words = ["heavy api cost", "expensive api", "expensive ai", "high hosting cost", "high running cost", "ai費用", "高額なホスティング"]
    solo_unsuitable_words = ["enterprise-grade", "multi-tenant", "collaboration", "rbac", "salesforce integration", "large scale", "組織向け", "共同編集", "権限管理"]

    negative_contexts = [
        "too complex", "too complicated", "complicated", "alternative", "simpl",
        "pain", "hate", "migration", "instead of", "replace", "avoid", "difficult",
        "too hard", "not want", "don't want", "away from", "overkill", "expensive", "slow"
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
    if has_backend_word:
        if not any(is_in_negative_context(w) for w in backend_words if w in text_lower):
            heavy_backend = True

    # Enterprise Sales Check
    has_sales_word = any(w in text_lower for w in sales_words)
    if has_sales_word:
        if not any(is_in_negative_context(w) for w in sales_words if w in text_lower):
            enterprise_sales = True

    # AI Cost Check
    has_ai_cost_word = any(w in text_lower for w in ai_cost_words)
    if has_ai_cost_word:
        if not any(is_in_negative_context(w) for w in ai_cost_words if w in text_lower):
            recurring_ai_cost = True

    # Solo Unsuitable Check
    has_solo_word = any(w in text_lower for w in solo_unsuitable_words)
    if has_solo_word:
        if not any(is_in_negative_context(w) for w in solo_unsuitable_words if w in text_lower):
            solo_unsuitable = True

    return {
        "heavy_backend": heavy_backend,
        "enterprise_sales": enterprise_sales,
        "recurring_ai_cost": recurring_ai_cost,
        "solo_unsuitable": solo_unsuitable,
    }

def calculate_metrics_and_gate_v3(cluster_signals: list[dict[str, Any]]) -> tuple[dict[str, Any], str, bool, str]:
    """Analyze a cluster's signals to determine Gate classification (Published / Research / Rejected).
    
    Returns:
        metrics: dict[str, int]
        gate_status: str ("passed" or "rejected")
        passed_published: bool (True for Published, False for Research Candidate or Rejected)
        reason: str
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

    # 1. Group by Evidence Origin to count unique independent evidences
    origins = {}
    for sig in signals:
        src_type = sig.source.source_type if (hasattr(sig, "source") and sig.source) else "generic"
        origin = calculate_evidence_origin(src_type, sig.canonical_url)
        origins.setdefault(origin, []).append(sig)

    independent_count = len(origins)

    # Count unique demand evidence origins
    demand_count = 0
    for origin, sigs_in_origin in origins.items():
        if any(sig.signal_role == SignalRole.DEMAND for sig in sigs_in_origin):
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

    # Combined text for negative constraints and single-signal detail checks
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
    
    is_spam = any(kw in combined_text_lower for kw in ["spam", "buy bitcoin", "casino online"])

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

    # 3. Check for Published Opportunity (Condition A or Condition B)
    # Condition A: Multiple independent evidences
    if independent_count >= 2:
        # Evidence must support the same problem concept (since they are in the same cluster, TF-IDF handles this,
        # but we also verify demand count >= 1 and no exclusion)
        if demand_count >= 1:
            return metrics, "passed", True, "Passed Condition A: Multiple independent evidences with demand."

    # Condition B: Strong Detailed Single Demand
    if independent_count == 1:
        single_origin_sigs = list(origins.values())[0]
        # We need at least one actual DEMAND signal in the origin
        demand_sig = next((sig for sig in single_origin_sigs if sig.signal_role == SignalRole.DEMAND), None)
        
        if demand_sig:
            sig_type = demand_sig.signal_type
            is_valid_type = sig_type in (SignalType.PAIN, SignalType.REQUEST, SignalType.COMPLAINT, SignalType.MIGRATION)
            
            # Content detail level checks using facets
            facets = extract_signal_facets(demand_sig.title, demand_sig.excerpt, demand_sig.source.source_type if demand_sig.source else "generic", sig_type)
            
            # Text length details
            desc_text = (demand_sig.title or "") + " " + (demand_sig.excerpt or "")
            is_detailed = len(desc_text.strip()) >= 60  # Require some length to not be a simple one-liner
            
            has_reinforcement = (
                bool(facets["workaround_markers"])
                or bool(facets["alternative_markers"])
                or bool(facets["cost_markers"])
                or bool(facets["privacy_markers"])
                or bool(facets["offline_markers"])
                or bool(facets["migration_markers"])
                or bool(facets["urgency_markers"])
                or bool(facets["willingness_to_pay_markers"])
            )
            
            # Single brief requests shouldn't qualify
            is_short_feature_request = (sig_type == SignalType.REQUEST) and (not is_detailed or not has_reinforcement)

            if is_valid_type and is_detailed and has_reinforcement and not is_short_feature_request:
                return metrics, "passed", True, "Passed Condition B: Strong single demand evidence."

    # 4. Fallback to Research Candidate (Not Published but has Demand and not Rejected)
    return metrics, "rejected", False, "Research Candidate: Needs further validation (structural completeness or more independent evidence)."
