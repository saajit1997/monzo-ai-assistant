"""Robots.txt + sitemap discovery.

Resolves the set of URLs a site's sitemap(s) advertise, following robots.txt
Sitemap: directives (falling back to a configured default), recursively
descending into sitemap indexes up to a configurable depth, and guarding
against cyclic/duplicate sitemap references with a visited-set.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import defusedxml.ElementTree as safe_ET
import httpx

from monzo_ai.utils.http import fetch_with_retries
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SitemapDiscoveryConfig:
    robots_url: str
    fallback_sitemap_url: str
    user_agent: str
    timeout_seconds: float = 10.0
    request_delay_seconds: float = 0.4
    max_sitemap_recursion_depth: int = 5
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class DiscoveredUrl:
    url: str
    discovered_from: str


def _fetch_ok(client: httpx.Client, url: str, config: SitemapDiscoveryConfig) -> httpx.Response | None:
    """Fetches url via the shared retry helper, treating any non-2xx response
    (after retries) the same as a total failure — sitemap/robots.txt content
    is either there or it isn't, there's nothing useful to do with a 404/500
    body here.
    """
    response = fetch_with_retries(client, url, max_retries=config.max_retries, retry_backoff_seconds=config.retry_backoff_seconds)
    if response is None:
        return None
    if not (200 <= response.status_code < 300):
        logger.error("Giving up on %s: HTTP %d", url, response.status_code)
        return None
    return response


def fetch_robots_parser(client: httpx.Client, robots_url: str, max_retries: int = 3, retry_backoff_seconds: float = 1.0) -> RobotFileParser | None:
    """Fetches and parses robots.txt. Returns None if it couldn't be fetched."""
    logger.info("Fetching robots.txt")
    response = fetch_with_retries(client, robots_url, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)
    if response is None or not (200 <= response.status_code < 300):
        return None
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser


def _get_robots_sitemaps(client: httpx.Client, config: SitemapDiscoveryConfig) -> tuple[list[str], float]:
    """Returns (sitemap URLs listed in robots.txt, effective crawl delay in seconds)."""
    parser = fetch_robots_parser(client, config.robots_url, config.max_retries, config.retry_backoff_seconds)
    if parser is None:
        return [], config.request_delay_seconds

    sitemaps = list(parser.site_maps() or [])
    robots_delay = parser.crawl_delay("*") or 0.0
    effective_delay = max(config.request_delay_seconds, float(robots_delay))
    if robots_delay and robots_delay > config.request_delay_seconds:
        logger.info(
            "robots.txt Crawl-delay (%.1fs) exceeds configured delay (%.1fs); using %.1fs",
            robots_delay,
            config.request_delay_seconds,
            effective_delay,
        )
    return sitemaps, effective_delay


def _parse_sitemap_xml(content: bytes) -> tuple[list[str], list[str]]:
    """Parses sitemap XML content, returning (page URLs, child sitemap URLs).

    Handles both <urlset> (leaf sitemap of pages) and <sitemapindex> (list of
    further sitemaps) documents, ignoring XML namespace prefixes.
    """
    root = safe_ET.fromstring(content)
    root_tag = root.tag.rsplit("}", 1)[-1]

    locs = [el.text.strip() for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "loc" and el.text and el.text.strip()]

    if root_tag == "sitemapindex":
        return [], locs
    if root_tag == "urlset":
        return locs, []
    raise ValueError(f"Unrecognised sitemap root element: <{root_tag}>")


def discover_sitemap_urls(client: httpx.Client, config: SitemapDiscoveryConfig) -> list[DiscoveredUrl]:
    """Discovers all page URLs reachable from a site's sitemap(s).

    Follows robots.txt Sitemap: directives (or config.fallback_sitemap_url if
    none are listed), recursing into nested sitemap indexes up to
    config.max_sitemap_recursion_depth. A visited-set prevents infinite loops
    on cyclic or duplicate sitemap references.
    """
    sitemaps, delay = _get_robots_sitemaps(client, config)

    if sitemaps:
        logger.info("Found %d sitemap(s) in robots.txt", len(sitemaps))
    else:
        logger.warning("No sitemaps listed in robots.txt; falling back to %s", config.fallback_sitemap_url)
        sitemaps = [config.fallback_sitemap_url]

    visited: set[str] = set()
    discovered: list[DiscoveredUrl] = []

    def _resolve(sitemap_url: str, depth: int) -> None:
        if sitemap_url in visited:
            logger.debug("Already visited %s; skipping (cycle guard)", sitemap_url)
            return
        visited.add(sitemap_url)

        if depth > config.max_sitemap_recursion_depth:
            logger.warning(
                "Max sitemap recursion depth (%d) exceeded at %s; stopping descent",
                config.max_sitemap_recursion_depth,
                sitemap_url,
            )
            return

        logger.info("Fetching sitemap%s: %s", " index" if depth == 1 else "", sitemap_url)
        response = _fetch_ok(client, sitemap_url, config)
        if response is None:
            return

        try:
            urls, child_sitemaps = _parse_sitemap_xml(response.content)
        except ValueError as exc:
            logger.error("Malformed sitemap XML at %s: %s", sitemap_url, exc)
            return

        if not urls and not child_sitemaps:
            logger.warning("Empty sitemap at %s (no <loc> entries found)", sitemap_url)

        for url in urls:
            discovered.append(DiscoveredUrl(url=url, discovered_from=sitemap_url))

        if child_sitemaps:
            logger.info("Resolved %d nested sitemap(s) at depth %d (from %s)", len(child_sitemaps), depth, sitemap_url)

        for child in child_sitemaps:
            if delay > 0:
                time.sleep(delay)
            _resolve(child, depth + 1)

    for sitemap_url in sitemaps:
        _resolve(sitemap_url, depth=1)

    logger.info("Found %d URL(s) across all sitemaps", len(discovered))
    return discovered
