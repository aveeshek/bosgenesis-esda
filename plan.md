# BOS Genesis ESDA Implementation Plan

## 1. Purpose

This checklist converts the accepted architecture into an executable build plan for the BOS Genesis ESDA Chatbot Console.

The target project is a Python-based web application with:

- [x] Python FastAPI backend.
- [x] HTML, CSS, and JavaScript frontend.
- [x] Bootstrap 5.3 UI.
- [ ] Optional jQuery for DOM/AJAX helpers.
- [ ] Optional Chart.js and Plotly.js for dashboards.
- [x] Azure OpenAI LLM integration for local V1 proofing with the provided GPT-4.1 mini deployment.
- [ ] Azure-deployed GPT-5 production deployment as the final LLM target.
- [x] LangChain for model and tool integration.
- [x] LangGraph for workflow orchestration and checkpointed run state.
- [ ] LangMem as the memory-management layer.
- [ ] PostgreSQL for transactional data, episodic memory, and procedural memory.
- [ ] Qdrant for semantic memory.
- [x] PostgreSQL for detailed logs, LLM explanations, tool events, and review analytics.
- [x] PostgreSQL-backed transaction replay state for refresh-safe workflow UX.
- [ ] Conditional L4 autonomy inside an approved operational design domain.

## 2. Accepted Architecture Decisions

- [x] Backend will be pure Python, preferably FastAPI.
- [x] Frontend will be JavaScript, HTML, and CSS, using Bootstrap 5.3.
- [x] Azure GPT-5 remains the target LLM provider; local proofing can use Azure OpenAI-compatible deployments.
- [x] LangChain will wrap Azure OpenAI and tool integration.
- [x] LangGraph will own live workflow state, checkpointing, interruptions, and retries.
- [x] LangMem will manage memory extraction, summarization, search, and maintenance.
- [x] PostgreSQL will store episodic and procedural memory.
- [x] Qdrant will store semantic memory.
- [x] PostgreSQL will store detailed logs and LLM review records.
- [x] PostgreSQL will be the source of truth for refresh-safe workflow transactions and UI state restoration.
- [x] MongoDB is excluded from V1.
- [x] Hidden raw chain-of-thought will not be stored.
- [x] Model-provided reasoning summaries, plans, explanations, and decisions will be stored for review.

## 3. Delivery Strategy

- [x] Build the system in four increments.
- [x] End every phase with a working vertical slice.
- [x] Prefer readable Python, explicit graph nodes, and operationally boring UI.
- [x] Avoid adding extra services until the core loop works.
- [x] Keep production mutation disabled until a separate explicit policy enables it.

Phases:

- [x] Phase 1: Foundation and authenticated read-only agent.
- [ ] Phase 2: Diagnostic workflow agent.
- [ ] Phase 3: Reviewable semi-autonomous agent.
- [ ] Phase 4: Conditional L4 bounded agent.

## 3.1 Current Implementation Update - 2026-06-25

- [x] Actual V1 hello-world workflow is release-note generation using `bosgenesis-release-note-agent`.
- [x] ESDA runs locally; do not deploy ESDA into the cluster for the current iteration.
- [x] Local ESDA uses BOS Genesis ingress endpoints for existing services such as `release-note-agent.bosgenesis.local`.
- [x] Future Helm deployment should use in-cluster service DNS names for the same dependencies.
- [x] PostgreSQL is the current durable store for runs, events, tool calls, LLM review records, approvals, policies, procedures, and artifact metadata.
- [x] ClickHouse has been removed from the active V1 target path.
- [x] SQLite has been removed from the active V1 target path.
- [x] Qdrant remains optional until semantic memory lookup is needed in V1.
- [x] Azure OpenAI works locally through LangChain and Azure CLI bearer-token auth with the provided GPT-4.1 mini deployment.
- [ ] Final Azure GPT-5 endpoint/deployment details are still pending.
- [x] Release-note-agent is called through MCP-compatible tool endpoints first.
- [x] Release-note-agent produces the initial document/evidence; ESDA GPT produces the final Markdown draft from that source material.
- [x] ESDA saves both Markdown and PDF artifacts and exposes separate download buttons.
- [x] Release-note live progress is scrollable and copyable.
- [x] Release-note progress is refresh-safe and restores active/historical transactions from PostgreSQL.
- [x] Latest backend verification for this slice: `node --check backend\app\static\js\release_notes.js`, `python -m py_compile backend\app\main.py backend\app\db\database.py backend\app\db\models.py`, `python -m ruff check .`, and `python -m pytest backend\tests -q` passed with 71 tests.

## 4. Phase 1 Checklist: Foundation and Read-Only Agent

### 4.1 Phase Goal

- [x] Create the runnable application shell.
- [x] Support authentication.
- [x] Connect to Azure OpenAI through the LangChain service wrapper; local real execution works with Azure CLI auth against the provided GPT-4.1 mini deployment.
- [x] Execute a minimal LangGraph workflow with a safe sequential fallback for local dependency gaps.
- [x] Stream run progress to the browser.
- [x] Persist run state in PostgreSQL.
- [x] Write event and LLM review logs to PostgreSQL.

### 4.2 Repository and Tooling

