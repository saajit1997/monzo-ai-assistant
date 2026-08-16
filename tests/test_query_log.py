"""Phase 8 groundwork tests -- pure SQLite, no network, no mocks needed."""

from __future__ import annotations

import sqlite3

import pandas as pd

from monzo_ai.assistant.query_log import load_queries, log_query


def _results() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"url": "https://example.com/a", "category": "help", "score": 0.05},
            {"url": "https://example.com/b", "category": "product", "score": 0.03},
        ]
    )


def test_log_query_writes_query_and_result_rows(tmp_path):
    db_path = tmp_path / "query_log.db"

    query_id = log_query(
        db_path,
        query_text="what's the fee for cash abroad",
        results=_results(),
        answer_text="It's £400 fee-free.",
        model="claude-haiku-4-5",
        had_answer=True,
        latency_ms=850,
        input_tokens=300,
        output_tokens=40,
    )

    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        query_row = conn.execute("SELECT query_text, had_answer, model FROM queries WHERE id = ?", (query_id,)).fetchone()
        result_rows = conn.execute("SELECT rank, url, category FROM query_results WHERE query_id = ? ORDER BY rank", (query_id,)).fetchall()

    assert query_row == ("what's the fee for cash abroad", 1, "claude-haiku-4-5")
    assert result_rows == [(1, "https://example.com/a", "help"), (2, "https://example.com/b", "product")]


def test_had_answer_false_is_stored_as_zero(tmp_path):
    db_path = tmp_path / "query_log.db"

    query_id = log_query(
        db_path,
        query_text="unrelated question",
        results=_results(),
        answer_text="I don't know.",
        model="claude-haiku-4-5",
        had_answer=False,
        latency_ms=200,
        input_tokens=100,
        output_tokens=10,
    )

    with sqlite3.connect(db_path) as conn:
        had_answer = conn.execute("SELECT had_answer FROM queries WHERE id = ?", (query_id,)).fetchone()[0]
    assert had_answer == 0


def test_multiple_queries_accumulate_across_calls(tmp_path):
    db_path = tmp_path / "query_log.db"

    log_query(db_path, "q1", _results(), "a1", "claude-haiku-4-5", True, 100, 50, 10)
    log_query(db_path, "q2", _results(), "a2", "claude-haiku-4-5", True, 100, 50, 10)

    df = load_queries(db_path)
    assert list(df["query_text"]) == ["q1", "q2"]


def test_load_queries_returns_empty_frame_when_db_missing(tmp_path):
    df = load_queries(tmp_path / "does-not-exist.db")
    assert df.empty
