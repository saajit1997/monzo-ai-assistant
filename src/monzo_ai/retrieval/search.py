"""Phase 6: hybrid (vector + keyword) retrieval via Reciprocal Rank Fusion.

Combines FAISS's cosine-similarity ranking with BM25's keyword ranking so a
query matches both on meaning ("card stolen abroad" ~ "lost or damaged
card") and on exact terms a pure embedding search can under-weight (product
names, plan tiers, specific fee figures).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from monzo_ai.retrieval.index import RetrievalIndex, _tokenize, embed_texts

DEFAULT_VECTOR_K = 20
DEFAULT_BM25_K = 20
DEFAULT_RRF_CONSTANT = 60


def _rrf_fuse(rankings: list[list[int]], k: int = DEFAULT_RRF_CONSTANT) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(item) = sum over each ranking it appears
    in of 1 / (k + rank + 1). An item ranked highly in multiple lists scores
    higher than one ranked highly in only one.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return scores


def search(
    index: RetrievalIndex,
    query: str,
    top_k: int = 5,
    vector_k: int = DEFAULT_VECTOR_K,
    bm25_k: int = DEFAULT_BM25_K,
) -> pd.DataFrame:
    """Hybrid search over index, returning the top_k chunks as a DataFrame
    (chunk columns plus a fused `score`), best match first.
    """
    n = len(index.chunks)
    if n == 0:
        return index.chunks.iloc[0:0].assign(score=[])

    query_embedding = embed_texts([query], model_name=index.model_name)
    _, vector_ids = index.faiss_index.search(query_embedding, min(vector_k, n))
    vector_ranking = [int(i) for i in vector_ids[0] if i != -1]

    bm25_scores = index.bm25.get_scores(_tokenize(query))
    bm25_ranking = [int(i) for i in np.argsort(bm25_scores)[::-1][: min(bm25_k, n)]]

    fused = _rrf_fuse([vector_ranking, bm25_ranking])
    ranked_ids = sorted(fused.keys(), key=lambda i: fused[i], reverse=True)[:top_k]

    result = index.chunks.iloc[ranked_ids].copy()
    result["score"] = [fused[i] for i in ranked_ids]
    return result.reset_index(drop=True)
