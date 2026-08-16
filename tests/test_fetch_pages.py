"""Phase 2 page-fetch tests. All HTTP calls are mocked via respx — no live
network access happens here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from monzo_ai.ingestion.fetch_pages import FetchPagesConfig, fetch_pages, load_manifest

ROBOTS_ALLOW_ALL = "User-agent: *\n"
ROBOTS_DISALLOW_SECRET = "User-agent: *\nDisallow: /secret/\n"


def _config(**overrides) -> FetchPagesConfig:
    defaults = dict(
        robots_url="https://example.com/robots.txt",
        user_agent="TestBot/0.1",
        timeout_seconds=5.0,
        request_delay_seconds=0.0,
        max_retries=2,
        retry_backoff_seconds=0.0,
    )
    defaults.update(overrides)
    return FetchPagesConfig(**defaults)


def _urls_df(rows: list[tuple[str, bool]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["url", "include_in_mvp"])


@respx.mock
def test_fetches_and_writes_html_and_manifest(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW_ALL))
    respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="<html>card</html>"))
    respx.get("https://example.com/pricing").mock(return_value=httpx.Response(200, html="<html>pricing</html>"))

    urls_df = _urls_df([("https://example.com/help/card", True), ("https://example.com/pricing", True)])
    output_dir = tmp_path / "pages"
    manifest_path = tmp_path / "manifest.csv"

    manifest, stats = fetch_pages(urls_df, output_dir, manifest_path, _config())

    assert stats == {"total_targets": 2, "fetched": 2, "skipped_cached": 0, "skipped_robots": 0, "failed": 0}
    assert len(manifest) == 2
    assert set(manifest["success"]) == {True}
    assert set(manifest["status_code"]) == {200}

    card_row = manifest.loc[manifest["url"] == "https://example.com/help/card"].iloc[0]
    expected_hash = hashlib.sha256(b"<html>card</html>").hexdigest()
    assert card_row["content_hash"] == expected_hash
    assert card_row["content_length"] == len(b"<html>card</html>")
    saved_file = output_dir / Path(card_row["file_path"]).name
    assert saved_file.read_bytes() == b"<html>card</html>"

    assert manifest_path.exists()
    reloaded = load_manifest(manifest_path)
    assert len(reloaded) == 2


@respx.mock
def test_ignores_urls_not_flagged_for_mvp(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW_ALL))
    included_route = respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="ok"))

    urls_df = _urls_df([("https://example.com/help/card", True), ("https://example.com/blog/post", False)])

    manifest, stats = fetch_pages(urls_df, tmp_path / "pages", tmp_path / "manifest.csv", _config())

    assert included_route.call_count == 1
    assert stats["total_targets"] == 1
    assert list(manifest["url"]) == ["https://example.com/help/card"]


@respx.mock
def test_reruns_skip_already_successfully_fetched_urls(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW_ALL))
    route = respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="ok"))

    urls_df = _urls_df([("https://example.com/help/card", True)])
    output_dir, manifest_path = tmp_path / "pages", tmp_path / "manifest.csv"

    fetch_pages(urls_df, output_dir, manifest_path, _config())
    assert route.call_count == 1

    _, stats = fetch_pages(urls_df, output_dir, manifest_path, _config())

    assert route.call_count == 1  # not re-fetched
    assert stats["skipped_cached"] == 1
    assert stats["fetched"] == 0


@respx.mock
def test_force_flag_refetches_cached_urls(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW_ALL))
    route = respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="ok"))

    urls_df = _urls_df([("https://example.com/help/card", True)])
    output_dir, manifest_path = tmp_path / "pages", tmp_path / "manifest.csv"

    fetch_pages(urls_df, output_dir, manifest_path, _config())
    assert route.call_count == 1

    _, stats = fetch_pages(urls_df, output_dir, manifest_path, _config(), force=True)

    assert route.call_count == 2  # re-fetched despite being cached
    assert stats["fetched"] == 1
    assert stats["skipped_cached"] == 0


@respx.mock
def test_4xx_response_recorded_without_crashing_the_batch(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW_ALL))
    respx.get("https://example.com/missing").mock(return_value=httpx.Response(404, text="not found"))
    respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="ok"))

    urls_df = _urls_df([("https://example.com/missing", True), ("https://example.com/help/card", True)])

    manifest, stats = fetch_pages(urls_df, tmp_path / "pages", tmp_path / "manifest.csv", _config())

    assert stats["failed"] == 1
    assert stats["fetched"] == 1

    missing_row = manifest.loc[manifest["url"] == "https://example.com/missing"].iloc[0]
    assert missing_row["success"] == False
    assert missing_row["status_code"] == 404
    assert missing_row["error"] == "HTTP 404"

    ok_row = manifest.loc[manifest["url"] == "https://example.com/help/card"].iloc[0]
    assert ok_row["success"] == True


@respx.mock
def test_persistent_connection_failure_recorded_without_crashing_the_batch(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_ALLOW_ALL))
    respx.get("https://example.com/unreachable").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="ok"))

    urls_df = _urls_df([("https://example.com/unreachable", True), ("https://example.com/help/card", True)])

    manifest, stats = fetch_pages(urls_df, tmp_path / "pages", tmp_path / "manifest.csv", _config())

    assert stats["failed"] == 1
    assert stats["fetched"] == 1

    failed_row = manifest.loc[manifest["url"] == "https://example.com/unreachable"].iloc[0]
    assert failed_row["success"] == False
    assert pd.isna(failed_row["status_code"])


@respx.mock
def test_robots_txt_disallow_skips_url_without_fetching_it(tmp_path):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_DISALLOW_SECRET))
    secret_route = respx.get("https://example.com/secret/plan").mock(return_value=httpx.Response(200, html="shh"))
    respx.get("https://example.com/help/card").mock(return_value=httpx.Response(200, html="ok"))

    urls_df = _urls_df([("https://example.com/secret/plan", True), ("https://example.com/help/card", True)])

    manifest, stats = fetch_pages(urls_df, tmp_path / "pages", tmp_path / "manifest.csv", _config())

    assert secret_route.call_count == 0
    assert stats["skipped_robots"] == 1
    assert stats["fetched"] == 1

    disallowed_row = manifest.loc[manifest["url"] == "https://example.com/secret/plan"].iloc[0]
    assert disallowed_row["success"] == False
    assert disallowed_row["error"] == "disallowed by robots.txt"
