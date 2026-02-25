"""
Integration tests for the full BrowserLens pipeline (without a live browser).

These tests exercise the lens orchestrator by injecting mock extractors,
validating the end-to-end flow from observe() call to ObservationResult.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from browserlens.core.lens import BrowserLens
from browserlens.core.types import (
    PageState,
    RepresentationType,
    StateNode,
)
from browserlens.formatter.ref_manager import RefManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_node(ref, role, name, children=None):
    return StateNode(ref=ref, role=role, name=name, children=children or [])


def make_state(root, step=1, url="https://example.com"):
    return PageState(
        url=url,
        title="Test Page",
        representation_type=RepresentationType.A11Y_TREE,
        root=root,
        step=step,
    )


def mock_extractor(state: PageState):
    ext = AsyncMock()
    ext.extract = AsyncMock(return_value=state)
    return ext


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrowserLensObserve:
    @pytest.mark.asyncio
    async def test_first_observe_returns_full_state(self):
        lens = BrowserLens(enable_routing=False)

        root = make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "Click me"),
        ])
        state = make_state(root, step=1)

        # Patch the a11y extractor
        lens._extractors[RepresentationType.A11Y_TREE].extract = AsyncMock(return_value=state)

        page = MagicMock()
        page.url = "https://example.com"

        result = await lens.observe(page)

        assert result.step == 1
        assert result.delta.is_full_state
        assert "[FULL PAGE STATE" in result.formatted_text
        assert result.token_count > 0
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_second_observe_returns_delta(self):
        lens = BrowserLens(enable_routing=False)

        root1 = make_node("@e1", "main", "")
        state1 = make_state(root1, step=1)

        root2 = make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "New Button"),
        ])
        state2 = make_state(root2, step=2)

        extractor = lens._extractors[RepresentationType.A11Y_TREE]
        extractor.extract = AsyncMock(side_effect=[state1, state2])

        page = MagicMock()
        page.url = "https://example.com"

        await lens.observe(page)
        result = await lens.observe(page)

        assert result.step == 2
        assert not result.delta.is_full_state
        assert "[DELTA" in result.formatted_text
        assert result.delta.total_changes > 0

    @pytest.mark.asyncio
    async def test_reset_makes_next_observe_full_state(self):
        lens = BrowserLens(enable_routing=False)

        root = make_node("@e1", "main", "")
        state = make_state(root)

        extractor = lens._extractors[RepresentationType.A11Y_TREE]
        extractor.extract = AsyncMock(return_value=state)

        page = MagicMock()
        page.url = "https://example.com"

        await lens.observe(page)  # step 1
        lens.reset()
        result = await lens.observe(page)  # step 1 again after reset

        assert result.step == 1
        assert result.delta.is_full_state

    @pytest.mark.asyncio
    async def test_force_representation_bypasses_router(self):
        lens = BrowserLens(
            enable_routing=True,
            force_representation=RepresentationType.DISTILLED_DOM,
        )

        root = make_node("@e1", "main", "")
        state = make_state(root)
        state.representation_type = RepresentationType.DISTILLED_DOM

        extractor = lens._extractors[RepresentationType.DISTILLED_DOM]
        extractor.extract = AsyncMock(return_value=state)

        page = MagicMock()
        page.url = "https://example.com"

        result = await lens.observe(page)
        assert result.representation_type == RepresentationType.DISTILLED_DOM

    @pytest.mark.asyncio
    async def test_diffing_disabled_delta_is_none(self):
        lens = BrowserLens(enable_routing=False, enable_diffing=False)

        root = make_node("@e1", "main", "")
        state = make_state(root)

        extractor = lens._extractors[RepresentationType.A11Y_TREE]
        extractor.extract = AsyncMock(return_value=state)

        page = MagicMock()
        page.url = "https://example.com"

        result = await lens.observe(page)
        assert result.delta is None
        assert "[FULL PAGE STATE" in result.formatted_text
