# ESDA MoP Bundle Execution Logic Using MoP Execution Agent

## Purpose

This document describes how an external GPT-5 LLM or Codex-style orchestrator should execute a previously generated ESDA MoP artifact bundle through the BOS Genesis MoP Execution Agent.

The execution flow is intentionally governed:

1. Register or validate the artifact bundle.
2. Create an execution job.
3. Run dry-run first.
4. Inspect observations, policy decisions, and generated reports.
5. Submit external instruction only when the worker pauses for a decision.
6. Submit human approval before mutation.
7. Start approved mutation.
8. Validate resources.
9. Generate execution, validation, and change evidence reports.
10. Roll back or cleanup when requested.

The external GPT-5 LLM is the reasoning and orchestration layer. The MoP Execution Agent is the deterministic executor. The LLM must not bypass the agent by calling Kubernetes or Helm mutation tools directly.

## Operating Model

```text
External GPT-5 LLM
  |
  | MCP/REST calls
  v
MoP Execution Agent
  |
  | governed MCP clients
  v
K8s Inspector MCP / Helm Manager MCP / Release Note Agent MCP
  |
  v
Target namespace
```

The LLM may inspect state and decide what instruction or approval to submit, but actual execution must be performed by the MoP Execution Agent.

## Default Inputs

```yaml
source_namespace: esda
target_namespace: agent-testing
bundle_path_or_archive: <path-to-mop-bundle.zip>
execution_mode: dry_run_first
mutation_allowed: false_until_approved
rollback_allowed: true_with_instruction_and_approval
generated_name_prefix: agent-ai
correlation_id: agent-ai-esda-execution-<timestamp>
```

If the source bundle is for a different source namespace, such as `signoz`, the LLM must not relabel it as ESDA. It must report the mismatch and ask whether to continue.

## Required MCP Servers

The execution workflow uses these servers:

| MCP server | Purpose |
| --- | --- |
| `bosgenesis-mop-execution-agent` | Primary execution control plane. Creates jobs, runs dry-runs, controls mutation, validates, reports, rollbacks, and cleanup. |
| `bosgenesis-k8s-inspector-mcp` | Called by the execution agent for Kubernetes dry-run, apply, get/list/describe, events, logs, wait, delete, and validation. |
| `bosgenesis-helm-manager-mcp` | Called by the execution agent for Helm template, dry-run install/upgrade, install/upgrade, status, history, rollback, and uninstall. |
| `bosgenesis-release-note-agent` | Called by the execution agent to generate change/release evidence notes when requested. |
| `bosgenesis-k8s-data-ingestion-agent` | Optional context source for historical facts and namespace inventory. |

The external LLM should normally call only the MoP Execution Agent during execution. Direct K8s or Helm MCP mutation should be avoided except for read-only troubleshooting and never used to bypass policy gates.

## Required MoP Execution Agent Capabilities

The execution agent must expose REST and MCP equivalents for:

- Health and readiness.
- Capabilities.
- Bundle registration and validation.
- Job creation.
- Job retrieval and listing.
- Job start, pause, resume, cancel.
- Dry-run execution.
- Human approval submission.
- External instruction submission.
- Decision-required context retrieval.
- Plan retrieval.
- Observations retrieval.
- Audit events retrieval.
- Memory context retrieval.
- Policy evaluation.
- Rollback request.
- Cleanup or revert target namespace.
- Report metadata retrieval.
- Report binary download through REST link metadata.

## Execution Phases

### Phase 0: Preflight Safety Checks

Before submitting anything to the execution agent, verify:

```text
Bundle exists.
Bundle is not empty.
Bundle has artifact.json.
Bundle has machine_execution_plan.yaml.
Bundle has human MoP Markdown.
Bundle has PDF MoP.
Bundle has deployment-artifacts.zip.
Bundle has deployment-artifacts/artifact-index.json.
Target namespace is explicitly configured.
Mutation is not allowed yet.
Dry-run is enabled.
```

Also verify target namespace policy:

```text
target_namespace is in allowed namespaces.
target_namespace is not production unless explicitly approved.
target_namespace matches the machine_execution_plan target namespace.
```

Fail closed if:

- Source namespace in the bundle does not match the intended source namespace.
- Target namespace in the bundle does not match the requested target namespace.
- Required artifacts are missing.
- The machine plan is missing.
- The artifact index references missing files.
- The bundle contains source Secret data.
- The bundle contains cluster-scoped destructive actions.

### Phase 1: Check MoP Execution Agent Health

Call:

```text
mop_execution.health
mop_execution.capabilities
```

Equivalent REST:

```http
GET /healthz
GET /readyz
GET /v1/capabilities
GET /v1/config/effective
```

Expected result:

```text
health: ok
ready: ok
capabilities include bundle validation, dry-run, mutation, approval, validation, reports, rollback
```

If the agent is not ready, stop and report the reason.

### Phase 2: Register and Validate the Bundle

Submit the bundle source to the execution agent, register it first, then validate by returned `bundle_id`.

Supported source types:

```yaml
type: object_store
value: https://raw.githubusercontent.com/aveeshek/bosgenesis-artifacts/main/<folder>/mop-bundle.zip
```

```yaml
type: local_path
path: <mounted-path-visible-to-agent>
```

```yaml
type: uploaded_archive
reference: <upload-ref-visible-to-agent>
```

For ESDA Activity runs that were published to `aveeshek/bosgenesis-artifacts`, prefer `object_store`. Do not pass an ESDA local filesystem path unless the execution agent pod can resolve the same path.

Register bundle:

```text
mop_execution.register_bundle({
  source: {
    type: "object_store",
    value: "https://raw.githubusercontent.com/aveeshek/bosgenesis-artifacts/main/<folder>/mop-bundle.zip"
  },
  source_metadata: {
    workflow_type: "mop_generation",
    publish_folder: "<folder>",
    branch: "main",
    original_source_type: "activity_run"
  },
  target_namespace: "agent-testing",
  correlation_id: "agent-ai-esda-execution-<timestamp>"
})
```

Equivalent REST:

```http
POST /v1/artifact-bundles
```

Expected registration result:

```text
bundle_id: <bundle-id>
bundle_source_resolved: true or pending
source_type: object_store
```

Validate registered bundle:

```text
mop_execution.validate_bundle({
  bundle_id: "<bundle-id>",
  target_namespace: "agent-testing",
  correlation_id: "agent-ai-esda-execution-<timestamp>"
})
```

Equivalent REST:

```http
POST /v1/artifact-bundles/{bundle_id}/validate
```

Expected validation result:

```text
valid: true
bundle_id: <bundle-id>
source_namespace: <source-namespace>
target_namespace: agent-testing
plan_schema_version: supported
warnings: acceptable or empty
```

If validation fails, do not create a job. Retrieve validation errors and report them. If the error is `bundle_source_not_locally_resolvable:object_store`, the execution-agent deployment does not yet include the object-store resolver required by ESDA and must be redeployed.

### Phase 3: Create Dry-Run Job

Create an execution job in dry-run-only mode.

Logical MCP call:

```text
mop_execution.create_job({
  bundle_id: "<bundle-id>",
  target_namespace: "agent-testing",
  mode: "dry_run_only",
  mutation_allowed: false,
  requires_approval: true,
  correlation_id: "agent-ai-esda-execution-<timestamp>",
  idempotency_key: "esda-agent-testing-dry-run-<timestamp>"
})
```

Equivalent REST:

```http
POST /v1/jobs
```

Expected job state:

```text
created
validating_bundle
ready_for_dry_run
```

The external LLM must store:

```text
job_id
bundle_id
correlation_id
target_namespace
```

### Phase 4: Start Dry-Run

Start the job.

Logical MCP call:

```text
mop_execution.start_job({
  job_id: "<job-id>",
  request_id: "start-dry-run-<timestamp>"
})
```

Equivalent REST:

```http
POST /v1/jobs/{job_id}/start
```

The worker should:

1. Acquire namespace lock.
2. Emit audit event before every potential mutation.
3. Run policy checks.
4. Run Helm template or Helm dry-run install/upgrade.
5. Run Kubernetes server-side dry-run for raw manifests.
6. Persist redacted observations.
7. Pause on ambiguity or failure.
8. Mark dry-run complete when all dry-run phases pass.

Expected states:

```text
running
dry_run_running
dry_run_succeeded
waiting_for_approval
```

