"""OutputFormatter — converts PageState / Delta to LLM-ready text."""

from __future__ import annotations

from browserlens.core.types import Delta, NodeChange, PageState, RepresentationType, StateNode
from browserlens.formatter.ref_manager import RefManager
from browserlens.formatter.token_budget import TokenBudget

_INDENT = "  "


class OutputFormatter:
    """
    Produces compact, LLM-ready text from a PageState (full) or Delta (diff).

    Format examples are shown in DESIGN.md under "What the LLM sees".
    """

    def __init__(self, ref_manager: RefManager, token_budget: int = 4096) -> None:
        self._refs = ref_manager
        self._budget = TokenBudget()
        self._max_tokens = token_budget

    def format(self, state: PageState, delta: Delta | None) -> tuple[str, int]:
        """
        Return (formatted_text, token_count).
        Uses full state on first step or when delta is None, otherwise uses delta.
        """
        if delta is None or delta.is_full_state:
            text = self._format_full(state)
        else:
            text = self._format_delta(delta, state)

        text, _ = self._budget.truncate(text, self._max_tokens)
        token_count = self._budget.count(text)
        return text, token_count

    # ------------------------------------------------------------------
    # Full state rendering
    # ------------------------------------------------------------------

    def _format_full(self, state: PageState) -> str:
        lines = [
            f"[FULL PAGE STATE — step {state.step}]",
            f"URL: {state.url}",
            f"Title: {state.title}",
            f"Representation: {state.representation_type.value}",
            "",
        ]
        lines += self._render_node(state.root, depth=0)

        if state.screenshot_b64:
            lines += ["", "[VISUAL: screenshot attached]"]

        return "\n".join(lines)

    def _render_node(self, node: StateNode, depth: int) -> list[str]:
        indent = _INDENT * depth
        parts = [f"{node.role}"]

        if node.name:
            parts.append(f'"{node.name}"')

        ref_str = f" [{node.ref}]"

        props: list[str] = []
        if node.value:
            props.append(f"value: {node.value!r}")
        if node.checked is not None:
            props.append(f"checked: {node.checked}")
        if node.expanded is not None:
            props.append(f"expanded: {node.expanded}")
        if node.disabled:
            props.append("disabled")
        if node.focused:
            props.append("focused")

        prop_str = f" ({', '.join(props)})" if props else ""
        line = f"{indent}- {' '.join(parts)}{ref_str}{prop_str}"

        result = [line]
        for child in node.children:
            result += self._render_node(child, depth + 1)
        return result

    # ------------------------------------------------------------------
    # Delta rendering
    # ------------------------------------------------------------------

    def _format_delta(self, delta: Delta, state: PageState) -> str:
        total = delta.total_changes
        lines = [
            f"[DELTA — step {state.step} — {total} change{'s' if total != 1 else ''}]",
            f"URL: {state.url}",
            "",
        ]

        if delta.added:
            lines.append("ADDED:")
            for node in delta.added:
                lines += [f"  {l}" for l in self._render_node(node, depth=0)]

        if delta.removed:
            lines.append("REMOVED:")
            for node in delta.removed:
                name_str = f' "{node.name}"' if node.name else ""
                lines.append(f"  - {node.role}{name_str} [{node.ref}]")

        if delta.changed:
            lines.append("CHANGED:")
            for change in delta.changed:
                lines.append(self._render_change(change))

        if delta.unchanged_summary:
            lines.append(f"UNCHANGED: {delta.unchanged_summary}")

        if state.screenshot_b64:
            lines += ["", "[VISUAL: screenshot attached]"]

        return "\n".join(lines)

    def _render_change(self, change: NodeChange) -> str:
        name_str = f' "{change.name}"' if change.name else ""
        prop_parts: list[str] = []
        for prop, (old, new) in change.changed_props.items():
            prop_parts.append(f"{prop}: {old!r} → {new!r}")
        props_str = ", ".join(prop_parts)
        return f"  - {change.role}{name_str} [{change.ref}] — {props_str}"
