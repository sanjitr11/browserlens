"""Layer 3 — Workflow executor: runs compiled Playwright scripts."""

from __future__ import annotations

import importlib.util
import sys
import time
from typing import Any, Callable

from playwright.async_api import Page

from browserlens.compiler.cache import WorkflowCache
from browserlens.compiler.types import ExecutionResult, StepResult


class WorkflowExecutor:
    """Executes a compiled workflow script against a live Playwright page."""

    def __init__(
        self,
        cache: WorkflowCache,
        healer: Any,  # WorkflowHealer — avoid circular import with type hint
    ) -> None:
        self._cache = cache
        self._healer = healer

    async def execute(
        self,
        workflow_id: str,
        page: Page,
        params: dict | None = None,
        llm_caller: Callable | None = None,
    ) -> ExecutionResult:
        """
        Execute a compiled workflow.

        Parameters
        ----------
        workflow_id:
            The cached workflow to run.
        page:
            Live Playwright page to run against.
        params:
            Runtime parameter overrides (e.g. ``{"username": "alice"}``).
        llm_caller:
            Optional callable for Level 3 (LLM-assisted) healing.

        Returns
        -------
        ExecutionResult with step-level detail.
        """
        params = params or {}

        metadata = self._cache.load(workflow_id)
        if metadata is None:
            return ExecutionResult(
                workflow_id=workflow_id,
                success=False,
                steps_executed=0,
                steps_succeeded=0,
                error=f"Workflow {workflow_id!r} not found in cache",
            )

        module = self._load_module(metadata.script_path, workflow_id)
        if module is None:
            return ExecutionResult(
                workflow_id=workflow_id,
                success=False,
                steps_executed=0,
                steps_succeeded=0,
                error=f"Failed to load module from {metadata.script_path!r}",
            )

        steps_list = getattr(module, "STEPS", [])
        step_results: list[StepResult] = []
        total_start = time.monotonic()
        overall_success = True

        for step_meta in steps_list:
            step_index = step_meta["index"]
            action_name = step_meta["action"]
            step_fn_name = f"step_{step_index}"
            step_fn = getattr(module, step_fn_name, None)

            if step_fn is None:
                step_results.append(
                    StepResult(
                        step_index=step_index,
                        success=False,
                        action=action_name,
                        error=f"Step function {step_fn_name!r} not found in module",
                    )
                )
                overall_success = False
                break

            step_start = time.monotonic()
            try:
                await step_fn(page, **params)
                latency = (time.monotonic() - step_start) * 1000
                step_results.append(
                    StepResult(
                        step_index=step_index,
                        success=True,
                        action=action_name,
                        latency_ms=latency,
                    )
                )
            except Exception as exc:
                healed, heal_level = await self._healer.heal(
                    page=page,
                    step_meta=step_meta,
                    module=module,
                    params=params,
                    original_error=exc,
                    llm_caller=llm_caller,
                )
                latency = (time.monotonic() - step_start) * 1000
                if healed:
                    step_results.append(
                        StepResult(
                            step_index=step_index,
                            success=True,
                            action=action_name,
                            healed=True,
                            heal_level=heal_level,
                            latency_ms=latency,
                        )
                    )
                else:
                    step_results.append(
                        StepResult(
                            step_index=step_index,
                            success=False,
                            action=action_name,
                            error=str(exc),
                            healed=False,
                            latency_ms=latency,
                        )
                    )
                    overall_success = False
                    break

        succeeded = sum(1 for r in step_results if r.success)
        total_latency = (time.monotonic() - total_start) * 1000

        return ExecutionResult(
            workflow_id=workflow_id,
            success=overall_success,
            steps_executed=len(step_results),
            steps_succeeded=succeeded,
            step_results=step_results,
            total_latency_ms=total_latency,
        )

    @staticmethod
    def _load_module(script_path: str, workflow_id: str):
        """Load a compiled workflow module, evicting any stale cached copy."""
        module_name = f"_browserlens_workflow_{workflow_id}"
        # Hot-reload: remove stale entry before loading
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            return None
        return module