or, on failure:

```text
decision_required
paused
failed_safe
```

### Phase 5: Poll Job State and Observations

Poll the job until a terminal dry-run state or decision-required state is reached.

Logical MCP calls:

```text
mop_execution.get_job(job_id)
mop_execution.get_observations(job_id)
mop_execution.get_plan(job_id)
mop_execution.get_audit(job_id)
```

Equivalent REST:

```http
GET /v1/jobs/{job_id}
GET /v1/jobs/{job_id}/plan
GET /v1/jobs/{job_id}/observations
GET /v1/jobs/{job_id}/audit-events
```

The LLM must inspect:

```text
state
current_phase
current_step
last_error_code
policy_blocks
dry_run_results
MCP call outcomes
redacted observations
audit completeness
```

If state is `decision_required`, retrieve the decision context.

### Phase 6: Decision-Required Handling

When the worker pauses, the LLM must not repair silently. It must retrieve context and submit explicit instruction only if safe.

Logical MCP call:

```text
mop_execution.get_decision_required_context({
  job_id: "<job-id>"
})
```

Equivalent REST:

```http
GET /v1/jobs/{job_id}/decision-required
```

The context should include:

```text
reason_code
phase_id
step_id
failed_command_kind
redacted error
relevant observations
memory context marked context_only_not_decision_authority
allowed instruction schema
unsafe instruction examples
```

Submit external instruction only if it is safe:

```text
mop_execution.submit_instruction({
  job_id: "<job-id>",
  instruction_id: "instruction-<timestamp>",
  scope: {
    namespace: "agent-testing",
    phase_id: "<phase>",
    step_id: "<step>"
  },
  instruction: {
    action: "retry_with_patch" | "skip_non_required_step" | "use_alternate_manifest" | "continue_after_manual_fix",
    rationale: "<why this is safe>",
    patch: "<optional non-secret patch>"
  }
})
```

Equivalent REST:

```http
POST /v1/jobs/{job_id}/instructions
```

Instruction rules:

- Must be explicit.
- Must be scoped.
- Must not copy secrets.
- Must not broaden namespace.
- Must not request cluster-scoped destructive actions.
- Must not override failed policy gates.
- Must produce an audit event.

If instruction is accepted, resume:

```text
mop_execution.resume_job(job_id)
```

### Phase 7: Review Dry-Run Report

After dry-run completion, generate or retrieve reports.

Logical MCP calls:

```text
mop_execution.list_reports(job_id)
mop_execution.get_report_metadata(job_id, report_id)
```

Equivalent REST:

```http
GET /v1/jobs/{job_id}/reports
GET /v1/reports/{report_id}
GET /v1/reports/{report_id}/download?artifact=pdf
```

Review:

```text
dry-run success/failure
resources that would be created
Helm release actions
Kubernetes raw manifest actions
policy checks
warnings
redactions
trace ID
correlation ID
audit events
```

Do not proceed to mutation until the dry-run report is acceptable.

### Phase 8: Submit Human Approval for Mutation

Mutation requires human approval.

Logical MCP call:

```text
mop_execution.submit_approval({
  job_id: "<job-id>",
  approval_id: "approval-<timestamp>",
  approved_by: "<human-operator>",
  scope: {
    namespace: "agent-testing",
    release_name: "agent-ai-esda",
    operation: "approved_mutation"
  },
  expires_at: "<timestamp>",
  dry_run_job_id: "<job-id>",
  command_fingerprints: ["<fingerprint-from-dry-run>"],
  rationale: "Dry-run reviewed and approved for target namespace only."
})
```

Equivalent REST:

```http
POST /v1/jobs/{job_id}/approvals
```

Approval must match:

```text
target namespace
release name
resource scope
command fingerprint
dry-run result
expiration time
operator identity
```

If approval is missing, expired, or scope-mismatched, the execution agent must block mutation.

### Phase 9: Create or Continue Mutation Job

Preferred approach:

```text
Create a separate mutation job using the same validated bundle and dry-run evidence.
```

Alternative:

```text
Continue the existing job from waiting_for_approval to mutation.
```

Logical mutation job creation:

```text
mop_execution.create_job({
  bundle_id: "<bundle-id>",
  target_namespace: "agent-testing",
  mode: "approved_mutation",
  dry_run_job_id: "<dry-run-job-id>",
  mutation_allowed: true,
  approval_id: "<approval-id>",
  correlation_id: "agent-ai-esda-execution-<timestamp>",
  idempotency_key: "esda-agent-testing-mutation-<timestamp>"
})
```

Then start:

```text
mop_execution.start_job({
  job_id: "<mutation-job-id>",
  request_id: "start-mutation-<timestamp>"
})
```

Mutation gate pipeline:

1. Job state is mutation-ready.
2. Bundle validated.
3. Dry-run succeeded.
4. Human approval is present and in scope.
5. External instructions, if any, are accepted.
6. Target namespace guard passes.
7. Cluster-scoped blockers pass.
8. Secrets are redacted or blocked.
9. Namespace lock acquired.
10. Idempotency check passes.
11. Audit pre-event written before mutation.

Only then may the agent invoke Helm/K8s MCP mutation tools.

### Phase 10: Mutation Execution

The execution agent maps plan steps to deterministic executors:

| Plan step type | Executor |
| --- | --- |
| `helm_upgrade` | Helm Manager MCP install/upgrade |
| `helm_validate` | Helm Manager MCP status/history/template |
| `k8s_apply` | K8s Inspector MCP apply |
| `k8s_wait` | K8s Inspector MCP wait/poll |
| `k8s_validate` | K8s Inspector MCP get/list/describe/events |
| `report` | Internal report generator |
| `release_notes` | Release Note Agent MCP |

During mutation:

```text
Persist every observation.
Persist every audit event.
Redact every MCP response.
Pause on any ambiguity.
Do not self-repair without external instruction.
Handle long-running Helm installs asynchronously.
Recover state after worker restart.
```

Expected mutation states:

```text
mutation_running
validating
succeeded
```

or:

```text
decision_required
paused
rollback_required
failed_safe
unknown_mutation_outcome
```

### Phase 11: Validation

After mutation, run validation.

Logical MCP calls:

```text
mop_execution.get_job(job_id)
mop_execution.get_observations(job_id, type="validation")
mop_execution.list_reports(job_id)
```

The execution agent should validate:

```text
Helm status
Helm history
Deployments
StatefulSets
Pods
Services
Ingress
PVCs
Events
Custom resources
Rollout status
Application-level custom validations from the plan
```

Expected validation report:

```text
resources created
resources ready
resources pending
warnings
failed checks
trace ID
correlation ID
redacted observations
operator actions
```

### Phase 12: Report Generation

Generate or retrieve:

```text
execution report
validation report
rollback report, if rollback happened
change evidence report
release note, if requested
```

Logical MCP:

```text
mop_execution.generate_report(job_id, report_type="execution")
mop_execution.generate_report(job_id, report_type="validation")
mop_execution.generate_release_notes(job_id)
mop_execution.list_reports(job_id)
```

Binary report download must be REST metadata/link based:

```http
GET /v1/reports/{report_id}/download?artifact=pdf
GET /v1/reports/{report_id}/download?artifact=html
GET /v1/reports/{report_id}/download?artifact=markdown
```

MCP should return metadata and links, not raw PDF bytes by default.

### Phase 13: Cleanup or Full Namespace Revert

For demo reset, use the execution agent cleanup/revert capability instead of manually deleting resources.

Logical MCP call:

```text
mop_execution.revert_namespace({
  target_namespace: "agent-testing",
  job_id: "<job-id>",
  scope: "resources_created_by_job",
  require_approval: true,
  approval_id: "<cleanup-approval-id>",
  correlation_id: "agent-ai-esda-cleanup-<timestamp>"
})
```

Allowed cleanup modes:

```text
resources_created_by_job
generated_prefix_only
helm_release_only
namespace_empty_state
```

Recommended demo cleanup:

```text
namespace_empty_state for agent-testing
```

Cleanup must:

- Require external instruction or approval.
- Use namespace lock.
- Avoid cluster-scoped delete unless explicitly approved.
- Delete Helm releases first.
- Delete generated raw manifests next.
- Delete PVCs only when cleanup approval includes data removal.
- Wait for custom resource finalizers.
- Emit audit events.
- Generate cleanup report.

