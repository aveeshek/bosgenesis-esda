from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4
from copy import deepcopy
from datetime import UTC, datetime

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
                    "release-delta",
                    "dependency-graph",
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
                "Lifecycle, Overview, Release Delta, summaries, freshness, and actions are "
                "authoritative. Remaining evidence modules are mock and non-authoritative."
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
        return await self._tab_phase4(twin, slug)

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
            "model_profile": generated.get("model_profile")
            or model_profile
            or "azure_gpt5_pro",
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
            "label": "Real Lifecycle + Overview + Release Delta + Mock Remaining Modules",
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

    @router.get("/{twin_id}/gate")
    async def gate(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        twin = await service.get(twin_id)
        return {
            "schema_version": "1.0.0",
            "data_mode": DATA_MODE,
            "module_mode": MODULE_MODE,
            "twin": twin,
            "decision": twin["decision"],
            "risk": twin["risk"],
            "policy": "not_available",
            "evidence": "collecting",
            "freshness": twin["freshness"],
            "dry_run": "awaiting_authoritative_dry_run",
            "rollback": "not_available",
            "drift": "not_available",
            "reasons": twin["top_reasons"],
            "approval": "not_available",
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
