from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse
from backend.app.db.models import DigitalTwinExplanationLog


CORE_TWIN = {
    "schema_version": "1.0.0",
    "twin_id": "twin_real_phase4",
    "display_name": "sample-bundle -> sample-target",
    "decision_version": 1,
    "decision": "pending",
    "decision_is_final": False,
    "lifecycle_status": "awaiting_dry_run",
    "target_cluster": "contract-cluster",
    "target_namespace": "sample-target",
    "bundle_name": "sample-bundle",
    "bundle_hash": "sha256:bundle",
    "input_hash": "sha256:input",
    "release_version": "1.0.0",
    "actor_id": "admin",
    "created_at": "2026-07-14T12:00:00+00:00",
    "updated_at": "2026-07-14T12:00:01+00:00",
    "expires_at": None,
    "superseded_by": None,
    "actions": [
        {"code": "open_twin", "label": "Open Twin", "visible": True, "enabled": True},
        {
            "code": "cancel_generation",
            "label": "Cancel generation",
            "visible": True,
            "enabled": True,
        },
    ],
    "facts": {
        "provisional": True,
        "module_modes": {"policy": "real_core"},
    },
}


CORE_TWIN.update(
    {
        "visible_lifecycle": "awaiting_dry_run",
        "risk": {"level": "preliminary", "score": None},
        "autonomy_eligibility": "pending",
        "recommended_action": "Attach authoritative dry-run evidence.",
        "freshness": {
            "status": "fresh",
            "captured_at": CORE_TWIN["updated_at"],
            "expires_at": None,
            "superseded_by": None,
            "message": "Persisted facts are fresh.",
        },
        "target": {
            "cluster_id": "contract-cluster",
            "cluster_name": "contract-cluster",
            "namespace": "sample-target",
        },
        "bundle": {
            "bundle_id": "sha256:input",
            "bundle_name": "sample-bundle",
            "bundle_hash": "sha256:bundle",
            "release_version": "1.0.0",
        },
        "created_by": "admin",
        "created_by_display": "admin",
        "relationships": {
            "dry_run_job_id": None,
            "approval_id": None,
            "approval_status": "not_required",
            "execution_id": None,
            "execution_status": "unlinked",
        },
        "top_reasons": [
            {
                "code": "AUTHORITATIVE_DRY_RUN_PENDING",
                "summary": "A final decision requires authoritative dry-run evidence.",
                "severity": "review",
                "tab_slug": "dry-run",
            }
        ],
        "preliminary_summary": {
            "status": "preliminary",
            "headline": "Twin is awaiting dry-run evidence.",
            "observations": ["A final decision requires authoritative dry-run evidence."],
            "deterministic": True,
        },
        "final_summary": None,
    }
)


def _response(method: str, path: str, data: dict) -> MopExecutionAgentResponse:
    return MopExecutionAgentResponse(
        method=method,
        url=f"http://execution-agent/{path}",
        status_code=200,
        payload={"ok": True, "data": deepcopy(data), "data_mode": "real_core"},
    )


