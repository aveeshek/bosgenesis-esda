# ENV Agent Implementation Plan

## Objective

Create an ENV Agent page that acts as a chatbot-first operational assistant for Kubernetes/Helm environments. It should use the existing ESDA theme, shared sphere/globe behavior, selected model profile, MCP tool chaining, PostgreSQL logging, and policy-gated remediation.

## Phase A: Architecture and Contracts

- [x] Confirm ENV Agent route as `/env-agent` and workflow id as `env_agent`.
- [x] Define supported environment scopes, namespaces, resource kinds, and user roles for backend policy/audit metadata.
- [x] Define diagnostic-only, propose-only, and approval-gated remediation modes for prompt-time inference, not UI selection.
- [x] Define tool risk levels for list/describe/logs/events/restart/scale/patch/rollback/delete.
- [x] Define policy stop conditions for secrets, cluster-wide changes, destructive actions, ambiguous target, missing namespace, and low confidence.
- [x] Define PostgreSQL event, tool-call, approval, and LLM-review logging requirements.
- [x] Define Activity visibility requirements for ENV Agent runs.

## Phase B: Backend Tool Adapters

- [x] Create ENV Agent tool adapter layer for k8s-inspector MCP.
- [x] Add namespace summary, pod health, restart analysis, events, logs, deployment status, service/ingress status, and PVC checks.
- [x] Create ENV Agent tool adapter layer for helm-manager MCP.
- [x] Add Helm release list, status, history, values summary, and rollback candidate lookup.
- [x] Add optional data-ingestion/observability lookup hooks when available.
- [x] Add strict redaction for secrets, tokens, credentials, and high-volume logs.
- [x] Normalize all tool results into evidence records with status, confidence, and source metadata.

## Phase C: LLM Chains and Workflow Graph

- [x] Create intent classifier for diagnostic, remediation request, follow-up, and unsafe request.
- [x] Create planner chain that outputs bounded MCP tool steps.
- [x] Create diagnosis chain that summarizes symptoms, likely cause, confidence, and missing evidence.
- [x] Create remediation planner that outputs typed tool actions, risk, rollback, and verification plan.
- [x] Create verifier chain that evaluates post-action health evidence.
- [x] Create recovery chain for tool failure, partial evidence, and decision-required states.
- [x] Build LangGraph nodes: intake, scope, classify, plan, inspect, correlate, diagnose, propose, approve, execute, verify, report, complete.
- [x] Persist safe summaries and never persist hidden chain-of-thought.

## Phase D: Page Shell and UX

- [x] Add top-nav entry: ENV Agent.
- [x] Create `/env-agent` page route.
- [x] Reuse ESDA matte-glass theme, global model selector, profile menu, and transaction sidebar.
- [x] Reuse the exact shared sphere/globe visual.
- [x] Show large idle sphere/globe before the first prompt.
- [x] Shrink sphere/globe into a working indicator after user submission.
- [x] Add chat transcript panel, prompt composer, live reasoning pane, tool logs pane, and result/report pane.
- [x] Add copy logs, maximize Autonomy Notes, and clear history controls.
- [x] Remove namespace/mode/scope form controls so ENV Agent is prompt-first like Codex.
- [x] Remove bottom Agent Activity Feed from ENV Agent; every prompt is an independent chat/tool loop.
- [ ] Ensure the page stays responsive and no panel escapes the visible viewport.

## Phase E: Diagnostic Chat Flow

- [x] Start a PostgreSQL run for every user prompt.
- [x] Stream `00 / CREATING PLAN` before MCP inspection starts.
- [x] Run read-only inspection tool chains without approval; MCP/policy responses decide namespace boundaries at runtime.
- [x] Answer pod-health, restart-loop, service-reachability, Helm-status, and namespace-summary questions.
- [x] Show evidence-backed answers with tool observations and confidence.
- [x] Store safe summaries, events, and tool logs.
- [x] Restore active ENV Agent runs after refresh.

## Phase F: Remediation Flow

- [x] Detect when the user asks ESDA to fix an issue.
- [x] Build a remediation proposal with action, target, risk, impact, rollback, and verification plan.
- [x] Block unsupported, ambiguous, secret-reading, destructive, or cluster-wide actions.
- [x] Require approval for implemented high-risk actions such as rollout restart, scale, patch, and Helm rollback.
- [x] Execute only typed approved MCP tools, never raw shell.
- [x] Verify post-action state and report success, needs-review, or failed.
- [x] Log all action inputs, redacted outputs, approvals, and verification results.

## Phase G: Activity and Audit Integration

- [ ] Include ENV Agent runs in Activity filters and timeline.
- [ ] Show chat summary, target namespace, tools used, risk level, approval state, and final status.
- [ ] Allow Activity Chat to ask about prior ENV Agent diagnostic/remediation runs.
- [ ] Add L4 Audit entries for remediation decisions and approvals.
- [ ] Add admin visibility for tool failures, blocked actions, and unsafe requests.

## Phase H: Tests and Demo Readiness

- [x] Add unit tests for intent classification and remediation schema validation.
- [x] Add adapter tests with mocked MCP responses.
- [x] Add policy tests for blocked secrets, delete, cluster-wide mutation, and approval-required actions.
- [x] Add workflow tests for diagnostic-only and approval-gated remediation paths.
- [x] Add UI smoke tests for chat submit, sphere shrink, prompt-first page shape, and log copy/maximize controls.
- [x] Prepare demo prompts for pod issue count, restart-loop diagnosis, and approved safe remediation.

## 2026-08-16 Implementation Reconciliation

### Implemented

- [x] Prompt-first two-pane Environment Chat; no namespace/mode/scope setup form and no bottom Agent Activity Feed.
- [x] Shared sphere animation, matte-glass theme, copy logs, maximize Autonomy Notes, and clear-history controls.
- [x] Prompt classification and bounded Kubernetes/Helm inspection through typed adapters.
- [x] Follow-up context for pod logs and prior diagnostic targets.
- [x] PostgreSQL sessions/messages/run restoration and bounded short/long context retrieval.
- [x] Markdown-formatted SIGMA/GPT reports grounded in structured MCP evidence.
- [x] Typed namespace-scoped remediation proposal, policy/approval persistence, execution, read-only verification, and audit events.
- [x] Admin chat normalization for the single-user lab.

### Current boundaries

- Namespace, mode, and scope are inferred from prompts; the MCP server and policy layer remain the authority for allowed boundaries.
- Namespace-scoped typed operations may include get/list/log/describe/apply/install/upgrade/uninstall/patch/scale/delete when the adapter, ODD, policy, and approval permit them. Cluster-wide mutation, arbitrary shell, Secret disclosure, and raw kubeconfig access remain blocked.
- LangMem package detection is not active memory management. PostgreSQL is the durable chat/session/memory authority and the in-process prompt window is short-term context.
- Qdrant and Redis are optional extension points, not current Environment Chat authorities.
- Phase G Activity/L4 integration remains intentionally deferred.
- Local admin normalization is demo-only and must be replaced with Entra ID and scoped RBAC.

### Remaining checklist

- [ ] Implement governed LangMem extraction/consolidation only after retention and review rules are approved.
- [ ] Add provider/tool capability tests for every selectable model profile.
- [ ] Complete remediation failure/recovery, concurrent-session, and malicious-prompt tests.
- [ ] Add Environment Chat to the unified Activity/L4 views when Phase G resumes.
