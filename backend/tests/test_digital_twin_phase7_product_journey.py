from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = ROOT / "backend" / "app" / "static" / "digital-twin"
RUNNER = ROOT / "backend" / "tests" / "browser" / "digital_twin_product_journey.e2e.cjs"
EVIDENCE_ROOT = ROOT / "knowledge-base" / "digital-twin" / "evidence" / "phase7-product-journey"
RESULTS = EVIDENCE_ROOT / "product-journey-results.json"


def test_phase7_product_journey_runner_covers_frozen_scope() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    required_journeys = [
        "list search, filters, sorting, pagination",
        "direct detail and selected-tab deep links survive refresh and reopen",
        "progressive availability and old-run reopening",
        "Green gate reaches execution with no approval override",
        "Amber approval, return, and execution",
        "Red block, corrected bundle, and regeneration",
        "stale and material drift require regeneration",
        "rollback, cleanup, and execution linkage",
        "browser Back and Forward across list, detail, and tabs",
        "desktop, laptop, tablet, and mobile screenshots",
        "keyboard, focus, labels, status text, and reduced motion",
    ]
    for journey in required_journeys:
        assert journey in runner

    for viewport in ["desktop", "laptop", "tablet", "mobile"]:
        assert f'"{viewport}"' in runner

    assert 'reducedMotion: "reduce"' in runner
    assert "aria-selected" in runner
    assert "document.activeElement" in runner


def test_phase7_product_journey_fixes_are_part_of_the_ui_contract() -> None:
    list_controller = (STATIC_ROOT / "digital-twins-page.js").read_text(encoding="utf-8")
    detail_controller = (STATIC_ROOT / "digital-twin-detail-page.js").read_text(encoding="utf-8")
    gate_controller = (STATIC_ROOT / "twin-gate-page.js").read_text(encoding="utf-8")
    adapter = (STATIC_ROOT / "twin-data-adapter.js").read_text(encoding="utf-8")

    assert "limit: realCore ? 25 : 6" in list_controller
    assert "selectedTab = slug" in detail_controller
    assert "lifecycleChanged" in detail_controller
    assert 'event.key === "ArrowRight"' in detail_controller
    assert 'label.setAttribute("for", control.id)' in gate_controller
    assert '"-corrected.zip"' in adapter


def test_canonical_product_journey_evidence_is_green() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert payload["suite"] == "Phase 7 Product Journey E2E"
    assert payload["passed"] == 11
    assert payload["failed"] == 0
    assert all(step["status"] == "passed" for step in payload["steps"])
    assert len(payload["screenshots"]) == 8

    for relative_path in payload["screenshots"]:
        artifact = ROOT / relative_path
        assert artifact.is_file()
        assert artifact.stat().st_size > 10_000
        assert artifact.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
