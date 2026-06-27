# Low-Level Design (LLD): BOS Genesis ESDA Chatbot UX Application

## 1. Document Purpose

This document defines the low-level design for the BOS Genesis ESDA UX application.

The application provides an authenticated web experience where users interact with a chatbot backed by Azure-hosted GPT-5. The chatbot acts as a bounded operational assistant for the BOS Genesis ecosystem and can coordinate release-note creation, MoP creation, MoP execution, Helm management, and Kubernetes management through approved tools, MCP servers, and existing BOS Genesis service agents.

This LLD is derived from `baseline_idea.md` and narrows the design around the requested UX application, login flow, Azure GPT-5 integration, workflow orchestration, policy enforcement, auditability, and future extensibility.

This version is also aligned with `hld.md`. Where the HLD describes the LLM provider generically as GPT-5 through the OpenAI API, this LLD maps that provider boundary to Azure-hosted GPT-5 because the ESDA implementation will receive Azure deployment details later.

This LLD is additionally aligned with `project_architecture_specification.md`, which finalizes the project as a Python web application with a JavaScript/HTML/CSS frontend, Azure GPT-5, LangGraph, LangChain, LangMem, PostgreSQL episodic/procedural memory, Qdrant semantic memory, and PostgreSQL review-grade logging. If this LLD conflicts with that specification, the project architecture specification should be treated as the source of truth.

## 1.1 Current Low-Level Implementation Status (2026-06-25)

The implemented V1 hello-world workflow is release-note generation. This path demonstrates the intended ESDA pattern: authenticated UI, GPT-backed classification/planning, governed MCP tool invocation, live progress, PostgreSQL audit records, and downloadable artifacts.

Implemented modules and responsibilities:

| Module | Current responsibility |
|---|---|
| `backend/app/tools/release_note_agent.py` | MCP-first adapter for `bosgenesis-release-note-agent`; starts scans, polls status, generates notes, retrieves artifact metadata, and downloads binary artifacts. |
| `backend/app/chains/release_notes.py` | Intent classifier, planner, verifier, recovery recommender, and report writer. The report writer treats release-note-agent Markdown as the initial document. |
| `backend/app/graphs/release_notes.py` | LangGraph workflow for classification, planning, agent execution, validation, recovery, final Markdown artifact save, and PDF artifact save. |
| `backend/app/artifacts.py` | Artifact metadata and local file storage for Markdown and binary PDF artifacts. |
| `backend/app/static/js/release_notes.js` | Release-note form, SSE live progress, copyable progress, preview, and multiple artifact download buttons. |

Current release-note sequence:

1. User submits a GitHub URL, release name, branch/tag/commit source reference, and analysis depth.
2. ESDA classifies the request as `release_note_creation` and logs prompt version/hash plus a model-provided reasoning summary.
3. ESDA creates a bounded plan and validates that the source URL is allowed.
4. ESDA invokes `bosgenesis-release-note-agent` through MCP-compatible tool endpoints: `github_release_scan_start`, `github_release_scan_status`, `github_release_generate_note`, and `github_release_get_artifact`.
5. The scan requests `markdown`, `html`, `pdf`, and `json` output formats.
6. ESDA hydrates the release-note-agent Markdown and artifact metadata through the artifact download path when the MCP envelope only returns references.
7. GPT uses the release-note-agent Markdown as the initial document and produces the final human-readable Markdown draft.
8. ESDA verifies required release-note structure and source evidence.
9. ESDA saves a final Markdown artifact and the release-note-agent-rendered PDF artifact.
10. The UI streams all major events, shows the Markdown preview, and renders separate Markdown/PDF download buttons.

Current configuration additions:

| Setting | Purpose |
|---|---|
| `RELEASE_NOTE_AGENT_URL` | REST/base URL used for compatibility and artifact downloads. |
| `RELEASE_NOTE_AGENT_MCP_URL` | MCP-compatible release-note-agent URL. Local runs use ingress; Helm deployments use the in-cluster service. |
| `RELEASE_NOTE_AGENT_TRANSPORT` | `auto`, `mcp`, or `rest`; V1 defaults to MCP when MCP URL is present. |
| `RELEASE_NOTE_AGENT_TIMEOUT_SECONDS` | Long-running scan/generation timeout. |

Current data-store position:

- PostgreSQL is the active store for runs, logs, LLM review records, approvals, policies, procedures, and artifact metadata.
- The next required UX/runtime enhancement is refresh-safe progress: every workflow page must restore active and historical transactions from PostgreSQL and keep backend work running after refresh/navigation.
- Artifact bytes are stored in the configured artifact storage directory.
- Qdrant remains optional for V1 and should only be enabled once semantic memory lookup is needed.
- ClickHouse and SQLite are excluded from the current V1 path.

---

## 2. Target System Name

Recommended name:

**BOS Genesis ESDA Console**

Alternative names:

- BOS Genesis ChatOps Console
- BOS Genesis Workflow Assistant
- BOS Genesis Agent UX
- BOS Genesis Operational Copilot

---

## 3. Objective

The system must allow an authorized user to log in to a web UX application and use a chatbot to perform BOS Genesis operational workflows.

The chatbot must use GPT-5 from Azure as the LLM provider. Azure endpoint, deployment, authentication, and API version details will be supplied later and must remain configurable.

The chatbot must support these workflow families:

1. Release-note creation.
2. MoP creation.
3. MoP execution.
4. Helm management.
5. Kubernetes management.

The assistant must be bounded. GPT-5 reasons, plans, and chooses actions, but the backend validates policy, invokes tools, manages approval, executes workflow steps, records evidence, and produces final artifacts.

LLM provider standardization:

- LLM provider boundary: GPT-5-compatible model endpoint.
- V1 implementation: Azure OpenAI GPT-5 deployment.
- Integration: LangChain wrapper.
- Browser never calls the model endpoint directly.
- Azure endpoint, deployment name, authentication mode, and API version remain configurable.

---

## 4. Design Principles

| Principle | Design Decision |
|---|---|
| Backend owns secrets | Browser never receives Azure OpenAI keys, Kubernetes tokens, Helm credentials, or service tokens. |
| Chatbot is bounded | GPT-5 can only use registered tools and approved workflow adapters. |
| Human approval for risk | Destructive or state-changing operations require explicit approval. |
| Evidence first | Operational workflows must collect evidence before and after actions. |
| Trace everything | User prompts, plans, tool calls, approvals, and outputs must be auditable. |
| Refresh-safe by default | Browser pages render persisted state; workflow truth lives in PostgreSQL and backend workers. |
| Least privilege | Tools are scoped by user role, environment, namespace, and workflow type. |
| Artifact-oriented | Release notes, MoPs, execution logs, and reports are durable artifacts. |
| Provider configurable | Azure GPT-5 configuration is injected through environment variables or secret manager. |

---

## 5. Scope

### 5.1 In Scope

- Authenticated web login.
- Role-based user access.
- Chatbot UI with streaming response updates.
- Azure GPT-5 LLM integration through backend service only.
- Bounded task instructions and system prompt.
- Workflow orchestration for release notes, MoPs, Helm, and Kubernetes.
- Tool registry with allowlisted tools.
- MCP client layer for configured MCP servers.
- REST API caller for allowlisted internal/service endpoints.
- Safe PowerShell runner for approved templates.
- Validation engine for API, Kubernetes, Helm, artifact, and execution results.
- Error diagnosis, bounded retry, and safe recovery proposal logic.
- Policy guard and approval workflow.
- Artifact generation and storage.
- Audit log and run history.
- Persistent transaction history, replayable progress events, and page rehydration for all workflow pages.
- Memory/context retrieval for known fixes, project facts, prior MoPs, previous release notes, traces, and analytics.
- Basic admin settings for model configuration, tool configuration, policies, and environments.

### 5.2 Out of Scope for Initial Version

- Browser-side LLM calls.
- Arbitrary shell execution.
- Arbitrary file deletion or modification.
- Direct unbounded Kubernetes cluster administration.
- Unrestricted Kubernetes patch/delete/scale operations.
- Unapproved production changes.
- Secret discovery or credential extraction.
- General-purpose internet browsing or uncontrolled downloads.
- General-purpose ChatGPT clone behavior.
- Fully autonomous cluster administration.
- Automated execution of destructive actions without human approval.
- User self-registration unless explicitly enabled later.

---

## 6. Assumptions

1. GPT-5 is exposed through Azure with deployment details to be provided later.
2. Existing BOS Genesis agents/MCP services will be integrated through HTTP APIs or MCP adapters.
3. The first version may run locally or in a controlled internal environment.
4. Production rollout will use stronger identity integration such as enterprise SSO or Azure Entra ID.
5. The backend is Python, with FastAPI as the recommended web framework.
6. The frontend is JavaScript, HTML, and CSS, using Bootstrap 5.3 and optional jQuery, Chart.js, and Plotly.js.
7. Azure GPT-5 is integrated through LangChain-compatible wrappers.
8. LangGraph is the workflow runtime for planning, conditional routing, tool execution, approval interrupts, validation, recovery, and resumability.
9. LangMem is used for memory extraction, search, summarization, and memory-management workflows.
10. Short-term memory uses LangMem-managed summaries/tools plus LangGraph state/checkpointing.
11. PostgreSQL is the primary transactional store and the selected store for episodic and procedural memory.
12. Qdrant is the selected semantic memory store.
13. PostgreSQL stores detailed application logs, tool logs, LLM explanations, model-provided reasoning summaries, and human-review analytics.
14. Redis is optional for cache, distributed locks, run event buffers, and rate limiting.
15. MongoDB is not recommended for the initial build unless a future document-native trace store is required.
16. Object storage can be local filesystem in development and S3-compatible/Azure Blob storage in deployed environments.
17. Kubernetes and Helm tools will default to read-only mode unless the user role, policy, and autonomy/approval rules all allow mutation.
18. The browser may be refreshed or navigated away at any time; workflow execution and progress state must remain durable in PostgreSQL.
19. Clearing a transaction from the UI means hiding it for that user, not deleting audit records, artifacts, or run evidence.

---

## 7. User Roles

| Role | Description |
|---|---|
| `viewer` | Can view prior runs, reports, release notes, MoPs, and read-only cluster information. |
| `operator` | Can start chatbot workflows and execute low-risk read-only tools. |
| `approver` | Can approve guarded state-changing actions such as Helm upgrade or Kubernetes restart. |
| `admin` | Can manage users, environments, workflow settings, LLM settings, and tool policies. |

### 7.1 Target User Mapping

The HLD identifies the system as useful for several operational personas. The UX and authorization model should support these persona-specific needs.

| Target User | Primary Need | Typical Role |
|---|---|---|
| Developer | Diagnose service/API failures and validate fixes. | `operator` |
| Test Engineer | Run bounded validation workflows and compare results. | `operator` |
| Platform Engineer | Inspect Kubernetes resources, logs, ingress, services, and Helm releases. | `operator` or `approver` |
| Solution Architect | Demonstrate safe autonomous workflow orchestration. | `viewer` or `operator` |
| Citizen Developer | Execute approved templates without needing low-level tool knowledge. | `operator` |

### 7.2 Role Capability Matrix

| Capability | Viewer | Operator | Approver | Admin |
|---|---:|---:|---:|---:|
| Log in | Yes | Yes | Yes | Yes |
| Start chat session | No | Yes | Yes | Yes |
| Create release note draft | View only | Yes | Yes | Yes |
| Create MoP draft | View only | Yes | Yes | Yes |
| Execute approved MoP | No | Yes | Yes | Yes |
| Read Kubernetes resources | Yes | Yes | Yes | Yes |
| Change Kubernetes resources | No | Needs approval | Approve/execute | Configure policy |
| Read Helm status | Yes | Yes | Yes | Yes |
| Helm upgrade/rollback | No | Needs approval | Approve/execute | Configure policy |
| Manage policies | No | No | No | Yes |
| Manage users | No | No | No | Yes |

