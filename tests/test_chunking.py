"""Phase 6 chunking tests. Pure logic — no model, no network, no filesystem."""

from __future__ import annotations

import pandas as pd

from monzo_ai.retrieval.chunking import CHUNK_COLUMNS, chunk_lines, chunk_page, chunk_pages


def _line(n_words: int, label: str = "word") -> str:
    return " ".join(f"{label}{i}" for i in range(n_words))


class TestChunkLines:
    def test_packs_lines_until_target_word_count_exceeded(self):
        lines = [_line(4, "a"), _line(4, "b"), _line(4, "c")]
        chunks = chunk_lines(lines, target_words=10, min_words=0, overlap_lines=0)
        assert chunks == [f"{lines[0]}\n{lines[1]}", lines[2]]

    def test_no_split_when_everything_fits_under_target(self):
        lines = [_line(3), _line(3)]
        chunks = chunk_lines(lines, target_words=100, min_words=0, overlap_lines=0)
        assert chunks == ["\n".join(lines)]

    def test_overlap_carries_last_line_into_next_chunk(self):
        lines = [_line(4, "a"), _line(4, "b"), _line(4, "c")]
        chunks = chunk_lines(lines, target_words=10, min_words=0, overlap_lines=1)
        assert chunks[0] == f"{lines[0]}\n{lines[1]}"
        assert chunks[1] == f"{lines[1]}\n{lines[2]}"  # line "b" carried forward

    def test_small_trailing_chunk_merged_into_previous(self):
        lines = [_line(5, "a"), _line(5, "b"), _line(2, "c")]
        chunks = chunk_lines(lines, target_words=10, min_words=5, overlap_lines=0)
        # "c" alone (2 words) is below min_words=5, so it merges back in
        assert len(chunks) == 1
        assert lines[2] in chunks[0]

    def test_empty_input_produces_no_chunks(self):
        assert chunk_lines([], target_words=100) == []

    def test_single_line_still_produces_one_chunk(self):
        assert chunk_lines([_line(3)], target_words=1, min_words=0, overlap_lines=0) == [_line(3)]


class TestChunkPage:
    def test_produces_sequential_chunk_ids_and_propagates_metadata(self):
        row = {
            "url": "https://example.com/help/card",
            "category": "help",
            "title": "Card help",
            "body_text": f"{_line(5,'a')}\n{_line(5,'b')}\n{_line(5,'c')}",
        }
        chunks = chunk_page(row, target_words=8, min_words=0, overlap_lines=0)

        assert [c["chunk_id"] for c in chunks] == [f"{row['url']}#{i}" for i in range(len(chunks))]
        assert all(c["url"] == row["url"] for c in chunks)
        assert all(c["category"] == "help" for c in chunks)
        assert all(c["title"] == "Card help" for c in chunks)
        assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))

    def test_empty_body_text_returns_no_chunks(self):
        row = {"url": "https://example.com/x", "category": "other", "title": "", "body_text": ""}
        assert chunk_page(row) == []

    def test_word_count_matches_chunk_text(self):
        row = {
            "url": "https://example.com/x",
            "category": "other",
            "title": "X",
            "body_text": _line(6),
        }
        chunks = chunk_page(row, target_words=100)
        assert chunks[0]["word_count"] == len(chunks[0]["text"].split())


class TestChunkPages:
    def test_flattens_multiple_pages_into_one_dataframe(self):
        pages_df = pd.DataFrame(
            [
                {"url": "https://example.com/a", "category": "help", "title": "A", "body_text": _line(5)},
                {"url": "https://example.com/b", "category": "product", "title": "B", "body_text": _line(5)},
            ]
        )
        chunks_df = chunk_pages(pages_df, target_words=100)
        assert list(chunks_df.columns) == CHUNK_COLUMNS
        assert set(chunks_df["url"]) == {"https://example.com/a", "https://example.com/b"}
        assert len(chunks_df) == 2
