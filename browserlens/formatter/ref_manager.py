"""RefManager — issues and maintains stable @eN reference IDs."""

from __future__ import annotations


class RefManager:
    """
    Maps (role, name, parent_role) fingerprints to stable @eN IDs.

    A fingerprint that has been seen before gets the same ID on every
    subsequent step, even if the underlying DOM node shuffled around.
    New fingerprints get the next available ID.
    """

    def __init__(self) -> None:
        self._counter = 0
        self._fp_to_ref: dict[tuple[str, str, str], str] = {}
        self._ref_to_fp: dict[str, tuple[str, str, str]] = {}

    def get_or_create(self, fingerprint: tuple[str, str, str]) -> str:
        if fingerprint in self._fp_to_ref:
            return self._fp_to_ref[fingerprint]
        self._counter += 1
        ref = f"@e{self._counter}"
        self._fp_to_ref[fingerprint] = ref
        self._ref_to_fp[ref] = fingerprint
        return ref

    def lookup(self, ref: str) -> tuple[str, str, str] | None:
        return self._ref_to_fp.get(ref)

    def reset(self) -> None:
        self._counter = 0
        self._fp_to_ref.clear()
        self._ref_to_fp.clear()

    @property
    def total_refs(self) -> int:
        return self._counter
