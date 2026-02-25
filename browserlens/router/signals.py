"""Signal extractors — fast, cheap page characterisation for the router."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.async_api import Page

from browserlens.core.types import PageSignals

# Interactive element selectors (used for a11y coverage calculation)
_INTERACTIVE_SELECTORS = (
    "a[href], button, input, select, textarea, "
    "[role='button'], [role='link'], [role='checkbox'], "
    "[role='radio'], [role='combobox'], [role='listbox'], "
    "[role='menuitem'], [role='tab'], [role='switch']"
)

_PAGE_TYPE_PATTERNS: dict[str, list[str]] = {
    "form": ["/login", "/signup", "/register", "/checkout", "/contact", "/form"],
    "dashboard": ["/dashboard", "/admin", "/analytics", "/metrics", "/stats"],
    "article": ["/article", "/blog", "/post", "/news", "/wiki"],
    "search": ["/search", "/results", "/find", "/query"],
}


class SignalExtractor:
    """Collects cheap page signals used by the AdaptiveRouter."""

    async def extract(self, page: Page) -> PageSignals:
        url = page.url
        signals = PageSignals(url=url)

        # Run independent checks in parallel via JS evaluation
        results = await page.evaluate("""() => {
            const interactive = document.querySelectorAll(
                'a[href], button, input, select, textarea, ' +
                '[role="button"], [role="link"], [role="checkbox"], ' +
                '[role="radio"], [role="combobox"], [role="listbox"], ' +
                '[role="menuitem"], [role="tab"], [role="switch"]'
            );

            // DOM complexity
            const allNodes = document.querySelectorAll('*');
            let maxDepth = 0;
            let totalChildren = 0;
            let nodeCount = allNodes.length;
            for (const el of allNodes) {
                let depth = 0;
                let cur = el;
                while (cur.parentElement) { depth++; cur = cur.parentElement; }
                if (depth > maxDepth) maxDepth = depth;
                totalChildren += el.children.length;
            }

            // Canvas / WebGL detection
            const canvases = document.querySelectorAll('canvas, [data-canvas]');
            let hasWebGL = false;
            for (const c of canvases) {
                if (c.tagName === 'CANVAS') {
                    try {
                        if (c.getContext('webgl') || c.getContext('webgl2')) {
                            hasWebGL = true;
                        }
                    } catch (_) {}
                }
            }

            return {
                interactiveCount: interactive.length,
                nodeCount,
                maxDepth,
                avgChildren: nodeCount > 0 ? totalChildren / nodeCount : 0,
                hasCanvas: canvases.length > 0,
                hasWebGL,
            };
        }""")

        signals.has_canvas = results["hasCanvas"]
        signals.has_webgl = results["hasWebGL"]
        signals.dom_node_count = results["nodeCount"]
        signals.dom_max_depth = results["maxDepth"]
        signals.dom_avg_children = results["avgChildren"]

        # A11y coverage: ratio of interactive elements that have accessible names
        signals.a11y_coverage = await self._compute_a11y_coverage(
            page, results["interactiveCount"]
        )

        # Page type from URL heuristic
        signals.page_type = self._classify_page_type(url)

        return signals

    async def _compute_a11y_coverage(self, page: Page, total_interactive: int) -> float:
        if total_interactive == 0:
            return 1.0

        named_count: int = await page.evaluate("""() => {
            const els = document.querySelectorAll(
                'a[href], button, input, select, textarea, ' +
                '[role="button"], [role="link"], [role="checkbox"], ' +
                '[role="radio"], [role="combobox"], [role="listbox"], ' +
                '[role="menuitem"], [role="tab"], [role="switch"]'
            );
            let named = 0;
            for (const el of els) {
                const label =
                    el.getAttribute('aria-label') ||
                    el.getAttribute('aria-labelledby') ||
                    el.getAttribute('title') ||
                    el.getAttribute('placeholder') ||
                    el.innerText?.trim() ||
                    el.value?.trim();
                if (label) named++;
            }
            return named;
        }""")

        return min(named_count / total_interactive, 1.0)

    def _classify_page_type(self, url: str) -> str:
        path = urlparse(url).path.lower()
        for page_type, patterns in _PAGE_TYPE_PATTERNS.items():
            if any(p in path for p in patterns):
                return page_type
        return "unknown"
