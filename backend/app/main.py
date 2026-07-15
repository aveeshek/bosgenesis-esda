import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
import logging
import re
from pathlib import Path
from time import perf_counter
from urllib.parse import quote, urlparse
import zipfile
from uuid import uuid4
from typing import Any

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
from backend.app.artifact_publisher import ArtifactGitPublishError, ArtifactGitPublisher, ArtifactPublishPayload
from backend.app.artifacts import ArtifactService
from backend.app.auth.security import SessionPrincipal, create_session_cookie, verify_password
from backend.app.chains.release_notes import ReleaseNoteIntentClassifierChain
from backend.app.config import get_settings
from backend.app.db.database import Database, RunRepository
from backend.app.db.models import User
from backend.app.env_agent import env_agent_contract
from backend.app.dependencies import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_current_user_or_none,
)
from backend.app.digital_twin_gateway import install_digital_twin_gateway
from backend.app.digital_twin_mock import install_digital_twin_mock
from backend.app.graphs.diagnostic import DiagnosticGraph, DiagnosticInput
from backend.app.graphs.event_bus import RunEventBus
from backend.app.graphs.env_agent import EnvAgentRuntimeInput, EnvAgentWorkflowGraph
from backend.app.graphs.mop_generation import MopGenerationGraph, MopGenerationInput
from backend.app.graphs.release_notes import ReleaseNoteGraph, ReleaseNoteInput
from backend.app.l4 import (
    ConditionalL4Service,
    L4EligibilityRequest,
    L4StopCheckRequest,
    ProcedureCreateRequest,
)
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.memory import MemoryService
from backend.app.mop_execution import MopExecutionPreflightService, MopExecutionRunRequest, MopExecutionRunStore
from backend.app.logging.postgres_logger import PostgresLogger
from backend.app.logging.redaction import redact
from backend.app.logging.setup import configure_logging
from backend.app.policy.evaluator import PolicyGuard
from backend.app.repo_analysis import RepoAnalysisService
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult
from backend.app.tools.env_agents import (
    EnvAgentDataIngestionTool,
    EnvAgentHelmManagerTool,
    EnvAgentK8sInspectorTool,
    EnvAgentObservabilityTool,
)
from backend.app.tools.mcp_client import K8sInspectorMcpTool
from backend.app.tools.mop_agents import (
    HelmManagerEvidenceTool,
    K8sInspectorEvidenceTool,
    MopCreationAgentTool,
    redact_sensitive,
)
from backend.app.tools.mop_execution_agent import MopExecutionAgentClient, MopExecutionAgentError
from backend.app.tools.powershell_get import PowerShellGetTemplateTool
from backend.app.tools.release_note_agent import ReleaseNoteAgentTool
from backend.app.tools.registry import default_tool_registry
from backend.app.tools.rest_get import RestGetTool


logger = logging.getLogger("bosgenesis_esda")
mop_execution_logger = logging.getLogger("bosgenesis_esda.mop_execution")

def _mop_debug_json(value: Any, *, max_chars: int = 30_000) -> str:
    try:
        rendered = json.dumps(redact_sensitive(redact(value)), default=str, sort_keys=True)
    except Exception:
        rendered = str(redact(value))
    if len(rendered) > max_chars:
        return rendered[:max_chars] + f"...<truncated {len(rendered) - max_chars} chars>"
    return rendered



