# BOS Genesis ESDA Knowledge Base

**Reconciled:** 2026-08-16
**Repository:** `bosgenesis-esda`
**Baseline type:** Code-verified working tree plus cross-service contract review

## Document Precedence

When documents disagree, use this order:

1. `project_architecture_specification.md` - controlling requirements and system boundaries.
2. `hld.md` - product and service architecture.
3. `bosgenesis_esda_chatbot_lld.md` - implemented modules, APIs, persistence, state machines, and UI behavior.
4. Workflow design and plan files under `activity/`, `mop-generation/`, `mop-execution/`, `env-agent/`, and `digital-twin/`.
5. Versioned contracts, fixtures, and evidence under `digital-twin/contracts/`, `digital-twin/fixtures/`, and `digital-twin/evidence/`.
6. `baseline_idea.md` - historical source material only. Crossed-out or annotated choices are not current requirements.

Runtime code, deployed service contracts, and automated tests remain the ultimate source of implementation truth. A historical run, screenshot, or immutable Digital Twin records the rules that existed when it was created and is not retroactively rewritten.

## Current Product Surface

| UX route | Backend workflow | Current role |
|---|---|---|
| `/` | health/diagnostic | Authenticated health-check console and shared shell. |
| `/release-notes` | `release_note_creation` | GitHub evidence, repository analysis, Markdown/PDF release notes, Git publishing. |
| `/mop-generation` | `mop_generation` | Read-only namespace evidence, professional MoP artifacts, complete `mop-bundle.zip`, Git publishing. |
| `/digital-twins` | Namespace Twin gateway | On-demand, non-mutating simulation and deterministic Green/Amber/Red evidence. |
| `/mop-execution` | `mop_execution` | Bundle preflight, dry-run, approval, mutation, validation, reports, cleanup/revert. |
| `/env-agent` | `env_agent` | Prompt-first Kubernetes/Helm diagnosis and typed approval-gated remediation. |
| `/activity` | multi-workflow activity | Timeline, run detail, artifact actions, and artifact-grounded chat. |
| `/approvals` | approval service | Human approval review and decisions. |
| `/l4-audit` | L4 audit | Eligibility, stop checks, decisions, and audit export. |

## Implemented Architecture Snapshot

- FastAPI, Jinja2, plain JavaScript, HTML, and CSS form one Python web project.
- PostgreSQL is the durable authority for users, chat, runs, ordered events, artifacts, tool calls, safe LLM review summaries, approvals, procedures, and L4 audit records.
- Local artifact storage plus the configured Git repository hold generated files and published bundles.
- Azure GPT-5 is the primary reasoning provider. UI labels are cosmetic: `SIGMA 5 PRO`, `SIGMA 4.1`, `TRAINIUM BEHEMOTH`, `TRAINIUM GEMMA`, and `CUSTOM` map to configured model profiles.
- LangChain wrappers provide model integration. Workflow graphs use LangGraph patterns where implemented. The configured checkpointer still defaults to process memory; LangMem, Qdrant, and Redis are extension points, not current decision authorities.
- Hidden chain-of-thought is never persisted or exposed. Ephemeral working notes may be shown while connected; durable records contain safe summaries and structured/redacted evidence.
- BOS Genesis release-note, MoP creation, MoP execution, Helm Manager, and Kubernetes Inspector services are reached through typed REST/MCP adapters. Local ESDA uses configured ingress URLs; in-cluster deployment should use service DNS.
- ClickHouse is not part of the current architecture. PostgreSQL owns operational, review, and audit persistence.

## Current Execution and Twin Contract

ESDA owns authenticated orchestration and presentation. `bosgenesis-mop-execution-agent` owns bundle execution jobs, Namespace Twin facts, deterministic policy/risk decisions, namespace locks, mutation controls, and execution reports.

- Digital Twin simulation is non-mutating and can be launched from the dedicated workspace or selected bundle/target context.
- The compact Twin panel on Bundle Execution is optional by default (`DIGITAL_TWIN_EXECUTION_GATE_REQUIRED=false`). When enabled, the server validates the immutable Twin/bundle/target/freshness contract before mutation.
- Namespace Twin automatic approval is a deterministic Twin policy option. It does not silently bypass the separate Bundle Execution human-approval API.
- After explicit approval is accepted, ESDA may use SIGMA/GPT to choose only a bounded `continue`, `hold`, or `abort` response for an execution-agent instruction gate. It never emits direct `kubectl` or `helm` mutation commands and never blindly retries unknown outcomes.
- The demo bundle preference is `260630_114925_mop_signoz`. This is a selection aid, not an authorization bypass.
- Post-mutation validation combines execution-agent evidence with live Kubernetes Inspector and Helm Manager observations. Current demo success requires healthy workload Pods, Services, the expected Helm release when configured, and no Kubernetes Ingress when the no-Ingress gate is enabled.
- `completed_with_review` is terminal and evidence-backed, but distinct from a full validation pass.

