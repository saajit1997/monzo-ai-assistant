#!/usr/bin/env python
"""Phase 6 acceptance check: hand-written query set with expected-relevant
URLs, measuring hit-rate@k against the built retrieval index.

Loads the already-built index (fast — no re-embedding) rather than
rebuilding it, so this can be re-run cheaply after any chunking/search
change to check for regressions.

Usage:
    python scripts/evaluate_retrieval.py [--top-k 5]
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

# Each entry: a realistic customer question and the URL(s) that would
# actually answer it. A query "hits" if any expected URL appears anywhere
# in the top-k results (as a substring match, since some are prefixes
# covering a whole section, e.g. any /legal/plus/... page).
QUERY_SET: list[dict] = [
    {"query": "what's the fee for withdrawing cash abroad", "expected": ["/help/travelling/understanding-fees", "/help/account-and-profile/understanding-fees"]},
    {"query": "my card was stolen, what do I do", "expected": ["/help/emergencies", "monzo-card-lost-damaged-stolen"]},
    {"query": "can I open a joint account with my partner", "expected": ["/current-account/joint-account"]},
    {"query": "how do I freeze my card", "expected": ["monzo-card-lost-damaged-stolen", "/help/emergencies"]},
    {"query": "what interest rate do I get on savings", "expected": ["/savings-isas"]},
    {"query": "does Monzo Business have a free plan", "expected": ["/business-banking"]},
    {"query": "how do I report a scam or fraud", "expected": ["/security", "/fraud", "scams"]},
    {"query": "what happens if someone in my household dies", "expected": ["/bereavements", "PASSES-AWAY"]},
    {"query": "can under 16s have a Monzo account", "expected": ["16-17", "under-16"]},
    {"query": "what are the terms and conditions for Monzo Plus", "expected": ["/legal/plus/terms-and-conditions"]},
    {"query": "how much does Monzo Max cost per month", "expected": ["/current-account/plans", "current-account/max"]},
    {"query": "can I get a business loan or overdraft from Monzo", "expected": ["/business-banking/business-loans-overdrafts"]},
    {"query": "how do I add my Monzo card to Apple Pay", "expected": ["apple-pay"]},
    {"query": "what is Monzo Flex and how does it work", "expected": ["/flex"]},
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate retrieval precision against a hand-written query set.")
    parser.add_argument("--top-k", type=int, default=5, help="How many results per query count as a hit.")
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

    hits = 0
    print()
    print(f"Retrieval evaluation (top-{args.top_k})")
    print()
    for case in QUERY_SET:
        results = search(index, case["query"], top_k=args.top_k)
        urls = results["url"].tolist()
        hit = any(any(expected in url for url in urls) for expected in case["expected"])
        hits += hit
        status = "HIT " if hit else "MISS"
        print(f"[{status}] {case['query']}")
        if not hit:
            print(f"         expected one of: {case['expected']}")
            print(f"         got: {urls}")

    total = len(QUERY_SET)
    print()
    print(f"Hit rate: {hits}/{total} ({100 * hits / total:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
