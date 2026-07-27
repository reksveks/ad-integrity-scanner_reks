"""Persistent Playwright browser pool.

One Chromium process is launched for the worker's lifetime; each render gets a
fresh (isolated) context. A semaphore caps concurrent contexts so memory stays
bounded. Images/fonts/media are blocked to cut bandwidth + RAM while preserving
CSS/JS (needed for layout geometry and ad execution).
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from urllib.parse import urlsplit

from playwright.async_api import Browser, Page, Playwright, async_playwright
try:
    from playwright_stealth import stealth_async, StealthConfig as _StealthConfig
    # Only apply the patches that defeat headless-browser fingerprinting.
    # chrome_runtime and chrome_app are disabled because GPT and many CMPs test
    # for window.chrome / window.chrome.runtime; patching those stops consent
    # dialogs and the whole ad stack from initialising.
    # iframe_content_window is disabled because TCF consent frames communicate
    # through iframe content-window access — patching it breaks __tcfapi.
    _STEALTH_CFG = _StealthConfig(
        chrome_runtime=False,
        chrome_app=False,
        iframe_content_window=False,
    )
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

from app.config import get_settings
from app.render.instrument import INIT_JS
from app.ssrf import literal_host_blocked

_VIEWPORT = {"width": 1366, "height": 768}


def _make_route_handler(blocked_types: set[str]):
    async def _route_handler(route) -> None:
        # SSRF: block any subresource/redirect to a literal private/metadata host
        # (cheap, no DNS). Then drop configured heavy resource types.
        if literal_host_blocked(urlsplit(route.request.url).hostname):
            await route.abort()
        elif route.request.resource_type in blocked_types:
            await route.abort()
        else:
            await route.continue_()
    return _route_handler


class RenderPool:
    def __init__(self, concurrency: int = 2, blocked_types: set[str] | None = None,
                 headless: bool = True, channel: str = "chromium") -> None:
        self._concurrency = concurrency
        self._headless = headless
        self._channel = channel
        # Default blocks fonts/media only — images are kept so page-weight stays
        # accurate (a headline metric). Pass {'image','font','media'} to trade
        # accuracy for lower bandwidth.
        self._blocked = blocked_types if blocked_types is not None else {"font", "media"}
        self._route = _make_route_handler(self._blocked)
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._sem: contextlib.AbstractAsyncContextManager | None = None

    async def start(self) -> None:
        import asyncio
        self._pw = await async_playwright().start()
        # Keep Chromium's sandbox ON — we render hostile pages, so --no-sandbox
        # would remove the last barrier between a browser exploit and the host.
        launch_kwargs: dict = {"headless": self._headless, "args": ["--disable-dev-shm-usage"]}
        if self._channel and self._channel != "chromium":
            launch_kwargs["channel"] = self._channel
        self._browser = await self._pw.chromium.launch(**launch_kwargs)
        self._sem = asyncio.Semaphore(self._concurrency)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = self._pw = None

    async def _restart_browser(self) -> None:
        """Relaunch Chromium after a crash."""
        with contextlib.suppress(Exception):
            if self._browser:
                await self._browser.close()
        launch_kwargs: dict = {"headless": self._headless, "args": ["--disable-dev-shm-usage"]}
        if self._channel and self._channel != "chromium":
            launch_kwargs["channel"] = self._channel
        self._browser = await self._pw.chromium.launch(**launch_kwargs)

    @contextlib.asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        if not self._browser or not self._sem:
            raise RuntimeError("RenderPool not started")
        async with self._sem:
            # If the browser process crashed, relaunch it before attempting a new context.
            if not self._browser.is_connected():
                await self._restart_browser()
            context = await self._browser.new_context(
                user_agent=get_settings().user_agent,
                viewport=_VIEWPORT,
                java_script_enabled=True,
            )
            try:
                await context.route("**/*", self._route)
                page = await context.new_page()
                if get_settings().render_stealth and _STEALTH_AVAILABLE:
                    await stealth_async(page, _STEALTH_CFG)
                await page.add_init_script(INIT_JS)
                yield page
            finally:
                await context.close()