class FakeNamespaceTwinClient:
    def __init__(self, *args, **kwargs) -> None:
        self.twin = deepcopy(CORE_TWIN)
        self.release_delta_params: dict = {}
        self.dependency_graph_params: dict = {}
        self.policy_params: dict = {}
        self.dry_run_params: dict = {}

    async def create_namespace_twin(self, payload: dict) -> MopExecutionAgentResponse:
        created = deepcopy(self.twin)
        created["target_namespace"] = payload["target_namespace"]
        created["target_cluster"] = payload["target_cluster"]
        created["target_namespace"] = payload["target_namespace"]
        created["target"] = {
            "cluster_id": payload["target_cluster"],
            "cluster_name": payload["target_cluster"],
            "namespace": payload["target_namespace"],
        }
        return _response("POST", "v1/namespace-twins", created)

    async def list_namespace_twins(self, params: dict | None = None) -> MopExecutionAgentResponse:
        return _response(
            "GET",
            "v1/namespace-twins",
            {
                "items": [self.twin],
                "page": {
                    "limit": int((params or {}).get("limit") or 25),
                    "offset": int((params or {}).get("offset") or 0),
                    "result_count": 1,
                    "has_more": False,
                    "next_offset": None,
                },
            },
        )

    async def get_namespace_twin_overview(self, twin_id: str) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/overview",
            {
                "schema_version": "1.0.0",
                "twin_id": twin_id,
                "decision_version": self.twin["decision_version"],
                "state": "available",
                "kind": "overview",
                "title": "Overview",
                "summary": self.twin["preliminary_summary"]["headline"],
                "metrics": [],
                "reasons": [],
                "recommended_action": self.twin["recommended_action"],
                "preliminary_summary": self.twin["preliminary_summary"],
                "final_summary": None,
                "risk": self.twin["risk"],
                "freshness": self.twin["freshness"],
                "actions": self.twin["actions"],
                "relationships": self.twin["relationships"],
                "fact_envelope": {"decision": "pending"},
            },
        )

    async def get_namespace_twin_release_delta(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        self.release_delta_params = deepcopy(params or {})
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/release-delta",
            {
                "schema_version": "1.0.0",
                "twin_id": twin_id,
                "decision_version": self.twin["decision_version"],
                "lifecycle_status": self.twin["lifecycle_status"],
                "freshness": self.twin["freshness"],
                "availability": {
                    "state": "available",
                    "message": "Authoritative canonical Release Delta facts are available.",
                },
                "data": {
                    "summary": {
                        "total": 1,
                        "create": 0,
                        "update": 1,
                        "explicit_delete": 0,
                        "no_op": 0,
                        "unknown": 0,
                        "immutable_conflict": 0,
                        "namespace_rewrite": 0,
                    },
                    "changes": [
                        {
                            "change_id": "delta_real_1",
                            "resource_identity": "v1:ConfigMap:sample-target:sample-app",
                            "api_version": "v1",
                            "kind": "ConfigMap",
                            "namespace": "sample-target",
                            "name": "sample-app",
                            "helm_release": None,
                            "action": "update",
                            "current_summary": "kind=ConfigMap",
                            "planned_summary": "kind=ConfigMap",
                            "risk": "low",
                            "reason": "Canonical intent differs at data.mode.",
                            "canonical_diff": '{"current":{},"planned":{},"field_changes":[]}',
                            "evidence_refs": [
                                {
                                    "evidence_id": "evidence_bundle",
                                    "source_type": "bundle",
                                    "summary": "generated/configmap.yaml",
                                    "captured_at": self.twin["updated_at"],
                                    "redacted": True,
                                }
                            ],
                            "redacted": True,
                        }
                    ],
                    "page": {
                        "limit": int((params or {}).get("limit") or 25),
                        "has_more": False,
                        "next_cursor": None,
                        "result_count": 1,
                    },
                    "artifacts": [],
                },
            },
        )

    async def get_namespace_twin_dependency_graph(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        self.dependency_graph_params = deepcopy(params or {})
        plan_node = {
            "node_id": "node_plan_apply",
            "resource_identity": "plan:apply",
            "api_version": "esda.bosgenesis/v1",
            "kind": "PlanPhase",
            "namespace": "sample-target",
            "name": "apply",
            "status": "present",
            "risk": "low",
            "confidence": "deterministic",
            "evidence_refs": ["evidence_plan"],
        }
        config_node = {
            "node_id": "node_config_sample",
            "resource_identity": "v1:ConfigMap:sample-target:sample-app",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "sample-target",
            "name": "sample-app",
            "status": "present",
            "risk": "low",
            "confidence": "deterministic",
            "evidence_refs": ["evidence_bundle"],
        }
        secret_node = {
            "node_id": "node_secret_missing",
            "resource_identity": "v1:Secret:sample-target:sample-secret",
            "api_version": "v1",
            "kind": "Secret",
            "namespace": "sample-target",
            "name": "sample-secret",
            "status": "missing",
            "risk": "high",
            "confidence": "high",
            "evidence_refs": ["evidence_bundle"],
        }
        edges = [
            {
                "edge_id": "edge_plan_config",
                "source": plan_node["node_id"],
                "target": config_node["node_id"],
                "source_label": "PlanPhase/apply",
                "target_label": "ConfigMap/sample-app",
                "relationship": "plan_applies",
                "status": "valid",
                "confidence": "deterministic",
                "risk": "low",
                "evidence_refs": ["evidence_plan"],
            },
            {
                "edge_id": "edge_config_secret",
                "source": config_node["node_id"],
                "target": secret_node["node_id"],
                "source_label": "ConfigMap/sample-app",
                "target_label": "Secret/sample-secret",
                "relationship": "secret_name_ref",
                "status": "missing",
                "confidence": "high",
                "risk": "high",
                "evidence_refs": ["evidence_bundle"],
            },
        ]
        selected_id = (params or {}).get("resource")
        selected_node = next(
            (
                node
                for node in (plan_node, config_node, secret_node)
                if node["node_id"] == selected_id
            ),
            None,
        )
        selected_context = {"found": False}
        if selected_node:
            selected_context = {
                "found": True,
                "node": selected_node,
                "inbound_edges": [edge for edge in edges if edge["target"] == selected_id],
                "outbound_edges": [edge for edge in edges if edge["source"] == selected_id],
                "impact_paths": [
                    {
                        "nodes": [config_node["node_id"], secret_node["node_id"]],
                        "relationships": ["secret_name_ref"],
                        "status": "missing",
                        "confidence": "high",
                    }
                ]
                if selected_id == config_node["node_id"]
                else [],
            }
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/dependency-graph",
            {
                "schema_version": "1.0.0",
                "twin_id": twin_id,
                "decision_version": self.twin["decision_version"],
                "lifecycle_status": self.twin["lifecycle_status"],
                "freshness": self.twin["freshness"],
                "availability": {
                    "state": "available",
                    "message": "Authoritative dependency graph facts are available.",
                },
                "data": {
                    "summary": {
                        "nodes": 3,
                        "edges": 2,
                        "missing_nodes": 1,
                        "uncertain_nodes": 0,
                        "high_risk_nodes": 1,
                        "cycles": 0,
                    },
                    "nodes": [plan_node, config_node, secret_node],
                    "edges": edges,
                    "table_rows": edges,
                    "node_page": {
                        "limit": int((params or {}).get("limit") or 50),
                        "has_more": False,
                        "next_cursor": None,
                        "result_count": 3,
                    },
                    "edge_page": {
                        "limit": int((params or {}).get("limit") or 50),
                        "has_more": False,
                        "next_cursor": None,
                        "result_count": 2,
                    },
                    "selected_context": selected_context,
                    "findings": [
                        {
                            "code": "MISSING_DEPENDENCY",
                            "severity": "high",
                            "summary": "Secret/sample-secret is missing.",
                        }
                    ],
                },
            },
        )

    async def get_namespace_twin_policy(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        self.policy_params = deepcopy(params or {})
        finding = {
            "id": "policyfinding_human_approval",
            "finding_id": "policyfinding_human_approval",
            "code": "HUMAN_APPROVAL_REQUIRED",
            "category": "approval_policy",
            "severity": "review",
            "effect": "approval_required",
            "status": "approval_required",
            "title": "Human Approval Required",
            "summary": "The machine plan requires human approval before mutation.",
            "detail": "The machine plan requires human approval before mutation.",
            "message": "The machine plan requires human approval before mutation.",
            "policy_version": "namespace-twin-policy-2026.07.1",
            "evidence_refs": [
                {
                    "evidence_id": "evidence_machine_plan",
                    "source_type": "bundle",
                    "summary": "machine_execution_plan.yaml#executor_contract",
                    "captured_at": self.twin["updated_at"],
                    "redacted": True,
                }
            ],
        }
        findings = [finding]
        effect = (params or {}).get("effect")
        if effect and effect != finding["effect"]:
            findings = []
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/policy",
            {
                "schema_version": "1.0.0",
                "twin_id": twin_id,
                "decision_version": self.twin["decision_version"],
                "lifecycle_status": self.twin["lifecycle_status"],
                "freshness": self.twin["freshness"],
                "availability": {
                    "state": "available",
                    "message": "Authoritative deterministic Policy Twin facts are available.",
                },
                "data": {
                    "verdict": "allow_with_approval",
                    "policy_version": "namespace-twin-policy-2026.07.1",
                    "policy_bundle_hash": "b" * 64,
                    "input_hash": "a" * 64,
                    "groups": ["namespace_boundary", "approval_policy"],
                    "findings": findings,
                    "passed_groups": ["namespace_boundary"],
                    "evidence_axis": {
                        "classification": "partial",
                        "completeness": "partial",
                        "freshness": "fresh",
                        "required_count": 5,
                        "present_count": 4,
                        "missing": ["authoritative_dry_run"],
                        "stale": [],
                        "checks": [],
                    },
                    "risk_axis": {
                        "level": "medium",
                        "score": 55,
                        "raw_score": 55,
                        "rules_version": "namespace-twin-risk-1.0.0",
                        "thresholds": {
                            "green_max": 29,
                            "amber_min": 30,
                            "amber_max": 69,
                            "red_min": 70,
                        },
                    },
                    "decision_projection": {
                        "level": "amber",
                        "label": "Amber",
                        "preliminary": True,
                        "decision_is_final": False,
                        "precedence_rule": "approval_required",
                        "summary": "Amber because human approval is required.",
                        "hard_blocks": [],
                        "approval_required": True,
                        "model_authority": False,
                        "axes_hash": "c" * 64,
                    },
                    "rule_contributions": [
                        {
                            "axis": "policy",
                            "rule": "approval_policy",
                            "matched": True,
                            "effect": "approval_required",
                            "contribution": 0,
                            "reason": "Human approval is required.",
                            "evidence_refs": ["machine_execution_plan.yaml"],
                        },
                        {
                            "axis": "risk",
                            "rule": "statefulset_change",
                            "matched": True,
                            "effect": "increase",
                            "contribution": 25,
                            "weight": 25,
                            "reason": "A StatefulSet changes.",
                            "evidence_refs": ["apps/v1:StatefulSet:sample-target:sample"],
                        },
                        {
                            "axis": "decision",
                            "rule": "approval_required",
                            "matched": True,
                            "effect": "amber",
                            "selected": True,
                            "contribution": 0,
                            "reason": "Approval-required precedence selected Amber.",
                            "evidence_refs": [],
                        },
                    ],
                    "command_fingerprint_hash": None,
                    "dry_run_job_id": None,
                    "model_authority": False,
                    "artifacts": [],
                },
            },
        )
    def _dry_run_response_data(self, params: dict | None = None) -> dict:
        self.dry_run_params = deepcopy(params or {})
        observation = {
            "observation_id": "obs_phase5e_k8s",
            "phase": "phase-apply",
            "step": "step-k8s-apply",
            "tool": "k8s_apply_manifest",
            "outcome": "accepted",
            "summary": "Kubernetes server-side dry-run accepted the manifest.",
            "resource_identity": "sample-target/ConfigMap/sample-app",
            "evidence_refs": [
                {
                    "evidence_id": "obs_phase5e_k8s",
                    "source_type": "dry_run",
                    "source_id": "obs_phase5e_k8s",
                    "summary": "Kubernetes server-side dry-run accepted the manifest.",
                    "captured_at": self.twin["updated_at"],
                    "redacted": True,
                    "href": None,
                }
            ],
            "redacted": True,
        }
        observations = [observation]
        for key, value in (params or {}).items():
            if key in {"phase", "step", "tool", "outcome"} and value:
                observations = [
                    item
                    for item in observations
                    if str(value).lower() in str(item.get(key) or "").lower()
                ]
            if key == "resource" and value:
                observations = [
                    item
                    for item in observations
                    if str(value).lower()
                    in str(item.get("resource_identity") or "").lower()
                ]
        return {
            "schema_version": "1.0.0",
            "twin_id": self.twin["twin_id"],
            "decision_version": self.twin["decision_version"],
            "lifecycle_status": "succeeded",
            "freshness": self.twin["freshness"],
            "availability": {
                "state": "available",
                "message": "Authoritative dry-run and structured diff evidence is available.",
            },
            "data": {
                "dry_run_job_id": "job-phase5e-real",
                "status": "passed",
                "qualification_status": "passed",
                "authoritative": True,
                "bundle_hash": "b" * 64,
                "input_hash": "a" * 64,
                "target_namespace": "sample-target",
                "snapshot": {
                    "snapshot_id": "snapshot_phase5e",
                    "captured_at": self.twin["updated_at"],
                    "hash": "d" * 64,
                },
                "command_fingerprint_hash": "e" * 64,
                "command_fingerprints": ["sha256:phase5e-command"],
                "validations": [
                    {
                        "type": "bundle_schema",
                        "status": "passed",
                        "summary": "The bundle schema is valid.",
                    },
                    {
                        "type": "helm_dry_run",
                        "status": "passed",
                        "summary": "Helm dry-run accepted the release.",
                    },
                    {
                        "type": "kubernetes_server_dry_run",
                        "status": "passed",
                        "summary": "Kubernetes server-side dry-run accepted the manifest.",
                    },
                ],
                "observations": observations,
                "observation_counts": {
                    "accepted": len(observations),
                    "rejected": 0,
                    "warning": 0,
                    "skipped": 0,
                    "unknown": 0,
                },
                "structured_diff": {
                    "rows": [
                        {
                            "change_id": "delta_real_1",
                            "resource_identity": "v1:ConfigMap:sample-target:sample-app",
                            "api_version": "v1",
                            "kind": "ConfigMap",
                            "namespace": "sample-target",
                            "name": "sample-app",
                            "helm_release": None,
                            "action": "update",
                            "current_summary": "kind=ConfigMap",
                            "planned_summary": "kind=ConfigMap",
                            "risk": "low",
                            "reason": "Canonical intent differs at data.mode.",
                            "canonical_diff": '{"current":{},"planned":{},"field_changes":[]}',
                            "evidence_refs": [],
                            "redacted": True,
                        }
                    ],
                    "result_count": 1,
                    "unfiltered_result_count": 1,
                    "summary": {
                        "total": 1,
                        "create": 0,
                        "update": 1,
                        "explicit_delete": 0,
                        "no_op": 0,
                        "unknown": 0,
                        "immutable_conflict": 0,
                        "namespace_rewrite": 0,
                    },
                },
                "evidence_refs": ["job:job-phase5e-real"],
                "fidelity_limitations": [
                    "Server-side dry-run cannot prove controller convergence."
                ],
                "artifacts": [
                    {
                        "artifact_id": "report_phase5e",
                        "filename": "dry-run-report.json",
                        "media_type": "application/json",
                        "download_href": "/reports/dry-run-report.json",
                        "sha256": None,
                    }
                ],
                "failed_steps": [],
                "partial_steps": [],
                "applied_filters": deepcopy(params or {}),
                "model_authority": False,
                "automatic_instruction_submission": False,
                "automatic_mutation_retry": False,
            },
        }

    async def get_namespace_twin_dry_run(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/dry-run",
            self._dry_run_response_data(params),
        )

    async def attach_namespace_twin_dry_run_evidence(
        self, twin_id: str, payload: dict
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        assert payload["dry_run_job_id"] == "job-phase5e-real"
        finalized = deepcopy(self.twin)
        finalized.update(
            {
                "lifecycle_status": "succeeded",
                "decision": "amber",
                "decision_is_final": True,
            }
        )
        return _response(
            "POST",
            f"v1/namespace-twins/{twin_id}/dry-run-evidence",
            {
                "twin": finalized,
                "dry_run": self._dry_run_response_data(),
                "idempotent_replay": False,
            },
        )
    async def get_namespace_twin(self, twin_id: str) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response("GET", f"v1/namespace-twins/{twin_id}", self.twin)

    async def get_namespace_twin_events(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/events",
            {
                "events": [
                    {
                        "event_id": "evt_1",
                        "sequence": 1,
                        "event_type": "twin_requested",
                        "message": "Twin generation requested.",
                        "payload": {"target_namespace": "sample-target"},
                    },
                    {
                        "event_id": "evt_2",
                        "sequence": 2,
                        "event_type": "twin_generating",
                        "message": "Bundle facts extracted.",
                        "payload": {},
                    },
                ],
                "page": {"has_more": False},
            },
        )

    async def cancel_namespace_twin(self, twin_id: str) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        self.twin["lifecycle_status"] = "cancelled"
        self.twin["decision"] = "cancelled"
        self.twin["decision_is_final"] = True
        return _response("POST", f"v1/namespace-twins/{twin_id}/cancel", self.twin)


async def _safe_llm_response(self, **kwargs) -> dict:
    return {
        "summary": "SIGMA explains the persisted preliminary twin facts.",
        "decision": "red",
        "actions": [{"code": "unsafe_override", "enabled": True}],
        "model_profile": kwargs.get("model_profile"),
        "prompt_version": "namespace_twin_overview_explanation_v1",
        "prompt_hash": "model-supplied-hash-is-not-authoritative",
        "token_usage": {"total_tokens": 42},
        "llm_fallback": {"used": False, "error": None},
    }


def build_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'phase4-esda.db'}")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("SECRET_KEY", "phase4-secret")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER", "disabled")
    monkeypatch.setenv("ARTIFACT_STORAGE_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    get_settings.cache_clear()
    import backend.app.digital_twin_gateway as gateway_module
    import backend.app.main as main_module

    monkeypatch.setattr(main_module.AzureGpt5Service, "structured_response", _safe_llm_response)

    monkeypatch.setattr(gateway_module, "MopExecutionAgentClient", FakeNamespaceTwinClient)
    main_module = importlib.reload(main_module)
    return TestClient(main_module.create_app())


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def test_real_gateway_requires_auth_and_projects_execution_agent_core(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/digital-twins").status_code == 401
        login(client)

        config = client.get("/api/digital-twins/config")
        listed = client.get("/api/digital-twins")
        detail = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}")
        overview = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/overview",
            params={"model_profile": "azure_gpt5_pro"},
        )
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))

    assert config.status_code == 200
    assert config.headers["x-esda-data-mode"] == "real_core"
    assert config.json()["label"] == (
        "Real Lifecycle + Overview + Release Delta + Dependency Graph + Policy Twin + Dry-run / Diff Twin + Mock Remaining Modules"
    )
    assert listed.json()["items"][0]["data_mode"] == "real_core"
    assert listed.json()["warning"].startswith(
        "Lifecycle, Overview, Release Delta, Dependency Graph, summaries"
    )
    assert detail.json()["lifecycle_status"] == "awaiting_dry_run"
    assert detail.json()["decision_is_final"] is False
    safe_explanation = overview.json()["safe_explanation"]
    assert safe_explanation["content"].startswith("SIGMA explains")
    assert safe_explanation["status"] == "generated"
    assert safe_explanation["chain_of_thought_included"] is False
    assert "decision" not in safe_explanation
    assert "actions" not in safe_explanation
    assert safe_explanation["prompt_hash"] != "model-supplied-hash-is-not-authoritative"
    assert explanation_logs[0].explanation_id == safe_explanation["explanation_id"]
    assert explanation_logs[0].safe_output_json == safe_explanation
    assert len(explanation_logs) == 1


