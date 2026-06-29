# ESDA MoP and Deployment Artifact Bundle Generation Logic

## Purpose

This document describes the deterministic execution logic ESDA should follow to generate a complete BOS Genesis MoP bundle for an allowlisted source namespace. Examples use `esda`; the implemented UI also supports other configured namespaces such as `bosgenesis`, `signoz`, and `agent-testing`.

The output must include:

- Human-readable MoP in Markdown.
- Human-readable MoP in PDF.
- Machine execution plan.
- Artifact metadata.
- Helm chart package and extracted chart, when the source workload is Helm-managed.
- Helm values file using the generic namespace placeholder.
- Rendered Kubernetes manifests.
- Raw Kubernetes manifests, when the source workload is not Helm-managed.
- Raw generated ConfigMap YAMLs under `deployment-artifacts/kubernetes-manifests/raw/` when the MoP Creation Agent returns them.
- Agent-generated Ingress manifest, only when a source Ingress exists.
- Artifact index, `deployment-artifacts.zip`, and complete `mop-bundle.zip`.

The generation phase must not mutate any namespace. Mutation belongs to a separate governed execution workflow.

## Core Principle

The LLM is an orchestrator, not the source of truth.

The LLM must follow this authority order:

1. Observed MCP evidence.
2. Deterministic reconstruction.
3. Existing Helm chart or Kubernetes manifest structure.
4. Prior data ingestion facts, only as supporting context.
5. LLM suggestions, only for safe naming, documentation, or gap explanation.
6. Human input, when required for private chart repositories, missing credentials, or unsafe ambiguity.

The LLM must not invent workloads, credentials, secret values, or chart repository details.

## Required Inputs

```yaml
source_namespace: esda
target_namespace_placeholder: generic-namespace
operation: generate_mop_and_artifact_bundle_only
mutation_allowed: false
dry_run_allowed: true
generated_name_prefix: agent-ai
correlation_id: agent-ai-mop-doc-rerun-esda-<timestamp>
```

MoP Generation does not ask for, require, or bind a real target namespace. It uses the placeholder `generic-namespace`; MoP Execution assigns the real target namespace later. All generated resources must be clearly identified as agent-generated through the prefix `agent-ai` or an equivalent approved prefix.

## Required MCP Servers

Use all available BOS Genesis MCP servers when they are reachable:

- MoP Creation Agent MCP or REST API.
- Kubernetes Inspector MCP.
- Helm Manager MCP.
- K8s Data Ingestion Agent MCP.
- Release Note Agent MCP, when generating final release or evidence notes.

If an MCP server is unavailable, the bundle may still be generated only when enough authoritative evidence exists from the remaining MCPs. Any missing MCP must be recorded in warnings and artifact metadata.

## Output Directory Layout

Use this logical layout:

```text
<bundle-root>/
  artifact.json
  machine_execution_plan.yaml
  mop-esda-to-<target>-<timestamp>.human-mop.md
  mop-esda-to-<target>-<timestamp>.installation.md
  mop-esda-to-<target>-<timestamp>.pdf
  deployment-artifacts/
    artifact-index.json
    helm-commands.md
    helm-chart/
      <chart-name>-<version>.tgz
      <repo-name>-index.yaml
      extracted/
        <chart-name>/
    helm-values/
      values-agent-ai-<release>.yaml
    kubernetes-manifests/
      namespace-<target>.yaml
      ingress-agent-ai-<app>.yaml
      raw/
        configmap-*.yaml
      crds/
    rendered-manifests/
      agent-ai-<release>-rendered.yaml
  deployment-artifacts.zip
```

## Required File Inventory

The generator must produce a complete, countable artifact set. File counts may grow when the chart contains many templates or CRDs, but the following minimum inventory is required.

### Root Bundle Files

Minimum root-level files:

