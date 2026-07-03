# ESDA MoP Execution Agent Runner

## Description

Use this skill when executing a generated ESDA MoP bundle through the BOS Genesis MoP Execution Agent for a target Kubernetes namespace.

This skill governs dry-run, approval, mutation, validation, reporting, rollback, and cleanup. The external LLM is the reasoning/orchestration layer; the MoP Execution Agent is the deterministic executor.

## When To Use

Use this skill when the user asks to:

- Execute an ESDA MoP bundle.
- Dry-run an ESDA MoP bundle.
- Apply or mutate a target namespace from a MoP bundle.
- Validate a MoP execution.
- Generate execution or validation reports.
- Roll back a MoP execution.
- Cleanup or revert a target namespace after a demo.

## Hard Rules

- Do not call Kubernetes or Helm mutation tools directly.
- Use `bosgenesis-mop-execution-agent` for dry-run, mutation, validation, rollback, and cleanup.
- Always run dry-run before mutation.
- Mutation requires human approval.
- External instructions must be explicit, scoped, and audited.
- Never copy source Secret values.
- Never mutate outside the target namespace.
- Do not bypass policy blocks.
- Pause and ask for human input on ambiguity.
- Use idempotency keys for state-changing operations.

## Default Inputs

```yaml
source_namespace: esda
target_namespace: agent-testing
bundle_ref: <mop-bundle.zip-or-bundle-id>
execution_mode: dry_run_first
mutation_allowed: false_until_approved
rollback_allowed: true_with_instruction_and_approval
generated_name_prefix: agent-ai
correlation_id: agent-ai-esda-execution-<timestamp>
```

If the bundle declares a different source namespace than the requested source namespace, stop and ask whether to continue.

## MCP Servers

Primary MCP:

```text
bosgenesis-mop-execution-agent
```

Downstream MCPs are called by the execution agent:

```text
bosgenesis-k8s-inspector-mcp
bosgenesis-helm-manager-mcp
bosgenesis-release-note-agent
bosgenesis-k8s-data-ingestion-agent
```

The external LLM should normally call only the MoP Execution Agent for execution control.

## Required Execution Agent Tools

Use MCP or REST equivalents for:

```text
health
capabilities
validate_bundle
register_bundle
create_job
get_job
list_jobs
start_job
pause_job
resume_job
cancel_job
submit_instruction
submit_approval
get_plan
get_decision_required_context
get_observations
get_audit
get_memory
evaluate_policy
request_rollback
revert_namespace
generate_report
list_reports
get_report_metadata
```

Binary report download should be exposed as REST links, not raw MCP bytes:

```text
/v1/execution-jobs/{job_id}/reports/{report_id}/download?artifact=pdf
/v1/execution-jobs/{job_id}/reports/{report_id}/download?artifact=html
/v1/execution-jobs/{job_id}/reports/{report_id}/download?artifact=markdown
```

## Workflow

### 1. Preflight Bundle Checks

Verify the bundle has:

```text
artifact.json
machine_execution_plan.yaml
human MoP Markdown
PDF MoP
deployment-artifacts.zip
deployment-artifacts/artifact-index.json
deployment-artifacts/helm-commands.md
deployment-artifacts/helm-values/*.yaml
deployment-artifacts/rendered-manifests/*.yaml
deployment-artifacts/kubernetes-manifests/*.yaml
```

Fail closed if:

- The bundle is missing `machine_execution_plan.yaml`.
- The target namespace does not match the requested target namespace.
- The source namespace does not match the requested source namespace.
- The artifact index references missing files.
- Source Secret values are present.
- Cluster-scoped destructive actions are requested.

### 2. Check Execution Agent Health

Call:

```text
mop_execution.health
mop_execution.capabilities
```

REST equivalents:

```text
GET /healthz
GET /readyz
GET /v1/capabilities
GET /v1/config/effective
```

Stop if the agent is not ready.

### 3. Validate or Register Bundle

Logical call:

```text
mop_execution.validate_bundle({
  source: {
    type: "uploaded_archive",
    reference: "<bundle-ref>"
  },
  target_namespace: "agent-testing",
  correlation_id: "<correlation-id>"
})
```

Expected:

```text
valid: true
bundle_id: <bundle-id>
source_namespace: esda
target_namespace: agent-testing
```

If invalid, retrieve validation errors and stop.

### 4. Create Dry-Run Job

Logical call:

```text
mop_execution.create_job({
  bundle_id: "<bundle-id>",
  target_namespace: "agent-testing",
  mode: "dry_run_only",
  mutation_allowed: false,
  requires_approval: true,
  correlation_id: "<correlation-id>",
  idempotency_key: "esda-agent-testing-dry-run-<timestamp>"
})
```

Store:

```text
job_id
bundle_id
correlation_id
target_namespace
```

### 5. Start Dry-Run

Logical call:

```text
mop_execution.start_job({
  job_id: "<job-id>",
  request_id: "start-dry-run-<timestamp>"
})
```

Expected state progression:

```text
created
validating_bundle
ready_for_dry_run
running
dry_run_running
dry_run_succeeded
waiting_for_approval
```

On failure:

```text
decision_required
paused
failed_safe
```

### 6. Poll and Inspect

Poll:

```text
mop_execution.get_job(job_id)
mop_execution.get_plan(job_id)
mop_execution.get_observations(job_id)
mop_execution.get_audit(job_id)
```

Review:

```text
current state
current phase
current step
last error code
policy blocks
MCP failures
dry-run output
redacted observations
audit events
```