def test_real_create_events_cancel_and_invalid_browser_scenario_are_typed(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        invalid = client.post("/api/digital-twins", json={"scenario_id": "green-helm"})
        created = client.post(
            "/api/digital-twins",
            json={
                "source": {"type": "local_path", "value": "C:/tmp/sample-bundle"},
                "target_namespace": "sample-target",
                "target_cluster": "contract-cluster",
                "idempotency_key": "phase4-gateway",
            },
        )
        events = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}/events")
        cancelled = client.post(f"/api/digital-twins/{CORE_TWIN['twin_id']}/cancel")

    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "real_bundle_required"
    assert created.status_code == 200
    assert created.json()["target"]["namespace"] == "sample-target"
    assert [event["sequence"] for event in events.json()["events"]] == [1, 2]
    assert cancelled.json()["lifecycle_status"] == "cancelled"


def test_remaining_mock_modules_are_labeled_and_cannot_supply_real_actions(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        actions = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}/actions")
        list_page = client.get("/digital-twins")
        static_page = client.get("/static/digital-twin/digital-twins.html")
        list_script = client.get("/static/digital-twin/digital-twins-page.js")
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js")
        adapter = client.get("/static/digital-twin/twin-http-adapter.js")

    assert {action["code"] for action in actions.json()} == {
        "open_twin",
        "cancel_generation",
    }
    assert 'data-adapter-mode="real_core"' in list_page.text
    assert "Real Core + Mock Modules" in static_page.text or "data-mode-marker" in static_page.text
    assert '["mock_server", "real_core"]' in adapter.text
    assert "return Array.isArray(item.actions)" in detail_script.text
    assert "adapter.getTwin(item.twin_id)" in detail_script.text
    assert "tab.safe_explanation.content" in detail_script.text
    assert "limit: 25" in list_script.text
    assert "if (realCore)" in list_script.text
    assert "load({ silent: true })" in list_script.text
    assert "adapter.advanceGeneration(item.twin_id)" in list_script.text