- [x] Create `backend/` directory.
- [x] Create `backend/app/` package.
- [x] Create `backend/app/main.py`.
- [x] Create `backend/app/config.py`.
- [x] Create `backend/app/dependencies.py`.
- [x] Create `backend/tests/`.
- [x] Create `pyproject.toml`.
- [x] Add FastAPI dependency.
- [x] Add Uvicorn dependency.
- [x] Add Pydantic settings dependency.
- [x] Add SQLAlchemy or SQLModel dependency.
- [x] Add Alembic dependency.
- [x] Add PostgreSQL logger dependency.
- [x] Add Qdrant client dependency.
- [x] Add LangChain dependency.
- [x] Add LangGraph dependency.
- [x] Add LangMem dependency.
- [x] Add pytest dependency.
- [x] Add ruff dependency.
- [x] Add `.env.example`.
- [x] Add `README.md` quickstart.
- [x] Add `docker-compose.yml`.

### 4.3 Local Infrastructure

- [x] Add PostgreSQL service to Docker Compose.
- [x] Add Qdrant service to Docker Compose.
- [x] Add PostgreSQL service to Docker Compose.
- [x] Add optional Redis service to Docker Compose.
- [x] Add health checks for PostgreSQL.
- [x] Add health checks for Qdrant.
- [x] Add health checks for PostgreSQL.
- [x] Add named volumes for local data.
- [x] Add local network configuration.
- [x] Document local startup command.

### 4.4 Configuration

- [x] Define `APP_ENV`.
- [x] Define `APP_NAME`.
- [x] Define `APP_BASE_URL`.
- [x] Define `DATABASE_URL`.
- [x] Define `QDRANT_URL`.
- [x] Define PostgreSQL log/review table ownership.
- [x] Define optional `REDIS_URL`.
- [x] Define `AZURE_OPENAI_ENDPOINT`.
- [x] Define `AZURE_OPENAI_API_KEY`.
- [x] Define `AZURE_OPENAI_AUTH_MODE`.
- [x] Define Azure deployment aliases, including `AZURE_OPENAI_GPT5_DEPLOYMENT` and `OPENAI_DEPLOYMENT`.
- [x] Define `AZURE_OPENAI_API_VERSION`.
- [x] Define `OPENAI_API_VERSION` alias.
- [x] Define `AZURE_OPENAI_USE_V1_API`.
- [x] Define `AZURE_OPENAI_TEMPERATURE`.
- [x] Define `AZURE_OPENAI_MAX_TOKENS`.
- [x] Define `AZURE_OPENAI_REASONING_EFFORT`.
- [x] Define `AZURE_OPENAI_REASONING_SUMMARY`.
- [x] Define `LANGGRAPH_CHECKPOINTER`.
- [x] Define `LANGMEM_ENABLED`.
- [x] Define `LLM_REVIEW_LOGGING_ENABLED`.
- [x] Add settings validation.
- [x] Mask secrets in config logging.

### 4.5 Backend Application Shell

- [x] Create FastAPI app factory.
- [x] Add `/health` endpoint.
- [x] Add request ID middleware.
- [x] Add structured logging middleware.
- [x] Add validation error handler.
- [x] Add unhandled exception handler.
- [x] Add static files mount.
- [x] Add Jinja2 template support.
- [x] Add OpenAPI tags.

### 4.6 Authentication

- [x] Create `users` PostgreSQL table.
- [x] Use signed HTTP-only session cookie; no server-side session/token table is needed in V1.
- [x] Implement password hashing.
- [x] Implement seed admin user.
- [x] Implement `POST /api/auth/login`.
- [x] Implement `POST /api/auth/logout`.
- [x] Implement `GET /api/auth/me`.
- [x] Implement session cookie or JWT issuance.
- [x] Add auth dependency.
- [x] Add role dependency.
- [x] Add login page.
- [x] Add logout button.
- [x] Add auth failure handling in UI.

### 4.7 Frontend Shell

- [x] Create base HTML template.
- [x] Add Bootstrap 5.3 assets.
- [x] Add local `app.css`.
- [x] Add local `app.js`.
- [x] Create login page.
- [x] Create main chat page.
- [x] Create empty run timeline panel.
- [x] Create empty plan panel.
- [x] Create empty evidence panel.
- [x] Add loading state.
- [x] Add error state.
- [x] Add responsive layout.

### 4.8 Chat and Run APIs

- [x] Create `chat_sessions` table.
- [x] Create `chat_messages` table.
- [x] Create `agent_runs` table.
- [x] Create `plan_steps` table.
- [x] Create `tool_calls` table.
- [x] Implement `POST /api/chat`.
- [x] Implement `GET /api/runs/{run_id}`.
- [x] Implement `GET /api/runs/{run_id}/events`.
- [x] Implement `POST /api/runs/{run_id}/stop`.
- [x] Implement `POST /api/llm/smoke-test`.
- [x] Implement authenticated `POST /api/llm/chat`.
- [x] Create run ID generator.
- [x] Persist user message.
- [x] Persist assistant message.
- [x] Persist run status changes.
- [x] Stream run events through SSE.
- [x] Render events in browser.

### 4.9 LangChain Azure OpenAI Service

