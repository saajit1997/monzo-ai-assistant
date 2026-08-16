#!/usr/bin/env python
"""CLI entrypoint for Phase 2: fetch raw HTML for MVP-included Monzo URLs.

Usage:
    python scripts/fetch_monzo_pages.py [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from monzo_ai.ingestion.discover_urls import DEFAULT_CONFIG_PATH, load_config
from monzo_ai.ingestion.fetch_pages import FetchPagesConfig, fetch_pages, print_fetch_summary
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch raw HTML for MVP-included Monzo URLs discovered in Phase 1.")
    parser.add_argument("--input", type=Path, default=None, help="Override the Phase 1 URL inventory CSV.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override the raw HTML output directory.")
    parser.add_argument("--manifest", type=Path, default=None, help="Override the fetch manifest CSV path.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of URLs fetched (fast local iteration).")
    parser.add_argument("--force", action="store_true", help="Re-fetch URLs even if already successfully fetched.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    content_cfg = config.get("content_fetch", {})

    input_path = args.input or Path(content_cfg.get("input_path", "data/raw/monzo_urls.csv"))
    output_dir = args.output_dir or Path(content_cfg.get("output_dir", "data/raw/pages"))
    manifest_path = args.manifest or Path(content_cfg.get("manifest_path", "data/raw/pages_manifest.csv"))

    if not input_path.exists():
        logger.error("URL inventory not found at %s — run scripts/discover_monzo_urls.py first.", input_path)
        return 1

    urls_df = pd.read_csv(input_path)

    fetch_config = FetchPagesConfig(
        robots_url=config["monzo"]["robots_url"],
        user_agent=config["crawler"]["user_agent"],
        timeout_seconds=config["crawler"].get("request_timeout_seconds", 10.0),
        request_delay_seconds=config["crawler"].get("request_delay_seconds", 0.4),
        max_retries=config["crawler"].get("max_retries", 3),
        retry_backoff_seconds=config["crawler"].get("retry_backoff_seconds", 1.0),
    )

    _, stats = fetch_pages(
        urls_df,
        output_dir=output_dir,
        manifest_path=manifest_path,
        config=fetch_config,
        force=args.force,
        limit=args.limit,
    )
    print_fetch_summary(stats, manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