| Count | File type | Required path pattern | Purpose |
| ---: | --- | --- | --- |
| 1 | JSON | `artifact.json` | Bundle metadata, evidence status, warnings, inventory classification. |
| 1 | YAML | `machine_execution_plan.yaml` | Deterministic execution plan consumed by the execution agent. |
| 1 | Markdown | `mop-esda-to-<target>-<timestamp>.human-mop.md` | Operator-readable MoP. |
| 1 | Markdown | `mop-esda-to-<target>-<timestamp>.installation.md` | Installation notes and command guidance. |
| 1 | PDF | `mop-esda-to-<target>-<timestamp>.pdf` | Operator-ready PDF MoP. |
| 1 | ZIP | `deployment-artifacts.zip` | Compressed deployment artifact folder. |
| 1 | ZIP | `mop-bundle.zip` | Complete unextracted bundle for Git publishing and user download. |

Root minimum: 7 files when including the complete `mop-bundle.zip`.

### Deployment Artifact Files

Minimum files under `deployment-artifacts/` for a Helm-managed source workload:

| Count | File type | Required path pattern | Purpose |
| ---: | --- | --- | --- |
| 1 | JSON | `deployment-artifacts/artifact-index.json` | Index of all deployable artifacts and policy notes. |
| 1 | Markdown | `deployment-artifacts/helm-commands.md` | Render, dry-run, and approval-gated mutation commands. |
| 1 | Helm chart archive | `deployment-artifacts/helm-chart/<chart-name>-<version>.tgz` | Original chart package. |
| 1 | YAML | `deployment-artifacts/helm-chart/<repo-name>-index.yaml` | Source Helm repository index evidence. |
| 1 directory | Extracted chart | `deployment-artifacts/helm-chart/extracted/<chart-name>/` | Extracted chart source. |
| 1 | YAML | `deployment-artifacts/helm-values/values-agent-ai-<release>.yaml` | Generic namespace placeholder values file. |
| 1 | YAML | `deployment-artifacts/rendered-manifests/agent-ai-<release>-rendered.yaml` | Full rendered Kubernetes manifest from `helm template`. |
| 1 | YAML | `deployment-artifacts/kubernetes-manifests/namespace-<target>.yaml` | Generic namespace placeholder manifest. |
| 0 or 1 | YAML | `deployment-artifacts/kubernetes-manifests/ingress-agent-ai-<app>.yaml` | Generated only when source Ingress exists. |
| 0 or more | YAML | `deployment-artifacts/kubernetes-manifests/crds/*.yaml` | CRDs copied from the chart when present. |

Deployment artifact minimum for Helm-managed workloads with no source Ingress and no CRDs: 8 files plus one extracted chart directory.

Deployment artifact minimum for Helm-managed workloads with source Ingress: 9 files plus one extracted chart directory.

Deployment artifact count increases by the number of CRD YAML files copied from the chart.

### Raw Kubernetes Workload Files

If ESDA is not Helm-managed, or if non-Helm resources must be preserved, generate one sanitized manifest per resource or one grouped manifest per resource kind:

```text
deployment-artifacts/kubernetes-manifests/raw/
  configmaps.yaml
  services.yaml
  deployments.yaml
  statefulsets.yaml
  persistentvolumeclaims.yaml
  serviceaccounts.yaml
  roles.yaml
  rolebindings.yaml
```

Only include files for resource kinds that actually exist in the source namespace. If the MoP Creation Agent returns generated ConfigMap YAML files, copy those files into this `raw/` directory when available. Do not create placeholder ConfigMaps when none are returned.

### Compression Requirement

First compress exactly the `deployment-artifacts/` directory contents into:

```text
deployment-artifacts.zip
```

The zip must contain paths beginning with:

```text
artifact-index.json
helm-commands.md
helm-chart/
helm-values/
kubernetes-manifests/
rendered-manifests/
```

Then create the complete user/Git publish bundle from the full bundle root:

```text
mop-bundle.zip
```

`mop-bundle.zip` must include root-level MoP documents, metadata, preserved non-primary agent payloads, `deployment-artifacts/`, and `deployment-artifacts.zip`. Do not compress root MoP files into `deployment-artifacts.zip`; they belong in `mop-bundle.zip`.

## Step 1: Establish Intent and Guardrails

Before calling any tool, fix the operating mode:

```text
Mode: artifact generation only.
Apply/mutation: forbidden.
Dry-run/render: allowed.
Secret copying: forbidden.
Target namespace: not user-supplied during generation; use placeholder `generic-namespace` only.
Generated names: must use agent-ai prefix.
```

The LLM must refuse or pause if asked to copy source Secret values into the target bundle.

