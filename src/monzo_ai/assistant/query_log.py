"""Phase 8 groundwork: structured logging of every query, its retrieved
chunks, and the generated answer.

This is the project's actual differentiator per docs/architecture.md -- the
query stream is a first-class analytics dataset, not just a chat log. A
normalized schema (one row per question, one row per retrieved chunk per
question) is what makes that queryable later: which categories get
retrieved most, which pages surface most often, and had_answer gives a
direct content-gap signal (the model explicitly said it didn't know).

SQLite (stdlib) rather than a new dependency -- directly SQL-queryable,
which is what matters for building analytics on top later.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    query_text TEXT NOT NULL,
    model TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    had_answer INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS query_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id INTEGER NOT NULL REFERENCES queries(id),
    rank INTEGER NOT NULL,
    url TEXT NOT NULL,
    category TEXT NOT NULL,
    score REAL NOT NULL
);
"""


@contextmanager
def _connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_query(
    db_path: Path,
    query_text: str,
    results: pd.DataFrame,
    answer_text: str,
    model: str,
    had_answer: bool,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> int:
    """Records one question asked, the chunks retrieval returned for it, and
    the generated answer. Returns the new query's row id.
    """
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO queries (timestamp, query_text, model, answer_text, had_answer, latency_ms, input_tokens, output_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                query_text,
                model,
                answer_text,
                int(had_answer),
                latency_ms,
                input_tokens,
                output_tokens,
            ),
        )
        query_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO query_results (query_id, rank, url, category, score) VALUES (?, ?, ?, ?, ?)",
            [
                (query_id, rank, row.url, row.category, float(row.score))
                for rank, row in enumerate(results.itertuples(), start=1)
            ],
        )
    return query_id


def load_queries(db_path: Path) -> pd.DataFrame:
    """Reads the full query log back as a DataFrame -- for inspection now,
    and the seed of Phase 9's analytics dashboard later.
    """
    if not db_path.exists():
        return pd.DataFrame()
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query("SELECT * FROM queries ORDER BY id", conn)
