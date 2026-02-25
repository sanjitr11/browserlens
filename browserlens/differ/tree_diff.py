"""Tree-based diff algorithm for a11y/DOM StateNode trees."""

from __future__ import annotations

from typing import Any

from browserlens.core.types import Delta, NodeChange, RepresentationType, StateNode

# Properties compared between matched nodes
_COMPARED_PROPS: tuple[str, ...] = (
    "value", "checked", "expanded", "disabled", "focused", "live",
)


def diff_trees(
    old_root: StateNode,
    new_root: StateNode,
    step: int,
    rep_type: RepresentationType,
) -> Delta:
    """
    Diff two StateNode trees.

    Matching strategy (in priority order):
      1. Exact ref ID match (most reliable when IDs persist)
      2. (role, name, parent_role) fingerprint match
    """
    old_nodes = _index_nodes(old_root)
    new_nodes = _index_nodes(new_root)

    added: list[StateNode] = []
    removed: list[StateNode] = []
    changed: list[NodeChange] = []
    matched_old_refs: set[str] = set()

    for new_ref, (new_node, new_parent_role) in new_nodes.items():
        if new_ref in old_nodes:
            # Matched by ref ID
            old_node, _ = old_nodes[new_ref]
            matched_old_refs.add(new_ref)
            props_diff = _compare_props(old_node, new_node)
            if props_diff:
                changed.append(NodeChange(
                    ref=new_ref,
                    role=new_node.role,
                    name=new_node.name,
                    changed_props=props_diff,
                ))
        else:
            # Try fingerprint match
            fp = (new_node.role, new_node.name, new_parent_role)
            old_match = _find_by_fingerprint(fp, old_nodes, matched_old_refs)
            if old_match is not None:
                old_ref, old_node = old_match
                matched_old_refs.add(old_ref)
                props_diff = _compare_props(old_node, new_node)
                if props_diff:
                    changed.append(NodeChange(
                        ref=new_ref,
                        role=new_node.role,
                        name=new_node.name,
                        changed_props=props_diff,
                    ))
            else:
                # Truly new node
                added.append(new_node)

    # Any old node not matched = removed
    for old_ref, (old_node, _) in old_nodes.items():
        if old_ref not in matched_old_refs:
            removed.append(old_node)

    unchanged_count = len(new_nodes) - len(added) - len(changed)

    return Delta(
        step=step,
        added=added,
        removed=removed,
        changed=changed,
        unchanged_count=max(unchanged_count, 0),
        representation_type=rep_type,
    )


def _index_nodes(root: StateNode) -> dict[str, tuple[StateNode, str]]:
    """Flat map: ref → (node, parent_role) via depth-first traversal."""
    result: dict[str, tuple[StateNode, str]] = {}
    _walk(root, parent_role="", result=result)
    return result


def _walk(node: StateNode, parent_role: str, result: dict[str, tuple[StateNode, str]]) -> None:
    result[node.ref] = (node, parent_role)
    for child in node.children:
        _walk(child, parent_role=node.role, result=result)


def _compare_props(old: StateNode, new: StateNode) -> dict[str, tuple[Any, Any]]:
    diff: dict[str, tuple[Any, Any]] = {}
    for prop in _COMPARED_PROPS:
        old_val = getattr(old, prop)
        new_val = getattr(new, prop)
        if old_val != new_val:
            diff[prop] = (old_val, new_val)
    return diff


def _find_by_fingerprint(
    fp: tuple[str, str, str],
    old_nodes: dict[str, tuple[StateNode, str]],
    already_matched: set[str],
) -> tuple[str, StateNode] | None:
    """Find first unmatched old node with the same (role, name, parent_role) fingerprint."""
    role, name, parent_role = fp
    for ref, (node, node_parent_role) in old_nodes.items():
        if ref in already_matched:
            continue
        if node.role == role and node.name == name and node_parent_role == parent_role:
            return ref, node
    return None