## Configuration Ownership

| Owner | Representative configuration |
|---|---|
| ESDA | Database, artifacts/Git, model profiles, MCP/agent URLs, namespace allowlists, logs, timeouts, optional Twin gate, preferred demo bundle, post-mutation live verification. |
| MoP Creation Agent | Professional MoP renderer, bundle artifact schema, source evidence collection, source-namespace approval metadata. |
| MoP Execution Agent | Execution exclusions, Namespace Twin collection/filtering, deterministic risk policy, auto-approval projection, dry-run age, locks, jobs, reports, cleanup. |
| Helm/Kubernetes MCP services | Typed namespace-scoped discovery, validation, mutation capability, and their own ODD/RBAC enforcement. |

Important execution-agent demo properties include:

```env
MOP_EXECUTION_EXCLUDED_K8S_KINDS=Ingress
MOP_EXECUTION_EXCLUDED_K8S_NAMES=kube-root-ca.crt
MOP_EXECUTION_EXCLUDED_K8S_NAME_PREFIXES=istio-
NAMESPACE_TWIN_LIVE_COLLECTION_ENABLED=true
NAMESPACE_TWIN_HELM_INSTALLED_RELEASES_ONLY=true
NAMESPACE_TWIN_HELM_IGNORE_PREFIXES=bosgenesis-
NAMESPACE_TWIN_CONFIGMAP_EXCLUDE_NAMES=kube-root-ca.crt
NAMESPACE_TWIN_CONFIGMAP_EXCLUDE_PREFIXES=istio-
NAMESPACE_TWIN_EXCLUDED_KINDS=Ingress
NAMESPACE_TWIN_ROLLBACK_REQUIRED=false
NAMESPACE_TWIN_AUTO_APPROVAL_ENABLED=true
NAMESPACE_TWIN_PVC_RISK_ENABLED=false
NAMESPACE_TWIN_STATEFULSET_RISK_ENABLED=false
```

These are lab/demo settings. Excluding or disabling a risk axis means it is out of the current decision scope; it does not prove the omitted behavior safe.

## Validated Lab Evidence

The current August lab baseline includes a real approved Signoz mutation to `agent-testing` through the deployed MoP Execution Agent. ESDA observed a terminal completed run, the expected Helm release, six Ready/Completed Pods, eight Services, and zero Kubernetes Ingress resources; an execution report bundle was published. Treat this as lab evidence for the pinned bundle and environment, not a general production guarantee.

## Known Gaps

- Explicit human approval remains a separate mutation gate even when a bundle or Twin says automatic approval/not required. One-click mutation is not the current normative contract.
- FastAPI background tasks and an in-memory LangGraph checkpointer do not provide durable process-level workflow recovery.
- Enterprise SSO/RBAC, managed secrets, tenant isolation, CSRF hardening, malicious-bundle testing, durable workers, and cross-service observability remain required before shared or production use.
- PVC and StatefulSet risk are disabled for the MVP. Ingress and platform ConfigMap exclusions are demo heuristics and technical debt.
- Dry-run admission cannot prove image pull, scheduling, PVC binding, readiness, or controller/webhook convergence.
- Environment Chat Activity/L4 integration remains deferred; its prompt memory is PostgreSQL-backed, while LangMem/Qdrant/Redis are not active authorities.
- Release/version metadata is not fully synchronized: repository tags reach `v0.2.18` while pyproject.toml still declares `0.1.0`. Align package, image, capability, and documentation versions before the next formal release.
- Historical plans contain original estimates and decisions. Their reconciliation appendices identify current behavior without deleting project history.

## Workflow Documents

- `activity/plan.md` - multi-workflow activity and artifact chat.
- `mop-generation/plan.md` and `mop-generation/ESDA_MOP_ARTIFACT_BUNDLE_GENERATION.md` - bundle creation flow and operator artifact contract.
- `mop-execution/plan.md`, `mop-execution/ESDA_MOP_BUNDLE_EXECUTION_WITH_MOP_EXECUTION_AGENT.md`, and `mop-execution/demo_runbook.md` - dry-run, approval, mutation, validation, cleanup, and demo proof.
- `env-agent/plan.md` and `env-agent/demo_prompts.md` - prompt-first environment operations.
- `digital-twin/esda_namespace_digital_twin_design.md`, `digital-twin/esda_digital_twin_webpage_implementation_plan.md`, and `digital-twin/plan.md` - authoritative Namespace Twin design, UX, phased implementation, and evidence contracts.