- [x] Create `AzureGpt5Service`.
- [x] Support LangChain `ChatOpenAI` for Azure v1-compatible endpoint.
- [x] Support fallback `AzureChatOpenAI`.
- [x] Support Azure CLI bearer-token authentication.
- [x] Support API-key authentication.
- [x] Add model timeout.
- [x] Add retry policy.
- [x] Add prompt metadata.
- [x] Add response parsing.
- [x] Add structured output helper.
- [x] Add tool-binding placeholder.
- [x] Redact secrets before sending context.
- [x] Log model deployment metadata without secrets.

### 4.10 LangGraph Foundation

- [x] Create `GraphState` model.
- [x] Create router graph skeleton.
- [x] Create read-only demo graph.
- [x] Add `load_scope` node.
- [x] Add `plan` node.
- [x] Add `write_postgres_logs` node.
- [x] Add `final_report` node.
- [x] Add graph checkpointing.
- [x] Persist graph run status.
- [x] Emit graph node events to SSE.
- [x] Add unit test for graph execution.

### 4.11 PostgreSQL Logging

- [x] Create `agent_event_logs` table.
- [x] Create `llm_reasoning_review_logs` table.
- [x] Create `tool_execution_logs` table.
- [x] Implement PostgreSQL connection.
- [x] Implement event logger.
- [x] Implement LLM review logger.
- [x] Log run created event.
- [x] Log graph node started event.
- [x] Log graph node completed event.
- [x] Log model request metadata.
- [x] Log model response summary.
- [x] Add redaction before PostgreSQL writes.

### 4.12 Phase 1 Exit Checks

- [x] User can log in.
- [x] User can submit a bounded task.
- [x] Backend creates a run.
- [x] Browser receives live SSE events.
- [x] LangGraph executes the read-only demo graph, with local fallback when LangGraph is unavailable.
- [x] Azure OpenAI service returns a real chat response when credentials are configured, or deterministic fallback response when they are not.
- [x] LLM Chat modal can call Azure OpenAI from an authenticated browser session.
- [x] PostgreSQL stores run metadata.
- [x] PostgreSQL stores event logs.
- [x] PostgreSQL stores LLM review records.
- [x] Azure credentials are not exposed to the browser.
- [x] Authenticated LLM chat endpoint does not expose credentials to the browser.
- [x] Tests pass.
- [x] README startup instructions work as the Phase 1 environment contract.
## 5. Phase 2 Checklist: Diagnostic Workflow Agent

### 5.1 Phase Goal

- [ ] Add real read-only diagnostic workflows.
- [x] Add registered tools.
- [ ] Add memory retrieval.
- [x] Add artifact creation.
- [x] Add structured validation.

### 5.2 Agent Chains

- [x] Implement intent classifier chain for the release-note workflow.
- [x] Implement planner chain for the release-note workflow.
- [x] Implement verifier chain for the release-note workflow.
- [x] Implement recovery recommendation chain for the release-note workflow.
- [x] Implement report writer chain for the release-note workflow.
- [x] Add structured output schemas for release-note chains.
- [x] Add prompt versioning for release-note chains.
- [x] Add prompt hash logging for release-note chains.
- [x] Add unit tests for each release-note chain.
- [x] Add authenticated workflow classification endpoint.

### 5.3 Tool Registry

- [x] Create tool registry table or config.
- [x] Define tool metadata model.
- [x] Add tool enable/disable flag.
- [x] Add tool risk level.
- [x] Add allowed roles.
- [x] Add allowed environments.
- [x] Add allowed workflow types.
- [x] Add input schema validation.
- [x] Add output schema validation.
- [x] Add timeout per tool.
- [x] Add audit metadata per tool call.

### 5.4 Read-Only Tools

- [x] Implement REST GET tool.
- [x] Implement MCP client tool.
- [x] Implement safe PowerShell HTTP GET template.
- [x] Implement Kubernetes list resources tool.
- [ ] Implement Kubernetes describe resource tool.
- [x] Implement Kubernetes read events tool.
- [ ] Implement Kubernetes read logs tool with redaction.
- [ ] Implement Helm list releases tool.
- [ ] Implement Helm get status tool.
- [ ] Implement Helm get values tool with secret redaction.
- [x] Add tests for blocked unsafe inputs.

### 5.5 Memory Setup

- [ ] Create PostgreSQL episodic memory table.
- [ ] Create PostgreSQL memory facts table.
- [ ] Create Qdrant `issue_memory` collection.
- [ ] Create Qdrant `artifact_memory` collection.
- [ ] Create Qdrant `knowledge_memory` collection.
- [ ] Implement embedding service.
- [ ] Implement Qdrant upsert.
- [ ] Implement Qdrant search.
- [ ] Implement LangMem service wrapper.
- [ ] Implement short-term summary generation.
- [ ] Implement memory extraction after run completion.
- [ ] Add memory redaction before write.

### 5.6 Diagnostic Graphs

- [x] Implement release-note draft graph.
- [ ] Implement MoP creation draft graph.
- [ ] Implement Kubernetes read-only diagnostic graph.
- [ ] Implement Helm read-only diagnostic graph.
- [ ] Add graph node for `retrieve_memory`.
- [x] Add graph node for `execute_tool`.
- [x] Add graph node for `observe`.
- [x] Add graph node for `validate`.
- [x] Add graph node for `recover_or_continue` in the release-note graph.
- [ ] Add graph node for `write_memory`.
- [x] Add graph node for `final_report`.
- [ ] Add graph-level retry limit.

