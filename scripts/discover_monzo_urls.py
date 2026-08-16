#!/usr/bin/env python
"""CLI entrypoint for Phase 1: Monzo public URL discovery.

Usage:
    python scripts/discover_monzo_urls.py [--limit N] [--output PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monzo_ai.ingestion.discover_urls import (
    DEFAULT_CONFIG_PATH,
    discover,
    load_config,
    print_summary,
    save_csv,
)
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover public Monzo URLs from their sitemap(s).")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of URLs processed (fast local iteration).")
    parser.add_argument("--output", type=Path, default=None, help="Override the output CSV path.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)

    try:
        df, stats = discover(config, limit=args.limit)
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    output_path = args.output or Path(config["monzo"]["output_path"])
    save_csv(df, output_path)
    print_summary(df, stats, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
