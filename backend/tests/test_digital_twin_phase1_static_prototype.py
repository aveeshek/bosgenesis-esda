from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE_ROOT = REPO_ROOT / "knowledge-base" / "digital-twin" / "static-prototype"

PAGES = [
    "digital-twins.html",
    "digital-twin-detail.html",
    "bundle-execution-twin-gate.html",
]

EXPECTED_NAVIGATION = [
    "LLM Chat",
    "Health Check",
    "Release Notes",
    "Bundle Generation",
    "Digital Twins",
    "Bundle Execution",
    "Environment Chat",
    "Activity",
    "Approvals",
    "L4 Audit",
]

EXPECTED_TABS = [
    "Overview",
    "Release Delta Twin",
    "Dependency Graph Twin",
    "Policy Twin",
    "Dry-run / Diff Twin",
    "Rollback Twin",
    "Drift Twin",
    "MoP Replay Twin",
    "Runtime Behavior Twin",
    "Release Note Validation Twin",
    "Audit Timeline",
]

FORBIDDEN_BROWSER_IO = [
    "fetch(",
    "xmlhttprequest",
    "websocket",
    "eventsource",
    "http://",
    "https://",
]


def _read(name: str) -> str:
    return (PROTOTYPE_ROOT / name).read_text(encoding="utf-8")


def test_prototype_is_complete_and_directly_openable() -> None:
    assets = [
        *PAGES,
        "prototype.css",
        "prototype-phase2.css",
        "prototype.js",
        "README.md",
    ]
    for name in assets:
        assert (PROTOTYPE_ROOT / name).is_file(), f"missing prototype asset: {name}"

    for name in PAGES:
        page = _read(name).lower()
        assert '<link rel="stylesheet" href="prototype.css">' in page
        assert '<script src="prototype.js"></script>' in page
        assert "<base " not in page


def test_prototype_has_no_network_or_server_interaction() -> None:
    names = [
        *PAGES,
        "prototype.js",
        "twin-components.js",
        "twin-data-adapter.js",
        "digital-twins-page.js",
        "digital-twin-detail-page.js",
        "twin-gate-page.js",
    ]
    combined = "\n".join(_read(name) for name in names)
    lowered = combined.lower()
    for token in FORBIDDEN_BROWSER_IO:
        assert token not in lowered, f"prototype must not contain {token}"

    external_asset = re.compile(r"(?:src|href)=[\"'](?://|[a-z]+:)", re.IGNORECASE)
    assert external_asset.search(combined) is None


def test_each_page_reproduces_frozen_navigation_order() -> None:
    for name in PAGES:
        page = _read(name)
        nav_markup = page[page.index("data-main-nav") : page.index("</nav>")]
        positions = [nav_markup.index(label) for label in EXPECTED_NAVIGATION]
        assert positions == sorted(positions), f"navigation order changed in {name}"


def test_list_shell_preserves_frozen_columns_states_actions_and_variants() -> None:
    page = _read("digital-twins.html")
    implementation = (
        page + _read("digital-twins-page.js") + _read("fixtures/v1/twin-fixtures.js")
    )
    columns = [
        "Twin Run ID",
        "Decision",
        "Risk Score",
        "Target Cluster",
        "Target Namespace",
        "MoP Bundle",
        "Release Version",
        "Freshness",
        "Created By",
        "Created At",
        "Linked Execution",
        "Actions",
    ]
    states = ["Green", "Amber", "Red", "Generating", "Stale", "Failed", "Superseded"]
    actions = ["Open twin", "Regenerate", "Download report", "Open execution", "Request approval"]
    variants = ["Loading", "Empty", "No matching twins", "Error"]

    for expected in columns:
        assert expected.lower() in page.lower()
    for expected in [*states, *actions, *variants]:
        assert expected.lower() in implementation.lower()


def test_detail_shell_preserves_all_tabs_and_required_actions() -> None:
    page = _read("digital-twin-detail.html")
    implementation = (
        page + _read("digital-twin-detail-page.js") + _read("fixtures/v1/twin-fixtures.js")
    )
    tab_markup = page[page.index('role="tablist"') :]
    positions = [tab_markup.index(label) for label in EXPECTED_TABS]
    assert positions == sorted(positions)
    assert page.count('role="tabpanel"') == 11

    actions = [
        "Generate / Regenerate",
        "Open Bundle",
        "Open Execution",
        "Start Execution",
        "Request Approval",
        "Approve",
        "Reject",
        "Download Report",
        "Export JSON",
    ]
    for action in actions:
        assert action in implementation

    assert "Preliminary" in implementation
    assert "Final" in implementation
    assert "decision_is_final" in implementation


def test_gate_controller_preserves_frozen_fields_actions_and_variants() -> None:
    implementation = (
        _read("bundle-execution-twin-gate.html")
        + _read("twin-gate-page.js")
        + _read("fixtures/v1/twin-fixtures.js")
    )
    fields = [
        "Twin ID",
        "Decision",
        "Risk",
        "Policy",
        "Evidence",
        "Freshness",
        "Dry-run",
        "Rollback",
        "Drift",
        "Approval",
    ]
    variants = ["green", "amber", "red", "stale", "expired", "generating"]
    actions = ["View Full Twin", "Regenerate", "Request Approval", "Start Execution"]

    for expected in [*fields, *actions]:
        assert expected in implementation
    for variant in variants:
        assert f'data-gate-fixture="{variant}"' in implementation


def test_accessibility_and_responsive_contract_is_represented() -> None:
    css = _read("prototype.css") + _read("prototype-phase2.css")
    javascript = _read("prototype.js")
    combined_pages = "\n".join(_read(name) for name in PAGES)

    assert "prefers-reduced-motion: reduce" in css
    assert "@media (max-width: 1350px)" in css
    assert "@media (max-width: 980px)" in css
    assert "@media (max-width: 680px)" in css
    assert ":focus-visible" in css
    assert "overflow-x: auto" in css
    assert 'class="skip-link"' in combined_pages
    assert 'role="tablist"' in combined_pages
    assert "ArrowRight" in javascript and "ArrowLeft" in javascript