### 5.7 Artifact Handling

- [x] Create artifact metadata table.
- [x] Create local artifact storage directory.
- [x] Implement Markdown artifact writer.
- [x] Implement release-note artifact renderer.
- [ ] Implement MoP artifact renderer.
- [ ] Implement execution report renderer.
- [ ] Add artifact browser page.
- [x] Add artifact download endpoint.
- [x] Add artifact links to final report.

### 5.8 Frontend Diagnostic UI

- [x] Add shared hidden-by-default floating transaction sidebar.
- [x] List prior workflow transactions from PostgreSQL.
- [x] Restore selected run state from backend snapshot.
- [x] Replay missed progress events after refresh/navigation.
- [x] Reconnect live SSE for active runs.
- [x] Add user-level clear/hide action for transaction history.

- [ ] Add workflow selector.
- [ ] Add environment selector.
- [x] Add namespace selector.
- [ ] Display classified workflow type.
- [x] Display generated plan.
- [x] Display tool call timeline.
- [x] Display validation result.
- [ ] Display memory used.
- [x] Display final report.
- [x] Display artifact links.

### 5.9 Release Note Generation Page and Workflow

- [x] Add dedicated `/release-notes` page.
- [x] Add GitHub repository URL input.
- [x] Add optional source range inputs: start tag/commit/date and end tag/commit/date.
- [ ] Add optional target audience selector: engineering, operations, business, customer.
- [x] Add output format selector, with Markdown as V1 default.
- [x] Add release name/version input with auto-infer fallback.
- [x] Validate GitHub URL format and allowed host policy before tool execution.
- [x] Add `RELEASE_NOTE_AGENT_URL` environment setting.
- [x] Add `RELEASE_NOTE_AGENT_MCP_URL` environment setting.
- [x] Add `RELEASE_NOTE_AGENT_TRANSPORT` environment setting.
- [x] Add `RELEASE_NOTE_AGENT_TIMEOUT_SECONDS` environment setting.
- [x] Add `release-note-agent` adapter under tool registry.
- [x] Prefer `release-note-agent` MCP transport when `RELEASE_NOTE_AGENT_MCP_URL` is configured.
- [x] Invoke `github_release_scan_start`, `github_release_scan_status`, `github_release_generate_note`, and `github_release_get_artifact` through the MCP-compatible endpoint.
- [x] Define `release_notes.collect_sources` tool contract.
- [x] Define `release_notes.generate_draft` graph node.
- [x] Define `release_notes.validate_draft` graph node.
- [x] Define `release_notes.save_artifact` graph node.
- [x] Use GPT-5 to classify the request as `release_note_creation`.
- [x] Use GPT-5 to create a source-collection and drafting plan.
- [x] Ask clarifying questions when repository URL is ambiguous in classifier output.
- [x] Call `release-note-agent` with GitHub URL and source range.
- [x] Capture release-note-agent evidence: commits, PRs, tags, changed services, issue references, and deployment metadata when available.
- [x] Request Markdown, HTML, PDF, and JSON outputs from release-note-agent.
- [x] Hydrate release-note-agent Markdown and artifact references before final drafting.
- [x] Preserve all release-note-agent artifact metadata so late-listed PDF artifacts are not dropped.
- [x] Ask GPT-5 to draft release notes only from collected evidence.
- [x] Include source evidence references in the generated release note.
- [x] Show live progress in the run timeline through SSE.
- [x] Show model-provided reasoning summaries and action explanations live in the UI.
- [x] Make release-note live progress scrollable and copyable.
- [x] Do not show or store hidden chain-of-thought.
- [x] Log plan, reasoning summary, tool calls, tool outputs, validation, and final artifact metadata to PostgreSQL.
- [x] Save generated release note as a Markdown artifact table/file record.
- [x] Save release-note PDF as a binary artifact table/file record.
- [x] Show generated release note in an artifact preview panel.
- [x] Add download endpoint for release-note Markdown artifact.
- [x] Add download endpoint/link for release-note PDF artifact.
- [x] Add approval gate only for publish/export actions, not for draft generation.
- [x] Add unit tests for GitHub URL validation.
- [x] Add unit tests for release-note-agent adapter request/response mapping.
- [x] Add integration test for release-note graph with mocked release-note-agent.
- [x] Add release-note-agent adapter tests for MCP URL mapping, MCP envelope handling, and preserving all artifact metadata.
- [x] Add artifact service and release-note graph tests for PDF binary artifact save.

### 5.10 Persistent Transaction State and Refresh-Safe UX