---

## 8. High-Level Architecture

```mermaid
flowchart TD
    U[User] --> B[Browser UX]
    B --> AUTH[Auth API]
    B --> CHAT[Chat API]
    B --> TX[Transaction History API]
    CHAT --> ORCH[Agent Orchestrator]
    TX --> RUNSTATE[Run State Service]
    ORCH --> LLM[Azure GPT-5 Client]
    ORCH --> POLICY[Policy Guard]
    ORCH --> TOOLS[Tool Registry]
    ORCH --> MEMORY[Context and Memory Manager]
    ORCH --> ART[Artifact Service]
    ORCH --> AUDIT[Audit Service]
    ORCH --> OBS[Observability Layer]
    ORCH --> RUNSTATE

    TOOLS --> RN[Release Note Adapter]
    TOOLS --> MOPC[MoP Creation Adapter]
    TOOLS --> MOPE[MoP Execution Adapter]
    TOOLS --> HELM[Helm Management Adapter]
    TOOLS --> K8S[Kubernetes Management Adapter]
    TOOLS --> MCP[MCP Client Adapter]
    TOOLS --> REST[REST API Adapter]
    TOOLS --> PS[Safe PowerShell Runner]
    TOOLS --> VAL[Validation Tool]
    TOOLS --> REPORT[Report Generator]

    MEMORY --> PG[(PostgreSQL: Episodic and Procedural Memory)]
    MEMORY --> REDIS[(Redis Optional Cache/Locks)]
    MEMORY --> QDRANT[(Qdrant Semantic Memory)]
    MEMORY --> CH[(PostgreSQL Logs and Review Analytics)]
    RUNSTATE --> PG
    ART --> OBJ[(Artifact Storage)]
    AUDIT --> PG
    OBS --> OTEL[OpenTelemetry Optional]

    RN --> RNSVC[Release Note Agent/API]
    MOPC --> MOPCSVC[MoP Creation Agent/API]
    MOPE --> MOPESVC[MoP Execution Agent/API]
    HELM --> HELMSVC[Helm Manager MCP/API]
    K8S --> K8SSVC[K8s Inspector/Manager MCP/API]
    MCP --> MCP1[API/Test MCP]
    MCP --> MCP2[Knowledge/RAG MCP]
    MCP --> MCP3[Observability MCP]
```

### 8.1 Logical Layering

This LLD follows the HLD layering model and maps each layer to implementation modules.

```mermaid
flowchart LR
    A[Presentation Layer: Web UX] --> B[Agent API Layer: FastAPI]
    B --> C[Reasoning Layer: Azure GPT-5]
    B --> D[Orchestration Layer: Planner / Executor / Verifier]
    D --> E[Tool Execution Layer: MCP / REST / PowerShell / K8s / Helm]
    D --> F[Policy + Approval Layer]
    D --> G[Memory Layer]
    D --> H[Observability + Audit Layer]
```

| Logical Layer | LLD Implementation |
|---|---|
| Presentation | Login, chat console, run detail, artifacts, approval queue, admin screens. |
| Agent API | FastAPI routes for auth, chat, runs, approvals, tools, memory, artifacts, reports. |
| Reasoning | Azure GPT-5 client with bounded prompts, tool schemas, redaction, retries, and trace metadata. |
| Orchestration | Intent classifier, planner, executor, verifier, recovery loop, run state machine. |
| Tool Execution | MCP, REST, PowerShell, Kubernetes, Helm, validation, artifact, and report tools. |
| Policy + Approval | RBAC, scope control, risk classifier, approval workflow, blocked action handling. |
| Memory | LangMem, LangGraph checkpointing, PostgreSQL, Qdrant, optional Redis. |
| Observability + Audit | PostgreSQL structured logs/review analytics, PostgreSQL audit records, optional OpenTelemetry. |

---

## 9. Runtime Flow

```mermaid
sequenceDiagram
    participant User
    participant UX as Web UX
    participant API as FastAPI Backend
    participant Auth as Auth Service
    participant Orchestrator as Agent Orchestrator
    participant GPT as Azure GPT-5
    participant Policy as Policy Guard
    participant Tools as Tool Registry
    participant Artifact as Artifact Service
    participant Audit as Audit Log

    User->>UX: Enter username/password
    UX->>API: POST /api/auth/login
    API->>Auth: Validate credentials
    Auth-->>API: Session/JWT
    API-->>UX: Authenticated session

    User->>UX: Send chatbot request
    UX->>API: POST /api/chat/sessions/{id}/messages
    API->>Orchestrator: Create run context
    Orchestrator->>GPT: Classify intent and draft plan
    GPT-->>Orchestrator: Plan and proposed tool calls
    Orchestrator->>Policy: Evaluate plan/tool action
    Policy-->>Orchestrator: Allow, deny, or approval_required
    Orchestrator->>Tools: Execute allowed tool
    Tools-->>Orchestrator: Tool result and evidence
    Orchestrator->>Artifact: Save generated artifacts if any
    Orchestrator->>Audit: Record prompt, action, result, decision
    Orchestrator->>GPT: Continue with observations
    GPT-->>Orchestrator: Final answer/report
    Orchestrator-->>API: Stream run events
    API-->>UX: SSE/WebSocket events
    UX-->>User: Plan, progress, approval prompts, final output
```

Refresh/navigation recovery flow:

1. Page loads and requests the user's visible transactions.
2. If the page has a selected or active run, the page requests `/api/runs/{run_id}/snapshot`.
3. The page renders the persisted snapshot immediately.
4. The page requests missed events after the last stored sequence.
5. If the run is still active, the page opens SSE with `after_event_id` or `after_sequence` and continues live rendering.
6. If the user moves to another page, the backend worker keeps executing and persists events until terminal state.

---

## 10. Recommended Technology Stack

| Layer | Recommended Technology | Notes |
|---|---|---|
| Frontend | HTML + CSS + JavaScript | Plain browser UI; no React requirement for initial build. |
| UI toolkit | Bootstrap 5.3 | Use one design system consistently. |
| JavaScript helpers | jQuery optional | Use only where it simplifies DOM, modal, and AJAX code. |
| Charts | Chart.js and Plotly.js | Chart.js for dashboard metrics; Plotly.js for interactive diagnostic analysis. |
| Backend API | Python FastAPI | Pure Python backend, REST APIs, SSE streaming, auth, OpenAPI docs. |
| Auth | Session cookie + JWT access token | SSO/Entra ID can be added later. |
| LLM | Azure GPT-5 deployment | Deployment name, endpoint, auth mode, and API version configurable. |
| LLM integration | LangChain | Azure GPT-5 wrapper, tool binding, structured output, streaming. |
| Agent runtime | LangGraph | Workflow state machines, conditional edges, interrupts, checkpointing, recovery. |
| Memory framework | LangMem | Memory extraction, search, summarization, and procedure/profile management. |
| Tool schema | Pydantic models | Strong validation for tool inputs, outputs, and policy decisions. |
| Streaming | Server-Sent Events first, WebSocket optional | SSE is sufficient for run progress and must support event replay/resume. |
| Background execution | FastAPI background tasks first; separate Python worker later | Keeps model/tool work alive after browser refresh or navigation. |
| Primary DB | PostgreSQL | Users, chats, runs, run events, user transaction views, artifacts, approvals, episodic memory, procedural memory. |
| Short-term memory | LangMem + LangGraph checkpointing | Current run state, summaries, conversation context, working memory. |
| Semantic memory | Qdrant | Similar issue, known fix, prior artifact and runbook retrieval. |
| Procedural memory | PostgreSQL source of truth, Qdrant semantic index optional | Procedures, workflow templates, safe remediation recipes. |
| Logs and analytics | PostgreSQL | Detailed logs, tool events, LLM explanations, review analytics. |
| Cache/locks | Redis optional | Cache, distributed locks, event buffers, rate limiting. |
| Artifact storage | Local filesystem initially, Azure Blob later | Stores release notes, MoPs, reports, evidence bundles. |
| Tool integration | REST + MCP client adapters + safe PowerShell runner | Existing agents remain separate services. |
| Deployment | Docker Compose initially, Kubernetes later | Supports local and cluster deployment. |

---

## 11. Application Pages

| Route | Page | Purpose |
|---|---|---|
| `/login` | Login | Authenticate the user. |
| `/` | Chat Console | Main chatbot workspace. |
| Shared floating drawer | Transaction Sidebar | Hidden-by-default left drawer that lists and restores prior workflow transactions. |
| `/sessions` | Chat Sessions | List and resume previous sessions. |
| `/runs/{run_id}` | Run Detail | Show plan, steps, tool calls, evidence, approvals, final report. |
| `/release-notes` | Release Notes | Generate release notes from GitHub URLs, stream refresh-safe progress/reasoning summaries, and browse generated artifacts. |
| `/mops` | MoP Library | Browse generated MoPs and execution reports. |
| `/approvals` | Approval Queue | Review pending high-risk actions. |
| `/helm` | Helm Console | Read Helm releases and trigger approved workflows. |
| `/kubernetes` | Kubernetes Console | Read cluster resources and trigger approved workflows. |
| `/admin/users` | User Admin | Manage local users and roles. |
| `/admin/settings` | System Settings | Configure LLM, tools, environments, and policy. |

---

## 12. UX Component Design

### 12.1 Login Page Components

| Component | Responsibility |
|---|---|
| `LoginForm` | Captures username/password. |
| `AuthErrorBanner` | Shows failed login or expired session. |
| `EnvironmentBadge` | Displays target environment such as local, dev, stage. |

### 12.2 Chat Console Components

| Component | Responsibility |
|---|---|
| `ChatShell` | Overall authenticated chat layout. |
| `ConversationPane` | Shows user and assistant messages. |
| `PromptComposer` | User input, attachments, workflow selector, submit button. |
| `WorkflowSelector` | Release note, MoP creation, MoP execution, Helm, Kubernetes, general BOS Genesis task. |
| `EnvironmentSelector` | Selects allowed environment/cluster/namespace. |
| `RunStatusBar` | Shows run status, current step, elapsed time. |
| `TransactionSidebar` | Floating ChatGPT-style left drawer for historical and in-progress transactions. |
| `RunStateMachineController` | Hydrates snapshots, replays events, reconnects SSE, and prevents refresh data loss. |
| `PlanPanel` | Shows generated plan and risk labels. |
| `ToolTimeline` | Shows tool calls, inputs, outputs, status, evidence. |
| `ApprovalModal` | Handles approve/reject/modify for guarded actions. |
| `ArtifactPanel` | Shows generated release notes, MoPs, reports, and download links. |
| `MemoryPanel` | Shows relevant facts or prior issues used by the agent. |

### 12.3 Persistent Transaction Sidebar

The transaction sidebar is a shared UX component used by every authenticated workflow page.

Behavior:

- Collapsed/hidden by default, with a compact launcher on the left edge.
- Opens as a floating drawer above the page content.
- Lists visible transactions for the logged-in user, newest first.
- Shows workflow icon/type, generated title, status, started/updated time, model label, agent/tool family, and artifact badges.
- Supports filtering by workflow type and status in a later iteration.
- Selecting a transaction loads the appropriate page state and run snapshot.
- Active transactions reconnect to live SSE automatically.
- Clear hides the item from the user's sidebar but keeps audit data, artifacts, and logs.
- Closing the drawer returns it to the hidden launcher state.

