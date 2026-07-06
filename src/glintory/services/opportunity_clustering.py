from collections import defaultdict
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from glintory.domain.clustering import OpportunityClusteringConfig
from glintory.domain.enums import EvidenceRelationType


class OpportunityClusteringEngine:
    def __init__(self, config: OpportunityClusteringConfig | None = None) -> None:
        self.config = config or OpportunityClusteringConfig()

    def cluster_signals(self, signals: list) -> list[dict]:
        """Group a list of signals into opportunity clusters based on text similarity.

        Returns a list of dictionaries, each representing an Opportunity candidate:
        {
            "representative_signal": Signal,
            "signals": list[dict]  # {"signal": Signal, "relation_type": EvidenceRelationType, "relevance_score": float}
        }
        """
        if not signals:
            return []

        # 1. Prepare text features
        texts = []
        for s in signals:
            title = s.title or ""
            excerpt = s.excerpt or ""
            texts.append(f"{title}\n{excerpt}".strip())

        # 2. Vectorize texts
        vectorizer = TfidfVectorizer(stop_words="english", min_df=1)
        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
        except ValueError:
            # Handle cases where all texts are empty or only contain stop words
            # Fallback to putting each signal in its own cluster
            candidates = []
            for s in signals:
                candidates.append({
                    "representative_signal": s,
                    "signals": [{
                        "signal": s,
                        "relation_type": EvidenceRelationType.SUPPORTING,
                        "relevance_score": 1.0,
                    }],
                })
            return candidates

        # 3. Calculate Cosine Similarity
        sim_matrix = cosine_similarity(tfidf_matrix)

        # 4. Build adjacency matrix for graph partitioning
        threshold = self.config.similarity_threshold
        adj = (sim_matrix >= threshold).astype(int)

        # 5. Find connected components (each component is a cluster)
        n_components, labels = connected_components(
            csgraph=csr_matrix(adj), directed=False, connection="weak"
        )

        # 6. Group signal indices by component label
        component_to_indices = defaultdict(list)
        for idx, label in enumerate(labels):
            component_to_indices[label].append(idx)

        candidates = []
        for label, indices in component_to_indices.items():
            cluster_signals = [signals[idx] for idx in indices]

            # Determine representative signal: oldest collected_at, fallback to ID
            sorted_cluster = sorted(
                cluster_signals,
                key=lambda x: (x.collected_at, getattr(x, "id", ""))
            )
            rep_signal = sorted_cluster[0]
            rep_idx = signals.index(rep_signal)

            # Map relations and relevance score for each signal in the cluster
            signals_info = []
            for idx in indices:
                sig = signals[idx]
                sim_val = float(sim_matrix[rep_idx][idx])
                # Ensure similarity is bounded between 0.0 and 1.0
                sim_val = max(0.0, min(1.0, sim_val))

                # Decide relation type: supporting if similarity with rep is high (>= 0.5), related otherwise
                if sim_val >= 0.5:
                    rel_type = EvidenceRelationType.SUPPORTING
                else:
                    rel_type = EvidenceRelationType.RELATED

                signals_info.append({
                    "signal": sig,
                    "relation_type": rel_type,
                    "relevance_score": sim_val,
                })

            candidates.append({
                "representative_signal": rep_signal,
                "signals": signals_info,
            })

        return candidates
