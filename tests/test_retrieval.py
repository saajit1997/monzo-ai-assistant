"""Phase 6 retrieval tests.

TestRrfFuse is pure logic (no model). TestHybridSearch is a functional test
against the real small local sentence-transformers model (no network beyond
the one-time model download, no API key) — it verifies actual retrieval
behaviour end-to-end rather than mocking the embedding step, since a mocked
embedding model can't tell us whether hybrid search actually ranks the
right chunk first.
"""

from __future__ import annotations

import pandas as pd

from monzo_ai.retrieval.index import build_index
from monzo_ai.retrieval.search import DEFAULT_RRF_CONSTANT, _rrf_fuse, search


class TestRrfFuse:
    def test_item_ranked_first_in_both_lists_scores_highest(self):
        scores = _rrf_fuse([[1, 2, 3], [1, 3, 2]])
        assert max(scores, key=scores.get) == 1

    def test_item_present_in_only_one_list_still_scores(self):
        scores = _rrf_fuse([[5], []])
        assert scores[5] == 1.0 / (DEFAULT_RRF_CONSTANT + 1)

    def test_rank_within_a_list_affects_score(self):
        scores = _rrf_fuse([[1, 2, 3]])
        assert scores[1] > scores[2] > scores[3]

    def test_appearing_in_multiple_lists_outscores_a_single_appearance(self):
        scores = _rrf_fuse([[1, 2], [2, 1]])
        solo = _rrf_fuse([[1, 2]])
        assert scores[1] > solo[1]


CHUNK_FIXTURES = [
    {
        "chunk_id": "card#0",
        "url": "https://example.com/help/card-lost",
        "category": "help",
        "title": "Lost or stolen card",
        "chunk_index": 0,
        "text": "If your card is lost, stolen, or damaged, freeze it immediately in the Monzo app and order a free replacement.",
        "word_count": 18,
    },
    {
        "chunk_id": "fees#0",
        "url": "https://example.com/help/fees-abroad",
        "category": "help",
        "title": "Fees for using your card abroad",
        "chunk_index": 0,
        "text": "Monzo Plus customers get unlimited fee-free cash withdrawals in the UK and European Economic Area.",
        "word_count": 14,
    },
    {
        "chunk_id": "joint#0",
        "url": "https://example.com/current-account/joint-account",
        "category": "product",
        "title": "Joint accounts",
        "chunk_index": 0,
        "text": "A joint account lets two people share one current account, with both people able to see the balance and spending.",
        "word_count": 19,
    },
    {
        "chunk_id": "savings#0",
        "url": "https://example.com/savings-isas",
        "category": "product",
        "title": "Savings and ISAs",
        "chunk_index": 0,
        "text": "Instant access savings pots earn interest at a variable rate and let you withdraw your money at any time.",
        "word_count": 18,
    },
]


class TestHybridSearch:
    def test_semantically_similar_query_ranks_the_right_chunk_first(self):
        chunks_df = pd.DataFrame(CHUNK_FIXTURES)
        index = build_index(chunks_df)

        results = search(index, "my card was stolen, what should I do?", top_k=2)

        assert results.iloc[0]["chunk_id"] == "card#0"

    def test_different_query_ranks_a_different_chunk_first(self):
        chunks_df = pd.DataFrame(CHUNK_FIXTURES)
        index = build_index(chunks_df)

        results = search(index, "can my partner and I open a shared bank account?", top_k=2)

        assert results.iloc[0]["chunk_id"] == "joint#0"

    def test_returns_at_most_top_k_results_with_scores(self):
        chunks_df = pd.DataFrame(CHUNK_FIXTURES)
        index = build_index(chunks_df)

        results = search(index, "interest rates on savings", top_k=2)

        assert len(results) == 2
        assert list(results.columns[: len(CHUNK_FIXTURES[0])]) == list(chunks_df.columns)
        assert "score" in results.columns
        assert results["score"].is_monotonic_decreasing
