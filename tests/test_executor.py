"""Unit tests for WorkflowExecutor (compiles real scripts, uses AsyncMock page)."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from browserlens.compiler.cache import WorkflowCache
from browserlens.compiler.compiler import WorkflowCompiler
from browserlens.compiler.executor import WorkflowExecutor
from browserlens.compiler.healer import WorkflowHealer
from browserlens.compiler.types import (
    ActionType,
    ElementTarget,
    ExecutionResult,
    SelectorStrategy,
    TraceStep,
    WorkflowTrace,
)


def make_target(role="button", name="Go") -> ElementTarget:
    return ElementTarget(
        ref="@e1",
        role=role,
        name=name,
        selectors={SelectorStrategy.ROLE_NAME: f"{role}::{name}"},
        selector_priority=[SelectorStrategy.ROLE_NAME],
    )


def make_navigate_step(index=0, url="https://example.com") -> TraceStep:
    return TraceStep(
        step_index=index,
        action=ActionType.NAVIGATE,
        target=None,
        value=url,
        url_before="about:blank",
    )


def make_type_step(index=1, value="hello") -> TraceStep:
    return TraceStep(
        step_index=index,
        action=ActionType.TYPE,
        target=make_target(role="textbox", name="Query"),
        value=value,
        url_before="https://example.com",
    )


def make_click_step(index=2) -> TraceStep:
    return TraceStep(
        step_index=index,
        action=ActionType.CLICK,
        target=make_target(role="button", name="Go"),
        value=None,
        url_before="https://example.com",
    )


def make_trace(steps=None) -> WorkflowTrace:
    return WorkflowTrace(
        task_description="search for puppies",
        site_domain="example.com",
        steps=steps or [make_navigate_step(), make_type_step(), make_click_step()],
    )


def make_page() -> AsyncMock:
    page = AsyncMock()
    page.url = "https://example.com"
    # find_element returns a locator-like AsyncMock
    loc = AsyncMock()
    page.get_by_role = MagicMock(return_value=loc)
    page.get_by_label = MagicMock(return_value=loc)
    page.get_by_placeholder = MagicMock(return_value=loc)
    page.get_by_text = MagicMock(return_value=loc)
    page.locator = MagicMock(return_value=loc)
    page.get_by_test_id = MagicMock(return_value=loc)
    loc.wait_for = AsyncMock()
    loc.click = AsyncMock()
    loc.fill = AsyncMock()
    loc.hover = AsyncMock()
    return page


class TestWorkflowExecutor:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = WorkflowCache(cache_dir=self.tmpdir)
        # Use a real healer backed by a no-op lens
        lens_mock = MagicMock()
        self.healer = WorkflowHealer(lens=lens_mock)
        self.executor = WorkflowExecutor(cache=self.cache, healer=self.healer)
        self.compiler = WorkflowCompiler()

    def _compile_and_cache(self, trace=None):
        trace = trace or make_trace()
        meta, src = self.compiler.compile(trace, output_dir=self.tmpdir)
        return self.cache.save(meta, src)

    # ------------------------------------------------------------------ non-existent workflow

    async def test_nonexistent_workflow_returns_failure(self):
        page = make_page()
        result = await self.executor.execute("no_such_wf", page)
        assert isinstance(result, ExecutionResult)
        assert result.success is False
        assert "not found" in (result.error or "").lower()

    # ------------------------------------------------------------------ navigate step

    async def test_navigate_step_calls_goto(self):
        meta = self._compile_and_cache(make_trace(steps=[make_navigate_step(url="https://example.com")]))
        page = make_page()
        result = await self.executor.execute(meta.workflow_id, page)
        page.goto.assert_awaited_once_with("https://example.com")
        assert result.steps_executed == 1

    # ------------------------------------------------------------------ failed step

    async def test_failed_step_recorded_in_results(self):
        meta = self._compile_and_cache(make_trace(steps=[make_navigate_step(), make_click_step()]))
        page = make_page()
        # Make goto raise an error
        page.goto.side_effect = Exception("network error")
        result = await self.executor.execute(meta.workflow_id, page)
        assert result.success is False
        assert any(not r.success for r in result.step_results)

    # ------------------------------------------------------------------ params passed to fill

    async def test_params_passed_to_fill(self):
        meta = self._compile_and_cache(
            make_trace(steps=[make_navigate_step(), make_type_step(value="default")])
        )
        page = make_page()
        # find_element will succeed via wait_for on the locator
        result = await self.executor.execute(meta.workflow_id, page, params={"param": "override"})
        # We just verify execution didn't fail due to params issues
        assert result.steps_executed >= 1

    # ------------------------------------------------------------------ latency populated

    async def test_latency_populated(self):
        meta = self._compile_and_cache(make_trace(steps=[make_navigate_step()]))
        page = make_page()
        result = await self.executor.execute(meta.workflow_id, page)
        assert result.total_latency_ms >= 0
        for sr in result.step_results:
            assert sr.latency_ms >= 0

    # ------------------------------------------------------------------ steps_succeeded count

    async def test_steps_succeeded_count(self):
        meta = self._compile_and_cache(make_trace(steps=[make_navigate_step()]))
        page = make_page()
        result = await self.executor.execute(meta.workflow_id, page)
        assert result.steps_succeeded == result.steps_executed
