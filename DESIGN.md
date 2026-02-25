# BrowserLens — Adaptive Perception & State Diffing for Browser Agents

## Project Overview

BrowserLens is a Python library that sits between the browser (Playwright) and any LLM-powered browser agent. It solves two problems no existing tool addresses:

1. **Adaptive Representation Router (Layer 1):** Automatically selects the cheapest sufficient page representation (accessibility tree, DOM subset, or screenshot) per step, instead of always dumping the same format.
2. **Structural State Diffing (Layer 2):** Diffs page state between agent steps and sends only meaningful deltas to the LLM, instead of re-sending the entire page every time.

Together, these dramatically cut token usage, latency, and cost per task — while maintaining or improving accuracy.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Developer's Agent               │
│          (Browser Use, custom, etc.)             │
└──────────────────────┬──────────────────────────┘
                       │ agent.observe(page)
                       ▼
┌─────────────────────────────────────────────────┐
│                  BrowserLens                     │
│                                                  │
│  ┌─────────────┐    ┌─────────────────────────┐ │
│  │   Router     │───▶│   Representation        │ │
│  │  (Layer 1)   │    │   Extractors            │ │
│  │              │    │                         │ │
│  │ • a11y score │    │ • A11yExtractor         │ │
│  │ • canvas det │    │ • DOMExtractor          │ │
│  │ • complexity │    │ • VisionExtractor       │ │
│  │ • DOM depth  │    │ • HybridExtractor       │ │
│  └─────────────┘    └────────────┬────────────┘ │
│                                  │               │
│                                  ▼               │
│                     ┌─────────────────────────┐  │
│                     │   State Differ          │  │
│                     │   (Layer 2)             │  │
│                     │                         │  │
│                     │ • Tree diff algorithm   │  │
│                     │ • Semantic filter       │  │
│                     │ • Delta formatter       │  │
│                     │ • Snapshot store        │  │
│                     └────────────┬────────────┘  │
│                                  │               │
│                                  ▼               │
│                     ┌─────────────────────────┐  │
│                     │   Output Formatter      │  │
│                     │                         │  │
│                     │ • Token budget aware    │  │
│                     │ • Ref ID system (@e1)   │  │
│                     │ • LLM-ready output      │  │
│                     └─────────────────────────┘  │
└──────────────────────┬──────────────────────────┘
                       │ Compact, diffed representation
                       ▼
                    LLM (any)
```

---

## File Structure

```
browserlens/
├── __init__.py                 # Public API: BrowserLens class
├── core/
│   ├── __init__.py
│   ├── lens.py                 # Main BrowserLens orchestrator class
│   └── types.py                # Shared types/dataclasses (PageState, Delta, RepresentationType, etc.)
│
├── router/
│   ├── __init__.py
│   ├── router.py               # AdaptiveRouter — picks representation per page
│   ├── signals.py              # Signal extractors (canvas detection, a11y coverage, DOM complexity)
│   └── strategies.py           # Representation strategies enum + selection logic
│
├── extractors/
│   ├── __init__.py
│   ├── base.py                 # Abstract BaseExtractor
│   ├── a11y.py                 # Accessibility tree extractor (via Playwright CDP)
│   ├── dom.py                  # DOM extractor with distillation (inspired by Agent-E)
│   ├── vision.py               # Screenshot extractor (base64, with optional annotation)
│   └── hybrid.py               # Combines a11y + selective vision for canvas/visual elements
│
├── differ/
│   ├── __init__.py
│   ├── differ.py               # StateDiffer — main diff engine
│   ├── tree_diff.py            # Tree-based diff algorithm for a11y/DOM tree structures
│   ├── semantic_filter.py      # Filters noise (animation timers, ad rotations) from diffs
│   └── snapshot_store.py       # Stores previous snapshots for comparison
│
├── formatter/
│   ├── __init__.py
│   ├── formatter.py            # OutputFormatter — converts to LLM-ready text
│   ├── ref_manager.py          # Manages stable @eN references across steps
│   └── token_budget.py         # Truncation/prioritization within a token budget
│
├── benchmarks/
│   ├── __init__.py
│   ├── token_counter.py        # Measures tokens per step (tiktoken)
│   ├── latency_tracker.py      # Measures wall-clock time per step
│   └── comparison.py           # A/B: BrowserLens vs raw Playwright MCP vs full a11y tree
│
└── tests/
    ├── test_router.py
    ├── test_extractors.py
    ├── test_differ.py
    ├── test_formatter.py
    └── test_integration.py
```

---

## Layer 1: Adaptive Router — Design

### Signals collected per page (all cheap/fast):

| Signal | How | Cost |
|---|---|---|
| **Canvas presence** | `page.query_selector_all('canvas, [data-canvas]')` | ~1ms |
| **A11y tree coverage** | Count interactive a11y nodes vs total DOM interactive elements | ~50ms |
| **DOM complexity** | Total node count, max nesting depth, avg children per node | ~10ms |
| **Dynamic content ratio** | MutationObserver sample over 500ms — how much is changing? | ~500ms (optional, cached) |
| **Page type heuristic** | URL pattern + meta tags (is this a form? a dashboard? a doc?) | ~1ms |

### Decision logic (V1 — heuristic, upgradeable to learned):

```python
def select_representation(signals: PageSignals) -> RepresentationType:
    # If canvas/WebGL detected and a11y coverage is low → need vision
    if signals.has_canvas and signals.a11y_coverage < 0.5:
        return RepresentationType.HYBRID  # a11y + screenshot of canvas region

    # If a11y coverage is high → use a11y tree (cheapest)
    if signals.a11y_coverage >= 0.8:
        return RepresentationType.A11Y_TREE

    # If DOM is manageable and a11y is moderate → use distilled DOM
    if signals.dom_node_count < 2000 and signals.a11y_coverage >= 0.5:
        return RepresentationType.DISTILLED_DOM

    # Fallback: hybrid
    return RepresentationType.HYBRID
