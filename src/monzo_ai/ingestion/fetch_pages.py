"""Phase 2 pipeline: fetch raw HTML for every include_in_mvp=True URL from
Phase 1's inventory (data/raw/monzo_urls.csv), respecting robots.txt and the
same rate-limiting rules as sitemap discovery.

Idempotent: re-running skips any URL that already has a successful fetch
recorded in the manifest, unless --force is passed. HTML bodies are stored
raw and untouched here — Phase 3 (content cleaning) is what parses them.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pandas as pd

from monzo_ai.ingestion.sitemap import fetch_robots_parser
from monzo_ai.utils.http import fetch_with_retries
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)

MANIFEST_COLUMNS = [
    "url",
    "file_path",
    "status_code",
    "success",
    "content_hash",
    "content_length",
    "fetched_at",
    "error",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class FetchPagesConfig:
    robots_url: str
    user_agent: str
    timeout_seconds: float = 10.0
    request_delay_seconds: float = 0.4
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0


def _slugify(path: str) -> str:
    slug = _SLUG_RE.sub("-", path.lower()).strip("-")
    return slug[:80]


def _file_path_for(url: str, output_dir: Path) -> Path:
    """Deterministic, collision-free filename for a URL: a readable slug of
    its path plus a short hash of the full URL (so e.g. /ie/security and
    /security don't collide, and re-running always resolves to the same
    file).
    """
    parsed = urlsplit(url)
    slug = _slugify(parsed.path) or "root"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return output_dir / f"{slug}-{digest}.html"


def load_manifest(manifest_path: Path) -> pd.DataFrame:
    if manifest_path.exists():
        df = pd.read_csv(manifest_path)
        if not df.empty:
            df["success"] = df["success"].astype(bool)
        return df
    return pd.DataFrame(columns=MANIFEST_COLUMNS)


def save_manifest(df: pd.DataFrame, manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(manifest_path, index=False)


def fetch_pages(
    urls_df: pd.DataFrame,
    output_dir: Path,
    manifest_path: Path,
    config: FetchPagesConfig,
    force: bool = False,
    limit: int | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Fetches raw HTML for every include_in_mvp=True URL in urls_df.

    Returns (updated manifest DataFrame, run stats). Never raises on an
    individual URL's failure — HTTP errors, timeouts, and robots.txt
    disallow rules are all recorded as a failed/skipped manifest row and the
    batch continues.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_manifest = load_manifest(manifest_path)
    rows: dict[str, dict[str, Any]] = {row["url"]: row for row in existing_manifest.to_dict("records")}
    already_ok = set(existing_manifest.loc[existing_manifest["success"], "url"]) if not existing_manifest.empty else set()

    targets = urls_df.loc[urls_df["include_in_mvp"], "url"].tolist()
    if limit is not None:
        targets = targets[:limit]

    stats = {"total_targets": len(targets), "fetched": 0, "skipped_cached": 0, "skipped_robots": 0, "failed": 0}

    with httpx.Client(headers={"User-Agent": config.user_agent}, timeout=config.timeout_seconds, follow_redirects=True) as client:
        robots_parser = fetch_robots_parser(client, config.robots_url, config.max_retries, config.retry_backoff_seconds)
        if robots_parser is None:
            logger.warning("Could not fetch robots.txt; proceeding without a robots.txt disallow check")

        first_request = True
        for url in targets:
            if not force and url in already_ok:
                stats["skipped_cached"] += 1
                continue

            if robots_parser is not None and not robots_parser.can_fetch(config.user_agent, url):
                logger.warning("Skipping %s: disallowed by robots.txt", url)
                rows[url] = {
                    "url": url,
                    "file_path": "",
                    "status_code": None,
                    "success": False,
                    "content_hash": "",
                    "content_length": 0,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "error": "disallowed by robots.txt",
                }
                stats["skipped_robots"] += 1
                continue

            if not first_request and config.request_delay_seconds > 0:
                time.sleep(config.request_delay_seconds)
            first_request = False

            response = fetch_with_retries(client, url, max_retries=config.max_retries, retry_backoff_seconds=config.retry_backoff_seconds)
            fetched_at = datetime.now(timezone.utc).isoformat()

            if response is None:
                rows[url] = {
                    "url": url,
                    "file_path": "",
                    "status_code": None,
                    "success": False,
                    "content_hash": "",
                    "content_length": 0,
                    "fetched_at": fetched_at,
                    "error": "request failed after retries (no response)",
                }
                stats["failed"] += 1
                logger.error("Failed to fetch %s: no response after retries", url)
                continue

            success = 200 <= response.status_code < 300
            content = response.content
            file_path = _file_path_for(url, output_dir)
            content_hash = hashlib.sha256(content).hexdigest()

            if success:
                file_path.write_bytes(content)
                stats["fetched"] += 1
                logger.info("Fetched %s (%d, %d bytes)", url, response.status_code, len(content))
            else:
                stats["failed"] += 1
                logger.error("Failed to fetch %s: HTTP %d", url, response.status_code)

            rows[url] = {
                "url": url,
                "file_path": str(file_path) if success else "",
                "status_code": response.status_code,
                "success": success,
                "content_hash": content_hash if success else "",
                "content_length": len(content),
                "fetched_at": fetched_at,
                "error": "" if success else f"HTTP {response.status_code}",
            }

    manifest_df = pd.DataFrame.from_records(list(rows.values()), columns=MANIFEST_COLUMNS)
    save_manifest(manifest_df, manifest_path)
    return manifest_df, stats


def print_fetch_summary(stats: dict[str, int], manifest_path: Path) -> None:
    lines = [
        "",
        "Monzo page fetch complete",
        "",
        f"Targets: {stats['total_targets']:,}",
        f"Fetched: {stats['fetched']:,}",
        f"Skipped (already cached): {stats['skipped_cached']:,}",
        f"Skipped (robots.txt disallow): {stats['skipped_robots']:,}",
        f"Failed: {stats['failed']:,}",
        "",
        f"Manifest: {manifest_path}",
    ]
    print("\n".join(lines))
