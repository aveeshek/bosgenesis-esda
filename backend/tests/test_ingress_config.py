from backend.app.config import Settings
from backend.app.tools.mcp_client import K8sInspectorMcpTool
from backend.app.tools.rest_get import RestGetTool


def test_rest_get_allows_bosgenesis_suffix() -> None:
    tool = RestGetTool(Settings(allowed_rest_hosts="localhost,.bosgenesis.local"))

    assert tool._allowed("http://k8s-inspector.bosgenesis.local/health")
    assert not tool._allowed("http://example.com/health")


def test_k8s_inspector_route_mapping() -> None:
    assert K8sInspectorMcpTool.route_for_tool("list_pods") == "/pods"
    assert K8sInspectorMcpTool.route_for_tool("namespace_summary") == "/namespace/summary"
    assert K8sInspectorMcpTool.route_for_tool("delete_pod") is None