- [x] Add durable run event replay using the existing `run_events` table with computed per-run event sequence.
- [ ] Add compact run `state_snapshot` cache in PostgreSQL if composed snapshots become too expensive.
- [x] Add user transaction visibility table or equivalent soft-hide field.
- [x] Add background execution boundary so workflow continues after page refresh/navigation.
- [x] Persist progress event before broadcasting over SSE.
- [x] Add `/api/transactions` endpoint for sidebar history.
- [x] Add `/api/runs/{run_id}/snapshot` endpoint for rehydration.
- [x] Add SSE resume support using `after_event_id` or `after_sequence`.
- [x] Add `/api/transactions/{run_id}/clear` endpoint that hides without deleting audit data.
- [x] Update release-note page to hydrate existing active run on load.
- [x] Update release-note page to replay persisted events below the working sphere.
- [x] Keep generated Markdown/PDF artifacts visible after refresh.
- [ ] Generalize the release-note state-machine into a shared controller for all workflow pages.
- [x] Add backend tests for snapshot, event replay cursor, transaction listing, and soft clear with mocked active run.
- [x] Add tests that cleared transactions disappear from sidebar but remain auditable.

### 5.11 Phase 2 Exit Checks

- [x] Agent can classify workflow type.
- [x] Agent creates a plan before tools execute.
- [x] Agent can call at least one registered read-only tool.
- [x] Agent validates tool output.
- [x] Agent stores useful episode in PostgreSQL.
- [x] Agent restores active release-note run progress after browser refresh/navigation.
- [x] Agent exposes prior release-note workflow transactions through the floating sidebar.
- [ ] Agent writes searchable memory to Qdrant.
- [x] Agent produces a release-note or MoP draft artifact.
- [x] PostgreSQL captures plan, tool explanation, validation explanation, and final answer.
- [x] Unsafe tool requests are blocked.
- [x] Tests pass.

## 6. Phase 3 Checklist: Reviewable Semi-Autonomous Agent

### 6.1 Phase Goal

- [x] Add policy guardrails.
- [x] Add approval workflow.
- [ ] Add review dashboard.
- [ ] Add safe retry and bounded recovery.
- [ ] Add dry-run MoP execution.

### 6.2 Policy Guard

- [x] Create policy rule model.
- [x] Create policy rules table or config.
- [x] Implement policy evaluator.
- [x] Implement risk classifier.
- [x] Implement namespace allowlist.
- [x] Implement environment allowlist.
- [x] Implement role-to-tool mapping.
- [x] Implement workflow-to-tool mapping.
- [x] Deny raw shell.
- [x] Deny raw PowerShell.
- [x] Deny Kubernetes secret reads.
- [x] Deny namespace delete.
- [x] Deny Helm uninstall by default.
- [x] Require approval for restart.
- [x] Require approval for patch.
- [x] Require approval for Helm upgrade.
- [x] Require approval for Helm rollback.
- [x] Add policy tests.

### 6.3 Approval Workflow

- [x] Create approvals table.
- [x] Implement approval request creation.
- [x] Implement `GET /api/approvals`.
- [x] Implement approve endpoint.
- [x] Implement reject endpoint.
- [x] Implement modify-and-recheck endpoint.
- [x] Add approval queue page.
- [x] Add approval modal.
- [x] Show target environment.
- [x] Show target namespace/resource.
- [x] Show proposed tool and parameters.
- [x] Show expected impact.
- [x] Show rollback note.
- [x] Add approval expiration.
- [x] Recheck policy after approval.

### 6.4 Semi-Autonomous Graph Behavior

- [ ] Add approval interrupt node.
- [ ] Add resume-after-approval flow.
- [ ] Add rejected-action branch.
- [ ] Add safe retry branch.
- [ ] Add recovery proposal branch.
- [ ] Add bounded retry counter.
- [ ] Add recovery validation.
- [ ] Add escalation report path.
- [ ] Persist graph state across approval wait.

### 6.5 MoP Execution Dry Run

- [ ] Implement MoP load tool.
- [ ] Validate MoP structure.
- [ ] Validate prechecks exist.
- [ ] Validate rollback section exists.
- [ ] Generate dry-run execution plan.
- [ ] Simulate step execution.
- [ ] Mark risky steps.
- [ ] Generate dry-run report.
- [ ] Store dry-run episode.

### 6.6 Helm and Kubernetes Expanded Diagnostics

- [ ] Add Helm diff tool.
- [ ] Add Helm values redaction.
- [ ] Add Kubernetes event correlation.
- [ ] Add Kubernetes log summarization.
- [ ] Add Kubernetes deployment condition validation.
- [ ] Add cross-check between Helm release and Kubernetes workload.

### 6.7 LLM Review Dashboard

- [ ] Implement `GET /api/llm-review`.
- [ ] Add review table page.
- [ ] Filter by workflow type.
- [ ] Filter by run status.
- [ ] Filter by risk level.
- [ ] Filter by model deployment.
- [ ] Show prompt version.
- [ ] Show prompt hash.
- [ ] Show plan JSON.
- [ ] Show reasoning summary.
- [ ] Show tool choice explanation.
- [ ] Show risk explanation.
- [ ] Show validation explanation.
- [ ] Show final answer.
- [ ] Add review status update.
- [ ] Add review notes.

### 6.8 Tool Evidence Dashboard

- [ ] Add tool execution logs query endpoint.
- [ ] Display tool input summary.
- [ ] Display redacted output summary.
- [ ] Display policy decision.
- [ ] Display validation result.
- [ ] Display duration.
- [ ] Display error message.
- [ ] Link tool event to run timeline.

### 6.9 Phase 3 Exit Checks

