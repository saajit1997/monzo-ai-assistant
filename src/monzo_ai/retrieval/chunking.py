"""Phase 6: split Phase 3's cleaned page text into retrieval-sized chunks.

Chunks are packed from body_text's existing lines (never split mid-line, so
a table row flattened in Phase 3 — already a self-contained
"row — column: value" line — never gets separated from its own label) up to
a target word count, with a small line overlap carried into the next chunk
for continuity across the split.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

DEFAULT_TARGET_WORDS = 220
DEFAULT_MIN_WORDS = 40
DEFAULT_OVERLAP_LINES = 1

CHUNK_COLUMNS = ["chunk_id", "url", "category", "title", "chunk_index", "text", "word_count"]


def chunk_lines(
    lines: list[str],
    target_words: int = DEFAULT_TARGET_WORDS,
    min_words: int = DEFAULT_MIN_WORDS,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> list[str]:
    """Packs lines into chunks of up to ~target_words, splitting only at line
    boundaries. If the final chunk is smaller than min_words, it's merged
    into the previous one rather than shipped as a near-empty trailing chunk.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for line in lines:
        line_words = len(line.split())
        if current and current_words + line_words > target_words:
            chunks.append("\n".join(current))
            current = current[-overlap_lines:] if overlap_lines else []
            current_words = sum(len(l.split()) for l in current)
        current.append(line)
        current_words += line_words

    if current:
        chunks.append("\n".join(current))

    if len(chunks) > 1 and len(chunks[-1].split()) < min_words:
        chunks[-2] = chunks[-2] + "\n" + chunks[-1]
        chunks.pop()

    return chunks


def chunk_page(
    row: dict[str, Any],
    target_words: int = DEFAULT_TARGET_WORDS,
    min_words: int = DEFAULT_MIN_WORDS,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> list[dict[str, Any]]:
    """Chunks a single Phase 3 page row (needs url, category, title,
    body_text keys) into a list of chunk records.
    """
    lines = [line for line in row["body_text"].split("\n") if line.strip()]
    if not lines:
        return []

    texts = chunk_lines(lines, target_words=target_words, min_words=min_words, overlap_lines=overlap_lines)
    return [
        {
            "chunk_id": f"{row['url']}#{i}",
            "url": row["url"],
            "category": row["category"],
            "title": row["title"],
            "chunk_index": i,
            "text": text,
            "word_count": len(text.split()),
        }
        for i, text in enumerate(texts)
    ]


def chunk_pages(pages_df: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    """Chunks every row of a Phase 3 pages DataFrame into a flat chunks table."""
    records: list[dict[str, Any]] = []
    for row in pages_df.to_dict("records"):
        records.extend(chunk_page(row, **kwargs))
    return pd.DataFrame.from_records(records, columns=CHUNK_COLUMNS)
