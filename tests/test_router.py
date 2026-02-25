"""Tests for the AdaptiveRouter and its signal/strategy components."""

import pytest

from browserlens.core.types import PageSignals, RepresentationType
from browserlens.router.strategies import RepresentationStrategy


@pytest.fixture
def strategy():
    return RepresentationStrategy()


def make_signals(**kwargs) -> PageSignals:
    defaults = dict(
        url="https://example.com",
        has_canvas=False,
        has_webgl=False,
        a11y_coverage=0.9,
        dom_node_count=500,
        dom_max_depth=8,
        dom_avg_children=2.5,
        dynamic_content_ratio=0.0,
        page_type="unknown",
    )
    defaults.update(kwargs)
    return PageSignals(**defaults)


class TestRepresentationStrategy:
    def test_high_a11y_coverage_returns_a11y_tree(self, strategy):
        signals = make_signals(a11y_coverage=0.9)
        assert strategy.select(signals) == RepresentationType.A11Y_TREE

    def test_canvas_low_a11y_returns_hybrid(self, strategy):
        signals = make_signals(has_canvas=True, a11y_coverage=0.3)
        assert strategy.select(signals) == RepresentationType.HYBRID

    def test_webgl_low_a11y_returns_hybrid(self, strategy):
        signals = make_signals(has_webgl=True, a11y_coverage=0.4)
        assert strategy.select(signals) == RepresentationType.HYBRID

    def test_moderate_a11y_small_dom_returns_distilled_dom(self, strategy):
        signals = make_signals(a11y_coverage=0.6, dom_node_count=800)
        assert strategy.select(signals) == RepresentationType.DISTILLED_DOM

    def test_large_dom_moderate_a11y_returns_hybrid(self, strategy):
        signals = make_signals(a11y_coverage=0.6, dom_node_count=3000)
        assert strategy.select(signals) == RepresentationType.HYBRID

    def test_very_low_a11y_returns_vision(self, strategy):
        signals = make_signals(a11y_coverage=0.1, has_canvas=False)
        assert strategy.select(signals) == RepresentationType.VISION

    def test_canvas_high_a11y_returns_a11y_tree(self, strategy):
        # Canvas present but a11y is good — no need for vision
        signals = make_signals(has_canvas=True, a11y_coverage=0.85)
        assert strategy.select(signals) == RepresentationType.A11Y_TREE


class TestPageSignals:
    def test_origin_extraction(self):
        signals = make_signals(url="https://app.example.com/dashboard?tab=1")
        assert signals.origin == "https://app.example.com"

    def test_origin_with_port(self):
        signals = make_signals(url="http://localhost:3000/page")
        assert signals.origin == "http://localhost:3000"
