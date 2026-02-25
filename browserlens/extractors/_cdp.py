"""
CDP-based accessibility tree extraction for Playwright 1.46+.

Playwright removed page.accessibility.snapshot() in 1.46.
This module uses Accessibility.getFullAXTree (Chrome DevTools Protocol)
to reconstruct an equivalent tree as StateNode objects.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from browserlens.core.types import StateNode
from browserlens.formatter.ref_manager import RefManager

# Internal Chrome role names → normalised role strings
_INTERNAL_ROLE_MAP: dict[str, str] = {
    "RootWebArea": "document",
    "StaticText": "text",
    "LineBreak": "text",
    "InlineTextBox": "text",
    "GenericContainer": "generic",
    "LayoutTable": "table",
    "LayoutTableRow": "row",
    "LayoutTableCell": "cell",
}

# Roles with no semantic meaning — pruned when they have no name AND no children
_STRUCTURAL_ROLES = frozenset({
    "generic", "none", "presentation", "text",
    "document",   # root carries no actionable info itself
})


def _ax_value(v: dict | None) -> Any:
    """Pull the concrete value out of a CDP AXValue envelope."""
    if v is None:
        return None
    return v.get("value")


def _get_props(raw_node: dict) -> dict[str, Any]:
    """Flatten the CDP properties array into a {name: value} dict."""
    return {
        p["name"]: _ax_value(p.get("value"))
        for p in raw_node.get("properties", [])
    }


async def extract_ax_tree(page: Page, ref_manager: RefManager) -> StateNode:
    """
    Extract the full accessibility tree via CDP and return a StateNode tree.

    Equivalent to the old page.accessibility.snapshot(interesting_only=True)
    but compatible with Playwright 1.46+.
    """
    cdp = await page.context.new_cdp_session(page)
    try:
        result = await cdp.send("Accessibility.getFullAXTree")
    finally:
        await cdp.detach()

    nodes: list[dict] = result.get("nodes", [])
    if not nodes:
        fp = ("document", "", "")
        return StateNode(ref=ref_manager.get_or_create(fp), role="document", name="")

    return _build_tree(nodes, ref_manager)


def _build_tree(nodes: list[dict], ref_manager: RefManager) -> StateNode:
    if not nodes:
        fp = ("document", "", "")
        return StateNode(ref=ref_manager.get_or_create(fp), role="document", name="")
    by_id: dict[str, dict] = {n["nodeId"]: n for n in nodes}
    # Root = the single node with no parentId (or empty string parentId)
    root_raw = next(
        (n for n in nodes if not n.get("parentId")),
        nodes[0],
    )
    return _convert_node(root_raw, by_id, ref_manager, parent_role="")


def _convert_node(
    raw: dict,
    by_id: dict[str, dict],
    ref_manager: RefManager,
    parent_role: str,
) -> StateNode:
    role_raw = raw.get("role", {})
    raw_role = role_raw.get("value", "generic") or "generic"
    role = _INTERNAL_ROLE_MAP.get(raw_role, raw_role)

    name = str(_ax_value(raw.get("name")) or "")
    value_raw = _ax_value(raw.get("value"))
    value = str(value_raw) if value_raw is not None else ""

    props = _get_props(raw)

    # checked
    checked_raw = props.get("checked")
    checked: bool | None = None
    if checked_raw is not None:
        checked = (checked_raw == "true") if isinstance(checked_raw, str) else bool(checked_raw)

    # expanded
    expanded_raw = props.get("expanded")
    expanded: bool | None = None
    if expanded_raw is not None:
        expanded = (expanded_raw == "true") if isinstance(expanded_raw, str) else bool(expanded_raw)

    # disabled / focused
    disabled_raw = props.get("disabled")
    disabled = disabled_raw is True or disabled_raw == "true"
    focused_raw = props.get("focused")
    focused = focused_raw is True or focused_raw == "true"

    # aria-live
    live_raw = props.get("live")
    live = str(live_raw) if live_raw and live_raw not in ("off", "none", False, None) else ""

    fp = (role, name, parent_role)
    ref = ref_manager.get_or_create(fp)

    node = StateNode(
        ref=ref,
        role=role,
        name=name,
        value=value,
        checked=checked,
        expanded=expanded,
        disabled=disabled,
        focused=focused,
        live=live,
    )

    # Process children — skip ignored nodes but keep traversing through them
    for child_id in raw.get("childIds", []):
        child_raw = by_id.get(child_id)
        if child_raw is None:
            continue
        if child_raw.get("ignored"):
            # Collect non-ignored descendants and attach them here
            for grandchild in _collect_unignored(child_raw, by_id, ref_manager, role):
                if _is_interesting(grandchild):
                    node.children.append(grandchild)
        else:
            child_node = _convert_node(child_raw, by_id, ref_manager, parent_role=role)
            if _is_interesting(child_node):
                node.children.append(child_node)

    return node


def _collect_unignored(
    ignored_node: dict,
    by_id: dict[str, dict],
    ref_manager: RefManager,
    parent_role: str,
) -> list[StateNode]:
    """Return non-ignored descendants of an ignored node, flattened one level up."""
    result: list[StateNode] = []
    for child_id in ignored_node.get("childIds", []):
        child_raw = by_id.get(child_id)
        if child_raw is None:
            continue
        if child_raw.get("ignored"):
            result.extend(_collect_unignored(child_raw, by_id, ref_manager, parent_role))
        else:
            node = _convert_node(child_raw, by_id, ref_manager, parent_role)
            result.append(node)
    return result


def _is_interesting(node: StateNode) -> bool:
    """
    Rough equivalent of Playwright's old interesting_only=True filter.
    Prune structural wrappers with no name and no children.
    """
    if node.role not in _STRUCTURAL_ROLES:
        return True  # any semantic role is worth keeping
    if node.name:
        return True  # has an accessible name
    if node.children:
        return True  # container for other interesting nodes
    return False