### 7. Handle Decision Required

If state is `decision_required`, call:

```text
mop_execution.get_decision_required_context({
  job_id: "<job-id>"
})
```

Only submit instruction if safe:

```text
mop_execution.submit_instruction({
  job_id: "<job-id>",
  instruction_id: "instruction-<timestamp>",
  scope: {
    namespace: "agent-testing",
    phase_id: "<phase-id>",
    step_id: "<step-id>"
  },
  instruction: {
    action: "<allowed-action>",
    rationale: "<why-safe>",
    patch: "<optional-non-secret-patch>"
  }
})
```

Then resume:

```text
mop_execution.resume_job(job_id)
```

Do not self-repair without instruction.

### 8. Review Dry-Run Report

Retrieve:

```text
mop_execution.list_reports(job_id)
mop_execution.get_report_metadata(job_id, report_id)
```

Review:

```text
resources that would be created
helm release actions
kubernetes manifest dry-run results
policy warnings
redactions
audit completeness
trace ID
correlation ID
```

Do not proceed until dry-run is accepted by a human.

### 9. Submit Human Approval

Logical call:

```text
mop_execution.submit_approval({
  job_id: "<job-id>",
  approval_id: "approval-<timestamp>",
  approved_by: "<operator>",
  scope: {
    namespace: "agent-testing",
    release_name: "agent-ai-esda",
    operation: "approved_mutation"
  },
  expires_at: "<timestamp>",
  dry_run_job_id: "<job-id>",
  command_fingerprints: ["<dry-run-command-fingerprint>"],
  rationale: "Dry-run reviewed and approved for target namespace only."
})
```

Approval must match namespace, release, command fingerprint, and expiration.

### 10. Start Approved Mutation

Either continue the existing job or create a separate mutation job.

Preferred:

```text
mop_execution.create_job({
  bundle_id: "<bundle-id>",
  target_namespace: "agent-testing",
  mode: "approved_mutation",
  dry_run_job_id: "<dry-run-job-id>",
  mutation_allowed: true,
  approval_id: "<approval-id>",
  correlation_id: "<correlation-id>",
  idempotency_key: "esda-agent-testing-mutation-<timestamp>"
})
```

Then:

```text
mop_execution.start_job({
  job_id: "<mutation-job-id>",
  request_id: "start-mutation-<timestamp>"
})
```

Mutation gate pipeline:

```text
state gate
bundle validation gate
dry-run success gate
approval gate
namespace guard
policy guard
lock guard
idempotency guard
audit-before-mutation guard
```

### 11. Validate Mutation

The execution agent must validate:

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
Rollouts
Plan-specific validations
```

Poll until:

```text
succeeded
decision_required
paused
rollback_required
failed_safe
unknown_mutation_outcome
```

### 12. Generate Reports

Generate or retrieve:

```text
execution report
validation report
change evidence report
rollback report, if applicable
release notes, if requested
```

Use report metadata and links for binary downloads.

### 13. Rollback or Cleanup

Rollback requires instruction and approval:

```text
mop_execution.request_rollback({
  job_id: "<job-id>",
  target_namespace: "agent-testing",
  reason: "<reason>",
  rollback_strategy: "helm_rollback",
  approval_id: "<approval-id>"
})
```

Demo cleanup:

```text
mop_execution.revert_namespace({
  target_namespace: "agent-testing",
  job_id: "<job-id>",
  scope: "namespace_empty_state",
  require_approval: true,
  approval_id: "<cleanup-approval-id>",
  correlation_id: "agent-ai-esda-cleanup-<timestamp>"
})
```

Cleanup must delete Helm releases first, then generated raw resources, then approved PVC/data resources if included in the cleanup approval.

## Error Handling

| State or error | Meaning | Required action |
| --- | --- | --- |
| `INVALID_STATE_TRANSITION` | Action not allowed in current state. | Fetch job and choose valid next action. |
| `POLICY_BLOCKED` | Policy rejected the requested operation. | Do not override. |
| `APPROVAL_REQUIRED` | Mutation needs approval. | Ask human for scoped approval. |
| `APPROVAL_SCOPE_MISMATCH` | Approval does not match job scope. | Submit corrected approval. |
| `DRY_RUN_FAILED` | Dry-run failed. | Retrieve decision context and observations. |
| `MCP_TIMEOUT` | Downstream MCP timed out. | Wait or submit scoped retry instruction. |
| `UNKNOWN_MUTATION_OUTCOME` | Mutation result unclear. | Reconcile before retry. |
| `NAMESPACE_LOCKED` | Another job holds target namespace lock. | Wait or cancel conflicting job. |

## Idempotency Keys

Use stable request IDs:

```text
validate-bundle: esda-agent-testing-validate-<timestamp>
create-dry-run: esda-agent-testing-create-dry-run-<timestamp>
start-dry-run: esda-agent-testing-start-dry-run-<timestamp>
submit-instruction: esda-agent-testing-instruction-<timestamp>
submit-approval: esda-agent-testing-approval-<timestamp>
create-mutation: esda-agent-testing-create-mutation-<timestamp>
start-mutation: esda-agent-testing-start-mutation-<timestamp>
cleanup: esda-agent-testing-cleanup-<timestamp>
```

## Final Response Checklist

Report:

```text
source namespace
target namespace
bundle ID
dry-run job ID
mutation job ID
final state
created Helm releases
created Kubernetes resources
warnings
validation status
report links
rollback or cleanup status
correlation ID
trace ID
```

