"""Unit tests for WorkflowCompiler (pure, no browser required)."""

from __future__ import annotations

import ast
import tempfile

import pytest

from browserlens.compiler.compiler import WorkflowCompiler
from browserlens.compiler.types import (
    ActionType,
    ElementTarget,
    ParameterSlot,
    SelectorStrategy,
    TraceStep,
    WorkflowTrace,
)


def make_target(role="button", name="Submit") -> ElementTarget:
    return ElementTarget(
        ref="@e1",
        role=role,
        name=name,
        selectors={
            SelectorStrategy.ROLE_NAME: f"{role}::{name}",
            SelectorStrategy.CSS: "button.submit",
        },
        selector_priority=[SelectorStrategy.ROLE_NAME, SelectorStrategy.CSS],
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
        target=make_target(role="textbox", name="Username"),
        value=value,
        url_before="https://example.com",
    )


def make_click_step(index=2) -> TraceStep:
    return TraceStep(
        step_index=index,
        action=ActionType.CLICK,
        target=make_target(role="button", name="Login"),
        value=None,
        url_before="https://example.com",
    )


def make_trace(steps=None, task="log in to example.com") -> WorkflowTrace:
    if steps is None:
        steps = [make_navigate_step(), make_type_step(), make_click_step()]
    return WorkflowTrace(
        task_description=task,
        site_domain="example.com",
        steps=steps,
    )


class TestWorkflowCompiler:
    def setup_method(self):
        self.compiler = WorkflowCompiler()
        self.output_dir = tempfile.mkdtemp()

    def _compile(self, trace=None, slots=None, wf_id=None):
        return self.compiler.compile(
            trace or make_trace(),
            parameter_slots=slots,
            workflow_id=wf_id,
            output_dir=self.output_dir,
        )

    # ------------------------------------------------------------------ source structure

    def test_navigate_step_uses_goto(self):
        trace = make_trace(steps=[make_navigate_step(url="https://example.com")])
        _, src = self._compile(trace)
        assert "page.goto(" in src
        assert "https://example.com" in src

    def test_type_step_uses_fill(self):
        trace = make_trace(steps=[make_navigate_step(), make_type_step(value="world")])
        _, src = self._compile(trace)
        assert "el.fill(" in src

    def test_click_step_uses_click(self):
        trace = make_trace(steps=[make_click_step()])
        _, src = self._compile(trace)
        assert "el.click()" in src

    def test_steps_list_present(self):
        _, src = self._compile()
        assert "STEPS = [" in src

    def test_find_element_helper_present(self):
        _, src = self._compile()
        assert "async def find_element(" in src

    def test_source_is_valid_python(self):
        _, src = self._compile()
        ast.parse(src)  # raises SyntaxError on bad source

    # ------------------------------------------------------------------ parameter slots

    def test_parameter_slot_replaces_literal(self):
        trace = make_trace(steps=[make_navigate_step(), make_type_step(value="alice")])
        slots = [ParameterSlot(name="username", step_indices=[1], default_value="alice")]
        _, src = self._compile(trace, slots=slots)
        assert 'params.get("username"' in src

    def test_parameter_slot_keeps_default(self):
        trace = make_trace(steps=[make_navigate_step(), make_type_step(value="alice")])
        slots = [ParameterSlot(name="username", step_indices=[1], default_value="alice")]
        _, src = self._compile(trace, slots=slots)
        assert "alice" in src

    def test_multi_slot_ordering(self):
        steps = [
            make_navigate_step(index=0),
            make_type_step(index=1, value="user@example.com"),
            TraceStep(
                step_index=2,
                action=ActionType.TYPE,
                target=make_target(role="textbox", name="Password"),
                value="secret",
                url_before="https://example.com",
            ),
        ]
        trace = make_trace(steps=steps)
        slots = [
            ParameterSlot(name="email", step_indices=[1], default_value="user@example.com"),
            ParameterSlot(name="password", step_indices=[2], default_value="secret"),
        ]
        _, src = self._compile(trace, slots=slots)
        assert 'params.get("email"' in src
        assert 'params.get("password"' in src

    # ------------------------------------------------------------------ __main__ block

    def test_argparse_in_main_block(self):
        _, src = self._compile()
        assert 'if __name__ == "__main__":' in src
        assert "argparse" in src

    def test_slot_arg_in_main_block(self):
        trace = make_trace(steps=[make_navigate_step(), make_type_step()])
        slots = [ParameterSlot(name="username", step_indices=[1], default_value="bob")]
        _, src = self._compile(trace, slots=slots)
        assert "--username" in src

    # ------------------------------------------------------------------ workflow_id

    def test_custom_workflow_id(self):
        _, src = self._compile(wf_id="my_custom_id")
        assert "my_custom_id" in src

    def test_metadata_workflow_id(self):
        wf, _ = self._compile(wf_id="specific_id")
        assert wf.workflow_id == "specific_id"

    def test_step_count(self):
        wf, _ = self._compile()
        assert wf.step_count == 3  # navigate + type + click

    def test_script_file_created(self):
        import os
        wf, _ = self._compile(wf_id="filetest")
        assert os.path.isfile(wf.script_path)

    # ------------------------------------------------------------------ STEPS introspection

    def test_steps_list_has_correct_count(self):
        trace = make_trace(steps=[make_navigate_step(), make_click_step()])
        _, src = self._compile(trace)
        # Count entries — each has "index":
        assert src.count('"index":') == 2

    def test_steps_list_contains_action_type(self):
        _, src = self._compile(make_trace(steps=[make_navigate_step()]))
        assert '"navigate"' in src
