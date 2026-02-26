"""Layer 3 — Workflow compiler: traces → standalone Playwright scripts."""

from __future__ import annotations

import datetime
import os
import tempfile
import uuid
from typing import Sequence

from browserlens.compiler.types import (
    ActionType,
    CompiledWorkflow,
    ElementTarget,
    ParameterSlot,
    SelectorStrategy,
    TraceStep,
    WorkflowTrace,
    make_fingerprint,
)

# Fixed priority order used in the generated find_element() helper
_FIND_PRIORITY = [
    SelectorStrategy.TEST_ID,
    SelectorStrategy.ROLE_NAME,
    SelectorStrategy.LABEL,
    SelectorStrategy.PLACEHOLDER,
    SelectorStrategy.TEXT,
    SelectorStrategy.CSS,
    SelectorStrategy.XPATH,
]

_FIND_ELEMENT_SOURCE = '''\
async def find_element(page, selectors, timeout=5000):
    """Try each selector strategy in priority order until one succeeds."""
    priority = [
        "test_id", "role_name", "label", "placeholder", "text", "css", "xpath"
    ]
    errors = []
    for strategy in priority:
        val = selectors.get(strategy)
        if val is None:
            continue
        try:
            if strategy == "test_id":
                loc = page.get_by_test_id(val)
            elif strategy == "role_name":
                role, name = val.split("::", 1)
                loc = page.get_by_role(role, name=name)
            elif strategy == "label":
                loc = page.get_by_label(val)
            elif strategy == "placeholder":
                loc = page.get_by_placeholder(val)
            elif strategy == "text":
                loc = page.get_by_text(val, exact=True)
            elif strategy == "css":
                loc = page.locator(val)
            elif strategy == "xpath":
                loc = page.locator("xpath=" + val)
            else:
                continue
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception as e:
            errors.append(f"{strategy}: {e}")
    raise RuntimeError(
        f"find_element exhausted all selectors. Errors: {errors}"
    )
'''


def _selectors_repr(target: ElementTarget) -> str:
    """Render selector dict as a Python literal (safe for ast.literal_eval)."""
    lines = ["{"]
    for strategy in _FIND_PRIORITY:
        if strategy in target.selectors:
            val = target.selectors[strategy]
            # escape backslashes and quotes
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    "{strategy.value}": "{escaped}",')
    lines.append("}")
    return "\n".join(lines)


def _action_call(step: TraceStep, slot_name: str | None) -> str:
    """Return the Playwright action call string for a step."""
    action = step.action
    value = step.value

    if action == ActionType.NAVIGATE:
        url = value or step.url_before
        return f'    await page.goto("{url}")'

    if action == ActionType.WAIT:
        ms = int(value or "1000")
        return f"    await page.wait_for_timeout({ms})"

    # Target-based actions
    el_call = "    el = await find_element(page, _selectors)"

    if action == ActionType.CLICK:
        return f"{el_call}\n    await el.click()"

    if action == ActionType.HOVER:
        return f"{el_call}\n    await el.hover()"

    if action == ActionType.SCROLL:
        return f"{el_call}\n    await el.scroll_into_view_if_needed()"

    if action == ActionType.TYPE:
        if slot_name is not None:
            val_expr = f'params.get("{slot_name}", "{value or ""}")'
        else:
            escaped = (value or "").replace("\\", "\\\\").replace('"', '\\"')
            val_expr = f'"{escaped}"'
        return f"{el_call}\n    await el.fill({val_expr})"

    if action == ActionType.SELECT:
        if slot_name is not None:
            val_expr = f'params.get("{slot_name}", "{value or ""}")'
        else:
            escaped = (value or "").replace("\\", "\\\\").replace('"', '\\"')
            val_expr = f'"{escaped}"'
        return f"{el_call}\n    await el.select_option({val_expr})"

    if action == ActionType.PRESS:
        escaped = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        return f"{el_call}\n    await el.press(\"{escaped}\")"

    return f"    # unhandled action: {action.value}"


def _step_function(step: TraceStep, slot_name: str | None) -> str:
    """Generate the async step_N function source."""
    lines: list[str] = [f"async def step_{step.step_index}(page, **params):"]

    if step.target is not None:
        sel_repr = _selectors_repr(step.target)
        lines.append(f"    _selectors = {sel_repr}")

    action_src = _action_call(step, slot_name)
    lines.append(action_src)
    return "\n".join(lines)


def _steps_list(steps: list[TraceStep]) -> str:
    """Render module-level STEPS list as Python literal."""
    entries: list[str] = []
    for step in steps:
        target_info = "None"
        if step.target is not None:
            role = step.target.role.replace('"', '\\"')
            name = step.target.name.replace('"', '\\"')
            target_info = f'{{"role": "{role}", "name": "{name}"}}'
        url_before = step.url_before.replace('"', '\\"')
        entries.append(
            f'    {{"index": {step.step_index}, "action": "{step.action.value}", '
            f'"target": {target_info}, "url_before": "{url_before}"}},'
        )
    return "STEPS = [\n" + "\n".join(entries) + "\n]"


