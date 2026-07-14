from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.dependencies import get_current_user


DATA_MODE = "mock_server"
SCHEMA_VERSION = "1.0.0"
ACTIVE_STATES = {"requested", "generating", "awaiting_dry_run", "decision_calculating"}
TERMINAL_STATES = {"completed", "green", "amber", "red", "failed", "cancelled", "superseded"}
TAB_ENDPOINTS = {
    "summary": "overview",
    "delta": "release-delta",
    "graph": "dependency-graph",
    "policy": "policy",
    "dry-run": "dry-run",
    "rollback": "rollback",
    "drift": "drift",
    "replay": "mop-replay",
    "runtime-risk": "runtime-behavior",
    "release-note-validation": "release-note-validation",
    "audit": "audit",
}


class DigitalTwinMockError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class TwinGenerationRequest(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=100)


class TwinApprovalRequest(BaseModel):
    notes: str = Field(default="", max_length=1000)


class DigitalTwinMockRepository:
    """Thread-safe fixture repository with the same boundary as the future gateway."""

    def __init__(self, fixture_path: Path) -> None:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.version = str(payload["version"])
        self.generated_at = str(payload["generated_at"])
        self.scenarios = payload["scenarios"]
        self.seed_twins = payload["twins"]
        self.tab_slugs = payload["tab_slugs"]
        self.progress_states = payload["progress_states"]
        self.response_modes = payload["response_modes"]
        self.scenario_tabs = payload["scenario_tabs"]
        self._lock = RLock()
        self._twins: dict[str, dict[str, Any]] = {}
        self._dynamic_ids: set[str] = set()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._twins = {item["twin_id"]: deepcopy(item) for item in self.seed_twins}
            self._dynamic_ids.clear()

    def all_twins(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._twins.values()))

    def dynamic_twins(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy([self._twins[item_id] for item_id in self._dynamic_ids if item_id in self._twins])

    def get(self, twin_id: str) -> dict[str, Any]:
        with self._lock:
            twin = self._twins.get(twin_id)
            if not twin:
                raise DigitalTwinMockError(404, "twin_not_found", f"Twin {twin_id} was not found.")
            return deepcopy(twin)

    def save(self, twin: dict[str, Any], *, dynamic: bool | None = None) -> dict[str, Any]:
        with self._lock:
            self._twins[twin["twin_id"]] = deepcopy(twin)
            if dynamic is True or twin["twin_id"].startswith("twin_mock_"):
                self._dynamic_ids.add(twin["twin_id"])
            return deepcopy(twin)

    def tab(self, twin: dict[str, Any], slug: str) -> dict[str, Any]:
        if slug not in self.tab_slugs:
            raise DigitalTwinMockError(404, "tab_not_found", f"Digital Twin tab {slug} was not found.")
        scenario_id = twin.get("scenario_id") or self.seed_twins[0]["scenario_id"]
        scenario_tabs = self.scenario_tabs.get(scenario_id) or self.scenario_tabs[self.seed_twins[0]["scenario_id"]]
        tab = deepcopy(scenario_tabs[slug])
        if twin.get("lifecycle_status") in ACTIVE_STATES:
            available_count = min(len(self.tab_slugs), max(1, int(twin.get("progress_index") or 0) + 1))
            if self.tab_slugs.index(slug) >= available_count:
                tab["state"] = "loading"
                tab["summary"] = "This evidence module is waiting for an earlier generation stage."
        return tab


class DigitalTwinMockService:
    def __init__(self, *, enabled: bool, default_delay_ms: int = 120) -> None:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "digital_twin"
            / "v1"
            / "server-fixtures.json"
        )
        self.enabled = enabled
        self.default_delay_ms = max(0, min(default_delay_ms, 3000))
        self.repository = DigitalTwinMockRepository(fixture_path)

    def ensure_enabled(self) -> None:
        if not self.enabled:
            raise DigitalTwinMockError(
                404,
                "mock_gateway_disabled",
                "The Digital Twin mock gateway is disabled for this environment.",
            )

    async def simulate(self, *, state: str = "success", delay_ms: int | None = None) -> None:
        self.ensure_enabled()
        delay = self.default_delay_ms if delay_ms is None else max(0, min(delay_ms, 10_000))
        await asyncio.sleep(delay / 1000)
        if state == "timeout":
            raise DigitalTwinMockError(504, "mock_timeout", "The mock gateway timed out.", retryable=True)
        if state == "failed":
            raise DigitalTwinMockError(
                503,
                "mock_service_unavailable",
                "The mock gateway injected a retryable failure.",
                retryable=True,
            )

    def list_twins(self, query: dict[str, Any]) -> dict[str, Any]:
        state = str(query.get("mock_state") or "success")
        items = self.repository.all_twins()
        if state == "empty":
            items = []

        search = str(query.get("q") or query.get("search") or "").strip().lower()
        if search:
            items = [
                item
                for item in items
                if search
                in " ".join(
                    str(value)
                    for value in (
                        item.get("twin_id"),
                        item.get("display_name"),
                        item.get("target", {}).get("cluster_name"),
                        item.get("target", {}).get("namespace"),
                        item.get("bundle", {}).get("bundle_name"),
                        item.get("bundle", {}).get("release_version"),
                        item.get("created_by_display"),
                    )
                ).lower()
            ]

        def matches(value: Any, expected: Any) -> bool:
            return expected in (None, "", "all") or str(value) == str(expected)

        filtered: list[dict[str, Any]] = []
        for item in items:
            linked = "linked" if item.get("relationships", {}).get("execution_status") != "unlinked" else "unlinked"
            created_day = str(item.get("created_at") or "")[:10]
            if not matches(item.get("decision"), query.get("decision")):
                continue
            if not matches(item.get("lifecycle_status"), query.get("lifecycle")):
                continue
            if not matches(item.get("freshness", {}).get("status"), query.get("freshness")):
                continue
            if not matches(item.get("target", {}).get("cluster_name"), query.get("cluster")):
                continue
            if not matches(item.get("target", {}).get("namespace"), query.get("namespace") or query.get("target")):
                continue
            bundle = query.get("bundle")
            if bundle not in (None, "", "all") and str(bundle) not in str(item.get("bundle", {}).get("bundle_name") or ""):
                continue
            if not matches(item.get("created_by_display"), query.get("created_by") or query.get("creator")):
                continue
            if not matches(created_day, query.get("date")):
                continue
            if not matches(linked, query.get("linked_execution")):
                continue
            created_from = str(query.get("created_from") or "")
            created_to = str(query.get("created_to") or "")
            if created_from and created_day < created_from:
                continue
            if created_to and created_day > created_to:
                continue
            filtered.append(item)

        sort = str(query.get("sort") or "created_at")
        reverse = str(query.get("direction") or "desc") != "asc"

        def sort_value(item: dict[str, Any]) -> Any:
            if sort == "risk":
                return item.get("risk", {}).get("score") if item.get("risk", {}).get("score") is not None else -1
            if sort == "decision":
                return item.get("decision") or ""
            return item.get(sort) or item.get("created_at") or ""

        filtered.sort(key=sort_value, reverse=reverse)
        if state == "stale":
            for item in filtered:
                item["freshness"]["status"] = "stale"
                item["freshness"]["message"] = "The server fixture response was forced into a stale state."

        metrics = {"total": len(filtered), "green": 0, "amber": 0, "red": 0, "generating": 0, "stale": 0, "linked": 0}
        for item in filtered:
            decision = item.get("decision")
            if decision in {"green", "amber", "red"}:
                metrics[decision] += 1
            if item.get("lifecycle_status") in ACTIVE_STATES:
                metrics["generating"] += 1
            if item.get("freshness", {}).get("status") in {"stale", "drifted", "expired"}:
                metrics["stale"] += 1
            if item.get("relationships", {}).get("execution_status") != "unlinked":
                metrics["linked"] += 1

        try:
            limit = int(query.get("limit") or 25)
        except (TypeError, ValueError) as exc:
            raise DigitalTwinMockError(422, "invalid_limit", "limit must be an integer.") from exc
        if not 1 <= limit <= 100:
            raise DigitalTwinMockError(422, "invalid_limit", "limit must be between 1 and 100.")
        cursor = str(query.get("cursor") or "")
        try:
            offset = int(cursor.removeprefix("cursor_")) if cursor else 0
        except ValueError as exc:
            raise DigitalTwinMockError(422, "invalid_cursor", "cursor is not valid.") from exc
        if offset < 0:
            raise DigitalTwinMockError(422, "invalid_cursor", "cursor is not valid.")
        page_items = filtered[offset : offset + limit]
        next_offset = offset + len(page_items)
        if state == "partial" and page_items:
            page_items = page_items[: max(1, len(page_items) - 1)]
        return {
            "schema_version": self.repository.version,
            "generated_at": self.repository.generated_at,
            "items": page_items,
            "metrics": metrics,
            "page": {
                "limit": limit,
                "has_more": next_offset < len(filtered),
                "next_cursor": f"cursor_{next_offset}" if next_offset < len(filtered) else None,
                "previous_cursor": f"cursor_{max(0, offset - limit)}" if offset > 0 else None,
                "result_count": len(filtered),
                "offset": offset,
            },
            "applied_query": deepcopy(query),
            "partial": state == "partial",
            "warning": "Runtime-behavior enrichment is unavailable; core decision rows are complete." if state == "partial" else None,
        }

    def generate(self, scenario_id: str) -> dict[str, Any]:
        source = next((item for item in self.repository.seed_twins if item.get("scenario_id") == scenario_id), None)
        if not source:
            raise DigitalTwinMockError(422, "unknown_scenario", f"Scenario {scenario_id} is not available.")
        twin = deepcopy(source)
        now = datetime.now(UTC).isoformat()
        twin["twin_id"] = f"twin_mock_{uuid4().hex}"
        twin["display_name"] = f"Mock generation - {source['display_name']}"
        twin["decision_version"] = 1
        twin["decision"] = "pending"
        twin["decision_is_final"] = False
        twin["lifecycle_status"] = "requested"
        twin["visible_lifecycle"] = "requested"
        twin["risk"] = {"level": "preliminary", "score": None}
        twin["autonomy_eligibility"] = "not_available"
        twin["recommended_action"] = "Generation was requested. Final eligibility is not available."
        twin["progress_index"] = 0
        twin["created_at"] = now
        twin["updated_at"] = now
        twin["actions"] = [
            self._action("open_twin", "Open Twin"),
            self._action("cancel_generation", "Cancel Generation", confirmation=True),
        ]
        return self.repository.save(twin, dynamic=True)

    def advance(self, twin_id: str) -> dict[str, Any]:
        twin = self.repository.get(twin_id)
        if twin.get("lifecycle_status") not in ACTIVE_STATES:
            return twin
        index = min(len(self.repository.progress_states) - 1, int(twin.get("progress_index") or 0) + 1)
        state = self.repository.progress_states[index]
        twin["progress_index"] = index
        twin["lifecycle_status"] = state
        twin["visible_lifecycle"] = state
        twin["updated_at"] = datetime.now(UTC).isoformat()
        if state == "green":
            source = self.repository.seed_twins[0]
            twin["decision"] = "green"
            twin["decision_is_final"] = True
            twin["risk"] = {"level": "low", "score": 18}
            twin["autonomy_eligibility"] = "eligible"
            twin["recommended_action"] = "Proceed through normal execution controls."
            twin["actions"] = deepcopy(source["actions"])
        return self.repository.save(twin)

    def regenerate(self, twin_id: str) -> dict[str, Any]:
        prior = self.repository.get(twin_id)
        superseded = deepcopy(prior)
        superseded["decision"] = "superseded"
        superseded["lifecycle_status"] = "superseded"
        superseded["visible_lifecycle"] = "superseded"
        superseded["autonomy_eligibility"] = "superseded"
        superseded["freshness"]["status"] = "expired"
        next_twin = deepcopy(prior)
        next_twin["twin_id"] = f"twin_mock_{uuid4().hex}"
        next_twin["decision_version"] = int(prior.get("decision_version") or 0) + 1
        next_twin["decision"] = "pending"
        next_twin["decision_is_final"] = False
        next_twin["lifecycle_status"] = "requested"
        next_twin["visible_lifecycle"] = "requested"
        next_twin["autonomy_eligibility"] = "not_available"
        next_twin["risk"] = {"level": "preliminary", "score": None}
        next_twin["progress_index"] = 0
        next_twin["prior_decision"] = {
            "twin_id": prior["twin_id"],
            "decision": prior.get("decision"),
            "risk": prior.get("risk"),
            "decision_version": prior.get("decision_version"),
        }
        now = datetime.now(UTC).isoformat()
        next_twin["created_at"] = now
        next_twin["updated_at"] = now
        superseded["freshness"]["superseded_by"] = next_twin["twin_id"]
        self.repository.save(superseded)
        return self.repository.save(next_twin, dynamic=True)

    def cancel(self, twin_id: str) -> dict[str, Any]:
        twin = self.repository.get(twin_id)
        if twin.get("lifecycle_status") not in ACTIVE_STATES:
            raise DigitalTwinMockError(
                409,
                "generation_not_active",
                "Only an active generation can be cancelled.",
            )
        twin["lifecycle_status"] = "cancelled"
        twin["visible_lifecycle"] = "cancelled"
        twin["decision"] = "cancelled"
        twin["decision_is_final"] = False
        twin["autonomy_eligibility"] = "not_available"
        twin["recommended_action"] = "Generation was cancelled before a final decision."
        return self.repository.save(twin)

    def approval(self, twin_id: str, status: str) -> dict[str, Any]:
        twin = self.repository.get(twin_id)
        if twin.get("decision") != "amber":
            raise DigitalTwinMockError(
                409,
                "approval_not_eligible",
                "Approval actions are available only for an Amber decision.",
            )
        twin["relationships"]["approval_status"] = status
        twin["relationships"]["approval_id"] = f"approval_mock_{uuid4().hex}" if status == "approved" else None
        if status == "approved":
            twin["autonomy_eligibility"] = "eligible_with_approval"
            twin["recommended_action"] = "Approval is valid for this decision version. Start Bundle Execution when ready."
        elif status == "rejected":
            twin["autonomy_eligibility"] = "blocked_by_rejection"
            twin["recommended_action"] = "Approval was rejected. Regenerate after addressing the rationale."
        return self.repository.save(twin)

    def gate(self, twin_id: str) -> dict[str, Any]:
        twin = self.repository.get(twin_id)
        decision = twin.get("decision")
        return {
            "schema_version": self.repository.version,
            "twin": twin,
            "decision": decision,
            "risk": twin.get("risk"),
            "policy": "blocked" if decision == "red" else "review" if decision == "amber" else "passed" if decision == "green" else "pending",
            "evidence": "complete" if twin.get("decision_is_final") else "collecting",
            "freshness": twin.get("freshness"),
            "dry_run": twin.get("optional_states", {}).get("dry-run") or ("passed" if twin.get("decision_is_final") else "queued"),
            "rollback": "high" if decision == "green" else "medium" if decision == "amber" else "not_available",
            "drift": "material" if twin.get("freshness", {}).get("status") == "drifted" else "none_material",
            "reasons": twin.get("top_reasons", []),
            "approval": twin.get("relationships", {}).get("approval_status"),
        }

    @staticmethod
    def _action(code: str, label: str, *, confirmation: bool = False) -> dict[str, Any]:
        return {
            "code": code,
            "label": label,
            "enabled": True,
            "visible": True,
            "method": "GET" if code == "open_twin" else "POST",
            "href": None,
            "reason_code": "eligible",
            "disabled_reason": None,
            "requires_confirmation": confirmation,
        }


