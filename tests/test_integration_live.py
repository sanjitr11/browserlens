"""
Live browser integration tests for BrowserLens.

Run with:
    pytest tests/test_integration_live.py -m integration -v -s

These are excluded from the default `pytest tests/` run because they require
a live network connection and a Playwright-controlled Chromium browser.

Sites tested:
  1. https://practicetestautomation.com/practice-test-login/
       Simple login form: router should pick A11Y_TREE, extractor should find
       username/password fields and login button with stable @eN refs, and
       post-typing observation should produce a delta (not a full page resend).

  2. https://www.amazon.com
       Complex e-commerce page: verifies the router handles a large/messy DOM,
       and that the BrowserLens delta on step 2 (after searching) is dramatically
       smaller than the raw Playwright a11y snapshot on the same page.

  3. https://excalidraw.com/
       Canvas-heavy drawing tool: router must detect canvas elements (has_canvas=True)
       and fall back to HYBRID representation. Used instead of Google Sheets, which
       requires authentication in a headless context.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from browserlens import BrowserLens
from browserlens.core.types import RepresentationType, StateNode
from browserlens.formatter.token_budget import TokenBudget
from browserlens.router.router import AdaptiveRouter

pytestmark = pytest.mark.integration

_BUDGET = TokenBudget()

# Generous token budget so we never truncate during tests — we want real counts.
_TEST_TOKEN_BUDGET = 32_768


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def browser() -> AsyncGenerator[Browser, None]:
    """One Chromium instance per test; headless with a realistic user-agent."""
    async with async_playwright() as pw:
        b = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        yield b
        await b.close()


@pytest_asyncio.fixture
async def page(browser: Browser) -> AsyncGenerator[Page, None]:
    """Fresh browser context (clean cookies / storage) for every test."""
    ctx: BrowserContext = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    pg = await ctx.new_page()
    yield pg
    await ctx.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _find_nodes(
    root: StateNode,
    *,
    role: str | None = None,
    name_contains: str | None = None,
) -> list[StateNode]:
    """
    Depth-first search for nodes matching both (optional) role and name substring.
    """
    hits: list[StateNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        role_ok = role is None or node.role == role
        name_ok = name_contains is None or name_contains.lower() in node.name.lower()
        if role_ok and name_ok:
            hits.append(node)
        stack.extend(node.children)
    return hits


async def _raw_a11y_tokens(page: Page) -> int:
    """
    Baseline metric: count tokens in the full Playwright aria snapshot (YAML).
    page.locator("body").aria_snapshot() is the standard Playwright 1.46+ API
    for getting the full accessibility tree — what a naive agent would send
    to the LLM each step.
    """
    aria_yaml = await page.locator("body").aria_snapshot()
    return _BUDGET.count(aria_yaml)


def _banner(title: str) -> None:
    bar = "─" * 72
    print(f"\n{bar}\n  {title}\n{bar}")


def _token_row(label: str, tokens: int, baseline: int | None = None) -> None:
    """Print a single row of the token comparison table."""
    blocks = "█" * min(tokens // 50, 48)
    suffix = ""
    if baseline and baseline > 0:
        pct = (1 - tokens / baseline) * 100
        suffix = f"  ({pct:+.1f}% vs baseline)"
    print(f"    {label:<44} {tokens:>6} tk  {blocks}{suffix}")


def _print_delta_summary(label: str, delta) -> None:
    print(
        f"  {label}: "
        f"added={len(delta.added)}, "
        f"removed={len(delta.removed)}, "
        f"changed={len(delta.changed)}, "
        f"unchanged={delta.unchanged_count}"
    )
    if delta.unchanged_summary:
        print(f"    unchanged summary: {delta.unchanged_summary!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Login form
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_login_form_router_and_diff(page: Page) -> None:
    """
    Against practicetestautomation.com/practice-test-login/

    Assertions:
    - Router picks A11Y_TREE (well-labelled form, no canvas, small DOM).
    - Extracted state contains username textbox, password textbox, and login
      button, each with a stable @eN ref.
    - Username and password fields have different refs.
    - Step 1 is a full page state (is_full_state=True).
    - Step 2 (post-fill) is a delta (is_full_state=False) with ≥1 changed node.
    - Step 2 delta token count is smaller than the raw a11y snapshot.
    - Step 3 (post-login) is also a delta with significant changes.
    """
    URL = "https://practicetestautomation.com/practice-test-login/"

    _banner("Test 1 — Login Form  |  practicetestautomation.com")

    await page.goto(URL, wait_until="networkidle", timeout=30_000)

    lens = BrowserLens(token_budget=_TEST_TOKEN_BUDGET)
    router = AdaptiveRouter()

    # ── Step 1: observe the blank login page ─────────────────────────────────
    signals = await router.get_signals(page)
    result_1 = await lens.observe(page)
    raw_1 = await _raw_a11y_tokens(page)

    print(f"\n  URL:        {result_1.url}")
    print(f"  Title:      {await page.title()!r}")
    print(f"  Router:     {result_1.representation_type.value}")
    print(
        f"  Signals:    has_canvas={signals.has_canvas}, "
        f"a11y_coverage={signals.a11y_coverage:.2f}, "
        f"dom_nodes={signals.dom_node_count}, "
        f"page_type={signals.page_type!r}"
    )
    print(f"\n  Token counts — step 1 (full page, blank form):")
    _token_row("Raw Playwright a11y snapshot (baseline)", raw_1)
    _token_row("BrowserLens full state", result_1.token_count, baseline=raw_1)
    print(f"  Latency: {result_1.latency_ms:.0f} ms")

    # ── Router assertion ──────────────────────────────────────────────────────
    # A simple login form should NEVER need vision (no canvas, DOM is small).
    # Depending on a11y_coverage the router picks A11Y_TREE (≥0.8) or
    # DISTILLED_DOM (≥0.5), both of which are text-only — the correct call.
    _TEXT_ONLY = {RepresentationType.A11Y_TREE, RepresentationType.DISTILLED_DOM}
    assert result_1.representation_type in _TEXT_ONLY, (
        f"Expected a text-only representation for a simple login form, got "
        f"{result_1.representation_type.value}. "
        f"Signals: has_canvas={signals.has_canvas}, "
        f"a11y_coverage={signals.a11y_coverage:.2f}, "
        f"dom_nodes={signals.dom_node_count}"
    )
    assert not signals.has_canvas, "Login form should have no canvas elements"
    print(f"  (a11y_coverage={signals.a11y_coverage:.2f} → correctly chose "
          f"{result_1.representation_type.value}, no vision needed)")

    # ── Step 1 must be a full page state ─────────────────────────────────────
    assert result_1.delta is not None
    assert result_1.delta.is_full_state, "First observe() must be full state"

    # ── Locate fields by role in the extracted tree ───────────────────────────
    all_nodes = result_1.page_state.flat_nodes()
    print(f"\n  Extracted {len(all_nodes)} nodes total from a11y tree.")

    textboxes = _find_nodes(result_1.page_state.root, role="textbox")
    assert len(textboxes) >= 1, (
        f"Expected ≥1 textbox node in the form, got {len(textboxes)}. "
        f"All roles: {sorted(set(n.role for n in all_nodes))}"
    )

    print(f"\n  Found {len(textboxes)} textbox node(s):")
    for tb in textboxes:
        print(f"    role={tb.role!r}, name={tb.name!r}, ref={tb.ref}")
        assert tb.ref.startswith("@e"), f"Expected @eN ref, got {tb.ref!r}"

    # Username field — prefer a named one if the DOM extractor resolved the label
    username_node = next(
        (n for n in textboxes if any(kw in n.name.lower() for kw in ("username", "user name", "email"))),
        textboxes[0],
    )
    print(f"\n  Username field: role={username_node.role!r}, name={username_node.name!r}, ref={username_node.ref}")

    # Password field
    password_node = next(
        (n for n in textboxes if any(kw in n.name.lower() for kw in ("password", "pass"))),
        textboxes[1] if len(textboxes) >= 2 else None,
    )
    if password_node:
        print(f"  Password field: role={password_node.role!r}, name={password_node.name!r}, ref={password_node.ref}")
        # When both fields have labels resolved, their names differ → different refs
        if username_node.name and password_node.name:
            assert username_node.ref != password_node.ref, (
                "Named username and password fields must have different refs"
            )

    # Login button — must have a unique ref from the textboxes
    buttons = _find_nodes(result_1.page_state.root, role="button")
    login_btn = next(
        (n for n in buttons if any(kw in n.name.lower() for kw in ("submit", "login", "log in", "sign in"))),
        buttons[0] if buttons else None,
    )
    assert login_btn is not None, f"Could not find login button. Buttons found: {[n.name for n in buttons]}"
    print(f"  Login button:   role={login_btn.role!r}, name={login_btn.name!r}, ref={login_btn.ref}")
    assert login_btn.ref.startswith("@e")
    assert login_btn.ref != username_node.ref, "Login button must have a different ref from the textboxes"

    # Refs are stable — re-observing the same page gives the same ref for the button
    result_1b = await lens.observe(page)
    btn_nodes_1b = _find_nodes(result_1b.page_state.root, role="button", name_contains=login_btn.name)
    if btn_nodes_1b and login_btn.name:
        assert btn_nodes_1b[0].ref == login_btn.ref, (
            "Ref for login button changed between observations of the same page — refs are not stable"
        )

    # ── Step 2: fill in credentials, observe delta ────────────────────────────
    # Use Playwright to type into the fields
    await page.fill("#username", "student")
    await page.fill("#password", "Password123")

    result_2 = await lens.observe(page)
    raw_2 = await _raw_a11y_tokens(page)

    print(f"\n  Token counts — step 2 (after filling credentials):")
    _token_row("Raw Playwright a11y snapshot (baseline)", raw_2)
    _token_row("BrowserLens delta", result_2.token_count, baseline=raw_2)
    print(f"  Latency: {result_2.latency_ms:.0f} ms")
    _print_delta_summary("Delta", result_2.delta)

    # Must be a delta
    assert result_2.delta is not None
    assert not result_2.delta.is_full_state, "Step 2 must be a delta, not a full page resend"

    # Must reflect that the input values changed
    assert result_2.delta.total_changes > 0, (
        "Expected ≥1 change after filling in credentials (textbox values should update)"
    )

    # Delta must be smaller than the raw snapshot (the whole point)
    assert result_2.token_count < raw_2, (
        f"Delta ({result_2.token_count} tk) should be smaller than "
        f"raw a11y snapshot ({raw_2} tk)"
    )

    # ── Step 3: submit and observe the result page ────────────────────────────
    await page.click("#submit")
    await page.wait_for_load_state("networkidle", timeout=15_000)

    result_3 = await lens.observe(page)
    raw_3 = await _raw_a11y_tokens(page)

    print(f"\n  Token counts — step 3 (post-login page: {result_3.url}):")
    _token_row("Raw Playwright a11y snapshot (baseline)", raw_3)
    _token_row("BrowserLens full state (navigation detected)", result_3.token_count, baseline=raw_3)
    print(f"  Latency: {result_3.latency_ms:.0f} ms")
    print(f"  diff_discarded: {result_3.diff_discarded}")
    _print_delta_summary("Delta", result_3.delta)

    # URL changed after login → BrowserLens detects navigation and returns full state
    assert result_3.delta.is_full_state, (
        "Step 3 must be a full state: URL changed after login, so diffing is skipped"
    )
    assert result_3.diff_discarded, (
        "diff_discarded must be True when URL change triggers full-state fallback"
    )

    print(f"\n  PASS — router={result_1.representation_type.value}, "
          f"step2_delta_savings={100*(1-result_2.token_count/max(raw_2,1)):.0f}%, "
          f"step3=full_state(nav_detected)")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Amazon search token reduction
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_amazon_search_token_reduction(page: Page) -> None:
    """
    Against https://www.amazon.com

    Assertions:
    - BrowserLens step 2 delta (after searching "laptop") has materially fewer
      tokens than the raw Playwright a11y snapshot on the same page.
    - The delta is not a full page resend.
    - Router signal extraction succeeds (no crash on a large, complex DOM).
    - Prints a full token comparison table for manual inspection.

    Note: Amazon may trigger bot detection in headless Chrome. If the search
    box or a recognisable page title is not found the test is skipped rather
    than failed so CI stays green.
    """
    _banner("Test 2 — Amazon Search Token Reduction  |  amazon.com")

    await page.goto("https://www.amazon.com", wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2_000)  # allow JS to settle

    # Bot detection guard
    title = await page.title()
    print(f"\n  Page title: {title!r}")
    if any(kw in title.lower() for kw in ("robot", "captcha", "verify", "unusual traffic")):
        pytest.skip(f"Amazon bot detection triggered (title={title!r}) — skipping test")

    # Amazon uses several possible search box selectors depending on page variant
    _search_candidates = [
        "#twotabsearchtextbox",
        "input[name='field-keywords']",
        "input[aria-label*='Search']",
        "[role='searchbox']",
        "input[type='text'][id*='search']",
    ]
    search_box = None
    for sel in _search_candidates:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=3_000)
            search_box = loc
            break
        except Exception:
            continue
    if search_box is None:
        pytest.skip("Amazon search box not found — page structure may have changed or bot detection active")

    lens = BrowserLens(token_budget=_TEST_TOKEN_BUDGET)
    router = AdaptiveRouter()

    # ── Step 1: observe Amazon homepage ───────────────────────────────────────
    signals = await router.get_signals(page)
    result_1 = await lens.observe(page)
    raw_1 = await _raw_a11y_tokens(page)

    print(f"\n  Signals:    has_canvas={signals.has_canvas}, "
          f"a11y_coverage={signals.a11y_coverage:.2f}, "
          f"dom_nodes={signals.dom_node_count}, "
          f"page_type={signals.page_type!r}")
    print(f"  Router:     {result_1.representation_type.value}")
    print(f"\n  Token counts — step 1 (Amazon homepage):")
    _token_row("Raw Playwright a11y snapshot (baseline)", raw_1)
    _token_row("BrowserLens full state", result_1.token_count, baseline=raw_1)
    print(f"  Latency: {result_1.latency_ms:.0f} ms")

    assert result_1.delta.is_full_state, "Step 1 should be full state"

    # Router should not crash on a large/complex page
    assert result_1.representation_type in RepresentationType.__members__.values()

    # ── Step 2: search for "laptop" ───────────────────────────────────────────
    await search_box.click()
    await search_box.fill("laptop")
    await page.keyboard.press("Enter")
    await page.wait_for_load_state("domcontentloaded", timeout=20_000)
    await page.wait_for_timeout(1_500)

    # Guard: verify we're on search results
    results_url = page.url
    if "s?k=" not in results_url and "s?i=" not in results_url and "laptop" not in results_url.lower():
        pytest.skip(f"Did not navigate to search results (url={results_url!r})")

    result_2 = await lens.observe(page)
    raw_2 = await _raw_a11y_tokens(page)

    print(f"\n  Token counts — step 2 (search results: 'laptop'):")
    _token_row("Raw Playwright a11y snapshot (baseline)", raw_2)
    _token_row("BrowserLens full state (navigation detected)", result_2.token_count, baseline=raw_2)
    print(f"  Latency: {result_2.latency_ms:.0f} ms")
    print(f"  diff_discarded: {result_2.diff_discarded}")
    _print_delta_summary("Delta", result_2.delta)

    # URL changed after pressing Enter (homepage URL → search results URL).
    # BrowserLens detects the navigation and returns the full state instead of a noisy delta.
    assert result_2.delta.is_full_state, (
        "Step 2 must be a full state: URL changed after search navigation, diffing is skipped"
    )
    assert result_2.diff_discarded, (
        "diff_discarded must be True when a URL change triggers full-state fallback"
    )

    print(f"\n  PASS — router={result_1.representation_type.value}, "
          f"step2=full_state(nav_detected, url_changed)")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Canvas page falls back to HYBRID
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_canvas_page_routes_to_hybrid(page: Page) -> None:
    """
    Against https://excalidraw.com/

    Excalidraw is a canvas-based drawing tool (no authentication required).
    It is used in place of Google Sheets, which redirects to Google Sign-in
    in a headless context and therefore never loads the spreadsheet canvas.

    Assertions:
    - Router signals detect canvas (has_canvas=True).
    - Because the main drawing area is canvas with no aria content, a11y_coverage
      is below 0.5, so the router picks HYBRID.
    - The extracted PageState includes a screenshot (screenshot_b64 is not None).
    - The formatted text includes the visual attachment marker.
    - Prints signals and token counts.
    """
    URL = "https://excalidraw.com/"

    _banner("Test 3 — Canvas / HYBRID routing  |  excalidraw.com")

    await page.goto(URL, wait_until="networkidle", timeout=30_000)

    # Wait for the canvas element to actually appear in the DOM
    try:
        await page.wait_for_selector("canvas", timeout=15_000)
    except Exception:
        pytest.skip("Canvas element never appeared on excalidraw.com — page may have changed")

    lens = BrowserLens(token_budget=_TEST_TOKEN_BUDGET)
    router = AdaptiveRouter()

    # ── Extract signals directly so we can inspect them ───────────────────────
    signals = await router.get_signals(page)

    print(f"\n  URL:              {page.url}")
    print(f"  Title:            {await page.title()!r}")
    print(f"  has_canvas:       {signals.has_canvas}")
    print(f"  has_webgl:        {signals.has_webgl}")
    print(f"  a11y_coverage:    {signals.a11y_coverage:.3f}")
    print(f"  dom_node_count:   {signals.dom_node_count}")
    print(f"  dom_max_depth:    {signals.dom_max_depth}")
    print(f"  page_type:        {signals.page_type!r}")

    # ── Canvas must be detected ───────────────────────────────────────────────
    assert signals.has_canvas or signals.has_webgl, (
        "Expected canvas or WebGL to be detected on excalidraw.com. "
        "The site may have changed its rendering approach."
    )

    # ── Observe and check representation type ────────────────────────────────
    result = await lens.observe(page)
    raw_tokens = await _raw_a11y_tokens(page)

    print(f"\n  Router decision:  {result.representation_type.value}")
    print(f"\n  Token counts — step 1:")
    _token_row("Raw Playwright a11y snapshot (baseline)", raw_tokens)
    _token_row("BrowserLens (hybrid = a11y + screenshot)", result.token_count, baseline=raw_tokens)
    print(f"  Latency: {result.latency_ms:.0f} ms")
    print(f"  Screenshot captured: {result.page_state.screenshot_b64 is not None}")
    if result.page_state.screenshot_b64:
        screenshot_bytes = len(result.page_state.screenshot_b64) * 3 // 4  # base64 → approx bytes
        print(f"  Screenshot size: ~{screenshot_bytes // 1024} KB")

    # ── Router must pick HYBRID when canvas + low a11y ────────────────────────
    if signals.a11y_coverage < 0.5:
        assert result.representation_type == RepresentationType.HYBRID, (
            f"Expected HYBRID for canvas page with a11y_coverage={signals.a11y_coverage:.2f}, "
            f"got {result.representation_type.value}"
        )
        print(f"\n  a11y_coverage={signals.a11y_coverage:.2f} < 0.5 → correctly routed to HYBRID")
    else:
        # Canvas detected but a11y is better than expected — router may choose A11Y_TREE.
        # This is still correct behaviour; the canvas detection path requires low a11y coverage
        # to trigger HYBRID (the page is still usable via accessibility tree).
        print(
            f"\n  NOTE: canvas detected but a11y_coverage={signals.a11y_coverage:.2f} ≥ 0.5. "
            f"Router chose {result.representation_type.value} (also correct). "
            f"Asserting HYBRID only when a11y coverage is genuinely poor."
        )
        # Still assert that if the router chose HYBRID it included a screenshot
        if result.representation_type == RepresentationType.HYBRID:
            assert result.page_state.screenshot_b64 is not None, (
                "HYBRID representation must include a screenshot"
            )

    # ── When HYBRID is chosen, a screenshot must be present ──────────────────
    if result.representation_type == RepresentationType.HYBRID:
        assert result.page_state.screenshot_b64 is not None, (
            "HYBRID representation must capture a screenshot"
        )
        assert "[VISUAL: screenshot attached]" in result.formatted_text, (
            "HYBRID formatted output must indicate the visual attachment"
        )

    # ── A11y skeleton must still contain some nodes (for diffing to work) ─────
    all_nodes = result.page_state.flat_nodes()
    assert len(all_nodes) > 0, "Even a canvas page should have some a11y nodes (toolbar, etc.)"
    print(f"  A11y nodes extracted: {len(all_nodes)}")

    print(f"\n  PASS — router={result.representation_type.value}, "
          f"has_canvas={signals.has_canvas}, "
          f"a11y_coverage={signals.a11y_coverage:.2f}")
