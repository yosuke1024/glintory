import logging
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from glintory.domain.clustering import OpportunityClusteringConfig
from glintory.domain.enums import EvidenceRelationType, SignalRole

logger = logging.getLogger(__name__)

def normalize_text(text: str) -> str:
    """Normalize text by removing boilerplates, markdown symbols, and extra whitespaces."""
    if not text:
        return ""
    text = text.lower()
    # Remove HN headers
    text = re.sub(r"\b(show hn|ask hn|show-hn|ask-hn)\b", "", text)
    # Remove GitHub issue number (#123)
    text = re.sub(r"#\d+", "", text)
    # Remove URLs
    text = re.sub(r"https?://[^\s]+", "", text)
    # Remove markdown formatting characters
    text = re.sub(r"[\#\*_\[\]\(\)\`\-+]", " ", text)
    # Collapse multiple whitespaces
    text = re.sub(r"\s+", " ", text).strip()
    return text

class OpportunityClusteringEngine:
    def __init__(self, config: OpportunityClusteringConfig | None = None) -> None:
        self.config = config or OpportunityClusteringConfig()

    def cluster_signals(self, signals: list) -> list[dict]:
        """Group a list of signals into opportunity clusters based on text similarity.

        Uses a Demand-Anchored Greedy Clustering strategy to prevent transitive chain merges.
        
        Returns:
            list[dict]: A list of Opportunity candidates:
            {
                "representative_signal": Signal,
                "signals": list[dict]  # {"signal": Signal, "relation_type": EvidenceRelationType, "relevance_score": float}
            }
        """
        if not signals:
            return []

        # 1. Filter and sort Demands (as anchors) and Non-Demands
        demands = [s for s in signals if s.signal_role == SignalRole.DEMAND or getattr(s, "_is_existing_rep", False)]
        non_demands = [s for s in signals if not (s.signal_role == SignalRole.DEMAND or getattr(s, "_is_existing_rep", False))]

        # Sort demands by oldest collected_at first, to establish stable centroids
        demands = sorted(demands, key=lambda s: (s.collected_at, getattr(s, "id", "")))
        all_ordered = demands + non_demands

        # 2. Extract and normalize texts
        texts = [normalize_text(f"{s.title or ''}\n{s.excerpt or ''}") for s in all_ordered]

        # 3. Vectorize texts using TF-IDF (1, 2) ngrams
        custom_stop_words = "english"
        vectorizer = TfidfVectorizer(
            stop_words=custom_stop_words,
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=1,
        )

        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            sim_matrix = cosine_similarity(tfidf_matrix)
        except ValueError:
            # Fallback when vectorization fails (e.g. all texts empty or only stopwords)
            # Create single-signal clusters for demands only
            candidates = []
            for s in demands:
                candidates.append({
                    "representative_signal": s,
                    "signals": [{
                        "signal": s,
                        "relation_type": EvidenceRelationType.SUPPORTING,
                        "relevance_score": 1.0,
                    }]
                })
            return candidates

        threshold = self.config.similarity_threshold

        # 4. Greedy Clustering on Demands
        # clusters will store dict: {"rep_idx": int, "members": list[int]}
        clusters = []

        for d_i in range(len(demands)):
            best_match_cluster = None
            best_sim = -1.0

            # Find the best matching cluster based on similarity with its Representative
            for c in clusters:
                sim_val = float(sim_matrix[c["rep_idx"]][d_i])
                if sim_val > best_sim:
                    best_sim = sim_val
                    best_match_cluster = c

            if best_match_cluster and best_sim >= threshold:
                best_match_cluster["members"].append(d_i)
            else:
                # Start a new cluster anchored at this demand signal
                clusters.append({
                    "rep_idx": d_i,
                    "members": [d_i],
                })

        # 5. Greedy Association for Non-Demands
        # We do not create new clusters for non-demands. They can only join existing demand clusters.
        start_nd_idx = len(demands)
        for nd_offset in range(len(non_demands)):
            nd_i = start_nd_idx + nd_offset
            best_match_cluster = None
            best_sim = -1.0

            for c in clusters:
                sim_val = float(sim_matrix[c["rep_idx"]][nd_i])
                if sim_val > best_sim:
                    best_sim = sim_val
                    best_match_cluster = c

            if best_match_cluster and best_sim >= threshold:
                best_match_cluster["members"].append(nd_i)

        # 6. Build the final output structure
        candidates = []
        for c in clusters:
            rep_sig = all_ordered[c["rep_idx"]]
            signals_info = []

            for idx in c["members"]:
                sig = all_ordered[idx]
                sim_val = float(sim_matrix[c["rep_idx"]][idx])
                sim_val = max(0.0, min(1.0, sim_val))

                # Relation type based on similarity to representative
                rel_type = EvidenceRelationType.SUPPORTING if sim_val >= 0.5 else EvidenceRelationType.RELATED

                signals_info.append({
                    "signal": sig,
                    "relation_type": rel_type,
                    "relevance_score": sim_val,
                })

            candidates.append({
                "representative_signal": rep_sig,
                "signals": signals_info,
            })

        return candidates