### 12.4 Admin Components

| Component | Responsibility |
|---|---|
| `UserTable` | Manage local users and roles. |
| `ToolRegistryTable` | Enable/disable tools and show risk levels. |
| `PolicyRuleEditor` | View and edit guardrail policy rules. |
| `EnvironmentConfigEditor` | Configure namespaces, clusters, Helm repositories, and endpoints. |
| `ModelConfigView` | Shows Azure GPT-5 deployment configuration status without exposing secrets. |

---

## 13. Frontend State Model

```typescript
type WorkflowType =
  | "release_note_creation"
  | "mop_creation"
  | "mop_execution"
  | "helm_management"
  | "k8s_management"
  | "general_bosgenesis";

type ChatSession = {
  sessionId: string;
  title: string;
  userId: string;
  activeRunId?: string;
  createdAt: string;
  updatedAt: string;
};

type ChatMessage = {
  messageId: string;
  sessionId: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  runId?: string;
  createdAt: string;
};

type RunEvent = {
  eventId: string;
  runId: string;
  eventType:
    | "run_created"
    | "plan_created"
    | "step_started"
    | "tool_call_started"
    | "tool_call_completed"
    | "approval_required"
    | "artifact_created"
    | "run_completed"
    | "run_failed";
  payload: Record<string, unknown>;
  timestamp: string;
  sequence?: number;
};

type TransactionSummary = {
  runId: string;
  sessionId: string;
  workflowType: WorkflowType;
  title: string;
  status: "created" | "planning" | "running" | "waiting_for_approval" | "completed" | "failed" | "cancelled";
  modelLabel?: string;
  agentLabel?: string;
  artifactCount: number;
  lastEventSequence: number;
  startedAt: string;
  updatedAt: string;
};

type WorkflowPageState = {
  selectedRunId?: string;
  status: TransactionSummary["status"] | "idle";
  snapshotLoaded: boolean;
  lastEventSequence: number;
  events: RunEvent[];
  artifacts: Artifact[];
};
```

---

## 14. Backend Project Structure

```text
bosgenesis-esda/
  backend/
    app/
      main.py
      config.py
      dependencies.py
      api/
        routes_auth.py
        routes_chat.py
        routes_runs.py
        routes_approvals.py
        routes_artifacts.py
        routes_tools.py
        routes_admin.py
        routes_health.py
      auth/
        password.py
        session_service.py
        jwt_service.py
        rbac.py
      llm/
        azure_gpt5.py
        prompts.py
        token_budget.py
        response_parser.py
        structured_outputs.py
        reasoning_capture.py
      graphs/
        base_state.py
        router_graph.py
        release_note_graph.py
        mop_creation_graph.py
        mop_execution_graph.py
        helm_graph.py
        k8s_graph.py
      chains/
        intent_classifier.py
        planner.py
        verifier.py
        recovery.py
        report_writer.py
      agent/
        orchestrator.py
        executor.py
        run_state.py
      workflows/
        release_notes.py
        mop_creation.py
        mop_execution.py
        helm_management.py
        k8s_management.py
      tools/
        registry.py
        base.py
        rest_tool.py
        mcp_tool.py
        powershell_tool.py
        artifact_tool.py
        validation_tool.py
      policy/
        guard.py
        rules.py
        risk_classifier.py
      memory/
        manager.py
        langmem_service.py
        short_term.py
        episodic_postgres.py
        semantic_qdrant.py
        procedural_store.py
      artifacts/
        service.py
        renderers.py
        storage.py
      logging/
        event_logger.py
        postgres_logger.py
        redaction.py
        llm_review_logger.py
      observability/
        otel.py
        audit.py
      db/
        models.py
        migrations/
      schemas/
        auth.py
        chat.py
        run.py
        tool.py
        artifact.py
        approval.py
        admin.py
    tests/
      test_auth.py
      test_rbac.py
      test_policy_guard.py
      test_orchestrator.py
      test_workflow_release_notes.py
      test_workflow_mop.py
      test_tools.py
  frontend/
    static/
      css/
      js/
      vendor/
    templates/
  knowledge-base/
    baseline_idea.md
    bosgenesis_esda_chatbot_lld.md
```

---

## 15. Configuration

### 15.1 Environment Variables

```env
APP_ENV=local
APP_NAME=bosgenesis-esda
APP_BASE_URL=http://localhost:8080

DATABASE_URL=postgresql://esda:esda@localhost:5432/esda
REDIS_URL=redis://localhost:6379/0
QDRANT_URL=http://localhost:6333
QDRANT_ISSUE_COLLECTION=issue_memory
QDRANT_ARTIFACT_COLLECTION=artifact_memory
QDRANT_PROCEDURE_COLLECTION=procedure_memory
DATABASE_URL=postgresql+psycopg://postgres:<password>@10.99.52.176:5432/esda
POSTGRES_LOG_SCHEMA=public
ARTIFACT_STORAGE_PROVIDER=local
ARTIFACT_LOCAL_PATH=./data/artifacts

AUTH_MODE=local
JWT_ISSUER=bosgenesis-esda
JWT_AUDIENCE=bosgenesis-users
JWT_ACCESS_TOKEN_MINUTES=30
SESSION_COOKIE_NAME=esda_session
SESSION_COOKIE_SECURE=false

AZURE_OPENAI_ENDPOINT=replace-me
AZURE_OPENAI_API_KEY=replace-me
AZURE_OPENAI_API_VERSION=replace-me
AZURE_OPENAI_GPT5_DEPLOYMENT=replace-me
AZURE_OPENAI_USE_V1_API=true
AZURE_OPENAI_REASONING_EFFORT=medium
AZURE_OPENAI_REASONING_SUMMARY=auto
AZURE_OPENAI_TIMEOUT_SECONDS=120

LANGGRAPH_CHECKPOINTER=postgres
LANGGRAPH_MAX_RETRIES=2
LANGMEM_ENABLED=true
LANGMEM_SHORT_TERM_SUMMARY_ENABLED=true
LANGMEM_BACKGROUND_PROCESSING_ENABLED=true

DEFAULT_WORKFLOW=general_bosgenesis
DEFAULT_ENVIRONMENT=local
DEFAULT_NAMESPACE=bosgenesis
DEFAULT_AUTONOMY_MODE=semi_autonomous

RELEASE_NOTE_AGENT_URL=http://localhost:8101
MOP_CREATION_AGENT_URL=http://localhost:8102
MOP_EXECUTION_AGENT_URL=http://localhost:8103
HELM_MANAGER_URL=http://localhost:8104
K8S_MANAGER_URL=http://localhost:8105

MCP_K8S_INSPECTOR_URL=http://localhost:8201/mcp
MCP_API_TEST_URL=http://localhost:8202/mcp
MCP_DOCS_RAG_URL=http://localhost:8203/mcp
MCP_OBSERVABILITY_URL=http://localhost:8204/mcp
MCP_MOP_VALIDATOR_URL=http://localhost:8205/mcp

POWERSHELL_RUNNER_URL=http://localhost:8090
LLM_REVIEW_LOGGING_ENABLED=true
LLM_REASONING_SUMMARY_LOGGING_ENABLED=true
POSTGRES_AGENT_EVENT_TABLE=agent_event_logs
POSTGRES_LLM_REVIEW_TABLE=llm_reasoning_review_logs
POSTGRES_TOOL_LOG_TABLE=tool_execution_logs
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

### 15.2 Azure GPT-5 Configuration Contract

The backend must not hardcode Azure details. It must load these values at startup:

| Config Key | Purpose |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL. |
| `AZURE_OPENAI_API_KEY` | API key if key-based auth is used. |
| `AZURE_OPENAI_API_VERSION` | Azure API version provided later. |
| `AZURE_OPENAI_GPT5_DEPLOYMENT` | Azure deployment name for GPT-5. |
| `AZURE_OPENAI_TIMEOUT_SECONDS` | Request timeout. |
| `AZURE_OPENAI_AUTH_MODE` | `api_key` initially, `managed_identity` later if needed. |
| `AZURE_OPENAI_USE_V1_API` | Use LangChain `ChatOpenAI` against Azure v1 endpoint when available. |
| `AZURE_OPENAI_REASONING_EFFORT` | Requested reasoning effort for supported Azure reasoning models. |
| `AZURE_OPENAI_REASONING_SUMMARY` | Requested model-provided reasoning summary level when supported. |
| `LANGGRAPH_CHECKPOINTER` | Backing checkpoint store for LangGraph state. |
| `LANGMEM_ENABLED` | Enables LangMem memory extraction/search/summarization workflows. |
| `LLM_REVIEW_LOGGING_ENABLED` | Enables PostgreSQL logging for human review of LLM plans and explanations. |

---

## 16. Core API Endpoints

### 16.1 Authentication

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Authenticate user and create session. |
| `POST` | `/api/auth/logout` | End session. |
| `GET` | `/api/auth/me` | Return current user, roles, permissions. |
| `POST` | `/api/auth/refresh` | Refresh access token/session. |

Login request:

```json
{
  "username": "operator1",
  "password": "replace-me"
}
```

Login response:

```json
{
  "user": {
    "user_id": "usr_001",
    "username": "operator1",
    "roles": ["operator"]
  },
  "access_token": "jwt-token",
  "expires_in": 1800
}
```

### 16.2 Chat Sessions

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/chat` | HLD-compatible shortcut for submitting a bounded user task. |
| `POST` | `/api/chat/sessions` | Create a new chat session. |
| `GET` | `/api/chat/sessions` | List user chat sessions. |
| `GET` | `/api/transactions` | List visible workflow transactions for the floating sidebar. |
| `GET` | `/api/chat/sessions/{session_id}` | Get session with messages. |
| `POST` | `/api/chat/sessions/{session_id}/messages` | Send user message and start/continue run. |
| `DELETE` | `/api/chat/sessions/{session_id}` | Archive a session. |

Send message request:

```json
{
  "message": "Create release notes for the latest bosgenesis deployment.",
  "workflow_type": "release_note_creation",
  "environment": "dev",
  "namespace": "bosgenesis",
  "autonomy_mode": "semi_autonomous",
  "attachments": []
}
```

Send message response:

```json
{
  "message_id": "msg_20260622_001",
  "run_id": "run_20260622_001",
  "status": "created",
  "events_url": "/api/runs/run_20260622_001/events"
}
```

### 16.3 Runs and Events

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/runs` | Start a new workflow run directly. |
| `GET` | `/api/runs/{run_id}` | Get run state and final report. |
| `GET` | `/api/runs/{run_id}/snapshot` | Get latest durable run snapshot for page rehydration. |
| `GET` | `/api/runs/{run_id}/events` | Stream run events via SSE; supports `after_event_id` or `after_sequence`. |
| `POST` | `/api/transactions/{run_id}/clear` | Hide/archive a transaction for the current user without deleting audit records. |
| `POST` | `/api/runs/{run_id}/stop` | Stop a running workflow. |
| `POST` | `/api/runs/{run_id}/cancel` | HLD-compatible alias for stopping/cancelling a run. |
| `POST` | `/api/runs/{run_id}/approve` | HLD-compatible run-scoped approval endpoint. |
| `GET` | `/api/runs/{run_id}/tool-calls` | List tool calls and evidence. |

### 16.4 Approvals

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/approvals` | List pending approvals. |
| `POST` | `/api/approvals/{approval_id}/approve` | Approve guarded action. |
| `POST` | `/api/approvals/{approval_id}/reject` | Reject guarded action. |
| `POST` | `/api/approvals/{approval_id}/modify` | Modify parameters and re-evaluate policy. |