- [x] High-risk action creates approval request.
- [x] Rejected action is not executed.
- [x] Approved action is rechecked by policy before execution.
- [ ] Agent can run safe retries only when policy allows.
- [ ] Human reviewer can inspect LLM plans and explanations.
- [ ] Tool outputs are redacted before logging.
- [ ] Tool outputs are redacted before LLM context reuse.
- [ ] MoP execution dry-run produces evidence report.
- [x] Tests pass.

## 7. Phase 4 Checklist: Conditional L4 Bounded Agent

### 7.1 Phase Goal

- [x] Enable conditional L4 autonomy for approved workflows.
- [x] Restrict L4 to a clear operational design domain.
- [x] Add stop conditions.
- [x] Add rollback readiness checks.
- [x] Add L4 audit and review.

### 7.2 Operational Design Domain

- [x] Define L4-approved workflow types.
- [x] Define L4-approved environments.
- [x] Define L4-approved namespaces.
- [x] Define L4-approved users and roles.
- [x] Define L4-approved tools.
- [x] Define L4-approved procedures.
- [x] Define max retry count.
- [x] Define max run duration.
- [x] Define required validation checks.
- [x] Define required rollback metadata.
- [x] Define required logging availability.
- [x] Store ODD configuration.
- [ ] Add admin page for ODD configuration.

### 7.3 L4 Eligibility Evaluator

- [x] Implement eligibility input model.
- [x] Check user authorization.
- [x] Check workflow authorization.
- [x] Check environment authorization.
- [x] Check namespace authorization.
- [x] Check tool sequence authorization.
- [x] Check risk threshold.
- [x] Check procedure approval status.
- [x] Check rollback readiness.
- [x] Check validation availability.
- [x] Check PostgreSQL logging availability.
- [x] Check retry budget.
- [x] Return eligible/ineligible decision with reason.
- [x] Log eligibility decision.

### 7.4 Procedure Catalog

- [x] Create procedures table.
- [x] Create procedure versions table.
- [x] Create procedure steps table.
- [x] Create procedure policies table.
- [x] Add procedure approval workflow.
- [ ] Add procedure semantic index in Qdrant.
- [ ] Add procedure browser page.
- [ ] Add procedure version comparison.
- [ ] Add procedure execution stats.

### 7.5 Stop Condition Engine

- [x] Stop on critical risk.
- [x] Stop on policy denial.
- [x] Stop on validation failure threshold.
- [x] Stop on secret-like output.
- [x] Stop on missing rollback state.
- [x] Stop on exceeded retry limit.
- [x] Stop on exceeded duration.
- [x] Stop on tool output contradiction.
- [x] Stop on unavailable critical logging.
- [x] Stop on model uncertainty threshold.
- [x] Emit stop reason to UI.
- [ ] Generate escalation report.

### 7.6 L4 Execution Graph

- [ ] Add `evaluate_l4_eligibility` node.
- [ ] Add full-plan policy check node.
- [ ] Add approved procedure matching node.
- [ ] Add autonomous execution loop.
- [ ] Add post-action validation node.
- [ ] Add rollback readiness node.
- [ ] Add stop-condition node.
- [ ] Add escalation node.
- [ ] Add final L4 report node.
- [ ] Add graph tests for stop paths.

### 7.7 L4 Audit Dashboard

- [ ] Add L4 run filter.
- [x] Show eligibility decision.
- [x] Show ODD configuration used.
- [x] Show procedure version used.
- [x] Show autonomous steps.
- [x] Show stop condition checks.
- [ ] Show validation results.
- [x] Show rollback readiness.
- [ ] Show human review status.
- [x] Export L4 audit report.

### 7.8 Phase 4 Exit Checks

- [x] L4 mode is available only for approved users.
- [x] L4 mode is available only for approved workflows.
- [x] L4 mode is available only for approved environments and namespaces.
- [x] L4 mode executes only approved tools.
- [x] Agent stops on critical risk.
- [x] Agent stops on policy denial.
- [x] Agent stops on repeated validation failure.
- [x] Agent stops on secret leakage.
- [x] Agent stops when rollback state is missing.
- [ ] Full workflow execution is logged to PostgreSQL.
- [x] Human reviewer can reconstruct the full run.
- [x] Production mutation remains disabled unless explicitly enabled.
- [x] Tests pass.

## 8. Cross-Phase Workstream Checklist

### 8.1 Backend

- [x] FastAPI app factory.
- [ ] Auth middleware.
- [ ] API route modules.
- [x] Pydantic schemas.
- [x] LangChain Azure OpenAI service.
- [x] LangGraph graph modules.
- [x] Tool registry.
- [x] Tool execution layer.
- [x] Policy guard.
- [x] Approval service.
- [x] Artifact service.
- [ ] Memory manager.
- [x] PostgreSQL logger.
- [x] Redaction service.

### 8.2 Frontend

- [x] Bootstrap layout.
- [x] Login page.
- [x] Chat console and LLM chat modal.
- [x] SSE event handling.
- [x] Replayable SSE resume after last persisted event sequence.
- [x] Run timeline.
- [x] Plan panel.
- [x] Tool evidence panel.
- [x] Approval modal.
- [ ] Artifact browser.
- [x] Floating transaction history sidebar.
- [x] Persisted page-state hydration on the release-note workflow page.
- [ ] Memory search page.
- [ ] LLM review page.
- [ ] Logs dashboard.
- [ ] Chart.js metrics.
- [ ] Plotly.js diagnostics.

