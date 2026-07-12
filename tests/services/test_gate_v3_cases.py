import pytest
from datetime import datetime, UTC
from typing import Any

from glintory.domain.enums import SignalRole, SignalType, OpportunityStatus
from glintory.domain.models import Signal, Source
from glintory.domain.clustering import calculate_evidence_origin
from glintory.services.gate_v3 import calculate_metrics_and_gate_v3, check_contextual_negative

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
        collected_at: datetime = None
    ):
        self.title = title
        self.excerpt = excerpt
        self.source = DummySource(source_type)
        self.canonical_url = canonical_url
        self.signal_role = signal_role
        self.signal_type = signal_type
        self.collected_at = collected_at or datetime.now(UTC)
        self.id = f"sig_{hash(canonical_url)}"

def make_cluster_item(sig: DummySignal) -> dict[str, Any]:
    return {
        "signal": sig,
        "relation_type": "supporting",
        "relevance_score": 1.0
    }

def test_case_a_multiple_independent_with_demand():
    # Case A: 複数独立エビデンス（Demand 1件, Supply 1件）、除外条件なし -> Published (INBOX)
    sig1 = DummySignal("Looking for a self-hosted alternative to Slack", "Slack is too expensive and my team needs privacy", "hackernews", "https://news.ycombinator.com/item?id=1", SignalRole.DEMAND, SignalType.PAIN)
    sig2 = DummySignal("Show HN: Rufslack - self-hosted Slack alternative", "Rufslack is a fast alternative written in Go", "hackernews", "https://news.ycombinator.com/item?id=2", SignalRole.SUPPLY, SignalType.LAUNCH)
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert metrics["independent_evidence_count"] == 2
    assert metrics["demand_evidence_count"] == 1
    assert gate_status == "passed"
    assert passed_published is True
    assert "Condition A" in reason

def test_case_b_multiple_independent_no_demand():
    # Case B: 複数独立エビデンス, Demand 0件 -> Rejected (No demand)
    sig1 = DummySignal("Show HN: Rufslack - self-hosted Slack alternative", "A Go implementation", "hackernews", "https://news.ycombinator.com/item?id=2", SignalRole.SUPPLY, SignalType.LAUNCH)
    sig2 = DummySignal("Github Repository", "Go code", "github", "https://github.com/user/rufslack", SignalRole.SUPPLY, SignalType.LAUNCH)
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert metrics["demand_evidence_count"] == 0
    assert gate_status == "rejected"
    assert passed_published is False
    assert "No demand evidence" in reason

def test_case_c_strong_single_demand():
    # Case C: 強い単独需要（Condition B：Demandであり、詳細で、Workaround等の補強要素あり、除外条件なし） -> Published
    # 補強要素: workaround
    title = "I wish there was a self-hosted alternative to Supabase"
    excerpt = "We are currently using a spreadsheet and manual scripts because existing hosted options are too expensive."
    sig = DummySignal(title, excerpt, "hackernews", "https://news.ycombinator.com/item?id=123", SignalRole.DEMAND, SignalType.PAIN)
    
    cluster = [make_cluster_item(sig)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert metrics["independent_evidence_count"] == 1
    assert gate_status == "passed"
    assert passed_published is True
    assert "Condition B" in reason

def test_case_d_weak_single_demand():
    # Case D: 弱い単独需要（Condition Bを満たさない：短い、補強要素なし） -> Research Candidate (Needs further validation)
    sig = DummySignal("Need a database alternative", "I want a database", "hackernews", "https://news.ycombinator.com/item?id=456", SignalRole.DEMAND, SignalType.PAIN)
    
    cluster = [make_cluster_item(sig)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert metrics["independent_evidence_count"] == 1
    assert gate_status == "rejected"  # not passed for Published
    assert passed_published is False
    assert "Research Candidate" in reason

def test_case_e_heavy_backend_exclusion():
    # Case E: 重いバックエンド要件（除外条件） -> Rejected
    sig = DummySignal("Looking for a team database cluster", "We require kubernetes and microservices for large scale operations", "hackernews", "https://news.ycombinator.com/item?id=789", SignalRole.DEMAND, SignalType.PAIN)
    
    cluster = [make_cluster_item(sig)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert gate_status == "rejected"
    assert passed_published is False
    assert "Requires heavy backend" in reason or "Not suitable for solo developer" in reason

def test_case_f_contextual_negative_pass():
    # Case F: 文脈ネガティブ（Kubernetes is too complex... のように否定・簡素化文脈） -> 正常に合格
    sig = DummySignal("I hate kubernetes, it is too complex", "I want a single binary alternative to k8s for simple deployments", "hackernews", "https://news.ycombinator.com/item?id=101", SignalRole.DEMAND, SignalType.PAIN)
    
    # 補強要素(alternative, too complex)を含んでおり、k8sはあるが negative context であるため exclude されない
    cluster = [make_cluster_item(sig)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert gate_status == "passed"
    assert passed_published is True
    assert "Condition B" in reason

def test_case_g_single_show_hn():
    # Case G: 単独 Show HN -> Rejected
    sig = DummySignal("Show HN: New database alternative", "This is my tool", "hackernews", "https://news.ycombinator.com/item?id=999", SignalRole.DEMAND, SignalType.PAIN)
    
    cluster = [make_cluster_item(sig)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert gate_status == "rejected"
    assert passed_published is False
    assert "Single Show HN" in reason

def test_case_h_rss_hn_deduplication():
    # Case H: 異なる HN Item を RSS から二重計上した時、重複排除されるか
    sig1 = DummySignal("Self-hosted alternative", "Need one", "hackernews", "https://news.ycombinator.com/item?id=111", SignalRole.DEMAND, SignalType.PAIN)
    sig2 = DummySignal("Self-hosted alternative", "Need one", "rss", "https://news.ycombinator.com/item?id=111", SignalRole.DEMAND, SignalType.PAIN)
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    # 同じ HN Item なので、1つの origin になり、結果として independent_evidence_count = 1 になる
    assert metrics["independent_evidence_count"] == 1

def test_case_i_different_hn_threads():
    # Case I: 異なる HN Thread からの投稿 -> 異なる origin になり、独立Evidence数が 2
    sig1 = DummySignal("Self-hosted alternative", "Need one", "hackernews", "https://news.ycombinator.com/item?id=111", SignalRole.DEMAND, SignalType.PAIN)
    sig2 = DummySignal("Self-hosted alternative", "Need one", "hackernews", "https://news.ycombinator.com/item?id=222", SignalRole.DEMAND, SignalType.PAIN)
    
    cluster = [make_cluster_item(sig1), make_cluster_item(sig2)]
    metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster)
    
    assert metrics["independent_evidence_count"] == 2

def test_case_j_tracking_parameters_normalization():
    # Case J: トラッキングパラメータ違いの同一URL -> 同一 origin として重複排除される
    o1 = calculate_evidence_origin("rss", "https://example.com/blog/post?utm_source=twitter&utm_medium=social")
    o2 = calculate_evidence_origin("rss", "https://example.com/blog/post?utm_source=feed&ref=newsletter")
    
    assert o1 == o2
    assert o1 == "example.com/blog/post"
