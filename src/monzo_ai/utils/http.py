"""Shared HTTP fetch-with-retry helper.

Used by every ingestion module that talks to the network (sitemap
discovery, page fetching, ...) so retry/backoff behaviour stays consistent
without each module reimplementing it.
"""

from __future__ import annotations

import time

import httpx

from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)


def fetch_with_retries(
    client: httpx.Client,
    url: str,
    max_retries: int = 3,
    retry_backoff_seconds: float = 1.0,
    method: str = "GET",
) -> httpx.Response | None:
    """Requests url, retrying transient failures with linear backoff.

    Connection errors/timeouts and 5xx server errors are treated as
    transient and retried up to max_retries times. A 4xx client error is
    returned immediately without retrying — it won't succeed on a retry, and
    the caller can inspect response.status_code to see what happened.

    Returns None only if every attempt failed to get a response at all
    (persistent connection failure/timeout). Otherwise returns the last
    response received, even if it's a non-2xx error.
    """
    last_exc: Exception | None = None
    last_response: httpx.Response | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = client.request(method, url)
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning("Request to %s failed (attempt %d/%d): %s", url, attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(retry_backoff_seconds * attempt)
            continue

        last_response = response
        if response.status_code < 500:
            return response

        logger.warning("Server error %d from %s (attempt %d/%d)", response.status_code, url, attempt, max_retries)
        if attempt < max_retries:
            time.sleep(retry_backoff_seconds * attempt)

    if last_response is not None:
        return last_response
    logger.error("Giving up on %s after %d attempt(s): %s", url, max_retries, last_exc)
    return None
