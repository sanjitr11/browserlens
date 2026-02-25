"""Snapshot store — holds the previous PageState for diffing."""

from __future__ import annotations

from browserlens.core.types import PageState


class SnapshotStore:
    """
    Single-slot store: keeps the most recent PageState.
    Extended to a multi-slot history if needed for back-navigation detection.
    """

    def __init__(self) -> None:
        self._previous: PageState | None = None

    def get_previous(self) -> PageState | None:
        return self._previous

    def update(self, state: PageState) -> None:
        self._previous = state

    def reset(self) -> None:
        self._previous = None