def test_real_audit_history_cannot_be_cleared(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.delete("/api/digital-twins/history")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "durable_history_not_clearable"


def test_release_delta_is_authoritative_filterable_and_audit_logged(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/release-delta",
            params={
                "action": "update",
                "risk": "low",
                "kind": "ConfigMap",
                "limit": 25,
                "model_profile": "azure_gpt5_pro",
            },
        )
        forwarded = client.app.state.digital_twin_gateway.client.release_delta_params
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js").text
        adapter_script = client.get("/static/digital-twin/twin-http-adapter.js").text

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "available"
    assert payload["kind"] == "delta"
    assert payload["data_mode"] == "real_core"
    assert payload["module_mode"] == "authoritative"
    assert payload["non_authoritative"] is False
    assert payload["data"]["summary"]["update"] == 1
    assert payload["data"]["changes"][0]["resource_identity"].endswith(":sample-app")
    assert payload["safe_explanation"]["chain_of_thought_included"] is False
    assert payload["safe_explanation"]["prompt_version"] == (
        "namespace_twin_release_delta_explanation_v1"
    )
    assert forwarded == {
        "action": "update",
        "risk": "low",
        "kind": "ConfigMap",
        "limit": "25",
    }
    assert explanation_logs[0].prompt_version == "namespace_twin_release_delta_explanation_v1"
    assert "tabData.changes" in detail_script
    assert "data-delta-filter" in detail_script
    assert "query = Object.assign" in adapter_script


def test_dependency_graph_is_authoritative_filterable_and_audit_logged(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/dependency-graph",
            params={
                "kind": "ConfigMap",
                "risk": "high",
                "status": "missing",
                "relationship": "secret_name_ref",
                "confidence": "high",
                "edge_status": "missing",
                "search": "sample",
                "missing_only": "true",
                "resource": "node_config_sample",
                "limit": 50,
                "model_profile": "azure_gpt5_pro",
            },
        )
        forwarded = client.app.state.digital_twin_gateway.client.dependency_graph_params
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js").text
        adapter_script = client.get("/static/digital-twin/twin-http-adapter.js").text

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "available"
    assert payload["kind"] == "graph"
    assert payload["data_mode"] == "real_core"
    assert payload["module_mode"] == "authoritative"
    assert payload["non_authoritative"] is False
    assert payload["data"]["summary"]["nodes"] == 3
    assert payload["data"]["summary"]["missing_nodes"] == 1
    assert payload["data"]["selected_context"]["found"] is True
    assert payload["data"]["selected_context"]["impact_paths"][0]["relationships"] == [
        "secret_name_ref"
    ]
    assert payload["safe_explanation"]["chain_of_thought_included"] is False
    assert payload["safe_explanation"]["prompt_version"] == (
        "namespace_twin_dependency_graph_explanation_v1"
    )
    assert forwarded == {
        "kind": "ConfigMap",
        "risk": "high",
        "status": "missing",
        "relationship": "secret_name_ref",
        "confidence": "high",
        "edge_status": "missing",
        "search": "sample",
        "missing_only": "true",
        "resource": "node_config_sample",
        "limit": "50",
    }
    assert explanation_logs[0].prompt_version == ("namespace_twin_dependency_graph_explanation_v1")
    assert "data.nodes" in detail_script
    assert "data.edges" in detail_script
    assert "data-graph-filter" in detail_script
    assert "data-graph-node" in detail_script
    assert "The browser infers no relationships" in detail_script
    assert "query = Object.assign" in adapter_script

def test_policy_twin_is_authoritative_filterable_and_model_cannot_override(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/policy",
            params={"effect": "approval_required", "model_profile": "azure_gpt5_pro"},
        )
        forwarded = deepcopy(client.app.state.digital_twin_gateway.client.policy_params)
        gate = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}/gate")
        detail = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}")
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js").text

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert payload["state"] == "available"
    assert payload["kind"] == "findings"
    assert payload["data_mode"] == "real_core"
    assert payload["module_mode"] == "authoritative"
    assert payload["non_authoritative"] is False
    assert forwarded == {"effect": "approval_required"}
    assert data["verdict"] == "allow_with_approval"
    assert data["policy_version"] == "namespace-twin-policy-2026.07.1"
    assert len(data["policy_bundle_hash"]) == 64
    assert data["evidence_axis"]["completeness"] == "partial"
    assert data["evidence_axis"]["freshness"] == "fresh"
    assert data["risk_axis"]["level"] == "medium"
    assert data["risk_axis"]["score"] == 55
    assert data["risk_axis"]["rules_version"] == "namespace-twin-risk-1.0.0"
    assert data["decision_projection"]["label"] == "Amber"
    assert data["decision_projection"]["precedence_rule"] == "approval_required"
    assert data["decision_projection"]["decision_is_final"] is False
    assert data["model_authority"] is False
    assert {item["axis"] for item in data["rule_contributions"]} == {
        "policy",
        "risk",
        "decision",
    }
    explanation = payload["safe_explanation"]
    assert explanation["content"].startswith("SIGMA explains")
    assert explanation["prompt_version"] == "namespace_twin_policy_explanation_v1"
    assert explanation["chain_of_thought_included"] is False
    assert explanation["model_authority"] is False
    assert "decision" not in explanation
    assert "actions" not in explanation
    assert detail.json()["decision"] == "pending"
    assert detail.json()["decision_is_final"] is False
    assert gate.json()["decision"] == "pending"
    assert gate.json()["policy"] == "allow_with_approval"
    assert gate.json()["dry_run"] == "passed"
    assert gate.json()["risk"]["score"] == 55
    assert gate.json()["decision_projection"]["label"] == "Amber"
    assert gate.json()["model_authority"] is False
    assert explanation_logs[0].prompt_version == "namespace_twin_policy_explanation_v1"
    assert explanation_logs[0].safe_output_json == explanation
    assert "data-policy-filter" in detail_script
    assert "data-rule-contribution" in detail_script
    assert 'panel.dataset.loading = "true"' in detail_script
    assert 'activePanel.dataset.loading !== "true"' in detail_script
    assert "Server-authoritative deterministic axes" in detail_script


