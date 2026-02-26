"""Unit tests for ActionRecorder (patches SelectorGenerator)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from browserlens.compiler.recorder import ActionRecorder
from browserlens.compiler.types import (
    ActionType,
    ElementTarget,
    SelectorStrategy,
    WorkflowTrace,
)


def make_element_target(role="button", name="OK") -> ElementTarget:
    return ElementTarget(
        ref="@e1",
        role=role,
        name=name,
        selectors={SelectorStrategy.ROLE_NAME: f"{role}::{name}"},
        selector_priority=[SelectorStrategy.ROLE_NAME],
    )


def make_page(url="https://example.com") -> MagicMock:
    page = MagicMock()
    page.url = url
    return page


class TestActionRecorder:
    def setup_method(self):
        self.recorder = ActionRecorder()

    def _patch_generator(self, target: ElementTarget | None = None):
        """Context manager that patches SelectorGenerator.generate."""
        mock_target = target or make_element_target()
        return patch(
            "browserlens.compiler.recorder.SelectorGenerator.generate",
            new_callable=lambda: lambda *a, **kw: AsyncMock(return_value=mock_target)(),
        )

    # ------------------------------------------------------------------ start / stop

    def test_stop_returns_workflow_trace(self):
        self.recorder.start("test task")
        trace = self.recorder.stop()
        assert isinstance(trace, WorkflowTrace)
        assert trace.task_description == "test task"

    def test_start_clears_previous_state(self):
        self.recorder.start("first task")
        self.recorder.start("second task")
        trace = self.recorder.stop()
        assert trace.task_description == "second task"
        assert trace.steps == []

    def test_stop_without_start_raises(self):
        with pytest.raises(RuntimeError):
            self.recorder.stop()

    def test_record_without_start_raises(self):
        page = make_page()
        with pytest.raises(RuntimeError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                self.recorder.record(ActionType.CLICK, page, ref="@e1", role="button", name="OK")
            )

    # ------------------------------------------------------------------ NAVIGATE — no target

    async def test_navigate_no_target(self):
        self.recorder.start("navigate test")
        page = make_page()

        with patch(
            "browserlens.compiler.recorder.SelectorGenerator.generate"
        ) as mock_gen:
            mock_gen.return_value = make_element_target()
            step = await self.recorder.record(
                ActionType.NAVIGATE, page, value="https://example.com"
            )

        # generate() must NOT have been called for NAVIGATE
        mock_gen.assert_not_awaited()
        assert step.target is None
        assert step.action == ActionType.NAVIGATE
        self.recorder.stop()

    # ------------------------------------------------------------------ url_after filled

    async def test_url_after_filled_from_next_step(self):
        self.recorder.start("url test")
        page = make_page("https://page1.com")

        mock_target = make_element_target()
        gen_mock = AsyncMock(return_value=mock_target)
        with patch("browserlens.compiler.recorder.SelectorGenerator.generate", gen_mock):
            step0 = await self.recorder.record(
                ActionType.NAVIGATE, page, value="https://page1.com"
            )
            page.url = "https://page2.com"
            step1 = await self.recorder.record(
                ActionType.CLICK, page, ref="@e1", role="button", name="Next"
            )

        trace = self.recorder.stop()
        # step0.url_after should equal step1.url_before
        assert trace.steps[0].url_after == "https://page2.com"
        # last step has no url_after
        assert trace.steps[-1].url_after is None

    # ------------------------------------------------------------------ step indices

    async def test_steps_indexed_in_order(self):
        self.recorder.start("index test")
        page = make_page()

        mock_target = make_element_target()
        gen_mock = AsyncMock(return_value=mock_target)
        with patch("browserlens.compiler.recorder.SelectorGenerator.generate", gen_mock):
            await self.recorder.record(ActionType.NAVIGATE, page, value="https://x.com")
            await self.recorder.record(ActionType.CLICK, page, role="button", name="A")
            await self.recorder.record(ActionType.CLICK, page, role="button", name="B")

        trace = self.recorder.stop()
        assert [s.step_index for s in trace.steps] == [0, 1, 2]

    # ------------------------------------------------------------------ success flag

    def test_stop_success_true(self):
        self.recorder.start("success test")
        trace = self.recorder.stop(success=True)
        assert trace.success is True

    def test_stop_success_false(self):
        self.recorder.start("fail test")
        trace = self.recorder.stop(success=False)
        assert trace.success is False

    # ------------------------------------------------------------------ site_domain

    async def test_site_domain_extracted_from_url(self):
        self.recorder.start("domain test")
        page = make_page("https://mysite.example.com/path")

        gen_mock = AsyncMock(return_value=make_element_target())
        with patch("browserlens.compiler.recorder.SelectorGenerator.generate", gen_mock):
            await self.recorder.record(ActionType.NAVIGATE, page, value="https://mysite.example.com")

        trace = self.recorder.stop()
        assert trace.site_domain == "mysite.example.com"