class WorkflowCompiler:
    """Compiles a WorkflowTrace into a standalone Playwright Python script."""

    def compile(
        self,
        trace: WorkflowTrace,
        parameter_slots: Sequence[ParameterSlot] | None = None,
        workflow_id: str | None = None,
        output_dir: str | None = None,
    ) -> tuple[CompiledWorkflow, str]:
        """
        Compile a trace into a Playwright script.

        Returns (CompiledWorkflow metadata, script_source_str).
        The script is written to output_dir (defaults to a temp directory).
        """
        if workflow_id is None:
            workflow_id = uuid.uuid4().hex[:12]

        slots = list(parameter_slots or [])
        now = datetime.datetime.utcnow().isoformat()
        fingerprint = make_fingerprint(trace.task_description)

        # Map each TYPE/SELECT step index to its parameter slot name
        step_slot_map: dict[int, str] = {}
        # Assign slots in order of declaration, matching TYPE steps in step_index order
        type_step_indices = [
            s.step_index
            for s in trace.steps
            if s.action in (ActionType.TYPE, ActionType.SELECT)
        ]
        for i, slot in enumerate(slots):
            # Assign slot to step indices listed in slot, or fall back to positional
            if slot.step_indices:
                for idx in slot.step_indices:
                    step_slot_map[idx] = slot.name
            elif i < len(type_step_indices):
                step_slot_map[type_step_indices[i]] = slot.name

        # Build script source
        parts: list[str] = []

        # 1. Docstring
        task_escaped = trace.task_description.replace('"""', "'''")
        parts.append(
            f'"""\n'
            f"workflow_id: {workflow_id}\n"
            f"task: {task_escaped}\n"
            f"site: {trace.site_domain}\n"
            f"steps: {len(trace.steps)}\n"
            f"compiled_at: {now}\n"
            f'"""'
        )

        # 2. Imports
        parts.append(
            "from __future__ import annotations\n\n"
            "import argparse\n"
            "import asyncio\n"
            "import sys\n\n"
            "from playwright.async_api import async_playwright"
        )

        # 3. STEPS list
        parts.append(_steps_list(trace.steps))

        # 4. find_element helper
        parts.append(_FIND_ELEMENT_SOURCE)

        # 5. Individual step functions
        for step in trace.steps:
            slot_name = step_slot_map.get(step.step_index)
            parts.append(_step_function(step, slot_name))

        # 6. run_workflow function
        slot_params = ""
        if slots:
            slot_params = ", " + ", ".join(
                f'{s.name}="{s.default_value or ""}"' for s in slots
            )
        parts.append(
            f"async def run_workflow(page=None{slot_params}):\n"
            f"    \"\"\"Run the compiled workflow. If page is None, creates its own browser.\"\"\"\n"
            f"    params = {{}}\n"
        )
        # Populate params dict from slot args
        run_lines: list[str] = []
        for slot in slots:
            run_lines.append(f'    params["{slot.name}"] = {slot.name}')

        # Navigate to initial URL if first step is navigate, else go to url_before of step 0
        if trace.steps:
            first_url = trace.steps[0].url_before
            first_url_escaped = first_url.replace('"', '\\"')
        else:
            first_url_escaped = ""

        run_lines.append("    _own_browser = page is None")
        run_lines.append("    _playwright = None")
        run_lines.append("    _browser = None")
        run_lines.append("    try:")
        run_lines.append("        if _own_browser:")
        run_lines.append("            _playwright = await async_playwright().start()")
        run_lines.append("            _browser = await _playwright.chromium.launch()")
        run_lines.append(
            "            page = await _browser.new_page()"
        )
        step_calls = "\n".join(
            f"        await step_{s.step_index}(page, **params)" for s in trace.steps
        )
        run_lines.append(step_calls)
        run_lines.append("    finally:")
        run_lines.append("        if _own_browser and _browser:")
        run_lines.append("            await _browser.close()")
        run_lines.append("        if _own_browser and _playwright:")
        run_lines.append("            await _playwright.stop()")

        # Append run lines to the run_workflow block
        run_func = parts.pop()  # Get the function header we added
        parts.append(run_func + "\n".join(run_lines))

        # 7. __main__ block
        main_lines: list[str] = [
            'if __name__ == "__main__":',
            "    parser = argparse.ArgumentParser()",
        ]
        for slot in slots:
            default_escaped = (slot.default_value or "").replace('"', '\\"')
            main_lines.append(
                f'    parser.add_argument("--{slot.name}", default="{default_escaped}")'
            )
        main_lines.append("    args = parser.parse_args()")
        if slots:
            slot_kwargs = ", ".join(f"{s.name}=args.{s.name}" for s in slots)
            main_lines.append(
                f"    asyncio.run(run_workflow({slot_kwargs}))"
            )
        else:
            main_lines.append("    asyncio.run(run_workflow())")
        parts.append("\n".join(main_lines))

        script_source = "\n\n\n".join(parts) + "\n"

        # Write to file
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="browserlens_")
        os.makedirs(output_dir, exist_ok=True)
        script_path = os.path.join(output_dir, f"{workflow_id}.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_source)

        metadata = CompiledWorkflow(
            workflow_id=workflow_id,
            task_description=trace.task_description,
            task_fingerprint=fingerprint,
            site_domain=trace.site_domain,
            script_path=script_path,
            parameter_slots=slots,
            step_count=len(trace.steps),
            compiled_at=now,
            source_trace=trace,
        )

        return metadata, script_source
