"""
BrowserLens benchmark — token usage and latency across multi-step workflows.

Run:
    python benchmarks/run_benchmark.py

For each site the script executes a realistic multi-step workflow, recording
at every step:
  • Raw baseline  — page.locator("body").aria_snapshot() (Playwright 1.46+
                    standard API, what a naive agent would send each step)
  • BrowserLens   — lens.observe() output (full state on step 1, delta after)
  • Latency       — wall-clock ms for lens.observe()
  • Full/delta    — whether this step was a full resend or a diff

Results are printed as a formatted table and saved to benchmarks/results.json.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Coroutine

from playwright.async_api import Browser, Page, async_playwright

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

from browserlens import BrowserLens
from browserlens.formatter.token_budget import TokenBudget

_BUDGET = TokenBudget()
_RESULTS_PATH = Path(__file__).parent / "results.json"

# Token budget high enough that nothing is truncated during benchmarks
_TOKEN_BUDGET = 32_768


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StepResult:
    step_num: int           # 1-based
    label: str              # human description of this step
    url: str
    raw_tokens: int         # baseline: aria_snapshot YAML token count
    lens_tokens: int        # BrowserLens output token count
    reduction_pct: float    # (1 - lens/raw) * 100  — positive = savings
    latency_ms: float       # lens.observe() wall-clock time
    is_delta: bool          # False on step 1, True after
    representation: str     # a11y_tree / distilled_dom / hybrid / vision
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class SiteResult:
    site_name: str
    url: str
    steps: list[StepResult] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def total_raw(self) -> int:
        return sum(s.raw_tokens for s in self.steps if not s.skipped)

    @property
    def total_lens(self) -> int:
        return sum(s.lens_tokens for s in self.steps if not s.skipped)

    @property
    def overall_reduction_pct(self) -> float:
        if self.total_raw == 0:
            return 0.0
        return (1 - self.total_lens / self.total_raw) * 100


@dataclass
class BenchmarkReport:
    sites: list[SiteResult] = field(default_factory=list)
    ran_at: str = ""

    @property
    def grand_raw(self) -> int:
        return sum(s.total_raw for s in self.sites if not s.skipped)

    @property
    def grand_lens(self) -> int:
        return sum(s.total_lens for s in self.sites if not s.skipped)

    @property
    def grand_reduction_pct(self) -> float:
        if self.grand_raw == 0:
            return 0.0
        return (1 - self.grand_lens / self.grand_raw) * 100


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _raw_tokens(page: Page) -> int:
    """Baseline: token count of Playwright's aria_snapshot YAML."""
    yaml = await page.locator("body").aria_snapshot()
    return _BUDGET.count(yaml)


async def _observe_step(
    lens: BrowserLens,
    page: Page,
    label: str,
    step_num: int,
) -> StepResult:
    """Run lens.observe() and collect all metrics for one step."""
    raw = await _raw_tokens(page)
    result = await lens.observe(page)

    reduction = (1 - result.token_count / max(raw, 1)) * 100
    is_delta = result.delta is not None and not result.delta.is_full_state

    return StepResult(
        step_num=step_num,
        label=label,
        url=page.url,
        raw_tokens=raw,
        lens_tokens=result.token_count,
        reduction_pct=reduction,
        latency_ms=result.latency_ms,
        is_delta=is_delta,
        representation=result.representation_type.value,
    )


def _skipped_step(step_num: int, label: str, url: str, reason: str) -> StepResult:
    return StepResult(
        step_num=step_num,
        label=label,
        url=url,
        raw_tokens=0,
        lens_tokens=0,
        reduction_pct=0.0,
        latency_ms=0.0,
        is_delta=False,
        representation="—",
        skipped=True,
        skip_reason=reason,
    )


async def _fresh_page(browser: Browser) -> Page:
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    return await ctx.new_page()


