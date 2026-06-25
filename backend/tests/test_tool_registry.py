from backend.app.tools.registry import default_tool_registry


def test_default_tool_registry_allows_expected_workflows() -> None:
    registry = default_tool_registry()

    assert registry.is_allowed(tool_name="rest.get", workflow_type="health_check_diagnostic")
    assert registry.is_allowed(tool_name="release_notes.agent_scan", workflow_type="release_note_creation")
    assert not registry.is_allowed(tool_name="release_notes.agent_scan", workflow_type="health_check_diagnostic")
    assert not registry.is_allowed(tool_name="shell.raw", workflow_type="health_check_diagnostic")