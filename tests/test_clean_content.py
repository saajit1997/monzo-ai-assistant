"""Phase 3 content-cleaning tests. Pure HTML parsing — no network, no
filesystem dependency for extract_page_content(); clean_pages() tests use
tmp_path for the small amount of file I/O it does.
"""

from __future__ import annotations

import pandas as pd

from monzo_ai.processing.clean_content import (
    clean_pages,
    extract_page_content,
    load_manifest_and_urls,
)

PAGE_WITH_MAIN = """
<html>
<head>
<title>Fallback Title</title>
<meta name="description" content="Fallback description">
<meta property="og:title" content="Business bank account">
<meta property="og:description" content="A fully-regulated UK current account.">
<script>console.log('tracking pixel');</script>
<style>.hero { color: red; }</style>
</head>
<body>
<nav>Home | Products | Help</nav>
<header>Monzo site header</header>
<div class="cookie-banner" id="onetrust-banner">
  We use cookies. <button>Accept</button>
</div>
<main>
  <nav aria-label="breadcrumb">Business / Account</nav>
  <h1>Business bank account</h1>
  <p>Open a business account in minutes.</p>
  <h2>Why Monzo Business</h2>
  <p>No monthly fees on the free plan.</p>
  <svg><path d="M0 0"/></svg>
</main>
<footer>Copyright Monzo 2026. Contact us.</footer>
</body>
</html>
"""

PAGE_WITHOUT_MAIN = """
<html>
<head><title>No Main Tag</title></head>
<body>
<nav>Home | Help</nav>
<div id="content">
  <h1>Help article</h1>
  <p>This page has no main tag.</p>
</div>
<footer>Footer text</footer>
</body>
</html>
"""

PAGE_WITH_TIME_TAG = """
<html><head><title>Dated article</title></head>
<body><main><h1>Dated article</h1>
<time datetime="2025-03-01T10:00:00Z">1 March 2025</time>
<p>Body text.</p></main></body></html>
"""

# Mirrors the real structure found on monzo.com/help/.../understanding-fees:
# <thead> with a blank corner cell, <tbody> rows of [row label, value...].
PAGE_WITH_FEE_TABLE = """
<html><head><title>Fees</title></head>
<body><main>
<h1>Understanding fees</h1>
<table>
<thead><tr><th></th><th>UK &amp; EEA</th><th>Outside EEA</th></tr></thead>
<tbody>
<tr><td>Monzo is not my main bank</td><td>400 fee-free</td><td>200 fee-free</td></tr>
<tr><td>Monzo is my main bank</td><td>Unlimited fee-free</td><td>200 fee-free</td></tr>
</tbody>
</table>
</main></body></html>
"""

# Mirrors the real structure found on monzo.com/savings-isas: SVG-icon-only
# headers (real label only in aria-label) and value cells that repeat the
# row label for their mobile card layout.
PAGE_WITH_COMPARISON_TABLE = """
<html><head><title>Savings</title></head>
<body><main>
<h1>Savings</h1>
<table>
<thead><tr>
<td></td>
<th aria-label="Monzo Free"><svg><title>Monzo Hot Coral card</title></svg></th>
<th aria-label="Monzo Extra £3 a month"><svg><title>Monzo Extra card</title></svg></th>
</tr></thead>
<tbody>
<tr>
<th><a aria-label="Learn more about Instant Access Savings Pot">Instant Access Savings Pot</a></th>
<td><span><a>Instant Access Savings Pot</a><span>2.75% AER (variable)</span></span></td>
<td><span><a>Instant Access Savings Pot</a><span>3.00% AER (variable)</span></span></td>
</tr>
</tbody>
</table>
</main></body></html>
"""


def test_strips_script_style_nav_header_footer_from_body_text():
    content = extract_page_content(PAGE_WITH_MAIN, "https://example.com/business-banking")

    assert "Open a business account in minutes." in content["body_text"]
    assert "No monthly fees on the free plan." in content["body_text"]

    assert "tracking pixel" not in content["body_text"]
    assert "color: red" not in content["body_text"]
    assert "Home | Products | Help" not in content["body_text"]
    assert "Monzo site header" not in content["body_text"]
    assert "Copyright Monzo 2026" not in content["body_text"]


def test_strips_cookie_banner_even_when_inside_main():
    html = PAGE_WITH_MAIN.replace("<main>", '<main><div class="cookie-banner">Accept our cookies</div>')
    content = extract_page_content(html, "https://example.com/business-banking")
    assert "Accept our cookies" not in content["body_text"]


def test_strips_nested_boilerplate_markers_without_crashing():
    # A cookie-consent wrapper containing its own cookie-marked child (e.g.
    # an inner "cookie-consent-buttons" div) — decompose()ing the outer div
    # must not blow up when find_all(True)'s pre-computed list later reaches
    # the now-invalidated inner one.
    html = PAGE_WITH_MAIN.replace(
        "<main>",
        '<main><div class="cookie-banner">Accept cookies'
        '<div class="cookie-consent-buttons">Accept all</div>'
        "</div>",
    )
    content = extract_page_content(html, "https://example.com/business-banking")
    assert "Accept cookies" not in content["body_text"]
    assert "Accept all" not in content["body_text"]


def test_strips_breadcrumb_nav_inside_main():
    content = extract_page_content(PAGE_WITH_MAIN, "https://example.com/business-banking")
    assert "Business / Account" not in content["body_text"]


def test_extracts_headings_in_document_order():
    content = extract_page_content(PAGE_WITH_MAIN, "https://example.com/business-banking")
    assert content["headings"] == ["Business bank account", "Why Monzo Business"]


