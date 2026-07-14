from __future__ import annotations

import json
from pathlib import Path

from jsonschema.validators import validator_for
from referencing import Registry, Resource


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "knowledge-base" / "digital-twin" / "contracts" / "v1"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"

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
    ("Overview", "overview"),
    ("Release Delta Twin", "release-delta"),
    ("Dependency Graph Twin", "dependency-graph"),
    ("Policy Twin", "policy"),
    ("Dry-run / Diff Twin", "dry-run"),
    ("Rollback Twin", "rollback"),
    ("Drift Twin", "drift"),
    ("MoP Replay Twin", "mop-replay"),
    ("Runtime Behavior Twin", "runtime-behavior"),
    ("Release Note Validation Twin", "release-note-validation"),
    ("Audit Timeline", "audit"),
]

EXPECTED_AVAILABILITY = {
    "loading",
    "available",
    "empty",
    "not_run",
    "not_available",
    "failed",
    "stale",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schemas() -> list[tuple[Path, dict]]:
    return [(path, _load_json(path)) for path in sorted(SCHEMA_ROOT.rglob("*.schema.json"))]


def _registry(schemas: list[tuple[Path, dict]]) -> Registry:
    resources = []
    for path, schema in schemas:
        schema_id = schema.get("$id")
        assert schema_id, f"{path} must declare $id"
        resources.append((schema_id, Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def test_manifest_freezes_navigation_routes_tabs_and_states() -> None:
    manifest = _load_json(CONTRACT_ROOT / "contract-manifest.json")

    assert manifest["contract_version"] == "1.0.0"
    assert manifest["decision_authority"] == "bosgenesis-mop-execution-agent"
    assert manifest["navigation"] == EXPECTED_NAVIGATION
    assert manifest["routes"]["list"] == "/digital-twins"
    assert manifest["routes"]["detail"] == "/digital-twins/{twin_id}"
    assert "/esda" not in manifest["routes"]["list"]
    assert "/esda" not in manifest["routes"]["detail"]
    assert [(tab["label"], tab["slug"]) for tab in manifest["tabs"]] == EXPECTED_TABS
    assert [tab["position"] for tab in manifest["tabs"]] == list(range(1, 12))
    assert set(manifest["tab_availability_states"]) == EXPECTED_AVAILABILITY


def test_every_declared_schema_is_present_and_versioned() -> None:
    manifest = _load_json(CONTRACT_ROOT / "contract-manifest.json")
    declared = list(manifest["schemas"].values()) + [tab["schema"] for tab in manifest["tabs"]]

    for relative_path in declared:
        path = CONTRACT_ROOT / relative_path
        assert path.is_file(), f"Missing declared schema: {relative_path}"
        schema = _load_json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["x-contract-version"] == "1.0.0"
        assert schema["$id"].startswith("https://bosgenesis.local/contracts/digital-twin/v1/")


def test_all_schemas_are_valid_draft_2020_12_documents() -> None:
    for path, schema in _schemas():
        validator_class = validator_for(schema)
        validator_class.check_schema(schema)


def test_golden_fixtures_validate_against_frozen_schemas() -> None:
    schemas = _schemas()
    registry = _registry(schemas)
    fixture_manifest = _load_json(FIXTURE_ROOT / "fixture-manifest.json")

    assert fixture_manifest["contract_version"] == "1.0.0"
    assert fixture_manifest["fixtures"]

    for record in fixture_manifest["fixtures"]:
        fixture_path = FIXTURE_ROOT / record["fixture"]
        schema_path = (FIXTURE_ROOT / record["schema"]).resolve()
        instance = _load_json(fixture_path)
        schema = _load_json(schema_path)
        validator_class = validator_for(schema)
        errors = sorted(
            validator_class(schema, registry=registry).iter_errors(instance),
            key=lambda error: list(error.absolute_path),
        )
        assert not errors, (
            f"{fixture_path.name} failed {schema_path.name}: "
            + "; ".join(error.message for error in errors)
        )


def test_optional_not_run_fixture_is_typed_and_contains_no_fake_evidence() -> None:
    fixture = _load_json(FIXTURE_ROOT / "mop-replay-not-run.json")

    assert fixture["availability"]["state"] == "not_run"
    assert fixture["data"] is None


def test_safe_explanation_cannot_claim_hidden_reasoning() -> None:
    fixture = _load_json(FIXTURE_ROOT / "safe-explanation.json")

    assert fixture["chain_of_thought_included"] is False
    assert fixture["status"] in {"generated", "fallback", "unavailable", "failed"}
    assert fixture["evidence_refs"]
