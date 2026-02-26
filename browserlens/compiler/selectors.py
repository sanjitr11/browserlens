"""Layer 3 — Selector generation from live DOM."""

from __future__ import annotations

from playwright.async_api import Page

from browserlens.compiler.types import ElementTarget, SelectorStrategy

# Priority order for selector strategies (most reliable first)
_STRATEGY_PRIORITY: list[SelectorStrategy] = [
    SelectorStrategy.TEST_ID,
    SelectorStrategy.ROLE_NAME,
    SelectorStrategy.LABEL,
    SelectorStrategy.PLACEHOLDER,
    SelectorStrategy.TEXT,
    SelectorStrategy.CSS,
    SelectorStrategy.XPATH,
]

# Maps ARIA roles to HTML tag names for DOM lookup
_ROLE_TAG_MAP: dict[str, str] = {
    "button": "button",
    "textbox": "input",
    "checkbox": "input",
    "radio": "input",
    "combobox": "select",
    "listbox": "select",
    "link": "a",
    "heading": "h1,h2,h3,h4,h5,h6",
    "img": "img",
    "searchbox": "input",
}

_JS_GENERATE_SELECTORS = """
(args) => {
    const { role, name, value, roleTagMap } = args;

    function escapeAttr(s) {
        return s.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
    }

    function escapeCSS(s) {
        return s.replace(/([!"#$%&'()*+,.\\/;<=>?@[\\\\\\]^`{|}~])/g, '\\\\$1');
    }

    // Try to find element by aria-label first, then by role+value/placeholder
    let el = null;

    if (name) {
        el = document.querySelector('[aria-label="' + escapeAttr(name) + '"]');
    }

    if (!el && name) {
        // Try by accessible name via placeholder
        el = document.querySelector('[placeholder="' + escapeAttr(name) + '"]');
    }

    if (!el && role && roleTagMap[role]) {
        const tags = roleTagMap[role].split(',');
        for (const tag of tags) {
            const candidates = document.querySelectorAll(tag);
            for (const c of candidates) {
                const label = c.getAttribute('aria-label') || c.getAttribute('placeholder') || c.textContent.trim();
                if (label === name || (value && c.value === value)) {
                    el = c;
                    break;
                }
            }
            if (el) break;
        }
    }

    const result = {};

    if (!el) {
        return result;
    }

    // test_id
    const tid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-cy');
    if (tid) {
        result.test_id = tid;
    }

    // label
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) {
        result.label = ariaLabel;
    } else {
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const labelEl = document.getElementById(labelledBy);
            if (labelEl) result.label = labelEl.textContent.trim();
        }
        if (!result.label) {
            const id = el.id;
            if (id) {
                const forLabel = document.querySelector('label[for="' + escapeAttr(id) + '"]');
                if (forLabel) result.label = forLabel.textContent.trim();
            }
        }
    }

    // placeholder
    const ph = el.getAttribute('placeholder');
    if (ph) {
        result.placeholder = ph;
    }

    // text (for buttons and links)
    const tagName = el.tagName.toLowerCase();
    if (tagName === 'button' || tagName === 'a' || tagName === 'label') {
        const txt = el.textContent.trim();
        if (txt && txt.length <= 100) {
            result.text = txt;
        }
    }

    // css selector
    let css = '';
    if (el.id) {
        css = '#' + escapeCSS(el.id);
    } else {
        // Build path up to 4 ancestors
        const parts = [];
        let node = el;
        let depth = 0;
        while (node && node !== document.body && depth < 4) {
            let part = node.tagName.toLowerCase();
            const classes = Array.from(node.classList).slice(0, 2);
            if (classes.length) {
                part += '.' + classes.map(c => escapeCSS(c)).join('.');
            }
            // nth-of-type for disambiguation
            let nth = 1;
            let sib = node.previousElementSibling;
            while (sib) {
                if (sib.tagName === node.tagName) nth++;
                sib = sib.previousElementSibling;
            }
            if (nth > 1) {
                part += ':nth-of-type(' + nth + ')';
            }
            parts.unshift(part);
            node = node.parentElement;
            depth++;
        }
        css = parts.join(' > ');
    }
    if (css) result.css = css;

    // xpath
    function getXPath(element) {
        if (element.id) {
            return '//*[@id="' + element.id + '"]';
        }
        const parts = [];
        let el = element;
        while (el && el.nodeType === Node.ELEMENT_NODE) {
            let index = 0;
            let hasSiblings = false;
            let sib = el.previousSibling;
            while (sib) {
                if (sib.nodeType === Node.ELEMENT_NODE && sib.tagName === el.tagName) {
                    index++;
                    hasSiblings = true;
                }
                sib = sib.previousSibling;
            }
            sib = el.nextSibling;
            while (sib) {
                if (sib.nodeType === Node.ELEMENT_NODE && sib.tagName === el.tagName) {
                    hasSiblings = true;
                    break;
                }
                sib = sib.nextSibling;
            }
            const tag = el.tagName.toLowerCase();
            parts.unshift(hasSiblings ? tag + '[' + (index + 1) + ']' : tag);
            el = el.parentNode;
        }
        return '/' + parts.join('/');
    }
    result.xpath = getXPath(el);

    return result;
}
"""


class SelectorGenerator:
    """Generates robust CSS/ARIA selectors for a DOM element."""

    async def generate(
        self,
        page: Page,
        ref: str,
        role: str,
        name: str,
        value: str = "",
    ) -> ElementTarget:
        """
        Generate selectors for an element identified by role and name.

        Must be called while the element is still in the DOM.
        """
        raw: dict = await page.evaluate(
            _JS_GENERATE_SELECTORS,
            {"role": role, "name": name, "value": value, "roleTagMap": _ROLE_TAG_MAP},
        )

        selectors: dict[SelectorStrategy, str] = {}

        if raw.get("test_id"):
            selectors[SelectorStrategy.TEST_ID] = raw["test_id"]

        # ROLE_NAME is always synthesized from a11y data — no DOM query needed
        if role and name:
            selectors[SelectorStrategy.ROLE_NAME] = f"{role}::{name}"

        if raw.get("label"):
            selectors[SelectorStrategy.LABEL] = raw["label"]

        if raw.get("placeholder"):
            selectors[SelectorStrategy.PLACEHOLDER] = raw["placeholder"]

        if raw.get("text"):
            selectors[SelectorStrategy.TEXT] = raw["text"]

        if raw.get("css"):
            selectors[SelectorStrategy.CSS] = raw["css"]

        if raw.get("xpath"):
            selectors[SelectorStrategy.XPATH] = raw["xpath"]

        # Filter priority to only found strategies
        selector_priority = [s for s in _STRATEGY_PRIORITY if s in selectors]

        return ElementTarget(
            ref=ref,
            role=role,
            name=name,
            selectors=selectors,
            selector_priority=selector_priority,
        )
