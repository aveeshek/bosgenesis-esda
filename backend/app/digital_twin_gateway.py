from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
    ) -> None:
        base_url = settings.digital_twin_execution_agent_url or None
        self.client = client or MopExecutionAgentClient(settings, base_url_override=base_url)
        self.fixtures = DigitalTwinMockService(enabled=True, default_delay_ms=0).repository

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

    async def list(self, query: dict[str, Any]) -> dict[str, Any]:
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
            await self.client.get_namespace_twin_events(
                twin_id, {"limit": limit, "offset": offset}
            )
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
            "next_cursor": (
                f"cursor_{offset + len(events)}" if page.get("has_more") else None
            ),
            "has_more": bool(page.get("has_more")),
        }

    async def tab(self, twin: dict[str, Any], slug: str) -> dict[str, Any]:
        fixture_twin = deepcopy(self.fixtures.seed_twins[0])
        fixture_twin["twin_id"] = twin["twin_id"]
        fixture_twin["scenario_id"] = "green-helm"
        tab = self.fixtures.tab(fixture_twin, slug)
        tab["data_mode"] = "mock_module"
        tab["module_mode"] = MODULE_MODE
        tab["non_authoritative"] = True
        tab["summary"] = (
            "Mock / non-authoritative module preview. This data cannot change the real lifecycle "
            "or action eligibility. "
            + str(tab.get("summary") or "")
        )
        return tab

    @staticmethod
    def project(core: dict[str, Any]) -> dict[str, Any]:
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
    def _unwrap(response: MopExecutionAgentResponse) -> dict[str, Any]:
        payload = response.payload
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise DigitalTwinGatewayError(
                502, "invalid_execution_agent_response", "Execution agent returned an invalid envelope."
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
            "module_mode": MODULE_MODE,
            "enabled": True,
            "non_production": False,
            "label": "Real Core + Mock Modules",
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
    async def tab(twin_id: str, slug: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        return await service.tab(await service.get(twin_id), slug)

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
        async def named_tab(
            twin_id: str, response: Response, _slug: str = slug
        ) -> dict[str, Any]:
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
    async def safe_explanation(twin_id: str, response: Response) -> dict[str, Any]:
        _response_headers(response)
        twin = await service.get(twin_id)
        return {
            "schema_version": "1.0.0",
            "data_mode": DATA_MODE,
            "twin_id": twin_id,
            "decision_version": twin["decision_version"],
            "summary": twin["recommended_action"],
            "factors": twin["top_reasons"],
            "limitations": [
                "Phase 4 has no final decision.",
                "Evidence tabs are mock and non-authoritative.",
            ],
            "generated_by": "deterministic_real_core_projection",
        }

    return router


def install_digital_twin_gateway(
    app: FastAPI,
    *,
    settings: Settings,
    client: MopExecutionAgentClient | None = None,
) -> DigitalTwinGatewayService:
    service = DigitalTwinGatewayService(settings, client=client)
    app.state.digital_twin_gateway = service
    app.include_router(build_digital_twin_gateway_router(service))

    @app.exception_handler(DigitalTwinGatewayError)
    async def gateway_error_handler(
        request: Request, exc: DigitalTwinGatewayError
    ) -> JSONResponse:
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
                    "code": (
                        error.get("code") if isinstance(error, dict) else None
                    )
                    or "execution_agent_unavailable",
                    "message": (
                        error.get("message") if isinstance(error, dict) else None
                    )
                    or "The namespace twin execution-agent core is unavailable.",
                    "retryable": status_code >= 500,
                    "request_id": getattr(request.state, "request_id", None),
                    "details": {},
                },
            },
        )

    return service