### 16.5 Artifacts

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/artifacts` | List artifacts visible to current user. |
| `GET` | `/api/artifacts/{artifact_id}` | Get artifact metadata and rendered preview. |
| `GET` | `/api/artifacts/{artifact_id}/download` | Download artifact content. |
| `GET` | `/api/reports/{run_id}` | HLD-compatible endpoint to download a final execution report. |
| `POST` | `/api/artifacts/{artifact_id}/approve` | Approve release note or MoP artifact. |

### 16.6 Memory

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/memory/search` | Search exact, episodic, semantic, and known-fix memory. |
| `GET` | `/api/memory/facts` | List visible memory facts for the current scope. |
| `GET` | `/api/memory/known-fixes` | List known issue/fix records. |

### 16.7 Admin

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/admin/users` | List users. |
| `POST` | `/api/admin/users` | Create user. |
| `PATCH` | `/api/admin/users/{user_id}` | Update user roles/status. |
| `GET` | `/api/admin/tools` | List configured tools. |
| `PATCH` | `/api/admin/tools/{tool_name}` | Enable/disable tool or edit risk metadata. |
| `GET` | `/api/admin/policies` | List policy rules. |
| `PATCH` | `/api/admin/policies/{rule_id}` | Update policy rule. |
| `GET` | `/api/admin/model` | Show masked LLM configuration status. |

---

## 17. Backend Domain Models

### 17.1 User

```python
class User(BaseModel):
    user_id: str
    username: str
    password_hash: str
    display_name: str
    email: str | None
    roles: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
```

### 17.2 Chat Session

```python
class ChatSession(BaseModel):
    session_id: str
    user_id: str
    title: str
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime
```

### 17.3 Chat Message

```python
class ChatMessage(BaseModel):
    message_id: str
    session_id: str
    run_id: str | None
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    metadata: dict
    created_at: datetime
```

### 17.4 Agent Run

```python
class AgentRun(BaseModel):
    run_id: str
    session_id: str
    user_id: str
    workflow_type: WorkflowType
    environment: str
    namespace: str | None
    autonomy_mode: str
    status: Literal[
        "created",
        "planning",
        "waiting_for_approval",
        "running",
        "completed",
        "failed",
        "cancelled"
    ]
    goal: str
    final_summary: str | None
    current_node: str | None
    last_event_sequence: int
    worker_status: Literal["queued", "running", "idle", "failed"]
    state_snapshot: dict
    created_at: datetime
    updated_at: datetime
```

### 17.5 Run Event

```python
class RunEventRecord(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    message: str
    payload: dict
    created_at: datetime
```

### 17.6 User Transaction View

```python
class UserTransactionView(BaseModel):
    user_id: str
    run_id: str
    hidden_at: datetime | None
    pinned: bool
    last_opened_at: datetime | None
```

### 17.7 Plan Step

```python
class PlanStep(BaseModel):
    step_id: str
    run_id: str
    sequence: int
    title: str
    description: str
    tool_name: str | None
    risk_level: Literal["low", "medium", "high", "critical"]
    requires_approval: bool
    validation_rule: dict | None
    status: Literal["pending", "running", "completed", "failed", "skipped"]
```

### 17.8 Tool Call

```python
class ToolCallRecord(BaseModel):
    tool_call_id: str
    run_id: str
    step_id: str | None
    tool_name: str
    tool_category: str
    request_json: dict
    response_json: dict | None
    status: Literal["created", "running", "completed", "failed", "blocked"]
    policy_decision: str
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None
```

### 17.9 Artifact

```python
class Artifact(BaseModel):
    artifact_id: str
    run_id: str
    artifact_type: Literal[
        "release_note",
        "mop",
        "mop_execution_report",
        "helm_report",
        "k8s_report",
        "general_report"
    ]
    title: str
    storage_uri: str
    content_type: str
    status: Literal["draft", "approved", "published", "archived"]
    created_by: str
    created_at: datetime
    updated_at: datetime
```

---

## 18. Database Design

### 18.1 `users`

```sql
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    roles JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.2 `chat_sessions`

```sql
CREATE TABLE chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.3 `chat_messages`

```sql
CREATE TABLE chat_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id),
    run_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.4 `agent_runs`

```sql
CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    workflow_type TEXT NOT NULL,
    environment TEXT NOT NULL,
    namespace TEXT,
    autonomy_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    goal TEXT NOT NULL,
    final_summary TEXT,
    current_node TEXT,
    last_event_sequence INT NOT NULL DEFAULT 0,
    worker_status TEXT NOT NULL DEFAULT 'queued',
    state_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.4.1 `run_events`

```sql
CREATE TABLE run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    sequence INT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, sequence)
);
```

### 18.4.2 `user_transaction_views`

```sql
CREATE TABLE user_transaction_views (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    hidden_at TIMESTAMP,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    last_opened_at TIMESTAMP,
    PRIMARY KEY (user_id, run_id)
);
```

### 18.5 `plan_steps`

```sql
CREATE TABLE plan_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    sequence INT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    tool_name TEXT,
    risk_level TEXT NOT NULL,
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    validation_rule JSONB,
    status TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

### 18.6 `tool_calls`

```sql
CREATE TABLE tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    step_id TEXT REFERENCES plan_steps(step_id),
    tool_name TEXT NOT NULL,
    tool_category TEXT NOT NULL,
    request_json JSONB NOT NULL,
    response_json JSONB,
    status TEXT NOT NULL,
    policy_decision TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);
```

### 18.7 `approvals`

```sql
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    step_id TEXT REFERENCES plan_steps(step_id),
    requested_by TEXT NOT NULL REFERENCES users(user_id),
    decided_by TEXT REFERENCES users(user_id),
    action_type TEXT NOT NULL,
    proposed_action JSONB NOT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP,
    decision_comment TEXT
);
```

### 18.8 `artifacts`

```sql
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    content_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_by TEXT NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.9 `audit_events`

```sql
CREATE TABLE audit_events (
    audit_id TEXT PRIMARY KEY,
    run_id TEXT,
    user_id TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.10 `memory_facts`

```sql
CREATE TABLE memory_facts (
    memory_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    confidence NUMERIC NOT NULL DEFAULT 1.0,
    source_run_id TEXT REFERENCES agent_runs(run_id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.11 PostgreSQL `episodic_memory_episodes`

PostgreSQL stores episodic memory as structured, queryable run history. Large raw logs should go to PostgreSQL or artifact/object storage, with PostgreSQL retaining references and summaries.

```sql
CREATE TABLE episodic_memory_episodes (
    episode_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    workflow_type TEXT NOT NULL,
    episode_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome TEXT,
    confidence NUMERIC DEFAULT 1.0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.12 PostgreSQL `procedures`

PostgreSQL is the source of truth for procedural memory: approved procedures, workflow templates, safe remediation recipes, and policy playbooks.

```sql
CREATE TABLE procedures (
    procedure_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    procedure_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    version INT NOT NULL,
    status TEXT NOT NULL,
    body JSONB NOT NULL,
    approval_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT REFERENCES users(user_id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 18.13 Qdrant Semantic Memory Collections

Qdrant stores semantic indexes for similarity search. PostgreSQL owns authoritative records; Qdrant stores embeddings and retrieval payloads.

| Collection | Purpose |
|---|---|
| `issue_memory` | Similar operational failures and fixes. |
| `artifact_memory` | Prior MoPs, release notes, execution reports. |
| `procedure_memory` | Embedded procedures and safe remediation patterns. |
| `knowledge_memory` | Internal docs, runbooks, and design snippets. |

Example payload:

```json
{
  "memory_id": "mem_001",
  "scope": "bosgenesis/dev",
  "memory_type": "known_fix",
  "tags": ["kubernetes", "memory-agent", "healthcheck"],
  "issue_signature": "memory-agent-v3 health endpoint returned 503",
  "symptoms": "Health check failed, pod running, ingress present",
  "recommended_fix": "Verify config and restart only after approval",
  "source_run_id": "run_20260622_001",
  "requires_approval": true
}
```

### 18.14 PostgreSQL Review and Log Tables

PostgreSQL stores high-volume application logs, tool execution logs, LLM explanations, model-provided reasoning summaries, and review analytics.

Deployment transport rule:

- Local workstation runs use `DATABASE_URL` pointing to the reachable PostgreSQL endpoint at `10.99.52.176:5432/esda`.
- Helm deployments use `DATABASE_URL` pointing to the in-cluster PostgreSQL service.
- PostgreSQL remains the run-state store; PostgreSQL does not replace run records.
- SQLite is not part of V1 local or Helm deployment profiles.

```sql
CREATE TABLE agent_event_logs (
    event_id String,
    timestamp DateTime64(3),
    run_id String,
    session_id String,
    user_id String,
    workflow_type String,
    graph_node String,
    event_type String,
    severity String,
    message String,
    payload_json String,
    duration_ms UInt64
)
ENGINE = MergeTree
ORDER BY (timestamp, workflow_type, run_id, event_type);
```

```sql
CREATE TABLE llm_reasoning_review_logs (
    review_id String,
    timestamp DateTime64(3),
    run_id String,
    session_id String,
    user_id String,
    workflow_type String,
    graph_node String,
    model_deployment String,
    prompt_version String,
    prompt_hash String,
    user_intent String,
    plan_json String,
    reasoning_summary String,
    tool_choice_json String,
    tool_choice_explanation String,
    risk_explanation String,
    validation_explanation String,
    recovery_explanation String,
    final_answer String,
    redaction_count UInt32,
    human_review_status String DEFAULT 'pending'
)
ENGINE = MergeTree
ORDER BY (timestamp, workflow_type, run_id, graph_node);
```

---

### 18.15 Persistent Progress and Rehydration Rules

PostgreSQL is the source of truth for page state.

Rules:

- `agent_runs.state_snapshot` stores the latest compact page/workflow state.
- `run_events` stores ordered replayable events for the progress panel.
- SSE may use in-memory fanout for live delivery, but every event must already be persisted.
- `user_transaction_views` controls sidebar visibility without deleting records.
- Page refresh must call snapshot + events APIs before opening SSE.
- Completed, failed, cancelled, and approval-waiting runs must all be restorable.
- Active runs must reconnect to SSE and continue showing new progress.
- All workflow pages must share this contract.

---

## 19. Azure GPT-5 Client Design

### 19.1 Responsibilities

The Azure GPT-5 integration must:

- Use LangChain-compatible wrappers for Azure GPT-5.
- Prefer `ChatOpenAI` against Azure v1-compatible endpoints when available.
- Fall back to `AzureChatOpenAI` when a traditional Azure `api_version` is required.
- Send prompts and tool schemas to the Azure GPT-5 deployment.
- Stream assistant tokens and tool-call decisions where supported.
- Request model-supported reasoning summaries when enabled.
- Enforce timeout, retry, and circuit-breaker behavior.
- Redact secrets before sending context to the model.
- Record model metadata, generated plans, tool explanations, risk explanations, validation explanations, and final answers in PostgreSQL review logs.
- Never attempt to capture hidden raw model chain-of-thought.
- Support deployment name configuration rather than hardcoded model names.

### 19.2 Client Interface

```python
class AzureGpt5Service:
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict | None,
        trace_context: dict,
        reasoning_summary: bool = True,
    ) -> LlmResponse:
        ...

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        trace_context: dict,
        reasoning_summary: bool = True,
    ) -> AsyncIterator[LlmStreamEvent]:
        ...

    async def log_for_review(
        self,
        run_id: str,
        graph_node: str,
        response: LlmResponse,
        explanation_fields: dict,
    ) -> None:
        ...
