from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4
from copy import deepcopy
from datetime import UTC, datetime

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.auth.security import SessionPrincipal
from backend.app.db.database import Database
from backend.app.db.models import DigitalTwinExplanationLog
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.config import Settings
from backend.app.dependencies import get_current_user
from backend.app.digital_twin_mock import ACTIVE_STATES, TAB_ENDPOINTS, DigitalTwinMockService
from backend.app.tools.mop_execution_agent import (
    MopExecutionAgentClient,
    MopExecutionAgentError,
    MopExecutionAgentResponse,
)


DATA_MODE = "real_core"
MODULE_MODE = "mock_non_authoritative"


class DigitalTwinGatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class RealTwinCreateRequest(BaseModel):
    source: dict[str, Any] | None = None
    target_namespace: str | None = Field(default=None, max_length=253)
    target_cluster: str = Field(default="configured-cluster", max_length=253)
    idempotency_key: str | None = Field(default=None, max_length=200)
    supersedes_twin_id: str | None = Field(default=None, max_length=200)
    scenario_id: str | None = Field(default=None, max_length=100)


class RealTwinDryRunEvidenceRequest(BaseModel):
    dry_run_job_id: str = Field(min_length=1, max_length=200)
    bundle_hash: str | None = Field(default=None, min_length=64, max_length=64)
    input_hash: str | None = Field(default=None, min_length=64, max_length=64)
    command_fingerprint_hash: str | None = Field(default=None, min_length=64, max_length=64)
    wait_seconds: int = Field(default=0, ge=0, le=30)
    poll_interval_ms: int = Field(default=500, ge=100, le=5000)


class RealTwinReleaseNoteValidationRequest(BaseModel):
    release_note_artifact_id: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200000)
    model_profile: str | None = Field(default=None, max_length=200)

