from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = REPO_ROOT / "knowledge-base" / "digital-twin" / "static-prototype"

PAGES = [
    "digital-twins.html",
    "digital-twin-detail.html",
    "bundle-execution-twin-gate.html",
]
SCRIPTS = [
    "fixtures/v1/twin-fixtures.js",
    "twin-data-adapter.js",
    "twin-components.js",
    "prototype.js",
    "digital-twins-page.js",
    "digital-twin-detail-page.js",
    "twin-gate-page.js",
]
TAB_SLUGS = [
    "overview",
    "release-delta",
    "dependency-graph",
    "policy",
    "dry-run",
    "rollback",
    "drift",
    "mop-replay",
    "runtime-behavior",
    "release-note-validation",
    "audit",
]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_phase2_assets_and_versioned_fixture_package_exist() -> None:
    required = [
        *PAGES,
        *SCRIPTS,
        "prototype.css",
        "prototype-phase2.css",
        "README.md",
    ]
    for name in required:
        assert (ROOT / name).is_file(), f"missing Phase 2 asset: {name}"

    fixture = read("fixtures/v1/twin-fixtures.js")
    assert 'VERSION = "1.0.0"' in fixture
    assert "ESDA_TWIN_FIXTURES_V1" in fixture


def test_phase2_has_no_network_or_server_dependency() -> None:
    combined = "\n".join(read(name) for name in [*PAGES, *SCRIPTS]).lower()
    forbidden = [
        "fetch(",
        "xmlhttprequest",
        "websocket",
        "eventsource",
        "http://",
        "https://",
        "/api/",
        "postgres",
        "langchain",
        "openai",
        "mcp__",
    ]
    for token in forbidden:
        assert token not in combined, f"browser-only Phase 2 contains forbidden token: {token}"

    external_asset = re.compile(r"(?:src|href)=[\"'](?://|[a-z]+:)", re.IGNORECASE)
    assert external_asset.search(combined) is None


def test_pages_load_adapter_before_component_controllers() -> None:
    expected_common = [
        "fixtures/v1/twin-fixtures.js",
        "twin-data-adapter.js",
        "twin-components.js",
        "prototype.js",
    ]
    page_controller = {
        "digital-twins.html": "digital-twins-page.js",
        "digital-twin-detail.html": "digital-twin-detail-page.js",
        "bundle-execution-twin-gate.html": "twin-gate-page.js",
    }
    for page_name, controller in page_controller.items():
        page = read(page_name)
        positions = [page.index(f'src="{name}"') for name in [*expected_common, controller]]
        assert positions == sorted(positions)


def test_adapter_contract_is_promise_based_and_components_use_it() -> None:
    adapter = read("twin-data-adapter.js")
    assert "function TwinDataAdapter()" in adapter
    assert "function BrowserFixtureTwinAdapter(options)" in adapter
    assert "return new Promise" in adapter
    assert "this.latencyMs" in adapter
    assert "window.esdaTwinAdapter" in adapter
    for method in [
        "listTwins",
        "getTwin",
        "getActiveTwin",
        "getTab",
        "getActions",
        "startGeneration",
        "advanceGeneration",
        "regenerate",
        "cancelGeneration",
        "requestApproval",
        "approveTwin",
        "rejectTwin",
        "getGate",
        "clearMockHistory",
        "invalidateCache",
    ]:
        assert f'prototype.{method} = function' in adapter

    controllers = "\n".join(
        read(name)
        for name in [
            "digital-twins-page.js",
            "digital-twin-detail-page.js",
            "twin-gate-page.js",
        ]
    )
    assert controllers.count("window.esdaTwinAdapter") == 3
    assert "ESDA_TWIN_FIXTURES_V1" not in read("digital-twins-page.js")
    assert "ESDA_TWIN_FIXTURES_V1" not in read("twin-gate-page.js")


def test_all_response_modes_and_scenarios_are_fixture_driven() -> None:
    fixture = read("fixtures/v1/twin-fixtures.js")
    adapter = read("twin-data-adapter.js")
    for mode in ["success", "partial", "empty", "stale", "failed"]:
        assert f'"{mode}"' in fixture
        assert mode in adapter

    scenarios = [
        "green-helm",
        "amber-pvc-rbac",
        "red-cluster-secret",
        "generating",
        "failed-dry-run",
        "stale-snapshot",
        "material-drift",
        "superseded",
        "missing-replay",
        "missing-runtime",
        "large-delta",
        "large-graph",
        "long-audit",
    ]
    for scenario in scenarios:
        assert scenario in fixture

    assert "deltaRows(520)" in fixture
    assert "graphFixture(320)" in fixture
    assert "auditEvents(180)" in fixture