```

### 19.3 LLM Request Metadata

```json
{
  "deployment": "${AZURE_OPENAI_GPT5_DEPLOYMENT}",
  "api_version": "${AZURE_OPENAI_API_VERSION}",
  "temperature": 0.2,
  "max_output_tokens": 8000,
  "tool_choice": "auto",
  "reasoning_effort": "medium",
  "reasoning_summary": "auto",
  "metadata": {
    "run_id": "run_20260622_001",
    "workflow_type": "mop_creation",
    "environment": "dev"
  }
}
```

### 19.4 System Prompt

```text
You are BOS Genesis ESDA Assistant, a bounded operational workflow agent.

You help authorized BOS Genesis users with:
- release-note creation
- MoP creation
- MoP execution
- Helm management
- Kubernetes management

Rules:
1. Use only registered tools supplied by the backend.
2. Create a plan before executing tools.
3. Call tools instead of inventing results.
4. Never request arbitrary shell execution.
5. Never reveal credentials, tokens, connection strings, or secret values.
6. Prefer read-only inspection before proposing any change.
7. Destructive or state-changing actions require backend policy approval.
8. After each tool call, validate the result when validation criteria exist.
9. If an action is outside policy, explain the blocked reason and suggest a safe alternative.
10. Produce evidence-based final reports with tool outputs and validation results.
```

### 19.5 Prompt and Version Governance

Prompt templates must be versioned files and every run must log prompt metadata for review.

```text
prompt_templates/
  system_bounded_agent_v1.md
  planner_v1.md
  verifier_v1.md
  recovery_v1.md
  report_writer_v1.md
```

Required run log fields:

- `prompt_version`
- `prompt_hash`
- `model_deployment`
- `workflow_type`
- `tool_choices`
- `reasoning_summary`
- `policy_decision`
- `validation_result`

The system must store available reasoning summaries, plans, decisions, explanations, observations, and policy rationales instead of hidden chain-of-thought.
---

## 20. LangGraph Orchestrator Design

### 20.1 Responsibilities

LangGraph is the workflow runtime for every chatbot run. Each workflow family should be represented as a graph or subgraph with explicit nodes, conditional edges, approval interrupts, retry limits, and checkpointing.

It must:

1. Authenticate and authorize the user context.
2. Classify the user intent into a workflow type.
3. Retrieve relevant context and memory through LangMem, PostgreSQL, and Qdrant.
4. Ask Azure GPT-5 through LangChain to produce a plan.
5. Validate the plan against policy.
6. Evaluate conditional L4 autonomy eligibility.
7. Execute allowed tool calls.
8. Pause for approval when required.
9. Validate results after every important step.
10. Save artifacts and audit evidence.
11. Store LLM plans, reasoning summaries, explanations, tool choices, and validation results in PostgreSQL for review.
12. Produce final chatbot answer and durable report.

### 20.2 LangGraph Node Pattern

Each workflow graph should use the same node pattern:

1. `load_scope`
2. `retrieve_memory`
3. `classify_intent`
4. `plan`
5. `evaluate_l4_eligibility`
6. `policy_check`
7. `approval_interrupt`
8. `execute_tool`
9. `observe`
10. `validate`
11. `recover_or_continue`
12. `write_memory`
13. `write_postgres_logs`
14. `final_report`

### 20.3 Orchestrator Loop

```mermaid
flowchart TD
    A[Receive Chat Message] --> B[Authorize User]
    B --> C[Classify Workflow]
    C --> D[Load Memory and Context]
    D --> E[Ask Azure GPT-5 for Plan]
    E --> F[Policy Check Plan]
    F --> G{Approval Needed?}
    G -->|Yes| H[Create Approval Request]
    H --> I[Wait for Decision]
    I --> J{Approved?}
    J -->|No| K[Skip or Stop Safely]
    J -->|Yes| L[Execute Step]
    G -->|No| L
    L --> M[Capture Evidence]
    M --> N[Validate Step Result]
    N --> O{Workflow Complete?}
    O -->|No| P[Ask GPT-5 for Next Action]
    P --> F
    O -->|Yes| Q[Generate Artifact/Report]
    K --> Q
    Q --> R[Persist Memory and Audit]
    R --> S[Return Final Chat Response]
```

### 20.4 Autonomy Modes

| Mode | HLD Name | Behavior |
|---|---|---|
| `observe_only` | Observe Only | Read-only tools only. No state changes. |
| `assisted` | Assisted | Agent proposes actions; user approves each action before execution. |
| `dry_run` | Dry Run | Plan and simulate actions. No mutation. |
| `semi_autonomous` | Semi-Autonomous | Execute low-risk actions; request approval for risky actions. |
| `autonomous_safe_mode` | Autonomous Safe Mode | Execute predefined safe workflows only; block high-risk actions. |
| `manual_approval` | Manual Approval | Every tool call requires approval. |
| `conditional_l4` | Conditional L4 | Full workflow execution is allowed only inside a predefined operational design domain with approved tools, scope, stop conditions, validation, rollback, and logging. |

Default for early versions:

```text
semi_autonomous
```

Target mode after sufficient guardrail maturity:

```text
conditional_l4
```

---

## 21. Tool Registry

### 21.1 Tool Definition

```python
class ToolDefinition(BaseModel):
    name: str
    category: str
    description: str
    input_schema: dict
    output_schema: dict | None
    workflow_types: list[str]
    risk_level: Literal["low", "medium", "high", "critical"]
    requires_approval: bool
    enabled: bool
    allowed_roles: list[str]
    allowed_environments: list[str]
    timeout_seconds: int
```

### 21.2 Tool Categories

| Category | Purpose |
|---|---|
| `release_notes` | Generate, validate, and publish release-note artifacts. |
| `mop_creation` | Generate and validate MoP documents. |
| `mop_execution` | Execute approved MoPs step by step. |
| `helm` | Inspect and manage Helm releases. |
| `kubernetes` | Inspect and manage Kubernetes resources. |
| `mcp` | Call configured MCP server tools. |
| `rest` | Call allowlisted REST APIs. |
| `powershell` | Execute approved PowerShell templates only. |
| `validation` | Validate JSON, status, logs, artifacts, and execution outcomes. |
| `artifact` | Save, render, approve, and retrieve generated artifacts. |

### 21.3 Initial Tool Catalog

| Tool | Workflow | Risk | Approval |
|---|---|---:|---:|
| `release_notes.collect_sources` | Release notes | Low | No |
| `release_notes.generate_draft` | Release notes | Low | No |
| `release_notes.validate_draft` | Release notes | Low | No |
| `release_notes.publish` | Release notes | Medium | Yes |
| `mop.create_draft` | MoP creation | Low | No |
| `mop.validate` | MoP creation | Low | No |
| `mop.approve_artifact` | MoP creation | Medium | Yes |
| `mop_execution.load_mop` | MoP execution | Low | No |
| `mop_execution.run_prechecks` | MoP execution | Low | No |
| `mop_execution.execute_step` | MoP execution | Medium/High | Based on step |
| `mop_execution.generate_report` | MoP execution | Low | No |
| `helm.list_releases` | Helm | Low | No |
| `helm.get_status` | Helm | Low | No |
| `helm.diff` | Helm | Medium | No |
| `helm.upgrade` | Helm | High | Yes |
| `helm.rollback` | Helm | High | Yes |
| `k8s.list_resources` | Kubernetes | Low | No |
| `k8s.describe_resource` | Kubernetes | Low | No |
| `k8s.get_logs` | Kubernetes | Medium | No |
| `k8s.rollout_restart` | Kubernetes | High | Yes |
| `k8s.scale_workload` | Kubernetes | High | Yes |
| `k8s.delete_resource` | Kubernetes | Critical | Block by default |

### 21.4 Code-First Tool Execution Contract

All tools must execute through a deterministic request/result contract.

```python
class ToolExecutionRequest(BaseModel):
    run_id: str
    step_id: str
    tool_name: str
    workflow_type: str
    environment: str
    namespace: str | None
    user_id: str
    arguments: dict
    autonomy_mode: str


class ToolExecutionResult(BaseModel):
    status: Literal["success", "failed", "blocked", "approval_required"]
    output: dict | None
    evidence_refs: list[str]
    validation_result: dict | None
    error: dict | None
```

Execution order:

1. Resolve tool from registry.
2. Validate role permission.
3. Validate workflow permission.
4. Validate environment permission.
5. Validate namespace/resource allowance.
6. Validate input schema.
7. Evaluate policy.
8. Request approval if required.
9. Execute tool only if allowed.
10. Validate result.
11. Redact output.
12. Write PostgreSQL logs.
13. Return normalized result.

### 21.5 HLD-Compatible Tool Aliases

The ESDA tool names are domain-specific, but the backend should expose aliases or registry metadata compatible with the HLD's initial tool vocabulary.

| HLD Tool Name | ESDA Tool Mapping | Purpose | Risk |
|---|---|---|---:|
| `mcp_call_tool` | `mcp.call_tool` | Call configured MCP server tool. | Medium |
| `rest_api_get` | `rest.call_api` with `GET` | Execute approved GET API call. | Low |
| `rest_api_post` | `rest.call_api` with `POST` | Execute approved POST API call. | Medium |
| `powershell_http_get` | `powershell.ps_http_get` | Run approved PowerShell HTTP GET template. | Low |
| `powershell_http_post` | `powershell.ps_http_post` | Run approved PowerShell HTTP POST template. | Medium |
| `k8s_get_pods` | `k8s.list_resources` with `kind=Pod` | Inspect pods in approved namespace. | Low |
| `k8s_get_services` | `k8s.list_resources` with `kind=Service` | Inspect services in approved namespace. | Low |
| `k8s_get_logs` | `k8s.get_logs` | Read logs from approved namespace. | Medium |
| `validator_assert_json_path` | `validation.assert_json_path` | Validate response payload. | Low |
| `memory_search` | `memory.search` | Search previous issues/fixes. | Low |
| `memory_write` | `memory.write` | Save fact or issue resolution. | Low |
| `generate_report` | `report.generate` | Generate final execution report. | Low |

### 21.6 MCP Server Integration

MCP servers expose governed tools to the orchestrator. GPT-5 may request a registered MCP action, but the backend must validate server, tool name, scope, arguments, role, and risk before invocation.

| MCP Server | Purpose | Initial Tools |
|---|---|---|
| `bosgenesis-k8s-inspector-mcp` | Kubernetes resource inspection. | `list_pods`, `list_services`, `get_deployment`, `get_logs`, `describe_resource` |
| `bosgenesis-api-test-mcp` | API testing and response validation. | `call_endpoint`, `validate_status`, `validate_schema` |
| `bosgenesis-docs-rag-mcp` | Knowledge search over docs, runbooks, and prior designs. | `search_docs`, `retrieve_context` |
| `bosgenesis-observability-mcp` | Query PostgreSQL logs, review records, OpenTelemetry traces, and operational metrics. | `get_recent_traces`, `get_error_spans`, `query_metrics` |
| `bosgenesis-mop-validator-mcp` | Validate MoP documents and execution steps. | `validate_mop`, `validate_step_order`, `validate_rollback` |

MCP server configuration contract:

```yaml
mcp_servers:
  bosgenesis-k8s-inspector-mcp:
    url: ${MCP_K8S_INSPECTOR_URL}
    enabled: true
    allowed_workflows:
      - k8s_management
      - mop_execution
    allowed_tools:
      - list_pods
      - list_services
      - get_logs
    timeout_seconds: 30
    max_response_bytes: 200000
    auth_mode: service_token
    risk_level: medium
