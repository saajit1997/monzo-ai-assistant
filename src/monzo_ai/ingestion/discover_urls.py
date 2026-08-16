"""Phase 1 pipeline orchestration: sitemap discovery -> normalise -> dedupe
-> filter -> categorise -> validate -> CSV.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pandas as pd
import yaml
from pydantic import BaseModel

from monzo_ai.ingestion import filters
from monzo_ai.ingestion.sitemap import SitemapDiscoveryConfig, discover_sitemap_urls
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "sources.yaml"

CSV_COLUMNS = ["url", "domain", "path", "category", "include_in_mvp", "discovered_from", "discovered_at"]


class URLRecord(BaseModel):
    """One validated row of the Phase 1 URL inventory."""

    url: str
    domain: str
    path: str
    category: str
    include_in_mvp: bool
    discovered_from: str
    discovered_at: str


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not config:
        raise ValueError(f"Config file {config_path} is empty or invalid")
    return config


def _build_sitemap_config(config: dict[str, Any]) -> SitemapDiscoveryConfig:
    monzo_cfg = config["monzo"]
    crawler_cfg = config["crawler"]
    return SitemapDiscoveryConfig(
        robots_url=monzo_cfg["robots_url"],
        fallback_sitemap_url=monzo_cfg["fallback_sitemap_url"],
        user_agent=crawler_cfg["user_agent"],
        timeout_seconds=crawler_cfg.get("request_timeout_seconds", 10.0),
        request_delay_seconds=crawler_cfg.get("request_delay_seconds", 0.4),
        max_sitemap_recursion_depth=crawler_cfg.get("max_sitemap_recursion_depth", 5),
        max_retries=crawler_cfg.get("max_retries", 3),
        retry_backoff_seconds=crawler_cfg.get("retry_backoff_seconds", 1.0),
    )


def discover(config: dict[str, Any], limit: int | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Runs the full Phase 1 pipeline and returns (inventory, run stats).

    Raises RuntimeError if sitemap discovery yields zero raw URLs, so the
    caller can fail loudly (non-zero exit) instead of writing an empty CSV.
    """
    sitemap_config = _build_sitemap_config(config)

    with httpx.Client(headers={"User-Agent": sitemap_config.user_agent}, timeout=sitemap_config.timeout_seconds) as client:
        discovered = discover_sitemap_urls(client, sitemap_config)

    if not discovered:
        raise RuntimeError("Sitemap discovery returned zero URLs; nothing to write.")

    total_discovered = len(discovered)
    if limit is not None:
        discovered = discovered[:limit]

    monzo_cfg = config["monzo"]
    strip_params = config.get("normalisation", {}).get("strip_query_params", [])
    allowed_domains = monzo_cfg.get("allowed_domains", ["monzo.com"])

    seen: dict[str, dict[str, str]] = {}
    duplicates = 0
    for entry in discovered:
        normalised = filters.normalise_url(entry.url, strip_params)
        if normalised is None or not filters.domain_allowed(normalised, allowed_domains):
            continue
        if normalised in seen:
            duplicates += 1
            continue
        seen[normalised] = {"url": normalised, "discovered_from": entry.discovered_from}

    logger.info("Removed %d duplicate(s)", duplicates)

    categories_cfg = config.get("categories")
    mvp_categories = config.get("mvp_include_categories")
    legal_keywords = config.get("legal_mvp_keywords")
    excluded_extensions = config.get("excluded_extensions")
    excluded_patterns = config.get("excluded_path_patterns")

    discovered_at = datetime.now(timezone.utc).isoformat()

    records: list[dict[str, Any]] = []
    for item in seen.values():
        url = item["url"]
        parsed = urlsplit(url)
        category = filters.categorise_url(url, categories_cfg)
        include = filters.determine_mvp_inclusion(
            url,
            category,
            mvp_include_categories=mvp_categories,
            legal_mvp_keywords=legal_keywords,
            excluded_extensions=excluded_extensions,
            excluded_path_patterns=excluded_patterns,
        )
        record = URLRecord(
            url=url,
            domain=parsed.netloc,
            path=parsed.path or "/",
            category=category,
            include_in_mvp=include,
            discovered_from=item["discovered_from"],
            discovered_at=discovered_at,
        )
        records.append(record.model_dump())

    logger.info("Categorised URLs")

    df = pd.DataFrame.from_records(records, columns=CSV_COLUMNS)
    stats = {
        "total_discovered": total_discovered,
        "duplicates_removed": duplicates,
        "final_count": len(df),
    }
    return df, stats


def save_csv(df: pd.DataFrame, output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
    except OSError as exc:
        raise RuntimeError(f"Failed to write URL inventory to {output_path}: {exc}") from exc
    logger.info("Saved URL inventory to %s", output_path)


def print_summary(df: pd.DataFrame, stats: dict[str, int], output_path: Path) -> None:
    included = int(df["include_in_mvp"].sum())
    category_counts = df["category"].value_counts()

    lines = [
        "",
        "Monzo URL discovery complete",
        "",
        f"Total discovered: {stats['total_discovered']:,}",
        f"Duplicates removed: {stats['duplicates_removed']:,}",
        f"Included in MVP: {included:,}",
        "",
        "Categories:",
    ]
    for category, count in category_counts.items():
        lines.append(f"{category}: {count:,}")
    lines.append("")
    lines.append(f"Saved: {output_path}")
    print("\n".join(lines))
