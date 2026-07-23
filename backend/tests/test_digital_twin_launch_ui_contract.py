from pathlib import Path


APP_ROOT = Path(__file__).parents[1] / "app"


def read(path: str) -> str:
    return (APP_ROOT / path).read_text(encoding="utf-8")


def test_digital_twins_button_opens_real_source_launcher() -> None:
    page = read("static/digital-twin/digital-twins.html")
    controller = read("static/digital-twin/digital-twins-page.js")
    adapter = read("static/digital-twin/twin-http-adapter.js")
    styles = read("static/digital-twin/prototype-phase2.css")

    assert 'id="generate-fixture"' in page
    assert "openRealSimulationLauncher()" in controller
    assert "adapter.listGenerationSources()" in controller
    assert "bundle_run_id: bundle.run_id" in controller
    assert "target_namespace: targetNamespace" in controller
    assert "Run Simulation" in controller
    assert "listGenerationSources" in adapter

    assert ".mock-modal-actions" in styles
    assert "gap: 10px" in styles


def test_bundle_execution_can_restore_or_launch_matching_twin() -> None:
    template = read("templates/mop_execution.html")
    controller = read("static/js/mop_execution.js")

    assert "Namespace Twin" in template
    assert "Run Digital Simulation" in controller
    assert "runSelectedTwinSimulation" in controller
    assert 'fetch("/api/digital-twins/sources")' in controller
    assert 'fetch("/api/digital-twins", {' in controller
    assert "candidate.canonical_sha256 || candidate.sha256" in controller
    assert "item.bundle_hash || item.bundle?.bundle_hash" in controller
    assert "twinRiskLabel(gate.risk)" in controller
    assert "decision v" in controller
    assert 'fetchWithTimeout("/api/mop-execution/bundles", {}, 12000)' in controller
    assert '/api/mop-execution/bundles/${encodeURIComponent(runId)}/identity' in controller
    assert "Could not load bundles - click to retry" in controller
