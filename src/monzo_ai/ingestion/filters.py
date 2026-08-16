"""URL normalisation, categorisation, and MVP-inclusion rules.

All functions here take plain data (strings, lists, dicts) rather than
reading config/sources.yaml directly, so they can be unit tested without any
filesystem or network dependency. Callers (see discover_urls.py) load
sources.yaml once and pass the relevant slices in.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Small hard-coded fallback so tests (and ad-hoc callers) don't depend on
# config/sources.yaml existing on disk. The real category table lives in
# config/sources.yaml under the `categories:` key and should be preferred
# whenever available.
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "help": ["/help/"],
    # blog/business are checked before the generic "product"/"pricing"
    # patterns so a free-form slug (e.g. a blog post literally titled
    # "plans-cancelled-...") or a business-section subpage isn't
    # miscategorised by an incidental substring match further down.
    "blog": ["/blog"],
    "business": ["/business"],
    "legal": ["/legal"],
    "product": ["/current-account", "/joint-account/", "/savings", "/pots"],
    "security": ["/security", "/fraud"],
    "pricing": ["/pricing", "/plans"],
    "careers": ["/careers"],
    "corporate": ["/about", "/press/"],
}

DEFAULT_MVP_INCLUDE_CATEGORIES: list[str] = ["help", "product", "pricing", "security", "business"]
DEFAULT_LEGAL_MVP_KEYWORDS: list[str] = ["faq", "terms", "summary", "fee"]
DEFAULT_EXCLUDED_EXTENSIONS: list[str] = [
    ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico", ".pdf", ".xml", ".json",
]
DEFAULT_EXCLUDED_PATH_PATTERNS: list[str] = ["/press/", "/investors/"]


def normalise_url(url: str, strip_query_params: list[str] | None = None) -> str | None:
    """Normalises a URL for dedup/comparison purposes.

    - lowercases scheme and host
    - strips the fragment
    - strips tracking query params (exact match, or prefix match for entries
      ending in "*")
    - drops a bare "?" if no query params remain
    - removes a trailing slash except on the root path "/"

    Returns None for URLs that aren't http(s) or otherwise can't be parsed.
    """
    strip_query_params = strip_query_params or []
    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    def _is_stripped(key: str) -> bool:
        for entry in strip_query_params:
            if entry.endswith("*"):
                if key.startswith(entry[:-1]):
                    return True
            elif key == entry:
                return True
        return False

    kept_params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_stripped(k)]
    query = urlencode(kept_params)

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if path == "":
        path = "/"

    return urlunsplit((scheme, netloc, path, query, ""))


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    """True if the URL's host is one of allowed_domains or a subdomain of one."""
    host = urlsplit(url).netloc.lower()
    for domain in allowed_domains:
        domain = domain.lower()
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


def categorise_url(url: str, categories: dict[str, list[str]] | None = None) -> str:
    """Assigns a URL to exactly one category via ordered path-pattern matching.

    The first category (in dict iteration order) with a pattern that appears
    anywhere in the URL's path wins. Falls back to "other" if nothing matches.
    """
    categories = categories if categories else DEFAULT_CATEGORIES
    path = urlsplit(url).path

    for category, patterns in categories.items():
        for pattern in patterns:
            if pattern in path:
                return category
    return "other"


def is_excluded_asset(url: str, excluded_extensions: list[str] | None = None) -> bool:
    excluded_extensions = excluded_extensions if excluded_extensions is not None else DEFAULT_EXCLUDED_EXTENSIONS
    path = urlsplit(url).path.lower()
    return any(path.endswith(ext) for ext in excluded_extensions)


def matches_excluded_pattern(url: str, excluded_path_patterns: list[str] | None = None) -> bool:
    excluded_path_patterns = excluded_path_patterns if excluded_path_patterns is not None else DEFAULT_EXCLUDED_PATH_PATTERNS
    path = urlsplit(url).path
    return any(pattern in path for pattern in excluded_path_patterns)


def determine_mvp_inclusion(
    url: str,
    category: str,
    mvp_include_categories: list[str] | None = None,
    legal_mvp_keywords: list[str] | None = None,
    excluded_extensions: list[str] | None = None,
    excluded_path_patterns: list[str] | None = None,
) -> bool:
    """Applies the Phase 1 MVP-inclusion rule set.

    Asset files and explicitly excluded paths are always dropped regardless
    of category. "legal" URLs are included only if the path suggests
    customer-facing terms/FAQs (keyword allow-list). Every other category is
    included only if it's in mvp_include_categories.
    """
    mvp_include_categories = mvp_include_categories if mvp_include_categories is not None else DEFAULT_MVP_INCLUDE_CATEGORIES
    legal_mvp_keywords = legal_mvp_keywords if legal_mvp_keywords is not None else DEFAULT_LEGAL_MVP_KEYWORDS

    if is_excluded_asset(url, excluded_extensions):
        return False
    if matches_excluded_pattern(url, excluded_path_patterns):
        return False

    if category == "legal":
        path = urlsplit(url).path.lower()
        return any(keyword.lower() in path for keyword in legal_mvp_keywords)

    return category in mvp_include_categories
