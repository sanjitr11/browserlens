"""Layer 3 — Action recorder for capturing agent traces."""

from __future__ import annotations

import datetime

from playwright.async_api import Page

from browserlens.compiler.selectors import SelectorGenerator
from browserlens.compiler.types import (
    ActionType,
    ElementTarget,
    TraceStep,
    WorkflowTrace,
)

# Actions that operate on a target element
_TARGET_ACTIONS = {
    ActionType.CLICK,
    ActionType.TYPE,
    ActionType.SELECT,
    ActionType.PRESS,
    ActionType.HOVER,
    ActionType.SCROLL,
}


class ActionRecorder:
    """Records agent actions into a WorkflowTrace for later compilation."""

    def __init__(self) -> None:
        self._task_description: str | None = None
        self._site_domain: str = ""
        self._steps: list[TraceStep] = []
        self._active = False
        self._selector_gen = SelectorGenerator()

    def start(self, task_description: str) -> None:
        """Begin recording a new workflow trace. Clears any previous state."""
        self._task_description = task_description
        self._site_domain = ""
        self._steps = []
        self._active = True

    async def record(
        self,
        action: ActionType,
        page: Page,
        *,
        ref: str = "",
        role: str = "",
        name: str = "",
        value: str | None = None,
    ) -> TraceStep:
        """
        Record a single action.

        Must be called while the element is still in the DOM (for target-based actions).
        Raises RuntimeError if start() has not been called first.
        """
        if not self._active:
            raise RuntimeError("ActionRecorder.start() must be called before record()")

        url_before = page.url

        # Extract domain from first step
        if not self._site_domain and url_before:
            from urllib.parse import urlparse

            parsed = urlparse(url_before)
            self._site_domain = parsed.netloc or parsed.path

        target: ElementTarget | None = None
        if action in _TARGET_ACTIONS:
            target = await self._selector_gen.generate(
                page, ref=ref, role=role, name=name, value=value or ""
            )

        step = TraceStep(
            step_index=len(self._steps),
            action=action,
            target=target,
            value=value,
            url_before=url_before,
            url_after=None,
        )
        self._steps.append(step)
        return step

    def stop(self, success: bool = True) -> WorkflowTrace:
        """
        Stop recording and return the completed WorkflowTrace.

        Fills url_after for each step from the next step's url_before.
        Raises RuntimeError if start() has not been called.
        """
        if not self._active:
            raise RuntimeError("ActionRecorder.start() must be called before stop()")

        self._active = False

        # Fill url_after from next step's url_before
        for i in range(len(self._steps) - 1):
            self._steps[i].url_after = self._steps[i + 1].url_before

        trace = WorkflowTrace(
            task_description=self._task_description or "",
            site_domain=self._site_domain,
            steps=list(self._steps),
            success=success,
            recorded_at=datetime.datetime.utcnow().isoformat(),
        )
        self._task_description = None
        self._steps = []
        return trace