```

MCP response envelope:

```json
{
  "status": "success | failed | denied | timeout",
  "tool_name": "list_pods",
  "normalized_result": {},
  "evidence_refs": [],
  "error": {
    "code": "K8S_TIMEOUT",
    "message": "Timed out calling Kubernetes API",
    "retryable": true
  }
}
```

Before invoking MCP, the backend must validate server, tool name, role, workflow, environment, namespace, argument schema, risk, timeout, and response-size limits.
MCP call sequence:

```mermaid
sequenceDiagram
    participant UX as Web UX
    participant API as Agent Backend
    participant GPT as Azure GPT-5
    participant Policy as Policy Guard
    participant MCP as MCP Client
    participant Server as MCP Server

    UX->>API: Submit bounded goal
    API->>GPT: Ask for plan/tool choice
    GPT-->>API: Tool request: mcp.call_tool
    API->>Policy: Validate server, tool, args, role, scope
    Policy-->>API: allow / deny / approval_required
    API->>MCP: Invoke allowed MCP tool
    MCP->>Server: Execute MCP tool
    Server-->>MCP: Normalized result
    MCP-->>API: Tool result
    API->>GPT: Return observation for next reasoning step
    API-->>UX: Stream progress event
```

---

## 22. Workflow Designs

## 22.1 Release-Note Creation Workflow

### Purpose

Generate release notes from approved sources such as a GitHub URL, commits, tags, Jira items, deployment metadata, PR summaries, or existing `release-note-agent` output. In the implemented V1 hello-world flow, the user enters a GitHub URL, GPT plans the release-note workflow, the backend calls `bosgenesis-release-note-agent` through MCP-compatible tools, the agent returns the initial Markdown/PDF evidence artifacts, GPT finalizes the Markdown draft from that evidence, and PostgreSQL stores the activity trail.

### Inputs

| Input | Required | Notes |
|---|---:|---|
| `github_url` | Required | Repository, compare, release, pull request, or tag URL accepted by policy. |
| `release_name` | Optional | Can be inferred from tag/version. |
| `source_ref_start` | Optional | Commit, tag, date, or release id. |
| `source_ref_end` | Optional | Commit, tag, date, or release id. |
| `service_names` | Optional | Limits release notes to selected services. |
| `target_audience` | Optional | Engineering, operations, business, customer. |
| `format` | Optional | Markdown and PDF are the current V1 outputs; Markdown remains the preview/default authoring format. |

### Flow

```mermaid
flowchart TD
    A[User asks for release notes] --> B[Classify release-note workflow]
    B --> C[Collect source refs and scope]
    C --> D[Call release-note-agent MCP tools]
    D --> E[Hydrate agent Markdown, PDF refs, evidence, and reasoning summary]
    E --> F[Ask GPT-5 to finalize Markdown from agent draft and evidence]
    F --> G[Validate completeness and formatting]
    G --> H[Save Markdown/PDF artifacts and PostgreSQL logs]
    H --> I{Publish requested?}
    I -->|No| J[Return draft artifact]
    I -->|Yes| K[Approval required]
    K --> L[Publish after approval]
```

### Artifact Output

```markdown
# Release Notes: <release_name>

## Summary

## Features

## Fixes

## Operational Changes

## Known Issues

## Deployment Notes

## Source Evidence
```

V1 currently stores two release-note artifacts for a successful run:

- Final ESDA Markdown artifact, generated from release-note-agent evidence and GPT finalization.
- Release-note-agent PDF artifact, preserving the existing PDF renderer look and feel.

The adapter must keep all artifact metadata returned by `release-note-agent`; it must not truncate the list because PDF artifacts may appear after Markdown/HTML/JSON artifacts.

### Live UX and Logging Requirements

- The `/release-notes` page must accept a GitHub URL and optional source range.
- The run timeline must stream source collection, GPT-5 planning, release-note-agent MCP invocation, artifact hydration, draft generation, validation, and Markdown/PDF artifact-save events through SSE.
- The UI may show model-provided reasoning summaries, plans, tool-choice explanations, and validation explanations; the live progress panel should remain scrollable and copyable.
- The UI must not expose hidden chain-of-thought.
- PostgreSQL must log the run, plan summary, release-note-agent request/response summary, GPT-5 reasoning summary, validation result, and final Markdown/PDF artifact metadata.
- Draft generation is read-only and does not require approval; publishing or writing back to GitHub/Confluence/Jira requires approval.

## 22.2 MoP Creation Workflow

### Purpose

Generate a Method of Procedure document for an operational change.

### Inputs

| Input | Required | Notes |
|---|---:|---|
| `change_summary` | Yes | What will be changed. |
| `target_environment` | Yes | local, dev, stage, prod. |
| `service_or_component` | Yes | Target service/component. |
| `implementation_window` | Optional | Planned date/time. |
| `risk_level` | Optional | Can be inferred. |
| `rollback_expectation` | Optional | Required before approval. |

### Flow

```mermaid
flowchart TD
    A[User requests MoP] --> B[Collect missing details]
    B --> C[Retrieve templates and prior MoPs]
    C --> D[Generate prechecks]
    D --> E[Generate implementation steps]
    E --> F[Generate validation and rollback steps]
    F --> G[Policy and quality validation]
    G --> H[Save MoP artifact]
    H --> I[Return draft for review]
```

### MoP Artifact Sections

```markdown
# Method of Procedure

## 1. Change Summary
## 2. Scope
## 3. Roles and Contacts
## 4. Preconditions
## 5. Risk Assessment
## 6. Implementation Steps
## 7. Validation Steps
## 8. Rollback Plan
## 9. Communication Plan
## 10. Approval Record
## 11. Execution Evidence
```

## 22.3 MoP Execution Workflow

### Purpose

Execute an approved MoP in a controlled, step-by-step manner, collecting evidence and requiring approval for guarded steps.

### Inputs

| Input | Required | Notes |
|---|---:|---|
| `mop_id` | Yes | Approved MoP artifact id. |
| `target_environment` | Yes | Must match policy. |
| `execution_mode` | Yes | dry-run or execute. |
| `approval_id` | Conditional | Required for high-risk steps. |

### Flow

```mermaid
flowchart TD
    A[User asks to execute MoP] --> B[Load approved MoP]
    B --> C[Verify user permission]
    C --> D[Run prechecks]
    D --> E{Prechecks pass?}
    E -->|No| F[Stop and report failures]
    E -->|Yes| G[Execute next step]
    G --> H[Collect evidence]
    H --> I[Validate step result]
    I --> J{More steps?}
    J -->|Yes| K{Approval needed?}
    K -->|Yes| L[Pause for approval]
    L --> G
    K -->|No| G
    J -->|No| M[Run postchecks]
    M --> N[Generate execution report]
```

### Execution Report Sections

```markdown
# MoP Execution Report

## Goal
## Environment
## Executed By
## Start and End Time
## Precheck Results
## Step Execution Evidence
## Validation Results
## Deviations
## Rollback Actions
## Final Status
```

## 22.4 Helm Management Workflow

### Purpose

Allow users to inspect Helm releases and perform approved Helm operations through a bounded tool adapter.

### Supported Actions

| Action | Risk | Approval |
|---|---:|---:|
| List releases | Low | No |
| Get release status | Low | No |
| Get values | Medium | No, but secret values redacted |
| Render template | Low | No |
| Diff upgrade | Medium | No |
| Upgrade release | High | Yes |
| Rollback release | High | Yes |
| Uninstall release | Critical | Block by default |

### Flow

```mermaid
flowchart TD
    A[User asks Helm question] --> B[Classify Helm intent]
    B --> C[Run read-only inspection]
    C --> D[Summarize current state]
    D --> E{State change requested?}
    E -->|No| F[Return report]
    E -->|Yes| G[Generate diff and risk summary]
    G --> H[Approval required]
    H --> I[Execute approved Helm action]
    I --> J[Verify release health]
    J --> K[Return evidence report]
```

## 22.5 Kubernetes Management Workflow

### Purpose

Allow users to inspect Kubernetes resources, diagnose issues, and perform approved remediation in scoped namespaces.

### Supported Actions

| Action | Risk | Approval |
|---|---:|---:|
| List pods/services/deployments | Low | No |
| Describe resource | Low | No |
| Read events | Low | No |
| Read logs | Medium | No |
| Rollout restart | High | Yes |
| Scale workload | High | Yes |
| Patch deployment/configmap | High | Yes |
| Read secrets | Critical | Block |
| Delete namespace/resource | Critical | Block by default |

### Flow

```mermaid
flowchart TD
    A[User asks Kubernetes task] --> B[Validate namespace and role]
    B --> C[Run read-only discovery]
    C --> D[Summarize health and symptoms]
    D --> E{Fix requested or needed?}
    E -->|No| F[Return diagnostic report]
    E -->|Yes| G[Generate remediation proposal]
    G --> H[Policy evaluation]
    H --> I{Allowed?}
    I -->|Denied| J[Explain blocked action]
    I -->|Approval required| K[Ask approval]
    K --> L[Execute approved action]
    L --> M[Verify health]
    M --> N[Return evidence report]
```

---

## 23. Policy Guard Design

### 23.1 Scope Control

Each run must carry an explicit execution scope. The policy guard evaluates every plan step and tool call against this scope before execution.

| Scope Field | Purpose |
|---|---|
| `user_id` | User identity used for RBAC and audit. |
| `roles` | Role set used for workflow, tool, and approval checks. |
| `environment` | Target environment such as local, dev, stage, or prod. |
| `namespace` | Kubernetes namespace/project boundary. |
| `allowed_mcp_servers` | MCP servers available to the run. |
| `allowed_tools` | Tool names available to the run. |
| `autonomy_mode` | Determines automatic execution vs approval. |
| `approval_mode` | Determines which actions require explicit approval. |
| `max_retries` | Prevents infinite recovery loops. |

### 23.2 Formal ODD Policy Contract

Conditional L4 autonomy must load an explicit Operational Design Domain policy. V1 starts with `knowledge-base/policy_rules.yaml` and can later move the same schema into an admin-managed policy store.

The ODD policy must define:

- Allowed workflows.
- Allowed environments.
- Allowed namespaces.
- Allowed MCP servers.
- Allowed tool categories.
- Allowed autonomy modes.
- Allowed mutation types.
- Rollback requirements.
- Stop conditions.
- Validation requirements.
- Production rules.

Required stop conditions include critical risk, out-of-scope action, high-risk action needing approval, validation failure twice, contradictory tool output, secret-like output, PostgreSQL logging failure, missing rollback state, high model uncertainty, retry budget exceeded, and duration budget exceeded.
### 23.3 Risk Levels

| Risk | Examples | Default Decision |
|---|---|---|
| Low | List releases, get health, list pods | Allow if role and scope match. |
| Medium | Read logs, diff Helm upgrade, POST to non-mutating API | Allow for operator; audit required. |
| High | Restart deployment, Helm upgrade, patch config | Approval required. |
| Critical | Delete namespace, read secrets, arbitrary shell | Deny by default. |

### 23.4 Policy Decision Model

```python
class PolicyDecision(BaseModel):
    decision: Literal["allow", "deny", "approval_required"]
    reason: str
    risk_level: Literal["low", "medium", "high", "critical"]
    matched_rules: list[str]
    redactions: list[str] = []