### 8.3 Data

- [ ] PostgreSQL migrations.
- [x] PostgreSQL run event and transaction visibility schema.
- [ ] Qdrant collection setup.
- [x] PostgreSQL DDL.
- [x] Seed admin user.
- [x] Seed tool registry.
- [x] Seed policy rules.
- [ ] Seed starter procedures.
- [ ] Backup and restore notes.

### 8.4 Agent Workflows

- [x] Router graph.
- [x] Background workflow execution boundary.
- [ ] Persisted state snapshot writer per graph transition.
- [x] Release-note creation graph.
- [ ] MoP creation graph.
- [ ] MoP execution graph.
- [ ] Helm management graph.
- [ ] Kubernetes management graph.
- [ ] Recovery subgraph.
- [ ] L4 eligibility subgraph.

### 8.5 Safety

- [x] Secret redaction.
- [x] Tool allowlist.
- [x] Role checks.
- [x] Environment checks.
- [x] Namespace checks.
- [x] Approval gates.
- [x] Retry limits.
- [x] Stop conditions.
- [x] Audit logs.
- [x] LLM review logs.
- [x] No hidden chain-of-thought storage.

## 9. Initial Repository Structure Checklist

- [x] Create `backend/`.
- [x] Create `backend/app/main.py`.
- [x] Create `backend/app/config.py`.
- [ ] Create `backend/app/api/`.
- [x] Create `backend/app/auth/`.
- [x] Create `backend/app/llm/`.
- [x] Create `backend/app/graphs/`.
- [x] Create `backend/app/chains/`.
- [ ] Create `backend/app/memory/`.
- [x] Create `backend/app/tools/`.
- [x] Create `backend/app/policy/`.
- [x] Create `backend/app/logging/`.
- [x] Create `backend/app/db/`.
- [x] Create `backend/app/templates/`.
- [x] Create `backend/app/static/`.
- [x] Create `backend/tests/`.
- [ ] Create `frontend/static/css/`.
- [ ] Create `frontend/static/js/`.
- [ ] Create `frontend/static/vendor/`.
- [ ] Create `frontend/templates/`.
- [x] Create `docker-compose.yml`.
- [x] Create `README.md`.
- [x] Keep `knowledge-base/` as design source.

## 10. Sprint 0 Checklist

- [x] S0-01: Create Python project scaffold.
- [x] S0-02: Add `pyproject.toml`.
- [x] S0-03: Add FastAPI app entrypoint.
- [x] S0-04: Add local Docker Compose.
- [x] S0-05: Add PostgreSQL service.
- [x] S0-06: Add Qdrant service.
- [x] S0-07: Add PostgreSQL service.
- [x] S0-08: Add optional Redis service.
- [x] S0-09: Add configuration loader.
- [x] S0-10: Add environment validation.
- [x] S0-11: Add PostgreSQL connectivity.
- [x] S0-12: Add PostgreSQL connectivity.
- [x] S0-13: Add Bootstrap frontend shell.
- [x] S0-14: Add login page.
- [x] S0-15: Add empty chat page.
- [x] S0-16: Add user model.
- [x] S0-17: Add password hashing.
- [x] S0-18: Add session/JWT handling.
- [x] S0-19: Add Azure OpenAI service wrapper with Azure CLI/API-key auth.
- [x] S0-20: Add LangGraph router skeleton.
- [x] S0-21: Add SSE event stream.
- [x] S0-22: Add PostgreSQL event logger.
- [x] S0-23: Add smoke tests.
- [x] S0-24: Update README quickstart.
- [x] S0-25: Add `knowledge-base/policy_rules.yaml` as initial ODD/policy artifact.
- [ ] S0-26: Add `prompt_templates/` directory with starter prompt template files.

## 11. Sprint 1 Checklist

- [x] S1-01: Implement tool registry.
- [x] S1-02: Implement REST GET tool.
- [x] S1-03: Implement MCP tool placeholder.
- [x] S1-04: Implement planner chain.
- [x] S1-05: Implement verifier chain.
- [x] S1-06: Implement run timeline UI.
- [x] S1-07: Add PostgreSQL run tables.
- [x] S1-08: Add PostgreSQL step table.
- [x] S1-09: Add PostgreSQL tool call table.
- [x] S1-10: Add PostgreSQL LLM review table.
- [x] S1-11: Add PostgreSQL tool execution table.
- [ ] S1-12: Add Qdrant setup.
- [ ] S1-13: Add LangMem service wrapper.
- [ ] S1-14: Add memory extraction stub.
- [x] S1-15: Add final report renderer.
- [x] S1-16: Add basic artifact storage.
- [x] S1-17: Add redaction utility.
- [x] S1-18: Add tests for unsafe tool rejection.
- [x] S1-19: Add tests for LLM review logging.
- [x] S1-20: Demo read-only diagnostic flow.
- [x] S1-21: Implement `ToolExecutionRequest` and `ToolExecutionResult` schemas.
- [x] S1-22: Implement MCP response envelope normalization.
- [x] S1-23: Implement PowerShell template request contract with no raw command field.
- [ ] S1-24: Log why retrieved memory was used in a run.
- [x] S1-25: Add authenticated LLM chat modal and `/api/llm/chat` endpoint.
- [x] S1-26: Add durable run event replay table and writer.
- [x] S1-27: Add transaction sidebar API and soft-clear behavior.
- [x] S1-28: Add release-note page rehydration and SSE resume.

