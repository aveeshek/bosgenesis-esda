import asyncio
from contextlib import asynccontextmanager
import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.approvals import ApprovalService
from backend.app.artifact_publisher import ArtifactGitPublisher
from backend.app.artifacts import ArtifactService
from backend.app.auth.security import SessionPrincipal, create_session_cookie, verify_password
from backend.app.chains.release_notes import ReleaseNoteIntentClassifierChain
from backend.app.config import get_settings
from backend.app.db.database import Database, RunRepository
from backend.app.db.models import User
from backend.app.dependencies import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_current_user_or_none,
)
from backend.app.graphs.diagnostic import DiagnosticGraph, DiagnosticInput
from backend.app.graphs.event_bus import RunEventBus
from backend.app.graphs.release_notes import ReleaseNoteGraph, ReleaseNoteInput
from backend.app.l4 import (
    ConditionalL4Service,
    L4EligibilityRequest,
    L4StopCheckRequest,
    ProcedureCreateRequest,
)
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.logging.postgres_logger import PostgresLogger
from backend.app.policy.evaluator import PolicyGuard
from backend.app.repo_analysis import RepoAnalysisService
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.mcp_client import K8sInspectorMcpTool
from backend.app.tools.powershell_get import PowerShellGetTemplateTool
from backend.app.tools.release_note_agent import ReleaseNoteAgentTool
from backend.app.tools.registry import default_tool_registry
from backend.app.tools.rest_get import RestGetTool


logger = logging.getLogger("bosgenesis_esda")


class LoginRequest(BaseModel):
    username: str
    password: str


class DiagnosticRequest(BaseModel):
    goal: str
    target_url: str
    namespace: str | None = None


class LlmChatRequest(BaseModel):
    message: str
    model_profile: str | None = None


class WorkflowClassifyRequest(BaseModel):
    message: str
    github_url: str | None = None
    release_name: str | None = None
    model_profile: str | None = None


class PolicyEvaluateRequest(BaseModel):
    run_id: str | None = None
    step_id: str = "manual_policy_check"
    tool_name: str
    workflow_type: str = "k8s_management"
    environment: str = "local"
    namespace: str | None = None
    arguments: dict = Field(default_factory=dict)
    autonomy_mode: str = "assisted"
    create_approval: bool = True


class ApprovalDecisionRequest(BaseModel):
    notes: str = ""


class ReleaseNoteRequest(BaseModel):
    github_url: str
    release_name: str | None = None
    branch: str | None = None
    tag: str | None = None
    commit_sha: str | None = None
    analysis_depth: str = "fast"
    model_profile: str | None = None


