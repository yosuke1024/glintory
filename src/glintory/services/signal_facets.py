import re
from typing import Any
from glintory.domain.enums import SignalRole, SignalType

def extract_signal_facets(
    title: str,
    excerpt: str | None,
    source_type: str,
    signal_type: SignalType,
) -> dict[str, Any]:
    """Deterministically extract facets from a signal's text content.
    
    This includes marker detection and structural completeness mapping.
    It does not generate new narratives or interpretation sentences.
    """
    text = (title or "") + " " + (excerpt or "")
    text_lower = text.lower()

    def has_word(pattern: str) -> bool:
        if pattern.replace(" ", "").isalnum() and pattern.isascii():
            # Match word boundary for English keywords
            return bool(re.search(rf"\b{re.escape(pattern)}\b", text_lower))
        return pattern in text_lower

    # 1. Keywords mapping
    problem_kws = [
        "problem", "pain", "issue", "difficult", "annoy", "error", "fail",
        "broken", "limit", "too complex", "not working", "frustrate", "bug",
        "課題", "問題", "困っ", "痛手", "バグ", "エラー", "使いづらい", "複雑"
    ]
    actor_kws = [
        "customer", "target user", "developer", "target audience", "for developers",
        "for users", "users", "clients", "team", "organization",
        "ユーザー", "顧客", "開発者", "ターゲットユーザー", "チーム", "組織"
    ]
    workaround_kws = [
        "workaround", "instead of", "alternative", "current tool", "manually",
        "excel", "spreadsheet", "scripts", "bash", "python script", "temporary fix",
        "回避", "代替", "手動", "スプレッドシート", "エクセル", "スクリプト"
    ]
    alternative_kws = [
        "alternative to", "looking for alternative", "replace", "substitute",
        "simpler alternative", "migrate away", "migration"
    ]
    cost_kws = [
        "too expensive", "cost", "pricing", "pricing plan", "stripe", "billing",
        "subscription", "sub", "saas", "license", "premium", "charge", "buy",
        "would pay", "willing to pay", "高価", "費用", "課金", "有料"
    ]
    privacy_kws = [
        "privacy concern", "privacy requirement", "gdpr", "compliance", "privacy",
        "local-first", "self-host", "private data", "data leak"
    ]
    offline_kws = [
        "offline support", "offline requirement", "local-first", "offline-first",
        "offline", "local support", "no internet", "without internet"
    ]
    migration_kws = [
        "migrate away", "migration intent", "migrate from", "migration", "import from",
        "export to", "transition", "move from", "移行", "乗り換え"
    ]
    urgency_kws = [
        "urgency", "urgent", "immediately", "must have", "critical", "annoy",
        "frustrating", "painful", "cannot work", "blocker", "急ぎ", "至急"
    ]
    willingness_to_pay_kws = [
        "would pay", "willing to pay", "pay for", "pricing is fine", "commercial license",
        "stripe", "subscription", "buy", "purchase", "budget", "willing to buy"
    ]
    solution_request_kws = [
        "wish there was", "looking for", "feature request", "i want", "is there any",
        "would be great if", "need a tool", "looking to buy", "欲しい", "必要", "機能"
    ]
    product_or_project_keywords = [
        "github.com", "open source", "repository", "project", "tool", "library",
        "framework", "npm package", "pypi package"
    ]

    # Extract matching tokens
    problem_terms = [kw for kw in problem_kws if has_word(kw)]
    actor_terms = [kw for kw in actor_kws if has_word(kw)]
    workaround_markers = [kw for kw in workaround_kws if has_word(kw)]
    alternative_markers = [kw for kw in alternative_kws if has_word(kw)]
    cost_markers = [kw for kw in cost_kws if has_word(kw)]
    privacy_markers = [kw for kw in privacy_kws if has_word(kw)]
    offline_markers = [kw for kw in offline_kws if has_word(kw)]
    migration_markers = [kw for kw in migration_kws if has_word(kw)]
    urgency_markers = [kw for kw in urgency_kws if has_word(kw)]
    willingness_to_pay_markers = [kw for kw in willingness_to_pay_kws if has_word(kw)]
    solution_request_markers = [kw for kw in solution_request_kws if has_word(kw)]
    product_or_project_terms = [kw for kw in product_or_project_keywords if has_word(kw)]

    # 2. Structural Completeness Mapping
    structural_completeness = {
        "target_user": "confirmed" if actor_terms else "missing",
        "problem": "confirmed" if problem_terms else "missing",
        "workaround": "confirmed" if workaround_markers else "missing",
        "gap": "confirmed" if (alternative_markers or cost_markers or migration_markers) else "missing",
        "mvp": "confirmed" if (solution_request_markers or any(kw in text_lower for kw in ["mvp", "feature", "scope"])) else "missing",
    }

    # 3. Simple Tokenization for Problem Concepts (lowercase alphanumeric words, removing basic stopwords)
    stop_words = {
        "the", "a", "an", "and", "or", "but", "if", "then", "else", "when", "where",
        "why", "how", "what", "who", "whom", "this", "that", "these", "those",
        "is", "am", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "to", "for", "of", "in", "on", "at", "by", "with", "about",
        "as", "from", "into", "through", "during", "before", "after", "above", "below"
    }
    raw_tokens = re.findall(r"\b[a-z0-9\-]{3,}\b", text_lower)
    problem_concept_tokens = sorted(list(set(t for t in raw_tokens if t not in stop_words)))

    # Determine role based on classifications and text clues
    role = SignalRole.UNKNOWN
    source_type_lower = (source_type or "").lower()
    if source_type_lower == "hackernews":
        if title.lower().startswith("show hn:") or signal_type == SignalType.LAUNCH:
            role = SignalRole.SUPPLY
        elif (
            title.lower().startswith("ask hn:")
            or "ask hn" in title.lower()
            or signal_type == SignalType.REQUEST
            or problem_terms
            or solution_request_markers
        ):
            role = SignalRole.DEMAND
        else:
            role = SignalRole.CONTEXT
    elif source_type_lower == "github":
        if signal_type == SignalType.PROJECT:
            role = SignalRole.SUPPLY
        elif signal_type in (SignalType.REQUEST, SignalType.PAIN, SignalType.COMPLAINT):
            role = SignalRole.DEMAND
        else:
            role = SignalRole.CONTEXT
    elif source_type_lower == "rss":
        # Check RSS markers
        supply_indicators = ["announcing", "introducing", "released", "launching", "created a", "showcase", "my project", "my tool"]
        if any(has_word(kw) for kw in supply_indicators) or signal_type in (SignalType.PROJECT, SignalType.LAUNCH, SignalType.HACKATHON_PROJECT):
            role = SignalRole.SUPPLY
        elif signal_type in (SignalType.REQUEST, SignalType.PAIN, SignalType.COMPLAINT) or problem_terms or solution_request_markers:
            role = SignalRole.DEMAND
        else:
            role = SignalRole.CONTEXT

    return {
        "signal_role": role,
        "problem_terms": problem_terms,
        "actor_terms": actor_terms,
        "workaround_markers": workaround_markers,
        "alternative_markers": alternative_markers,
        "cost_markers": cost_markers,
        "privacy_markers": privacy_markers,
        "offline_markers": offline_markers,
        "migration_markers": migration_markers,
        "urgency_markers": urgency_markers,
        "willingness_to_pay_markers": willingness_to_pay_markers,
        "solution_request_markers": solution_request_markers,
        "product_or_project_terms": product_or_project_terms,
        "problem_concept_tokens": problem_concept_tokens,
        "structural_completeness": structural_completeness,
    }
