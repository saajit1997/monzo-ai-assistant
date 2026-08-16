"""Sitemap discovery tests. All HTTP calls are mocked via respx — no live
network access happens here.
"""

from __future__ import annotations

import httpx
import respx

from monzo_ai.ingestion.sitemap import SitemapDiscoveryConfig, discover_sitemap_urls

ROBOTS_TXT = "User-agent: *\nDisallow: /admin/\nSitemap: https://example.com/sitemap.xml\n"

URLSET_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/help/card</loc></url>
  <url><loc>https://example.com/pricing</loc></url>
</urlset>"""

INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-a.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-b.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_A_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a/1</loc></url>
</urlset>"""

SITEMAP_B_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/b/1</loc></url>
</urlset>"""

DUPLICATE_INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-a.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-a.xml</loc></sitemap>
</sitemapindex>"""


def _config(**overrides) -> SitemapDiscoveryConfig:
    defaults = dict(
        robots_url="https://example.com/robots.txt",
        fallback_sitemap_url="https://example.com/sitemap.xml",
        user_agent="TestBot/0.1",
        timeout_seconds=5.0,
        request_delay_seconds=0.0,
        max_sitemap_recursion_depth=5,
        max_retries=2,
        retry_backoff_seconds=0.0,
    )
    defaults.update(overrides)
    return SitemapDiscoveryConfig(**defaults)


@respx.mock
def test_extracts_urls_from_a_urlset():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_TXT))
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, content=URLSET_XML))

    with httpx.Client() as client:
        discovered = discover_sitemap_urls(client, _config())

    urls = {d.url for d in discovered}
    assert urls == {"https://example.com/help/card", "https://example.com/pricing"}
    assert all(d.discovered_from == "https://example.com/sitemap.xml" for d in discovered)


@respx.mock
def test_recurses_into_nested_sitemap_index_and_merges_results():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_TXT))
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, content=INDEX_XML))
    respx.get("https://example.com/sitemap-a.xml").mock(return_value=httpx.Response(200, content=SITEMAP_A_XML))
    respx.get("https://example.com/sitemap-b.xml").mock(return_value=httpx.Response(200, content=SITEMAP_B_XML))

    with httpx.Client() as client:
        discovered = discover_sitemap_urls(client, _config())

    urls = {d.url for d in discovered}
    assert urls == {"https://example.com/a/1", "https://example.com/b/1"}


@respx.mock
def test_deduplicates_repeated_sitemap_references():
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_TXT))
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, content=DUPLICATE_INDEX_XML))
    sitemap_a_route = respx.get("https://example.com/sitemap-a.xml").mock(return_value=httpx.Response(200, content=SITEMAP_A_XML))

    with httpx.Client() as client:
        discovered = discover_sitemap_urls(client, _config())

    # The index lists sitemap-a.xml twice; the visited-set means it's only
    # fetched (and its URLs contributed) once.
    assert sitemap_a_route.call_count == 1
    assert [d.url for d in discovered] == ["https://example.com/a/1"]


@respx.mock
def test_respects_max_recursion_depth_and_breaks_cycles():
    # root (depth 1) -> sitemap2 (depth 2) -> sitemap3 (depth 3, beyond max=2)
    # root also cyclically references itself from sitemap2, which the
    # visited-set must catch without re-fetching.
    root_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
</sitemapindex>"""
    sitemap2_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap3.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap.xml</loc></sitemap>
</sitemapindex>"""

    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_TXT))
    root_route = respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, content=root_xml))
    respx.get("https://example.com/sitemap2.xml").mock(return_value=httpx.Response(200, content=sitemap2_xml))
    sitemap3_route = respx.get("https://example.com/sitemap3.xml").mock(return_value=httpx.Response(200, content=b""))

    with httpx.Client() as client:
        discovered = discover_sitemap_urls(client, _config(max_sitemap_recursion_depth=2))

    assert discovered == []
    assert root_route.call_count == 1  # never re-fetched via the cyclic back-reference
    assert sitemap3_route.call_count == 0  # never fetched: depth 3 exceeds max depth 2


@respx.mock
def test_falls_back_to_configured_sitemap_when_robots_lists_none():
    robots_no_sitemap = "User-agent: *\nDisallow: /admin/\n"
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=robots_no_sitemap))
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, content=URLSET_XML))

    with httpx.Client() as client:
        discovered = discover_sitemap_urls(client, _config())

    urls = {d.url for d in discovered}
    assert urls == {"https://example.com/help/card", "https://example.com/pricing"}