## 12. Guardrail Checklist

- [x] Azure OpenAI LLM can propose but cannot execute directly.
- [x] Backend executes only registered tools.
- [x] Tool calls pass schema validation.
- [x] Tool calls pass policy validation.
- [x] Secrets are redacted before logging.
- [x] Secrets are redacted before LLM context reuse.
- [x] Hidden raw chain-of-thought is not stored.
- [x] Model-supported reasoning summaries are logged.
- [x] Structured explanations are logged.
- [x] High-risk actions require approval.
- [x] Conditional L4 runs only inside approved ODD.
- [x] Production mutation is disabled by default.
- [x] Tool failures are captured as evidence.
- [x] Validation failures are captured as evidence.
- [x] Retry loops have hard limits.
- [x] ODD policy is loaded from `policy_rules.yaml` in V1.
- [ ] MCP server/tool/response-size limits are enforced.
- [x] PowerShell runner accepts only template IDs and typed parameters.
- [ ] Memory writes are policy-checked and include source run ID.
- [x] Prompt version and prompt hash are logged for every run.

## 13. Definition of Done Checklist

- [x] Code or documentation is committed-ready.
- [x] Tests exist for risky behavior.
- [x] Logs are written for important actions.
- [x] Secrets are not exposed.
- [x] Failure behavior is defined.
- [x] UI reflects backend status correctly.
- [x] PostgreSQL receives review-grade events when applicable.
- [x] PostgreSQL stores durable state when applicable.
- [x] Browser refresh/navigation does not lose active release-note workflow progress.
- [x] User can restore prior release-note transactions from the floating sidebar.
- [ ] Qdrant stores semantic memory when applicable.
- [x] Documentation is updated.

## 14. Open Questions Checklist

- [x] Confirm Azure OpenAI local test endpoint: `https://aiservicesprjbossdcdevh23aw001.openai.azure.com/`.
- [x] Confirm Azure OpenAI local test deployment name: `bos-trainium-sigma-gpt-4.1-mini`.
- [x] Confirm Azure API version for local LLM test: `2024-12-01-preview`.
- [x] Confirm local Azure auth mode: Azure CLI bearer token.
- [ ] Confirm final Azure GPT-5 endpoint and deployment name for production configuration.
- [x] Decide whether V1 auth is local only or Entra ID-ready.
- [x] Identify first available BOS Genesis MCP server.
- [x] Confirm first vertical slice.
- [x] Confirm V1 environments.
- [x] Confirm V1 namespaces.
- [ ] Confirm PostgreSQL retention policy.
- [ ] Confirm who can review LLM explanations.
- [ ] Confirm who can approve high-risk actions.
- [ ] Confirm which workflows may qualify for conditional L4.

## 15. Recommended First Vertical Slice Checklist

Actual current first vertical slice: release-note generation. Kubernetes read-only diagnostics remains a useful next diagnostic workflow, but it is no longer the first working demo path.

Current shipped hello-world vertical slice: release-note generation.

- [x] User enters a GitHub repository URL and release/source details on `/release-notes`.
- [x] Agent classifies the request as `release_note_creation`.
- [x] Agent creates a release-note plan with prompt version and prompt hash logging.
- [x] Agent calls the `bosgenesis-release-note-agent` MCP-compatible tools.
- [x] Agent uses release-note-agent Markdown as the initial document/evidence source.
- [x] Agent saves the final Markdown artifact.
- [x] Agent downloads and saves the release-note-agent PDF artifact.
- [x] UI displays progress, preview, and Markdown/PDF download links.
- [x] PostgreSQL stores run, event, tool, artifact, and LLM review records.
- [ ] Future iteration: render the GPT-final Markdown itself to PDF if exact Markdown/PDF content parity becomes required.

Original recommendation: start with Kubernetes read-only diagnostics.

Why:

- [x] It exercises auth.
- [x] It exercises chat.
- [x] It exercises LangGraph.
- [x] It exercises tools.
- [ ] It exercises policy.
- [ ] It exercises memory.
- [x] It exercises PostgreSQL logging.
- [x] It exercises final reports.
- [x] It is safe to scope to read-only actions.

Minimum workflow:

- [x] User asks: "Check health of service X in namespace Y."
- [ ] Agent classifies Kubernetes diagnostic intent.
- [x] Agent creates a plan.
- [ ] Policy allows read-only inspection.
- [x] Tool lists pods.
- [ ] Tool lists services.
- [ ] Tool fetches events.
- [ ] Tool fetches logs if allowed.
- [x] Verifier summarizes health.
- [ ] LangMem extracts useful memory.
- [x] PostgreSQL stores episode.
- [ ] Qdrant stores semantic issue/fix if useful.
- [x] PostgreSQL stores event logs.
- [x] PostgreSQL stores tool logs.
- [x] PostgreSQL stores LLM review logs.
- [x] UI shows final evidence-backed report.
