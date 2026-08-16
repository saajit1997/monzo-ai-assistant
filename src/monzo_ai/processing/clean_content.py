"""Phase 3: turn raw HTML (Phase 2's output) into clean, structured text.

extract_page_content() is a pure function — HTML string in, structured dict
out — so it's testable against inline fixtures with no filesystem or network
dependency. clean_pages() is the orchestration layer that reads Phase 2's
manifest, loads each HTML file, and writes the combined result to Parquet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup
from pydantic import BaseModel

from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)

# Tags that are never page content, wherever they appear in the document.
_ALWAYS_STRIP_TAGS = ["script", "style", "noscript", "svg", "template", "iframe"]

# Tags that are boilerplate chrome (site nav/header/footer) when they appear
# *inside* the main content area — e.g. an in-page breadcrumb nav. The
# site-wide nav/header/footer already live outside <main> on every page we
# inspected, so this mainly catches stray in-content chrome.
_STRIP_TAGS_IN_CONTENT = ["nav", "header", "footer"]

# class/id substrings that mark cookie-consent banners, modals, etc.
_BOILERPLATE_MARKERS = ["cookie", "consent", "onetrust", "cookiebot"]

_HEADING_TAGS = ["h1", "h2", "h3", "h4"]

PROCESSED_COLUMNS = [
    "url",
    "category",
    "title",
    "meta_description",
    "headings",
    "body_text",
    "word_count",
    "published_at",
    "content_hash",
    "source_file",
    "extracted_at",
]


class PageContent(BaseModel):
    """One validated row of the Phase 3 cleaned-content table."""

    url: str
    category: str
    title: str
    meta_description: str
    headings: list[str]
    body_text: str
    word_count: int
    published_at: str | None
    content_hash: str
    source_file: str
    extracted_at: str


def _is_boilerplate_marked(tag) -> bool:
    classes = " ".join(tag.get("class", [])).lower()
    element_id = (tag.get("id") or "").lower()
    haystack = f"{classes} {element_id}"
    return any(marker in haystack for marker in _BOILERPLATE_MARKERS)


def _clean_text_block(raw_text: str) -> str:
    lines = [line.strip() for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _meta_content(soup: BeautifulSoup, *, property_: str | None = None, name: str | None = None) -> str | None:
    attrs = {"property": property_} if property_ else {"name": name}
    tag = soup.find("meta", attrs=attrs)
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _extract_published_at(soup: BeautifulSoup, content_root) -> str | None:
    time_tag = content_root.find("time") or soup.find("time")
    if time_tag and time_tag.get("datetime"):
        return time_tag["datetime"].strip()
    for prop in ("article:published_time", "article:modified_time", "og:updated_time"):
        value = _meta_content(soup, property_=prop)
        if value:
            return value
    return None


def extract_page_content(html: str, url: str) -> dict[str, Any]:
    """Parses raw HTML into a structured, boilerplate-stripped content dict.

    Strategy: every Monzo page we inspected has exactly one <main> tag
    holding the real content, with the site nav/header/footer and
    cookie-consent banner all living outside it. We use <main> as the
    content root (falling back to <body>, then the whole document, if it's
    missing) and additionally strip nav/header/footer/script/style/cookie
    markers found inside it defensively, since not every page template is
    guaranteed to follow the same layout.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(_ALWAYS_STRIP_TAGS):
        tag.decompose()

    content_root = soup.find("main") or soup.find("body") or soup

    published_at = _extract_published_at(soup, content_root)

    for tag in content_root.find_all(_STRIP_TAGS_IN_CONTENT):
        tag.decompose()
    for tag in content_root.find_all(True):
        # decompose()ing a parent invalidates its descendants (their .attrs
        # becomes None); skip anything already torn down by an earlier
        # iteration rather than crashing on nested boilerplate markers.
        if tag.decomposed:
            continue
        if _is_boilerplate_marked(tag):
            tag.decompose()

    headings = [h.get_text(strip=True) for h in content_root.find_all(_HEADING_TAGS)]
    headings = [h for h in headings if h]

    body_text = _clean_text_block(content_root.get_text(separator="\n"))

    title = _meta_content(soup, property_="og:title") or (soup.title.get_text(strip=True) if soup.title else "")
    meta_description = _meta_content(soup, property_="og:description") or _meta_content(soup, name="description") or ""

    return {
        "title": title,
        "meta_description": meta_description,
        "headings": headings,
        "body_text": body_text,
        "word_count": len(body_text.split()),
        "published_at": published_at,
    }


def load_manifest_and_urls(manifest_path: Path, urls_path: Path) -> pd.DataFrame:
    """Joins Phase 2's fetch manifest to Phase 1's URL inventory on url,
    keeping only successfully-fetched rows.
    """
    manifest = pd.read_csv(manifest_path)
    urls = pd.read_csv(urls_path)
    manifest = manifest[manifest["success"] == True]  # noqa: E712
    merged = manifest.merge(urls[["url", "category"]], on="url", how="left")
    merged["category"] = merged["category"].fillna("other")
    return merged


def clean_pages(
    fetched_df: pd.DataFrame,
    output_path: Path,
    limit: int | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Reads and cleans every fetched HTML file described in fetched_df
    (as returned by load_manifest_and_urls), writing the result to Parquet.

    Never raises on an individual page's failure (missing file, decode
    error) — records it in the run stats and continues.
    """
    rows = fetched_df.to_dict("records")
    if limit is not None:
        rows = rows[:limit]

    stats = {"total_targets": len(rows), "cleaned": 0, "failed": 0}
    records: list[dict[str, Any]] = []
    extracted_at = datetime.now(timezone.utc).isoformat()

    for row in rows:
        file_path = Path(row["file_path"])
        if not file_path.exists():
            logger.error("Skipping %s: HTML file not found at %s", row["url"], file_path)
            stats["failed"] += 1
            continue

        try:
            html = file_path.read_text(encoding="utf-8", errors="replace")
            content = extract_page_content(html, row["url"])
        except Exception as exc:  # noqa: BLE001 - keep the batch going regardless of cause
            logger.error("Failed to clean %s: %s", row["url"], exc)
            stats["failed"] += 1
            continue

        record = PageContent(
            url=row["url"],
            category=row["category"],
            title=content["title"],
            meta_description=content["meta_description"],
            headings=content["headings"],
            body_text=content["body_text"],
            word_count=content["word_count"],
            published_at=content["published_at"],
            content_hash=row["content_hash"],
            source_file=str(file_path),
            extracted_at=extracted_at,
        )
        records.append(record.model_dump())
        stats["cleaned"] += 1

    df = pd.DataFrame.from_records(records, columns=PROCESSED_COLUMNS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    logger.info("Saved %d cleaned page(s) to %s", len(df), output_path)
    return df, stats


def print_clean_summary(stats: dict[str, int], output_path: Path) -> None:
    lines = [
        "",
        "Monzo content cleaning complete",
        "",
        f"Targets: {stats['total_targets']:,}",
        f"Cleaned: {stats['cleaned']:,}",
        f"Failed: {stats['failed']:,}",
        "",
        f"Saved: {output_path}",
    ]
    print("\n".join(lines))
