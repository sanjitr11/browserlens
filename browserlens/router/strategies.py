"""Representation strategy selection logic."""

from __future__ import annotations

from browserlens.core.types import PageSignals, RepresentationType


class RepresentationStrategy:
    """
    V1 heuristic strategy. Upgradeable to a learned model by subclassing
    and overriding select().
    """

    def select(self, signals: PageSignals) -> RepresentationType:
        """
        Choose the cheapest representation that will give the LLM enough information.

        Priority order (cheapest first):
          1. A11Y_TREE       — text-only, most compact
          2. DISTILLED_DOM   — slightly richer than a11y, still text
          3. HYBRID          — a11y + targeted screenshot
          4. VISION          — full screenshot only (last resort)
        """
        # Canvas / WebGL with poor a11y → need vision component
        if (signals.has_canvas or signals.has_webgl) and signals.a11y_coverage < 0.5:
            return RepresentationType.HYBRID

        # High a11y coverage → pure a11y tree is sufficient
        if signals.a11y_coverage >= 0.8:
            return RepresentationType.A11Y_TREE

        # Moderate a11y + manageable DOM → distilled DOM bridges the gap
        if signals.dom_node_count < 2000 and signals.a11y_coverage >= 0.5:
            return RepresentationType.DISTILLED_DOM

        # Large or complex page with moderate a11y → hybrid gives structure + visuals
        if signals.a11y_coverage >= 0.3:
            return RepresentationType.HYBRID

        # Very poor a11y (e.g., pure canvas app) → fall back to vision
        return RepresentationType.VISION