```

### 23.5 Example Policy Rules

```yaml
rules:
  - id: allow_readonly_bosgenesis_namespace
    effect: allow
    when:
      namespace: bosgenesis
      action_type: read
      role_in:
        - viewer
        - operator
        - approver
        - admin

  - id: require_approval_for_helm_upgrade
    effect: approval_required
    when:
      tool: helm.upgrade

  - id: require_approval_for_k8s_restart
    effect: approval_required
    when:
      tool: k8s.rollout_restart

  - id: deny_k8s_secret_read
    effect: deny
    when:
      resource_kind: Secret
      action_type: read

  - id: deny_arbitrary_shell
    effect: deny
    when:
      tool: powershell.run_raw_command

  - id: deny_prod_mutation_without_admin_policy
    effect: deny
    when:
      environment: prod
      action_type: write
      prod_mutation_enabled: false
```

---

## 24. Approval Flow

```mermaid
flowchart TD
    A[Tool action proposed] --> B[Policy Guard]
    B --> C{Decision}
    C -->|Allow| D[Execute action]
    C -->|Deny| E[Return blocked reason]
    C -->|Approval Required| F[Create approval record]
    F --> G[Show Approval Modal]
    G --> H{Approver decision}
    H -->|Approve| I[Re-check policy with approval id]
    I --> D
    H -->|Reject| J[Skip action and continue safely]
    H -->|Modify| K[Update proposed parameters]
    K --> B
```

Approval modal must show:

- User request.
- Proposed action.
- Target environment.
- Target namespace/resource.
- Risk level.
- Exact tool and parameters.
- Expected impact.
- Rollback plan if applicable.
- Expiration time.

Approval is required for these HLD-aligned actions:

- Restart deployment.
- Patch configmap.
- Patch deployment environment.
- Run Helm upgrade.
- Run Helm rollback.
- Delete pod.
- Write to production systems.
- Modify test data.
- Trigger long-running jobs.
- Any operation outside default namespace or approved scope.

---

## 25. Artifact Design

### 25.1 Artifact Types

| Artifact Type | Created By | Format |
|---|---|---|
| `release_note` | Release-note workflow | Markdown initially, PDF/HTML optional. |
| `mop` | MoP creation workflow | Markdown initially, DOCX/PDF optional. |
| `mop_execution_report` | MoP execution workflow | Markdown/JSON evidence bundle. |
| `helm_report` | Helm workflow | Markdown/JSON. |
| `k8s_report` | Kubernetes workflow | Markdown/JSON. |
| `chat_transcript` | Chat service | JSON/Markdown. |

### 25.2 Artifact Storage Layout

```text
data/artifacts/
  release_notes/
    <artifact_id>.md
  mops/
    <artifact_id>.md
  execution_reports/
    <artifact_id>.md
  evidence/
    <run_id>/
      tool_calls.json
      logs/
      screenshots/
```

### 25.3 Artifact Metadata

```json
{
  "artifact_id": "art_20260622_001",
  "run_id": "run_20260622_001",
  "artifact_type": "mop",
  "title": "MoP - Upgrade BOS Genesis Helm Release",
  "storage_uri": "local://data/artifacts/mops/art_20260622_001.md",
  "status": "draft",
  "created_by": "usr_001"
}
```

---

## 26. Memory and Context Design

### 26.1 Memory Sources

| Memory Type | Store | Purpose |
|---|---|---|
| Short-term/session memory | LangMem + LangGraph checkpointing | Current conversation state, summaries, run state, temporary observations. |
| Episodic memory | PostgreSQL | Runs, episodes, steps, observations, validation outcomes, evidence references. |
| Semantic memory | Qdrant | Similar issue/fix search, prior artifact retrieval, runbook/context search. |
| Procedural memory | PostgreSQL source of truth, optional Qdrant index | Procedures, workflow templates, safe remediation recipes, policy playbooks. |
| Log/review memory | PostgreSQL | Detailed logs, LLM explanations, model-provided reasoning summaries, tool events, review analytics. |
| Cache/coordination | Redis optional | Run locks, event buffers, rate limits, cache. |

### 26.2 Context Retrieval Flow

```mermaid
flowchart TD
    A[New user message] --> B[Classify workflow]
    B --> C[Load session state via LangGraph checkpoint]
    B --> D[Load LangMem short-term summary]
    B --> E[Load environment config]
    B --> F[Search related artifacts]
    B --> G[Search known fixes in PostgreSQL/Qdrant]
    B --> H[Load episodic summaries from PostgreSQL]
    B --> I[Load trend and review signals from PostgreSQL]
    C --> J[Build model context]
    D --> J
    E --> J
    F --> J
    G --> J
    H --> J
    I --> J
    J --> K[Redact sensitive values]
    K --> L[Send bounded context to Azure GPT-5]
```

### 26.3 Memory Write Flow

```mermaid
flowchart TD
    A[Tool execution completes] --> B[Update run state in PostgreSQL]
    A --> C[Write episode summary/evidence refs to PostgreSQL]
    A --> D[LangMem extracts useful memory]
    D --> E[Write fact/fix/procedure to PostgreSQL]
    D --> F[Upsert semantic memory in Qdrant]
    A --> G[Write detailed event and review logs to PostgreSQL]
```

### 26.4 Memory Governance Rules

Memory writes must be governed because bad memory can create repeated bad automation.

- Memory writes must be policy-checked.
- Secret-like content must be rejected.
- Every memory record must include `source_run_id`.
- Known fixes require confidence score.
- Low-confidence memories must be reviewable.
- Procedural memory requires approval and versioning.
- Semantic memory payloads should reference authoritative PostgreSQL records when possible.
- Memory retrieval must record why the memory was used.
- Memory used in a run must be visible in the run evidence panel.
### 26.5 Redaction Rules

Before sending context to GPT-5:

- Replace token-like values with `[REDACTED_TOKEN]`.
- Replace connection strings with `[REDACTED_CONNECTION_STRING]`.
- Replace Kubernetes secret data with `[REDACTED_SECRET]`.
- Do not include raw kubeconfig.
- Do not include private keys.
- Do not include password hashes.

---

## 27. Security Design

### 27.1 Authentication

Initial implementation:

- Local username/password login.
- Passwords hashed using Argon2id or bcrypt.
- HttpOnly secure session cookie.
- Short-lived JWT access token for API calls.
- CSRF protection for cookie-authenticated mutation endpoints.

Future enterprise implementation:

- Azure Entra ID SSO.
- Group-to-role mapping.
- Managed identity for Azure service access.

### 27.2 Authorization

Every API request must enforce:

1. Authenticated user.
2. Active account.
3. Required role.
4. Environment permission.
5. Namespace permission.
6. Workflow permission.
7. Tool permission.

### 27.3 Secret Handling

- Secrets are loaded from environment variables or secret manager only.
- Secrets are never returned to the browser.
- Secrets are redacted from tool outputs before LLM use.
- Audit records must store masked values only.
- The model must never be asked to summarize or inspect raw secret values.

### 27.4 Execution Boundaries

| Boundary | Rule |
|---|---|
| LLM | Can request registered tools only. |
| PowerShell | Template-based only; raw commands denied. |
| Kubernetes | Namespace allowlist; secrets blocked. |
| Helm | Upgrade/rollback require approval. |
| REST | Allowlisted base URLs only. |
| Artifacts | Per-user/role access control. |
| Production | Read-only by default until explicit policy is added. |

---

## 28. Observability and Audit

### 28.1 Structured Logs

Every backend service log line should include:

- `request_id`
- `session_id`
- `run_id`
- `user_id`
- `workflow_type`
- `tool_name`
- `status`
- `duration_ms`

### 28.2 OpenTelemetry Spans

```text
http.request
auth.login
chat.message.create
agent.intent.classify
agent.context.retrieve
llm.azure_gpt5.call
agent.plan.create
policy.evaluate
tool.execute
artifact.create
approval.create
audit.write
```

### 28.3 Evidence Store Mapping

| Evidence | Store |
|---|---|
| LLM prompt metadata, plans, model-provided reasoning summaries, and explanations | PostgreSQL `llm_reasoning_review_logs` |
| Tool calls and tool result summaries | PostgreSQL `tool_execution_logs` plus PostgreSQL references |
| API/service traces | OpenTelemetry, with summary references in PostgreSQL |
| Run history, episodes, approvals, procedures | PostgreSQL |
| Metrics and latency | PostgreSQL |
| Similar issue retrieval | Qdrant |
| Generated release notes, MoPs, and reports | Artifact storage |

### 28.4 LLM Review Logging

The system must log all available LLM reasoning artifacts for later human review and analysis.

Log these fields to PostgreSQL:

- User intent.
- Prompt template version and hash.
- LangGraph node name.
- Generated plan.
- Model-provided reasoning summary where supported.
- Tool choice and tool choice explanation.
- Risk explanation.
- Policy explanation.
- Validation explanation.
- Recovery explanation.
- Final answer.
- Redaction counts.
- Human review status.

Do not log hidden raw chain-of-thought. The backend should request model-supported reasoning summaries or ask for structured explanations, then store those review-safe fields.

### 28.5 Audit Events

Audit must record:

- Login success/failure.
- Chat message submitted.
- Run created/completed/failed.
- LLM plan generated.
- Tool call requested.
- Policy decision.
- Approval requested/approved/rejected.
- Artifact created/approved/published.
- Admin setting changed.

Audit event example:

```json
{
  "audit_id": "aud_20260622_001",
  "run_id": "run_20260622_001",
  "user_id": "usr_001",
  "event_type": "policy_decision",
  "resource_type": "tool_call",
  "resource_id": "tc_001",
  "payload": {
    "tool_name": "helm.upgrade",
    "decision": "approval_required",
    "risk_level": "high"
  }
}
```

---

## 29. Error Handling

| Error | Behavior |
|---|---|
| Invalid login | Return generic authentication failure. |
| Expired session | Return `401`; frontend redirects to login. |
| Missing permission | Return `403` with safe explanation. |
| Azure GPT timeout | Retry once if idempotent, then fail run gracefully. |
| Tool unavailable | Record tool failure and ask GPT-5 for safe fallback. |
| Policy denied | Return blocked reason and alternative read-only recommendation. |
| Approval timeout | Mark action skipped and generate partial report. |
| Artifact save failure | Fail run only if artifact is required; otherwise warn. |
| Kubernetes/Helm failure | Capture stderr/status, redact secrets, propose diagnosis. |

### 29.1 Error Diagnosis and Self-Fix Loop

The agent may attempt self-recovery only inside approved boundaries. Low-risk retries can execute automatically in `semi_autonomous` mode; medium/high-risk remediation must pass policy and approval.

```mermaid
flowchart TD
    FAIL[Step Failed] --> CLASSIFY[Classify Error]
    CLASSIFY --> MEMORY[Search Similar Past Failures]
    MEMORY --> OPTIONS[Generate Fix Options]
    OPTIONS --> RISK[Risk Assessment]
    RISK --> LOW{Low Risk?}
    LOW -->|Yes| RETRY[Execute Safe Retry]
    LOW -->|No| ASK[Ask Human Approval]
    ASK --> APPROVED{Approved?}
    APPROVED -->|Yes| FIX[Execute Approved Fix]
    APPROVED -->|No| STOP[Stop and Report]
    RETRY --> VERIFY[Verify Result]
    FIX --> VERIFY
    VERIFY --> DONE{Fixed?}
    DONE -->|Yes| CONTINUE[Continue Workflow]
    DONE -->|No| ESCALATE[Escalate with Evidence]
