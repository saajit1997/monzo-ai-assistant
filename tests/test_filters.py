"""URL normalisation, categorisation, and MVP-inclusion tests.

These exercise pure functions against plain data — no filesystem or network
access, and no dependency on config/sources.yaml existing.
"""

from __future__ import annotations

import pytest

from monzo_ai.ingestion import filters


class TestNormaliseUrl:
    def test_utm_params_and_trailing_slash_normalise_to_the_same_key_as_clean(self):
        clean = filters.normalise_url("https://Monzo.com/help/card")
        dirty = filters.normalise_url(
            "https://Monzo.com/help/card/?utm_source=newsletter&utm_medium=email#section",
            strip_query_params=["utm_source", "utm_medium"],
        )
        assert clean == dirty == "https://monzo.com/help/card"

    def test_dedup_collapses_normalised_duplicates_to_one_row(self):
        variants = [
            "https://monzo.com/help/card",
            "https://MONZO.com/help/card/",
            "https://monzo.com/help/card?utm_source=x",
            "https://monzo.com/help/card/?utm_source=x&utm_medium=y#anchor",
        ]
        normalised = {filters.normalise_url(v, strip_query_params=["utm_source", "utm_medium"]) for v in variants}
        assert normalised == {"https://monzo.com/help/card"}

    def test_keeps_root_path_slash(self):
        assert filters.normalise_url("https://monzo.com/") == "https://monzo.com/"

    def test_drops_bare_query_marker_once_all_params_are_stripped(self):
        result = filters.normalise_url("https://monzo.com/help?utm_source=x", strip_query_params=["utm_source"])
        assert result == "https://monzo.com/help"

    def test_keeps_non_tracking_query_params(self):
        result = filters.normalise_url("https://monzo.com/search?q=overdraft", strip_query_params=["utm_source"])
        assert result == "https://monzo.com/search?q=overdraft"

    def test_rejects_non_http_scheme(self):
        assert filters.normalise_url("mailto:help@monzo.com") is None


class TestDomainAllowed:
    def test_exact_domain_allowed(self):
        assert filters.domain_allowed("https://monzo.com/help", ["monzo.com"])

    def test_subdomain_allowed(self):
        assert filters.domain_allowed("https://community.monzo.com/help", ["monzo.com"])

    def test_unrelated_domain_rejected(self):
        assert not filters.domain_allowed("https://example.com/help", ["monzo.com"])


class TestCategoriseUrl:
    @pytest.mark.parametrize(
        "url,expected_category",
        [
            ("https://monzo.com/help/lost-card", "help"),
            ("https://monzo.com/blog/some-post", "blog"),
            ("https://monzo.com/careers/engineering", "careers"),
            ("https://monzo.com/business-banking/pricing", "business"),
            ("https://monzo.com/current-account/joint-account", "product"),
            ("https://monzo.com/pricing/plus", "pricing"),
            ("https://monzo.com/legal/terms", "legal"),
            ("https://monzo.com/totally-unmatched-page", "other"),
        ],
    )
    def test_categorises_representative_urls(self, url, expected_category):
        assert filters.categorise_url(url) == expected_category

    def test_first_match_wins_in_config_order(self):
        # A path matching two categories' patterns resolves to whichever
        # category is listed first in the (ordered) mapping.
        categories = {"first": ["/foo"], "second": ["/foo/bar"]}
        assert filters.categorise_url("https://monzo.com/foo/bar", categories) == "first"

    def test_falls_back_to_default_categories_when_none_given(self):
        assert filters.categorise_url("https://monzo.com/help/x", categories=None) == "help"


class TestDetermineMvpInclusion:
    @pytest.mark.parametrize("category", ["help", "product", "security", "business"])
    def test_default_included_categories(self, category):
        assert filters.determine_mvp_inclusion("https://monzo.com/page", category) is True

    @pytest.mark.parametrize("category", ["careers", "corporate", "blog", "other"])
    def test_default_excluded_categories(self, category):
        assert filters.determine_mvp_inclusion("https://monzo.com/page", category) is False

    @pytest.mark.parametrize("url", ["https://monzo.com/help/card.png", "https://monzo.com/style.css"])
    def test_asset_urls_always_excluded_regardless_of_category(self, url):
        assert filters.determine_mvp_inclusion(url, "help") is False

    def test_legal_included_only_with_allow_listed_keyword(self):
        assert filters.determine_mvp_inclusion("https://monzo.com/legal/faqs", "legal", legal_mvp_keywords=["faq"]) is True
        assert (
            filters.determine_mvp_inclusion("https://monzo.com/legal/internal-policy", "legal", legal_mvp_keywords=["faq"])
            is False
        )

    def test_excluded_path_pattern_overrides_an_otherwise_included_category(self):
        assert (
            filters.determine_mvp_inclusion(
                "https://monzo.com/business/press/announcement", "business", excluded_path_patterns=["/press/"]
            )
            is False
        )
