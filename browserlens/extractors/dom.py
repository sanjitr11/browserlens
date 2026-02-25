"""Distilled DOM extractor — inspired by Agent-E's DOM distillation approach."""

from __future__ import annotations

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType, StateNode
from browserlens.extractors.base import BaseExtractor
from browserlens.formatter.ref_manager import RefManager

# Elements that carry semantic/interactive meaning for an agent
_KEPT_TAGS = {
    "a", "button", "input", "select", "textarea", "form",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "nav", "main", "header", "footer", "aside", "section", "article",
    "table", "th", "td", "tr", "ul", "ol", "li",
    "label", "fieldset", "legend",
    "dialog", "details", "summary",
    "img",  # kept for alt text
}

_DOM_EXTRACTION_JS = """() => {
    const KEPT_TAGS = new Set([
        'A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'FORM',
        'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
        'NAV', 'MAIN', 'HEADER', 'FOOTER', 'ASIDE', 'SECTION', 'ARTICLE',
        'TABLE', 'TH', 'TD', 'TR', 'UL', 'OL', 'LI',
        'LABEL', 'FIELDSET', 'LEGEND',
        'DIALOG', 'DETAILS', 'SUMMARY',
        'IMG',
    ]);

    function getRole(el) {
        const role = el.getAttribute('role');
        if (role) return role;
        const tag = el.tagName.toLowerCase();
        const roleMap = {
            a: 'link', button: 'button', input: _inputRole(el),
            select: 'combobox', textarea: 'textbox',
            h1: 'heading', h2: 'heading', h3: 'heading',
            h4: 'heading', h5: 'heading', h6: 'heading',
            nav: 'navigation', main: 'main', header: 'banner',
            footer: 'contentinfo', aside: 'complementary',
            section: 'region', article: 'article',
            table: 'table', ul: 'list', ol: 'list', li: 'listitem',
            dialog: 'dialog', details: 'group', img: 'img',
        };
        return roleMap[tag] || tag;
    }

    function _inputRole(el) {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        const map = {
            checkbox: 'checkbox', radio: 'radio', submit: 'button',
            button: 'button', reset: 'button', range: 'slider',
            search: 'searchbox',
        };
        return map[t] || 'textbox';
    }

    function getName(el) {
        return (
            el.getAttribute('aria-label') ||
            el.getAttribute('title') ||
            el.getAttribute('placeholder') ||
            el.getAttribute('alt') ||
            el.innerText?.trim().slice(0, 80) ||
            el.value?.trim() ||
            ''
        );
    }

    function serializeNode(el, depth) {
        if (depth > 20) return null;
        const tag = el.tagName;
        if (!tag) return null;

        const keep = KEPT_TAGS.has(tag);
        const children = [];
        for (const child of el.children) {
            const s = serializeNode(child, depth + 1);
            if (s) children.push(s);
        }

        if (!keep && children.length === 0) return null;

        const node = {
            tag: tag.toLowerCase(),
            role: getRole(el),
            name: getName(el),
            value: el.value || '',
            checked: el.checked !== undefined ? el.checked : null,
            expanded: el.getAttribute('aria-expanded'),
            disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
            children,
        };
        return node;
    }

    return serializeNode(document.body, 0);
}"""


class DOMExtractor(BaseExtractor):
    """
    Extracts a distilled DOM tree: only semantically meaningful elements,
    pruning layout divs, spans, and other non-interactive wrappers.
    """

    def __init__(self, ref_manager: RefManager) -> None:
        super().__init__(ref_manager)

    @property
    def representation_type(self) -> RepresentationType:
        return RepresentationType.DISTILLED_DOM

    async def extract(self, page: Page) -> PageState:
        raw = await page.evaluate(_DOM_EXTRACTION_JS)
        root = self._convert_node(raw or {})
        return PageState(
            url=page.url,
            title=await page.title(),
            representation_type=self.representation_type,
            root=root,
        )

    def _convert_node(self, raw: dict, parent_role: str = "") -> StateNode:
        role = raw.get("role", "generic")
        name = raw.get("name", "")

        fingerprint = (role, name, parent_role)
        ref = self._refs.get_or_create(fingerprint)

        expanded_raw = raw.get("expanded")
        expanded: bool | None = None
        if expanded_raw is not None:
            expanded = str(expanded_raw).lower() == "true"

        node = StateNode(
            ref=ref,
            role=role,
            name=name,
            value=raw.get("value", "") or "",
            checked=raw.get("checked"),
            expanded=expanded,
            disabled=raw.get("disabled", False),
        )

        for child_raw in raw.get("children", []):
            node.children.append(self._convert_node(child_raw, parent_role=role))

        return node