## Step 2: Set Source Namespace on Runtime MCPs

If an MCP server supports runtime namespace switching, set the active namespace to `esda` before collecting evidence.

Example logical call:

```http
PUT /namespace
{
  "namespace": "esda"
}
```

Then verify:

```text
active_namespace == esda
allowed_namespaces includes esda
```

If namespace switching is unavailable, call MCP methods with explicit namespace arguments.

## Step 3: Run MoP Creation

Ask the MoP Creation Agent to create a base bundle:

```json
{
  "source_namespace": "esda",
  "target_namespace_placeholder": "generic-namespace",
  "generation_mode": "platform-only",
  "correlation_id": "agent-ai-mop-doc-rerun-esda-<timestamp>"
}
```

Download and preserve:

- `artifact.json`
- `machine_execution_plan.yaml`
- Human MoP Markdown.
- Installation notes Markdown.
- Human MoP PDF.
- Generated values files.
- Generated manifests, if present.

If the generated bundle is empty or partial, do not hide that fact. Continue only if direct MCP evidence can fill the gap.

## Step 4: Inspect `artifact.json`

Read the artifact metadata and evaluate:

```text
inventory.resource_count
inventory.helm_release_count
classification.helm_managed_count
classification.raw_k8s_count
reconstruction.helm_release_count
reconstruction.raw_manifest_count
warnings
```

Decision table:

| Condition | Action |
| --- | --- |
| `helm_release_count > 0` | Treat the namespace as Helm-managed and reconstruct Helm artifacts. |
| `raw_k8s_count > 0` | Include raw Kubernetes manifests. |
| `helm_release_count == 0` and `raw_k8s_count == 0` | Fail closed unless direct MCP evidence identifies runtime resources. |
| K8s MCP unavailable | Continue only with clearly marked partial evidence. |
| Helm MCP unavailable but Helm-managed evidence exists | Stop unless chart source is already known and verifiable. |

## Step 5: Query Helm Manager MCP

Use the Helm Manager MCP to discover release evidence:

```text
helm_list_releases(namespace="esda", all_statuses=true)
```

For each release, collect:

```text
release_name
namespace
revision
status
chart
chart_version
app_version
updated_at
```

Then query:

```text
helm_release_history(release_name, namespace="esda")
helm_get_values(release_name, namespace="esda", all_values=false)
helm_release_status(release_name, namespace="esda")
```

All sensitive output must be redacted before it is written to Markdown, PDF, JSON, logs, or memory.

## Step 6: Classify Chart Source

Derive chart identity from Helm evidence.

Example:

```text
source_release_name: esda
chart_ref: <repo>/<chart>
chart_version: <version>
repo_url: <public-or-private-url>
generated_release_name: agent-ai-esda
```

Classification rules:

| Evidence | Classification | Action |
| --- | --- | --- |
| Public repo URL and chart version are known | Public Helm chart | Download chart index and package. |
| Private repo URL is known but credentials are missing | Private Helm chart | Ask user for repo credentials or a packaged chart. |
| Only runtime Kubernetes objects exist | Raw Kubernetes | Reconstruct namespace-scoped manifests. |
| Chart source cannot be identified | Incomplete | Fail closed or create a clearly labeled evidence-only bundle. |

## Step 7: Download Helm Chart

For public charts, use one of the following approaches.

Preferred Helm CLI approach:

```bash
helm repo add <repo_name> <repo_url>
helm repo update
helm pull <repo_name>/<chart_name> --version <chart_version> --destination ./deployment-artifacts/helm-chart
helm pull <repo_name>/<chart_name> --version <chart_version> --untar --untardir ./deployment-artifacts/helm-chart/extracted
```

Manual approach:

```text
Download <repo_url>/index.yaml.
Find the chart entry matching chart_name and chart_version.
Download the resolved .tgz URL.
Extract the .tgz into deployment-artifacts/helm-chart/extracted/.
```

Preserve:

```text
deployment-artifacts/helm-chart/<chart-name>-<version>.tgz
deployment-artifacts/helm-chart/<repo-name>-index.yaml
deployment-artifacts/helm-chart/extracted/<chart-name>/
```

## Step 8: Generate Target Helm Values

Create:

```text
deployment-artifacts/helm-values/values-agent-ai-<release>.yaml
```

Rules:

- Preserve safe, non-secret values that are required for the target environment.
- Never copy source Secret values.
- Prefer generic safe defaults; MoP Execution applies environment-specific namespace values later.
- Disable chart Ingress if the source Ingress is malformed, if host/service mapping must be rewritten, or if the chart-generated Ingress caused a previous failure.
- Generate Ingress separately when a source Ingress exists.

Example:

```yaml
global:
  storageClass: local-path

esda:
  ingress:
    enabled: false
```

The chart-specific top-level key must match the chart's values schema. If the schema is unknown, inspect `values.yaml` from the extracted chart.

## Step 9: Render Helm Manifests

Render manifests without applying them:

```bash
helm template agent-ai-<release> ./deployment-artifacts/helm-chart/extracted/<chart> \
  --namespace <execution_target_namespace> \
  --values ./deployment-artifacts/helm-values/values-agent-ai-<release>.yaml \
  --version <chart_version> \
  > ./deployment-artifacts/rendered-manifests/agent-ai-<release>-rendered.yaml
```

The rendered manifest must be non-empty.

Validate expected resource kinds:

```bash
grep '^kind:' ./deployment-artifacts/rendered-manifests/agent-ai-<release>-rendered.yaml
```

Expected kinds may include:

- Deployment
- StatefulSet
- Service
- ConfigMap
- Secret template
- ServiceAccount
- Role
- RoleBinding
- ClusterRole
- ClusterRoleBinding
- Job
- Pod
- CustomResource

If Helm render fails, stop and record the exact failure in the artifact metadata.

## Step 10: Generate Namespace Manifest

Create:

```text
deployment-artifacts/kubernetes-manifests/namespace-<execution_target_namespace>.yaml
```

Example:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: generic-namespace
  labels:
    bosgenesis.io/generated-by: codex-mop-artifact-rerun
    bosgenesis.io/source-namespace: esda
```

## Step 11: Reconstruct Ingress Only If Source Ingress Exists

Query Kubernetes Inspector MCP for source Ingress evidence.

If source Ingress exists, generate an agent-prefixed target Ingress:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agent-ai-<app>
  namespace: <execution_target_namespace>
  labels:
    app.kubernetes.io/name: <app>
    app.kubernetes.io/instance: agent-ai-<app>
    bosgenesis.io/generated-by: codex-mop-artifact-rerun
    bosgenesis.io/source-namespace: esda
  annotations:
    bosgenesis.io/source-ingress: <source-ingress-name>
    bosgenesis.io/runtime-prefix: agent-ai
spec:
  ingressClassName: nginx
  rules:
    - host: <app>-<execution_target_namespace>.bosgenesis.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: agent-ai-<app>
                port:
                  number: <service-port>
```

If no source Ingress exists, do not generate one.

## Step 12: Copy CRDs

If the Helm chart contains CRDs, copy them into:

```text
deployment-artifacts/kubernetes-manifests/crds/
```

CRDs must be marked as operator-reviewed resources. Do not apply them automatically during artifact generation.

## Step 13: Include Raw Kubernetes Manifests When Needed

If the namespace is not Helm-managed, or if there are non-Helm resources that must be preserved, reconstruct namespace-scoped Kubernetes manifests.

Rules:

- Rewrite namespace to the generic namespace placeholder during generation; MoP Execution substitutes the real target later.
- Add `agent-ai` prefix or equivalent approved marker where a resource name may collide.
- Exclude generated runtime fields:
  - `status`
  - `metadata.resourceVersion`
  - `metadata.uid`
  - `metadata.generation`
  - `metadata.managedFields`
  - `metadata.creationTimestamp`
- Do not copy Secret data.
- Do not include cluster-scoped resources unless explicitly approved and required.

## Step 14: Generate Command Runbook

Create:

```text
deployment-artifacts/helm-commands.md
```

Include:

```bash
# Render only
helm template agent-ai-<release> ./helm-chart/extracted/<chart> \
  --namespace <execution_target_namespace> \
  --values ./helm-values/values-agent-ai-<release>.yaml

# Dry-run only
helm upgrade --install agent-ai-<release> ./helm-chart/extracted/<chart> \
  --namespace <execution_target_namespace> \
  --create-namespace \
  --values ./helm-values/values-agent-ai-<release>.yaml \
  --dry-run

# Governed mutation only after dry-run, approval, and policy checks
helm upgrade --install agent-ai-<release> ./helm-chart/extracted/<chart> \
  --namespace <execution_target_namespace> \
  --create-namespace \
  --values ./helm-values/values-agent-ai-<release>.yaml \
  --atomic \
  --timeout 10m

# Apply generated ingress only after backend service exists
kubectl apply -f ./kubernetes-manifests/ingress-agent-ai-<app>.yaml
```

The runbook must clearly state that mutation commands are informational and must not be executed during artifact generation.

## Step 15: Generate Artifact Index

Create:

```text
deployment-artifacts/artifact-index.json
```

Example:

```json
{
  "artifact_type": "bosgenesis_deployment_artifacts",
  "source_namespace": "esda",
  "target_namespace_placeholder": "generic-namespace",
  "generated_release_name": "agent-ai-esda",
  "chart": {
    "name": "<chart>",
    "version": "<version>",
    "source_release": "<source_release>",
    "package": "helm-chart/<chart>-<version>.tgz",
    "extracted_chart": "helm-chart/extracted/<chart>",
    "repo_url": "<repo_url>"
  },
  "values": [
    "helm-values/values-agent-ai-esda.yaml"
  ],
  "kubernetes_manifests": [
    "kubernetes-manifests/namespace-generic-namespace.yaml",
    "kubernetes-manifests/ingress-agent-ai-esda.yaml"
  ],
  "rendered_manifests": [
    "rendered-manifests/agent-ai-esda-rendered.yaml"
  ],
  "commands": "helm-commands.md",
  "policy_notes": [
    "No mutation was performed during artifact generation.",
    "Secrets are not copied from source namespace.",
    "Generated resources use agent-ai prefix."
  ]
}
```

## Step 16: Zip the Bundle

Create:

```text
deployment-artifacts.zip
```

Then create the complete user/Git publish bundle:

```text
mop-bundle.zip
```

`deployment-artifacts.zip` must contain the entire `deployment-artifacts/` directory contents.

## Step 17: Final Validation Checklist

Before handing the bundle to the user, verify:

- [ ] `artifact.json` exists.
- [ ] `machine_execution_plan.yaml` exists.
- [ ] Human MoP Markdown exists.
- [ ] Human MoP PDF exists and starts with PDF magic bytes.
- [ ] Helm chart package exists when Helm-managed.
- [ ] Extracted Helm chart exists when Helm-managed.
- [ ] Target values file exists.
- [ ] Rendered manifest exists and is non-empty.
- [ ] Namespace manifest exists.
- [ ] Ingress manifest exists only if source Ingress exists.
- [ ] CRDs are copied when present.
- [ ] `artifact-index.json` exists.
- [ ] `deployment-artifacts.zip` exists.
- [ ] `mop-bundle.zip` exists and contains the complete bundle root.
- [ ] Raw generated ConfigMaps are present under `deployment-artifacts/kubernetes-manifests/raw/` when the MoP Creation Agent returned ConfigMap YAML payloads.
- [ ] Rendered manifest contains expected resource kinds.
- [ ] No source Secret values are present in generated artifacts.
- [ ] No apply or mutation command was executed.

## Failure Handling

Fail closed when:

- The source namespace cannot be inspected.
- The Helm release exists but chart source cannot be identified.
- A private chart requires credentials that were not provided.
- Helm render fails.
- The generated bundle is empty and no MCP evidence can explain why.
- Secret values are required to render safely.
- A cluster-scoped destructive operation would be needed.

Partial bundles are allowed only when clearly labeled as partial evidence bundles.

## Final Response Template

When finished, report:

```text
Generated BOS Genesis MoP and deployment artifact bundle.

Bundle root:
<path>

Included:
- Human MoP Markdown
- Human MoP PDF
- Machine execution plan
- Helm chart package/extracted chart
- Target values file
- Rendered Kubernetes manifests
- Agent-generated namespace/ingress manifests, if applicable
- Artifact index
- `deployment-artifacts.zip`
- Complete `mop-bundle.zip`

No mutation was performed.
```



