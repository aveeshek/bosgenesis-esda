# Bundle Execution Demo Runbook

## Objective

Demonstrate one real, approval-gated Bundle Execution that starts with an empty `agent-testing` namespace and ends with a healthy Signoz installation. The execution must create no Kubernetes `Ingress` resource. ESDA may still reach the deployed BOS Genesis MCP services through their existing ingress endpoints.

## Pinned Demo Inputs

| Item | Value |
|---|---|
| Bundle Generation run | `mop_2b04bd2d72394766bced0c7dab4ec2a0` |
| Bundle artifact | `art_29e11e7657744f26bc7c1faf1954817a` |
| Bundle identity | `2b04bd2d72394766bced0c7dab4ec2a0` |
| Published folder | `260630_114925_mop_signoz` |
| Source namespace | `signoz` |
| Target namespace | `agent-testing` |
| Expected Helm release | `agent-ai-signoz` |
| Expected chart | `signoz/signoz` version `0.122.0` |

Do not change bundle, target namespace, generated-name prefix, or expected release during the demo.

## Required Deployment Settings

ESDA:

```env
MOP_EXECUTION_AGENT_URL=http://mop-execution-agent.bosgenesis.local
MOP_EXECUTION_AGENT_MCP_URL=http://mop-execution-agent.bosgenesis.local
HELM_MANAGER_AGENT_MCP_URL=http://helm-manager.bosgenesis.local
K8S_INSPECTOR_AGENT_MCP_URL=http://k8s-inspector.bosgenesis.local
MOP_EXECUTION_DEMO_PASS_THROUGH_ENABLED=false
MOP_EXECUTION_POST_MUTATION_LIVE_VERIFICATION_ENABLED=true
MOP_EXECUTION_POST_MUTATION_VERIFICATION_ATTEMPTS=30
MOP_EXECUTION_POST_MUTATION_VERIFICATION_INTERVAL_SECONDS=10
MOP_EXECUTION_POST_MUTATION_REQUIRE_NO_INGRESS=true
MOP_EXECUTION_POST_MUTATION_EXPECTED_HELM_RELEASE=agent-ai-signoz
```

MoP Execution Agent:

```env
MOP_EXECUTION_EXCLUDED_K8S_KINDS=Ingress
```

The execution-agent deployment must be rebuilt/redeployed after adding this property. The default in source is also `Ingress`, but the explicit deployment value makes the demo contract visible.

## Pre-Demo Proof

Verify the target is empty before starting:

```text
kubectl get pods,svc,ingress -n agent-testing
helm list -n agent-testing
```

Expected: no workload resources and no Helm releases. Keep the namespace itself present.

## UI Journey

1. Open `http://127.0.0.1:9999/mop-execution` and click **Start New**.
2. Select the pinned Signoz bundle and target `agent-testing`.
3. Select **Approved mutation**, enter a clear rationale, and prepare execution.
4. Review the successful dry-run, submit approval, and start mutation.
5. Wait for post-mutation verification. ESDA must show `completed` and this evidence-backed message:

```text
Mutation completed successfully. Helm release agent-ai-signoz is deployed; all workload pods are Ready or Completed; services are present; and zero Kubernetes Ingress resources were created.
```

## Success Evidence

ESDA polls the deployed Helm Manager and Kubernetes Inspector after mutation. Green success requires all conditions:

- Helm release `agent-ai-signoz` has status `deployed`.
- At least one workload Pod exists.
- Every Pod is Running and Ready, or Succeeded/Completed.
- At least one Service exists.
- No Kubernetes Ingress exists.

The UI must remain `needs_review` if any condition is missing. `MOP_EXECUTION_DEMO_PASS_THROUGH_ENABLED` remains `false`.

## Expected Namespace Shape

The exact generated Pod suffixes vary. The healthy shape includes the Signoz server, ClickHouse operator and instance, OpenTelemetry collector, ZooKeeper, and a completed telemetry-store migrator. Services should be present. Ingress count must be zero.

## Safety and Reset

- Do not launch a second mutation while `agent-testing` is populated.
- Use the run's **Cleanup/Revert** action after the demonstration.
- Recheck Pods, Services, Ingresses, and Helm releases before another test.
- If ESDA does not reach terminal success, open Autonomy Notes and inspect `live_mcp_verification`; do not claim success from an agent report alone.