def test_dry_run_diff_twin_is_authoritative_filterable_and_non_mutating(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/dry-run",
            params={
                "phase": "phase-apply",
                "step": "step-k8s-apply",
                "resource": "sample-app",
                "tool": "k8s_apply_manifest",
                "outcome": "accepted",
                "model_profile": "azure_gpt5_pro",
            },
        )
        forwarded = deepcopy(client.app.state.digital_twin_gateway.client.dry_run_params)
        attached = client.post(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/dry-run-evidence",
            json={
                "dry_run_job_id": "job-phase5e-real",
                "bundle_hash": "b" * 64,
                "input_hash": "a" * 64,
                "command_fingerprint_hash": "e" * 64,
            },
        )
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js").text
        detail_css = client.get("/static/digital-twin/prototype-phase2.css").text

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert payload["state"] == "available"
    assert payload["kind"] == "dry-run"
    assert payload["module_mode"] == "authoritative"
    assert payload["non_authoritative"] is False
    assert forwarded == {
        "phase": "phase-apply",
        "step": "step-k8s-apply",
        "resource": "sample-app",
        "tool": "k8s_apply_manifest",
        "outcome": "accepted",
    }
    assert data["authoritative"] is True
    assert data["status"] == "passed"
    assert data["qualification_status"] == "passed"
    assert data["target_namespace"] == "sample-target"
    assert len(data["observations"]) == 1
    assert data["validations"][1]["type"] == "helm_dry_run"
    assert data["validations"][2]["type"] == "kubernetes_server_dry_run"
    assert data["structured_diff"]["result_count"] == 1
    assert data["command_fingerprint_hash"] == "e" * 64
    assert data["command_fingerprints"] == ["sha256:phase5e-command"]
    assert data["automatic_instruction_submission"] is False
    assert data["automatic_mutation_retry"] is False
    assert data["model_authority"] is False
    explanation = payload["safe_explanation"]
    assert explanation["prompt_version"] == "namespace_twin_dry_run_explanation_v1"
    assert explanation["chain_of_thought_included"] is False
    assert explanation["automatic_instruction_submission"] is False
    assert explanation["automatic_mutation_retry"] is False
    assert explanation_logs[0].safe_output_json == explanation
    assert attached.status_code == 200
    assert attached.json()["twin"]["decision_is_final"] is True
    assert attached.json()["idempotent_replay"] is False
    assert "data-dry-run-filter" in detail_script
    assert "data-copy-fingerprints" in detail_script
    assert "cannot submit instructions, retry a mutation" in detail_script
    assert ".dry-run-identity-grid" in detail_css