class DigitalTwinGatewayService:
    """Projects execution-agent facts without becoming decision authority."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: MopExecutionAgentClient | None = None,
        llm: AzureGpt5Service | None = None,
        database: Database | None = None,
    ) -> None:
        base_url = settings.digital_twin_execution_agent_url or None
        self.client = client or MopExecutionAgentClient(settings, base_url_override=base_url)
        self.fixtures = DigitalTwinMockService(enabled=True, default_delay_ms=0).repository

        self.llm = llm
        self.database = database

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("source") or not payload.get("target_namespace"):
            raise DigitalTwinGatewayError(
                409,
                "real_bundle_required",
                "Real twin generation requires a bundle source and target namespace. "
                "Browser scenarios remain preview-only.",
            )
        response = await self.client.create_namespace_twin(
            {
                "source": payload["source"],
                "target_namespace": payload["target_namespace"],
                "target_cluster": payload.get("target_cluster") or "configured-cluster",
                "idempotency_key": payload.get("idempotency_key"),
                "supersedes_twin_id": payload.get("supersedes_twin_id"),
            }
        )
        return self.project(self._unwrap(response))

    async def _list_phase4(self, query: dict[str, Any]) -> dict[str, Any]:
        cursor = str(query.get("cursor") or "")
        try:
            offset = int(cursor.removeprefix("cursor_")) if cursor else 0
        except ValueError as exc:
            raise DigitalTwinGatewayError(422, "invalid_cursor", "cursor is not valid.") from exc
        limit = max(1, min(int(query.get("limit") or 25), 100))
        response = await self.client.list_namespace_twins(
            {
                "limit": limit,
                "offset": offset,
                "lifecycle_status": query.get("lifecycle") or None,
                "target_namespace": query.get("namespace") or query.get("target") or None,
            }
        )
        core = self._unwrap(response)
        items = [self.project(item) for item in core.get("items", [])]
        search = str(query.get("q") or query.get("search") or "").strip().lower()
        if search:
            items = [
                item
                for item in items
                if search
                in " ".join(
                    [
                        item["twin_id"],
                        item["display_name"],
                        item["target"]["cluster_name"],
                        item["target"]["namespace"],
                        item["bundle"]["bundle_name"],
                    ]
                ).lower()
            ]
        decision = str(query.get("decision") or "")
        if decision and decision != "all":
            items = [item for item in items if item["decision"] == decision]
        metrics = {
            "total": len(items),
            "green": sum(item["decision"] == "green" for item in items),
            "amber": sum(item["decision"] == "amber" for item in items),
            "red": sum(item["decision"] == "red" for item in items),
            "generating": sum(item["lifecycle_status"] in ACTIVE_STATES for item in items),
            "stale": 0,
            "linked": 0,
        }
        page = core.get("page") or {}
        next_offset = page.get("next_offset")
        return {
            "schema_version": "1.0.0",
            "data_mode": DATA_MODE,
            "module_mode": MODULE_MODE,
            "generated_at": datetime.now(UTC).isoformat(),
            "items": items,
            "metrics": metrics,
            "page": {
                "limit": limit,
                "has_more": bool(page.get("has_more")),
                "next_cursor": f"cursor_{next_offset}" if next_offset is not None else None,
                "previous_cursor": f"cursor_{max(0, offset - limit)}" if offset else None,
                "result_count": page.get("result_count", len(items)),
                "offset": offset,
            },
            "applied_query": deepcopy(query),
            "partial": False,
            "warning": "Lifecycle facts are real. Evidence modules remain mock and non-authoritative.",
        }

    async def get(self, twin_id: str) -> dict[str, Any]:
        return self.project(self._unwrap(await self.client.get_namespace_twin(twin_id)))

    async def cancel(self, twin_id: str) -> dict[str, Any]:
        return self.project(self._unwrap(await self.client.cancel_namespace_twin(twin_id)))

    async def refresh_drift(self, twin_id: str) -> dict[str, Any]:
        return self._unwrap(await self.client.refresh_namespace_twin_drift(twin_id))

    async def refresh_runtime_behavior(self, twin_id: str) -> dict[str, Any]:
        return self._unwrap(await self.client.refresh_namespace_twin_runtime_behavior(twin_id))

    async def validate_release_note(
        self,
        twin_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        """Extract bounded claims with SIGMA, then delegate classification authority."""
        twin = await self.get(twin_id)
        artifact_id = str(payload.get("release_note_artifact_id") or "").strip()
        content = str(payload.get("content") or "")
        model_profile = str(payload.get("model_profile") or "azure_gpt5_pro")
        artifact_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        prompt_version = "namespace_twin_release_note_claim_extraction_v1"
        input_envelope = {
            "twin_id": twin_id,
            "artifact_id": artifact_id,
            "artifact_hash": artifact_hash,
            "release_note_markdown": content,
            "allowed_categories": [
                "image", "configuration", "migration", "pvc_storage", "rbac", "route",
                "rollback", "breaking_change", "known_risk", "other",
            ],
        }
        input_hash = hashlib.sha256(
            json.dumps(
                {key: value for key, value in input_envelope.items() if key != "release_note_markdown"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        system = (
            "Extract bounded operational claims from the supplied release-note Markdown. "
            "Return JSON with a claims array only. Each item must contain category and claim. "
            "Use only the allowed categories. Preserve negation because deterministic code must "
            "detect contradictions. Do not classify support, infer evidence, edit the artifact, "
            "include credentials or Secret values, reveal hidden chain-of-thought, or decide "
            "execution eligibility. Return at most 100 concise claims."
        )
        prompt_hash = hashlib.sha256((system + input_hash).encode("utf-8")).hexdigest()
        fallback_claims = _release_note_fallback_claims(content)
        fallback = {
            "claims": fallback_claims,
            "model_profile": model_profile,
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=input_envelope,
                fallback=fallback,
                model_profile=model_profile,
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        claims = _sanitize_release_note_claims(generated.get("claims"))
        if not claims:
            claims = fallback_claims
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        token_usage = generated.get("token_usage") or {}
        safe_summary = (
            f"Extracted {len(claims)} bounded operational claim(s); deterministic twin "
            "evidence retains classification and execution authority."
        )
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": safe_summary,
            "evidence_refs": [],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        self._unwrap(
            await self.client.validate_namespace_twin_release_note(
                twin_id,
                {
                    "release_note_artifact_id": artifact_id,
                    "release_note_artifact_hash": artifact_hash,
                    "claims": claims,
                    "extraction": {
                        "method": "bounded_model_with_deterministic_fallback",
                        "model_profile": generated.get("model_profile") or model_profile,
                        "prompt_version": prompt_version,
                        "prompt_hash": prompt_hash,
                        "input_hash": input_hash,
                        "fallback_used": fallback_used,
                        "safe_summary": safe_summary,
                    },
                },
                actor_id=actor_id,
            )
        )
        return await self.tab(
            await self.get(twin_id),
            "release-note-validation",
            model_profile=model_profile,
        )
    async def attach_dry_run_evidence(
        self,
        twin_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._unwrap(
            await self.client.attach_namespace_twin_dry_run_evidence(twin_id, payload)
        )
        projected = deepcopy(result)
        if isinstance(projected.get("twin"), dict):
            projected["twin"] = self.project(projected["twin"])
        return projected

    async def events(self, twin_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        core = self._unwrap(
            await self.client.get_namespace_twin_events(twin_id, {"limit": limit, "offset": offset})
        )
        page = core.get("page") or {}
        events = [
            {
                **event,
                "summary": event.get("message"),
                "actor": "execution-agent",
            }
            for event in core.get("events", [])
        ]
        return {
            "schema_version": "1.0.0",
            "data_mode": DATA_MODE,
            "twin_id": twin_id,
            "events": events,
            "next_cursor": (f"cursor_{offset + len(events)}" if page.get("has_more") else None),
            "has_more": bool(page.get("has_more")),
        }

    async def _tab_phase4(self, twin: dict[str, Any], slug: str) -> dict[str, Any]:
        fixture_twin = deepcopy(self.fixtures.seed_twins[0])
        fixture_twin["twin_id"] = twin["twin_id"]
        fixture_twin["scenario_id"] = "green-helm"
        tab = self.fixtures.tab(fixture_twin, slug)
        tab["data_mode"] = "mock_module"
        tab["module_mode"] = MODULE_MODE
        tab["non_authoritative"] = True
        tab["summary"] = (
            "Mock / non-authoritative module preview. This data cannot change the real lifecycle "
            "or action eligibility. " + str(tab.get("summary") or "")
        )
        return tab

    @staticmethod
    def _project_phase4(core: dict[str, Any]) -> dict[str, Any]:
        status = str(core.get("lifecycle_status") or "requested")
        facts = core.get("facts") or {}
        expires_at = core.get("expires_at")
        decision = str(core.get("decision") or "pending")
        return {
            "schema_version": core.get("schema_version") or "1.0.0",
            "data_mode": DATA_MODE,
            "module_mode": MODULE_MODE,
            "scenario_id": "real-core-provisional",
            "twin_id": core["twin_id"],
            "display_name": core.get("display_name") or core["twin_id"],
            "decision_version": int(core.get("decision_version") or 1),
            "decision": decision,
            "decision_is_final": bool(core.get("decision_is_final")),
            "lifecycle_status": status,
            "visible_lifecycle": status,
            "risk": {"level": "preliminary", "score": None},
            "autonomy_eligibility": "not_available",
            "recommended_action": (
                "Await the existing authoritative dry-run. No execution decision is available "
                "from the Phase 4 foundation."
            ),
            "freshness": {
                "status": "collecting" if status in ACTIVE_STATES else status,
                "captured_at": core.get("updated_at"),
                "expires_at": expires_at,
                "superseded_by": core.get("superseded_by"),
                "message": "Real lifecycle facts; evidence modules are still mock.",
            },
            "target": {
                "cluster_id": core.get("target_cluster") or "configured-cluster",
                "cluster_name": core.get("target_cluster") or "Configured cluster",
                "namespace": core.get("target_namespace"),
            },
            "bundle": {
                "bundle_id": core.get("input_hash"),
                "bundle_name": core.get("bundle_name"),
                "bundle_hash": core.get("bundle_hash"),
                "release_version": core.get("release_version") or "not_available",
                "open_href": None,
            },
            "created_by": core.get("actor_id"),
            "created_by_display": core.get("actor_id"),
            "created_at": core.get("created_at"),
            "updated_at": core.get("updated_at"),
            "relationships": {
                "dry_run_job_id": None,
                "approval_id": None,
                "approval_status": "not_available",
                "execution_id": None,
                "execution_status": "unlinked",
                "used_for_execution": False,
            },
            "top_reasons": [
                {
                    "code": "REAL_CORE_PROVISIONAL",
                    "summary": "Bundle facts and lifecycle are persisted by the execution agent.",
                    "severity": "info",
                    "tab_slug": "overview",
                },
                {
                    "code": "AUTHORITATIVE_DRY_RUN_PENDING",
                    "summary": "The existing authoritative dry-run has not supplied evidence yet.",
                    "severity": "review",
                    "tab_slug": "dry-run",
                },
            ],
            "actions": deepcopy(core.get("actions") or []),
            "optional_states": {
                slug: MODULE_MODE
                for slug in (
                    "policy",
                    "dry-run",
                    "rollback",
                    "drift",
                    "runtime-behavior",
                )
            },
            "prior_decision": None,
            "progress_index": {
                "requested": 0,
                "generating": 1,
                "awaiting_dry_run": 2,
                "decision_calculating": 3,
            }.get(status, 4),
            "foundation_facts": facts,
        }

    @staticmethod
    def project(core: dict[str, Any]) -> dict[str, Any]:
        """Copy execution-agent facts without recomputing policy or eligibility."""
        projected = deepcopy(core)
        projected["data_mode"] = DATA_MODE
        projected["module_mode"] = "mixed_authoritative_overview"
        projected["scenario_id"] = "real-core-phase5a"
        return projected

    async def list(self, query: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.list_namespace_twins(
            {
                "q": query.get("q") or query.get("search") or None,
                "decision": query.get("decision") or None,
                "lifecycle_status": query.get("lifecycle") or None,
                "freshness": query.get("freshness") or None,
                "target_namespace": query.get("namespace") or query.get("target") or None,
                "bundle_name": query.get("bundle") or None,
                "actor_id": query.get("creator") or query.get("created_by") or None,
                "created_from": query.get("created_from") or None,
                "created_to": query.get("created_to") or None,
                "linked_execution": query.get("linked_execution") or None,
                "sort": query.get("sort") or "created_at",
                "direction": query.get("direction") or "desc",
                "cursor": query.get("cursor") or None,
                "limit": max(1, min(int(query.get("limit") or 25), 100)),
            }
        )
        core = self._unwrap(response)
        return {
            "schema_version": core.get("schema_version") or "1.0.0",
            "data_mode": DATA_MODE,
            "module_mode": "mixed_authoritative_overview",
            "generated_at": datetime.now(UTC).isoformat(),
            "items": [self.project(item) for item in core.get("items", [])],
            "metrics": deepcopy(core.get("metrics") or {}),
            "page": deepcopy(core.get("page") or {}),
            "applied_query": deepcopy(core.get("applied_query") or query),
            "partial": False,
            "warning": (
                "Lifecycle, Overview, Release Delta, Dependency Graph, summaries, freshness, "
                "Policy Twin, Dry-run / Diff Twin, Rollback Twin, Drift Twin, Runtime Behavior Twin, Release Note Validation Twin, Audit Reports, and actions are authoritative. Remaining evidence modules are mock and "
                "non-authoritative."
            ),
        }

    async def tab(
        self,
        twin: dict[str, Any],
        slug: str,
        *,
        model_profile: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if slug == "overview":
            overview = self._unwrap(await self.client.get_namespace_twin_overview(twin["twin_id"]))
            overview["data_mode"] = DATA_MODE
            overview["module_mode"] = "authoritative"
            overview["non_authoritative"] = False
            overview["safe_explanation"] = await self.safe_explanation(
                twin,
                overview,
                model_profile=model_profile,
            )
            return overview
        if slug == "release-delta":
            query = query or {}
            params = {
                key: query.get(key)
                for key in ("action", "risk", "kind", "cursor", "limit")
                if query.get(key) not in (None, "")
            }
            release_delta = self._unwrap(
                await self.client.get_namespace_twin_release_delta(twin["twin_id"], params)
            )
            availability = release_delta.get("availability") or {}
            release_delta.update(
                {
                    "state": availability.get("state") or "not_available",
                    "kind": "delta",
                    "title": "Release Delta Twin",
                    "summary": availability.get("message")
                    or "Canonical release delta facts are unavailable.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "applied_query": params,
                }
            )
            release_delta["safe_explanation"] = await self.release_delta_explanation(
                twin, release_delta, model_profile=model_profile
            )
            return release_delta
        if slug == "dependency-graph":
            query = query or {}
            params = {
                key: query.get(key)
                for key in (
                    "kind",
                    "risk",
                    "status",
                    "namespace",
                    "relationship",
                    "confidence",
                    "edge_status",
                    "search",
                    "missing_only",
                    "resource",
                    "node_cursor",
                    "edge_cursor",
                    "limit",
                )
                if query.get(key) not in (None, "")
            }
            dependency_graph = self._unwrap(
                await self.client.get_namespace_twin_dependency_graph(twin["twin_id"], params)
            )
            availability = dependency_graph.get("availability") or {}
            dependency_graph.update(
                {
                    "state": availability.get("state") or "not_available",
                    "kind": "graph",
                    "title": "Dependency Graph Twin",
                    "summary": availability.get("message")
                    or "Authoritative dependency graph facts are unavailable.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "applied_query": params,
                }
            )
            dependency_graph["safe_explanation"] = await self.dependency_graph_explanation(
                twin, dependency_graph, model_profile=model_profile
            )
            return dependency_graph
        if slug == "dry-run":
            query = query or {}
            params = {
                key: query.get(key)
                for key in ("phase", "step", "resource", "tool", "outcome")
                if query.get(key) not in (None, "", "all")
            }
            dry_run = self._unwrap(
                await self.client.get_namespace_twin_dry_run(twin["twin_id"], params)
            )
            availability = dry_run.get("availability") or {}
            data = dry_run.get("data") or {}
            counts = data.get("observation_counts") or {}
            dry_run.update(
                {
                    "state": availability.get("state") or "not_run",
                    "kind": "dry-run",
                    "title": "Authoritative Dry-run / Diff Twin",
                    "summary": availability.get("message")
                    or "Authoritative dry-run evidence has not been attached.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "applied_query": params,
                    "metrics": [
                        {
                            "label": "Accepted",
                            "value": int(counts.get("accepted") or 0),
                            "tone": "green",
                        },
                        {
                            "label": "Rejected",
                            "value": int(counts.get("rejected") or 0),
                            "tone": "red",
                        },
                        {
                            "label": "Warnings",
                            "value": int(counts.get("warning") or 0),
                            "tone": "amber",
                        },
                        {
                            "label": "Diff rows",
                            "value": int(
                                (data.get("structured_diff") or {}).get("result_count") or 0
                            ),
                            "tone": "info",
                        },
                    ],
                }
            )
            if data:
                dry_run["safe_explanation"] = await self.dry_run_explanation(
                    twin,
                    dry_run,
                    model_profile=model_profile,
                )
            return dry_run
        if slug == "release-note-validation":
            validation = self._unwrap(
                await self.client.get_namespace_twin_release_note_validation(twin["twin_id"])
            )
            availability = validation.get("availability") or {}
            data = validation.get("data") or {}
            counts = data.get("claim_counts") or {}
            status = str(data.get("status") or "not_run")
            validation.update(
                {
                    "state": availability.get("state") or "not_run",
                    "kind": "release-note-validation",
                    "title": "Release Note Validation Twin",
                    "summary": availability.get("message")
                    or "Link a release-note artifact to validate operational claims.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "metrics": [
                        {
                            "label": "Supported",
                            "value": int(counts.get("supported") or 0),
                            "tone": "green",
                        },
                        {
                            "label": "Unsupported",
                            "value": int(counts.get("unsupported") or 0),
                            "tone": "amber",
                        },
                        {
                            "label": "Contradicted",
                            "value": int(counts.get("contradicted") or 0),
                            "tone": "red",
                        },
                        {
                            "label": "Missing",
                            "value": int(counts.get("missing") or 0),
                            "tone": "amber",
                        },
                    ],
                    "validation_status": status,
                }
            )
            return validation
        if slug == "runtime-behavior":
            runtime = self._unwrap(
                await self.client.get_namespace_twin_runtime_behavior(twin["twin_id"])
            )
            availability = runtime.get("availability") or {}
            data = runtime.get("data") or {}
            health = data.get("current_health") or {}
            risk = str(data.get("risk") or "unknown")
            runtime.update(
                {
                    "state": availability.get("state") or "not_available",
                    "kind": "runtime",
                    "title": "Runtime Behavior Twin",
                    "summary": availability.get("message")
                    or "Rules-first current runtime facts are unavailable.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "metrics": [
                        {
                            "label": "Runtime risk",
                            "value": risk,
                            "tone": (
                                "green"
                                if risk == "low"
                                else "amber"
                                if risk in {"medium", "unknown"}
                                else "red"
                            ),
                        },
                        {
                            "label": "Health",
                            "value": str(health.get("status") or "unknown"),
                            "tone": (
                                "green"
                                if health.get("status") == "healthy"
                                else "amber"
                                if health.get("status") in {"degraded", "unknown"}
                                else "red"
                            ),
                        },
                        {
                            "label": "Not ready",
                            "value": int(health.get("not_ready_pods") or 0),
                            "tone": "red" if int(health.get("not_ready_pods") or 0) else "green",
                        },
                        {
                            "label": "Event anomalies",
                            "value": int(health.get("event_anomalies") or 0),
                            "tone": "amber" if int(health.get("event_anomalies") or 0) else "green",
                        },
                    ],
                }
            )
            if data:
                runtime["safe_explanation"] = await self.runtime_behavior_explanation(
                    twin, runtime, model_profile=model_profile
                )
            return runtime
        if slug == "drift":
            drift = self._unwrap(await self.client.get_namespace_twin_drift(twin["twin_id"]))
            availability = drift.get("availability") or {}
            data = drift.get("data") or {}
            counts = data.get("change_counts") or {}
            status = str(data.get("status") or "unknown")
            drift.update(
                {
                    "state": availability.get("state") or "not_available",
                    "kind": "drift",
                    "title": "Drift Twin",
                    "summary": availability.get("message")
                    or "Deterministic drift facts are unavailable.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "metrics": [
                        {
                            "label": "Status",
                            "value": status,
                            "tone": (
                                "green"
                                if status == "none"
                                else "amber"
                                if status in {"minor", "unknown"}
                                else "red"
                            ),
                        },
                        {
                            "label": "Changed",
                            "value": int(counts.get("total") or 0),
                            "tone": "info",
                        },
                        {
                            "label": "Material",
                            "value": "Yes" if data.get("material") else "No",
                            "tone": "red" if data.get("material") else "green",
                        },
                        {
                            "label": "Execution",
                            "value": "Disabled" if data.get("execution_disabled") else "Eligible",
                            "tone": "red" if data.get("execution_disabled") else "green",
                        },
                    ],
                }
            )
            if data.get("material"):
                drift["safe_explanation"] = await self.drift_explanation(
                    twin, drift, model_profile=model_profile
                )
            return drift
        if slug == "rollback":
            rollback = self._unwrap(await self.client.get_namespace_twin_rollback(twin["twin_id"]))
            availability = rollback.get("availability") or {}
            data = rollback.get("data") or {}
            coverage = data.get("coverage") or {}
            rollback.update(
                {
                    "state": availability.get("state") or "not_available",
                    "kind": "rollback",
                    "title": "Rollback Twin",
                    "summary": availability.get("message")
                    or "Deterministic rollback facts are unavailable.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "metrics": [
                        {
                            "label": "Confidence",
                            "value": str(data.get("confidence") or "unavailable"),
                            "tone": (
                                "green"
                                if data.get("confidence") == "high"
                                else "amber"
                                if data.get("confidence") == "medium"
                                else "red"
                            ),
                        },
                        {
                            "label": "Plan coverage",
                            "value": f"{int(coverage.get('coverage_percent') or 0)}%",
                            "tone": (
                                "green"
                                if int(coverage.get("coverage_percent") or 0) == 100
                                else "amber"
                            ),
                        },
                        {
                            "label": "Defined",
                            "value": "Yes" if data.get("rollback_defined") else "No",
                            "tone": "green" if data.get("rollback_defined") else "red",
                        },
                        {
                            "label": "Proven",
                            "value": "Yes" if data.get("rollback_proven") else "No",
                            "tone": "green" if data.get("rollback_proven") else "amber",
                        },
                    ],
                }
            )
            if data:
                rollback["safe_explanation"] = await self.rollback_explanation(
                    twin, rollback, model_profile=model_profile
                )
            return rollback
        if slug == "audit":
            query = query or {}
            params = {
                key: query.get(key)
                for key in ("cursor", "limit")
                if query.get(key) not in (None, "")
            }
            audit = self._unwrap(
                await self.client.get_namespace_twin_audit(twin["twin_id"], params)
            )
            report_response = await self.client.get_namespace_twin_report(twin["twin_id"])
            report = report_response.payload
            if not isinstance(report, dict) or not report.get("report_hash"):
                raise DigitalTwinGatewayError(
                    502,
                    "invalid_execution_agent_report",
                    "Execution agent returned an invalid Namespace Twin report.",
                )
            audit.update(
                {
                    "state": "available",
                    "kind": "audit",
                    "title": "Audit Timeline and Reports",
                    "summary": (
                        "Append-only lifecycle and operator events with deterministic "
                        "JSON and Markdown reports."
                    ),
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "report": {
                        "report_id": report.get("report_id"),
                        "report_hash": report.get("report_hash"),
                        "generated_at": report.get("generated_at"),
                        "decision": (report.get("decision") or {}).get("value"),
                        "json_href": (f"/api/digital-twins/{twin['twin_id']}/reports/json"),
                        "markdown_href": (f"/api/digital-twins/{twin['twin_id']}/reports/markdown"),
                    },
                    "total_events": int((audit.get("page") or {}).get("result_count") or 0),
                    "applied_query": params,
                }
            )
            audit["safe_explanation"] = await self.audit_executive_summary(
                twin,
                report,
                model_profile=model_profile,
            )
            return audit
        if slug == "policy":
            query = query or {}
            params = {
                key: query.get(key)
                for key in ("severity", "category", "effect")
                if query.get(key) not in (None, "", "all")
            }
            policy = self._unwrap(
                await self.client.get_namespace_twin_policy(twin["twin_id"], params)
            )
            availability = policy.get("availability") or {}
            data = policy.get("data") or {}
            policy.update(
                {
                    "state": availability.get("state") or "not_available",
                    "kind": "findings",
                    "title": "Policy Twin",
                    "summary": availability.get("message")
                    or "Authoritative policy facts are unavailable.",
                    "data_mode": DATA_MODE,
                    "module_mode": "authoritative",
                    "non_authoritative": False,
                    "applied_query": params,
                    "findings": list(data.get("findings") or []),
                    "passed_groups": list(data.get("passed_groups") or []),
                }
            )
            policy["safe_explanation"] = await self.policy_explanation(
                twin, policy, model_profile=model_profile
            )
            return policy
        return await self._tab_phase4(twin, slug)

    async def audit_executive_summary(
        self,
        twin: dict[str, Any],
        report: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        """Summarize the immutable redacted report without changing its decision."""
        prompt_version = "namespace_twin_audit_executive_summary_v1"
        fact_envelope = {
            "report_id": report.get("report_id"),
            "report_hash": report.get("report_hash"),
            "twin": report.get("twin") or {},
            "decision": report.get("decision") or {},
            "versions": report.get("versions") or {},
            "hashes": report.get("hashes") or {},
            "evidence_summary": report.get("evidence_summary") or {},
            "event_count": len(report.get("timeline") or []),
            "recent_events": [
                {
                    "sequence": item.get("sequence"),
                    "phase": item.get("phase"),
                    "status": item.get("status"),
                    "event_type": item.get("event_type"),
                    "safe_summary": item.get("safe_summary"),
                }
                for item in (report.get("timeline") or [])[-20:]
            ],
            "safety": report.get("safety") or {},
            "model_authority": False,
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "Write a concise executive summary of an immutable Namespace Digital Twin "
            "audit report. Return JSON with only a summary string. Use only supplied "
            "redacted report facts. State the persisted decision, lifecycle, evidence "
            "coverage, notable phases, and operator follow-up. Never alter the decision, "
            "invent events, expose Secret values, include hidden chain-of-thought, or "
            "claim model authority."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        decision = str((report.get("decision") or {}).get("value") or "pending")
        evidence = report.get("evidence_summary") or {}
        deterministic_summary = (
            f"Audit report {report.get('report_id')} records "
            f"{len(report.get('timeline') or [])} append-only event(s). The persisted "
            f"decision is {decision}; policy is "
            f"{evidence.get('policy_verdict') or 'not available'}, dry-run is "
            f"{evidence.get('dry_run_status') or 'not run'}, and runtime risk is "
            f"{evidence.get('runtime_risk') or 'not available'}."
        )
        fallback = {
            "summary": deterministic_summary,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        summary = generated.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": summary.strip()[:12000],
            "evidence_refs": [str(report.get("report_id"))],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def safe_explanation(
        self,
        twin: dict[str, Any],
        overview: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        prompt_version = "namespace_twin_overview_explanation_v1"
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "lifecycle_status": twin["lifecycle_status"],
            "visible_lifecycle": twin["visible_lifecycle"],
            "decision": twin["decision"],
            "decision_is_final": twin["decision_is_final"],
            "risk": twin["risk"],
            "autonomy_eligibility": twin["autonomy_eligibility"],
            "freshness": twin["freshness"],
            "top_reasons": twin["top_reasons"],
            "recommended_next_step": twin["recommended_action"],
            "preliminary_summary": twin["preliminary_summary"],
            "final_summary": twin.get("final_summary"),
            "overview_facts": overview.get("fact_envelope") or {},
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "You produce one concise, audit-safe operator explanation from deterministic "
            "Namespace Digital Twin facts. Return JSON with only a summary string. Do not "
            "change, reinterpret, rank, or invent decisions, risk, freshness, evidence, "
            "recommended actions, or action eligibility. Never provide hidden chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        deterministic_summary = str(
            (overview.get("final_summary") or overview["preliminary_summary"])["headline"]
        )
        fallback = {
            "summary": deterministic_summary,
            "reasoning_summary": deterministic_summary,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        if self.llm is None:
            generated = fallback
        else:
            generated = await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        model_summary = generated.get("summary")
        if not isinstance(model_summary, str) or not model_summary.strip():
            model_summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        explanation_id = f"twinexp_{uuid4().hex}"
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": explanation_id,
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": model_summary.strip()[:12000],
            "evidence_refs": [],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def release_delta_explanation(
        self,
        twin: dict[str, Any],
        release_delta: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        prompt_version = "namespace_twin_release_delta_explanation_v1"
        data = release_delta.get("data") or {}
        summary = data.get("summary") or {}
        changes = data.get("changes") or []
        important_changes = [
            {
                "resource_identity": change.get("resource_identity"),
                "action": change.get("action"),
                "risk": change.get("risk"),
                "reason": change.get("reason"),
            }
            for change in changes
            if change.get("risk") in {"high", "critical"}
            or change.get("action") in {"immutable_conflict", "explicit_delete"}
        ][:12]
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "lifecycle_status": twin["lifecycle_status"],
            "target": twin["target"],
            "delta_summary": summary,
            "important_changes": important_changes,
            "applied_query": release_delta.get("applied_query") or {},
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "Explain deterministic Kubernetes Release Delta facts for an operator. Return JSON "
            "with only a concise summary string. Mention only counts, actions, risks, resource "
            "identities, and reasons supplied in the structured input. Do not infer deletion "
            "from omission, invent live state, change risk, recommend mutation, or reveal hidden "
            "chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        total = int(summary.get("total") or 0)
        deterministic_summary = (
            f"Release Delta contains {total} canonical fact(s): "
            f"{int(summary.get('create') or 0)} create, "
            f"{int(summary.get('update') or 0)} update, "
            f"{int(summary.get('explicit_delete') or 0)} explicit delete, "
            f"{int(summary.get('no_op') or 0)} no-op, "
            f"{int(summary.get('unknown') or 0)} unknown, and "
            f"{int(summary.get('immutable_conflict') or 0)} immutable conflict."
        )
        fallback = {
            "summary": deterministic_summary,
            "reasoning_summary": deterministic_summary,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        if self.llm is None:
            generated = fallback
        else:
            generated = await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        model_summary = generated.get("summary")
        if not isinstance(model_summary, str) or not model_summary.strip():
            model_summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        evidence_refs = sorted(
            {
                str(ref.get("evidence_id"))
                for change in changes
                for ref in (change.get("evidence_refs") or [])
                if isinstance(ref, dict) and ref.get("evidence_id")
            }
        )
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": model_summary.strip()[:12000],
            "evidence_refs": evidence_refs,
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def dependency_graph_explanation(
        self,
        twin: dict[str, Any],
        dependency_graph: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        prompt_version = "namespace_twin_dependency_graph_explanation_v1"
        data = dependency_graph.get("data") or {}
        summary = data.get("summary") or {}
        selected = data.get("selected_context") or {}

        def node_fact(node: dict[str, Any]) -> dict[str, Any]:
            return {
                "node_id": node.get("node_id"),
                "resource_identity": node.get("resource_identity"),
                "kind": node.get("kind"),
                "name": node.get("name"),
                "namespace": node.get("namespace"),
                "status": node.get("status"),
                "risk": node.get("risk"),
            }

        def edge_fact(edge: dict[str, Any]) -> dict[str, Any]:
            return {
                "edge_id": edge.get("edge_id"),
                "source": edge.get("source_label"),
                "target": edge.get("target_label"),
                "relationship": edge.get("relationship"),
                "status": edge.get("status"),
                "risk": edge.get("risk"),
                "confidence": edge.get("confidence"),
            }

        important_nodes = [
            node_fact(node)
            for node in data.get("nodes") or []
            if node.get("status") in {"missing", "uncertain"}
            or node.get("risk") in {"high", "critical"}
        ][:16]
        important_edges = [
            edge_fact(edge)
            for edge in data.get("table_rows") or []
            if edge.get("status") in {"missing", "uncertain"}
            or edge.get("risk") in {"high", "critical"}
        ][:20]
        selected_fact: dict[str, Any] = {"found": False}
        if isinstance(selected, dict) and selected.get("found"):
            selected_fact = {
                "found": True,
                "node": node_fact(selected.get("node") or {}),
                "inbound_edges": [
                    edge_fact(edge) for edge in (selected.get("inbound_edges") or [])[:20]
                ],
                "outbound_edges": [
                    edge_fact(edge) for edge in (selected.get("outbound_edges") or [])[:20]
                ],
                "impact_paths": [
                    {
                        "nodes": path.get("nodes"),
                        "relationships": path.get("relationships"),
                        "status": path.get("status"),
                        "risk": path.get("risk"),
                        "confidence": path.get("confidence"),
                    }
                    for path in (selected.get("impact_paths") or [])[:12]
                ],
            }
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "lifecycle_status": twin["lifecycle_status"],
            "target": twin["target"],
            "graph_summary": summary,
            "important_nodes": important_nodes,
            "important_edges": important_edges,
            "selected_context": selected_fact,
            "applied_query": dependency_graph.get("applied_query") or {},
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "Explain authoritative Kubernetes dependency graph facts for an operator. "
            "Return JSON with only a concise summary string. When selected_context.found "
            "is true, explain only the supplied inbound, outbound, and impact paths. "
            "Mention missing or uncertain dependencies and confidence exactly as supplied. "
            "Do not infer new nodes, edges, ordering, blast radius, live state, or mutation "
            "advice. Never reveal hidden chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        selected_node = selected_fact.get("node") or {}
        if selected_fact.get("found"):
            deterministic_summary = (
                f"Selected {selected_node.get('kind')}/{selected_node.get('name')} has "
                f"{len(selected_fact.get('inbound_edges') or [])} inbound edge(s), "
                f"{len(selected_fact.get('outbound_edges') or [])} outbound edge(s), and "
                f"{len(selected_fact.get('impact_paths') or [])} bounded impact path(s)."
            )
        else:
            deterministic_summary = (
                f"Dependency Graph contains {int(summary.get('nodes') or 0)} node(s) and "
                f"{int(summary.get('edges') or 0)} edge(s), including "
                f"{int(summary.get('missing_nodes') or summary.get('missing') or 0)} "
                "missing node(s), "
                f"{int(summary.get('uncertain_nodes') or summary.get('uncertain') or 0)} "
                "uncertain node(s), and "
                f"{int(summary.get('cycles') or 0)} detected cycle(s)."
            )
        fallback = {
            "summary": deterministic_summary,
            "reasoning_summary": deterministic_summary,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        if self.llm is None:
            generated = fallback
        else:
            generated = await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        model_summary = generated.get("summary")
        if not isinstance(model_summary, str) or not model_summary.strip():
            model_summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        evidence_refs = sorted(
            {
                str(ref.get("evidence_id") if isinstance(ref, dict) else ref)
                for collection in (
                    data.get("nodes") or [],
                    data.get("table_rows") or [],
                )
                for item in collection
                for ref in (item.get("evidence_refs") or [])
                if (isinstance(ref, dict) and ref.get("evidence_id"))
                or (isinstance(ref, str) and ref.strip())
            }
        )
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": model_summary.strip()[:12000],
            "evidence_refs": evidence_refs,
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def dry_run_explanation(
        self,
        twin: dict[str, Any],
        dry_run: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        """Explain supplied dry-run failures and safe next steps without taking action."""
        prompt_version = "namespace_twin_dry_run_explanation_v1"
        data = dry_run.get("data") or {}
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "decision": twin["decision"],
            "dry_run_job_id": data.get("dry_run_job_id"),
            "status": data.get("status"),
            "qualification_status": data.get("qualification_status"),
            "target_namespace": data.get("target_namespace"),
            "bundle_hash": data.get("bundle_hash"),
            "input_hash": data.get("input_hash"),
            "command_fingerprint_hash": data.get("command_fingerprint_hash"),
            "failed_steps": list(data.get("failed_steps") or []),
            "partial_steps": list(data.get("partial_steps") or []),
            "failed_validations": [
                item
                for item in (data.get("validations") or [])
                if item.get("status") in {"failed", "warning"}
            ][:20],
            "rejected_observations": [
                {
                    "phase": item.get("phase"),
                    "step": item.get("step"),
                    "tool": item.get("tool"),
                    "resource_identity": item.get("resource_identity"),
                    "outcome": item.get("outcome"),
                    "summary": item.get("summary"),
                }
                for item in (data.get("observations") or [])
                if item.get("outcome") in {"rejected", "warning"}
            ][:30],
            "fidelity_limitations": list(data.get("fidelity_limitations") or []),
            "model_authority": False,
            "automatic_instruction_submission": False,
            "automatic_mutation_retry": False,
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "Explain authoritative Namespace Digital Twin dry-run facts for an operator. "
            "Return JSON with only a concise summary string. For failures, explain the supplied "
            "rejections and safe investigation steps. For success, summarize accepted evidence "
            "and fidelity limits. Do not invent facts, submit instructions, trigger tools, retry "
            "dry-run or mutation, bypass approval, or expose hidden chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        rejected = len(fact_envelope["rejected_observations"])
        deterministic_summary = (
            f"Authoritative dry-run status is {fact_envelope['status']} for "
            f"{fact_envelope['target_namespace']}; {rejected} rejected or warning "
            "observation(s) require operator review. No instruction or mutation retry was "
            "submitted automatically."
        )
        fallback = {
            "summary": deterministic_summary,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        model_summary = generated.get("summary")
        if not isinstance(model_summary, str) or not model_summary.strip():
            model_summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": model_summary.strip()[:12000],
            "evidence_refs": list(data.get("evidence_refs") or [])[:100],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
            "automatic_instruction_submission": False,
            "automatic_mutation_retry": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def rollback_explanation(
        self,
        twin: dict[str, Any],
        rollback: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        """Explain deterministic rollback gaps without changing rollback authority."""
        prompt_version = "namespace_twin_rollback_explanation_v1"
        data = rollback.get("data") or {}
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "confidence": data.get("confidence"),
            "confidence_score": data.get("confidence_score"),
            "rollback_defined": data.get("rollback_defined"),
            "rollback_proven": data.get("rollback_proven"),
            "coverage": data.get("coverage") or {},
            "helm": data.get("helm") or {},
            "previous_artifacts": data.get("previous_artifacts") or {},
            "pvc_data_reversibility": data.get("pvc_data_reversibility"),
            "non_reversible_changes": [
                {
                    "code": item.get("code"),
                    "severity": item.get("severity"),
                    "status": item.get("status"),
                    "summary": item.get("summary"),
                }
                for item in (data.get("non_reversible_changes") or [])[:30]
            ],
            "gaps": list(data.get("gaps") or [])[:30],
            "proof": data.get("proof") or {},
            "manual_steps": list(data.get("manual_steps") or [])[:30],
            "validation_checks": list(data.get("validation_checks") or [])[:30],
            "model_authority": False,
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "Explain deterministic Namespace Digital Twin rollback facts for an operator. "
            "Return JSON with only a concise summary string. State confidence, whether "
            "rollback is defined and proven, plan coverage, previous Helm/artifact evidence, "
            "PVC/data reversibility, non-reversible changes, evidence gaps, manual review, "
            "and validation needs exactly as supplied. Never claim defined means proven, "
            "change confidence, execute rollback, submit instructions, bypass approval, or "
            "reveal hidden chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        coverage = fact_envelope["coverage"]
        deterministic_summary = (
            f"Rollback confidence is {fact_envelope['confidence']} with "
            f"{int(coverage.get('coverage_percent') or 0)}% plan coverage. Rollback is "
            f"{'defined' if fact_envelope['rollback_defined'] else 'not fully defined'} "
            f"and {'proven' if fact_envelope['rollback_proven'] else 'not proven'}; "
            f"{len(fact_envelope['gaps'])} evidence gap(s) require review."
        )
        fallback = {
            "summary": deterministic_summary,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        model_summary = generated.get("summary")
        if not isinstance(model_summary, str) or not model_summary.strip():
            model_summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": model_summary.strip()[:12000],
            "evidence_refs": [
                str(item.get("evidence_id"))
                for item in (data.get("evidence_refs") or [])
                if isinstance(item, dict) and item.get("evidence_id")
            ][:100],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def drift_explanation(
        self,
        twin: dict[str, Any],
        drift: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        """Explain deterministic changed-resource facts without classifying drift."""
        prompt_version = "namespace_twin_drift_explanation_v1"
        data = drift.get("data") or {}
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "status": data.get("status"),
            "material": data.get("material"),
            "execution_disabled": data.get("execution_disabled"),
            "decision_invalidated": data.get("decision_invalidated"),
            "rules_version": data.get("rules_version"),
            "freshness": data.get("freshness") or {},
            "change_counts": data.get("change_counts") or {},
            "helm_revision_drift": data.get("helm_revision_drift"),
            "manual_patch_indicators": list(data.get("manual_patch_indicators") or [])[:40],
            "health_changes": list(data.get("health_changes") or [])[:40],
            "changes": [
                {
                    "resource_identity": item.get("resource_identity"),
                    "change_type": item.get("change_type"),
                    "classification": item.get("classification"),
                    "summary": item.get("summary"),
                    "axes": item.get("axes") or {},
                }
                for item in (data.get("changes") or [])[:80]
            ],
            "model_authority": False,
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode()).hexdigest()
        system = (
            "Explain deterministic Namespace Drift Twin facts for an operator. Return JSON "
            "with only a concise summary string. Use only supplied changed-resource facts, "
            "classification, rule version, freshness, Helm, target, policy-boundary, safety, "
            "manual-patch, and health axes. Never reclassify drift, restore execution "
            "eligibility, mutate resources, bypass policy, or reveal hidden chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode()).hexdigest()
        deterministic_summary = str(data.get("summary") or "Drift facts are unavailable.")
        fallback = {
            "summary": deterministic_summary,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        summary = generated.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": summary.strip()[:12000],
            "evidence_refs": list(data.get("evidence_refs") or [])[:100],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def runtime_behavior_explanation(
        self,
        twin: dict[str, Any],
        runtime: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        """Explain deterministic runtime signals without changing decision authority."""
        prompt_version = "namespace_twin_runtime_behavior_explanation_v1"
        data = runtime.get("data") or {}
        health = data.get("current_health") or {}
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "runtime_risk": data.get("risk"),
            "risk_score": data.get("risk_score"),
            "confidence": data.get("confidence"),
            "current_health": health,
            "factors": [
                {
                    "factor_id": item.get("factor_id"),
                    "title": item.get("title"),
                    "impact": item.get("impact"),
                    "confidence": item.get("confidence"),
                    "summary": item.get("summary"),
                }
                for item in (data.get("factors") or [])[:40]
            ],
            "historical_context_status": data.get("historical_context_status"),
            "historical_context_message": data.get("historical_context_message"),
            "execution_effect": data.get("execution_effect"),
            "rules_version": data.get("rules_version"),
            "may_independently_approve": False,
            "model_authority": False,
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode()).hexdigest()
        system = (
            "Explain rules-first Namespace Runtime Behavior Twin facts for an operator. "
            "Return JSON with only a concise summary string. Use only the supplied current "
            "health, pod, event, pressure, confidence, and deterministic factor facts. "
            "State that historical comparison is Not Available when indicated. Never alter "
            "risk or health classification, approve execution, execute changes, invent "
            "history, bypass policy, or reveal hidden chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode()).hexdigest()
        deterministic_summary = str(
            data.get("summary") or "Rules-first runtime behavior facts are unavailable."
        )
        fallback = {
            "summary": deterministic_summary,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        summary = generated.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": summary.strip()[:12000],
            "evidence_refs": list(data.get("evidence_refs") or [])[:100],
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    async def policy_explanation(
        self,
        twin: dict[str, Any],
        policy: dict[str, Any],
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        """Explain immutable policy axes without granting the model decision authority."""
        prompt_version = "namespace_twin_policy_explanation_v1"
        data = policy.get("data") or {}
        fact_envelope = {
            "twin_id": twin["twin_id"],
            "decision_version": twin["decision_version"],
            "lifecycle_status": twin["lifecycle_status"],
            "policy_verdict": data.get("verdict"),
            "policy_version": data.get("policy_version"),
            "policy_bundle_hash": data.get("policy_bundle_hash"),
            "evidence_axis": data.get("evidence_axis") or {},
            "risk_axis": data.get("risk_axis") or {},
            "decision_projection": data.get("decision_projection") or {},
            "findings": [
                {
                    "code": item.get("code"),
                    "severity": item.get("severity"),
                    "status": item.get("status"),
                    "category": item.get("category"),
                    "summary": item.get("summary"),
                }
                for item in (data.get("findings") or [])[:50]
            ],
            "rule_contributions": [
                {
                    "axis": item.get("axis"),
                    "rule": item.get("rule"),
                    "matched": item.get("matched"),
                    "effect": item.get("effect"),
                    "contribution": item.get("contribution"),
                    "selected": item.get("selected"),
                    "reason": item.get("reason"),
                }
                for item in [
                    contribution
                    for contribution in (data.get("rule_contributions") or [])
                    if contribution.get("matched") or contribution.get("selected")
                ][:40]
            ],
            "model_authority": False,
        }
        canonical = json.dumps(fact_envelope, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        system = (
            "Explain deterministic Namespace Digital Twin policy facts for an operator. "
            "Return JSON with only a concise summary string. State the policy verdict, "
            "evidence completeness/freshness, risk score/level, and preliminary decision "
            "exactly as supplied. Explain the selected precedence rule and important matched "
            "contributions. Never change, rank, override, upgrade, or downgrade any axis, "
            "score, finding, or decision. Never recommend bypassing policy or reveal hidden "
            "chain-of-thought."
        )
        prompt_hash = hashlib.sha256((system + canonical).encode("utf-8")).hexdigest()
        evidence_axis = fact_envelope["evidence_axis"]
        risk_axis = fact_envelope["risk_axis"]
        projection = fact_envelope["decision_projection"]
        deterministic_summary = (
            f"Policy verdict is {fact_envelope['policy_verdict']}; evidence is "
            f"{evidence_axis.get('completeness', 'unknown')} and "
            f"{evidence_axis.get('freshness', 'unknown')}; deterministic change risk is "
            f"{risk_axis.get('level', 'unknown')} ({risk_axis.get('score', 'n/a')}); "
            f"the preliminary projection is {projection.get('label', 'Unknown')} under "
            f"precedence rule {projection.get('precedence_rule', 'not_available')}."
        )
        fallback = {
            "summary": deterministic_summary,
            "model_profile": model_profile or "azure_gpt5_pro",
            "llm_fallback": {"used": True, "error": None},
        }
        started = time.perf_counter()
        generated = (
            fallback
            if self.llm is None
            else await self.llm.structured_response(
                system=system,
                user_payload=fact_envelope,
                fallback=fallback,
                model_profile=model_profile or "azure_gpt5_pro",
            )
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        model_summary = generated.get("summary")
        if not isinstance(model_summary, str) or not model_summary.strip():
            model_summary = deterministic_summary
        token_usage = generated.get("token_usage") or {}
        fallback_used = bool((generated.get("llm_fallback") or {}).get("used"))
        evidence_refs = sorted(
            {
                str(reference.get("evidence_id"))
                for finding in (data.get("findings") or [])
                for reference in (finding.get("evidence_refs") or [])
                if isinstance(reference, dict) and reference.get("evidence_id")
            }
        )
        safe_output = {
            "schema_version": "1.0.0",
            "explanation_id": f"twinexp_{uuid4().hex}",
            "status": "fallback" if fallback_used else "generated",
            "model_profile": generated.get("model_profile") or model_profile or "azure_gpt5_pro",
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "plain_text",
            "content": model_summary.strip()[:12000],
            "evidence_refs": evidence_refs,
            "fallback_reason": (
                str((generated.get("llm_fallback") or {}).get("error"))[:1000]
                if (generated.get("llm_fallback") or {}).get("error")
                else None
            ),
            "latency_ms": latency_ms,
            "input_tokens": token_usage.get("input_tokens"),
            "output_tokens": token_usage.get("output_tokens"),
            "chain_of_thought_included": False,
            "model_authority": False,
        }
        self._log_explanation(
            twin=twin,
            safe_output=safe_output,
            token_usage=token_usage,
            error_message=(generated.get("llm_fallback") or {}).get("error"),
        )
        return safe_output

    def _log_explanation(
        self,
        *,
        twin: dict[str, Any],
        safe_output: dict[str, Any],
        token_usage: dict[str, Any],
        error_message: str | None,
    ) -> None:
        if self.database is None:
            return
        try:
            with self.database.session() as session:
                session.add(
                    DigitalTwinExplanationLog(
                        explanation_id=safe_output["explanation_id"],
                        twin_id=twin["twin_id"],
                        decision_version=int(twin["decision_version"]),
                        prompt_version=safe_output["prompt_version"],
                        prompt_hash=safe_output["prompt_hash"],
                        model_profile=safe_output["model_profile"],
                        input_hash=safe_output["input_hash"],
                        latency_ms=int(safe_output["latency_ms"]),
                        token_usage_json=deepcopy(token_usage),
                        safe_output_json=deepcopy(safe_output),
                        fallback_used=safe_output["status"] == "fallback",
                        error_message=str(error_message)[:1000] if error_message else None,
                    )
                )
        except Exception:
            # Explanation logging must not hide otherwise valid deterministic twin facts.
            return

    @staticmethod
    def _unwrap(response: MopExecutionAgentResponse) -> dict[str, Any]:
        payload = response.payload
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise DigitalTwinGatewayError(
                502,
                "invalid_execution_agent_response",
                "Execution agent returned an invalid envelope.",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DigitalTwinGatewayError(
                502, "invalid_execution_agent_response", "Execution agent response data is missing."
            )
        return data


_RELEASE_NOTE_CATEGORIES = {
    "image", "configuration", "migration", "pvc_storage", "rbac", "route",
    "rollback", "breaking_change", "known_risk", "other",
}
_RELEASE_NOTE_SECRET = re.compile(
    r"(?i)\b(password|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*\S+"
)
_RELEASE_NOTE_INSTRUCTION = re.compile(
    r"(?i)(?:\b(?:ignore|disregard|override|bypass)\b.{0,120}"
    r"\b(?:instruction|rule|validation|policy)\b|"
    r"\b(?:classify|mark)\b.{0,80}\b(?:all|every)\b.{0,80}"
    r"\b(?:supported|passed|valid)\b)"
)


def _sanitize_release_note_claims(value: Any) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(value, list):
        return claims
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "other").strip().lower()
        if category not in _RELEASE_NOTE_CATEGORIES:
            category = "other"
        claim = " ".join(str(item.get("claim") or "").split())[:4000]
        if (
            not claim
            or "-----BEGIN " in claim.upper()
            or _RELEASE_NOTE_SECRET.search(claim)
            or _RELEASE_NOTE_INSTRUCTION.search(claim)
        ):
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", claim.lower()).strip()
        key = (category, normalized)
        if normalized and key not in seen:
            seen.add(key)
            claims.append({"category": category, "claim": claim})
    return claims


def _release_note_fallback_claims(content: str) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []
    category = "other"
    in_fence = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line:
            continue
        if line.startswith("#"):
            category = _release_note_category(line.lstrip("# "))
            continue
        candidate = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", line).strip()
        if len(candidate) < 4:
            continue
        inferred_category = _release_note_category(candidate)
        claims.append({
            "category": category if category != "other" else inferred_category,
            "claim": candidate,
        })
        if len(claims) >= 100:
            break
    return _sanitize_release_note_claims(claims)


def _release_note_category(value: str) -> str:
    text = value.lower()
    if any(token in text for token in ("image", "container", "version", "tag")):
        return "image"
    if any(token in text for token in ("config", "setting", "value", "environment")):
        return "configuration"
    if "migrat" in text:
        return "migration"
    if any(token in text for token in ("pvc", "storage", "volume", "persistent")):
        return "pvc_storage"
    if any(token in text for token in ("rbac", "role", "permission", "service account")):
        return "rbac"
    if any(token in text for token in ("route", "ingress", "service", "endpoint")):
        return "route"
    if "rollback" in text or "recovery" in text:
        return "rollback"
    if "breaking" in text or "incompatible" in text:
        return "breaking_change"
    if any(token in text for token in ("risk", "known issue", "warning")):
        return "known_risk"
    return "other"

def _response_headers(response: Response) -> None:
    response.headers["X-ESDA-Data-Mode"] = DATA_MODE
    response.headers["Cache-Control"] = "no-store"


def build_digital_twin_gateway_router(service: DigitalTwinGatewayService) -> APIRouter:
    router = APIRouter(
        prefix="/api/digital-twins",
        tags=["digital twins"],
        dependencies=[Depends(get_current_user)],
    )

    @router.get("/config")
    async def config(response: Response) -> dict[str, Any]:
        _response_headers(response)
        return {
            "schema_version": "1.0.0",
            "data_mode": DATA_MODE,
            "module_mode": "mixed_authoritative_overview",
            "enabled": True,
            "non_production": False,
            "label": (
                "Real Lifecycle + Overview + Release Delta + Dependency Graph + Policy Twin + "
                "Dry-run / Diff Twin + Rollback Twin + Release Note Validation Twin + Audit Reports + Mock Remaining Modules"
            ),
        }

    @router.get("/scenarios")
    async def scenarios(response: Response) -> list[dict[str, Any]]:
        _response_headers(response)
        return [
            {**item, "module_mode": MODULE_MODE, "non_authoritative": True}
            for item in deepcopy(service.fixtures.scenarios)
        ]

    @router.get("")
    async def list_twins(request: Request, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.list(dict(request.query_params))

    @router.post("")
    async def create_twin(payload: RealTwinCreateRequest, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.create(payload.model_dump(mode="json"))

    @router.post("/{twin_id}/dry-run-evidence")
    async def attach_dry_run_evidence(
        twin_id: str,
        payload: RealTwinDryRunEvidenceRequest,
        response: Response,
    ) -> dict[str, Any]:
        _response_headers(response)
        return await service.attach_dry_run_evidence(
            twin_id,
            payload.model_dump(mode="json", exclude_none=True),
        )

    @router.get("/active")
    async def active_twin(response: Response) -> dict[str, Any] | None:
        _response_headers(response)
        result = await service.list({"limit": 100})
        return next(
            (item for item in result["items"] if item["lifecycle_status"] in ACTIVE_STATES),
            None,
        )

    @router.get("/history")
    async def history(response: Response) -> list[dict[str, Any]]:
        _response_headers(response)
        return (await service.list({"limit": 100}))["items"]

    @router.delete("/history")
    async def clear_history(response: Response) -> dict[str, Any]:
        _response_headers(response)
        raise DigitalTwinGatewayError(
            405,
            "durable_history_not_clearable",
            "Real twin audit history cannot be cleared from the presentation gateway.",
        )

    @router.get("/{twin_id}")
    async def get_twin(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.get(twin_id)

    @router.get("/{twin_id}/actions")
    async def actions(twin_id: str, response: Response) -> list[dict[str, Any]]:
        _response_headers(response)
        return (await service.get(twin_id))["actions"]

    @router.get("/{twin_id}/tabs/{slug}")
    async def tab(
        twin_id: str,
        slug: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        _response_headers(response)
        return await service.tab(
            await service.get(twin_id),
            slug,
            model_profile=request.query_params.get("model_profile"),
            query=dict(request.query_params),
        )

    @router.post("/{twin_id}/advance")
    async def advance(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        raise DigitalTwinGatewayError(
            409,
            "authoritative_dry_run_required",
            "Real lifecycle cannot be advanced by the browser. Await authoritative evidence.",
        )

    @router.post("/{twin_id}/regenerate")
    async def regenerate(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        raise DigitalTwinGatewayError(
            409,
            "bundle_source_required",
            "Regeneration requires a new idempotency key and the original bundle source.",
        )

    @router.post("/{twin_id}/cancel")
    async def cancel(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.cancel(twin_id)

    @router.post("/{twin_id}/drift/refresh")
    async def refresh_drift(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.refresh_drift(twin_id)

    @router.post("/{twin_id}/runtime-behavior/refresh")
    async def refresh_runtime_behavior(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.refresh_runtime_behavior(twin_id)

    @router.post("/{twin_id}/release-note-validation")
    async def validate_release_note(
        twin_id: str,
        payload: RealTwinReleaseNoteValidationRequest,
        response: Response,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict[str, Any]:
        _response_headers(response)
        return await service.validate_release_note(
            twin_id,
            payload.model_dump(mode="json"),
            actor_id=principal.username,
        )
    async def _report_response(twin_id: str, report_format: str) -> Response:
        content, content_type, filename = await service.client.download_namespace_twin_report(
            twin_id, report_format
        )
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Cache-Control": "no-store",
                "X-ESDA-Data-Mode": DATA_MODE,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @router.get("/{twin_id}/reports/json")
    async def json_report(twin_id: str) -> Response:
        return await _report_response(twin_id, "json")

    @router.get("/{twin_id}/reports/markdown")
    async def markdown_report(twin_id: str) -> Response:
        return await _report_response(twin_id, "markdown")

    @router.get("/{twin_id}/report")
    async def legacy_json_report(twin_id: str) -> Response:
        return await _report_response(twin_id, "json")

    @router.get("/{twin_id}/gate")
    async def gate(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        twin = await service.get(twin_id)
        policy = service._unwrap(await service.client.get_namespace_twin_policy(twin_id, None))
        policy_data = policy.get("data") or {}
        dry_run = service._unwrap(await service.client.get_namespace_twin_dry_run(twin_id, None))
        dry_run_data = dry_run.get("data") or {}
        dry_run_availability = dry_run.get("availability") or {}
        rollback = service._unwrap(await service.client.get_namespace_twin_rollback(twin_id))
        rollback_data = rollback.get("data") or {}
        drift = service._unwrap(await service.client.get_namespace_twin_drift(twin_id))
        drift_data = drift.get("data") or {}
        return {
            "schema_version": "1.0.0",
            "data_mode": DATA_MODE,
            "module_mode": "mixed_authoritative_policy_and_dry_run",
            "twin": twin,
            "decision": twin["decision"],
            "risk": policy_data.get("risk_axis") or twin["risk"],
            "policy": policy_data.get("verdict") or "not_available",
            "evidence": policy_data.get("evidence_axis") or {"classification": "unavailable"},
            "decision_projection": policy_data.get("decision_projection"),
            "freshness": twin["freshness"],
            "dry_run": dry_run_data.get("qualification_status")
            or dry_run_data.get("status")
            or dry_run_availability.get("state")
            or "not_available",
            "rollback": rollback_data.get("confidence") or "not_available",
            "drift": drift_data.get("status") or "not_available",
            "reasons": twin["top_reasons"],
            "approval": (
                "required"
                if policy_data.get("verdict") == "allow_with_approval"
                else "not_required"
            ),
            "model_authority": False,
        }

    for endpoint, slug in TAB_ENDPOINTS.items():

        async def named_tab(twin_id: str, response: Response, _slug: str = slug) -> dict[str, Any]:
            _response_headers(response)
            return await service.tab(await service.get(twin_id), _slug)

        router.add_api_route(f"/{{twin_id}}/{endpoint}", named_tab, methods=["GET"])

    @router.get("/{twin_id}/events")
    async def events(
        twin_id: str,
        response: Response,
        cursor: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, Any]:
        _response_headers(response)
        offset = int(str(cursor or "cursor_0").removeprefix("cursor_") or 0)
        return await service.events(twin_id, limit=limit, offset=offset)

    @router.get("/{twin_id}/safe-explanation")
    async def safe_explanation(
        twin_id: str,
        response: Response,
        model_profile: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _response_headers(response)
        twin = await service.get(twin_id)
        overview = service._unwrap(await service.client.get_namespace_twin_overview(twin_id))
        return await service.safe_explanation(twin, overview, model_profile=model_profile)

    return router


def install_digital_twin_gateway(
    app: FastAPI,
    *,
    settings: Settings,
    client: MopExecutionAgentClient | None = None,
    llm: AzureGpt5Service | None = None,
    database: Database | None = None,
) -> DigitalTwinGatewayService:
    service = DigitalTwinGatewayService(settings, client=client, llm=llm, database=database)
    app.state.digital_twin_gateway = service
    app.include_router(build_digital_twin_gateway_router(service))

    @app.exception_handler(DigitalTwinGatewayError)
    async def gateway_error_handler(request: Request, exc: DigitalTwinGatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-ESDA-Data-Mode": DATA_MODE, "Cache-Control": "no-store"},
            content={
                "data_mode": DATA_MODE,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": False,
                    "request_id": getattr(request.state, "request_id", None),
                    "details": exc.details,
                },
            },
        )

    @app.exception_handler(MopExecutionAgentError)
    async def execution_agent_error_handler(
        request: Request, exc: MopExecutionAgentError
    ) -> JSONResponse:
        status_code = exc.status_code if exc.status_code and exc.status_code < 500 else 502
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        error = payload.get("error") if isinstance(payload, dict) else {}
        return JSONResponse(
            status_code=status_code,
            headers={"X-ESDA-Data-Mode": DATA_MODE, "Cache-Control": "no-store"},
            content={
                "data_mode": DATA_MODE,
                "error": {
                    "code": (error.get("code") if isinstance(error, dict) else None)
                    or "execution_agent_unavailable",
                    "message": (error.get("message") if isinstance(error, dict) else None)
                    or "The namespace twin execution-agent core is unavailable.",
                    "retryable": status_code >= 500,
                    "request_id": getattr(request.state, "request_id", None),
                    "details": {},
                },
            },
        )

    return service
