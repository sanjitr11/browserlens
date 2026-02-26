"""Unit tests for WorkflowHealer."""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from browserlens.compiler.healer import WorkflowHealer, _extract_selectors_from_ast
from browserlens.core.types import PageState, StateNode

_FIND_ELEMENT_SRC = """\
async def find_element(page, selectors, timeout=5000):
    priority = ["test_id", "role_name", "label", "placeholder", "text", "css", "xpath"]
    for strategy in priority:
        val = selectors.get(strategy)
        if val is None:
            continue
        if strategy == "role_name":
            role, name = val.split("::", 1)
            loc = page.get_by_role(role, name=name)
        else:
            loc = page.locator(val)
        await loc.wait_for(state="visible", timeout=timeout)
        return loc
    raise RuntimeError("exhausted selectors")
"""


def make_minimal_module(step_fn_source: str, wf_id: str = "test") -> types.ModuleType:
    """
    Create a minimal module with a step function and find_element.

    Writes to a real temp file so inspect.getsource() works in Level 1 healing.
    """
    full_src = _FIND_ELEMENT_SRC + "\n\n" + step_fn_source

    fd, path = tempfile.mkstemp(suffix=".py", prefix=f"_bl_wf_{wf_id}_")
    try:
        os.write(fd, full_src.encode())
    finally:
        os.close(fd)

    module_name = f"_browserlens_workflow_{wf_id}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def make_step_meta(index=0, action="click", role="button", name="Submit") -> dict:
    return {
        "index": index,
        "action": action,
        "target": {"role": role, "name": name},
    }


def make_state_with_node(role="button", name="Submit") -> PageState:
    node = StateNode(ref="@e1", role=role, name=name)
    return PageState(
        url="https://example.com",
        title="Test",
        representation_type=None,  # type: ignore[arg-type]
        root=node,
        step=1,
    )


class TestExtractSelectorsFromAst:
    def test_extracts_simple_selectors(self):
        src = '''
async def step_0(page, **params):
    _selectors = {"role_name": "button::Submit", "css": "button.submit"}
    el = await find_element(page, _selectors)
    await el.click()
'''
        tree = ast.parse(src)
        result = _extract_selectors_from_ast(tree)
        assert result == {"role_name": "button::Submit", "css": "button.submit"}

    def test_returns_empty_when_no_selectors(self):
        src = '''
async def step_0(page, **params):
    await page.goto("https://example.com")
'''
        tree = ast.parse(src)
        result = _extract_selectors_from_ast(tree)
        assert result == {}


class TestHealerLevel1:
    def setup_method(self):
        self.lens_mock = MagicMock()
        self.healer = WorkflowHealer(lens=self.lens_mock)

    async def test_level1_returns_false_when_no_selectors(self):
        # Module with no _selectors in step function
        src = """
async def step_0(page, **params):
    await page.goto("https://example.com")
"""
        module = make_minimal_module(src)
        page = AsyncMock()
        meta = {"index": 0, "action": "navigate", "target": None}
        healed, level = await self.healer.heal(page, meta, module, {}, Exception("fail"))
        # No selectors → level 1 fails. No role/name for level 2. No llm_caller for level 3.
        assert healed is False
        assert level is None

    async def test_level1_succeeds_on_second_selector(self):
        src = """
async def step_0(page, **params):
    _selectors = {"css": "button.bad", "role_name": "button::Submit"}
    el = await find_element(page, _selectors)
    await el.click()
"""
        module = make_minimal_module(src)
        page = AsyncMock()
        loc = AsyncMock()
        loc.wait_for = AsyncMock()
        loc.click = AsyncMock()
        page.locator = MagicMock(side_effect=[Exception("css bad"), loc])
        page.get_by_role = MagicMock(return_value=loc)

        # Simulate: CSS selector broken, role_name works.
        # Fail when called with only {"css": ...} (level1 probe).
        # Succeed when role_name is present (full dict or single role_name probe).
        async def mock_find(pg, selectors, timeout=5000):
            if "role_name" in selectors:
                return loc
            raise Exception("css failed")

        module.find_element = mock_find

        meta = make_step_meta(role="button", name="Submit")
        healed, level = await self.healer.heal(page, meta, module, {}, Exception("original"))
        assert healed is True
        assert level == 1