def _response_headers(response: Response) -> None:
    response.headers["X-ESDA-Data-Mode"] = DATA_MODE
    response.headers["Cache-Control"] = "no-store"


def build_digital_twin_mock_router(service: DigitalTwinMockService) -> APIRouter:
    router = APIRouter(
        prefix="/api/digital-twins",
        tags=["digital twins"],
        dependencies=[Depends(get_current_user)],
    )

    async def prepare(response: Response, mock_state: str, mock_delay_ms: int | None) -> None:
        _response_headers(response)
        await service.simulate(state=mock_state, delay_ms=mock_delay_ms)

    @router.get("/config")
    async def config(response: Response) -> dict[str, Any]:
        await prepare(response, "success", 0)
        return {
            "schema_version": SCHEMA_VERSION,
            "data_mode": DATA_MODE,
            "enabled": service.enabled,
            "non_production": True,
            "fixture_version": service.repository.version,
        }

    @router.get("/scenarios")
    async def scenarios(response: Response) -> list[dict[str, Any]]:
        await prepare(response, "success", 0)
        return deepcopy(service.repository.scenarios)

    @router.get("")
    async def list_twins(
        response: Response,
        request: Request,
        mock_state: str = Query(default="success"),
        mock_delay_ms: int | None = Query(default=None, ge=0, le=10_000),
    ) -> dict[str, Any]:
        await prepare(response, mock_state, mock_delay_ms)
        return service.list_twins(dict(request.query_params))

    @router.post("")
    async def create_twin(payload: TwinGenerationRequest, response: Response) -> dict[str, Any]:
        await prepare(response, "success", None)
        return service.generate(payload.scenario_id)

    @router.get("/active")
    async def active_twin(response: Response) -> dict[str, Any] | None:
        await prepare(response, "success", 0)
        active = [item for item in service.repository.dynamic_twins() if item.get("lifecycle_status") in ACTIVE_STATES]
        active.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return active[0] if active else None

    @router.get("/history")
    async def history(response: Response) -> list[dict[str, Any]]:
        await prepare(response, "success", 0)
        return service.repository.dynamic_twins()

    @router.delete("/history")
    async def clear_history(response: Response) -> dict[str, Any]:
        await prepare(response, "success", 0)
        service.repository.reset()
        return {"cleared": True, "data_mode": DATA_MODE}

    @router.get("/{twin_id}")
    async def get_twin(
        twin_id: str,
        response: Response,
        mock_state: str = Query(default="success"),
        mock_delay_ms: int | None = Query(default=None, ge=0, le=10_000),
    ) -> dict[str, Any]:
        await prepare(response, mock_state, mock_delay_ms)
        return service.repository.get(twin_id)

    @router.get("/{twin_id}/actions")
    async def actions(twin_id: str, response: Response) -> list[dict[str, Any]]:
        await prepare(response, "success", 0)
        return service.repository.get(twin_id).get("actions", [])

    @router.get("/{twin_id}/tabs/{slug}")
    async def tab(twin_id: str, slug: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", None)
        twin = service.repository.get(twin_id)
        return service.repository.tab(twin, slug)

    @router.post("/{twin_id}/advance")
    async def advance(twin_id: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", 40)
        return service.advance(twin_id)

    @router.post("/{twin_id}/regenerate")
    async def regenerate(twin_id: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", None)
        return service.regenerate(twin_id)

    @router.post("/{twin_id}/cancel")
    async def cancel(twin_id: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", None)
        return service.cancel(twin_id)

    @router.post("/{twin_id}/approval/request")
    async def request_approval(twin_id: str, response: Response, _: TwinApprovalRequest) -> dict[str, Any]:
        await prepare(response, "success", None)
        return service.approval(twin_id, "pending")

    @router.post("/{twin_id}/approval/approve")
    async def approve(twin_id: str, response: Response, _: TwinApprovalRequest) -> dict[str, Any]:
        await prepare(response, "success", None)
        return service.approval(twin_id, "approved")

    @router.post("/{twin_id}/approval/reject")
    async def reject(twin_id: str, response: Response, _: TwinApprovalRequest) -> dict[str, Any]:
        await prepare(response, "success", None)
        return service.approval(twin_id, "rejected")

    @router.get("/{twin_id}/gate")
    async def gate(twin_id: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", 0)
        return service.gate(twin_id)

    for endpoint, slug in TAB_ENDPOINTS.items():
        async def named_tab(twin_id: str, response: Response, _slug: str = slug) -> dict[str, Any]:
            await prepare(response, "success", None)
            twin = service.repository.get(twin_id)
            return service.repository.tab(twin, _slug)

        router.add_api_route(f"/{{twin_id}}/{endpoint}", named_tab, methods=["GET"])

    @router.get("/{twin_id}/report")
    async def report(twin_id: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", None)
        twin = service.repository.get(twin_id)
        return {
            "schema_version": service.repository.version,
            "twin_id": twin_id,
            "decision_version": twin.get("decision_version"),
            "decision": twin.get("decision"),
            "generated_at": twin.get("updated_at"),
            "download_name": f"{twin_id}-decision-report.json",
            "summary": twin.get("recommended_action"),
        }

    @router.get("/{twin_id}/events")
    async def events(
        twin_id: str,
        response: Response,
        cursor: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, Any]:
        await prepare(response, "success", None)
        audit = service.repository.tab(service.repository.get(twin_id), "audit")
        all_events = audit.get("events", [])
        offset = int(str(cursor or "cursor_0").removeprefix("cursor_") or 0)
        rows = all_events[offset : offset + limit]
        next_offset = offset + len(rows)
        return {
            "schema_version": service.repository.version,
            "twin_id": twin_id,
            "events": rows,
            "next_cursor": f"cursor_{next_offset}" if next_offset < len(all_events) else None,
            "has_more": next_offset < len(all_events),
        }

    @router.get("/{twin_id}/safe-explanation")
    async def safe_explanation(twin_id: str, response: Response) -> dict[str, Any]:
        await prepare(response, "success", None)
        twin = service.repository.get(twin_id)
        return twin.get("safe_explanation") or {
            "schema_version": service.repository.version,
            "twin_id": twin_id,
            "decision_version": twin.get("decision_version"),
            "summary": "Fixture explanation only. No model was called.",
            "factors": twin.get("top_reasons", []),
            "limitations": ["This non-production response is deterministic fixture text."],
            "generated_by": "mock_server_fixture",
        }

    return router


def install_digital_twin_mock(app: FastAPI, *, enabled: bool, default_delay_ms: int = 120) -> DigitalTwinMockService:
    service = DigitalTwinMockService(enabled=enabled, default_delay_ms=default_delay_ms)
    app.state.digital_twin_mock = service
    app.include_router(build_digital_twin_mock_router(service))

    @app.exception_handler(DigitalTwinMockError)
    async def digital_twin_mock_error_handler(request: Request, exc: DigitalTwinMockError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-ESDA-Data-Mode": DATA_MODE, "Cache-Control": "no-store"},
            content={
                "data_mode": DATA_MODE,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "request_id": request_id,
                    "details": exc.details,
                },
            },
        )

    return service