```

Example safe-fix pattern:

1. Health endpoint returns 503.
2. Agent inspects pod, service, ingress, and logs through approved tools.
3. Agent runs an approved PowerShell HTTP validation template.
4. Agent searches known fixes for similar symptoms.
5. Agent proposes a scoped remediation.
6. Policy marks remediation as approval-required.
7. User approves or rejects the action.
8. Agent executes only the approved action.
9. Agent verifies health and writes evidence to memory.

---

## 30. Validation Design

### 30.1 Validation Types

| Type | Example |
|---|---|
| HTTP status | Health API returned 200. |
| JSON field | `status == "healthy"`. |
| Kubernetes condition | Deployment `Available=True`. |
| Helm status | Release status is `deployed`. |
| Artifact schema | MoP contains rollback and validation sections. |
| Policy validation | Tool call matches environment and namespace limits. |
| Cross-check | Helm release version matches Kubernetes deployment image. |

### 30.2 Validation Result

```python
class ValidationResult(BaseModel):
    valid: bool
    validator_name: str
    message: str
    evidence: dict
```

---

## 31. Safe PowerShell Design

PowerShell execution is optional and must remain restricted.

Implementation rules:

- PowerShell runner must be a separate service.
- It accepts only `template_id` plus typed parameters.
- No raw command string parameter should exist.
- Every template has a risk level.
- Every template has allowed roles, workflows, and environments.
- Stdout and stderr must be size-limited.
- Secrets must be redacted before persistence.
- Commands must run with timeout.
- Commands must run as a low-privilege Windows identity.
- `Invoke-Expression`, `iex`, and script download execution are blocked.

Allowed pattern:

```text
GPT-5 selects template and parameters.
Backend validates policy.
Runner executes approved template.
Backend captures output and redacts secrets.
```

Blocked pattern:

```text
GPT-5 generates raw shell command.
Backend executes raw shell command.
```

Initial templates:

| Template | Purpose | Risk | Approval |
|---|---|---:|---:|
| `ps_http_get` | Invoke-RestMethod GET against approved endpoint. | Low | No |
| `ps_http_post` | Invoke-RestMethod POST against approved endpoint. | Medium | Optional by endpoint |
| `ps_test_connection` | Connectivity check. | Low | No |
| `ps_curl_health_check` | Curl/Invoke-WebRequest health check against known endpoint. | Low | No |
| `ps_kubectl_get_pods` | Read-only pod listing in approved namespace. | Low | No |
| `ps_kubectl_logs` | Read approved pod logs. | Medium | No |
| `ps_helm_status` | Read Helm release status. | Low | No |

Blocked:

- `Invoke-Expression`
- `iex`
- `Remove-Item -Recurse`
- `Format-Volume`
- `Stop-Service`
- `Set-ExecutionPolicy`
- `Start-Process` with unknown executable
- Raw `kubectl delete`
- Cluster-wide delete/patch operations
- Raw `helm upgrade`
- Secret reads
- Credential store access
- Recursive filesystem deletion
- Download-and-execute commands

---

## 32. Deployment Design

### 32.1 Local Development

```text
Developer Machine
  Python FastAPI backend
  HTML/CSS/JavaScript frontend served by FastAPI
  LangGraph workflow runtime
  LangChain Azure GPT-5 integration
  LangMem memory services
  MCP client
  PowerShell runner
  PostgreSQL
  Qdrant
  PostgreSQL
  Redis optional for cache/locks/event buffers
  OpenTelemetry collector optional
  local artifact directory
  optional local MCP/agent services
```

### 32.2 Internal Kubernetes Deployment

```mermaid
flowchart TD
    ING[Ingress] --> FE[esda-frontend]
    ING --> API[esda-backend-api]
    API --> PG[(PostgreSQL)]
    API --> REDIS[(Redis optional)]
    API --> QDRANT[(Qdrant)]
    API --> CH[(PostgreSQL)]
    API --> BLOB[(Artifact Storage)]
    API --> AZURE[Azure GPT-5 Deployment]
    API --> RN[release-note-agent]
    API --> MOPC[mop-creation-agent]
    API --> MOPE[mop-execution-agent]
    API --> HELM[helm-manager]
    API --> K8S[k8s-manager]
    API --> MCP1[k8s-inspector-mcp]
    API --> MCP2[api-test-mcp]
    API --> MCP3[docs-rag-mcp]
    API --> MCP4[observability-mcp]
    API --> PS[Windows PowerShell Runner]
    API --> OTEL[OpenTelemetry Collector]
```

### 32.3 Container Services

| Service | Description |
|---|---|
| `esda-frontend` | Static frontend served by Nginx or backend. |
| `esda-backend-api` | FastAPI backend. |
| `postgres` | Transactional storage, episodic memory, procedural memory. |
| `redis` | Optional cache, locks, event buffers, rate limiting. |
| `qdrant` | Semantic issue/fix, procedure, and artifact retrieval. |
| `postgres` | Run state, UI events, tool logs, LLM review records, and review analytics. |
| `powershell-runner` | Restricted Windows PowerShell execution layer. |
| `release-note-agent` | Existing release-note service integration. |
| `mop-creation-agent` | Existing MoP creation service integration. |
| `mop-execution-agent` | Existing MoP execution service integration. |
| `helm-manager` | Existing Helm management service/MCP. |
| `k8s-manager` | Existing Kubernetes management service/MCP. |

---

## 33. Testing Strategy

### 33.1 Unit Tests

| Area | Test |
|---|---|
| Auth | Password verification, inactive user blocked, role checks. |
| RBAC | Operator cannot approve high-risk action. |
| Azure GPT client | Request construction, timeout, redaction. |
| Policy guard | Secret read denied, Helm upgrade requires approval. |
| Tool registry | Disabled tool cannot execute. |
| Artifact service | Save and retrieve release note/MoP. |
| Orchestrator | Plan, execute, validate, report loop. |

### 33.2 Integration Tests

| Scenario | Expected Result |
|---|---|
| Login and send chat | Run created and events stream starts. |
| Release note creation | Draft artifact created. |
| MoP creation | MoP artifact has required sections. |
| MoP execution dry-run | No mutation tools executed. |
| Helm status | Read-only tool returns release status. |
| Helm upgrade | Approval request created before execution. |
| K8s logs | Logs returned with secrets redacted. |
| K8s secret read | Policy denies request. |

### 33.3 Safety Tests

The system must reject:

- Arbitrary PowerShell.
- Kubernetes secret reads.
- Namespace deletion.
- Helm uninstall by default.
- Production mutation without explicit admin policy.
- Browser access to Azure OpenAI key.
- LLM output that attempts to bypass tools.
- Tool calls outside user role or namespace.

---

## 34. Versioned Delivery Plan

### 34.0 Measurable Phase Acceptance Tests

V1 must prove:

- User logs in.
- User submits a bounded task.
- Agent creates a plan.
- Agent calls one MCP tool.
- Agent calls one REST GET.
- Agent calls one safe PowerShell GET.
- Agent validates response.
- Agent writes run record.
- Agent writes PostgreSQL event.
- Agent returns final evidence-backed report.

V2 must prove:

- Agent classifies an error.
- Agent retrieves a prior similar issue from Qdrant.
- Agent recommends a safe next step.
- No mutation occurs.

V3 must prove:

- Agent proposes restart or patch.
- Approval is created.
- Unauthorized user cannot approve.
- Approved action executes.
- Post-fix validation runs.
- Memory write-back occurs.

V4 must prove:

- L4 eligibility is evaluated.
- Workflow runs only inside approved ODD.
- Stop condition causes escalation.
- No production mutation occurs without policy.

### Version 1: Foundation and Authenticated Read-Only Agent

Capabilities:

- Python FastAPI backend.
- Bootstrap 5.3 + JavaScript/HTML/CSS frontend.
- Login page and authenticated session.
- Local user store and roles.
- Chat session API and SSE streaming event panel.
- LangChain Azure GPT-5 integration with externalized configuration.
- LangGraph router graph and basic workflow graph.
- Basic plan generation.
- MCP call.
- REST API GET.
- Safe PowerShell GET.
- PostgreSQL run storage.
- PostgreSQL event and LLM review logging.
- Final evidence-backed report.

Success criteria:

- User can log in and submit a bounded goal.
- Agent creates a plan before execution.
- Agent calls at least one MCP server.
- Agent calls one API endpoint.
- Agent returns an evidence-backed result.

### Version 2: Diagnostic Agent

Capabilities:

- Intent classification.
- Error classification.
- Log retrieval.
- Validation rules.
- LangMem-assisted short-term summaries and memory extraction.
- Memory lookup.
- Similar issue detection through Qdrant.
- Episodic memory through PostgreSQL.
- Run history.
- Release-note draft workflow.
- MoP draft workflow.
- Read-only Helm and Kubernetes tools.
- Artifact storage and run detail page.

Success criteria:

- Agent can diagnose a failing endpoint.
- Agent can search previous issues/fixes.
- Agent can recommend safe next steps.
- Draft release-note and MoP artifacts can be generated and stored.

### Version 3: Semi-Autonomous Repair Agent

Capabilities:

- Safe retry.
- Human approval gate.
- Policy guard enforcement.
- Approved restart/patch templates.
- MoP execution workflow.
- Helm upgrade/rollback behind approval.
- Kubernetes restart/scale/patch behind approval.
- Post-fix verification.
- Memory write-back to PostgreSQL and Qdrant.
- Human LLM-review dashboard backed by PostgreSQL.
- Execution evidence reports.

Success criteria:

- Agent can propose a fix.
- User can approve or reject the fix.
- Agent executes only approved fixes.
- Agent verifies outcome and saves evidence.

### Version 4: Conditional L4 Bounded Agent

Capabilities:

- Conditional L4 autonomy inside approved operational design domain.
- Multi-step autonomous orchestration through LangGraph.
- Multiple MCP servers.
- LangMem-assisted memory-driven troubleshooting.
- Policy-governed remediation.
- MoP-style execution report.
- PostgreSQL review analytics and operational dashboards.
- OpenTelemetry trace integration.
- Enterprise SSO.
- Managed identity or secret-manager integration.
- Fine-grained environment policies.
- High-availability deployment.

Success criteria:

- Agent can execute a full bounded workflow from task to validated result.
- Risky actions are gated.
- All steps are auditable.
- Similar future issues are resolved faster using memory.

---

## 35. Open Questions

These details must be finalized before implementation:

1. Azure GPT-5 endpoint, deployment name, API version, and authentication mode.
2. Initial authentication mode: local login, LDAP, Azure Entra ID, or another identity provider.
3. Exact URLs/contracts for release-note, MoP creation, MoP execution, Helm, and Kubernetes services.
4. Required artifact formats: Markdown only, PDF, DOCX, HTML, or all.
5. Which environments are available initially: local, dev, stage, prod.
6. Namespace and cluster allowlists.
7. Whether production mutation is allowed in any phase.
8. Whether release-note publishing integrates with GitHub, Confluence, Jira, SharePoint, or another system.
9. Whether MoP approval must integrate with an external change-management system.

---

## 36. Summary

The BOS Genesis ESDA Console is an authenticated chatbot UX application backed by Azure GPT-5. It provides a bounded operational assistant for release-note creation, MoP creation, MoP execution, Helm management, and Kubernetes management.

The core safety model is:

```text
GPT-5 reasons and proposes.
The backend validates and executes.
Users approve risky actions.
The system records evidence and audit history.
```

This design preserves the Codex-like task loop from the baseline document while adapting it into a login-based UX application with workflow-specific guardrails, Azure GPT-5 configuration, durable artifacts, approval controls, and operational auditability.