class TestHealerLevel2:
    def setup_method(self):
        self.lens_mock = MagicMock()
        self.healer = WorkflowHealer(lens=self.lens_mock)

    async def test_level2_calls_lens_observe(self):
        # No selectors in step → level 1 skips; level 2 uses lens
        src = """
async def step_0(page, **params):
    el = await find_element(page, {"role_name": "button::Submit"})
    await el.click()
"""
        module = make_minimal_module(src)

        # Patch find_element to always fail (so level 1 fails)
        async def always_fail(pg, selectors, timeout=5000):
            raise Exception("always fails")

        module.find_element = always_fail

        page = AsyncMock()
        # Set up obs with the element present
        state = make_state_with_node(role="button", name="Submit")
        obs_mock = MagicMock()
        obs_mock.page_state = state
        self.lens_mock.observe = AsyncMock(return_value=obs_mock)

        # Make step succeed on retry (patch step_0 directly)
        module.step_0 = AsyncMock()

        meta = make_step_meta(role="button", name="Submit")
        healed, level = await self.healer.heal(page, meta, module, {}, Exception("fail"))
        # Level 2 should have been tried
        self.lens_mock.observe.assert_awaited()

    async def test_level2_succeeds_when_element_in_state(self):
        src = """
async def step_0(page, **params):
    _selectors = {"role_name": "button::Submit"}
    el = await find_element(page, _selectors)
    await el.click()
"""
        module = make_minimal_module(src)

        # Make find_element fail (forces level 1 to fail too)
        async def fail_find(pg, selectors, timeout=5000):
            raise Exception("no element")

        module.find_element = fail_find

        page = AsyncMock()
        state = make_state_with_node(role="button", name="Submit")
        obs_mock = MagicMock()
        obs_mock.page_state = state
        self.lens_mock.observe = AsyncMock(return_value=obs_mock)

        # step_0 succeeds on direct call (healer calls it again after observe)
        module.step_0 = AsyncMock()

        meta = make_step_meta(role="button", name="Submit")
        healed, level = await self.healer.heal(page, meta, module, {}, Exception("fail"))
        assert healed is True
        assert level == 2


class TestHealerLevel3:
    def setup_method(self):
        self.lens_mock = MagicMock()
        self.healer = WorkflowHealer(lens=self.lens_mock)

    async def test_level3_calls_llm_caller_with_correct_keys(self):
        src = """
async def step_0(page, **params):
    _selectors = {"role_name": "button::Submit"}
    el = await find_element(page, _selectors)
    await el.click()
"""
        module = make_minimal_module(src)

        async def fail_find(pg, selectors, timeout=5000):
            raise Exception("no element")

        module.find_element = fail_find

        # Level 2 also fails — no element in state
        self.lens_mock.observe = AsyncMock(side_effect=Exception("observe fail"))

        page = AsyncMock()
        loc = AsyncMock()
        page.locator = MagicMock(return_value=loc)
        loc.click = AsyncMock()

        captured = {}

        def llm_caller(ctx):
            captured.update(ctx)
            return "button:has-text('Submit')"

        meta = make_step_meta(role="button", name="Submit")
        healed, level = await self.healer.heal(page, meta, module, {}, Exception("fail"), llm_caller=llm_caller)

        # llm_caller must receive required context keys
        assert "page_observation" in captured
        assert "step_action" in captured
        assert "step_role" in captured
        assert "step_name" in captured
        assert "error" in captured

    async def test_level3_uses_locator_returned_by_llm(self):
        src = """
async def step_0(page, **params):
    _selectors = {"role_name": "button::Submit"}
    el = await find_element(page, _selectors)
    await el.click()
"""
        module = make_minimal_module(src)

        async def fail_find(pg, selectors, timeout=5000):
            raise Exception("fail")

        module.find_element = fail_find
        self.lens_mock.observe = AsyncMock(side_effect=Exception("fail"))

        page = AsyncMock()
        loc = AsyncMock()
        page.locator = MagicMock(return_value=loc)
        loc.click = AsyncMock()

        def llm_caller(ctx):
            return "button.my-btn"

        meta = make_step_meta(role="button", name="Submit")
        healed, level = await self.healer.heal(page, meta, module, {}, Exception("fail"), llm_caller=llm_caller)
        assert healed is True
        assert level == 3
        page.locator.assert_called_with("button.my-btn")