def create_app() -> FastAPI:
    settings = get_settings()
    database = Database(settings)
    repository = RunRepository(database)
    event_bus = RunEventBus()
    pg_logger = PostgresLogger(database, settings)
    artifact_service = ArtifactService(
        repository=repository, storage_root=settings.artifact_storage_dir
    )
    artifact_publisher = ArtifactGitPublisher(settings)
    tool_registry = default_tool_registry()
    policy_guard = PolicyGuard(settings=settings, tool_registry=tool_registry)
    approval_service = ApprovalService(
        repository=repository,
        settings=settings,
        policy_guard=policy_guard,
    )
    l4_service = ConditionalL4Service(
        settings=settings,
        repository=repository,
        policy_guard=policy_guard,
        tool_registry=tool_registry,
    )
    llm = AzureGpt5Service(settings)
    repo_analyzer = RepoAnalysisService(llm=llm)
    workflow_classifier = ReleaseNoteIntentClassifierChain(llm)
    graph = DiagnosticGraph(
        repository=repository,
        event_bus=event_bus,
        logger=pg_logger,
        llm=llm,
        rest_tool=RestGetTool(settings),
        powershell_tool=PowerShellGetTemplateTool(settings),
        mcp_tool=K8sInspectorMcpTool(settings),
        tool_registry=tool_registry,
        policy_guard=policy_guard,
        approval_service=approval_service,
    )
    release_note_graph = ReleaseNoteGraph(
        repository=repository,
        event_bus=event_bus,
        logger=pg_logger,
        llm=llm,
        release_note_agent=ReleaseNoteAgentTool(settings),
        artifact_service=artifact_service,
        artifact_publisher=artifact_publisher,
        tool_registry=tool_registry,
        policy_guard=policy_guard,
        approval_service=approval_service,
        repo_analyzer=repo_analyzer,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        database.init()
        pg_logger.init()
        logger.info("settings_loaded %s", settings.redacted_summary())
        yield

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "system", "description": "Application health and diagnostics."},
            {"name": "auth", "description": "Local Phase 1 authentication."},
            {"name": "runs", "description": "Agent run lifecycle and event streaming."},
            {"name": "workflows", "description": "Read-only workflow entrypoints."},
            {"name": "policy", "description": "Policy guardrail evaluation."},
            {"name": "approvals", "description": "Human approval lifecycle."},
            {"name": "l4", "description": "Conditional L4 eligibility and audit."},
            {"name": "procedures", "description": "Approved procedure catalog."},
            {"name": "pages", "description": "HTML application pages."},
        ],
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.event_bus = event_bus
    app.state.logger = pg_logger
    app.state.artifact_service = artifact_service
    app.state.artifact_publisher = artifact_publisher
    app.state.tool_registry = tool_registry
    app.state.policy_guard = policy_guard
    app.state.approval_service = approval_service
    app.state.l4_service = l4_service
    app.state.graph = graph
    app.state.release_note_graph = release_note_graph
    app.state.workflow_classifier = workflow_classifier
    app.state.repo_analyzer = repo_analyzer

    templates = Jinja2Templates(directory="backend/app/templates")
    app.mount("/static", StaticFiles(directory="backend/app/static"), name="static")

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        start = perf_counter()
        response = await call_next(request)
        duration_ms = int((perf_counter() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed %s",
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "request_id": getattr(request.state, "request_id", None),
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "unhandled_exception %s",
            {"request_id": getattr(request.state, "request_id", None), "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    def authenticate(username: str, password: str) -> SessionPrincipal | None:
        with database.session() as db:
            user = db.scalar(select(User).where(User.username == username))
            if not user or not user.is_active or not verify_password(password, user.password_hash):
                return None
            return SessionPrincipal(user_id=user.user_id, username=user.username, roles=user.roles)

    def set_session_cookie(response: Response, principal: SessionPrincipal) -> None:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            create_session_cookie(principal, settings.secret_key),
            httponly=True,
            samesite="lax",
        )

    def public_user(principal: SessionPrincipal) -> dict:
        return {
            "user_id": principal.user_id,
            "username": principal.username,
            "roles": principal.roles,
        }

    def template_context(principal: SessionPrincipal, **extra) -> dict:
        context = {
            "user": principal,
            "model_profiles": llm.model_profiles(),
            "default_model_profile": settings.llm_default_model_profile,
        }
        context.update(extra)
        return context

    def require_approver(principal: SessionPrincipal) -> None:
        if not {"admin", "approver"}.intersection(principal.roles):
            raise HTTPException(status_code=403, detail="Approver role required")

    def tool_request_from_policy_request(
        request: PolicyEvaluateRequest,
        principal: SessionPrincipal,
    ) -> ToolExecutionRequest:
        return ToolExecutionRequest(
            run_id=request.run_id or f"policy_{uuid4().hex}",
            step_id=request.step_id,
            tool_name=request.tool_name,
            workflow_type=request.workflow_type,
            environment=request.environment,
            namespace=request.namespace,
            user_id=principal.user_id,
            arguments=request.arguments,
            autonomy_mode=request.autonomy_mode,
        )

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "app": settings.app_name}

    @app.get("/api/llm/model-profiles", tags=["system"])
    def llm_model_profiles(principal: SessionPrincipal = Depends(get_current_user)) -> dict:
        return {
            "default_model_profile": settings.llm_default_model_profile,
            "profiles": llm.model_profiles(),
            "user": principal.username,
        }

    @app.post("/api/llm/chat", tags=["system"])
    async def llm_chat(
        request: LlmChatRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        result = await llm.chat(message=request.message, model_profile=request.model_profile)
        result["user"] = principal.username
        return result

    @app.post("/api/llm/smoke-test", tags=["system"])
    async def llm_smoke_test(
        model_profile: str | None = None,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        result = await llm.smoke_test(model_profile=model_profile)
        result["user"] = principal.username
        return result

    @app.get("/login", response_class=HTMLResponse, tags=["pages"])
    def login_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login", tags=["pages"])
    def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
        principal = authenticate(username, password)
        if not principal:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid username or password"},
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, principal)
        return response

    @app.get("/logout", tags=["pages"])
    def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return response

    @app.post("/api/auth/login", tags=["auth"])
    def api_login(credentials: LoginRequest, response: Response) -> dict:
        principal = authenticate(credentials.username, credentials.password)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        set_session_cookie(response, principal)
        return {"user": public_user(principal)}

    @app.post("/api/auth/logout", tags=["auth"])
    def api_logout(
        response: Response, principal: SessionPrincipal = Depends(get_current_user)
    ) -> dict:
        response.delete_cookie(SESSION_COOKIE_NAME)
        return {"status": "logged_out", "user": public_user(principal)}

    @app.get("/api/auth/me", tags=["auth"])
    def api_me(principal: SessionPrincipal = Depends(get_current_user)) -> dict:
        return {"user": public_user(principal)}

    @app.get("/", response_class=HTMLResponse, tags=["pages"])
    def index(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "index.html",
            template_context(principal, default_namespace=settings.default_namespace),
        )

    @app.get("/release-notes", response_class=HTMLResponse, tags=["pages"])
    def release_notes_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, "release_notes.html", template_context(principal)
        )

    @app.get("/approvals", response_class=HTMLResponse, tags=["pages"])
    def approvals_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        if not {"admin", "approver"}.intersection(principal.roles):
            raise HTTPException(status_code=403, detail="Approver role required")
        return templates.TemplateResponse(request, "approvals.html", template_context(principal))

    @app.get("/l4-audit", response_class=HTMLResponse, tags=["pages"])
    def l4_audit_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "l4_audit.html", template_context(principal))

    @app.post("/api/policy/evaluate", tags=["policy"])
    def evaluate_policy(
        request: PolicyEvaluateRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        tool_request = tool_request_from_policy_request(request, principal)
        decision = policy_guard.evaluate_tool(tool_request, user_roles=principal.roles)
        approval = None
        if decision.decision == "approval_required" and request.create_approval:
            approval = approval_service.create_request(
                request=tool_request,
                requested_by_user_id=principal.user_id,
                decision=decision,
            )
        return {
            "request": tool_request.model_dump(),
            "decision": decision.to_dict(),
            "approval": approval,
        }

    @app.get("/api/approvals", tags=["approvals"])
    def list_approvals(
        status: str | None = None,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_approver(principal)
        return {"approvals": approval_service.list_requests(status=status)}

    @app.post("/api/approvals/{approval_id}/approve", tags=["approvals"])
    def approve_request(
        approval_id: str,
        request: ApprovalDecisionRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_approver(principal)
        approval = approval_service.approve(
            approval_id,
            approver_user_id=principal.user_id,
            approver_roles=list(principal.roles),
            notes=request.notes,
        )
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")
        return {"approval": approval}

    @app.post("/api/approvals/{approval_id}/reject", tags=["approvals"])
    def reject_request(
        approval_id: str,
        request: ApprovalDecisionRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_approver(principal)
        approval = approval_service.reject(
            approval_id,
            reviewer_user_id=principal.user_id,
            notes=request.notes,
        )
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")
        return {"approval": approval}

    @app.post("/api/approvals/{approval_id}/modify-and-recheck", tags=["approvals"])
    def modify_and_recheck_request(
        approval_id: str,
        request: PolicyEvaluateRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_approver(principal)
        tool_request = tool_request_from_policy_request(request, principal)
        approval = approval_service.modify_and_recheck(
            approval_id,
            modified_request=tool_request,
            reviewer_user_id=principal.user_id,
            reviewer_roles=list(principal.roles),
            notes="Modified and rechecked by approver.",
        )
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")
        return {"approval": approval}

    @app.post("/api/l4/eligibility", tags=["l4"])
    def evaluate_l4_eligibility(
        request: L4EligibilityRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        return l4_service.evaluate_eligibility(
            request,
            user_id=principal.user_id,
            user_roles=list(principal.roles),
        )

    @app.post("/api/l4/stop-check", tags=["l4"])
    def evaluate_l4_stop_conditions(
        request: L4StopCheckRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        return l4_service.stop_conditions.evaluate(request)

    @app.get("/api/l4/audit", tags=["l4"])
    def list_l4_audit_records(
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        return {"audits": repository.list_l4_audit_records()}

    @app.get("/api/l4/audit/{audit_id}/export", response_class=PlainTextResponse, tags=["l4"])
    def export_l4_audit_record(
        audit_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> PlainTextResponse:
        audit = repository.get_l4_audit_record(audit_id)
        if not audit:
            raise HTTPException(status_code=404, detail="L4 audit record not found")
        return PlainTextResponse(
            l4_service.export_audit_markdown(audit),
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={audit_id}.md"},
        )

    @app.get("/api/procedures", tags=["procedures"])
    def list_procedures(principal: SessionPrincipal = Depends(get_current_user)) -> dict:
        return {"procedures": repository.list_procedures()}

    @app.post("/api/procedures", tags=["procedures"])
    def create_procedure(
        request: ProcedureCreateRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_approver(principal)
        return {"procedure": l4_service.create_procedure(request, owner_user_id=principal.user_id)}

    @app.post("/api/workflows/classify", tags=["workflows"])
    async def classify_workflow(
        request: WorkflowClassifyRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        classification = await workflow_classifier.run(
            user_text=request.message,
            github_url=request.github_url,
            release_name=request.release_name,
            model_profile=request.model_profile,
        )
        result = classification.model_dump()
        result["user"] = principal.username
        return result

    @app.post("/api/chat", tags=["workflows"])
    async def start_diagnostic(
        request: DiagnosticRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run_id = f"run_{uuid4().hex}"
        chat_session_id = f"chat_{uuid4().hex}"
        namespace = request.namespace or settings.default_namespace
        repository.create_chat_session(
            session_id=chat_session_id,
            user_id=principal.user_id,
            title=request.goal[:120] or "Health-check diagnostic",
        )
        repository.create_run(
            run_id=run_id,
            user_id=principal.user_id,
            goal=request.goal,
            target_url=request.target_url,
            namespace=namespace,
        )
        repository.add_chat_message(
            session_id=chat_session_id,
            run_id=run_id,
            role="user",
            content=request.goal,
            payload={"target_url": request.target_url, "namespace": namespace},
        )
        diagnostic = DiagnosticInput(
            run_id=run_id,
            user_id=principal.user_id,
            goal=request.goal,
            target_url=request.target_url,
            namespace=namespace,
            chat_session_id=chat_session_id,
            user_roles=list(principal.roles),
        )
        asyncio.create_task(graph.run(diagnostic))
        return {
            "run_id": run_id,
            "chat_session_id": chat_session_id,
            "events_url": f"/api/runs/{run_id}/events",
        }

    @app.post("/api/release-notes", tags=["workflows"])
    async def start_release_notes(
        request: ReleaseNoteRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run_id = f"run_{uuid4().hex}"
        release_goal = f"Generate release notes for {request.github_url}"
        repository.create_run(
            run_id=run_id,
            user_id=principal.user_id,
            goal=release_goal,
            target_url=request.github_url,
            namespace=None,
            workflow_type="release_note_creation",
        )
        release_note = ReleaseNoteInput(
            run_id=run_id,
            user_id=principal.user_id,
            github_url=request.github_url,
            release_name=request.release_name,
            branch=request.branch,
            tag=request.tag,
            commit_sha=request.commit_sha,
            analysis_depth=request.analysis_depth,
            model_profile=request.model_profile,
            user_roles=list(principal.roles),
        )
        asyncio.create_task(release_note_graph.run(release_note))
        return {
            "run_id": run_id,
            "events_url": f"/api/runs/{run_id}/events",
            "model_profile": request.model_profile or settings.llm_default_model_profile,
        }

    def require_run_access(run_id: str, principal: SessionPrincipal):
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.user_id != principal.user_id and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return run

    @app.get("/api/transactions", tags=["runs"])
    def list_transactions(
        workflow_type: str | None = None,
        include_hidden: bool = False,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        transactions = repository.list_transactions(
            user_id=principal.user_id, include_hidden=include_hidden
        )
        if workflow_type:
            transactions = [item for item in transactions if item["workflow_type"] == workflow_type]
        return {"transactions": transactions}

    @app.post("/api/transactions/{run_id}/clear", tags=["runs"])
    def clear_transaction(
        run_id: str, principal: SessionPrincipal = Depends(get_current_user)
    ) -> dict:
        run = require_run_access(run_id, principal)
        if run.user_id != principal.user_id:
            raise HTTPException(status_code=403, detail="Only the owner can clear this transaction")
        if not repository.clear_transaction(user_id=principal.user_id, run_id=run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run_id": run_id, "status": "hidden"}

    @app.get("/api/runs/{run_id}", tags=["runs"])
    def get_run(run_id: str, principal: SessionPrincipal = Depends(get_current_user)) -> dict:
        run = require_run_access(run_id, principal)
        return {
            "run_id": run.run_id,
            "workflow_type": run.workflow_type,
            "status": run.status,
            "goal": run.goal,
            "target_url": run.target_url,
            "namespace": run.namespace,
            "final_report": run.final_report,
        }

    @app.get("/api/runs/{run_id}/snapshot", tags=["runs"])
    def get_run_snapshot(
        run_id: str, principal: SessionPrincipal = Depends(get_current_user)
    ) -> dict:
        require_run_access(run_id, principal)
        snapshot = repository.get_run_snapshot(run_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Run not found")
        if principal.user_id == snapshot["run"]["user_id"]:
            repository.mark_transaction_opened(user_id=principal.user_id, run_id=run_id)
        snapshot["events_url"] = f"/api/runs/{run_id}/events"
        return snapshot

    @app.post("/api/runs/{run_id}/stop", tags=["runs"])
    def stop_run(run_id: str, principal: SessionPrincipal = Depends(get_current_user)) -> dict:
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.user_id != principal.user_id and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        repository.update_status(run_id, "stopped")
        repository.add_event(
            run_id, "run_stopped", "Run stop requested", {"requested_by": principal.user_id}
        )
        return {"run_id": run_id, "status": "stopped"}

    @app.get("/api/runs/{run_id}/artifacts", tags=["runs"])
    def list_run_artifacts(
        run_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.user_id != principal.user_id and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return {"run_id": run_id, "artifacts": repository.list_artifacts(run_id)}

    @app.get("/api/artifacts/{artifact_id}", tags=["runs"])
    def download_artifact(
        artifact_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ):
        artifact = repository.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        run = repository.get_run(artifact["run_id"])
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.user_id != principal.user_id and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        filename = artifact["storage_path"].rsplit("/", 1)[-1]
        return FileResponse(
            artifact_service.storage_root / artifact["storage_path"],
            media_type=artifact["mime_type"],
            filename=filename,
        )

    @app.get("/api/runs/{run_id}/events", tags=["runs"])
    async def run_events(
        run_id: str,
        after_event_id: str | None = Query(default=None),
        after_sequence: int | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        principal: SessionPrincipal = Depends(get_current_user),
    ):
        require_run_access(run_id, principal)

        def sse(event: dict) -> str:
            event_id = event.get("event_id", "")
            return f"id: {event_id}\ndata: {json.dumps(event)}\n\n"

        async def stream():
            replay_after_event_id = after_event_id or last_event_id
            for event in repository.list_events(
                run_id,
                after_event_id=replay_after_event_id,
                after_sequence=after_sequence,
            ):
                yield sse(event)
            async for event in event_bus.subscribe(run_id):
                yield sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
