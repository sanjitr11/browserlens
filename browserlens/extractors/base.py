"""Abstract base extractor."""

from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType
from browserlens.formatter.ref_manager import RefManager


class BaseExtractor(ABC):
    """All extractors share a RefManager so @eN IDs are stable across extractor switches."""

    def __init__(self, ref_manager: RefManager) -> None:
        self._refs = ref_manager

    @property
    @abstractmethod
    def representation_type(self) -> RepresentationType: ...

    @abstractmethod
    async def extract(self, page: Page) -> PageState: ...
