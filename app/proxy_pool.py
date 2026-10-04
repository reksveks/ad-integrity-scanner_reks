"""Rotating proxy pool sourced from the proxyscrape free-proxy-list feed.

Usage
-----
    from app.proxy_pool import get_proxy_pool
    pool = await get_proxy_pool()   # cached singleton
    url  = pool.pick()              # e.g. "http://1.2.3.4:8080" or None

The pool is fetched once at first use and refreshed every `refresh_interval`
seconds in the background.  If the feed is unreachable the pool returns None
so callers fall back to a direct (no-proxy) connection.

Security notes
--------------
* Free public proxies are untrusted third parties — do NOT send authenticated
  requests (API keys, cookies) through them.
* Only HTTP/HTTPS/SOCKS4/SOCKS5 protocols are accepted; any other value in the
  feed is silently skipped.
* Proxies are filtered by minimum uptime and maximum latency so obviously
  dead entries are discarded up-front, but callers should still handle
  per-request proxy failures gracefully.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

_FEED_URL = (
    "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list"
    "@main/proxies/all/data.json"
)

_VALID_PROTOCOLS = {"http", "https", "socks4", "socks5"}

# Defaults — overridden by Settings when build_proxy_pool() is used.
_DEFAULT_MIN_UPTIME = 30.0       # percent
_DEFAULT_MAX_LATENCY = 8_000.0   # ms
_DEFAULT_REFRESH = 3600          # seconds


def _entry_to_url(entry: dict) -> str | None:
    """Return a proxy URL string from a feed entry, or None if invalid."""
    protocol = str(entry.get("protocol", "")).lower()
    ip = str(entry.get("ip", "")).strip()
    port = entry.get("port")
    if protocol not in _VALID_PROTOCOLS or not ip or not port:
        return None
    return f"{protocol}://{ip}:{port}"


class ProxyPool:
    """Thread-safe rotating proxy pool with background refresh."""

    def __init__(
        self,
        feed_url: str = _FEED_URL,
        min_uptime: float = _DEFAULT_MIN_UPTIME,
        max_latency: float = _DEFAULT_MAX_LATENCY,
        refresh_interval: int = _DEFAULT_REFRESH,
    ) -> None:
        self._feed_url = feed_url
        self._min_uptime = min_uptime
        self._max_latency = max_latency
        self._refresh_interval = refresh_interval
        self._proxies: List[str] = []
        self._index: int = 0
        self._lock = asyncio.Lock()
        self._last_refresh: float = 0.0
        self._refresh_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Fetch the initial list and start the background refresh loop."""
        await self._refresh()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Cancel the background refresh task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            with asyncio.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

    def pick(self) -> str | None:
        """Return the next proxy URL in round-robin order, or None if empty."""
        if not self._proxies:
            return None
        url = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return url

    def pick_random(self) -> str | None:
        """Return a random proxy URL, or None if the pool is empty."""
        return random.choice(self._proxies) if self._proxies else None

    def remove(self, url: str) -> None:
        """Remove a proxy that has been found unreachable."""
        try:
            self._proxies.remove(url)
        except ValueError:
            pass

    @property
    def size(self) -> int:
        return len(self._proxies)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(self._feed_url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # network, JSON, or HTTP error
            logger.warning("proxy_pool: feed fetch failed: %s", exc)
            return

        if not isinstance(data, list):
            logger.warning("proxy_pool: unexpected feed format (not a list)")
            return

        fresh: list[str] = []
        for entry in data:
            uptime = entry.get("uptime_percent", 0) or 0
            latency = entry.get("latency_ms", 9_999_999) or 9_999_999
            if uptime < self._min_uptime:
                continue
            if latency > self._max_latency:
                continue
            url = _entry_to_url(entry)
            if url:
                fresh.append(url)

        async with self._lock:
            self._proxies = fresh
            self._index = 0
            self._last_refresh = time.monotonic()

        logger.info("proxy_pool: loaded %d proxies (filtered from %d)", len(fresh), len(data))

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            await self._refresh()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_pool: ProxyPool | None = None


async def get_proxy_pool() -> ProxyPool:
    """Return the module-level singleton, starting it if needed."""
    global _pool
    if _pool is None:
        from app.config import get_settings
        s = get_settings()
        _pool = ProxyPool(
            min_uptime=s.proxy_min_uptime,
            max_latency=s.proxy_max_latency_ms,
            refresh_interval=s.proxy_refresh_interval,
        )
        await _pool.start()
    return _pool