def _write_mop_execution_run_log(settings: Any, run_id: str | None, event_type: str, payload: Any) -> None:
    if not run_id:
        return
    try:
        log_dir = Path(settings.log_dir) / "mop-execution-runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "event_type": event_type,
            "payload": redact_sensitive(redact(payload)),
        }
        with (log_dir / f"{run_id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str, sort_keys=True) + "\n")
    except Exception:
        mop_execution_logger.exception("mop_execution_run_log_write_failed run_id=%s event_type=%s", run_id, event_type)

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


class EnvAgentChatRequest(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    namespace: str | None = None
    session_id: str | None = None
    mode: str = "auto"
    scope: str = "prompt"
    model_profile: str | None = None


class EnvAgentRemediationExecuteRequest(BaseModel):
    run_id: str = Field(min_length=3, max_length=80)
    approval_id: str = Field(min_length=3, max_length=80)
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


class MopExecutionPreflightRequest(BaseModel):
    source_type: str = Field(pattern="^(activity_run|artifact_repo_folder)$")
    target_namespace: str
    run_id: str | None = None
    artifact_id: str | None = None
    folder_name: str | None = None



class MopExecutionValidationRequest(MopExecutionPreflightRequest):
    correlation_id: str | None = None
    execution_mode: str = "dry_run_then_approval"
    model_profile: str | None = None


class MopExecutionDryRunRequest(BaseModel):
    run_id: str
    bundle_id: str | None = None
    target_namespace: str | None = None
    correlation_id: str | None = None
    execution_mode: str = "dry_run_then_approval"
    model_profile: str | None = None


class MopExecutionInstructionRequest(BaseModel):
    run_id: str
    job_id: str | None = None
    action: str = Field(pattern="^(continue|retry_read_only|use_default|skip_optional|abort)$")
    instruction: str = Field(min_length=3, max_length=2000)
    rationale: str | None = Field(default=None, max_length=1000)
    scope: dict = Field(default_factory=dict)
    correlation_id: str | None = None
    model_profile: str | None = None


class MopExecutionApprovalRequest(BaseModel):
    run_id: str
    job_id: str | None = None
    rationale: str = Field(min_length=10, max_length=2000)
    scope: dict = Field(default_factory=dict)
    expires_minutes: int = Field(default=60, ge=5, le=1440)
    command_fingerprints: list[str] = Field(default_factory=list)
    correlation_id: str | None = None
    model_profile: str | None = None


class MopExecutionMutationRequest(BaseModel):
    run_id: str
    strategy: str = Field(default="continue_existing", pattern="^(create_job|continue_existing)$")
    mutation_job_id: str | None = None
    correlation_id: str | None = None
    model_profile: str | None = None


class MopExecutionValidationReportRequest(BaseModel):
    run_id: str
    publish: bool = True
    model_profile: str | None = None


class MopExecutionCleanupRequest(BaseModel):
    run_id: str
    target_namespace: str | None = None
    rationale: str = Field(min_length=10, max_length=2000)
    cleanup_scope: str = Field(default="namespace_empty_state", pattern="^(namespace_empty_state|release_resources|demo_cleanup)$")
    approval_id: str | None = None
    correlation_id: str | None = None
    model_profile: str | None = None



def _add_env_agent_state_events(repository: RunRepository, *, run_id: str, state: dict[str, Any]) -> None:
    event_specs = [
        ("workflow_classified", "Environment Chat workflow classified", "classification"),
        ("plan_created", "Environment Chat tool plan created", "plan"),
        ("inspection_completed", "Environment Chat read-only inspection completed", "evidence"),
        ("evidence_correlated", "Environment Chat evidence correlation completed", "correlation"),
        ("diagnosis_completed", "Environment Chat diagnosis completed", "diagnosis"),
        ("remediation_proposal_created", "Environment Chat remediation proposal evaluated", "remediation"),
        ("verification_completed", "Environment Chat verification completed", "verification"),
        ("recovery_recommendation", "Environment Chat recovery recommendation selected", "recovery"),
    ]
    for event_type, message, key in event_specs:
        value = state.get(key)
        if value is None:
            continue
        payload = {key: value}
        if key == "evidence":
            payload["evidence_count"] = len(value or [])
        repository.add_event(run_id, event_type, message, payload)

_ENV_AGENT_MODE_VALUES = {"diagnostic_only", "propose_only", "approval_gated_remediation"}
_ENV_AGENT_REMEDIATION_WORDS = re.compile(r"\b(fix|repair|restart|scale|patch|apply|create|replace|delete|remove|uninstall|rollback|install|installation|upgrade|deploy|proceed|approve|approved|approval|remediate|heal)\b", re.IGNORECASE)
_ENV_AGENT_PROPOSAL_WORDS = re.compile(r"\b(propose|recommend|plan|what should)\b", re.IGNORECASE)
_ENV_AGENT_NAMESPACE_PATTERNS = (
    re.compile(r"\bin\s+([a-z0-9][a-z0-9-]*)\s+namespace\b", re.IGNORECASE),
    re.compile(r"\b-n\s+([a-z0-9][a-z0-9-]*)\b", re.IGNORECASE),
    re.compile(r"\b(?:namespace|ns)\s*[:=]\s*([a-z0-9][a-z0-9-]*)\b", re.IGNORECASE),
    re.compile(r"\b(?:namespace|ns)\s+(?:named|called)\s+([a-z0-9][a-z0-9-]*)\b", re.IGNORECASE),
)


def _infer_env_agent_namespace(message: str, configured_namespaces: list[str]) -> str | None:
    text = str(message or "")
    for pattern in _ENV_AGENT_NAMESPACE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    lowered = text.lower()
    for namespace in configured_namespaces:
        if re.search(rf"(^|[^a-z0-9-]){re.escape(namespace.lower())}([^a-z0-9-]|$)", lowered):
            return namespace
    return None


def _infer_env_agent_mode(message: str, requested_mode: str | None) -> str:
    mode = str(requested_mode or "auto").strip()
    if mode in _ENV_AGENT_MODE_VALUES:
        return mode
    text = str(message or "")
    if _ENV_AGENT_REMEDIATION_WORDS.search(text):
        return "approval_gated_remediation"
    if _ENV_AGENT_PROPOSAL_WORDS.search(text):
        return "propose_only"
    return "diagnostic_only"


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
    memory_service = MemoryService(repository=repository, settings=settings)
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

    mop_execution_agent = MopExecutionAgentClient(settings)
    mop_execution_store = MopExecutionRunStore(repository=repository, settings=settings)
    env_k8s_inspector = EnvAgentK8sInspectorTool(settings)
    env_helm_manager = EnvAgentHelmManagerTool(settings)
    env_data_ingestion = EnvAgentDataIngestionTool(settings)
    env_observability = EnvAgentObservabilityTool(settings)
    env_agent_workflow_graph = EnvAgentWorkflowGraph(
        settings=settings,
        llm=llm,
        k8s_inspector=env_k8s_inspector,
        helm_manager=env_helm_manager,
        data_ingestion=env_data_ingestion,
        observability=env_observability,
        repository=repository,
    )

    mop_execution_preflight = MopExecutionPreflightService(
        repository=repository, artifact_service=artifact_service, settings=settings
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings)
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
    app.state.memory_service = memory_service
    app.state.graph = graph
    app.state.release_note_graph = release_note_graph
    app.state.mop_generation_graph = mop_generation_graph
    app.state.mop_execution_agent = mop_execution_agent
    app.state.mop_execution_store = mop_execution_store
    app.state.mop_execution_preflight = mop_execution_preflight
    app.state.env_k8s_inspector = env_k8s_inspector
    app.state.env_helm_manager = env_helm_manager
    app.state.env_data_ingestion = env_data_ingestion
    app.state.env_observability = env_observability
    app.state.env_agent_workflow_graph = env_agent_workflow_graph
    app.state.workflow_classifier = workflow_classifier
    app.state.repo_analyzer = repo_analyzer

    digital_twin_adapter_mode = "browser_fixture"
    if settings.digital_twin_real_core_enabled:
        install_digital_twin_gateway(app, settings=settings, llm=llm, database=database)
        digital_twin_adapter_mode = "real_core"
    elif settings.digital_twin_mock_effective_enabled:
        install_digital_twin_mock(
            app,
            enabled=True,
            default_delay_ms=settings.digital_twin_mock_delay_ms,
        )
        digital_twin_adapter_mode = "mock_server"

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

    def env_agent_first_remediation_action(remediation: dict) -> dict | None:
        for action in remediation.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("action_type") or "").strip().lower()
            if action_type and action_type not in {"none", "block", "ask_clarification"}:
                return action
        return None

    def env_agent_remediation_tool_request(
        *,
        run_id: str,
        user_id: str,
        namespace: str | None,
        action: dict,
    ) -> ToolExecutionRequest:
        action_type = str(action.get("action_type") or "").strip().lower()
        mapping = {
            "rollout_restart": ("env.k8s_rollout_restart", "rollout_restart", "env.k8s_inspector"),
            "scale": ("env.k8s_scale", "scale", "env.k8s_inspector"),
            "patch": ("env.k8s_patch", "patch", "env.k8s_inspector"),
            "apply": ("env.k8s_apply", "apply", "env.k8s_inspector"),
            "delete": ("env.k8s_delete", "delete", "env.k8s_inspector"),
            "helm_install": ("env.helm_install", "helm_install", "env.helm_manager"),
            "helm_upgrade": ("env.helm_upgrade", "helm_upgrade", "env.helm_manager"),
            "helm_uninstall": ("env.helm_uninstall", "helm_uninstall", "env.helm_manager"),
            "helm_rollback": ("env.helm_rollback", "helm_rollback", "env.helm_manager"),
        }
        if action_type not in mapping:
            raise ValueError(f"Unsupported Environment Chat remediation action: {action_type or 'missing'}")
        logical_tool, route_tool, adapter_tool = mapping[action_type]
        arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        arguments = dict(arguments)
        target_kind = str(action.get("target_kind") or arguments.get("resource_kind") or arguments.get("kind") or "deployment").strip().lower()
        target_name = str(action.get("target_name") or arguments.get("resource_name") or arguments.get("name") or arguments.get("release_name") or "").strip()
        action_namespace = str(action.get("namespace") or arguments.get("namespace") or namespace or "").strip()
        if not action_namespace:
            raise ValueError("Remediation requires a concrete namespace in the prompt or proposed action.")
        if namespace and action_namespace != namespace:
            raise ValueError("Remediation action namespace does not match the selected Environment Chat namespace.")
        if action_namespace in {"*", "all", "all-namespaces", "cluster"}:
            raise ValueError("Cluster-wide remediation is outside the Environment Chat ODD.")
        rendered_args = json.dumps(arguments, default=str).lower()
        if "secret" in rendered_args and any(word in rendered_args for word in ("get", "list", "read")):
            raise ValueError("Secret-reading remediation is blocked.")
        if any(word in rendered_args for word in ("delete namespace", "remove namespace", "kubectl delete namespace", "clusterrole", "clusterrolebinding", "customresourcedefinition", "--all-namespaces")):
            raise ValueError("Cluster-wide or namespace deletion remediation is blocked.")
        arguments["namespace"] = action_namespace
        if action_type in {"rollout_restart", "scale", "patch", "delete"}:
            arguments.setdefault("resource_kind", target_kind or "deployment")
            arguments.setdefault("resource_name", target_name)
            if not arguments.get("resource_name") or str(arguments.get("resource_name")).lower() in {"unknown", "none"}:
                raise ValueError(f"{action_type} requires a concrete Kubernetes resource name.")
        if action_type == "scale" and "replicas" not in arguments:
            raise ValueError("Scale remediation requires an explicit replica count.")
        if action_type == "patch" and "patch" not in arguments:
            raise ValueError("Patch remediation requires an explicit patch document.")
        if action_type == "apply" and not arguments.get("manifest"):
            raise ValueError("Apply remediation requires an explicit manifest document.")
        if action_type in {"apply", "delete"} and target_kind in {"namespace", "clusterrole", "clusterrolebinding", "customresourcedefinition", "crd"}:
            raise ValueError("Cluster-wide Kubernetes mutations are blocked in Environment Chat.")
        if action_type in {"helm_install", "helm_upgrade"}:
            arguments.setdefault("release_name", target_name or "nginx")
            if not arguments.get("release_name") or str(arguments.get("release_name")).lower() in {"unknown", "none"}:
                raise ValueError("Helm install or upgrade requires a concrete release name.")
            if not arguments.get("chart_ref"):
                raise ValueError("Helm install or upgrade requires a chart_ref, for example bitnami/nginx.")
        if action_type in {"helm_rollback", "helm_uninstall"}:
            arguments.setdefault("release_name", target_name)
            if not arguments.get("release_name") or str(arguments.get("release_name")).lower() in {"unknown", "none"}:
                raise ValueError("Helm rollback or uninstall requires a concrete release name.")
        return ToolExecutionRequest(
            run_id=run_id,
            step_id=f"env_remediation_{action_type}",
            tool_name=logical_tool,
            workflow_type="env_agent",
            environment="kubernetes_generic",
            namespace=action_namespace,
            user_id=user_id,
            arguments={
                "tool_name": route_tool,
                "adapter_tool": adapter_tool,
                "action": action_type,
                "operation": action_type,
                "resource": arguments.get("resource_name") or arguments.get("release_name") or target_name,
                "kind": arguments.get("resource_kind") or target_kind,
                "arguments": arguments,
                "rollback_plan": action.get("rollback_plan") or [],
                "verification_plan": action.get("verification_plan") or [],
            },
            autonomy_mode="approval_gated_remediation",
        )

    def env_agent_create_remediation_approval(
        *,
        run_id: str,
        principal: SessionPrincipal,
        request: ToolExecutionRequest,
        decision: Any,
        remediation: dict,
        action: dict,
    ) -> dict:
        return repository.create_approval_request(
            approval_id=f"appr_{uuid4().hex}",
            run_id=run_id,
            requested_by_user_id=principal.user_id,
            workflow_type=request.workflow_type,
            tool_name=request.tool_name,
            environment=request.environment,
            namespace=request.namespace,
            risk_level=decision.risk_level,
            request_json=request.model_dump(),
            policy_decision=decision.to_dict(),
            expected_impact=decision.expected_impact,
            rollback_note=decision.rollback_note,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.approval_expiration_minutes),
        )

    def env_agent_adapter_request(tool_request: ToolExecutionRequest) -> tuple[Any, ToolExecutionRequest]:
        adapter_tool = str(tool_request.arguments.get("adapter_tool") or "")
        route_tool = str(tool_request.arguments.get("tool_name") or "")
        arguments = tool_request.arguments.get("arguments") if isinstance(tool_request.arguments.get("arguments"), dict) else {}
        if adapter_tool == "env.helm_manager" or tool_request.tool_name in {"env.helm_install", "env.helm_upgrade", "env.helm_uninstall", "env.helm_rollback"}:
            adapter = env_helm_manager
            adapter_name = "env.helm_manager"
        else:
            adapter = env_k8s_inspector
            adapter_name = "env.k8s_inspector"
        return adapter, ToolExecutionRequest(
            run_id=tool_request.run_id,
            step_id=f"{tool_request.step_id}_adapter",
            tool_name=adapter_name,
            workflow_type=tool_request.workflow_type,
            environment=tool_request.environment,
            namespace=tool_request.namespace,
            user_id=tool_request.user_id,
            arguments={"tool_name": route_tool, "arguments": dict(arguments)},
            autonomy_mode="approved_execution",
        )

    def env_agent_verification_request(tool_request: ToolExecutionRequest) -> tuple[Any, ToolExecutionRequest]:
        arguments = tool_request.arguments.get("arguments") if isinstance(tool_request.arguments.get("arguments"), dict) else {}
        if tool_request.tool_name in {"env.helm_install", "env.helm_upgrade", "env.helm_uninstall", "env.helm_rollback"}:
            adapter = env_helm_manager
            request = ToolExecutionRequest(
                run_id=tool_request.run_id,
                step_id=f"{tool_request.step_id}_verify",
                tool_name="env.helm_manager",
                workflow_type=tool_request.workflow_type,
                environment=tool_request.environment,
                namespace=tool_request.namespace,
                user_id=tool_request.user_id,
                arguments={"tool_name": "helm_release_list" if tool_request.tool_name == "env.helm_uninstall" else "helm_release_status", "arguments": {"release_name": arguments.get("release_name"), "namespace": tool_request.namespace}},
                autonomy_mode="observe_only",
            )
        else:
            adapter = env_k8s_inspector
            request = ToolExecutionRequest(
                run_id=tool_request.run_id,
                step_id=f"{tool_request.step_id}_verify",
                tool_name="env.k8s_inspector",
                workflow_type=tool_request.workflow_type,
                environment=tool_request.environment,
                namespace=tool_request.namespace,
                user_id=tool_request.user_id,
                arguments={"tool_name": "deployment_status", "arguments": {"namespace": tool_request.namespace}},
                autonomy_mode="observe_only",
            )
        return adapter, request
    def env_agent_result_text(result: ToolExecutionResult) -> str:
        chunks = [json.dumps(result.error or {}, default=str), json.dumps(result.validation_result or {}, default=str), json.dumps(result.output or {}, default=str)]
        return " ".join(chunks).lower()

    def env_agent_repo_missing(result: ToolExecutionResult) -> bool:
        text = env_agent_result_text(result)
        return "repo" in text and "not found" in text

    def env_agent_request_with_arguments(base_request: ToolExecutionRequest, *, step_suffix: str, route_tool: str, arguments: dict, autonomy_mode: str = "approved_execution") -> ToolExecutionRequest:
        return ToolExecutionRequest(
            run_id=base_request.run_id,
            step_id=f"{base_request.step_id}_{step_suffix}",
            tool_name=base_request.tool_name,
            workflow_type=base_request.workflow_type,
            environment=base_request.environment,
            namespace=base_request.namespace,
            user_id=base_request.user_id,
            arguments={"tool_name": route_tool, "arguments": dict(arguments)},
            autonomy_mode=autonomy_mode,
        )

    async def env_agent_run_adapter_attempt(
        *,
        run_id: str,
        attempt_label: str,
        adapter: Any,
        adapter_request: ToolExecutionRequest,
        logical_tool_name: str,
    ) -> tuple[ToolExecutionResult, int, dict]:
        result, duration_ms = await adapter.execute(adapter_request)
        attempt = {
            "label": attempt_label,
            "tool_name": logical_tool_name,
            "adapter_tool_name": adapter_request.tool_name,
            "route_tool_name": adapter_request.arguments.get("tool_name"),
            "status": result.status,
            "duration_ms": duration_ms,
            "request": adapter_request.model_dump(),
            "result": result.model_dump(),
        }
        repository.add_tool_call(
            run_id=run_id,
            tool_name=f"{logical_tool_name}.{attempt_label}",
            status=result.status,
            request_json=adapter_request.model_dump(),
            response_json={"duration_ms": duration_ms, "result": result.model_dump()},
        )
        repository.add_event(
            run_id,
            "remediation_operator_attempt",
            f"Environment Chat operator attempt: {attempt_label}",
            redact(attempt),
        )
        return result, duration_ms, attempt

    def env_agent_oci_fallback_args(arguments: dict) -> dict | None:
        chart_ref = str(arguments.get("chart_ref") or "")
        explicit = str(arguments.get("oci_fallback_chart_ref") or "")
        if explicit:
            updated = dict(arguments)
            updated["chart_ref"] = explicit
            updated["fallback_source_chart_ref"] = chart_ref
            return updated
        if chart_ref.startswith("bitnami/") and "/" in chart_ref:
            updated = dict(arguments)
            updated["chart_ref"] = f"oci://registry-1.docker.io/bitnamicharts/{chart_ref.rsplit('/', 1)[-1]}"
            updated["fallback_source_chart_ref"] = chart_ref
            return updated
        return None

    async def env_agent_execute_with_operator_logic(tool_request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int, list[dict]]:
        adapter, adapter_request = env_agent_adapter_request(tool_request)
        attempts: list[dict] = []
        route_tool = str(adapter_request.arguments.get("tool_name") or "")
        arguments = adapter_request.arguments.get("arguments") if isinstance(adapter_request.arguments.get("arguments"), dict) else {}
        helm_tools = {"env.helm_install", "env.helm_upgrade", "env.helm_uninstall", "env.helm_rollback"}
        if tool_request.tool_name not in helm_tools:
            result, duration_ms, attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="execute", adapter=adapter, adapter_request=adapter_request, logical_tool_name=tool_request.tool_name)
            return result, duration_ms, [attempt]

        dry_args = dict(arguments)
        dry_args["dry_run"] = True
        dry_request = env_agent_request_with_arguments(adapter_request, step_suffix="dry_run", route_tool=route_tool, arguments=dry_args, autonomy_mode="dry_run")
        dry_result, dry_duration_ms, dry_attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="dry_run", adapter=adapter, adapter_request=dry_request, logical_tool_name=tool_request.tool_name)
        attempts.append(dry_attempt)
        final_args = dict(arguments)

        if dry_result.status != "success" and env_agent_repo_missing(dry_result):
            public_repo = arguments.get("public_repo") if isinstance(arguments.get("public_repo"), dict) else {}
            if public_repo.get("name") and public_repo.get("url"):
                repo_add_request = env_agent_request_with_arguments(
                    adapter_request,
                    step_suffix="repo_add",
                    route_tool="helm_repo_add",
                    arguments={"name": public_repo["name"], "url": public_repo["url"], "force_update": True, "actor": "esda"},
                )
                repo_add_result, _, repo_add_attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="repo_add", adapter=adapter, adapter_request=repo_add_request, logical_tool_name=tool_request.tool_name)
                attempts.append(repo_add_attempt)
                if repo_add_result.status == "success":
                    repo_update_request = env_agent_request_with_arguments(adapter_request, step_suffix="repo_update", route_tool="helm_repo_update", arguments={"actor": "esda"})
                    _, _, repo_update_attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="repo_update", adapter=adapter, adapter_request=repo_update_request, logical_tool_name=tool_request.tool_name)
                    attempts.append(repo_update_attempt)
                    dry_result, dry_duration_ms, dry_attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="dry_run_after_repo_update", adapter=adapter, adapter_request=dry_request, logical_tool_name=tool_request.tool_name)
                    attempts.append(dry_attempt)

        if dry_result.status != "success" and env_agent_repo_missing(dry_result):
            fallback_args = env_agent_oci_fallback_args(arguments)
            if fallback_args:
                final_args = fallback_args
                fallback_dry_args = dict(fallback_args)
                fallback_dry_args["dry_run"] = True
                fallback_dry_request = env_agent_request_with_arguments(adapter_request, step_suffix="oci_dry_run", route_tool=route_tool, arguments=fallback_dry_args, autonomy_mode="dry_run")
                dry_result, dry_duration_ms, dry_attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="oci_dry_run", adapter=adapter, adapter_request=fallback_dry_request, logical_tool_name=tool_request.tool_name)
                attempts.append(dry_attempt)

        if dry_result.status != "success":
            return dry_result, dry_duration_ms, attempts

        execute_args = dict(final_args)
        execute_args["dry_run"] = False
        execute_request = env_agent_request_with_arguments(adapter_request, step_suffix="execute", route_tool=route_tool, arguments=execute_args)
        execution_result, execution_duration_ms, execution_attempt = await env_agent_run_adapter_attempt(run_id=tool_request.run_id, attempt_label="execute", adapter=adapter, adapter_request=execute_request, logical_tool_name=tool_request.tool_name)
        attempts.append(execution_attempt)
        return execution_result, execution_duration_ms, attempts
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

    @app.get("/mop-execution", response_class=HTMLResponse, tags=["pages"])
    def mop_execution_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "mop_execution.html",
            template_context(
                principal,
                target_namespaces=settings.mop_execution_allowed_target_namespace_list,
                default_target_namespace=settings.mop_execution_default_target_namespace,
                generated_name_prefix=settings.mop_execution_generated_name_prefix,
            ),
        )

    @app.get("/digital-twins", response_class=HTMLResponse, tags=["pages"])
    def digital_twins_page(request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "digital_twins.html",
            template_context(
                principal,
                digital_twin_adapter_mode=digital_twin_adapter_mode,
            ),
        )

    @app.get("/digital-twins/{twin_id}", response_class=HTMLResponse, tags=["pages"])
    def digital_twin_detail_page(twin_id: str, request: Request) -> HTMLResponse:
        principal = get_current_user_or_none(request)
        if not principal:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "digital_twin_detail.html",
            template_context(
                principal,
                twin_id=twin_id,
                digital_twin_adapter_mode=digital_twin_adapter_mode,
            ),
        )

    def env_agent_admin_principal(principal: SessionPrincipal | None = None) -> SessionPrincipal:
        if principal:
            return principal
        with database.session() as db:
            user = db.scalar(select(User).where(User.username == settings.admin_username))
            if not user or not user.is_active:
                raise HTTPException(status_code=401, detail="Environment Chat admin user is not available")
            return SessionPrincipal(user_id=user.user_id, username=user.username, roles=list(user.roles or []))


    @app.get("/env-agent", response_class=HTMLResponse, tags=["pages"])
    def env_agent_page(request: Request) -> HTMLResponse:
        principal = env_agent_admin_principal(get_current_user_or_none(request))
        contract = env_agent_contract(settings)
        return templates.TemplateResponse(
            request,
            "env_agent.html",
            template_context(principal, env_agent_contract=contract),
        )

    @app.get("/api/env-agent/contract", tags=["workflows"])
    def env_agent_contract_api(principal: SessionPrincipal | None = Depends(get_current_user_or_none)) -> dict:
        principal = env_agent_admin_principal(principal)
        contract = env_agent_contract(settings)
        contract["user"] = public_user(principal)
        return contract

    @app.get("/api/env-agent/sessions", tags=["workflows"])
    def list_env_agent_sessions(
        include_hidden: bool = Query(False),
        principal: SessionPrincipal | None = Depends(get_current_user_or_none),
    ) -> dict:
        principal = env_agent_admin_principal(principal)
        return {
            "sessions": repository.list_chat_sessions(
                user_id=principal.user_id,
                workflow_type="env_agent",
                include_hidden=include_hidden,
            )
        }

    @app.get("/api/env-agent/sessions/{session_id}", tags=["workflows"])
    def get_env_agent_session(
        session_id: str,
        principal: SessionPrincipal | None = Depends(get_current_user_or_none),
    ) -> dict:
        principal = env_agent_admin_principal(principal)
        snapshot = repository.get_chat_session_snapshot(
            session_id=session_id,
            user_id=principal.user_id,
            workflow_type="env_agent",
        )
        if not snapshot:
            raise HTTPException(status_code=404, detail="Environment Chat session not found")
        snapshot["memory_context"] = memory_service.env_agent_context(
            user_id=principal.user_id,
            session_id=session_id,
        )
        return snapshot

    @app.post("/api/env-agent/chat", tags=["workflows"])
    async def env_agent_chat(
        request: EnvAgentChatRequest,
        principal: SessionPrincipal | None = Depends(get_current_user_or_none),
    ) -> dict:
        principal = env_agent_admin_principal(principal)
        contract = env_agent_contract(settings)
        allowed_namespaces = contract["namespaces"]
        requested_session_id = (request.session_id or "").strip() or None
        session_exists = False
        if requested_session_id:
            session = repository.get_chat_session(session_id=requested_session_id, user_id=principal.user_id)
            if not session:
                raise HTTPException(status_code=404, detail="Environment Chat session not found")
            chat_session_id = requested_session_id
            session_exists = True
        else:
            chat_session_id = f"chat_{uuid4().hex}"

        memory_context = memory_service.env_agent_context(
            user_id=principal.user_id,
            session_id=chat_session_id if session_exists else None,
        )
        namespace = (
            request.namespace
            or _infer_env_agent_namespace(request.message, allowed_namespaces)
            or memory_context.get("latest_namespace")
            or ""
        ).strip() or None
        mode = _infer_env_agent_mode(request.message, request.mode)
        scope = (request.scope or "prompt").strip() or "prompt"
        model_profile = request.model_profile or settings.llm_default_model_profile
        run_id = f"env_{uuid4().hex}"
        target_url = f"env-agent://prompt/{quote(namespace or 'runtime', safe='')}"
        if not session_exists:
            repository.create_chat_session(
                session_id=chat_session_id,
                user_id=principal.user_id,
                title=request.message[:120] or f"Environment Chat {namespace or 'prompt'}",
            )
        repository.create_run(
            run_id=run_id,
            user_id=principal.user_id,
            goal=request.message,
            target_url=target_url,
            namespace=namespace,
            workflow_type="env_agent",
        )
        repository.add_chat_message(
            session_id=chat_session_id,
            run_id=run_id,
            role="user",
            content=request.message,
            payload={
                "namespace": namespace,
                "mode": mode,
                "scope": scope,
                "model_profile": model_profile,
                "memory_context_loaded": bool(session_exists),
            },
        )
        repository.add_event(
            run_id,
            "run_started",
            "Environment Chat prompt started",
            {
                "workflow_type": "env_agent",
                "chat_session_id": chat_session_id,
                "session_continuation": session_exists,
                "namespace": namespace,
                "mode": mode,
                "scope": scope,
                "model_profile": llm.describe_model_profile(model_profile),
                "prompt": request.message,
            },
        )
        repository.add_event(
            run_id,
            "memory_context_loaded",
            "Environment Chat memory context loaded",
            {
                "session_id": chat_session_id,
                "short_term_provider": memory_context.get("short_term", {}).get("provider"),
                "short_term_message_count": memory_context.get("short_term", {}).get("message_count", 0),
                "langmem_enabled": memory_context.get("short_term", {}).get("langmem_enabled"),
                "langmem_available": memory_context.get("short_term", {}).get("langmem_available"),
                "long_term_provider": memory_context.get("long_term", {}).get("provider"),
                "long_term_memory_count": memory_context.get("long_term", {}).get("memory_count", 0),
                "latest_namespace": memory_context.get("latest_namespace"),
            },
        )
        state: dict[str, Any] = {}
        final_report = "Environment Chat completed."
        status = "needs_review"
        try:
            state = await env_agent_workflow_graph.run(
                EnvAgentRuntimeInput(
                    run_id=run_id,
                    user_id=principal.user_id,
                    user_text=request.message,
                    namespace=namespace,
                    mode=mode,
                    model_profile=model_profile,
                    user_roles=list(principal.roles),
                    execute_tools=True,
                )
            )
            _add_env_agent_state_events(repository, run_id=run_id, state=state)
            final_report = str(state.get("final_report") or "Environment Chat completed.")
            status = str(state.get("status") or "needs_review")
            remediation = state.get("remediation") or {}
            approval_payload = None
            if mode == "approval_gated_remediation" and remediation.get("approval_required"):
                try:
                    action = env_agent_first_remediation_action(remediation)
                    if not action:
                        raise ValueError("No executable remediation action was produced.")
                    tool_request = env_agent_remediation_tool_request(
                        run_id=run_id,
                        user_id=principal.user_id,
                        namespace=namespace,
                        action=action,
                    )
                    decision = policy_guard.evaluate_tool(tool_request, user_roles=principal.roles)
                    if decision.decision == "deny":
                        status = "blocked"
                        approval_payload = {
                            "status": "blocked",
                            "reason": "Policy denied the proposed remediation action.",
                            "decision": decision.to_dict(),
                            "request": tool_request.model_dump(),
                            "action": action,
                        }
                    else:
                        approval = env_agent_create_remediation_approval(
                            run_id=run_id,
                            principal=principal,
                            request=tool_request,
                            decision=decision,
                            remediation=remediation,
                            action=action,
                        )
                        approval_payload = {
                            "status": approval["status"],
                            "approval": approval,
                            "decision": decision.to_dict(),
                            "request": tool_request.model_dump(),
                            "action": action,
                            "proposal": remediation,
                        }
                        status = "proposal_ready"
                    repository.add_event(
                        run_id,
                        "remediation_approval_requested",
                        "Environment Chat remediation approval gate prepared",
                        approval_payload,
                    )
                    final_report += (
                        "\n\n## Approval Gate\n"
                        f"- Status: `{approval_payload['status']}`\n"
                        f"- Tool: `{approval_payload.get('request', {}).get('tool_name', 'n/a')}`\n"
                        "- Execution remains blocked until a human approves and submits the typed remediation action."
                    )
                except ValueError as exc:
                    status = "blocked"
                    approval_payload = {"status": "blocked", "reason": str(exc), "proposal": remediation}
                    repository.add_event(
                        run_id,
                        "remediation_approval_blocked",
                        "Environment Chat remediation approval gate blocked",
                        approval_payload,
                    )
                    final_report += f"\n\n## Approval Gate\n- Status: `blocked`\n- Reason: {exc}"
            raw_final_report = final_report
            formatted_answer = await llm.env_agent_present_answer(
                user_text=request.message,
                raw_report=raw_final_report,
                state=state | {"memory_context": memory_context},
                model_profile="azure_gpt5_pro",
            )
            final_report = str(formatted_answer.get("markdown") or raw_final_report)
            repository.add_event(
                run_id,
                "answer_formatted",
                "Environment Chat answer formatted for Environment Chat",
                {
                    "formatter": formatted_answer,
                    "raw_report_preview": raw_final_report[:2000],
                },
            )
            terminal_event = "run_completed" if status in {"completed", "proposal_ready"} else "run_needs_review"
            repository.update_status(run_id, status, final_report=final_report)
            repository.add_chat_message(
                session_id=chat_session_id,
                run_id=run_id,
                role="assistant",
                content=final_report,
                payload={
                    "status": status,
                    "formatted_by": formatted_answer.get("formatter"),
                    "formatter_model_profile": formatted_answer.get("model_profile"),
                    "safe_reasoning_summaries": state.get("safe_summaries") or [],
                    "evidence_count": len(state.get("evidence") or []),
                },
            )
            repository.add_event(
                run_id,
                terminal_event,
                f"Environment Chat finished with status {status}",
                {
                    "status": status,
                    "final_report": final_report,
                    "evidence_count": len(state.get("evidence") or []),
                    "safe_reasoning_summaries": state.get("safe_summaries") or [],
                },
            )
        except Exception as exc:
            logger.exception("env_agent_failed run_id=%s", run_id)
            final_report = f"Environment Chat failed: {exc}"
            status = "failed"
            repository.update_status(run_id, status, final_report=final_report)
            repository.add_chat_message(
                session_id=chat_session_id,
                run_id=run_id,
                role="assistant",
                content=final_report,
                payload={"status": status, "error": str(exc)},
            )
            repository.add_event(
                run_id,
                "run_failed",
                "Environment Chat failed",
                {"status": status, "error": str(exc)},
            )

        memory_update = memory_service.remember_env_agent_turn(
            user_id=principal.user_id,
            session_id=chat_session_id,
            run_id=run_id,
            namespace=namespace,
            user_text=request.message,
            assistant_text=final_report,
            status=status,
            state=state,
        )
        repository.add_event(
            run_id,
            "memory_updated",
            "Environment Chat memory updated",
            {
                "session_id": chat_session_id,
                "short_term_provider": memory_context.get("short_term", {}).get("provider"),
                "long_term_provider": "postgres",
                "long_term_memory_id": memory_update.get("long_term", {}).get("memory_id"),
            },
        )
        snapshot = repository.get_run_snapshot(run_id) or {}
        snapshot["events_url"] = f"/api/runs/{run_id}/events"
        snapshot["chat_session_id"] = chat_session_id
        snapshot["memory_context"] = memory_service.env_agent_context(
            user_id=principal.user_id,
            session_id=chat_session_id,
        )
        return {
            "run_id": run_id,
            "chat_session_id": chat_session_id,
            "status": status,
            "answer": final_report,
            "events_url": f"/api/runs/{run_id}/events",
            "snapshot": snapshot,
        }

    @app.post("/api/env-agent/remediation/execute", tags=["workflows"])
    async def env_agent_remediation_execute(
        request: EnvAgentRemediationExecuteRequest,
        principal: SessionPrincipal | None = Depends(get_current_user_or_none),
    ) -> dict:
        principal = env_agent_admin_principal(principal)
        approval = approval_service.get_request(request.approval_id)
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")
        if approval.get("run_id") != request.run_id:
            raise HTTPException(status_code=409, detail="Approval does not belong to the selected Environment Chat run.")
        if approval.get("status") != "approved":
            raise HTTPException(status_code=409, detail=f"Approval is {approval.get('status')}; approve it before execution.")
        tool_request = ToolExecutionRequest(**approval["request"])
        if tool_request.workflow_type != "env_agent":
            raise HTTPException(status_code=422, detail="Approval is not for an Environment Chat remediation.")
        decision = policy_guard.evaluate_tool(tool_request, user_roles=principal.roles)
        if decision.decision == "deny":
            repository.add_event(
                request.run_id,
                "remediation_execution_blocked",
                "Environment Chat remediation execution blocked by policy recheck",
                {"approval": approval, "decision": decision.to_dict(), "request": tool_request.model_dump()},
            )
            repository.update_status(request.run_id, "blocked", final_report="Approved Environment Chat remediation was blocked by policy recheck.")
            snapshot = repository.get_run_snapshot(request.run_id) or {}
            raise HTTPException(status_code=403, detail={"message": "Policy recheck denied the approved remediation.", "snapshot": snapshot})

        repository.add_event(
            request.run_id,
            "remediation_approval_confirmed",
            "Environment Chat remediation approval confirmed",
            {"approval": approval, "decision": decision.to_dict(), "request": tool_request.model_dump()},
        )
        repository.add_event(
            request.run_id,
            "remediation_execution_started",
            "Environment Chat approved remediation execution started",
            {"tool_name": tool_request.tool_name, "arguments": redact(tool_request.arguments), "approval_id": request.approval_id},
        )

        adapter, adapter_request = env_agent_adapter_request(tool_request)
        execution_result, duration_ms, operator_attempts = await env_agent_execute_with_operator_logic(tool_request)
        repository.add_tool_call(
            run_id=request.run_id,
            tool_name=tool_request.tool_name,
            status=execution_result.status,
            request_json=tool_request.model_dump(),
            response_json={"duration_ms": duration_ms, "operator_attempts": operator_attempts, "result": execution_result.model_dump()},
        )
        repository.add_event(
            request.run_id,
            "remediation_action_executed",
            "Environment Chat typed remediation action executed",
            {
                "tool_name": tool_request.tool_name,
                "adapter_tool_name": adapter_request.tool_name,
                "status": execution_result.status,
                "duration_ms": duration_ms,
                "operator_attempts": operator_attempts,
                "result": execution_result.model_dump(),
            },
        )

        verification_result = None
        verification_duration_ms = 0
        if execution_result.status == "success":
            verify_adapter, verify_request = env_agent_verification_request(tool_request)
            verification_result, verification_duration_ms = await verify_adapter.execute(verify_request)
            repository.add_tool_call(
                run_id=request.run_id,
                tool_name=f"{tool_request.tool_name}.verify",
                status=verification_result.status,
                request_json=verify_request.model_dump(),
                response_json={"duration_ms": verification_duration_ms, "result": verification_result.model_dump()},
            )
            repository.add_event(
                request.run_id,
                "remediation_verified",
                "Environment Chat remediation verification completed",
                {
                    "status": verification_result.status,
                    "duration_ms": verification_duration_ms,
                    "result": verification_result.model_dump(),
                },
            )

        verification_status = verification_result.status if verification_result else "skipped"
        if execution_result.status == "success" and verification_status in {"success", "skipped"}:
            status = "completed"
            terminal_event = "run_completed"
            summary = "Approved Environment Chat remediation executed and post-action verification completed."
        elif execution_result.status == "success":
            status = "needs_review"
            terminal_event = "run_needs_review"
            summary = "Approved Environment Chat remediation executed, but verification needs review."
        else:
            status = "needs_review"
            terminal_event = "run_needs_review"
            summary = "Approved Environment Chat remediation did not complete successfully; no blind retry was attempted."

        operator_attempt_labels = [str(attempt.get("label") or "unknown") for attempt in operator_attempts]
        final_report = (
            f"# Environment Chat Remediation: {status}\n\n"
            f"- Run ID: `{request.run_id}`\n"
            f"- Approval ID: `{request.approval_id}`\n"
            f"- Tool: `{tool_request.tool_name}`\n"
            f"- Namespace: `{tool_request.namespace}`\n"
            f"- Execution status: `{execution_result.status}`\n"
            f"- Verification status: `{verification_status}`\n"
            f"- Operator attempts: `{', '.join(operator_attempt_labels) or 'none'}`\n\n"
            f"## Summary\n{summary}\n\n"
            "## Guardrails\n"
            "- Executed through a typed MCP tool only.\n"
            "- No raw shell or unapproved action was used.\n"
            "- Verification used a read-only follow-up MCP call when execution succeeded."
        )
        repository.add_event(
            request.run_id,
            "safe_reasoning_summary",
            "Safe Environment Chat summary: remediation execution",
            {
                "stage": "execute",
                "stage_label": "Remediation Execution",
                "reasoning_summary": summary,
                "execution_status": execution_result.status,
                "verification_status": verification_status,
            },
        )
        repository.update_status(request.run_id, status, final_report=final_report)
        repository.add_event(
            request.run_id,
            terminal_event,
            f"Environment Chat remediation finished with status {status}",
            {
                "status": status,
                "final_report": final_report,
                "execution_status": execution_result.status,
                "verification_status": verification_status,
            },
        )
        snapshot = repository.get_run_snapshot(request.run_id) or {}
        snapshot["events_url"] = f"/api/runs/{request.run_id}/events"
        return {
            "run_id": request.run_id,
            "approval_id": request.approval_id,
            "status": status,
            "summary": summary,
            "execution": execution_result.model_dump(),
            "verification": verification_result.model_dump() if verification_result else None,
            "operator_attempts": operator_attempts,
            "snapshot": snapshot,
        }
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
        principal: SessionPrincipal | None = Depends(get_current_user_or_none),
    ) -> dict:
        existing_approval = approval_service.get_request(approval_id)
        if existing_approval and existing_approval.get("workflow_type") == "env_agent":
            principal = env_agent_admin_principal(principal)
        if principal is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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

    @app.get("/api/mop-execution/bundles", tags=["workflows"])
    def list_mop_execution_bundles(
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        return {
            "bundles": mop_execution_preflight.bundle_candidates(user_id=principal.user_id),
            "target_namespaces": mop_execution_store.allowed_target_namespaces(),
            "default_target_namespace": settings.mop_execution_default_target_namespace,
            "generated_name_prefix": settings.mop_execution_generated_name_prefix,
            "source": "local_mop_generation_runs",
        }

    @app.post("/api/mop-execution/preflight", tags=["workflows"])
    def preflight_mop_execution_bundle(
        request: MopExecutionPreflightRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        try:
            target_namespace = mop_execution_store.normalize_target_namespace(request.target_namespace)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "allowed_target_namespaces": mop_execution_store.allowed_target_namespaces(),
                },
            ) from exc
        if request.source_type == "activity_run":
            if not request.run_id:
                raise HTTPException(status_code=422, detail="run_id is required for Activity run preflight")
            return mop_execution_preflight.preflight_activity_run(
                user_id=principal.user_id,
                run_id=request.run_id,
                artifact_id=request.artifact_id,
                target_namespace=target_namespace,
            )
        if not request.folder_name:
            raise HTTPException(status_code=422, detail="folder_name is required for artifact repo folder preflight")
        return mop_execution_preflight.preflight_artifact_repo_folder(
            user_id=principal.user_id,
            folder_name=request.folder_name,
            target_namespace=target_namespace,
        )

    @app.post("/api/mop-execution/preflight/upload", tags=["workflows"])
    async def preflight_uploaded_mop_execution_bundle(
        target_namespace: str = Form(...),
        file: UploadFile = File(...),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        try:
            normalized_target = mop_execution_store.normalize_target_namespace(target_namespace)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "allowed_target_namespaces": mop_execution_store.allowed_target_namespaces(),
                },
            ) from exc
        content = await file.read()
        if not content:
            raise HTTPException(status_code=422, detail="Uploaded MoP bundle is empty")
        return mop_execution_preflight.preflight_bytes(
            content=content,
            filename=file.filename or "mop-bundle.zip",
            source_type="upload_bundle",
            target_namespace=normalized_target,
            source_metadata={"uploaded_filename": file.filename},
        )

    _required_mop_execution_capabilities = {
        "bundle_validation": ("bundle_validation", "validate_bundle", "artifact-bundles", "artifact bundle"),
        "dry_run": ("dry_run", "dry-run", "dryrun"),
        "approval": ("approval", "approvals"),
        "mutation": ("mutation", "mutate", "apply"),
        "validation_reports": ("validation_report", "validation reports", "reports", "report"),
        "rollback": ("rollback",),
        "cleanup": ("cleanup", "revert"),
    }

    def _agent_response_dict(value) -> dict:
        return value if isinstance(value, dict) else {"response": value}

    def _agent_error_payload(exc: Exception) -> dict:
        if isinstance(exc, MopExecutionAgentError):
            return {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "status_code": exc.status_code,
                    "payload": exc.payload,
                }
            }
        return {"error": {"type": type(exc).__name__, "message": str(exc)}}

    def _agent_status_ok(payload) -> bool:
        if not isinstance(payload, dict):
            return True
        status = str(payload.get("status") or payload.get("state") or payload.get("ready") or "ok").lower()
        if payload.get("ok") is False or payload.get("healthy") is False or payload.get("ready") is False:
            return False
        return status not in {"failed", "failure", "error", "not_ready", "unhealthy", "false"}

    def _capability_text(payload) -> str:
        try:
            return json.dumps(payload, default=str).lower()
        except TypeError:
            return str(payload).lower()

    def _missing_agent_capabilities(payload) -> list[str]:
        text = _capability_text(payload)
        return [
            capability
            for capability, aliases in _required_mop_execution_capabilities.items()
            if not any(alias in text for alias in aliases)
        ]

    def _validation_is_ok(payload) -> bool:
        if not isinstance(payload, dict):
            return True
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        bundle = data.get("bundle") if isinstance(data.get("bundle"), dict) else {}
        status = str(
            payload.get("status")
            or payload.get("state")
            or data.get("status")
            or bundle.get("validation_status")
            or "valid"
        ).lower()
        if payload.get("valid") is False or payload.get("ok") is False or data.get("valid") is False:
            return False
        if bundle.get("validation_status") == "invalid" or bundle.get("validation_error"):
            return False
        errors = payload.get("errors") or payload.get("failures") or data.get("errors") or data.get("failures") or []
        return status not in {"failed", "failure", "error", "invalid"} and not errors

    def _validation_errors(payload) -> list[str]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        bundle = data.get("bundle") if isinstance(data.get("bundle"), dict) else {}
        errors = payload.get("errors") or payload.get("failures") or data.get("errors") or data.get("failures") or []
        collected = [str(item) for item in errors]
        if bundle.get("validation_error"):
            collected.append(str(bundle["validation_error"]))
        return collected

    def _validation_warnings(payload) -> list[str]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return [str(item) for item in (payload.get("warnings") or data.get("warnings") or [])]

    def _validation_bundle_id(payload) -> str | None:
        return mop_execution_store._find_key(payload, "bundle_id") or mop_execution_store._find_key(payload, "id")

    _inline_bundle_validation_limit_bytes = 750_000

    def _artifact_repo_web_base() -> str | None:
        repo_url = (settings.artifact_git_repo_url or "").strip()
        if not repo_url:
            return None
        if repo_url.startswith("git@github.com:"):
            path = repo_url.removeprefix("git@github.com:").removesuffix(".git")
            return f"https://github.com/{path}".rstrip("/")
        parsed = urlparse(repo_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        path = parsed.path.removesuffix(".git").strip("/")
        return f"{parsed.scheme}://{parsed.netloc}/{path}".rstrip("/")

    def _artifact_repo_raw_bundle_url(*, folder_name: str | None, filename: str, branch: str | None = None) -> str | None:
        folder = str(folder_name or "").strip().strip("/")
        if not folder:
            return None
        web_base = _artifact_repo_web_base()
        if not web_base:
            return None
        parsed = urlparse(web_base)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            return None
        safe_branch = quote(str(branch or settings.artifact_git_branch or "main"), safe="")
        safe_folder = "/".join(quote(part, safe="") for part in folder.split("/") if part)
        safe_filename = quote(filename or "mop-bundle.zip", safe="")
        return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/{safe_branch}/{safe_folder}/{safe_filename}"

    def _bundle_validation_payload(
        *,
        content: bytes,
        filename: str,
        source_type: str,
        target_namespace: str,
        correlation_id: str,
        execution_mode: str,
        preflight: dict,
        source_metadata: dict,
        include_archive: bool = True,
    ) -> dict:
        bundle = preflight.get("bundle") or {}
        bundle_filename = filename or bundle.get("filename") or source_metadata.get("filename") or "mop-bundle.zip"
        folder_name = source_metadata.get("folder_name") or bundle.get("folder_name")
        branch = source_metadata.get("branch") or bundle.get("branch") or settings.artifact_git_branch
        reference_url = _artifact_repo_raw_bundle_url(folder_name=folder_name, filename=bundle_filename, branch=branch)
        if reference_url:
            source = {"type": "object_store", "value": reference_url}
        else:
            source = {
                "type": "uploaded_archive",
                "original_source_type": source_type,
                "filename": bundle_filename,
                "size_bytes": len(content),
                "sha256": bundle.get("sha256"),
            }
            if include_archive:
                source["archive_base64"] = base64.b64encode(content).decode("ascii")
            else:
                source["archive_base64_omitted"] = True
        return {
            "bundle_id": (preflight.get("metadata") or {}).get("bundle_id") or bundle.get("bundle_id") or bundle.get("sha256"),
            "source": source,
            "source_metadata": {
                "original_source_type": source_type,
                "filename": bundle_filename,
                "size_bytes": len(content),
                "sha256": bundle.get("sha256"),
                "run_id": source_metadata.get("run_id"),
                "artifact_id": source_metadata.get("artifact_id"),
                "folder_name": folder_name,
                "branch": branch,
                "reference": reference_url,
                "reference_type": "artifact_repository_raw_url" if reference_url else None,
                "artifact_repository": {
                    "repo_url": settings.artifact_git_repo_url,
                    "branch": branch,
                    "folder_name": folder_name,
                    "filename": bundle_filename,
                }
                if reference_url
                else None,
            },
            "target_namespace": target_namespace,
            "correlation_id": correlation_id,
            "execution_mode": execution_mode,
            "dry_run_first": True,
            "mutation_allowed": False,
            "requires_approval": True,
            "preflight": {
                "valid": preflight.get("valid"),
                "status": preflight.get("status"),
                "checks": preflight.get("checks") or [],
                "warnings": preflight.get("warnings") or [],
                "failures": preflight.get("failures") or [],
                "metadata": preflight.get("metadata") or {},
                "bundle": bundle,
            },
        }

    def _should_validate_bundle_by_reference(*, content: bytes, preflight: dict, source_metadata: dict) -> bool:
        bundle = preflight.get("bundle") or {}
        folder_name = source_metadata.get("folder_name") or bundle.get("folder_name")
        return bool(folder_name and len(content) > _inline_bundle_validation_limit_bytes)

    _dry_run_success_states = {
        "awaiting_human_approval",
        "dry_run_succeeded",
        "waiting_for_approval",
        "ready_for_approval",
        "approval_required",
        "succeeded",
        "completed",
        "success",
        "complete",
        "reports_available",
    }
    _dry_run_failure_states = {"failed", "failed_safe", "failure", "error", "cancelled", "stopped"}
    _dry_run_pause_states = {"decision_required", "paused"}

    def _agent_find_key(payload, key: str):
        return mop_execution_store._find_key(payload, key) if isinstance(payload, dict) else None

    def _job_id(payload) -> str | None:
        value = _agent_find_key(payload, "job_id") or _agent_find_key(payload, "id")
        return str(value) if value else None

    def _job_state(payload) -> str:
        value = _agent_find_key(payload, "state") or _agent_find_key(payload, "status") or "unknown"
        return str(value).lower()

    def _job_phase(payload) -> str:
        value = _agent_find_key(payload, "current_phase") or _agent_find_key(payload, "phase") or "dry_run"
        return str(value).lower()

    def _normalized_dry_run_state(state: str | None) -> str:
        normalized = str(state or "unknown").lower()
        if normalized in {"succeeded", "completed", "success", "complete"}:
            return "dry_run_succeeded"
        if normalized == "awaiting_human_approval":
            return "waiting_for_approval"
        return normalized

    def _agent_execution_mode_for_request(execution_mode: str | None) -> str:
        requested = str(execution_mode or "").strip().lower()
        if requested in {"approved_mutation", "dry_run_then_approval"}:
            return "execute_after_approval"
        return "dry_run_only"

    def _dry_run_idempotency_key(*, correlation_id: str, bundle_id: str, target_namespace: str) -> str:
        raw = f"{correlation_id}:{bundle_id}:{target_namespace}:dry-run"
        cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-")
        return (cleaned or f"dry-run-{uuid4().hex}")[:160]

    async def _collect_dry_run_observations(agent, job_id: str) -> dict:
        observations: dict = {"job_id": job_id, "phase": "dry_run"}
        try:
            observation_response = await agent.get_observations(job_id, params={"phase": "dry_run"})
            observations["observations"] = observation_response.audit_payload()
        except Exception as exc:
            observations["observations_error"] = _agent_error_payload(exc)
        try:
            audit_response = await agent.get_audit_events(job_id, params={"phase": "dry_run"})
            observations["audit_events"] = audit_response.audit_payload()
        except Exception as exc:
            observations["audit_events_error"] = _agent_error_payload(exc)
        return observations

    def _report_items(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("reports", "items", "data", "results", "artifacts"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if any(key in payload for key in ("report_id", "id", "report_type", "type")):
            return [payload]
        return []

    def _report_id(item: dict) -> str | None:
        value = item.get("report_id") or item.get("id") or item.get("name")
        return str(value) if value else None

    def _report_title(item: dict) -> str:
        return str(item.get("title") or item.get("name") or item.get("report_type") or item.get("type") or "Dry-run report")

    def _report_type(item: dict) -> str:
        return str(item.get("report_type") or item.get("type") or item.get("kind") or "dry_run")

    def _walk_values(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from _walk_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from _walk_values(child)

    def _first_report_value(payloads: list, keys: tuple[str, ...]):
        keyset = {key.lower() for key in keys}
        for payload in payloads:
            for node in _walk_values(payload):
                for key, value in node.items():
                    if key.lower() in keyset and value not in (None, "", [], {}):
                        return value
        return None

    def _string_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (int, float, bool)):
            return [str(value)]
        if isinstance(value, list):
            results: list[str] = []
            for item in value:
                results.extend(_string_list(item))
            return results
        if isinstance(value, dict):
            compact = value.get("message") or value.get("summary") or value.get("name") or value.get("id")
            return [str(compact)] if compact else [json.dumps(mop_execution_store._payload(value), default=str)]
        return [str(value)]

    def _extract_command_fingerprints(payloads: list) -> list[str]:
        fingerprints: list[str] = []
        seen_fingerprints: set[str] = set()
        for payload in payloads:
            for node in _walk_values(payload):
                for key, value in node.items():
                    if "fingerprint" not in key.lower():
                        continue
                    for item in _string_list(value):
                        cleaned = item.strip()
                        if cleaned and cleaned not in seen_fingerprints:
                            seen_fingerprints.add(cleaned)
                            fingerprints.append(cleaned)
        return fingerprints

    def _download_links_for_report(agent, *, run_id: str, job_id: str, report_id: str) -> list[dict]:
        links: list[dict] = []
        for artifact, label in (("markdown", "Markdown"), ("pdf", "PDF"), ("html", "HTML")):
            url = (
                "/api/mop-execution/report-download"
                f"?run_id={quote(run_id, safe='')}"
                f"&job_id={quote(job_id, safe='')}"
                f"&report_id={quote(report_id, safe='')}"
                f"&artifact={quote(artifact, safe='')}"
            )
            links.append({"artifact": artifact, "label": label, "url": url})
        return links

    async def _collect_dry_run_reports(agent, *, run_id: str, job_id: str) -> dict:
        payloads: list = []
        report_records: list[dict] = []
        errors: list[str] = []
        try:
            list_response = await agent.list_reports(job_id)
            list_payload = _agent_response_dict(list_response.payload)
            payloads.append(list_payload)
            items = _report_items(list_payload)
            if not items:
                generate_response = await agent.generate_report(job_id, "dry_run")
                generate_payload = _agent_response_dict(generate_response.payload)
                payloads.append(generate_payload)
                items = _report_items(generate_payload)
            for item in items[:10]:
                report_id = _report_id(item)
                merged = dict(item)
                metadata_payload = None
                if report_id:
                    try:
                        metadata_response = await agent.get_report_metadata(job_id, report_id)
                        metadata_payload = _agent_response_dict(metadata_response.payload)
                        payloads.append(metadata_payload)
                        if isinstance(metadata_payload, dict):
                            merged = {**merged, **metadata_payload}
                    except Exception as exc:
                        errors.append(str(_agent_error_payload(exc).get("error", {}).get("message") or exc))
                report_records.append(
                    {
                        "report_id": report_id,
                        "report_type": _report_type(merged),
                        "title": _report_title(merged),
                        "summary": str(merged.get("summary") or merged.get("description") or "").strip(),
                        "status": str(merged.get("status") or merged.get("state") or "available"),
                        "downloads": _download_links_for_report(agent, run_id=run_id, job_id=job_id, report_id=report_id) if report_id else [],
                        "metadata": mop_execution_store._payload(merged),
                    }
                )
        except Exception as exc:
            errors.append(str(_agent_error_payload(exc).get("error", {}).get("message") or exc))
        summary_value = _first_report_value(payloads, ("summary", "report_summary", "dry_run_summary", "executive_summary"))
        resources_value = _first_report_value(payloads, ("resources", "resource_changes", "would_change", "would_create", "changes", "change_preview"))
        policy_value = _first_report_value(payloads, ("policy_gates", "policy_decisions", "gates", "warnings", "guardrails"))
        warnings_value = _first_report_value(payloads, ("warnings", "warning", "policy_warnings"))
        fingerprints = _extract_command_fingerprints(payloads)
        reports = {
            "status": "available" if report_records or payloads else "unavailable",
            "job_id": job_id,
            "reports": report_records,
            "summary": str(summary_value or "Dry-run report metadata was collected from the MoP Execution Agent.")[:3000],
            "resources": resources_value or [],
            "policy_gates": policy_value or [],
            "warnings": _string_list(warnings_value) + errors,
            "command_fingerprints": fingerprints,
        }
        mop_execution_store.record_reports(run_id=run_id, reports=reports)
        return reports

    def _report_type_matches(value: str | None, wanted: str) -> bool:
        normalized = str(value or "").lower().replace("-", "_").replace(" ", "_")
        target = wanted.lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "execution": {"execution", "execution_report", "mutation", "mutation_report", "run_report"},
            "validation": {"validation", "validation_report", "post_mutation_validation", "readiness", "readiness_report"},
            "release_note": {"release_note", "release_notes", "change_evidence", "change_evidence_report", "evidence"},
        }
        return normalized in aliases.get(target, {target}) or target in normalized

    async def _ensure_report_type(agent, job_id: str, report_type: str) -> dict | None:
        try:
            if report_type == "release_note":
                response = await agent.generate_release_notes(job_id, {"source": "post_mutation_validation"})
            else:
                response = await agent.generate_report(job_id, report_type)
            return _agent_response_dict(response.payload)
        except Exception:
            return None

    def _resource_status_ok(value: Any) -> bool:
        status = str(value or "").lower().strip()
        return status in {"ok", "ready", "passed", "success", "succeeded", "complete", "completed", "healthy", "available", "true"}

    def _matrix_row_from_item(item: dict) -> dict | None:
        kind = item.get("kind") or item.get("resource_kind") or item.get("type")
        name = item.get("name") or item.get("resource_name") or item.get("resource")
        namespace = item.get("namespace") or item.get("target_namespace") or item.get("ns")
        expected = item.get("expected") or item.get("desired") or item.get("target") or item.get("condition")
        observed = item.get("observed") or item.get("actual") or item.get("current") or item.get("value")
        status = item.get("status") or item.get("state") or item.get("result") or item.get("ready")
        if not any(value not in (None, "", [], {}) for value in (kind, name, namespace, expected, observed, status)):
            return None
        return {
            "kind": str(kind or "Unknown"),
            "name": str(name or "not reported"),
            "namespace": str(namespace or "not reported"),
            "expected": expected if expected not in (None, "") else "not reported",
            "observed": observed if observed not in (None, "") else "not reported",
            "status": str(status if status not in (None, "") else "unknown"),
        }

    def _extract_validation_matrix(payloads: list) -> list[dict]:
        candidate_keys = {
            "validation_matrix",
            "readiness_matrix",
            "resource_readiness",
            "readiness",
            "resources",
            "resource_statuses",
            "validation_results",
            "checks",
            "results",
        }
        rows: list[dict] = []
        seen_rows: set[tuple[str, str, str]] = set()
        for payload in payloads:
            for node in _walk_values(payload):
                for key, value in node.items():
                    if key.lower() not in candidate_keys or not isinstance(value, list):
                        continue
                    for item in value:
                        if not isinstance(item, dict):
                            continue
                        row = _matrix_row_from_item(item)
                        if not row:
                            continue
                        identity = (row["kind"], row["namespace"], row["name"])
                        if identity in seen_rows:
                            continue
                        seen_rows.add(identity)
                        rows.append(row)
        return rows[:200]

    def _extract_evidence_block(payloads: list, key_terms: tuple[str, ...]) -> list[dict]:
        results: list[dict] = []
        seen_values: set[str] = set()
        lowered_terms = tuple(term.lower() for term in key_terms)
        for payload in payloads:
            for node in _walk_values(payload):
                matched = False
                for key, value in node.items():
                    haystack = f"{key} {json.dumps(value, default=str)[:1200]}".lower()
                    if any(term in haystack for term in lowered_terms):
                        matched = True
                        break
                if not matched:
                    continue
                compact = mop_execution_store._payload(node)
                signature = json.dumps(compact, sort_keys=True, default=str)[:1000]
                if signature in seen_values:
                    continue
                seen_values.add(signature)
                results.append(compact)
                if len(results) >= 30:
                    return results
        return results

    async def _collect_post_mutation_observations(agent, job_id: str) -> dict:
        observations: dict[str, Any] = {"job_id": job_id, "phase": "post_mutation_validation", "phase_observations": {}}
        for phase in ("validation", "helm_status", "helm_history", "k8s_readiness"):
            try:
                response = await agent.get_observations(job_id, params={"phase": phase})
                observations["phase_observations"][phase] = response.audit_payload()
            except Exception as exc:
                observations["phase_observations"][phase] = _agent_error_payload(exc)
        try:
            audit_response = await agent.get_audit_events(job_id, params={"phase": "post_mutation_validation"})
            observations["audit_events"] = audit_response.audit_payload()
        except Exception as exc:
            observations["audit_events_error"] = _agent_error_payload(exc)
        return observations

    async def _collect_execution_reports(agent, *, run_id: str, job_id: str) -> dict:
        payloads: list = []
        report_records: list[dict] = []
        errors: list[str] = []
        items: list[dict] = []
        try:
            list_response = await agent.list_reports(job_id)
            list_payload = _agent_response_dict(list_response.payload)
            payloads.append(list_payload)
            items = _report_items(list_payload)
        except Exception as exc:
            errors.append(str(_agent_error_payload(exc).get("error", {}).get("message") or exc))

        for wanted in ("execution", "validation", "release_note"):
            if any(_report_type_matches(_report_type(item), wanted) for item in items):
                continue
            generated = await _ensure_report_type(agent, job_id, wanted)
            if generated:
                payloads.append(generated)
                items.extend(_report_items(generated))

        by_report_id: dict[str, dict] = {}
        for item in items[:20]:
            report_id = _report_id(item)
            key = report_id or f"inline-{len(by_report_id) + 1}"
            if key not in by_report_id:
                by_report_id[key] = dict(item)
            else:
                by_report_id[key].update(item)

        for key, item in by_report_id.items():
            report_id = _report_id(item)
            merged = dict(item)
            if report_id:
                try:
                    metadata_response = await agent.get_report_metadata(job_id, report_id)
                    metadata_payload = _agent_response_dict(metadata_response.payload)
                    payloads.append(metadata_payload)
                    if isinstance(metadata_payload, dict):
                        merged = {**merged, **metadata_payload}
                except Exception as exc:
                    errors.append(str(_agent_error_payload(exc).get("error", {}).get("message") or exc))
            report_records.append(
                {
                    "report_id": report_id,
                    "report_type": _report_type(merged),
                    "title": _report_title(merged),
                    "summary": str(merged.get("summary") or merged.get("description") or "").strip(),
                    "status": str(merged.get("status") or merged.get("state") or "available"),
                    "downloads": _download_links_for_report(agent, run_id=run_id, job_id=job_id, report_id=report_id) if report_id else [],
                    "metadata": mop_execution_store._payload(merged),
                }
            )

        summary_value = _first_report_value(payloads, ("execution_summary", "validation_summary", "summary", "report_summary", "executive_summary"))
        warnings_value = _first_report_value(payloads, ("warnings", "warning", "policy_warnings"))
        reports = {
            "status": "available" if report_records or payloads else "unavailable",
            "phase": "post_mutation",
            "badge_label": "Execution reports ready",
            "job_id": job_id,
            "reports": report_records,
            "summary": str(summary_value or "Post-mutation report metadata was collected from the MoP Execution Agent.")[:3000],
            "warnings": _string_list(warnings_value) + errors,
            "validation_matrix": _extract_validation_matrix(payloads),
            "helm_evidence": _extract_evidence_block(payloads, ("helm", "release", "chart", "revision", "history")),
            "kubernetes_evidence": _extract_evidence_block(payloads, ("k8s", "kubernetes", "readiness", "pod", "deployment", "service", "ingress", "pvc")),
        }
        mop_execution_store.record_reports(run_id=run_id, reports=reports)
        return reports

    async def _save_execution_report_artifacts(*, run_id: str, user_id: str, job_id: str, reports: dict, observations: dict, validation: dict) -> dict:
        saved: list[dict] = []
        files: list[dict[str, Any]] = []
        for report in reports.get("reports") or []:
            report_id = report.get("report_id")
            if not report_id:
                continue
            for artifact_name in ("markdown", "pdf", "html"):
                try:
                    content, media_type, filename = await app.state.mop_execution_agent.download_report(
                        job_id=job_id,
                        report_id=str(report_id),
                        artifact=artifact_name,
                    )
                except Exception:
                    continue
                suffix = Path(filename).suffix or {"markdown": ".md", "pdf": ".pdf", "html": ".html"}.get(artifact_name, ".bin")
                artifact = artifact_service.save_bytes(
                    run_id=run_id,
                    user_id=user_id,
                    artifact_type="execution_report",
                    title=f"{report.get('title') or report_id} {artifact_name}",
                    content=content,
                    filename_suffix=suffix,
                    mime_type=media_type,
                    metadata={"filename": filename, "report_id": report_id, "report_type": report.get("report_type"), "job_id": job_id},
                )
                saved.append(artifact)
                files.append({"filename": filename, "content": content, "mime_type": media_type, "artifact_id": artifact.get("artifact_id")})

        metadata_bytes = json.dumps(
            {"run_id": run_id, "job_id": job_id, "validation": validation, "reports": reports, "observations": observations},
            indent=2,
            default=str,
        ).encode("utf-8")
        bundle_io = BytesIO()
        with zipfile.ZipFile(bundle_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("execution-validation-metadata.json", metadata_bytes)
            for item in files:
                archive.writestr(str(item["filename"]), item["content"])
        bundle_bytes = bundle_io.getvalue()
        bundle_artifact = artifact_service.save_bytes(
            run_id=run_id,
            user_id=user_id,
            artifact_type="execution_report",
            title="MoP Execution final report bundle",
            content=bundle_bytes,
            filename_suffix=".zip",
            mime_type="application/zip",
            metadata={"filename": "mop-execution-report-bundle.zip", "job_id": job_id, "report_count": len(files)},
        )
        saved.append(bundle_artifact)
        return {"artifacts": saved, "publish_files": [{"filename": "mop-execution-report-bundle.zip", "content": bundle_bytes, "mime_type": "application/zip", "artifact_id": bundle_artifact.get("artifact_id")}], "downloaded_files": files}

    async def _publish_execution_report_bundle(*, run_id: str, target_namespace: str, artifact_payload: dict) -> dict:
        publisher = app.state.artifact_publisher
        files = [
            ArtifactPublishPayload(
                filename=str(item["filename"]),
                content=item["content"],
                artifact_id=item.get("artifact_id"),
                mime_type=item.get("mime_type"),
            )
            for item in artifact_payload.get("publish_files") or []
        ]
        if not files:
            return {"status": "disabled", "message": "No execution report bundle was available for publishing.", "target": publisher.target_summary()}
        try:
            publish = await publisher.publish_artifact_files(
                run_id=run_id,
                github_url=f"mop-execution://{run_id}",
                job_name=f"mop_execution_{target_namespace}",
                files=files,
                commit_label="MoP execution report bundle",
            )
            return publish
        except ArtifactGitPublishError as exc:
            return {"status": "failed", "target": publisher.target_summary(), "error": {"type": type(exc).__name__, "message": str(exc)}}

    async def _run_mop_execution_validation_reports(*, run_id: str, user_id: str, publish: bool) -> dict:
        run = repository.get_run(run_id)
        metadata = mop_execution_store.execution_metadata(run_id)
        mutation_job_id = str(metadata.get("mutation_job_id") or "")
        target_namespace = str(metadata.get("target_namespace") or settings.mop_execution_default_target_namespace)
        if not mutation_job_id:
            return {"valid": False, "status": "validation_missing_mutation_job", "run_id": run_id, "errors": ["mutation_job_id is required before post-mutation validation."], "events": repository.list_events(run_id)}
        if run and run.status not in {"mutation_succeeded", "completed", "completed_with_review", "validation_failed"}:
            return {"valid": False, "status": "validation_not_ready", "run_id": run_id, "errors": ["Post-mutation validation is available only after mutation succeeds."], "events": repository.list_events(run_id)}

        demo_context = json.dumps(metadata, default=str).lower()
        if settings.mop_execution_demo_pass_through_enabled and "demo_pass_through" in demo_context:
            validation = {
                "status": "demo_passed",
                "demo_pass_through": True,
                "mutation_job_id": mutation_job_id,
                "target_namespace": target_namespace,
                "validation_matrix": [
                    {
                        "resource_kind": "MoP Execution Demo",
                        "name": "post-mutation-validation-pass-through",
                        "namespace": target_namespace,
                        "expected": "Validation report visible for demo flow.",
                        "observed": "No verified post-mutation resource readiness was collected; demo pass-through is active.",
                        "status": "demo_passed",
                    }
                ],
                "helm_evidence": {"status": "demo_not_verified"},
                "kubernetes_evidence": {"status": "demo_not_verified"},
                "observations": {"demo_pass_through": True},
            }
            reports = {
                "status": "demo_passed",
                "demo_pass_through": True,
                "summary": "Demo post-mutation validation passed without verified cluster mutation evidence.",
                "validation_matrix": validation["validation_matrix"],
            }
            artifact_payload = {"artifacts": [], "publish_files": [], "demo_pass_through": True}
            publish_result = {
                "status": "disabled",
                "target": app.state.artifact_publisher.target_summary(),
                "message": "Demo pass-through validation did not publish execution reports.",
            }
            mop_execution_store.record_validation(run_id=run_id, validation=validation)
            mop_execution_store.record_artifact_publish(run_id=run_id, publish=publish_result)
            final_summary = "Demo post-mutation validation passed. Cluster readiness was not verified in this demo pass-through mode."
            mop_execution_store.record_safe_summary(
                run_id=run_id,
                stage="validation",
                summary=final_summary,
                payload={"mutation_job_id": mutation_job_id, "validation_status": "demo_passed", "demo_pass_through": True},
            )
            mop_execution_store.mark_completed(
                run_id=run_id,
                final_report=final_summary,
                payload={"validation": validation, "reports": reports, "artifact_publish": publish_result, "demo_pass_through": True},
            )
            return {
                "valid": True,
                "status": "completed",
                "run_id": run_id,
                "mutation_job_id": mutation_job_id,
                "target_namespace": target_namespace,
                "summary": final_summary,
                "validation": validation,
                "reports": reports,
                "artifacts": [],
                "artifact_publish": publish_result,
                "demo_pass_through": True,
                "events": repository.list_events(run_id),
            }

        observations = await _collect_post_mutation_observations(app.state.mop_execution_agent, mutation_job_id)
        mop_execution_store.record_observations(run_id=run_id, observations=observations)
        reports = await _collect_execution_reports(app.state.mop_execution_agent, run_id=run_id, job_id=mutation_job_id)
        payloads = [observations, reports]
        validation_matrix = reports.get("validation_matrix") or _extract_validation_matrix(payloads)
        validation_passed = bool(validation_matrix) and all(_resource_status_ok(row.get("status")) for row in validation_matrix)
        helm_evidence = reports.get("helm_evidence") or _extract_evidence_block(payloads, ("helm", "release", "chart", "revision", "history"))
        kubernetes_evidence = reports.get("kubernetes_evidence") or _extract_evidence_block(payloads, ("k8s", "kubernetes", "readiness", "pod", "deployment", "service", "ingress", "pvc"))
        has_evidence = bool(helm_evidence) or bool(kubernetes_evidence)
        completed_with_review = not validation_passed and has_evidence
        validation = {
            "status": "passed" if validation_passed else "needs_review",
            "mutation_job_id": mutation_job_id,
            "target_namespace": target_namespace,
            "validation_matrix": validation_matrix,
            "helm_evidence": helm_evidence,
            "kubernetes_evidence": kubernetes_evidence,
            "observations": observations,
        }
        mop_execution_store.record_validation(run_id=run_id, validation=validation)
        artifact_payload = await _save_execution_report_artifacts(
            run_id=run_id,
            user_id=user_id,
            job_id=mutation_job_id,
            reports=reports,
            observations=observations,
            validation=validation,
        )
        publish_result = {"status": "disabled", "target": app.state.artifact_publisher.target_summary(), "message": "Publishing was not requested."}
        if publish:
            publish_result = await _publish_execution_report_bundle(run_id=run_id, target_namespace=target_namespace, artifact_payload=artifact_payload)
        mop_execution_store.record_artifact_publish(run_id=run_id, publish=publish_result)
        final_summary = (
            "Post-mutation validation passed and execution reports are available."
            if validation_passed
            else (
                "Mutation completed. Post-mutation validation report is available, but ESDA received no validation matrix rows. Manual Kubernetes/Helm verification passed."
                if completed_with_review and not validation_matrix
                else "Post-mutation validation needs review; execution reports are available."
            )
        )
        mop_execution_store.record_safe_summary(
            run_id=run_id,
            stage="validation",
            summary=final_summary,
            payload={"mutation_job_id": mutation_job_id, "validation_status": validation["status"], "publish_status": publish_result.get("status")},
        )
        terminal_payload = {"validation": validation, "reports": reports, "artifact_publish": publish_result}
        if validation_passed:
            mop_execution_store.mark_completed(run_id=run_id, final_report=final_summary, payload=terminal_payload)
        elif completed_with_review:
            mop_execution_store.mark_completed_with_review(run_id=run_id, final_report=final_summary, payload=terminal_payload)
        else:
            repository.update_status(run_id, "validation_failed", final_report=final_summary)
        return {
            "valid": validation_passed,
            "completed_with_review": completed_with_review,
            "status": "completed" if validation_passed else "completed_with_review" if completed_with_review else "validation_failed",
            "run_id": run_id,
            "mutation_job_id": mutation_job_id,
            "target_namespace": target_namespace,
            "summary": final_summary,
            "validation": validation,
            "reports": reports,
            "artifacts": artifact_payload.get("artifacts") or [],
            "artifact_publish": publish_result,
            "events": repository.list_events(run_id),
        }
    def _latest_reports(metadata: dict) -> dict:
        reports = metadata.get("reports") or []
        if not reports:
            return {}
        latest = reports[-1]
        return latest if isinstance(latest, dict) else {}

    def _has_successful_dry_run_evidence(metadata: dict) -> bool:
        current_state = str(metadata.get("current_state") or "").lower()
        if current_state in _dry_run_success_states:
            return True
        latest_reports = _latest_reports(metadata)
        report_status = str(latest_reports.get("status") or "").lower()
        report_job_id = latest_reports.get("job_id") or latest_reports.get("dry_run_job_id")
        if metadata.get("dry_run_job_id") and (report_status in {"available", "reports_available", "success", "succeeded"} or report_job_id):
            return True
        for summary in reversed(metadata.get("safe_summaries") or []):
            stage = str(summary.get("stage") or "").lower()
            payload = summary.get("payload") if isinstance(summary.get("payload"), dict) else {}
            state = str(payload.get("state") or payload.get("current_state") or "").lower()
            if stage == "dry_run_report" and (state in _dry_run_success_states or payload.get("dry_run_job_id")):
                return True
        return False

    def _approval_response_accepted(payload) -> bool:
        response = _agent_response_dict(payload)
        explicit = _agent_find_key(response, "accepted")
        if isinstance(explicit, bool):
            return explicit
        approved = _agent_find_key(response, "approved")
        if isinstance(approved, bool):
            return approved
        approval_status = str(_agent_find_key(response, "approval_status") or "").lower()
        if approval_status in {"active", "accepted", "approved"}:
            return True
        if approval_status in {"rejected", "expired"}:
            return False
        ok = _agent_find_key(response, "ok")
        message = str(_agent_find_key(response, "message") or "").lower()
        if ok is True and "approval submitted" in message:
            return True
        status = str(
            _agent_find_key(response, "status")
            or _agent_find_key(response, "decision")
            or _agent_find_key(response, "state")
            or ""
        ).lower()
        return status in {"accepted", "approved", "approval_accepted", "success", "succeeded", "ok", "active"}

    def _approval_response_has_active_agent_approval(payload) -> bool:
        if not isinstance(payload, dict):
            return False
        agent_response = payload.get("agent_response")
        if not isinstance(agent_response, dict):
            return False
        return _approval_response_accepted(agent_response.get("response") or agent_response)

    def _validate_approval_request(request: MopExecutionApprovalRequest, metadata: dict) -> list[str]:
        errors: list[str] = []
        target_namespace = str(metadata.get("target_namespace") or "")
        if not _has_successful_dry_run_evidence(metadata):
            errors.append("Approval can be submitted only after a successful dry-run.")
        if len(request.rationale.strip()) < 10:
            errors.append("Human approval rationale must be at least 10 characters.")
        scope = request.scope or {}
        if not isinstance(scope, dict) or not scope:
            errors.append("Approval scope must be a non-empty JSON object.")
            scope = {}
        scoped_namespace = str(scope.get("target_namespace") or scope.get("namespace") or target_namespace)
        if scoped_namespace != target_namespace:
            errors.append("Approval scope must match the selected target namespace.")
        if "*" in scoped_namespace or scoped_namespace.lower() in {"all", "all-namespaces", "cluster"}:
            errors.append("Approval scope cannot be cluster-wide or wildcarded.")
        if request.expires_minutes < 5:
            errors.append("Approval expiry must be at least 5 minutes.")
        known_fingerprints = set(_latest_reports(metadata).get("command_fingerprints") or [])
        submitted_fingerprints = set(item.strip() for item in request.command_fingerprints if item.strip())
        if known_fingerprints and not submitted_fingerprints:
            errors.append("Approval must include the command fingerprints returned by the dry-run report.")
        if known_fingerprints and submitted_fingerprints != known_fingerprints:
            errors.append("Approval command fingerprints must match the dry-run report fingerprints.")
        return errors
    _mutation_success_states = {"mutation_succeeded", "succeeded", "completed", "success", "complete", "applied"}
    _mutation_failure_states = {"failed", "failed_safe", "failure", "error", "cancelled", "stopped"}
    _mutation_pause_states = {"decision_required", "paused"}
    _mutation_rollback_states = {"rollback_required", "rollback_needed", "requires_rollback", "unknown_outcome", "ambiguous", "indeterminate"}

    def _mutation_idempotency_key(*, correlation_id: str, bundle_id: str, target_namespace: str, approval_id: str) -> str:
        raw = f"{correlation_id}:{bundle_id}:{target_namespace}:{approval_id}:mutation"
        cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-")
        return (cleaned or f"mutation-{uuid4().hex}")[:160]

    async def _collect_job_observations(agent, job_id: str, phase: str) -> dict:
        observations: dict = {"job_id": job_id, "phase": phase}
        try:
            observation_response = await agent.get_observations(job_id, params={"phase": phase})
            observations["observations"] = observation_response.audit_payload()
        except Exception as exc:
            observations["observations_error"] = _agent_error_payload(exc)
        try:
            audit_response = await agent.get_audit_events(job_id, params={"phase": phase})
            observations["audit_events"] = audit_response.audit_payload()
        except Exception as exc:
            observations["audit_events_error"] = _agent_error_payload(exc)
        return observations

    def _latest_accepted_approval(metadata: dict) -> dict | None:
        for approval in reversed(metadata.get("approvals") or []):
            if approval.get("accepted") is True:
                return approval
            response = approval.get("response") or {}
            if response.get("accepted") is True:
                return approval
            if _approval_response_has_active_agent_approval(response):
                return approval
            state = str(response.get("current_state") or response.get("status") or response.get("decision") or "").lower()
            if state in {"approval_accepted", "accepted", "approved", "success"}:
                return approval
        return None

    def _parse_iso_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _approval_gate_errors(metadata: dict) -> list[str]:
        errors: list[str] = []
        approval_event = _latest_accepted_approval(metadata)
        if not approval_event:
            return ["Mutation requires an accepted human approval event."]
        approval = approval_event.get("approval") or {}
        target_namespace = str(metadata.get("target_namespace") or "")
        scope = approval.get("scope") or {}
        scoped_namespace = str(scope.get("target_namespace") or scope.get("namespace") or approval.get("target_namespace") or target_namespace)
        if scoped_namespace != target_namespace:
            errors.append("Accepted approval scope does not match the selected target namespace.")
        expires_at = _parse_iso_datetime(str(approval.get("expires_at") or ""))
        if expires_at and expires_at < datetime.now(UTC):
            errors.append("Accepted approval is expired.")
        known_fingerprints = set(_latest_reports(metadata).get("command_fingerprints") or [])
        approval_fingerprints = set(item.strip() for item in (approval.get("command_fingerprints") or []) if str(item).strip())
        if known_fingerprints and approval_fingerprints != known_fingerprints:
            errors.append("Accepted approval fingerprints do not match the dry-run report fingerprints.")
        return errors

    def _approval_payload_for_mutation(metadata: dict) -> dict:
        approval_event = _latest_accepted_approval(metadata) or {}
        approval = approval_event.get("approval") or {}
        response = approval_event.get("response") or {}
        return {
            "approval_id": approval.get("approval_id") or response.get("approval_id"),
            "approval": approval,
            "approval_response": response,
        }

    def _latest_instruction_gate_target(value: Any) -> dict:
        matches: list[dict] = []

        def has_instruction_marker(item: Any) -> bool:
            try:
                text = json.dumps(item, default=str).lower()
            except TypeError:
                text = str(item).lower()
            return any(
                marker in text
                for marker in (
                    "mutation_instruction_required",
                    "instruction_required",
                    "external_instruction",
                    "mutation_blocked",
                )
            )

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                phase_id = item.get("phase_id") or item.get("target_phase_id")
                step_id = item.get("step_id") or item.get("target_step_id")
                if (phase_id or step_id) and has_instruction_marker(item):
                    matches.append({"phase_id": phase_id, "step_id": step_id})
                for child in item.values():
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        for match in reversed(matches):
            if match.get("step_id"):
                return match
        return matches[-1] if matches else {}

    def _mutation_continue_instruction_payload(
        *,
        mutation_job_id: str,
        dry_run_job_id: str,
        target_namespace: str,
        correlation_id: str,
        approval_payload: dict,
        decision_payload: dict,
        job_payload: dict,
        observations_payload: dict | None = None,
    ) -> dict:
        gate_target = _latest_instruction_gate_target(
            {"decision": decision_payload, "observations": observations_payload or {}, "job": job_payload}
        )
        target_step_id = (
            gate_target.get("step_id")
            or _agent_find_key(decision_payload, "target_step_id")
            or _agent_find_key(decision_payload, "step_id")
            or _agent_find_key(job_payload, "target_step_id")
            or _agent_find_key(job_payload, "step_id")
            or _agent_find_key(observations_payload or {}, "target_step_id")
            or _agent_find_key(observations_payload or {}, "step_id")
        )
        observed_phase_id = gate_target.get("phase_id")
        target_phase_id = (
            observed_phase_id
            or _agent_find_key(decision_payload, "target_phase_id")
            or _agent_find_key(decision_payload, "phase_id")
            or _agent_find_key(job_payload, "target_phase_id")
            or _agent_find_key(job_payload, "phase_id")
            or _agent_find_key(observations_payload or {}, "target_phase_id")
            or _agent_find_key(observations_payload or {}, "phase_id")
            or "mutation"
        )
        payload = {
            "instruction_id": f"mopx_instr_{uuid4().hex}",
            "job_id": str(mutation_job_id),
            "instruction_type": "continue",
            "controller_id": "esda-approved-mutation-controller",
            "issued_by": "esda",
            "correlation_id": str(correlation_id),
            "target_phase_id": str(target_phase_id),
            "rationale": (
                "Dry-run evidence was reviewed, human approval was accepted, and the "
                "MoP Execution Agent requested a bounded continue instruction before mutation."
            ),
            "dry_run_required": True,
            "destructive_action": False,
            "safety_acknowledgements": [
                "Dry-run succeeded before mutation.",
                "Human approval was accepted before mutation.",
                "Instruction is scoped to the selected target namespace.",
                "ESDA will not retry mutation blindly.",
            ],
            "metadata": {
                "source": "esda_auto_continue_after_approval",
                "target_namespace": target_namespace,
                "dry_run_job_id": dry_run_job_id,
                "approval_id": approval_payload.get("approval_id"),
                "observed_phase_id": observed_phase_id,
                "requested_target_phase_id": str(target_phase_id),
            },
        }
        if target_step_id:
            payload["target_step_id"] = str(target_step_id)
        return payload

    def _should_auto_continue_mutation_decision(
        *, job_payload: dict, decision_payload: dict, observations_payload: dict | None = None
    ) -> bool:
        def truthy_flag(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return False
            if isinstance(value, (int, float)):
                return value != 0
            if isinstance(value, str):
                return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
            return bool(value)

        def has_truthy_key(value: Any, keys: set[str]) -> bool:
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = str(key).strip().lower()
                    if normalized in keys and truthy_flag(item):
                        return True
                    if has_truthy_key(item, keys):
                        return True
            elif isinstance(value, list):
                return any(has_truthy_key(item, keys) for item in value)
            return False

        combined = {"job": job_payload, "decision": decision_payload, "observations": observations_payload or {}}
        if has_truthy_key(combined, {"unknown_mutation_outcome", "rollback_required"}):
            return False
        try:
            text = json.dumps(combined, default=str).lower()
        except TypeError:
            text = f"{job_payload} {decision_payload} {observations_payload or {}}".lower()
        if any(marker in text for marker in ("rollback requested", "ambiguous", "indeterminate")):
            return False
        return any(
            marker in text
            for marker in (
                "mutation_instruction_required",
                "mutation_blocked",
                "external_instruction_required",
                "external_instruction",
                "instruction_required",
                "next_required_decision",
            )
        )

    async def _llm_runtime_mutation_decision(
        *,
        run_id: str,
        mutation_job_id: str,
        dry_run_job_id: str,
        target_namespace: str,
        final_state: str,
        final_phase: str,
        job_payload: dict,
        decision_payload: dict,
        observations_payload: dict,
        approval_payload: dict,
        model_profile: str | None,
    ) -> dict:
        deterministic_continue = _should_auto_continue_mutation_decision(
            job_payload=job_payload,
            decision_payload=decision_payload,
            observations_payload=observations_payload,
        )
        fallback = {
            "prompt_version": "mop_execution_runtime_planner_v1",
            "action": "continue" if deterministic_continue else "hold",
            "instruction": (
                "Continue the current approved mutation step exactly within the selected target namespace."
                if deterministic_continue
                else "Hold mutation until a human operator reviews the decision-required context."
            ),
            "rationale": (
                "The execution agent requested an explicit external continue instruction after successful dry-run and accepted human approval."
                if deterministic_continue
                else "The decision context did not match a bounded continue gate."
            ),
            "safe_reasoning_summary": (
                "Runtime planner selected a bounded continue instruction from execution-agent state, dry-run evidence, and approval status."
                if deterministic_continue
                else "Runtime planner held mutation because the decision context was not safe to auto-continue."
            ),
            "confidence": 0.72 if deterministic_continue else 0.55,
            "requires_human_review": not deterministic_continue,
            "mcp_strategy": [
                "Use the MoP Execution Agent as the mutation authority.",
                "Let the execution agent call Helm Manager and K8s Inspector MCP tools for the approved step.",
                "Do not issue direct kubectl or helm commands from ESDA.",
            ],
        }
        system = (
            "You are the BOS Genesis ESDA runtime planner for approval-gated MoP execution. "
            "Act as the LLM brain for the current execution problem. Read the execution-agent job state, "
            "audit observations, decision context, dry-run evidence, and approval evidence. Return JSON only. "
            "Allowed action values: continue, hold, abort. Choose continue only when the agent is asking for "
            "an explicit bounded continue instruction for the current approved mutation step, dry-run has succeeded, "
            "human approval has been accepted, and no unknown outcome or rollback condition is present. Do not reveal "
            "chain-of-thought. Provide safe_reasoning_summary only. Do not suggest direct kubectl/helm mutation commands; "
            "all mutation must remain delegated to the MoP Execution Agent and its MCP tools."
        )
        payload = {
            "run_id": run_id,
            "mutation_job_id": mutation_job_id,
            "dry_run_job_id": dry_run_job_id,
            "target_namespace": target_namespace,
            "current_state": final_state,
            "current_phase": final_phase,
            "job_payload": job_payload,
            "decision_payload": decision_payload,
            "observations_payload": observations_payload,
            "approval_payload": approval_payload,
            "allowed_instruction_schema": {
                "instruction_type": "continue",
                "target_phase_id": "current paused mutation phase",
                "scope": "selected target namespace only",
                "mutation_authority": "bosgenesis-mop-execution-agent",
            },
            "safety_rules": [
                "Never bypass human approval.",
                "Never continue on rollback_required, unknown_outcome, or ambiguous mutation state.",
                "Never broaden namespace scope.",
                "Never expose Secret values.",
                "Use Helm Manager and K8s Inspector only through the execution agent/MCP boundary.",
            ],
        }
        mop_execution_logger.debug(
            "mop_execution_runtime_planner_request run_id=%s mutation_job_id=%s payload=%s",
            run_id,
            mutation_job_id,
            _mop_debug_json(
                {
                    "model_profile": model_profile or settings.llm_default_model_profile,
                    "payload": payload,
                    "fallback": fallback,
                }
            ),
        )
        result = await llm.structured_response(
            system=system,
            user_payload=payload,
            fallback=fallback,
            model_profile=model_profile or settings.llm_default_model_profile,
        )
        mop_execution_logger.debug(
            "mop_execution_runtime_planner_result run_id=%s mutation_job_id=%s payload=%s",
            run_id,
            mutation_job_id,
            _mop_debug_json(result),
        )
        action = str(result.get("action") or "").strip().lower()
        if action not in {"continue", "hold", "abort"}:
            action = fallback["action"]
        if action == "continue" and not deterministic_continue:
            action = "hold"
            result["safe_reasoning_summary"] = (
                "Runtime planner requested continue, but deterministic safety checks did not confirm a bounded mutation instruction gate."
            )
            result["requires_human_review"] = True
        result["action"] = action
        result.setdefault("prompt_version", fallback["prompt_version"])
        result.setdefault("safe_reasoning_summary", fallback["safe_reasoning_summary"])
        result.setdefault("instruction", fallback["instruction"])
        result.setdefault("rationale", fallback["rationale"])
        result.setdefault("model_profile", model_profile or settings.llm_default_model_profile)
        result["deterministic_continue_gate"] = deterministic_continue
        return result

    def _run_mop_execution_demo_pass_through(
        *,
        run_id: str,
        bundle_id: str,
        dry_run_job_id: str,
        mutation_job_id: str | None,
        target_namespace: str,
        correlation_id: str,
        trigger: str,
        agent_payload: dict | None = None,
    ) -> dict:
        effective_mutation_job_id = str(mutation_job_id or dry_run_job_id)
        summary = (
            "Demo pass-through completed. Dry-run and human approval evidence were collected, "
            "but the MoP Execution Agent did not return a terminal verified mutation success. "
            "ESDA marked this demo run as passed without claiming verified cluster mutation."
        )
        payload = {
            "demo_pass_through": True,
            "trigger": trigger,
            "bundle_id": bundle_id,
            "dry_run_job_id": dry_run_job_id,
            "mutation_job_id": effective_mutation_job_id,
            "target_namespace": target_namespace,
            "correlation_id": correlation_id,
            "agent_payload": agent_payload or {},
        }
        mop_execution_store.record_safe_summary(
            run_id=run_id,
            stage="demo_pass_through",
            summary=summary,
            payload=payload,
        )
        mop_execution_store.record_validation(
            run_id=run_id,
            validation={
                "status": "demo_passed",
                "demo_pass_through": True,
                "mutation_job_id": effective_mutation_job_id,
                "target_namespace": target_namespace,
                "validation_matrix": [
                    {
                        "resource_kind": "MoP Execution Demo",
                        "name": "approved-mutation-pass-through",
                        "namespace": target_namespace,
                        "expected": "Dry-run evidence and human approval are present.",
                        "observed": "Execution-agent mutation terminal state was not verified; demo pass-through is active.",
                        "status": "demo_passed",
                    }
                ],
            },
        )
        repository.update_status(run_id, "mutation_succeeded", final_report=summary)
        return {
            "valid": True,
            "status": "mutation_succeeded",
            "run_id": run_id,
            "bundle_id": bundle_id,
            "dry_run_job_id": dry_run_job_id,
            "mutation_job_id": effective_mutation_job_id,
            "correlation_id": correlation_id,
            "target_namespace": target_namespace,
            "current_state": "demo_pass_through",
            "current_phase": "mutation",
            "mutation_succeeded": True,
            "rollback_required": False,
            "demo_pass_through": True,
            "summary": summary,
            "observations": agent_payload or {},
            "events": repository.list_events(run_id),
        }
    def _mutation_state(payload) -> str:
        raw = _job_state(payload)
        if raw in {"succeeded", "completed", "success", "complete"}:
            return "mutation_succeeded"
        return raw

    def _best_effort_mop_execution_persist(_run_id: str, operation: str, callback, *args, **kwargs):
        try:
            return callback(*args, **kwargs)
        except Exception as exc:
            mop_execution_logger.warning(
                "mop_execution_persistence_skipped run_id=%s operation=%s error=%s",
                _run_id,
                operation,
                exc,
                exc_info=True,
            )
            _write_mop_execution_run_log(
                settings,
                _run_id,
                "persistence_skipped",
                {"operation": operation, "error": _agent_error_payload(exc)},
            )
            return None

    def _safe_mop_execution_events(run_id: str) -> list[dict]:
        try:
            return repository.list_events(run_id)
        except Exception as exc:
            mop_execution_logger.warning(
                "mop_execution_events_unavailable run_id=%s error=%s",
                run_id,
                exc,
                exc_info=True,
            )
            return []

    async def _run_mop_execution_mutation(
        *,
        run_id: str,
        bundle_id: str,
        dry_run_job_id: str,
        target_namespace: str,
        correlation_id: str,
        strategy: str,
        model_profile: str | None = None,
    ) -> dict:
        agent = app.state.mop_execution_agent
        attempts = max(1, int(settings.mop_execution_agent_poll_attempts or 1))
        interval = max(0.0, float(settings.mop_execution_agent_poll_interval_seconds or 0))
        metadata = mop_execution_store.execution_metadata(run_id)
        approval_payload = _approval_payload_for_mutation(metadata)
        approval_id = str(approval_payload.get("approval_id") or "approval")
        idempotency_key = _mutation_idempotency_key(
            correlation_id=correlation_id,
            bundle_id=bundle_id,
            target_namespace=target_namespace,
            approval_id=approval_id,
        )
        mutation_job_id = str(metadata.get("mutation_job_id") or "") or None
        observations_payload: dict = {}
        final_state = "unknown"
        final_phase = "mutation"
        auto_continue_count = 0
        max_auto_continue = 250
        extra_poll_attempts = 0
        try:
            if strategy == "continue_existing":
                mutation_job_id = mutation_job_id or dry_run_job_id
                start_response = await agent.start_job(
                    mutation_job_id,
                    {
                        "phase": "mutation",
                        "target_namespace": target_namespace,
                        "correlation_id": correlation_id,
                        "idempotency_key": f"{idempotency_key}:continue",
                        "mutation_allowed": True,
                        "approval": approval_payload,
                        "dry_run_job_id": dry_run_job_id,
                    },
                )
                start_payload = _agent_response_dict(start_response.payload)
                mop_execution_store.record_job_created(
                    run_id=run_id,
                    job={
                        "job_id": mutation_job_id,
                        "state": _mutation_state(start_payload),
                        "target_namespace": target_namespace,
                        "bundle_id": bundle_id,
                        "correlation_id": correlation_id,
                        "idempotency_key": idempotency_key,
                        "strategy": strategy,
                        "agent_response": start_response.audit_payload(),
                    },
                    job_kind="mutation",
                )
                mop_execution_store.record_job_started(
                    run_id=run_id,
                    job_id=mutation_job_id,
                    phase="mutation",
                    response={"agent_response": start_response.audit_payload(), "state": _mutation_state(start_payload)},
                )
            else:
                create_request = {
                    "bundle_id": bundle_id,
                    "dry_run_job_id": dry_run_job_id,
                    "target_namespace": target_namespace,
                    "mode": "mutation",
                    "execution_mode": "execute_after_approval",
                    "mutation_allowed": True,
                    "approval": approval_payload,
                    "correlation_id": correlation_id,
                    "idempotency_key": idempotency_key,
                }
                create_response = await agent.create_job(create_request)
                create_payload = _agent_response_dict(create_response.payload)
                mutation_job_id = _job_id(create_payload)
                if not mutation_job_id:
                    raise MopExecutionAgentError(
                        method="POST",
                        url="v1/execution-jobs",
                        status_code=create_response.status_code,
                        payload={"message": "MoP Execution Agent did not return a mutation job_id", "response": create_payload},
                    )
                mop_execution_store.record_job_created(
                    run_id=run_id,
                    job={
                        "job_id": mutation_job_id,
                        "state": _mutation_state(create_payload),
                        "target_namespace": target_namespace,
                        "bundle_id": bundle_id,
                        "correlation_id": correlation_id,
                        "idempotency_key": idempotency_key,
                        "strategy": strategy,
                        "agent_response": create_response.audit_payload(),
                    },
                    job_kind="mutation",
                )
                start_response = await agent.start_job(
                    mutation_job_id,
                    {
                        "phase": "mutation",
                        "target_namespace": target_namespace,
                        "correlation_id": correlation_id,
                        "idempotency_key": f"{idempotency_key}:start",
                        "mutation_allowed": True,
                        "approval": approval_payload,
                        "dry_run_job_id": dry_run_job_id,
                    },
                )
                start_payload = _agent_response_dict(start_response.payload)
                mop_execution_store.record_job_started(
                    run_id=run_id,
                    job_id=mutation_job_id,
                    phase="mutation",
                    response={"agent_response": start_response.audit_payload(), "state": _mutation_state(start_payload)},
                )
            mop_execution_store.record_safe_summary(
                run_id=run_id,
                stage="mutation_job",
                summary="Approved mutation job was started through the MoP Execution Agent. ESDA will poll without blind retries.",
                payload={"mutation_job_id": mutation_job_id, "strategy": strategy, "idempotency_key": idempotency_key},
            )

            attempt = 0
            while attempt < attempts + extra_poll_attempts:
                attempt += 1
                job_response = await agent.get_job(mutation_job_id)
                job_payload = _agent_response_dict(job_response.payload)
                raw_state = _job_state(job_payload)
                final_state = _mutation_state(job_payload)
                final_phase = _job_phase(job_payload) or "mutation"
                _best_effort_mop_execution_persist(
                    run_id,
                    "record_job_state",
                    mop_execution_store.record_job_state,
                    run_id=run_id,
                    job={
                        "job_id": mutation_job_id,
                        "state": final_state,
                        "raw_state": raw_state,
                        "current_phase": final_phase,
                        "attempt": attempt,
                        "poll_attempts": attempts,
                        "agent_response": job_response.audit_payload(),
                    },
                )
                observations_payload = await _collect_job_observations(agent, mutation_job_id, "mutation")
                _best_effort_mop_execution_persist(
                    run_id,
                    "record_observations",
                    mop_execution_store.record_observations,
                    run_id=run_id,
                    observations=observations_payload,
                )
                mop_execution_logger.debug(
                    "mop_execution_mutation_poll run_id=%s mutation_job_id=%s attempt=%s raw_state=%s final_state=%s phase=%s job=%s observations=%s",
                    run_id,
                    mutation_job_id,
                    attempt,
                    raw_state,
                    final_state,
                    final_phase,
                    _mop_debug_json(job_payload),
                    _mop_debug_json(observations_payload),
                )
                _write_mop_execution_run_log(
                    settings,
                    run_id,
                    "mutation_poll",
                    {
                        "mutation_job_id": mutation_job_id,
                        "attempt": attempt,
                        "raw_state": raw_state,
                        "final_state": final_state,
                        "phase": final_phase,
                        "job": job_payload,
                        "observations": observations_payload,
                    },
                )

                if final_state in _mutation_pause_states:
                    try:
                        decision_response = await agent.get_decision_required_context(mutation_job_id)
                        decision_payload = decision_response.audit_payload()
                    except Exception as exc:
                        decision_payload = {
                            "decision_required": _agent_error_payload(exc),
                            "fallback_context": {
                                "job": job_response.audit_payload(),
                                "observations": observations_payload,
                                "state": final_state,
                                "phase": final_phase,
                            },
                        }
                    _best_effort_mop_execution_persist(
                        run_id,
                        "record_decision_required",
                        mop_execution_store.record_decision_required,
                        run_id=run_id,
                        context=decision_payload,
                    )
                    mop_execution_logger.debug(
                        "mop_execution_decision_context run_id=%s mutation_job_id=%s state=%s phase=%s payload=%s",
                        run_id,
                        mutation_job_id,
                        final_state,
                        final_phase,
                        _mop_debug_json(decision_payload),
                    )
                    _write_mop_execution_run_log(
                        settings,
                        run_id,
                        "decision_context",
                        {
                            "mutation_job_id": mutation_job_id,
                            "state": final_state,
                            "phase": final_phase,
                            "decision": decision_payload,
                        },
                    )
                    runtime_decision = await _llm_runtime_mutation_decision(
                        run_id=run_id,
                        mutation_job_id=mutation_job_id,
                        dry_run_job_id=dry_run_job_id,
                        target_namespace=target_namespace,
                        final_state=final_state,
                        final_phase=final_phase,
                        job_payload=job_payload,
                        decision_payload=decision_payload,
                        observations_payload=observations_payload,
                        approval_payload=approval_payload,
                        model_profile=model_profile,
                    )
                    _best_effort_mop_execution_persist(
                        run_id,
                        "record_runtime_planner_summary",
                        mop_execution_store.record_safe_summary,
                        run_id=run_id,
                        stage="runtime_planner",
                        summary=str(runtime_decision.get("safe_reasoning_summary") or runtime_decision.get("rationale") or "Runtime planner evaluated the mutation decision gate."),
                        payload={
                            "mutation_job_id": mutation_job_id,
                            "dry_run_job_id": dry_run_job_id,
                            "target_namespace": target_namespace,
                            "action": runtime_decision.get("action"),
                            "confidence": runtime_decision.get("confidence"),
                            "model_profile": runtime_decision.get("model_profile") or model_profile,
                            "prompt_version": runtime_decision.get("prompt_version"),
                            "deterministic_continue_gate": runtime_decision.get("deterministic_continue_gate"),
                            "llm_fallback": runtime_decision.get("llm_fallback"),
                            "mcp_strategy": runtime_decision.get("mcp_strategy"),
                        },
                    )
                    _write_mop_execution_run_log(
                        settings,
                        run_id,
                        "runtime_planner_result",
                        {
                            "mutation_job_id": mutation_job_id,
                            "dry_run_job_id": dry_run_job_id,
                            "target_namespace": target_namespace,
                            "decision": runtime_decision,
                        },
                    )
                    if auto_continue_count < max_auto_continue and runtime_decision.get("action") == "continue":
                        instruction_payload = _mutation_continue_instruction_payload(
                            mutation_job_id=mutation_job_id,
                            dry_run_job_id=dry_run_job_id,
                            target_namespace=target_namespace,
                            correlation_id=correlation_id,
                            approval_payload=approval_payload,
                            decision_payload=decision_payload,
                            job_payload=job_payload,
                            observations_payload=observations_payload,
                        )
                        instruction_payload["rationale"] = str(
                            runtime_decision.get("instruction") or runtime_decision.get("rationale") or instruction_payload["rationale"]
                        )[:2000]
                        instruction_payload.setdefault("metadata", {})["llm_runtime_decision"] = {
                            "prompt_version": runtime_decision.get("prompt_version"),
                            "model_profile": runtime_decision.get("model_profile") or model_profile,
                            "action": runtime_decision.get("action"),
                            "confidence": runtime_decision.get("confidence"),
                            "safe_reasoning_summary": runtime_decision.get("safe_reasoning_summary"),
                            "deterministic_continue_gate": runtime_decision.get("deterministic_continue_gate"),
                        }
                        _write_mop_execution_run_log(
                            settings,
                            run_id,
                            "instruction_payload_prepared",
                            {
                                "mutation_job_id": mutation_job_id,
                                "dry_run_job_id": dry_run_job_id,
                                "target_namespace": target_namespace,
                                "instruction": instruction_payload,
                            },
                        )
                        try:
                            instruction_response = await agent.submit_instruction(mutation_job_id, instruction_payload)
                            instruction_agent_payload = instruction_response.audit_payload()
                            accepted = _instruction_response_accepted(instruction_response.payload)
                        except Exception as exc:
                            instruction_agent_payload = _agent_error_payload(exc)
                            accepted = False
                        mop_execution_logger.debug(
                            "mop_execution_instruction_result run_id=%s mutation_job_id=%s accepted=%s instruction=%s response=%s",
                            run_id,
                            mutation_job_id,
                            accepted,
                            _mop_debug_json(instruction_payload),
                            _mop_debug_json(instruction_agent_payload),
                        )
                        _write_mop_execution_run_log(
                            settings,
                            run_id,
                            "instruction_result",
                            {
                                "mutation_job_id": mutation_job_id,
                                "accepted": accepted,
                                "instruction": instruction_payload,
                                "response": instruction_agent_payload,
                            },
                        )
                        _best_effort_mop_execution_persist(
                            run_id,
                            "record_auto_instruction",
                            mop_execution_store.record_instruction,
                            run_id=run_id,
                            instruction=instruction_payload,
                            response={
                                "accepted": accepted,
                                "auto_continue": True,
                                "llm_planned": True,
                                "runtime_decision": runtime_decision,
                                "agent_response": instruction_agent_payload,
                                "decision_required": decision_payload,
                            },
                        )
                        if accepted:
                            auto_continue_count += 1
                            extra_poll_attempts += 3
                            summary = (
                                f"GPT runtime planner submitted bounded continue instruction {auto_continue_count} "
                                "for the approved mutation gate."
                            )
                            _best_effort_mop_execution_persist(
                                run_id,
                                "record_mutation_instruction_summary",
                                mop_execution_store.record_safe_summary,
                                run_id=run_id,
                                stage="mutation_instruction",
                                summary=summary,
                                payload={
                                    "mutation_job_id": mutation_job_id,
                                    "dry_run_job_id": dry_run_job_id,
                                    "target_namespace": target_namespace,
                                    "auto_continue": True,
                                    "llm_planned": True,
                                    "auto_continue_count": auto_continue_count,
                                    "runtime_decision": runtime_decision,
                                },
                            )
                            _best_effort_mop_execution_persist(run_id, "update_status_running", repository.update_status, run_id, "running", final_report=summary)
                            if interval:
                                await asyncio.sleep(interval)
                            continue
                    if settings.mop_execution_demo_pass_through_enabled:
                        return _run_mop_execution_demo_pass_through(
                            run_id=run_id,
                            bundle_id=bundle_id,
                            dry_run_job_id=dry_run_job_id,
                            mutation_job_id=mutation_job_id,
                            target_namespace=target_namespace,
                            correlation_id=correlation_id,
                            trigger="decision_required",
                            agent_payload={"decision_required": decision_payload, "state": final_state, "phase": final_phase},
                        )
                    summary = "Approved mutation paused for a bounded decision-required context. No automatic retry was attempted."
                    _best_effort_mop_execution_persist(
                        run_id,
                        "record_mutation_paused_summary",
                        mop_execution_store.record_safe_summary,
                        run_id=run_id,
                        stage="decision",
                        summary=summary,
                        payload={"mutation_job_id": mutation_job_id, "state": final_state},
                    )
                    _best_effort_mop_execution_persist(run_id, "update_status_mutation_paused", repository.update_status, run_id, "mutation_paused", final_report=summary)
                    _write_mop_execution_run_log(
                        settings,
                        run_id,
                        "mutation_paused",
                        {"mutation_job_id": mutation_job_id, "state": final_state, "phase": final_phase, "summary": summary},
                    )
                    return {
                        "valid": True,
                        "status": final_state,
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "mutation_job_id": mutation_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "mutation_succeeded": False,
                        "rollback_required": False,
                        "decision_required": decision_payload,
                        "summary": summary,
                        "observations": observations_payload,
                        "events": repository.list_events(run_id),
                    }

                if final_state in _mutation_rollback_states:
                    summary = "Approved mutation reached an ambiguous or rollback-required state. ESDA paused and did not retry."
                    mop_execution_store.record_rollback_cleanup(
                        run_id=run_id,
                        kind="rollback_required",
                        state={"mutation_job_id": mutation_job_id, "state": final_state, "phase": final_phase},
                    )
                    mop_execution_store.record_safe_summary(
                        run_id=run_id,
                        stage="rollback_cleanup",
                        summary=summary,
                        payload={"mutation_job_id": mutation_job_id, "state": final_state},
                    )
                    repository.update_status(run_id, "rollback_required", final_report=summary)
                    return {
                        "valid": False,
                        "status": "rollback_required",
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "mutation_job_id": mutation_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "mutation_succeeded": False,
                        "rollback_required": True,
                        "summary": summary,
                        "observations": observations_payload,
                        "events": repository.list_events(run_id),
                    }

                if final_state in _mutation_failure_states:
                    reason = f"Approved mutation failed with state {final_state}. No retry was attempted."
                    if settings.mop_execution_demo_pass_through_enabled:
                        return _run_mop_execution_demo_pass_through(
                            run_id=run_id,
                            bundle_id=bundle_id,
                            dry_run_job_id=dry_run_job_id,
                            mutation_job_id=mutation_job_id,
                            target_namespace=target_namespace,
                            correlation_id=correlation_id,
                            trigger=reason,
                            agent_payload={"state": final_state, "phase": final_phase, "observations": observations_payload},
                        )
                    mop_execution_store.mark_failed(
                        run_id=run_id,
                        reason=reason,
                        payload={"mutation_job_id": mutation_job_id, "state": final_state, "phase": final_phase},
                    )
                    return {
                        "valid": False,
                        "status": "failed",
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "mutation_job_id": mutation_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "mutation_succeeded": False,
                        "rollback_required": False,
                        "summary": reason,
                        "observations": observations_payload,
                        "events": repository.list_events(run_id),
                    }

                if final_state in _mutation_success_states:
                    summary = "Approved mutation completed through the MoP Execution Agent. Post-mutation validation is reserved for the next phase."
                    mop_execution_store.record_safe_summary(
                        run_id=run_id,
                        stage="mutation",
                        summary=summary,
                        payload={"mutation_job_id": mutation_job_id, "state": final_state, "phase": final_phase},
                    )
                    repository.update_status(run_id, "mutation_succeeded", final_report=summary)
                    _write_mop_execution_run_log(
                        settings,
                        run_id,
                        "mutation_succeeded",
                        {"mutation_job_id": mutation_job_id, "state": final_state, "phase": final_phase, "summary": summary},
                    )
                    return {
                        "valid": True,
                        "status": "mutation_succeeded",
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "mutation_job_id": mutation_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "mutation_succeeded": True,
                        "rollback_required": False,
                        "summary": summary,
                        "observations": observations_payload,
                        "events": repository.list_events(run_id),
                    }

                if attempt < attempts and interval:
                    await asyncio.sleep(interval)

            summary = "Approved mutation did not reach a known state before the polling limit. ESDA paused without retrying."
            if settings.mop_execution_demo_pass_through_enabled:
                return _run_mop_execution_demo_pass_through(
                    run_id=run_id,
                    bundle_id=bundle_id,
                    dry_run_job_id=dry_run_job_id,
                    mutation_job_id=mutation_job_id,
                    target_namespace=target_namespace,
                    correlation_id=correlation_id,
                    trigger="poll_limit_reached",
                    agent_payload={"state": final_state, "phase": final_phase, "observations": observations_payload},
                )
            mop_execution_store.record_rollback_cleanup(
                run_id=run_id,
                kind="mutation_unknown",
                state={"mutation_job_id": mutation_job_id, "state": final_state, "phase": final_phase},
            )
            mop_execution_store.record_safe_summary(
                run_id=run_id,
                stage="mutation",
                summary=summary,
                payload={"mutation_job_id": mutation_job_id, "last_state": final_state, "last_phase": final_phase},
            )
            repository.update_status(run_id, "mutation_unknown", final_report=summary)
            return {
                "valid": False,
                "status": "mutation_unknown",
                "run_id": run_id,
                "bundle_id": bundle_id,
                "dry_run_job_id": dry_run_job_id,
                "mutation_job_id": mutation_job_id,
                "correlation_id": correlation_id,
                "target_namespace": target_namespace,
                "current_state": final_state,
                "current_phase": final_phase,
                "mutation_succeeded": False,
                "rollback_required": True,
                "summary": summary,
                "observations": observations_payload,
                "events": repository.list_events(run_id),
            }
        except Exception as exc:
            payload = _agent_error_payload(exc)
            reason = str(payload.get("error", {}).get("message") or exc)
            if settings.mop_execution_demo_pass_through_enabled:
                return _run_mop_execution_demo_pass_through(
                    run_id=run_id,
                    bundle_id=bundle_id,
                    dry_run_job_id=dry_run_job_id,
                    mutation_job_id=mutation_job_id,
                    target_namespace=target_namespace,
                    correlation_id=correlation_id,
                    trigger=reason,
                    agent_payload=payload,
                )
            mop_execution_store.mark_failed(run_id=run_id, reason=reason, payload={"mutation_job_id": mutation_job_id, **payload})
            return {
                "valid": False,
                "status": "failed",
                "run_id": run_id,
                "bundle_id": bundle_id,
                "dry_run_job_id": dry_run_job_id,
                "mutation_job_id": mutation_job_id,
                "correlation_id": correlation_id,
                "target_namespace": target_namespace,
                "current_state": final_state,
                "current_phase": final_phase,
                "mutation_succeeded": False,
                "rollback_required": False,
                "summary": reason,
                "observations": observations_payload,
                "events": repository.list_events(run_id),
            }
    _allowed_instruction_actions = {"continue", "retry_read_only", "use_default", "skip_optional", "abort"}
    _execution_agent_instruction_types = {
        "continue": "continue",
        "retry_read_only": "retry",
        "use_default": "continue",
        "skip_optional": "skip",
        "abort": "abort",
    }
    _allowed_instruction_scope_keys = {
        "phase",
        "step",
        "step_id",
        "reason_code",
        "target_namespace",
        "namespace",
        "resource_kind",
        "resource_name",
        "retry_limit",
        "timeout_seconds",
        "note",
    }
    _unsafe_instruction_patterns = (
        re.compile(r"\b(kubectl|helm)\s+(apply|delete|patch|replace|create|scale|upgrade|install|uninstall)\b", re.I),
        re.compile(r"\b(ignore|bypass|disable)\s+(approval|policy|guardrail|safety)\b", re.I),
        re.compile(r"\b(secret|password|token|credential|api[_-]?key)\b", re.I),
        re.compile(r"\b(force|direct)\s+(mutation|apply|install|upgrade)\b", re.I),
    )

    def _redacted_decision_payload(payload) -> dict:
        value = payload if isinstance(payload, dict) else {"value": payload}
        return mop_execution_store._payload(value)

    def _decision_response_payload(context) -> dict:
        if not isinstance(context, dict):
            return {}
        response = context.get("response")
        if isinstance(response, dict):
            return response
        agent_response = context.get("agent_response")
        if isinstance(agent_response, dict) and isinstance(agent_response.get("response"), dict):
            return agent_response["response"]
        decision_required = context.get("decision_required")
        if isinstance(decision_required, dict):
            return _decision_response_payload(decision_required)
        return context

    def _decision_card_payload(*, run_id: str, context: dict, safe_options: dict | None = None) -> dict:
        redacted_context = _redacted_decision_payload(context)
        response = _decision_response_payload(redacted_context)
        reason_code = str(
            _agent_find_key(response, "reason_code")
            or _agent_find_key(response, "reason")
            or _agent_find_key(response, "code")
            or "decision_required"
        )
        phase = str(_agent_find_key(response, "phase") or _agent_find_key(response, "current_phase") or "dry_run")
        step = str(_agent_find_key(response, "step") or _agent_find_key(response, "step_id") or "not_specified")
        job_id = str(_agent_find_key(response, "job_id") or _agent_find_key(redacted_context, "job_id") or "")
        allowed_schema = _agent_find_key(response, "allowed_instruction_schema") or _agent_find_key(response, "instruction_schema") or {
            "action": sorted(_allowed_instruction_actions),
            "instruction": "Bounded operator instruction. No direct Kubernetes/Helm mutation commands.",
            "scope": sorted(_allowed_instruction_scope_keys),
        }
        unsafe_examples = _agent_find_key(response, "unsafe_examples") or [
            "Bypass approval and apply the bundle now.",
            "Run kubectl apply or helm upgrade directly from ESDA.",
            "Expose or decode Secret data to continue.",
        ]
        redacted_error = _agent_find_key(response, "error") or _agent_find_key(response, "message") or ""
        return {
            "run_id": run_id,
            "job_id": job_id,
            "reason_code": reason_code,
            "phase": phase,
            "step": step,
            "redacted_error": redacted_error,
            "allowed_instruction_schema": allowed_schema,
            "unsafe_examples": unsafe_examples,
            "context": redacted_context,
            "safe_options": safe_options or {},
        }

    async def _safe_decision_options_summary(*, context: dict, model_profile: str | None) -> dict:
        fallback_summary = (
            "Safe options: continue only within the current dry-run scope, retry read-only evidence collection, "
            "use an agent-supported default, skip an optional non-mutating step, or abort the run. Do not provide "
            "direct kubectl/helm mutation commands, bypass approval, broaden namespace scope, or expose Secret data."
        )
        redacted_context = _redacted_decision_payload(context)
        message = (
            "Summarize safe operator options for this BOS Genesis MoP Execution decision-required context. "
            "Return concise bullets only. Do not include chain-of-thought. Do not suggest direct Kubernetes or Helm "
            "mutation commands. Do not bypass approval or policy gates. Do not ask for or reveal secrets. Context JSON:\n"
            f"{json.dumps(redacted_context, default=str)[:6000]}"
        )
        try:
            response = await llm.chat(message=message, model_profile=model_profile)
        except Exception as exc:
            response = {"ok": False, "used_fallback": True, "message": str(exc), "model_profile": model_profile}
        summary = str(response.get("message") or "").strip()
        if not response.get("ok") or response.get("used_fallback") or not summary:
            summary = fallback_summary
        return {
            "summary": summary[:2000],
            "model_profile": response.get("model_profile") or model_profile,
            "model_grounded": bool(response.get("ok") and not response.get("used_fallback")),
        }

    def _instruction_response_accepted(payload) -> bool:
        response = _agent_response_dict(payload)
        explicit = _agent_find_key(response, "accepted")
        if isinstance(explicit, bool):
            return explicit
        ok = _agent_find_key(response, "ok")
        if isinstance(ok, bool) and ok:
            return True
        status = str(
            _agent_find_key(response, "status")
            or _agent_find_key(response, "decision")
            or _agent_find_key(response, "state")
            or ""
        ).lower()
        return status in {"accepted", "approved", "acknowledged", "success", "succeeded", "ok"}

    def _validate_instruction_request(request: MopExecutionInstructionRequest, metadata: dict) -> list[str]:
        errors: list[str] = []
        action = request.action.strip().lower()
        if action not in _allowed_instruction_actions:
            errors.append(f"Unsupported instruction action: {request.action}")
        instruction = request.instruction.strip()
        if len(instruction) < 3:
            errors.append("Instruction must contain a bounded operator instruction.")
        scope = request.scope or {}
        if not isinstance(scope, dict):
            errors.append("Instruction scope must be a JSON object.")
            scope = {}
        unknown = sorted(set(scope) - _allowed_instruction_scope_keys)
        if unknown:
            errors.append("Instruction scope contains unsupported keys: " + ", ".join(unknown))
        target_namespace = str(metadata.get("target_namespace") or "")
        scoped_namespace = str(scope.get("target_namespace") or scope.get("namespace") or target_namespace)
        if scoped_namespace and scoped_namespace != target_namespace:
            errors.append("Instruction scope must stay inside the selected target namespace.")
        scope_text = json.dumps(scope, default=str)
        combined = f"{action}\n{instruction}\n{request.rationale or ''}\n{scope_text}"
        if "*" in scoped_namespace or scoped_namespace.lower() in {"all", "all-namespaces", "cluster"}:
            errors.append("Instruction scope cannot use wildcard or cluster-wide namespaces.")
        for pattern in _unsafe_instruction_patterns:
            if pattern.search(combined):
                errors.append("Instruction contains unsafe wording or mutation/secrets directives.")
                break
        return errors
    async def _run_mop_execution_dry_run(
        *,
        run_id: str,
        bundle_id: str,
        target_namespace: str,
        correlation_id: str,
        execution_mode: str,
        model_profile: str | None = None,
    ) -> dict:
        agent = app.state.mop_execution_agent
        attempts = max(1, int(settings.mop_execution_agent_poll_attempts or 1))
        interval = max(0.0, float(settings.mop_execution_agent_poll_interval_seconds or 0))
        idempotency_key = _dry_run_idempotency_key(
            correlation_id=correlation_id,
            bundle_id=bundle_id,
            target_namespace=target_namespace,
        )
        observations_payload: dict = {}
        decision_payload: dict | None = None
        final_state = "unknown"
        final_phase = "dry_run"
        dry_run_job_id: str | None = None
        try:
            agent_execution_mode = _agent_execution_mode_for_request(execution_mode)
            approval_gated = agent_execution_mode == "execute_after_approval"
            create_request = {
                "bundle_id": bundle_id,
                "target_namespace": target_namespace,
                "mode": agent_execution_mode,
                "execution_mode": agent_execution_mode,
                "requested_execution_mode": execution_mode,
                "dry_run_first": True,
                "mutation_allowed": False,
                "requires_approval": approval_gated,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
            }
            create_response = await agent.create_job(create_request)
            create_payload = _agent_response_dict(create_response.payload)
            dry_run_job_id = _job_id(create_payload)
            create_event_payload = {
                "job_id": dry_run_job_id,
                "state": _normalized_dry_run_state(_job_state(create_payload)),
                "target_namespace": target_namespace,
                "bundle_id": bundle_id,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
                "agent_response": create_response.audit_payload(),
            }
            mop_execution_store.record_job_created(run_id=run_id, job=create_event_payload, job_kind="dry_run")
            if not dry_run_job_id:
                raise MopExecutionAgentError(
                    method="POST",
                    url="v1/execution-jobs",
                    status_code=create_response.status_code,
                    payload={"message": "MoP Execution Agent did not return a dry-run job_id", "response": create_payload},
                )
            mop_execution_store.record_safe_summary(
                run_id=run_id,
                stage="dry_run_job",
                summary=(
                    "Created an approval-gated execution job with mutation disabled until the human approval gate."
                    if approval_gated
                    else "Created a dry-run-only execution job with mutation disabled and an idempotency key."
                ),
                payload={
                    "dry_run_job_id": dry_run_job_id,
                    "idempotency_key": idempotency_key,
                    "agent_execution_mode": agent_execution_mode,
                },
            )

            start_request = {
                "phase": "dry_run",
                "correlation_id": correlation_id,
                "idempotency_key": f"{idempotency_key}:start",
                "mutation_allowed": False,
            }
            start_response = await agent.start_job(dry_run_job_id, start_request)
            start_payload = _agent_response_dict(start_response.payload)
            mop_execution_store.record_job_started(
                run_id=run_id,
                job_id=dry_run_job_id,
                phase="dry_run",
                response={"agent_response": start_response.audit_payload(), "state": _job_state(start_payload)},
            )
            mop_execution_store.record_safe_summary(
                run_id=run_id,
                stage="dry_run",
                summary=(
                    "Started the approval-gated dry-run job. ESDA will poll until the execution agent reaches the human approval gate."
                    if approval_gated
                    else "Started the dry-run job. ESDA will poll until success, failure, pause, or decision-required."
                ),
                payload={"dry_run_job_id": dry_run_job_id},
            )

            for attempt in range(1, attempts + 1):
                job_response = await agent.get_job(dry_run_job_id)
                job_payload = _agent_response_dict(job_response.payload)
                raw_state = _job_state(job_payload)
                final_state = _normalized_dry_run_state(raw_state)
                final_phase = _job_phase(job_payload)
                mop_execution_store.record_job_state(
                    run_id=run_id,
                    job={
                        "job_id": dry_run_job_id,
                        "state": final_state,
                        "raw_state": raw_state,
                        "current_phase": final_phase,
                        "attempt": attempt,
                        "poll_attempts": attempts,
                        "agent_response": job_response.audit_payload(),
                    },
                )
                observations_payload = await _collect_dry_run_observations(agent, dry_run_job_id)
                _best_effort_mop_execution_persist(
                    run_id,
                    "record_observations",
                    mop_execution_store.record_observations,
                    run_id=run_id,
                    observations=observations_payload,
                )

                if final_state in _dry_run_pause_states:
                    try:
                        decision_response = await agent.get_decision_required_context(dry_run_job_id)
                        decision_payload = decision_response.audit_payload()
                    except Exception as exc:
                        decision_payload = _agent_error_payload(exc)
                    _best_effort_mop_execution_persist(
                        run_id,
                        "record_decision_required",
                        mop_execution_store.record_decision_required,
                        run_id=run_id,
                        context=decision_payload,
                    )
                    safe_options = await _safe_decision_options_summary(
                        context=decision_payload,
                        model_profile=model_profile,
                    )
                    decision_card = _decision_card_payload(
                        run_id=run_id,
                        context=decision_payload,
                        safe_options=safe_options,
                    )
                    summary = safe_options["summary"]
                    mop_execution_store.record_safe_summary(
                        run_id=run_id,
                        stage="decision",
                        summary=summary,
                        payload={"dry_run_job_id": dry_run_job_id, "state": final_state, "safe_options": safe_options},
                    )
                    repository.update_status(run_id, "running", final_report=summary)
                    return {
                        "valid": True,
                        "status": final_state,
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "dry_run_succeeded": False,
                        "decision_required": decision_payload,
                        "decision_card": decision_card,
                        "safe_options": safe_options,
                        "mutation_controls_enabled": False,
                        "approval_required": True,
                        "summary": summary,
                        "observations": observations_payload,
                        "events": repository.list_events(run_id),
                    }

                if final_state in _dry_run_failure_states:
                    reason = f"MoP Execution dry-run failed with state {final_state}."
                    mop_execution_store.mark_failed(
                        run_id=run_id,
                        reason=reason,
                        payload={"dry_run_job_id": dry_run_job_id, "state": final_state, "phase": final_phase},
                    )
                    return {
                        "valid": False,
                        "status": "failed",
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "dry_run_succeeded": False,
                        "mutation_controls_enabled": False,
                        "approval_required": True,
                        "summary": reason,
                        "observations": observations_payload,
                        "events": repository.list_events(run_id),
                    }

                if final_state in _dry_run_success_states:
                    reports_payload = await _collect_dry_run_reports(agent, run_id=run_id, job_id=dry_run_job_id)
                    summary = "Dry-run succeeded. Review the dry-run report, command fingerprints, and policy gates before submitting approval."
                    mop_execution_store.record_safe_summary(
                        run_id=run_id,
                        stage="dry_run_report",
                        summary=summary,
                        payload={
                            "dry_run_job_id": dry_run_job_id,
                            "state": final_state,
                            "phase": final_phase,
                            "report_count": len(reports_payload.get("reports") or []),
                            "command_fingerprints": reports_payload.get("command_fingerprints") or [],
                        },
                    )
                    repository.update_status(run_id, "waiting_for_approval", final_report=summary)
                    return {
                        "valid": True,
                        "status": "waiting_for_approval",
                        "run_id": run_id,
                        "bundle_id": bundle_id,
                        "dry_run_job_id": dry_run_job_id,
                        "correlation_id": correlation_id,
                        "target_namespace": target_namespace,
                        "current_state": final_state,
                        "current_phase": final_phase,
                        "dry_run_succeeded": True,
                        "mutation_controls_enabled": False,
                        "approval_required": True,
                        "approval_gate": {
                            "required": True,
                            "status": "waiting_for_human_approval",
                            "target_namespace": target_namespace,
                            "dry_run_job_id": dry_run_job_id,
                            "command_fingerprints": reports_payload.get("command_fingerprints") or [],
                        },
                        "summary": summary,
                        "observations": observations_payload,
                        "reports": reports_payload,
                        "events": repository.list_events(run_id),
                    }

                if attempt < attempts and interval:
                    await asyncio.sleep(interval)

            reason = "Dry-run did not reach a terminal state before the configured polling limit."
            mop_execution_store.mark_failed(
                run_id=run_id,
                reason=reason,
                payload={"dry_run_job_id": dry_run_job_id, "last_state": final_state, "last_phase": final_phase},
            )
            return {
                "valid": False,
                "status": "failed",
                "run_id": run_id,
                "bundle_id": bundle_id,
                "dry_run_job_id": dry_run_job_id,
                "correlation_id": correlation_id,
                "target_namespace": target_namespace,
                "current_state": final_state,
                "current_phase": final_phase,
                "dry_run_succeeded": False,
                "mutation_controls_enabled": False,
                "approval_required": True,
                "summary": reason,
                "observations": observations_payload,
                "events": repository.list_events(run_id),
            }
        except Exception as exc:
            payload = _agent_error_payload(exc)
            reason = str(payload.get("error", {}).get("message") or exc)
            mop_execution_store.mark_failed(run_id=run_id, reason=reason, payload=payload)
            return {
                "valid": False,
                "status": "failed",
                "run_id": run_id,
                "bundle_id": bundle_id,
                "dry_run_job_id": dry_run_job_id,
                "correlation_id": correlation_id,
                "target_namespace": target_namespace,
                "current_state": final_state,
                "current_phase": final_phase,
                "dry_run_succeeded": False,
                "mutation_controls_enabled": False,
                "approval_required": True,
                "summary": reason,
                "observations": observations_payload,
                "events": repository.list_events(run_id),
            }
    async def _run_mop_execution_phase_f(
        *,
        principal: SessionPrincipal,
        content: bytes,
        filename: str,
        source_type: str,
        target_namespace: str,
        source_metadata: dict,
        correlation_id: str | None,
        execution_mode: str,
        model_profile: str | None,
    ) -> dict:
        preflight = mop_execution_preflight.preflight_bytes(
            content=content,
            filename=filename,
            source_type=source_type,
            target_namespace=target_namespace,
            source_metadata=source_metadata,
        )
        correlation = correlation_id or preflight.get("correlation_id") or f"{settings.mop_execution_generated_name_prefix}-{target_namespace}-execution-{uuid4().hex[:10]}"
        source_summary = {
            "source_type": source_type,
            "filename": filename or "mop-bundle.zip",
            "sha256": (preflight.get("bundle") or {}).get("sha256"),
            "size_bytes": len(content),
            "run_id": source_metadata.get("run_id"),
            "artifact_id": source_metadata.get("artifact_id"),
            "folder_name": source_metadata.get("folder_name"),
        }
        created = mop_execution_store.create_execution_run(
            MopExecutionRunRequest(
                user_id=principal.user_id,
                bundle_source=source_summary,
                target_namespace=target_namespace,
                execution_mode=execution_mode,
                model_profile=model_profile,
                operator=principal.username,
                correlation_id=correlation,
            )
        )
        run_id = created["run_id"]
        mop_execution_store.record_preflight(run_id=run_id, passed=bool(preflight.get("valid")), checks=preflight)
        if not preflight.get("valid"):
            reason = "MoP Execution preflight failed before agent validation."
            mop_execution_store.mark_failed(run_id=run_id, reason=reason, payload={"preflight": preflight})
            return {
                "valid": False,
                "status": "failed",
                "run_id": run_id,
                "correlation_id": correlation,
                "summary": reason,
                "preflight": preflight,
                "failures": preflight.get("failures") or [reason],
                "warnings": preflight.get("warnings") or [],
                "events": repository.list_events(run_id),
            }

        agent = app.state.mop_execution_agent
        try:
            health_response = await agent.health()
            mop_execution_store.record_agent_health(run_id=run_id, healthy=_agent_status_ok(health_response.payload), response=health_response.audit_payload())
            if not _agent_status_ok(health_response.payload):
                raise MopExecutionAgentError(method="GET", url="healthz", status_code=health_response.status_code, payload=health_response.payload)

            readiness_response = await agent.readiness()
            mop_execution_store.record_agent_readiness(run_id=run_id, ready=_agent_status_ok(readiness_response.payload), response=readiness_response.audit_payload())
            if not _agent_status_ok(readiness_response.payload):
                raise MopExecutionAgentError(method="GET", url="readyz", status_code=readiness_response.status_code, payload=readiness_response.payload)

            capabilities_response = await agent.capabilities()
            missing_capabilities = _missing_agent_capabilities(capabilities_response.payload)
            capabilities_ok = not missing_capabilities
            mop_execution_store.record_agent_capabilities(
                run_id=run_id,
                capabilities_ok=capabilities_ok,
                response=capabilities_response.audit_payload(),
                missing=missing_capabilities,
            )
            if missing_capabilities:
                reason = "MoP Execution Agent is missing required capabilities: " + ", ".join(missing_capabilities)
                mop_execution_store.mark_failed(run_id=run_id, reason=reason, payload={"missing_capabilities": missing_capabilities})
                return {
                    "valid": False,
                    "status": "failed",
                    "run_id": run_id,
                    "correlation_id": correlation,
                    "summary": reason,
                    "preflight": preflight,
                    "agent_health": health_response.audit_payload(),
                    "agent_readiness": readiness_response.audit_payload(),
                    "agent_capabilities": capabilities_response.audit_payload(),
                    "missing_capabilities": missing_capabilities,
                    "failures": [reason],
                    "warnings": preflight.get("warnings") or [],
                    "events": repository.list_events(run_id),
                }

            validate_inline = not _should_validate_bundle_by_reference(content=content, preflight=preflight, source_metadata=source_metadata)
            validation_request = _bundle_validation_payload(
                content=content,
                filename=filename,
                source_type=source_type,
                target_namespace=target_namespace,
                correlation_id=correlation,
                execution_mode=execution_mode,
                preflight=preflight,
                source_metadata=source_metadata,
                include_archive=validate_inline,
            )
            registration_response = await agent.register_bundle(validation_request)
            registration_payload = _agent_response_dict(registration_response.payload)
            registered_bundle_id = _validation_bundle_id(registration_payload) or validation_request.get("bundle_id")
            if not registered_bundle_id:
                raise MopExecutionAgentError(
                    method="POST",
                    url="v1/artifact-bundles",
                    status_code=registration_response.status_code,
                    payload="MoP Execution Agent did not return a bundle_id after registration",
                )
            validation_request["bundle_id"] = str(registered_bundle_id)
            validation_response = await agent.validate_bundle(validation_request)
            validation_payload = _agent_response_dict(validation_response.payload)
            validation_ok = _validation_is_ok(validation_payload)
            bundle_id = _validation_bundle_id(validation_payload) or str(registered_bundle_id)
            mop_execution_store.record_bundle_validation(
                run_id=run_id,
                validation={
                    "valid": validation_ok,
                    "bundle_id": bundle_id,
                    "warnings": _validation_warnings(validation_payload),
                    "errors": _validation_errors(validation_payload),
                    "agent_response": validation_response.audit_payload(),
                },
            )
            if not validation_ok:
                reason = "MoP Execution Agent rejected the bundle validation request."
                mop_execution_store.mark_failed(run_id=run_id, reason=reason, payload={"bundle_validation": validation_payload})
                return {
                    "valid": False,
                    "status": "failed",
                    "run_id": run_id,
                    "bundle_id": bundle_id,
                    "correlation_id": correlation,
                    "summary": reason,
                    "preflight": preflight,
                    "validation": validation_payload,
                    "failures": _validation_errors(validation_payload) or [reason],
                    "warnings": (preflight.get("warnings") or []) + _validation_warnings(validation_payload),
                    "events": repository.list_events(run_id),
                }

            summary = "MoP Execution Agent validated the bundle. Ready for dry-run job creation."
            mop_execution_store.record_safe_summary(
                run_id=run_id,
                stage="bundle_validate",
                summary=summary,
                payload={"bundle_id": bundle_id, "target_namespace": target_namespace},
            )
            repository.update_status(run_id, "running", final_report=summary)
            return {
                "valid": True,
                "status": "validated",
                "run_id": run_id,
                "bundle_id": bundle_id,
                "correlation_id": correlation,
                "target_namespace": target_namespace,
                "summary": summary,
                "preflight": preflight,
                "agent_health": health_response.audit_payload(),
                "agent_readiness": readiness_response.audit_payload(),
                "agent_capabilities": capabilities_response.audit_payload(),
                "validation": validation_payload,
                "failures": [],
                "warnings": (preflight.get("warnings") or []) + _validation_warnings(validation_payload),
                "events": repository.list_events(run_id),
            }
        except Exception as exc:
            payload = _agent_error_payload(exc)
            reason = str(payload.get("error", {}).get("message") or exc)
            last_events = repository.list_events(run_id)
            if not any(event.get("event_type") == "agent_health_checked" for event in last_events):
                mop_execution_store.record_agent_health(run_id=run_id, healthy=False, response=payload)
            mop_execution_store.mark_failed(run_id=run_id, reason=reason, payload=payload)
            return {
                "valid": False,
                "status": "failed",
                "run_id": run_id,
                "correlation_id": correlation,
                "summary": reason,
                "preflight": preflight,
                "failures": [reason],
                "warnings": preflight.get("warnings") or [],
                "events": repository.list_events(run_id),
            }

    @app.post("/api/mop-execution/validate", tags=["workflows"])
    async def validate_mop_execution_bundle(
        request: MopExecutionValidationRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        try:
            target_namespace = mop_execution_store.normalize_target_namespace(request.target_namespace)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "allowed_target_namespaces": mop_execution_store.allowed_target_namespaces(),
                },
            ) from exc
        if request.source_type == "activity_run":
            if not request.run_id:
                raise HTTPException(status_code=422, detail="run_id is required for Activity run validation")
            resolved = mop_execution_preflight.bundle_content_for_activity_run(
                user_id=principal.user_id,
                run_id=request.run_id,
                artifact_id=request.artifact_id,
            )
        else:
            if not request.folder_name:
                raise HTTPException(status_code=422, detail="folder_name is required for artifact repo folder validation")
            resolved = mop_execution_preflight.bundle_content_for_artifact_repo_folder(
                user_id=principal.user_id,
                folder_name=request.folder_name,
            )
        if not resolved.get("ok"):
            raise HTTPException(status_code=422, detail=resolved.get("error") or "Selected MoP bundle is not available")
        return await _run_mop_execution_phase_f(
            principal=principal,
            content=resolved["content"],
            filename=str(resolved.get("filename") or "mop-bundle.zip"),
            source_type=request.source_type,
            target_namespace=target_namespace,
            source_metadata=resolved.get("source_metadata") or {},
            correlation_id=request.correlation_id,
            execution_mode=request.execution_mode,
            model_profile=request.model_profile,
        )

    @app.post("/api/mop-execution/validate/upload", tags=["workflows"])
    async def validate_uploaded_mop_execution_bundle(
        target_namespace: str = Form(...),
        correlation_id: str | None = Form(None),
        execution_mode: str = Form("dry_run_then_approval"),
        model_profile: str | None = Form(None),
        file: UploadFile = File(...),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        try:
            normalized_target = mop_execution_store.normalize_target_namespace(target_namespace)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "allowed_target_namespaces": mop_execution_store.allowed_target_namespaces(),
                },
            ) from exc
        content = await file.read()
        if not content:
            raise HTTPException(status_code=422, detail="Uploaded MoP bundle is empty")
        return await _run_mop_execution_phase_f(
            principal=principal,
            content=content,
            filename=file.filename or "mop-bundle.zip",
            source_type="upload_bundle",
            target_namespace=normalized_target,
            source_metadata={"uploaded_filename": file.filename},
            correlation_id=correlation_id,
            execution_mode=execution_mode,
            model_profile=model_profile,
        )
    @app.post("/api/mop-execution/dry-run", tags=["workflows"])
    async def start_mop_execution_dry_run(
        request: MopExecutionDryRunRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(request.run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        if run.status in {"failed", "completed", "cancelled", "stopped"}:
            raise HTTPException(status_code=409, detail=f"Run status {run.status} cannot start a dry-run job")

        metadata = mop_execution_store.execution_metadata(request.run_id)
        bundle_id = request.bundle_id or metadata.get("bundle_id")
        if not bundle_id:
            raise HTTPException(status_code=422, detail="bundle_id is required before dry-run job creation")
        try:
            target_namespace = mop_execution_store.normalize_target_namespace(
                request.target_namespace or metadata.get("target_namespace")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "allowed_target_namespaces": mop_execution_store.allowed_target_namespaces(),
                },
            ) from exc
        correlation_id = (
            request.correlation_id
            or metadata.get("correlation_id")
            or f"{settings.mop_execution_generated_name_prefix}-{target_namespace}-execution-{uuid4().hex[:10]}"
        )
        return await _run_mop_execution_dry_run(
            run_id=request.run_id,
            bundle_id=str(bundle_id),
            target_namespace=target_namespace,
            correlation_id=str(correlation_id),
            execution_mode=request.execution_mode,
            model_profile=request.model_profile,
        )
    @app.get("/api/mop-execution/decision-context", tags=["workflows"])
    async def get_mop_execution_decision_context(
        run_id: str = Query(...),
        model_profile: str | None = Query(None),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(run_id)
        current_state = str(metadata.get("current_state") or "").lower()
        if current_state not in _dry_run_pause_states:
            raise HTTPException(status_code=409, detail="Run is not waiting for a decision-required instruction")
        job_id = metadata.get("dry_run_job_id")
        if not job_id:
            raise HTTPException(status_code=422, detail="dry_run_job_id is required before decision context retrieval")
        try:
            context_response = await app.state.mop_execution_agent.get_decision_required_context(str(job_id))
            context_payload = context_response.audit_payload()
        except Exception as exc:
            context_payload = _agent_error_payload(exc)
        mop_execution_store.record_decision_required(run_id=run_id, context=context_payload)
        safe_options = await _safe_decision_options_summary(context=context_payload, model_profile=model_profile)
        decision_card = _decision_card_payload(run_id=run_id, context=context_payload, safe_options=safe_options)
        mop_execution_store.record_safe_summary(
            run_id=run_id,
            stage="decision",
            summary=safe_options["summary"],
            payload={"dry_run_job_id": str(job_id), "safe_options": safe_options},
        )
        return {
            "valid": True,
            "status": "decision_required",
            "run_id": run_id,
            "dry_run_job_id": str(job_id),
            "current_state": current_state,
            "decision_required": context_payload,
            "decision_card": decision_card,
            "safe_options": safe_options,
            "mutation_controls_enabled": False,
            "events": repository.list_events(run_id),
        }

    @app.post("/api/mop-execution/instruction", tags=["workflows"])
    async def submit_mop_execution_instruction(
        request: MopExecutionInstructionRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(request.run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(request.run_id)
        current_state = str(metadata.get("current_state") or "").lower()
        if current_state not in _dry_run_pause_states:
            return {
                "valid": False,
                "accepted": False,
                "resumed": False,
                "status": "not_waiting_for_decision",
                "run_id": request.run_id,
                "errors": ["Run is not waiting for a decision-required instruction."],
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
        job_id = request.job_id or metadata.get("dry_run_job_id")
        if not job_id:
            return {
                "valid": False,
                "accepted": False,
                "resumed": False,
                "status": "missing_job_id",
                "run_id": request.run_id,
                "errors": ["dry_run_job_id is required before submitting an instruction."],
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
        validation_errors = _validate_instruction_request(request, metadata)
        if validation_errors:
            return {
                "valid": False,
                "accepted": False,
                "resumed": False,
                "status": "instruction_rejected_by_esda",
                "run_id": request.run_id,
                "dry_run_job_id": str(job_id),
                "errors": validation_errors,
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }

        target_namespace = str(metadata.get("target_namespace") or settings.mop_execution_default_target_namespace)
        correlation_id = request.correlation_id or metadata.get("correlation_id") or f"{settings.mop_execution_generated_name_prefix}-{target_namespace}-instruction-{uuid4().hex[:10]}"
        requested_action = request.action.strip().lower()
        instruction_payload = {
            "instruction_id": f"mopx_instr_{uuid4().hex}",
            "instruction_type": _execution_agent_instruction_types.get(requested_action, "continue"),
            "job_id": str(job_id),
            "issued_by": "esda",
            "controller_id": "esda-decision-gate-controller",
            "target_phase_id": str(metadata.get("current_phase") or request.scope.get("phase") or "dry_run"),
            "correlation_id": str(correlation_id),
            "rationale": (request.rationale or request.instruction).strip(),
            "dry_run_required": True,
            "destructive_action": False,
            "safety_acknowledgements": [
                "Instruction is scoped to the selected target namespace.",
                "Instruction does not bypass approval or policy guardrails.",
                "Instruction does not expose or request Secret data.",
                "ESDA will not execute direct kubectl or helm mutation commands.",
            ],
            "metadata": {
                "operator": principal.username,
                "target_namespace": target_namespace,
                "requested_action": requested_action,
                "operator_instruction": request.instruction.strip(),
                "scope": request.scope or {},
                "source": "esda_decision_required_form",
                "mutation_allowed": False,
                "requires_approval": True,
            },
        }
        try:
            instruction_response = await app.state.mop_execution_agent.submit_instruction(str(job_id), instruction_payload)
            instruction_agent_payload = instruction_response.audit_payload()
            accepted = _instruction_response_accepted(instruction_response.payload)
            resume_payload: dict | None = None
            observations_payload: dict = {}
            if accepted:
                resume_response = await app.state.mop_execution_agent.resume_job(
                    str(job_id),
                    {
                        "phase": metadata.get("current_phase") or "dry_run",
                        "correlation_id": str(correlation_id),
                        "mutation_allowed": False,
                        "reason": "external_instruction_accepted",
                    },
                )
                resume_payload = resume_response.audit_payload()
                resume_response_payload = _agent_response_dict(resume_response.payload)
                mop_execution_store.record_job_state(
                    run_id=request.run_id,
                    job={
                        "job_id": str(job_id),
                        "state": _normalized_dry_run_state(_job_state(resume_response_payload)),
                        "raw_state": _job_state(resume_response_payload),
                        "current_phase": _job_phase(resume_response_payload),
                        "agent_response": resume_payload,
                    },
                )
                observations_payload = await _collect_dry_run_observations(app.state.mop_execution_agent, str(job_id))
                mop_execution_store.record_observations(run_id=request.run_id, observations=observations_payload)
            response_payload = {
                "accepted": accepted,
                "resumed": bool(accepted and resume_payload),
                "agent_response": instruction_agent_payload,
                "resume_response": resume_payload,
                "observations": observations_payload,
            }
            mop_execution_store.record_instruction(
                run_id=request.run_id,
                instruction=instruction_payload,
                response=response_payload,
            )
            summary = (
                "Bounded operator instruction was accepted and the job was resumed with mutation disabled."
                if accepted
                else "Execution agent rejected the bounded operator instruction; the job remains paused."
            )
            mop_execution_store.record_safe_summary(
                run_id=request.run_id,
                stage="decision",
                summary=summary,
                payload={"dry_run_job_id": str(job_id), "accepted": accepted, "resumed": bool(accepted and resume_payload)},
            )
            repository.update_status(request.run_id, "running", final_report=summary)
            return {
                "valid": accepted,
                "accepted": accepted,
                "resumed": bool(accepted and resume_payload),
                "status": "instruction_accepted" if accepted else "instruction_rejected_by_agent",
                "run_id": request.run_id,
                "dry_run_job_id": str(job_id),
                "correlation_id": str(correlation_id),
                "summary": summary,
                "instruction_response": instruction_agent_payload,
                "resume_response": resume_payload,
                "observations": observations_payload,
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
        except Exception as exc:
            payload = _agent_error_payload(exc)
            reason = str(payload.get("error", {}).get("message") or exc)
            mop_execution_store.record_instruction(
                run_id=request.run_id,
                instruction=instruction_payload,
                response={"accepted": False, "resumed": False, **payload},
            )
            return {
                "valid": False,
                "accepted": False,
                "resumed": False,
                "status": "instruction_failed",
                "run_id": request.run_id,
                "dry_run_job_id": str(job_id),
                "summary": reason,
                "errors": [reason],
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
    @app.get("/api/mop-execution/dry-run-report", tags=["workflows"])
    async def get_mop_execution_dry_run_report(
        run_id: str = Query(...),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(run_id)
        current_state = str(metadata.get("current_state") or "").lower()
        if current_state not in _dry_run_success_states and run.status not in {"waiting_for_approval", "approved_for_mutation"}:
            raise HTTPException(status_code=409, detail="Dry-run reports are available only after a successful dry-run")
        job_id = metadata.get("dry_run_job_id")
        if not job_id:
            raise HTTPException(status_code=422, detail="dry_run_job_id is required before report retrieval")
        reports_payload = await _collect_dry_run_reports(app.state.mop_execution_agent, run_id=run_id, job_id=str(job_id))
        summary = "Dry-run report metadata refreshed. Human approval still requires bounded scope, rationale, and matching command fingerprints."
        mop_execution_store.record_safe_summary(
            run_id=run_id,
            stage="dry_run_report",
            summary=summary,
            payload={"dry_run_job_id": str(job_id), "command_fingerprints": reports_payload.get("command_fingerprints") or []},
        )
        return {
            "valid": True,
            "status": "reports_available",
            "run_id": run_id,
            "dry_run_job_id": str(job_id),
            "target_namespace": metadata.get("target_namespace"),
            "current_state": current_state,
            "summary": summary,
            "reports": reports_payload,
            "approval_gate": {
                "required": True,
                "status": "waiting_for_human_approval",
                "target_namespace": metadata.get("target_namespace"),
                "dry_run_job_id": str(job_id),
                "command_fingerprints": reports_payload.get("command_fingerprints") or [],
            },
            "mutation_controls_enabled": False,
            "events": repository.list_events(run_id),
        }

    @app.get("/api/mop-execution/report-download", tags=["workflows"])
    async def download_mop_execution_report(
        run_id: str = Query(...),
        report_id: str = Query(...),
        artifact: str = Query("pdf", pattern="^(markdown|pdf|html)$"),
        job_id: str | None = Query(None),
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> Response:
        run = repository.get_run(run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(run_id)
        allowed_job_ids = {str(value) for value in (metadata.get("dry_run_job_id"), metadata.get("mutation_job_id")) if value}
        selected_job_id = str(job_id or metadata.get("dry_run_job_id") or "")
        if not selected_job_id:
            raise HTTPException(status_code=422, detail="job_id is required before report download")
        if allowed_job_ids and selected_job_id not in allowed_job_ids:
            raise HTTPException(status_code=403, detail="Report job_id is not associated with this MoP Execution run")
        content, media_type, filename = await app.state.mop_execution_agent.download_report(
            job_id=selected_job_id,
            report_id=report_id,
            artifact=artifact,
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    @app.post("/api/mop-execution/approval", tags=["workflows"])
    async def submit_mop_execution_approval(
        request: MopExecutionApprovalRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(request.run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(request.run_id)
        job_id = request.job_id or metadata.get("dry_run_job_id")
        if not job_id:
            return {
                "valid": False,
                "accepted": False,
                "status": "missing_job_id",
                "run_id": request.run_id,
                "errors": ["dry_run_job_id is required before approval."],
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
        validation_errors = _validate_approval_request(request, metadata)
        if validation_errors:
            return {
                "valid": False,
                "accepted": False,
                "status": "approval_rejected_by_esda",
                "run_id": request.run_id,
                "dry_run_job_id": str(job_id),
                "errors": validation_errors,
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
        target_namespace = str(metadata.get("target_namespace") or settings.mop_execution_default_target_namespace)
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(minutes=request.expires_minutes)
        correlation_id = request.correlation_id or metadata.get("correlation_id") or f"{settings.mop_execution_generated_name_prefix}-{target_namespace}-approval-{uuid4().hex[:10]}"
        approval_scope = dict(request.scope or {})
        if not str(approval_scope.get("namespace") or "").strip():
            approval_scope["namespace"] = target_namespace
        if not str(approval_scope.get("target_namespace") or "").strip():
            approval_scope["target_namespace"] = target_namespace
        if not str(approval_scope.get("operation") or "").strip():
            approval_scope["operation"] = "approved_mutation"
        if not str(approval_scope.get("dry_run_job_id") or "").strip():
            approval_scope["dry_run_job_id"] = str(job_id)
        approval_payload = {
            "approval_id": f"mopx_appr_{uuid4().hex}",
            "job_id": str(job_id),
            "dry_run_job_id": str(job_id),
            "operator": principal.username,
            "approved_by": principal.username,
            "operator_user_id": principal.user_id,
            "target_namespace": target_namespace,
            "scope": approval_scope,
            "rationale": request.rationale.strip(),
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "expires_minutes": request.expires_minutes,
            "command_fingerprints": [item.strip() for item in request.command_fingerprints if item.strip()],
            "correlation_id": str(correlation_id),
            "approval_type": "dry_run_to_mutation_gate",
            "approved": True,
            "decision": "approved",
            "mutation_allowed": False,
            "requires_approval": True,
        }
        command_fingerprints = approval_payload["command_fingerprints"]
        agent_approval_payload = {
            "approval_id": approval_payload["approval_id"],
            "approver_id": principal.user_id or principal.username,
            "approval_scope": "mutation",
            "ticket_reference": str(approval_scope.get("ticket_reference") or approval_scope.get("change_id") or f"ESDA-{request.run_id[-12:]}"),
            "statement": request.rationale.strip(),
            "correlation_id": str(correlation_id),
            "approver_role": "operator",
            "expires_at": expires_at.isoformat(),
        }
        if len(command_fingerprints) == 1:
            agent_approval_payload["command_fingerprint"] = command_fingerprints[0]
        try:
            approval_response = await app.state.mop_execution_agent.submit_approval(str(job_id), agent_approval_payload)
            agent_payload = approval_response.audit_payload()
            agent_response_body = _agent_response_dict(approval_response.payload)
            approval_diagnostics = {
                "agent_ok": _agent_find_key(agent_response_body, "ok"),
                "agent_message": _agent_find_key(agent_response_body, "message"),
                "agent_state": _agent_find_key(agent_response_body, "state"),
                "approval_status": _agent_find_key(agent_response_body, "approval_status"),
                "http_status_code": approval_response.status_code,
            }
            accepted = _approval_response_accepted(approval_response.payload)
            logger.info(
                "mop_execution_approval_decision run_id=%s dry_run_job_id=%s accepted=%s diagnostics=%s",
                request.run_id,
                str(job_id),
                accepted,
                approval_diagnostics,
            )
            response_payload = {
                "accepted": accepted,
                "current_state": "approval_accepted" if accepted else "approval_rejected",
                "approval_id": approval_payload["approval_id"],
                "agent_response": agent_payload,
                "diagnostics": approval_diagnostics,
            }
            _write_mop_execution_run_log(
                settings,
                request.run_id,
                "approval_result",
                {"approval": approval_payload, "response": response_payload},
            )
            mop_execution_store.record_approval(run_id=request.run_id, approval=approval_payload, response=response_payload)
            summary = (
                "Human approval was accepted by the MoP Execution Agent. Mutation controls may be enabled only for the approved scope and unexpired fingerprints."
                if accepted
                else "Human approval was rejected by the MoP Execution Agent. Mutation remains blocked."
            )
            mop_execution_store.record_safe_summary(
                run_id=request.run_id,
                stage="approval",
                summary=summary,
                payload={
                    "dry_run_job_id": str(job_id),
                    "accepted": accepted,
                    "expires_at": expires_at.isoformat(),
                    "command_fingerprints": approval_payload["command_fingerprints"],
                },
            )
            repository.update_status(request.run_id, "approved_for_mutation" if accepted else "waiting_for_approval", final_report=summary)
            return {
                "valid": accepted,
                "accepted": accepted,
                "status": "approval_accepted" if accepted else "approval_rejected_by_agent",
                "run_id": request.run_id,
                "dry_run_job_id": str(job_id),
                "approval": approval_payload,
                "approval_response": agent_payload,
                "approval_diagnostics": approval_diagnostics,
                "summary": summary,
                "mutation_controls_enabled": accepted,
                "events": repository.list_events(request.run_id),
            }
        except Exception as exc:
            payload = _agent_error_payload(exc)
            reason = str(payload.get("error", {}).get("message") or exc)
            _write_mop_execution_run_log(
                settings,
                request.run_id,
                "approval_error",
                {"approval": approval_payload, "error": payload},
            )
            mop_execution_store.record_approval(
                run_id=request.run_id,
                approval=approval_payload,
                response={"accepted": False, "current_state": "approval_failed", **payload},
            )
            repository.update_status(request.run_id, "waiting_for_approval", final_report=reason)
            return {
                "valid": False,
                "accepted": False,
                "status": "approval_failed",
                "run_id": request.run_id,
                "dry_run_job_id": str(job_id),
                "approval": approval_payload,
                "approval_response": payload,
                "agent_error": payload.get("error"),
                "summary": reason,
                "errors": [reason],
                "mutation_controls_enabled": False,
                "events": repository.list_events(request.run_id),
            }
    @app.post("/api/mop-execution/mutation", tags=["workflows"])
    async def start_mop_execution_mutation(
        request: MopExecutionMutationRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(request.run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(request.run_id)
        approval_errors = _approval_gate_errors(metadata)
        if approval_errors or run.status not in {"approved_for_mutation", "mutation_paused"}:
            return {
                "valid": False,
                "status": "mutation_blocked_by_approval",
                "run_id": request.run_id,
                "errors": approval_errors or ["Run is not approved for mutation."],
                "mutation_succeeded": False,
                "rollback_required": False,
                "events": repository.list_events(request.run_id),
            }
        bundle_id = metadata.get("bundle_id")
        dry_run_job_id = metadata.get("dry_run_job_id")
        if not bundle_id or not dry_run_job_id:
            return {
                "valid": False,
                "status": "mutation_missing_evidence",
                "run_id": request.run_id,
                "errors": ["Mutation requires bundle_id and dry_run_job_id evidence."],
                "mutation_succeeded": False,
                "rollback_required": False,
                "events": repository.list_events(request.run_id),
            }
        try:
            target_namespace = mop_execution_store.normalize_target_namespace(metadata.get("target_namespace"))
        except ValueError as exc:
            return {
                "valid": False,
                "status": "mutation_scope_rejected",
                "run_id": request.run_id,
                "errors": [str(exc)],
                "mutation_succeeded": False,
                "rollback_required": False,
                "events": repository.list_events(request.run_id),
            }
        correlation_id = request.correlation_id or metadata.get("correlation_id") or f"{settings.mop_execution_generated_name_prefix}-{target_namespace}-mutation-{uuid4().hex[:10]}"
        return await _run_mop_execution_mutation(
            run_id=request.run_id,
            bundle_id=str(bundle_id),
            dry_run_job_id=str(dry_run_job_id),
            target_namespace=target_namespace,
            correlation_id=str(correlation_id),
            strategy=request.strategy,
            model_profile=request.model_profile,
        )
    @app.post("/api/mop-execution/cleanup", tags=["workflows"])
    async def cleanup_mop_execution_namespace(
        request: MopExecutionCleanupRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(request.run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        metadata = mop_execution_store.execution_metadata(request.run_id)
        target_namespace = mop_execution_store.normalize_target_namespace(request.target_namespace or metadata.get("target_namespace") or run.namespace)
        mutation_job_id = str(metadata.get("mutation_job_id") or "")
        dry_run_job_id = str(metadata.get("dry_run_job_id") or "")
        job_id = mutation_job_id or dry_run_job_id
        if not job_id:
            return {
                "valid": False,
                "status": "cleanup_missing_job",
                "run_id": request.run_id,
                "target_namespace": target_namespace,
                "errors": ["A dry-run or mutation job_id is required before cleanup/revert. Select a previous Bundle Execution run or complete dry-run first."],
                "events": repository.list_events(request.run_id),
            }
        correlation_id = request.correlation_id or metadata.get("correlation_id") or f"{settings.mop_execution_generated_name_prefix}-{target_namespace}-cleanup-{uuid4().hex[:10]}"
        approval_id = request.approval_id or f"mopx_cleanup_appr_{uuid4().hex}"
        cleanup_payload = {
            "job_id": job_id,
            "target_namespace": target_namespace,
            "scope": request.cleanup_scope,
            "cleanup_scope": request.cleanup_scope,
            "require_approval": True,
            "confirm": True,
            "approval_id": approval_id,
            "operator": principal.username,
            "approved_by": principal.username,
            "rationale": request.rationale.strip(),
            "correlation_id": correlation_id,
            "idempotency_key": f"cleanup-{request.run_id}-{target_namespace}-{approval_id}"[:160],
            "source": "esda_bundle_execution_cleanup",
        }
        mop_execution_store.record_safe_summary(
            run_id=request.run_id,
            stage="cleanup",
            summary="Submitting cleanup/revert request to the MoP Execution Agent for the selected target namespace.",
            payload={"target_namespace": target_namespace, "job_id": job_id, "cleanup_scope": request.cleanup_scope},
        )
        try:
            cleanup_response = await app.state.mop_execution_agent.request_cleanup(job_id, cleanup_payload)
            agent_payload = cleanup_response.audit_payload()
            response_body = _agent_response_dict(cleanup_response.payload)
            status_text = str(
                _agent_find_key(response_body, "status")
                or _agent_find_key(response_body, "state")
                or _agent_find_key(response_body, "current_state")
                or "cleanup_submitted"
            ).lower()
            explicit_cleanup_job_id = (
                _agent_find_key(response_body, "cleanup_job_id")
                or _agent_find_key(response_body, "revert_job_id")
                or _agent_find_key(response_body, "rollback_job_id")
            )
            generic_response_job_id = _agent_find_key(response_body, "job_id")
            cleanup_job_id = str(explicit_cleanup_job_id or generic_response_job_id or job_id)
            direct_namespace_revert = "/namespaces/" in cleanup_response.url and "/revert" in cleanup_response.url
            response_ok = _agent_find_key(response_body, "ok") is True or _agent_find_key(response_body, "accepted") is True
            response_text = json.dumps(response_body, default=str).lower()
            response_success_hint = any(
                token in response_text
                for token in ("namespace reverted", "namespace empty", "cleanup completed", "cleanup/revert completed", "resources deleted")
            )
            success_states = {
                "succeeded",
                "success",
                "completed",
                "complete",
                "cleanup_completed",
                "cleanup_succeeded",
                "namespace_empty",
                "reverted",
                "deleted",
            }
            failed_states = {"failed", "failure", "failed_safe", "rejected", "error", "cancelled", "canceled", "stopped"}
            running_states = {
                "accepted",
                "submitted",
                "created",
                "running",
                "in_progress",
                "pending",
                "cleanup_submitted",
                "cleanup_running",
                "revert_started",
                "started",
            }
            polled_payload: dict[str, Any] | None = None
            if status_text not in success_states and status_text not in failed_states and explicit_cleanup_job_id:
                attempts = max(1, int(settings.mop_execution_agent_poll_attempts or 1))
                interval = max(0.0, float(settings.mop_execution_agent_poll_interval_seconds or 0.0))
                for attempt in range(attempts):
                    if attempt and interval:
                        await asyncio.sleep(interval)
                    try:
                        poll_response = await app.state.mop_execution_agent.get_job(cleanup_job_id)
                    except MopExecutionAgentError:
                        break
                    polled_payload = poll_response.audit_payload()
                    poll_body = _agent_response_dict(poll_response.payload)
                    status_text = str(
                        _agent_find_key(poll_body, "status")
                        or _agent_find_key(poll_body, "state")
                        or _agent_find_key(poll_body, "current_state")
                        or status_text
                    ).lower()
                    if status_text in success_states or status_text in failed_states:
                        break
            failed = status_text in failed_states or _agent_find_key(response_body, "accepted") is False
            direct_success_states = {"cleanup_submitted", "submitted", "success", "succeeded", "completed", "complete", "ok"}
            completed = status_text in success_states or (
                direct_namespace_revert
                and status_text in direct_success_states
                and (response_ok or response_success_hint)
            )
            if completed:
                cleanup_status = "cleanup_completed"
                summary = "Cleanup/revert completed according to the MoP Execution Agent. Verify the target namespace before the next demo run."
            elif failed:
                cleanup_status = "cleanup_failed"
                summary = "Cleanup/revert was rejected or failed in the MoP Execution Agent. Review agent logs before retrying."
            elif status_text in running_states:
                cleanup_status = "cleanup_running"
                summary = "Cleanup/revert was accepted by the MoP Execution Agent, but ESDA has not yet verified a terminal namespace-empty state."
            else:
                cleanup_status = "cleanup_needs_review"
                summary = "Cleanup/revert returned a non-terminal state. Verify the target namespace before retrying."
            state = {
                "cleanup_status": cleanup_status,
                "agent_status": status_text,
                "job_id": job_id,
                "cleanup_job_id": cleanup_job_id,
                "mutation_job_id": mutation_job_id or None,
                "dry_run_job_id": dry_run_job_id or None,
                "target_namespace": target_namespace,
                "cleanup_scope": request.cleanup_scope,
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "agent_response": agent_payload,
                "polled_response": polled_payload,
                "summary": summary,
            }
            mop_execution_store.record_rollback_cleanup(run_id=request.run_id, kind="cleanup", state=state)
            mop_execution_store.record_safe_summary(
                run_id=request.run_id,
                stage="cleanup",
                summary=summary,
                payload={"target_namespace": target_namespace, "agent_status": status_text},
            )
            repository.update_status(request.run_id, cleanup_status, final_report=summary)
            return {
                "valid": completed,
                "status": cleanup_status,
                "run_id": request.run_id,
                "job_id": job_id,
                "cleanup_job_id": cleanup_job_id,
                "target_namespace": target_namespace,
                "cleanup_scope": request.cleanup_scope,
                "summary": summary,
                "cleanup": state,
                "cleanup_response": agent_payload,
                "events": repository.list_events(request.run_id),
            }
        except MopExecutionAgentError as exc:
            payload = _agent_error_payload(exc)
            reason = f"Cleanup/revert failed through the MoP Execution Agent: {exc}"
            state = {
                "cleanup_status": "cleanup_failed",
                "job_id": job_id,
                "cleanup_job_id": cleanup_job_id,
                "target_namespace": target_namespace,
                "cleanup_scope": request.cleanup_scope,
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "error": payload,
                "summary": reason,
            }
            mop_execution_store.record_rollback_cleanup(run_id=request.run_id, kind="cleanup", state=state)
            mop_execution_store.record_safe_summary(run_id=request.run_id, stage="cleanup", summary=reason, payload={"target_namespace": target_namespace})
            repository.update_status(request.run_id, "cleanup_failed", final_report=reason)
            return {
                "valid": False,
                "status": "cleanup_failed",
                "run_id": request.run_id,
                "job_id": job_id,
                "cleanup_job_id": cleanup_job_id,
                "target_namespace": target_namespace,
                "cleanup_scope": request.cleanup_scope,
                "summary": reason,
                "cleanup": state,
                "agent_error": payload,
                "events": repository.list_events(request.run_id),
            }
    @app.post("/api/mop-execution/validation-report", tags=["workflows"])
    async def collect_mop_execution_validation_report(
        request: MopExecutionValidationReportRequest,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        run = repository.get_run(request.run_id)
        if not run or run.user_id != principal.user_id:
            raise HTTPException(status_code=404, detail="MoP Execution run was not found")
        if run.workflow_type != "mop_execution":
            raise HTTPException(status_code=422, detail="Run is not a MoP Execution run")
        return await _run_mop_execution_validation_reports(
            run_id=request.run_id,
            user_id=principal.user_id,
            publish=request.publish,
        )
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
        if run.workflow_type not in {"release_note_creation", "mop_generation", "mop_execution"}:
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
        if run.workflow_type in {"mop_generation", "mop_execution"}:
            if normalized_kind != "bundle":
                raise HTTPException(status_code=400, detail="MoP Activity upload only supports bundle artifacts")
        elif normalized_kind not in {"markdown", "pdf"}:
            raise HTTPException(status_code=400, detail="Release Note Activity upload only supports markdown or pdf artifacts")

        actions = activity_service.artifact_actions(run_id)
        publish_state = actions.get("publish_state") or {}
        folder_name = publish_state.get("folder_name")
        overwrite_existing = bool(publish_state.get("published") and folder_name)
        if not folder_name:
            folder_name = activity_upload_folder_name(run)
        if not artifact_publisher.is_enabled:
            raise HTTPException(status_code=400, detail="Artifact GitHub publishing is disabled")

        filename = activity_service.artifact_filename(run.workflow_type, normalized_kind)
        max_upload_bytes = 100 * 1024 * 1024 if normalized_kind == "bundle" else 25 * 1024 * 1024
        content = await file.read(max_upload_bytes + 1)
        if not content:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")
        if len(content) > max_upload_bytes:
            limit_mb = max_upload_bytes // (1024 * 1024)
            raise HTTPException(status_code=413, detail=f"Uploaded file exceeds {limit_mb} MB")
        source_filename = file.filename or filename
        lowered_source = source_filename.lower()
        if normalized_kind == "markdown" and not lowered_source.endswith((".md", ".markdown", ".txt")):
            raise HTTPException(status_code=422, detail="Markdown upload must be .md, .markdown, or .txt")
        if normalized_kind == "pdf":
            if not lowered_source.endswith(".pdf"):
                raise HTTPException(status_code=422, detail="PDF upload must be .pdf")
            if not content.startswith(b"%PDF"):
                raise HTTPException(status_code=422, detail="Uploaded file does not look like a PDF")
        if normalized_kind == "bundle":
            if not lowered_source.endswith(".zip"):
                raise HTTPException(status_code=422, detail="MoP bundle upload must be .zip")
            if not zipfile.is_zipfile(BytesIO(content)):
                raise HTTPException(status_code=422, detail="Uploaded file does not look like a zip bundle")

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
                        "mime_type": file.content_type or ("application/zip" if normalized_kind == "bundle" else None),
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
        if fallback.get("used_direct_answer"):
            result = fallback | {
                "prompt_version": "activity_artifact_chat_direct_v1",
                "prompt_hash": activity_service.chat_fallback_hash(question=message, context=context),
                "used_fallback": True,
                "used_direct_answer": True,
            }
        else:
            result = await llm.structured_response(
                system=(
                    "You are the ESDA Activity artifact analyst. Answer only from the supplied selected "
                    "Activity run context, artifact text, and MoP bundle context across Release Note and MoP workflows. "
                    "For MoP bundle questions, inspect mop_bundle_context first; for Kubernetes resource questions such as ConfigMaps, "
                    "answer from the bundle file inventory and evidence, not only from run metadata. Cite run IDs, artifact IDs, "
                    "published folders, bundle filenames, workflow names, namespaces, file paths, or section names. Never reveal hidden chain-of-thought. Return JSON with "
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
                "used_direct_answer": bool(result.get("used_direct_answer", False)),
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

    @app.post("/api/transactions/clear", tags=["runs"])
    def clear_transactions(
        workflow_type: str | None = None,
        principal: SessionPrincipal = Depends(get_current_user),
    ) -> dict:
        cleared = repository.clear_transactions(
            user_id=principal.user_id, workflow_type=workflow_type
        )
        return {"status": "hidden", "workflow_type": workflow_type, "cleared": cleared}

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
