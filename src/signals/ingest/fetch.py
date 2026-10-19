"""HTTP fetch helper: shared timeouts, UA, and bounded retries.

Commercial-vehicle newsrooms and regulators are behind aggressive CDNs, so a
single retry policy here keeps every parser honest about failures instead of
each one inventing its own.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from signals.config import get_settings


def build_client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        timeout=settings.http_timeout,
        follow_redirects=True,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/rss+xml, application/xml, application/json, text/html;q=0.9, */*;q=0.5",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=12),
    reraise=True,
)
def fetch(client: httpx.Client, url: str) -> httpx.Response:
    response = client.get(url)
    if response.status_code == 429 or response.status_code >= 500:
        response.raise_for_status()
    return response
