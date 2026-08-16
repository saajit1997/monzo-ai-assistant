#!/usr/bin/env python
"""CLI entrypoint for Phase 6: chunk cleaned content and build the hybrid
(FAISS + BM25) retrieval index.

Usage:
    python scripts/build_retrieval_index.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from monzo_ai.ingestion.discover_urls import DEFAULT_CONFIG_PATH, load_config
from monzo_ai.retrieval.chunking import chunk_pages
from monzo_ai.retrieval.index import build_index, save_index
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk cleaned content and build the retrieval index.")
    parser.add_argument("--pages", type=Path, default=None, help="Override the Phase 3 cleaned-content Parquet path.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override the retrieval index output directory.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of pages indexed (fast local iteration).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    retrieval_cfg = config.get("retrieval", {})

    pages_path = args.pages or Path(retrieval_cfg.get("pages_path", "data/processed/pages.parquet"))
    output_dir = args.output_dir or Path(retrieval_cfg.get("index_dir", "data/processed/retrieval"))
    model_name = retrieval_cfg.get("model_name", "all-MiniLM-L6-v2")

    if not pages_path.exists():
        logger.error("Cleaned content not found at %s — run scripts/clean_monzo_content.py first.", pages_path)
        return 1

    pages_df = pd.read_parquet(pages_path)
    if args.limit is not None:
        pages_df = pages_df.head(args.limit)

    chunks_df = chunk_pages(
        pages_df,
        target_words=retrieval_cfg.get("chunk_target_words", 220),
        min_words=retrieval_cfg.get("chunk_min_words", 40),
        overlap_lines=retrieval_cfg.get("chunk_overlap_lines", 1),
    )
    if chunks_df.empty:
        logger.error("Chunking produced zero chunks from %s; nothing to index.", pages_path)
        return 1

    logger.info("Chunked %d page(s) into %d chunk(s)", len(pages_df), len(chunks_df))

    index = build_index(chunks_df, model_name=model_name)
    save_index(index, output_dir)

    print()
    print("Monzo retrieval index build complete")
    print()
    print(f"Pages chunked: {len(pages_df):,}")
    print(f"Chunks indexed: {len(chunks_df):,}")
    print(f"Embedding model: {model_name}")
    print(f"Saved: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