def test_prefers_og_title_and_og_description_over_plain_tags():
    content = extract_page_content(PAGE_WITH_MAIN, "https://example.com/business-banking")
    assert content["title"] == "Business bank account"
    assert content["meta_description"] == "A fully-regulated UK current account."


def test_falls_back_to_title_tag_and_meta_description_without_og_tags():
    content = extract_page_content(PAGE_WITHOUT_MAIN, "https://example.com/help/article")
    assert content["title"] == "No Main Tag"
    assert content["meta_description"] == ""


def test_falls_back_to_body_when_no_main_tag_present():
    content = extract_page_content(PAGE_WITHOUT_MAIN, "https://example.com/help/article")
    assert "This page has no main tag." in content["body_text"]
    assert "Home | Help" not in content["body_text"]
    assert "Footer text" not in content["body_text"]


def test_extracts_published_at_from_time_tag():
    content = extract_page_content(PAGE_WITH_TIME_TAG, "https://example.com/dated")
    assert content["published_at"] == "2025-03-01T10:00:00Z"


def test_published_at_is_none_when_absent():
    content = extract_page_content(PAGE_WITH_MAIN, "https://example.com/business-banking")
    assert content["published_at"] is None


def test_table_values_stay_attached_to_their_row_and_column_labels():
    content = extract_page_content(PAGE_WITH_FEE_TABLE, "https://example.com/fees")

    assert "Monzo is not my main bank — UK & EEA: 400 fee-free; Outside EEA: 200 fee-free" in content["body_text"]
    assert "Monzo is my main bank — UK & EEA: Unlimited fee-free; Outside EEA: 200 fee-free" in content["body_text"]

    # the flattened table shouldn't leave bare, unlabelled values floating
    # around on their own line (the original get_text() bug this replaces)
    assert "\n400 fee-free\n" not in content["body_text"]


def test_table_header_prefers_aria_label_over_svg_only_visible_text():
    content = extract_page_content(PAGE_WITH_COMPARISON_TABLE, "https://example.com/savings-isas")
    assert "Monzo Free: 2.75% AER (variable)" in content["body_text"]
    assert "Monzo Extra £3 a month: 3.00% AER (variable)" in content["body_text"]


def test_table_strips_row_label_repeated_inside_value_cell():
    content = extract_page_content(PAGE_WITH_COMPARISON_TABLE, "https://example.com/savings-isas")
    # the raw cell text is "Instant Access Savings Pot2.75% AER (variable)"
    # (row label glued to the mobile rate span) -- the label prefix must be
    # stripped, not left concatenated onto the value
    assert "Pot2.75%" not in content["body_text"]
    assert "Instant Access Savings Pot — Monzo Free: 2.75% AER (variable)" in content["body_text"]


def test_word_count_matches_body_text_word_count():
    content = extract_page_content(PAGE_WITH_MAIN, "https://example.com/business-banking")
    assert content["word_count"] == len(content["body_text"].split())


def test_clean_pages_writes_parquet_and_records_stats(tmp_path):
    (tmp_path / "a.html").write_text(PAGE_WITH_MAIN, encoding="utf-8")
    (tmp_path / "b.html").write_text(PAGE_WITHOUT_MAIN, encoding="utf-8")

    fetched_df = pd.DataFrame(
        [
            {"url": "https://example.com/business-banking", "category": "business", "file_path": str(tmp_path / "a.html"), "content_hash": "hash-a"},
            {"url": "https://example.com/help/article", "category": "help", "file_path": str(tmp_path / "b.html"), "content_hash": "hash-b"},
        ]
    )
    output_path = tmp_path / "pages.parquet"

    df, stats = clean_pages(fetched_df, output_path)

    assert stats == {"total_targets": 2, "cleaned": 2, "failed": 0}
    assert output_path.exists()

    reloaded = pd.read_parquet(output_path)
    assert len(reloaded) == 2
    assert set(reloaded["url"]) == {"https://example.com/business-banking", "https://example.com/help/article"}
    assert reloaded.loc[reloaded["url"] == "https://example.com/business-banking", "category"].iloc[0] == "business"


def test_clean_pages_skips_missing_files_without_crashing(tmp_path):
    (tmp_path / "a.html").write_text(PAGE_WITH_MAIN, encoding="utf-8")

    fetched_df = pd.DataFrame(
        [
            {"url": "https://example.com/business-banking", "category": "business", "file_path": str(tmp_path / "a.html"), "content_hash": "hash-a"},
            {"url": "https://example.com/missing", "category": "help", "file_path": str(tmp_path / "does-not-exist.html"), "content_hash": "hash-b"},
        ]
    )

    df, stats = clean_pages(fetched_df, tmp_path / "pages.parquet")

    assert stats == {"total_targets": 2, "cleaned": 1, "failed": 1}
    assert list(df["url"]) == ["https://example.com/business-banking"]


def test_load_manifest_and_urls_joins_category_and_filters_to_successes(tmp_path):
    manifest_path = tmp_path / "manifest.csv"
    urls_path = tmp_path / "urls.csv"

    pd.DataFrame(
        [
            {"url": "https://example.com/a", "file_path": "a.html", "status_code": 200, "success": True, "content_hash": "h1"},
            {"url": "https://example.com/b", "file_path": "", "status_code": 404, "success": False, "content_hash": ""},
        ]
    ).to_csv(manifest_path, index=False)

    pd.DataFrame(
        [
            {"url": "https://example.com/a", "category": "help", "include_in_mvp": True},
            {"url": "https://example.com/b", "category": "help", "include_in_mvp": True},
        ]
    ).to_csv(urls_path, index=False)

    merged = load_manifest_and_urls(manifest_path, urls_path)

    assert list(merged["url"]) == ["https://example.com/a"]
    assert merged.iloc[0]["category"] == "help"
