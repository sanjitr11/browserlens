"""BrowserLens — main orchestrator class."""

from __future__ import annotations

import time
from typing import Callable

from playwright.async_api import Page

from browserlens.compiler.cache import WorkflowCache
from browserlens.compiler.compiler import WorkflowCompiler
from browserlens.compiler.executor import WorkflowExecutor
from browserlens.compiler.healer import WorkflowHealer
from browserlens.compiler.recorder import ActionRecorder
from browserlens.compiler.types import (
    ActionType,
    CompiledWorkflow,
    ExecutionResult,
    ParameterSlot,
    WorkflowTrace,
)
from browserlens.core.types import ObservationResult, RepresentationType
from browserlens.differ.differ import StateDiffer
from browserlens.extractors.a11y import A11yExtractor
from browserlens.extractors.dom import DOMExtractor
from browserlens.extractors.hybrid import HybridExtractor
from browserlens.extractors.vision import VisionExtractor
from browserlens.formatter.formatter import OutputFormatter
from browserlens.formatter.ref_manager import RefManager
from browserlens.router.router import AdaptiveRouter


class BrowserLens:
    """
    Sits between the browser (Playwright) and an LLM agent.

    Usage:
        lens = BrowserLens()
        result = await lens.observe(page)
        # result.formatted_text → send to LLM
        # result.delta         → inspect what changed
    """

    def __init__(
        self,
        *,
        token_budget: int = 4096,
        enable_diffing: bool = True,
        enable_routing: bool = True,
        force_representation: RepresentationType | None = None,
        router_override: Callable | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self.token_budget = token_budget
        self.enable_diffing = enable_diffing
        self.enable_routing = enable_routing
        self.force_representation = force_representation

        self._step = 0
        self._ref_manager = RefManager()
        self._router = AdaptiveRouter(override=router_override)
        self._differ = StateDiffer()
        self._formatter = OutputFormatter(
            ref_manager=self._ref_manager,
            token_budget=token_budget,
        )
        self._extractors = {
            RepresentationType.A11Y_TREE: A11yExtractor(self._ref_manager),
            RepresentationType.DISTILLED_DOM: DOMExtractor(self._ref_manager),
            RepresentationType.VISION: VisionExtractor(self._ref_manager),
            RepresentationType.HYBRID: HybridExtractor(self._ref_manager),
        }

        # Layer 3 — Workflow Compiler components
        self._recorder = ActionRecorder()
        self._compiler = WorkflowCompiler()
        self._cache = WorkflowCache(cache_dir=cache_dir)
        self._healer = WorkflowHealer(lens=self)
        self._executor = WorkflowExecutor(cache=self._cache, healer=self._healer)

    async def observe(self, page: Page) -> ObservationResult:
        """
        Observe the current browser page and return a compact, LLM-ready representation.

        On the first call: returns the full page state.
        On subsequent calls: returns only the delta (what changed).
        """
        t0 = time.monotonic()
        self._step += 1

        # Layer 1: choose representation type
        if self.force_representation is not None:
            rep_type = self.force_representation
        elif self.enable_routing:
            rep_type = await self._router.select(page)
        else:
            rep_type = RepresentationType.A11Y_TREE

        # Extract page state
        extractor = self._extractors[rep_type]
        page_state = await extractor.extract(page)
        page_state.step = self._step

        diff_discarded = False

        # Layer 2: diff against previous state
        if self.enable_diffing:
            prev_url = self._differ.get_previous_url()
            if prev_url is not None and prev_url != page_state.url:
                # URL changed — navigation event; skip tree diff and return full state
                delta = self._differ.force_full_state(page_state)
                diff_discarded = True
            else:
                delta = self._differ.diff(page_state)
        else:
            delta = None

        # Format for LLM
        formatted_text, token_count = self._formatter.format(page_state, delta)

        # Token-count fallback: if the delta is larger than the full state, discard it
        if delta is not None and not delta.is_full_state:
            full_text, full_tokens = self._formatter.format_full(page_state)
            if token_count > full_tokens:
                formatted_text = full_text
                token_count = full_tokens
                diff_discarded = True

        latency_ms = (time.monotonic() - t0) * 1000

        return ObservationResult(
            step=self._step,
            url=page_state.url,
            representation_type=rep_type,
            formatted_text=formatted_text,
            delta=delta,
            page_state=page_state,
            token_count=token_count,
            latency_ms=latency_ms,
            diff_discarded=diff_discarded,
        )

    def reset(self) -> None:
        """Reset step counter and all stored state (e.g., start a new task)."""
        self._step = 0
        self._ref_manager.reset()
        self._differ.reset()

    # ------------------------------------------------------------------
    # Layer 3 — Workflow Compiler public API
    # ------------------------------------------------------------------

    def start_recording(self, task_description: str) -> None:
        """Begin recording agent actions for later compilation."""
        self._recorder.start(task_description)

    async def record_action(
        self,
        action: ActionType,
        page: Page,
        *,
        target_ref: str = "",
        role: str = "",
        name: str = "",
        value: str | None = None,
    ) -> None:
        """
        Record a single agent action.

        Must be called while the element is still in the DOM.
        """
        await self._recorder.record(
            action, page, ref=target_ref, role=role, name=name, value=value
        )

    def stop_recording(self, success: bool = True) -> WorkflowTrace:
        """Stop recording and return the completed WorkflowTrace."""
        return self._recorder.stop(success=success)

    def compile_workflow(
        self,
        trace: WorkflowTrace,
        parameters: list[ParameterSlot] | None = None,
    ) -> CompiledWorkflow:
        """
        Compile a WorkflowTrace and save it to the cache.

        Returns the CompiledWorkflow metadata.
        """
        metadata, script_source = self._compiler.compile(
            trace, parameter_slots=parameters
        )
        return self._cache.save(metadata, script_source)

    async def execute_workflow(
        self,
        task: str,
        page: Page,
        params: dict | None = None,
        llm_caller=None,
    ) -> ExecutionResult | None:
        """
        Look up a cached workflow by task description and execute it.

        Returns None if no matching workflow is found (caller should fall
        back to agent exploration).
        """
        cached = self._cache.lookup_by_task(task)
        if cached is None:
            return None
        return await self._executor.execute(
            cached.workflow_id, page, params=params, llm_caller=llm_caller
        )

    def export_workflow(self, workflow_id: str, path: str) -> str:
        """Export a compiled workflow script to the given file path."""
        return self._cache.export(workflow_id, path)
