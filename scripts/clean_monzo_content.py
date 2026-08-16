#!/usr/bin/env python
"""CLI entrypoint for Phase 3: clean raw HTML into structured text.

Usage:
    python scripts/clean_monzo_content.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monzo_ai.ingestion.discover_urls import DEFAULT_CONFIG_PATH, load_config
from monzo_ai.processing.clean_content import clean_pages, load_manifest_and_urls, print_clean_summary
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean raw HTML fetched in Phase 2 into structured text.")
    parser.add_argument("--manifest", type=Path, default=None, help="Override the Phase 2 fetch manifest CSV.")
    parser.add_argument("--urls", type=Path, default=None, help="Override the Phase 1 URL inventory CSV.")
    parser.add_argument("--output", type=Path, default=None, help="Override the output Parquet path.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of pages cleaned (fast local iteration).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    clean_cfg = config.get("content_clean", {})

    manifest_path = args.manifest or Path(clean_cfg.get("manifest_path", "data/raw/pages_manifest.csv"))
    urls_path = args.urls or Path(clean_cfg.get("urls_path", "data/raw/monzo_urls.csv"))
    output_path = args.output or Path(clean_cfg.get("output_path", "data/processed/pages.parquet"))

    if not manifest_path.exists():
        logger.error("Fetch manifest not found at %s — run scripts/fetch_monzo_pages.py first.", manifest_path)
        return 1
    if not urls_path.exists():
        logger.error("URL inventory not found at %s — run scripts/discover_monzo_urls.py first.", urls_path)
        return 1

    fetched_df = load_manifest_and_urls(manifest_path, urls_path)
    if fetched_df.empty:
        logger.error("No successfully-fetched pages found in %s; nothing to clean.", manifest_path)
        return 1

    _, stats = clean_pages(fetched_df, output_path, limit=args.limit)
    print_clean_summary(stats, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
