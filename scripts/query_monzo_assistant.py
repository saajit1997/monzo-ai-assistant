#!/usr/bin/env python
"""CLI entrypoint for Phase 6: run a hybrid-search query against the
retrieval index built by scripts/build_retrieval_index.py.

This is a retrieval smoke-test / demo tool, not the assistant itself — no
LLM call happens here, it just shows what context a query would retrieve.
That's Phase 7's job.

Usage:
    python scripts/query_monzo_assistant.py "what's the fee for cash withdrawals abroad"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monzo_ai.ingestion.discover_urls import DEFAULT_CONFIG_PATH, load_config
from monzo_ai.retrieval.index import load_index
from monzo_ai.retrieval.search import search
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query the Monzo retrieval index.")
    parser.add_argument("query", type=str, help="The search query.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of chunks to return.")
    parser.add_argument("--index-dir", type=Path, default=None, help="Override the retrieval index directory.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    retrieval_cfg = config.get("retrieval", {})
    index_dir = args.index_dir or Path(retrieval_cfg.get("index_dir", "data/processed/retrieval"))

    if not (index_dir / "vector_index.faiss").exists():
        logger.error("Retrieval index not found at %s — run scripts/build_retrieval_index.py first.", index_dir)
        return 1

    index = load_index(index_dir)
    results = search(index, args.query, top_k=args.top_k)

    print()
    print(f'Query: "{args.query}"')
    print()
    for i, row in results.iterrows():
        print(f"[{i + 1}] score={row['score']:.4f}  {row['url']}")
        print(f"    {row['title']}")
        snippet = row["text"].replace("\n", " ")[:200]
        print(f"    {snippet}...")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