def test_list_supports_frozen_filters_sort_cursor_url_and_states() -> None:
    page = read("digital-twins.html")
    controller = read("digital-twins-page.js")
    adapter = read("twin-data-adapter.js")

    for field in [
        "search",
        "decision",
        "lifecycle",
        "freshness",
        "target",
        "bundle",
        "creator",
        "date",
        "linked_execution",
        "sort",
        "direction",
    ]:
        assert f'name="{field}"' in page

    assert "next_cursor" in adapter and "previous_cursor" in adapter
    assert "data-page-cursor" in controller
    assert "data-sort" in page and "data-sort" in controller
    assert 'data-sort="risk_score"' in page
    assert 'requestedSort === "risk" ? "risk_score"' in controller
    assert "popstate" in controller
    assert "data-twin-row" in controller
    assert "disabled_reason" in controller
    assert "loadingRows" in controller
    assert "No matching twins" in controller
    assert "response.warning" in controller
    assert "data-retry" in controller
    assert "setupBoundedRefresh" in controller


def test_detail_uses_all_frozen_tabs_lazy_cache_and_deep_links() -> None:
    page = read("digital-twin-detail.html")
    controller = read("digital-twin-detail-page.js")
    adapter = read("twin-data-adapter.js")
    fixture = read("fixtures/v1/twin-fixtures.js")

    assert page.count('role="tabpanel"') == 11
    positions = [page.index(f'id="panel-{slug}"') for slug in TAB_SLUGS]
    assert positions == sorted(positions)
    assert (
        'tab_slugs: ["' + '", "'.join(TAB_SLUGS) + '"]'
        in fixture
    )
    assert "adapter.getTab" in controller
    assert "this.tabCache = new Map()" in adapter
    assert "decisionVersion || twin.decision_version" in adapter
    assert "invalidateCache" in adapter
    assert 'ui.params().get("tab")' in controller
    assert "data-summary-target" in controller
    assert 'title="' in controller
    summary_css = read("prototype-phase2.css").split(".summary-link {", 1)[1].split("}", 1)[0]
    for rule in [
        "max-width: 100%",
        "min-width: 0",
        "overflow: hidden",
        "text-overflow: ellipsis",
        "white-space: nowrap",
    ]:
        assert rule in summary_css
    assert "data-jump-finding" in controller
    assert "data-evidence-modal" in controller
    assert "data-graph-node" in controller
    assert "data-audit-modal" in controller
    assert "data-tab-content" in controller
    assert "mockDownload" in controller
    assert "copyText" in controller
    assert 'class="drawer"' in page


def test_lifecycle_approval_block_and_regeneration_are_explicit() -> None:
    adapter = read("twin-data-adapter.js")
    fixture = read("fixtures/v1/twin-fixtures.js")
    gate = read("twin-gate-page.js")
    detail = read("digital-twin-detail-page.js")

    assert (
        'progress_states: ["requested", "generating", "awaiting_dry_run", '
        '"decision_calculating", "green"]'
        in fixture
    )
    for terminal in ["amber", "red", "failed", "cancelled", "superseded"]:
        assert terminal in adapter + fixture
    assert "prior_decision" in adapter and "Previous evidence remains visible" in detail
    assert "decision_is_final = false" in adapter
    assert "request-approval" in gate
    assert 'data-gate-action="approve"' in gate
    assert 'data-gate-action="reject"' in gate
    assert "cannot be overridden" in gate
    assert "superseded_by" in adapter


def test_browser_restoration_is_active_only_and_clearable() -> None:
    adapter = read("twin-data-adapter.js")
    detail = read("digital-twin-detail-page.js")
    readme = read("README.md")

    for key in [
        "esda.digital-twin.mock-history.v1",
        "esda.digital-twin.active-run.v1",
        "esda.digital-twin.response-mode.v1",
    ]:
        assert key in adapter or key in readme

    assert "getActiveTwin" in detail
    assert "Terminal evidence opens only when the user explicitly selects it" in readme
    assert "clearMockHistory" in adapter
    assert "Clear mock history" in read("digital-twins.html")
    assert "Phase 3" in readme
    assert "server-side mock API becomes the source of truth" in readme


def test_hardcoded_phase1_twin_records_are_not_in_html() -> None:
    html = "\n".join(read(name) for name in PAGES)
    for record_id in [
        "twin_signal_scout_001",
        "twin_beacon_pilot_002",
        "twin_core_gateway_003",
        "twin_signoz_upgrade_004",
    ]:
        assert record_id not in html

    assert 'id="twin-list-body"></tbody>' in read("digital-twins.html")
    assert "Loading twin run" in read("digital-twin-detail.html")
    assert "Loading Twin Gate" in read("bundle-execution-twin-gate.html")