```

### Key design decisions:
- Router runs BEFORE the main LLM call — must be fast (<100ms, no LLM needed)
- Signals are cached per URL origin (sites don't change structure between pages often)
- Router is pluggable — developers can override with custom logic or swap in a learned model later

---

## Layer 2: State Differ — Design

### Core algorithm:

1. After each action, the chosen extractor produces a `PageState` (a tree of nodes with roles, names, states, refs)
2. `StateDiffer` compares the new `PageState` against the stored previous `PageState`
3. Diff produces a `Delta` object containing:
   - `added`: New nodes that appeared (e.g., a modal, dropdown options, error message)
   - `removed`: Nodes that disappeared
   - `changed`: Nodes whose properties changed (e.g., button went from enabled → disabled, input got a value)
   - `unchanged_summary`: One-line summary of what stayed the same ("Navigation, header, and footer unchanged. 47 form fields stable.")
4. `SemanticFilter` removes noise:
   - Animation/timer updates (aria-live regions updating every second)
   - Ad rotation / carousel position changes
   - Scroll position artifacts
   - Keeps: new error messages, state changes on interactive elements, new modals/dialogs, navigation changes

### Tree diff algorithm:

```python
def diff_trees(old: StateTree, new: StateTree) -> Delta:
    """
    Match nodes by stable ref IDs (preferred) or by role+name fingerprint.
    For matched nodes: compare properties (state, value, checked, expanded, etc.)
    Unmatched in new → added
    Unmatched in old → removed
    """
```

Key insight: Accessibility tree nodes have relatively stable identities (role + accessible name). A button labeled "Submit" is likely the same button between steps even if the DOM reshuffled underneath. We use `(role, name, parent_role)` tuples as fingerprints for matching when ref IDs don't persist.

### What the LLM sees:

**Step 1 (first observation):** Full representation (no diff available yet)
```
[FULL PAGE STATE]
- navigation: link "Home" [@e1], link "Products" [@e2], link "Cart (0)" [@e3]
- main: heading "Search Products" [@e4]
  - search: textbox "Search" [@e5], button "Go" [@e6]
  - region "Results": text "Enter a search term to begin"
```

**Step 2 (after typing "laptop" and clicking Go):**
```
[DELTA — 3 changes from previous state]
CHANGED: link "Cart (0)" [@e3] → unchanged
CHANGED: textbox "Search" [@e5] → value: "laptop"
REMOVED: text "Enter a search term to begin"
ADDED: region "Results":
  - link "MacBook Pro 16" — $2,499" [@e47]
  - link "ThinkPad X1 Carbon — $1,299" [@e48]
  - link "Dell XPS 15 — $1,799" [@e49]
  - text "Showing 3 of 142 results"
  - button "Next Page" [@e50]
UNCHANGED: navigation (3 links), heading, search box — all stable
```

This is dramatically smaller than re-sending the entire page.

---

## Benchmarking Plan

### Metrics:
1. **Tokens per step** — Average tokens sent to LLM per agent step (measured via tiktoken)
2. **Tokens per task** — Total tokens for a complete multi-step task
3. **Latency per step** — Wall-clock time for perception (router + extraction + diff + formatting)
4. **Task success rate** — Does the agent still complete the task correctly with diffed input?

### Comparisons:
- **Baseline A:** Raw Playwright MCP (full a11y tree every step)
- **Baseline B:** Browser Use default (DOM extraction every step)
- **BrowserLens (router only):** Adaptive representation, no diffing
- **BrowserLens (router + differ):** Full system

### Test scenarios:
1. Simple form fill (login page) — 3-5 steps
2. E-commerce search + filter + add to cart — 8-12 steps
3. Multi-page research task — 15+ steps
4. Canvas-heavy page (Google Sheets, dashboard) — tests vision fallback

---

## Prior Art & How We Differ

| System | What it does | How BrowserLens differs |
|---|---|---|
| **Agent-E (2024)** | Flexible DOM distillation (3 modes), change observation (linguistic) | We do structural diffing (not linguistic), standalone library (not baked into an agent), include vision routing |
| **WebRouter (Oct 2025)** | Routes between LLM models via information bottleneck | We route between representation types (a11y/DOM/vision), complementary not competing |
| **AWM (2024, pub 2025)** | Learns reusable workflow recipes in natural language | Future Layer 3 — we'd compile to executable scripts instead |
| **Vercel agent-browser (2026)** | Compact refs system, 93% less context vs Playwright MCP | We add adaptive routing + diffing on top. Compatible, not competing |
| **Browser Use 1.0** | Extract tool for targeted queries, DOM-first with optional screenshots | We diff the full state automatically, they query on demand |
| **Playwright MCP** | A11y snapshots with vision fallback | We add intelligence to when/how that fallback triggers, plus diffing |

---

## Tech Stack

- **Python 3.11+**
- **Playwright** (async, Chromium) — browser automation
- **CDP (Chrome DevTools Protocol)** — direct a11y tree access
- **tiktoken** — token counting for benchmarks
- **pydantic** — data models
- **pytest + pytest-asyncio** — testing
- **No ML dependencies for V1** — router is heuristic-based, upgradeable later
