"""AdaptiveRouter — picks the cheapest sufficient representation per page."""

from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlparse

from playwright.async_api import Page

from browserlens.core.types import PageSignals, RepresentationType
from browserlens.router.signals import SignalExtractor
from browserlens.router.strategies import RepresentationStrategy

# Cache TTL for origin-level signal caching (seconds)
_CACHE_TTL = 60.0


class AdaptiveRouter:
    """
    Runs fast page signals and selects the best representation type.
    Signal results are cached per URL origin for _CACHE_TTL seconds.
    """

    def __init__(self, *, override: Callable[[PageSignals], RepresentationType] | None = None) -> None:
        self._extractor = SignalExtractor()
        self._strategy = RepresentationStrategy()
        self._override = override
        # origin → (signals, timestamp)
        self._cache: dict[str, tuple[PageSignals, float]] = {}

    async def select(self, page: Page) -> RepresentationType:
        """Extract signals (with caching) and return the chosen representation type."""
        signals = await self._get_signals(page)

        if self._override is not None:
            return self._override(signals)

        return self._strategy.select(signals)

    async def get_signals(self, page: Page) -> PageSignals:
        """Public access to the signals (useful for debugging / benchmarks)."""
        return await self._get_signals(page)

    async def _get_signals(self, page: Page) -> PageSignals:
        origin = self._origin(page.url)
        now = time.monotonic()

        if origin in self._cache:
            cached_signals, ts = self._cache[origin]
            if now - ts < _CACHE_TTL:
                # Return cached signals but update URL (may have changed within same origin)
                cached_signals.url = page.url
                return cached_signals

        signals = await self._extractor.extract(page)
        self._cache[origin] = (signals, now)
        return signals

    def invalidate_cache(self, url: str | None = None) -> None:
        """Invalidate cached signals. Pass a URL to clear just that origin."""
        if url is None:
            self._cache.clear()
        else:
            self._cache.pop(self._origin(url), None)

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
