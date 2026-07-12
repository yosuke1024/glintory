from datetime import UTC, datetime
from typing import Any
import pytest

from glintory.domain.enums import SignalRole, SignalType
from glintory.services.gate_v4 import calculate_metrics_and_gate_v4


class DummySource:
    def __init__(self, source_type: str):
        self.source_type = source_type


class DummySignal:
    def __init__(
        self,
        title: str,
        excerpt: str,
        source_type: str,
        canonical_url: str,
        signal_role: SignalRole,
        signal_type: SignalType,
        author: str | None = None,
        raw_metadata: dict | None = None,
        collected_at: datetime | None = None,
    ):
        self.title = title
        self.excerpt = excerpt
        self.source = DummySource(source_type)
        self.canonical_url = canonical_url
        self.signal_role = signal_role
        self.signal_type = signal_type
        self.author = author
        self.raw_metadata = raw_metadata or {}
        self.collected_at = collected_at or datetime.now(UTC)
        self.id = f"sig_{hash(canonical_url)}"


def make_cluster_item(sig: DummySignal) -> dict[str, Any]:
    return {"signal": sig, "relation_type": "supporting", "relevance_score": 1.0}


def test_gate_v4_condition_b_abolished():
    # Case: Single strong detailed demand evidence (used to pass in gate_v3 under Condition B)
    # Should fall back to Research Candidate (gate_status="rejected", passed_published=False)
    sig = DummySignal(
        title="I need a workaround for missing PDF signature feature in python",
        excerpt="Currently there's no way to sign PDFs offline using python without external proprietary CLI tools. I am looking for a library to achieve this.",
        source_type="hackernews",
        canonical_url="https://news.ycombinator.com/item?id=123",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.PAIN,
        author="user_a",
    )
    
    cluster = [make_cluster_item(sig)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v4(cluster)
    
    assert metrics["independent_evidence_count"] == 1
    assert metrics["demand_evidence_count"] == 1
    assert gate_status == "rejected"
    assert passed_published is False
    assert "Research Candidate" in reason


def test_gate_v4_passed_multiple_demands():
    # Case: Multiple independent demand evidences -> Passed Published (gate_status="passed", passed_published=True)
    sig1 = DummySignal(
        title="Pain in python pdf signature",
        excerpt="Need offline pdf signer.",
        source_type="hackernews",
        canonical_url="https://news.ycombinator.com/item?id=123",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.PAIN,
        author="user_a",
    )
    sig2 = DummySignal(
        title="Feature request: sign pdfs in python",
        excerpt="Please add support for cryptographic signatures.",
        source_type="github",
        canonical_url="https://github.com/some/repo/issues/10",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.REQUEST,
        author="user_b",
    )
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v4(cluster)
    
    assert metrics["independent_evidence_count"] == 2
    assert metrics["demand_evidence_count"] == 2
    assert gate_status == "passed"
    assert passed_published is True
    assert "Passed Gate v4" in reason


def test_gate_v4_author_deduplication():
    # Case: Multiple demand evidences but by the same author (cross-post)
    # Should count as only 1 unique demand count, failing gate v4 (since we need >= 2 unique demand authors)
    sig1 = DummySignal(
        title="Pain in python pdf signature",
        excerpt="Need offline pdf signer.",
        source_type="hackernews",
        canonical_url="https://news.ycombinator.com/item?id=123",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.PAIN,
        author="user_a",
    )
    sig2 = DummySignal(
        title="Feature request: sign pdfs in python",
        excerpt="Please add support for cryptographic signatures.",
        source_type="github",
        canonical_url="https://github.com/some/repo/issues/10",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.REQUEST,
        author="user_a", # SAME AUTHOR
    )
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v4(cluster)
    
    assert metrics["demand_evidence_count"] == 1
    assert gate_status == "rejected"
    assert passed_published is False
    assert "Research Candidate" in reason


def test_gate_v4_fork_deduplication():
    # Case: Multiple demand evidences but one is from a fork repo
    # raw_metadata points to same parent repo, so they should be grouped into same origin (independent count = 1)
    sig1 = DummySignal(
        title="Need pdf signer",
        excerpt="Python signature problem.",
        source_type="github",
        canonical_url="https://github.com/parent/repo/issues/1",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.PAIN,
        author="user_a",
    )
    sig2 = DummySignal(
        title="Need pdf signer",
        excerpt="Python signature problem.",
        source_type="github",
        canonical_url="https://github.com/forked/repo/issues/1",
        signal_role=SignalRole.DEMAND,
        signal_type=SignalType.PAIN,
        author="user_b",
        raw_metadata={
            "fork": True,
            "parent": {
                "full_name": "parent/repo"
            }
        }
    )
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v4(cluster)
    
    assert metrics["independent_evidence_count"] == 1
    assert metrics["demand_evidence_count"] == 1
    assert gate_status == "rejected"
    assert passed_published is False
