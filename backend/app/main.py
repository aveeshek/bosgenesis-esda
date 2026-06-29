import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import json
import logging
import re
from pathlib import Path
from time import perf_counter
import zipfile
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
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

from backend.app.activity import ActivityService
from backend.app.approvals import ApprovalService
from backend.app.artifact_publisher import ArtifactGitPublishError, ArtifactGitPublisher
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
from backend.app.graphs.mop_generation import MopGenerationGraph, MopGenerationInput
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
from backend.app.tools.mop_agents import (
    HelmManagerEvidenceTool,
    K8sInspectorEvidenceTool,
    MopCreationAgentTool,
)
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


class ActivityChatRequest(BaseModel):
    message: str
    selected_run_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
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


class MopGenerationRequest(BaseModel):
    namespace: str
    change_intent: str = Field(min_length=3, max_length=4000)
    target_environment: str | None = None
    target_namespace: str | None = None
    helm_release: str | None = None
    implementation_window: str | None = None
    analysis_depth: str = "standard"
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
    activity_service = ActivityService(
        repository, settings=settings, artifact_storage_root=artifact_service.storage_root
    )
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
    mop_generation_graph = MopGenerationGraph(
        repository=repository,
        event_bus=event_bus,
        logger=pg_logger,
        settings=settings,
        llm=llm,
        k8s_inspector=K8sInspectorEvidenceTool(settings),
        helm_manager=HelmManagerEvidenceTool(settings),
        mop_creation_agent=MopCreationAgentTool(settings),
        tool_registry=tool_registry,
        policy_guard=policy_guard,
        approval_service=approval_service,
        artifact_service=artifact_service,
        artifact_publisher=artifact_publisher,
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
            {"name": "activity", "description": "Release-note activity timeline and graph data."},
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
    app.state.activity_service = activity_service
    app.state.tool_registry = tool_registry
    app.state.policy_guard = policy_guard
    app.state.approval_service = approval_service
    app.state.l4_service = l4_service
    app.state.graph = graph
    app.state.release_note_graph = release_note_graph
    app.state.mop_generation_graph = mop_generation_graph
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

    @app.get("/mop-generation", response_class=HTMLResponse, tags=["pages"])
    def mop_generation_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, "mop_generation.html", template_context(principal)
        )

    @app.get("/activity", response_class=HTMLResponse, tags=["pages"])
    def activity_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "activity.html", template_context(principal))

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

    @app.get("/api/mop-generation/namespaces", tags=["workflows"])
    def list_mop_generation_namespaces(
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        namespaces = settings.mop_allowed_namespace_list or [settings.default_namespace]
        return {
            "namespaces": [
                {
                    "name": namespace,
                    "default": namespace == settings.default_namespace,
                    "source": "settings_allowlist",
                }
                for namespace in namespaces
            ],
            "default_namespace": settings.default_namespace
            if settings.default_namespace in namespaces
            else namespaces[0],
            "default_environment": settings.mop_default_environment,
            "source": "settings_allowlist",
            "user": principal.username,
        }

    @app.post("/api/mop-generation", tags=["workflows"])
    async def start_mop_generation(
        request: MopGenerationRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        allowed_namespaces = settings.mop_allowed_namespace_list or [settings.default_namespace]
        if request.namespace not in allowed_namespaces:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Namespace is not allowlisted for MoP Generation",
                    "namespace": request.namespace,
                    "allowed_namespaces": allowed_namespaces,
                },
            )
        run_id = f"mop_{uuid4().hex}"
        target_environment = request.target_environment or settings.mop_default_environment
        target_namespace = request.target_namespace or settings.mop_default_target_namespace
        allowed_target_namespaces = {"generic-namespace", "agent-testing"}
        if target_namespace not in allowed_target_namespaces:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Target namespace is not supported for MoP Generation",
                    "target_namespace": target_namespace,
                    "allowed_target_namespaces": sorted(allowed_target_namespaces),
                },
            )
        goal = f"Generate portable MoP for namespace {request.namespace}: {request.change_intent}"
        repository.create_run(
            run_id=run_id,
            user_id=principal.user_id,
            goal=goal,
            target_url=f"k8s://{target_environment}/{request.namespace}",
            namespace=request.namespace,
            workflow_type="mop_generation",
        )
        mop = MopGenerationInput(
            run_id=run_id,
            user_id=principal.user_id,
            namespace=request.namespace,
            target_environment=target_environment,
            target_namespace=target_namespace,
            change_intent=request.change_intent,
            helm_release=request.helm_release,
            implementation_window=request.implementation_window,
            analysis_depth=request.analysis_depth,
            model_profile=request.model_profile,
            user_roles=list(principal.roles),
        )
        asyncio.create_task(mop_generation_graph.run(mop))
        return {
            "run_id": run_id,
            "workflow_type": "mop_generation",
            "events_url": f"/api/runs/{run_id}/events",
            "model_profile": request.model_profile or settings.llm_default_model_profile,
            "namespace": request.namespace,
            "target_environment": target_environment,
            "target_namespace": target_namespace,
            "status": "started",
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

    def activity_upload_folder_name(run) -> str:
        timestamp = run.created_at.strftime("%y%m%d_%H%M%S") if run.created_at else uuid4().hex[:12]
        title = repository._generate_session_name(run) or run.run_id
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip()).strip("._-")
        return f"{timestamp}_{(clean or run.run_id)[:72]}"

    def require_activity_run(run_id: str, principal: SessionPrincipal):
        run = require_run_access(run_id, principal)
        if run.workflow_type not in {"release_note_creation", "mop_generation"}:
            raise HTTPException(status_code=400, detail="Run is not supported by Activity")
        return run

    def activity_workflow_message(workflow_type: str, noun: str) -> str:
        label = activity_service.workflow_label(workflow_type)
        return f"{label} {noun}"

    def download_activity_artifact_response(run_id: str, kind: str, principal: SessionPrincipal):
        require_activity_run(run_id, principal)
        resolution = activity_service.resolve_artifact_download(run_id, kind)
        if not resolution:
            raise HTTPException(status_code=404, detail="Requested artifact is not available")
        if resolution["mode"] == "redirect":
            return RedirectResponse(resolution["url"], status_code=307)
        artifact = resolution["artifact"]
        root = artifact_service.storage_root.resolve()
        path = (root / artifact["storage_path"]).resolve()
        if root not in path.parents and path != root:
            raise HTTPException(status_code=400, detail="Artifact path escaped storage root")
        return FileResponse(path, media_type=artifact["mime_type"], filename=resolution["filename"])

    async def upload_activity_artifact_response(
        run_id: str,
        kind: str,
        file: UploadFile,
        principal: SessionPrincipal,
    ) -> dict:
        run = require_activity_run(run_id, principal)
        normalized_kind = kind.strip().lower()
        if normalized_kind not in {"markdown", "pdf"}:
            raise HTTPException(status_code=400, detail="Artifact kind must be markdown or pdf")

        actions = activity_service.artifact_actions(run_id)
        publish_state = actions.get("publish_state") or {}
        folder_name = publish_state.get("folder_name")
        overwrite_existing = bool(publish_state.get("published") and folder_name)
        if not folder_name:
            folder_name = activity_upload_folder_name(run)
        if not artifact_publisher.is_enabled:
            raise HTTPException(status_code=400, detail="Artifact GitHub publishing is disabled")

        filename = activity_service.artifact_filename(run.workflow_type, normalized_kind)
        max_upload_bytes = 25 * 1024 * 1024
        content = await file.read(max_upload_bytes + 1)
        if not content:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")
        if len(content) > max_upload_bytes:
            raise HTTPException(status_code=413, detail="Uploaded file exceeds 25 MB")
        source_filename = file.filename or filename
        lowered_source = source_filename.lower()
        if normalized_kind == "markdown" and not lowered_source.endswith((".md", ".markdown", ".txt")):
            raise HTTPException(status_code=422, detail="Markdown upload must be .md, .markdown, or .txt")
        if normalized_kind == "pdf":
            if not lowered_source.endswith(".pdf"):
                raise HTTPException(status_code=422, detail="PDF upload must be .pdf")
            if not content.startswith(b"%PDF"):
                raise HTTPException(status_code=422, detail="Uploaded file does not look like a PDF")

        repository.add_event(
            run_id,
            "artifact_overwrite_started",
            f"GitHub artifact upload started for {filename}",
            {
                "artifact_overwrite": {
                    "kind": normalized_kind,
                    "filename": filename,
                    "source_filename": source_filename,
                    "folder_name": folder_name,
                    "branch": publish_state.get("branch") or settings.artifact_git_branch,
                    "mode": "overwrite" if overwrite_existing else "create_folder",
                    "workflow_type": run.workflow_type,
                }
            },
        )
        try:
            overwrite = await artifact_publisher.overwrite_release_artifact(
                run_id=run_id,
                folder_name=str(folder_name),
                filename=filename,
                content=content,
                source_filename=source_filename,
                mime_type=file.content_type,
                allow_create=not overwrite_existing,
            )
        except ArtifactGitPublishError as error:
            repository.add_event(
                run_id,
                "artifact_overwrite_failed",
                f"GitHub artifact overwrite failed for {filename}",
                {
                    "artifact_overwrite": {
                        "status": "failed",
                        "kind": normalized_kind,
                        "filename": filename,
                        "source_filename": source_filename,
                        "folder_name": folder_name,
                        "workflow_type": run.workflow_type,
                        "error": {"message": str(error)},
                    }
                },
            )
            raise HTTPException(status_code=502, detail=str(error)) from error

        if not overwrite_existing:
            publish_metadata = {
                "status": "success",
                "repo_url": overwrite.get("repo_url"),
                "branch": overwrite.get("branch") or settings.artifact_git_branch,
                "folder_name": overwrite.get("folder_name"),
                "folder_path": overwrite.get("folder_name"),
                "tree_url": overwrite.get("tree_url"),
                "commit_hash": overwrite.get("commit_hash"),
                "files": [
                    {
                        "filename": filename,
                        "artifact_id": None,
                        "mime_type": file.content_type,
                    }
                ],
                "github_url": run.target_url,
                "workflow_type": run.workflow_type,
            }
            repository.add_event(
                run_id,
                "artifact_publish_completed",
                activity_workflow_message(run.workflow_type, "artifact folder created in GitHub artifact repository"),
                {"artifact_publish": publish_metadata},
            )

        repository.add_event(
            run_id,
            "artifact_overwrite_completed",
            f"GitHub artifact overwrite completed for {filename}",
            {"artifact_overwrite": overwrite},
        )
        return {"run_id": run_id, "kind": normalized_kind, "artifact_overwrite": overwrite}

    @app.get("/api/activity/runs", tags=["activity"])
    def list_activity_runs(
        workflow_type: str | None = Query(default="all"),
        status: str | None = Query(default=None),
        repo: str | None = Query(default=None),
        model: str | None = Query(default=None),
        published: bool | None = Query(default=None),
        time_range: str = Query(default="30d"),
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        include_hidden: bool = Query(default=False),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        nodes = activity_service.list_activity_nodes(
            user_id=principal.user_id,
            workflow_type=workflow_type,
            include_hidden=include_hidden,
            status=status,
            repo=repo,
            model=model,
            published=published,
            time_range=time_range,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        workflow_counts: dict[str, int] = {}
        for node in nodes:
            workflow_counts[node.get("workflow_type") or "unknown"] = workflow_counts.get(node.get("workflow_type") or "unknown", 0) + 1
        return {"nodes": nodes, "count": len(nodes), "workflow_counts": workflow_counts}

    @app.get("/api/activity/runs/{run_id}", tags=["activity"])
    def get_activity_run_detail(
        run_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_activity_run(run_id, principal)
        detail = activity_service.get_activity_detail(run_id)
        if not detail:
            raise HTTPException(status_code=404, detail="Activity run not found")
        return detail

    @app.get("/api/activity/runs/{run_id}/artifacts", tags=["activity"])
    def get_activity_run_artifacts(
        run_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        require_activity_run(run_id, principal)
        actions = activity_service.artifact_actions(run_id)
        if not actions.get("actions"):
            raise HTTPException(status_code=404, detail="Activity artifacts not found")
        return actions

    @app.get("/api/activity/runs/{run_id}/artifact/{kind}/download", tags=["activity"])
    def download_activity_run_artifact(
        run_id: str,
        kind: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ):
        return download_activity_artifact_response(run_id, kind, principal)

    @app.post("/api/activity/runs/{run_id}/artifact/{kind}/upload", tags=["activity"])
    async def upload_activity_run_artifact(
        run_id: str,
        kind: str,
        file: UploadFile = File(...),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        return await upload_activity_artifact_response(run_id, kind, file, principal)

    @app.get("/api/activity/release-notes", tags=["activity"])
    def list_release_note_activity(
        status: str | None = Query(default=None),
        repo: str | None = Query(default=None),
        model: str | None = Query(default=None),
        published: bool | None = Query(default=None),
        time_range: str = Query(default="30d"),
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        include_hidden: bool = Query(default=False),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        nodes = activity_service.list_release_note_nodes(
            user_id=principal.user_id,
            include_hidden=include_hidden,
            status=status,
            repo=repo,
            model=model,
            published=published,
            time_range=time_range,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return {"nodes": nodes, "count": len(nodes)}

    @app.get("/api/activity/release-notes/{run_id}", tags=["activity"])
    def get_release_note_activity_detail(
        run_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = require_run_access(run_id, principal)
        if run.workflow_type != "release_note_creation":
            raise HTTPException(status_code=400, detail="Release-note activity route only supports release-note runs")
        detail = activity_service.get_release_note_detail(run_id)
        if not detail:
            raise HTTPException(status_code=404, detail="Release-note activity not found")
        return detail

    @app.get("/api/activity/release-notes/{run_id}/artifacts", tags=["activity"])
    def get_release_note_activity_artifacts(
        run_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = require_run_access(run_id, principal)
        if run.workflow_type != "release_note_creation":
            raise HTTPException(status_code=400, detail="Release-note activity route only supports release-note runs")
        actions = activity_service.release_note_artifact_actions(run_id)
        if not actions.get("actions"):
            raise HTTPException(status_code=404, detail="Release-note artifacts not found")
        return actions

    @app.get("/api/activity/release-notes/{run_id}/artifact/{kind}/download", tags=["activity"])
    def download_release_note_activity_artifact(
        run_id: str,
        kind: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ):
        run = require_run_access(run_id, principal)
        if run.workflow_type != "release_note_creation":
            raise HTTPException(status_code=400, detail="Release-note activity route only supports release-note runs")
        return download_activity_artifact_response(run_id, kind, principal)

    @app.post("/api/activity/release-notes/{run_id}/artifact/{kind}/upload", tags=["activity"])
    async def upload_release_note_activity_artifact(
        run_id: str,
        kind: str,
        file: UploadFile = File(...),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = require_run_access(run_id, principal)
        if run.workflow_type != "release_note_creation":
            raise HTTPException(status_code=400, detail="Release-note activity route only supports release-note runs")
        return await upload_activity_artifact_response(run_id, kind, file, principal)

    @app.post("/api/activity/chat", tags=["activity"])
    async def activity_chat(
        request: ActivityChatRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        message = request.message.strip()
        selected_run_ids = list(dict.fromkeys([run_id for run_id in request.selected_run_ids if run_id]))[:8]
        if not message:
            raise HTTPException(status_code=422, detail="Message is required")
        if not selected_run_ids:
            raise HTTPException(status_code=422, detail="Select at least one activity node")
        for run_id in selected_run_ids:
            require_activity_run(run_id, principal)

        chat_session_id = request.session_id or f"chat_{uuid4().hex}"
        if request.session_id:
            if not repository.get_chat_session(session_id=request.session_id, user_id=principal.user_id):
                raise HTTPException(status_code=404, detail="Activity chat session not found")
        else:
            first_detail = activity_service.get_activity_detail(selected_run_ids[0]) or {}
            first_node = first_detail.get("node") or {}
            repository.create_chat_session(
                session_id=chat_session_id,
                user_id=principal.user_id,
                title=f"Activity: {first_node.get('repository') or selected_run_ids[0]}",
            )

        context = activity_service.build_chat_context(selected_run_ids)
        fallback = activity_service.fallback_chat_answer(question=message, context=context)
        model_profile = request.model_profile or settings.llm_default_model_profile
        repository.add_chat_message(
            session_id=chat_session_id,
            run_id=selected_run_ids[0] if len(selected_run_ids) == 1 else None,
            role="user",
            content=message,
            payload={"selected_run_ids": selected_run_ids, "surface": "activity"},
        )
        result = await llm.structured_response(
            system=(
                "You are the ESDA Activity artifact analyst. Answer only from the supplied selected "
                "Activity run context and artifact text across Release Note and MoP workflows. Cite run IDs, "
                "artifact IDs, published folders, workflow names, namespaces, or section names. Never reveal hidden chain-of-thought. Return JSON with "
                "answer, citations, and safe_summary."
            ),
            user_payload=activity_service.chat_prompt_payload(question=message, context=context),
            fallback=fallback
            | {
                "prompt_version": "activity_artifact_chat_v1",
                "prompt_hash": activity_service.chat_fallback_hash(question=message, context=context),
            },
            model_profile=model_profile,
        )
        answer = str(result.get("answer") or result.get("message") or fallback["answer"])[:5000]
        citations = result.get("citations") if isinstance(result.get("citations"), list) else fallback["citations"]
        safe_summary = str(result.get("safe_summary") or fallback["safe_summary"])[:1000]
        repository.add_chat_message(
            session_id=chat_session_id,
            run_id=selected_run_ids[0] if len(selected_run_ids) == 1 else None,
            role="assistant",
            content=answer,
            payload={
                "selected_run_ids": selected_run_ids,
                "citations": citations,
                "safe_summary": safe_summary,
                "model_profile": model_profile,
                "prompt_version": result.get("prompt_version", "activity_artifact_chat_v1"),
                "prompt_hash": result.get("prompt_hash"),
                "used_fallback": bool(result.get("used_fallback", False)),
            },
        )
        return {
            "session_id": chat_session_id,
            "answer": answer,
            "citations": citations,
            "safe_summary": safe_summary,
            "selected_run_ids": selected_run_ids,
            "model_profile": model_profile,
        }

    @app.get("/api/activity/chat/{session_id}", tags=["activity"])
    def get_activity_chat(
        session_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        session = repository.get_chat_session(session_id=session_id, user_id=principal.user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Activity chat session not found")
        messages = repository.list_chat_messages(session_id=session_id, user_id=principal.user_id) or []
        return {"session": session, "messages": messages}

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

    @app.get("/api/runs/{run_id}/bundle", tags=["runs"])
    def download_run_bundle(
        run_id: str,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> Response:
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.user_id != principal.user_id and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        artifacts = repository.list_artifacts(run_id)
        if not artifacts:
            raise HTTPException(status_code=404, detail="No artifacts found for this run")
        root = artifact_service.storage_root.resolve()
        buffer = BytesIO()
        used_names: set[str] = set()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for artifact in artifacts:
                storage_path = artifact.get("storage_path") or ""
                absolute_path = (root / storage_path).resolve()
                if root not in absolute_path.parents and absolute_path != root:
                    raise HTTPException(status_code=400, detail="Artifact path escaped storage root")
                if not absolute_path.exists() or not absolute_path.is_file():
                    continue
                filename = (artifact.get("metadata") or {}).get("filename") or Path(storage_path).name
                arcname = str(filename)
                if arcname in used_names:
                    arcname = f"{artifact.get('artifact_type') or 'artifact'}/{arcname}"
                used_names.add(arcname)
                archive.write(absolute_path, arcname)
        if not used_names:
            raise HTTPException(status_code=404, detail="No downloadable artifacts found for this run")
        buffer.seek(0)
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{run_id}-mop-bundle.zip"'},
        )

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
        filename = (artifact.get("metadata") or {}).get("filename") or artifact["storage_path"].rsplit("/", 1)[-1]
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
