"""Layer 3 — Self-healing: recovers failed workflow steps."""

from __future__ import annotations

import ast
import inspect
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from playwright.async_api import Page


class WorkflowHealer:
    """
    Attempts to recover a failed step using three escalating levels.

    Level 1 (no I/O)
        Re-parses ``_selectors`` from the step function source and retries
        each selector strategy individually until one succeeds.

    Level 2 (re-analyze via lens)
        Calls ``lens.observe(page)`` and searches the live accessibility tree
        for the expected ``role``/``name``. If found, re-runs the step.

        .. note::
            This call increments ``lens._step`` and updates the differ.
            This side effect is documented and acceptable in v1.

    Level 3 (LLM-assisted, optional)
        Passes context to the provided ``llm_caller``. If it returns a
        locator string, uses ``page.locator()`` to perform the action.
    """

    def __init__(self, lens: Any) -> None:
        self._lens = lens

    async def heal(
        self,
        page: "Page",
        step_meta: dict,
        module: Any,
        params: dict,
        original_error: Exception,
        llm_caller: Callable | None = None,
    ) -> tuple[bool, int | None]:
        """
        Attempt to heal a failed step.

        Returns ``(healed: bool, heal_level: int | None)``.
        """
        step_index = step_meta["index"]
        action = step_meta.get("action", "")
        target = step_meta.get("target")
        role = (target or {}).get("role", "") if target else ""
        name = (target or {}).get("name", "") if target else ""
        step_fn_name = f"step_{step_index}"

        # Level 1: selector re-try (no I/O)
        healed = await self._heal_level1(page, module, step_fn_name, params)
        if healed:
            return True, 1

        # Level 2: re-observe via lens
        if self._lens is not None and role and name:
            healed = await self._heal_level2(
                page, module, step_fn_name, params, role, name
            )
            if healed:
                return True, 2

        # Level 3: LLM-assisted
        if llm_caller is not None:
            healed = await self._heal_level3(
                page, module, step_fn_name, params, action, role, name, original_error, llm_caller
            )
            if healed:
                return True, 3

        return False, None

    async def _heal_level1(
        self,
        page: "Page",
        module: Any,
        step_fn_name: str,
        params: dict,
    ) -> bool:
        """
        Level 1: parse _selectors from source, try each strategy individually.
        """
        step_fn = getattr(module, step_fn_name, None)
        if step_fn is None:
            return False

        try:
            source = inspect.getsource(step_fn)
            tree = ast.parse(source)
        except (OSError, SyntaxError, TypeError):
            return False

        selectors = _extract_selectors_from_ast(tree)
        if not selectors:
            return False

        find_element = getattr(module, "find_element", None)
        if find_element is None:
            return False

        # Try each strategy individually
        for strategy, val in selectors.items():
            try:
                await find_element(page, {strategy: val}, timeout=3000)
                # Strategy worked — re-run full step
                step_fn = getattr(module, step_fn_name)
                await step_fn(page, **params)
                return True
            except Exception:
                continue
        return False

    async def _heal_level2(
        self,
        page: "Page",
        module: Any,
        step_fn_name: str,
        params: dict,
        role: str,
        name: str,
    ) -> bool:
        """
        Level 2: re-observe via lens and check element is present.

        Side effect: increments lens._step and updates the differ state.
        """
        try:
            obs = await self._lens.observe(page)
            flat = obs.page_state.flat_nodes()
            found = any(
                n.role == role and n.name == name
                for n in flat
            )
            if not found:
                return False
            # Element confirmed present — retry step
            step_fn = getattr(module, step_fn_name, None)
            if step_fn is None:
                return False
            await step_fn(page, **params)
            return True
        except Exception:
            return False

    async def _heal_level3(
        self,
        page: "Page",
        module: Any,
        step_fn_name: str,
        params: dict,
        action: str,
        role: str,
        name: str,
        original_error: Exception,
        llm_caller: Callable,
    ) -> bool:
        """
        Level 3: LLM-assisted healing via llm_caller.

        llm_caller receives a dict and should return a locator string or None.
        """
        try:
            context = {
                "page_observation": "",  # caller may enrich
                "step_action": action,
                "step_role": role,
                "step_name": name,
                "error": str(original_error),
            }
            result = llm_caller(context)
            if not result:
                return False
            locator_str = str(result)
            loc = page.locator(locator_str)
            # Perform the action based on action type
            if action == "click":
                await loc.click()
            elif action == "type":
                val = params.get(list(params.keys())[0], "") if params else ""
                await loc.fill(str(val))
            elif action == "hover":
                await loc.hover()
            else:
                await loc.click()
            return True
        except Exception:
            return False


def _extract_selectors_from_ast(tree: ast.AST) -> dict:
    """
    Extract _selectors dict literal from a step function AST.

    Safe because the compiler only generates literal dicts.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets
        if not targets:
            continue
        target = targets[0]
        if not (isinstance(target, ast.Name) and target.id == "_selectors"):
            continue
        try:
            return ast.literal_eval(node.value)
        except (ValueError, TypeError):
            pass
    return {}