### Phase 14: Rollback

Rollback differs from cleanup. Rollback restores the previous release/resource state when possible.

Rollback requires:

```text
external instruction
human approval
rollback scope
rollback reason
rollback target revision, if Helm
```

Logical MCP:

```text
mop_execution.request_rollback({
  job_id: "<job-id>",
  target_namespace: "agent-testing",
  reason: "<reason>",
  rollback_strategy: "helm_rollback" | "delete_generated_resources" | "custom_plan_rollback",
  approval_id: "<approval-id>"
})
```

The agent should use:

```text
Helm rollback when release history exists.
Helm uninstall when clone install must be removed.
K8s delete only for generated raw resources.
```

### Phase 15: Final Evidence Summary

The external GPT-5 LLM should produce a final operator summary:

```text
Source namespace:
Target namespace:
Bundle ID:
Dry-run job ID:
Mutation job ID:
Final state:
Created Helm releases:
Created Kubernetes resources:
Warnings:
Validation status:
Reports:
Rollback/cleanup status:
Correlation ID:
Trace ID:
```

## Idempotency Rules

Every mutating or state-changing request must include a request ID or idempotency key:

```text
create-job: esda-agent-testing-create-<timestamp>
start-dry-run: esda-agent-testing-start-dry-run-<timestamp>
submit-approval: esda-agent-testing-approval-<timestamp>
start-mutation: esda-agent-testing-start-mutation-<timestamp>
cleanup: esda-agent-testing-cleanup-<timestamp>
```

If the same request is retried, the execution agent should replay the existing result instead of creating duplicate jobs or duplicate mutations.

## Error Handling

The LLM must understand these common outcomes:

| Error / state | Meaning | LLM action |
| --- | --- | --- |
| `INVALID_STATE_TRANSITION` | Requested action is not valid for current job state. | Fetch job state and choose allowed next action. |
| `POLICY_BLOCKED` | Policy gate blocked the action. | Do not override. Ask for policy change or safer instruction. |
| `APPROVAL_REQUIRED` | Mutation cannot proceed without approval. | Ask human for approval scope. |
| `APPROVAL_SCOPE_MISMATCH` | Approval does not match namespace/release/command. | Submit corrected approval. |
| `DRY_RUN_FAILED` | Dry-run failed. | Retrieve observations and decision context. |
| `MCP_TIMEOUT` | Downstream MCP timed out. | Let worker pause; submit retry instruction only if safe. |
| `UNKNOWN_MUTATION_OUTCOME` | Mutation may or may not have completed. | Reconcile state before retrying. |
| `NAMESPACE_LOCKED` | Another job holds lock. | Wait or cancel conflicting job. |

## Final Validation Checklist

- [ ] Bundle validated.
- [ ] Dry-run job completed successfully.
- [ ] Observations reviewed.
- [ ] Audit events present.
- [ ] Policy gates passed.
- [ ] Human approval submitted and accepted.
- [ ] Mutation job started only after approval.
- [ ] Namespace lock was acquired.
- [ ] Helm release installed or upgraded.
- [ ] Raw Kubernetes resources applied only inside target namespace.
- [ ] Ingress uses generated prefix and target host.
- [ ] Validation report generated.
- [ ] Execution report generated.
- [ ] No source Secret values copied.
- [ ] Final state is `succeeded` or safely paused with explanation.

## External GPT-5 Prompt Skeleton

```text
You are the external execution brain for BOS Genesis.

Goal:
Execute the supplied MoP bundle into target namespace <target_namespace> through the MoP Execution Agent.

Rules:
- Do not mutate directly through Kubernetes or Helm MCPs.
- Use MoP Execution Agent for all dry-run, mutation, validation, rollback, and cleanup.
- Start with dry-run-only.
- Mutation requires successful dry-run and human approval.
- If the worker pauses, retrieve decision-required context and submit explicit scoped instruction.
- Never copy secrets.
- Never expand beyond target namespace.
- Generate final evidence report links.

Inputs:
- bundle: <bundle-ref>
- target_namespace: <target_namespace>
- source_namespace: <source_namespace>
- operator: <operator-name>
- correlation_id: <correlation-id>

Proceed phase by phase and stop at approval gates.
```