async def _safe_goto(page: Page, url: str, *, timeout: int = 20_000) -> bool:
    """Navigate; return False on timeout/error."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        return True
    except Exception as exc:
        print(f"    [navigation error] {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 1 — Practice Test Automation login
# ─────────────────────────────────────────────────────────────────────────────


async def run_login_workflow(browser: Browser) -> SiteResult:
    site = SiteResult(
        site_name="Practice Test Automation — Login",
        url="https://practicetestautomation.com/practice-test-login/",
    )
    page = await _fresh_page(browser)
    lens = BrowserLens(token_budget=_TOKEN_BUDGET)

    try:
        # Step 1 — navigate & observe blank form
        ok = await _safe_goto(page, site.url)
        if not ok:
            site.skipped = True
            site.skip_reason = "Navigation failed"
            return site
        await page.wait_for_load_state("networkidle", timeout=15_000)
        site.steps.append(await _observe_step(lens, page, "Navigate to login page", 1))

        # Step 2 — fill username
        await page.fill("#username", "student")
        site.steps.append(await _observe_step(lens, page, "Fill username field", 2))

        # Step 3 — fill password
        await page.fill("#password", "Password123")
        site.steps.append(await _observe_step(lens, page, "Fill password field", 3))

        # Step 4 — click Login
        await page.click("#submit")
        await page.wait_for_load_state("networkidle", timeout=15_000)
        site.steps.append(await _observe_step(lens, page, "Click Login → success page", 4))

        # Verify success (non-fatal)
        success_text = await page.locator("h1, .post-title, #loop-container").first.text_content(timeout=5_000)
        if success_text:
            print(f"    [verified] success page: {success_text.strip()!r}")

    except Exception as exc:
        print(f"    [workflow error] {exc}")
        site.skipped = True
        site.skip_reason = str(exc)
    finally:
        await page.context.close()

    return site


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 2 — Sauce Demo e-commerce
# ─────────────────────────────────────────────────────────────────────────────


async def run_saucedemo_workflow(browser: Browser) -> SiteResult:
    site = SiteResult(
        site_name="Sauce Demo — Login → Product → Cart",
        url="https://www.saucedemo.com/",
    )
    page = await _fresh_page(browser)
    lens = BrowserLens(token_budget=_TOKEN_BUDGET)

    try:
        # Step 1 — navigate
        ok = await _safe_goto(page, site.url)
        if not ok:
            site.skipped = True
            site.skip_reason = "Navigation failed"
            return site
        await page.wait_for_load_state("networkidle", timeout=15_000)
        site.steps.append(await _observe_step(lens, page, "Navigate to Sauce Demo", 1))

        # Step 2 — fill username
        await page.fill("#user-name", "standard_user")
        site.steps.append(await _observe_step(lens, page, "Fill username", 2))

        # Step 3 — fill password
        await page.fill("#password", "secret_sauce")
        site.steps.append(await _observe_step(lens, page, "Fill password", 3))

        # Step 4 — click Login
        await page.click("#login-button")
        await page.wait_for_load_state("networkidle", timeout=15_000)
        site.steps.append(await _observe_step(lens, page, "Click Login → products page", 4))

        # Step 5 — click first product title
        first_product = page.locator(".inventory_item_name").first
        await first_product.wait_for(timeout=10_000)
        await first_product.click()
        await page.wait_for_load_state("networkidle", timeout=10_000)
        site.steps.append(await _observe_step(lens, page, "Click first product", 5))

        # Step 6 — click Add to Cart
        add_btn = page.locator("button.btn_primary.btn_inventory, button[data-test*='add-to-cart']").first
        await add_btn.wait_for(timeout=10_000)
        await add_btn.click()
        site.steps.append(await _observe_step(lens, page, "Click Add to Cart", 6))

        # Step 7 — click cart icon
        await page.locator(".shopping_cart_link").click()
        await page.wait_for_load_state("networkidle", timeout=10_000)
        site.steps.append(await _observe_step(lens, page, "Click cart icon → cart page", 7))

    except Exception as exc:
        print(f"    [workflow error] {exc}")
        # Append a skipped marker for steps not reached
        reached = len(site.steps) + 1
        for i in range(reached, 8):
            site.steps.append(_skipped_step(i, f"Step {i} (not reached)", page.url, str(exc)))
    finally:
        await page.context.close()

    return site


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 3 — The Internet: Dynamic Loading
# ─────────────────────────────────────────────────────────────────────────────


async def run_dynamic_loading_workflow(browser: Browser) -> SiteResult:
    site = SiteResult(
        site_name="The Internet — Dynamic Loading",
        url="https://the-internet.herokuapp.com/dynamic_loading/1",
    )
    page = await _fresh_page(browser)
    lens = BrowserLens(token_budget=_TOKEN_BUDGET)

    try:
        # Step 1 — navigate
        ok = await _safe_goto(page, site.url)
        if not ok:
            site.skipped = True
            site.skip_reason = "Navigation failed"
            return site
        await page.wait_for_load_state("networkidle", timeout=15_000)
        site.steps.append(await _observe_step(lens, page, "Navigate to dynamic loading page", 1))

        # Step 2 — click Start
        start_btn = page.get_by_role("button", name="Start")
        await start_btn.wait_for(timeout=10_000)
        await start_btn.click()
        site.steps.append(await _observe_step(lens, page, "Click Start (loading begins)", 2))

        # Step 3 — wait for loading indicator to appear
        loading = page.locator("#loading")
        await loading.wait_for(state="visible", timeout=10_000)
        site.steps.append(await _observe_step(lens, page, "Loading spinner visible", 3))

        # Step 4 — wait for finish text to appear
        finish = page.locator("#finish")
        await finish.wait_for(state="visible", timeout=15_000)
        site.steps.append(await _observe_step(lens, page, "Loading complete — result visible", 4))

        # Read and print result text (non-fatal)
        result_text = await finish.text_content(timeout=5_000)
        if result_text:
            print(f"    [verified] result: {result_text.strip()!r}")

    except Exception as exc:
        print(f"    [workflow error] {exc}")
        reached = len(site.steps) + 1
        for i in range(reached, 5):
            site.steps.append(_skipped_step(i, f"Step {i} (not reached)", page.url, str(exc)))
    finally:
        await page.context.close()

    return site


# ─────────────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────────────

_COL_WIDTHS = {
    "step":    4,
    "label":  36,
    "raw":     8,
    "lens":    8,
    "red":     8,
    "lat":     8,
    "mode":    9,
    "rep":    14,
}
_DIVIDER = (
    "─" * (_COL_WIDTHS["step"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["label"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["raw"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["lens"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["red"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["lat"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["mode"] + 2) + "┼" +
    "─" * (_COL_WIDTHS["rep"] + 2)
)


def _header_row() -> str:
    return (
        f"{'#':>{_COL_WIDTHS['step']}}  "
        f"{'Step':<{_COL_WIDTHS['label']}}  "
        f"{'Raw tk':>{_COL_WIDTHS['raw']}}  "
        f"{'Lens tk':>{_COL_WIDTHS['lens']}}  "
        f"{'Saving':>{_COL_WIDTHS['red']}}  "
        f"{'Lat ms':>{_COL_WIDTHS['lat']}}  "
        f"{'Mode':<{_COL_WIDTHS['mode']}}  "
        f"{'Repr':<{_COL_WIDTHS['rep']}}"
    )


def _step_row(s: StepResult) -> str:
    if s.skipped:
        label = f"{s.label[:_COL_WIDTHS['label']]:<{_COL_WIDTHS['label']}}"
        return (
            f"{s.step_num:>{_COL_WIDTHS['step']}}  "
            f"{label}  "
            f"{'—':>{_COL_WIDTHS['raw']}}  "
            f"{'—':>{_COL_WIDTHS['lens']}}  "
            f"{'—':>{_COL_WIDTHS['red']}}  "
            f"{'—':>{_COL_WIDTHS['lat']}}  "
            f"{'skip':<{_COL_WIDTHS['mode']}}  "
            f"{'—':<{_COL_WIDTHS['rep']}}"
        )

    # Colour-code reduction:  positive = savings (good), negative = overhead
    pct = s.reduction_pct
    pct_str = f"{pct:+.1f}%"
    mode = "delta" if s.is_delta else "full"
    label = s.label[:_COL_WIDTHS["label"]]

    return (
        f"{s.step_num:>{_COL_WIDTHS['step']}}  "
        f"{label:<{_COL_WIDTHS['label']}}  "
        f"{s.raw_tokens:>{_COL_WIDTHS['raw']}}  "
        f"{s.lens_tokens:>{_COL_WIDTHS['lens']}}  "
        f"{pct_str:>{_COL_WIDTHS['red']}}  "
        f"{s.latency_ms:>{_COL_WIDTHS['lat']}.0f}  "
        f"{mode:<{_COL_WIDTHS['mode']}}  "
        f"{s.representation:<{_COL_WIDTHS['rep']}}"
    )


def _site_total_row(site: SiteResult) -> str:
    if site.skipped or not site.steps:
        return f"  SITE TOTAL  raw=—  lens=—  reduction=—"
    pct = site.overall_reduction_pct
    return (
        f"  SITE TOTAL  "
        f"raw={site.total_raw:,}  "
        f"lens={site.total_lens:,}  "
        f"reduction={pct:+.1f}%"
    )


def print_report(report: BenchmarkReport) -> None:
    bar = "═" * 90
    thin = "─" * 90

    print(f"\n{bar}")
    print(f"  BROWSERLENS BENCHMARK REPORT")
    print(f"  {report.ran_at}")
    print(f"{bar}\n")

    for site in report.sites:
        print(f"  ▶  {site.site_name}")
        print(f"     {site.url}")

        if site.skipped:
            print(f"     [SKIPPED] {site.skip_reason}\n")
            continue

        print()
        print(f"  {_header_row()}")
        print(f"  {_DIVIDER}")
        for step in site.steps:
            print(f"  {_step_row(step)}")
        print(f"  {thin}")
        print(f"  {_site_total_row(site)}")
        print()

    print(f"{bar}")
    if report.grand_raw > 0:
        print(
            f"  GRAND TOTAL  "
            f"raw={report.grand_raw:,} tokens  "
            f"lens={report.grand_lens:,} tokens  "
            f"overall reduction={report.grand_reduction_pct:+.1f}%"
        )
    print(f"{bar}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation
# ─────────────────────────────────────────────────────────────────────────────


def _to_json(report: BenchmarkReport) -> dict:
    return {
        "ran_at": report.ran_at,
        "grand_summary": {
            "total_raw_tokens": report.grand_raw,
            "total_lens_tokens": report.grand_lens,
            "overall_reduction_pct": round(report.grand_reduction_pct, 2),
        },
        "sites": [
            {
                "site_name": s.site_name,
                "url": s.url,
                "skipped": s.skipped,
                "skip_reason": s.skip_reason,
                "totals": {
                    "raw_tokens": s.total_raw,
                    "lens_tokens": s.total_lens,
                    "reduction_pct": round(s.overall_reduction_pct, 2),
                },
                "steps": [asdict(step) for step in s.steps],
            }
            for s in report.sites
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


_WORKFLOWS: list[tuple[str, Callable]] = [
    ("Practice Test Automation", run_login_workflow),
    ("Sauce Demo", run_saucedemo_workflow),
    ("The Internet — Dynamic Loading", run_dynamic_loading_workflow),
]


async def main() -> None:
    from datetime import datetime, timezone

    report = BenchmarkReport(
        ran_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            for display_name, workflow_fn in _WORKFLOWS:
                print(f"\n  Running: {display_name} …")
                t0 = time.monotonic()
                site_result = await workflow_fn(browser)
                elapsed = (time.monotonic() - t0) * 1000
                status = "SKIPPED" if site_result.skipped else f"{len(site_result.steps)} steps"
                print(f"  Done    ({status}, {elapsed:.0f} ms total)")
                report.sites.append(site_result)
        finally:
            await browser.close()

    print_report(report)

    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_PATH.write_text(json.dumps(_to_json(report), indent=2))
    print(f"  Results saved → {_RESULTS_PATH}\n")


if __name__ == "__main__":
    asyncio.run(main())
