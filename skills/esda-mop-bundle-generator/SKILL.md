# ESDA MoP Bundle Generator

## Description

Use this skill when generating a BOS Genesis Method of Procedure (MoP) and deployment artifact bundle for an allowlisted source namespace, especially when the user wants Markdown/PDF MoP documents plus Helm charts, Kubernetes manifests, rendered manifests, values files, and an artifact index.

This skill is for artifact generation only. It must not apply, install, upgrade, delete, or mutate Kubernetes resources.

## When To Use

Use this skill when the user asks to:

- Generate a MoP for an allowlisted BOS Genesis namespace, including ESDA.
- Generate MoP documents in Markdown and PDF.
- Generate Helm chart artifacts for the source namespace.
- Generate Kubernetes manifests for the source namespace.
- Clone or reconstruct source namespace artifacts for a later execution target namespace.
- Build a complete deployment artifact bundle.
- Produce a bundle that an execution agent can later dry-run or apply.

## Operating Contract

Example inputs:

```yaml
source_namespace: esda
target_namespace_placeholder: generic-namespace
operation: generate_mop_and_artifact_bundle_only
mutation_allowed: false
dry_run_allowed: true
generated_name_prefix: agent-ai
```

Hard rules:

- Do not mutate the cluster.
- Do not run `helm upgrade`, `kubectl apply`, `kubectl delete`, or equivalent mutating MCP tools.
- Do not copy source Secret values.
- Do not invent chart repositories, credentials, workloads, or service ports.
- Use observed MCP evidence before inference.
- Mark partial evidence honestly.
- Use `agent-ai` or another approved generated prefix for target resource names that may collide.
- Generate Ingress only when a source Ingress exists.

## Required File Inventory

The generated bundle must have an explicit, countable file set.

Root bundle minimum:

| Count | File type | Path pattern |
| ---: | --- | --- |
| 1 | JSON | `artifact.json` |
| 1 | YAML | `machine_execution_plan.yaml` |
| 1 | Markdown | `mop-esda-to-<target>-<timestamp>.human-mop.md` |
| 1 | Markdown | `mop-esda-to-<target>-<timestamp>.installation.md` |
| 1 | PDF | `mop-esda-to-<target>-<timestamp>.pdf` |
| 1 | ZIP | `deployment-artifacts.zip` |

Root minimum: 7 files when including the complete `mop-bundle.zip`.

Deployment artifact minimum for Helm-managed workloads:

| Count | File type | Path pattern |
| ---: | --- | --- |
| 1 | JSON | `deployment-artifacts/artifact-index.json` |
| 1 | Markdown | `deployment-artifacts/helm-commands.md` |
| 1 | Helm chart archive | `deployment-artifacts/helm-chart/<chart-name>-<version>.tgz` |
| 1 | YAML | `deployment-artifacts/helm-chart/<repo-name>-index.yaml` |
| 1 directory | Extracted chart | `deployment-artifacts/helm-chart/extracted/<chart-name>/` |
| 1 | YAML | `deployment-artifacts/helm-values/values-agent-ai-<release>.yaml` |
| 1 | YAML | `deployment-artifacts/rendered-manifests/agent-ai-<release>-rendered.yaml` |
| 1 | YAML | `deployment-artifacts/kubernetes-manifests/namespace-<target>.yaml` |
| 0 or 1 | YAML | `deployment-artifacts/kubernetes-manifests/ingress-agent-ai-<app>.yaml` |
| 0 or more | YAML | `deployment-artifacts/kubernetes-manifests/crds/*.yaml` |

Minimum deployment artifact count:

- 8 files plus extracted chart directory when no source Ingress and no CRDs exist.
- 9 files plus extracted chart directory when source Ingress exists.
- Add one file for every copied CRD YAML.

Raw Kubernetes workload files, when applicable:

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

Only generate raw files for resource kinds observed in the source namespace. If the MoP Creation Agent returns generated ConfigMap YAML files, copy those files into `deployment-artifacts/kubernetes-manifests/raw/` when available; do not create placeholder ConfigMaps when none are returned.

Compression rule:

```text
Compress exactly `deployment-artifacts/` contents into `deployment-artifacts.zip`. Then create `mop-bundle.zip` from the full bundle root, including root-level MoP documents, metadata, preserved agent payloads, `deployment-artifacts/`, and `deployment-artifacts.zip`. Do not include root-level MoP documents inside `deployment-artifacts.zip`.
```

The zip must contain:

```text
artifact-index.json
helm-commands.md
helm-chart/
helm-values/
kubernetes-manifests/
rendered-manifests/
```

## Required Evidence Sources

Use available BOS Genesis MCP servers in this order:

1. MoP Creation Agent.
2. Helm Manager MCP.
3. Kubernetes Inspector MCP.
4. K8s Data Ingestion Agent MCP.
5. Release Note Agent MCP, only for supplemental notes or summaries.

If an MCP server is unreachable, record the failure in warnings and continue only if remaining evidence is sufficient.

## Workflow

### 1. Set Source Namespace

If supported, set the active namespace on MCP servers to the selected source namespace.

Logical request:

```http
PUT /namespace
{
  "namespace": "esda"
}
```

Verify that:

```text
active_namespace == esda
allowed_namespaces includes esda
```

If runtime namespace switching is not available, pass `namespace="esda"` explicitly to every MCP call.

### 2. Generate Base MoP

Call the MoP Creation Agent with:

```json
{
  "source_namespace": "esda",
  "target_namespace": "agent-testing",
  "generation_mode": "platform-only",
  "correlation_id": "agent-ai-mop-doc-rerun-esda-<timestamp>"
}
```

Download:

- `artifact.json`
- `machine_execution_plan.yaml`
- Human MoP Markdown
- Installation notes Markdown
- Human MoP PDF
- Generated values files
- Generated manifests, if present

### 3. Inspect Artifact Metadata

Read `artifact.json` and classify the namespace:

```text
inventory.resource_count
inventory.helm_release_count
classification.helm_managed_count
classification.raw_k8s_count
reconstruction.helm_release_count
reconstruction.raw_manifest_count
warnings
```

Decision rules:

- If Helm releases exist, reconstruct Helm artifacts.
- If raw Kubernetes resources exist, reconstruct raw manifests.
- If both are missing, fail closed unless direct MCP evidence proves the workload.
- If a bundle is partial, label it as partial.

### 4. Query Helm Evidence

For Helm-managed namespaces, call:

```text
helm_list_releases(namespace="esda", all_statuses=true)
helm_release_history(release_name, namespace="esda")
helm_get_values(release_name, namespace="esda", all_values=false)
helm_release_status(release_name, namespace="esda")
```

Collect:

```text
source_release_name
chart_name
chart_version
repo_url
release_status
app_version
safe non-secret values
```

Redact all sensitive values.

### 5. Resolve and Download Chart

If the chart is public:

```bash
helm repo add <repo_name> <repo_url>
helm repo update
helm pull <repo_name>/<chart_name> --version <chart_version> --destination ./deployment-artifacts/helm-chart
helm pull <repo_name>/<chart_name> --version <chart_version> --untar --untardir ./deployment-artifacts/helm-chart/extracted
```

If Helm CLI is unavailable:

```text
Download index.yaml from the repo.
Find the chart version entry.
Download the .tgz URL.
Extract it under deployment-artifacts/helm-chart/extracted/.
```

If the chart is private and credentials are unavailable, stop and ask the user for the private chart repo or packaged chart.

### 6. Generate Target Values

Create:

```text
deployment-artifacts/helm-values/values-agent-ai-<release>.yaml
```

Rules:

- Preserve safe non-secret values.
- Use target namespace defaults.
- Do not copy source secrets.
- Disable chart Ingress if target host/backend rewriting is needed.

Example pattern:

```yaml
global:
  storageClass: local-path

<chart_root_key>:
  ingress:
    enabled: false
```

Inspect the extracted chart's `values.yaml` to identify `<chart_root_key>`.

### 7. Render Kubernetes Manifests

Run render only:

```bash
helm template agent-ai-<release> ./deployment-artifacts/helm-chart/extracted/<chart> \
  --namespace agent-testing \
  --values ./deployment-artifacts/helm-values/values-agent-ai-<release>.yaml \
  --version <chart_version> \
  > ./deployment-artifacts/rendered-manifests/agent-ai-<release>-rendered.yaml
```

Validate:

```bash
grep '^kind:' ./deployment-artifacts/rendered-manifests/agent-ai-<release>-rendered.yaml
```

The rendered file must be non-empty and should include the expected workload kinds.

### 8. Generate Namespace Manifest

Create:

```text
deployment-artifacts/kubernetes-manifests/namespace-agent-testing.yaml
```

Template:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: agent-testing
  labels:
    bosgenesis.io/generated-by: codex-mop-artifact-rerun
    bosgenesis.io/source-namespace: esda
```

### 9. Generate Ingress Manifest If Source Ingress Exists

Use Kubernetes Inspector MCP to check source Ingress.

If source Ingress exists, generate:

```text
deployment-artifacts/kubernetes-manifests/ingress-agent-ai-<app>.yaml
```

Template:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agent-ai-<app>
  namespace: agent-testing
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
    - host: <app>-agent-testing.bosgenesis.local
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

### 10. Copy CRDs

If the chart has CRDs, copy them to:

```text
deployment-artifacts/kubernetes-manifests/crds/
```

Do not apply CRDs during bundle generation.

### 11. Generate Command Runbook

Create:

```text
deployment-artifacts/helm-commands.md
```

Include render, dry-run, and approval-gated mutation commands. Clearly state that mutation commands are not executed during generation.

### 12. Generate Artifact Index

Create:

```text
deployment-artifacts/artifact-index.json
```

Include:

```json
{
  "artifact_type": "bosgenesis_deployment_artifacts",
  "source_namespace": "esda",
  "target_namespace": "agent-testing",
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
    "kubernetes-manifests/namespace-agent-testing.yaml",
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

### 13. Zip the Deployment Artifacts

Create:

```text
deployment-artifacts.zip
```

The zip must contain the whole `deployment-artifacts/` directory.

## Final Validation Checklist

Before responding to the user, verify:

- [ ] `artifact.json` exists.
- [ ] `machine_execution_plan.yaml` exists.
- [ ] Human MoP Markdown exists.
- [ ] Human MoP PDF exists.
- [ ] Helm chart package exists when Helm-managed.
- [ ] Extracted Helm chart exists when Helm-managed.
- [ ] Values file exists.
- [ ] Rendered manifest exists and is non-empty.
- [ ] Namespace manifest exists.
- [ ] Ingress manifest exists only if source Ingress exists.
- [ ] CRDs are copied when present.
- [ ] `artifact-index.json` exists.
- [ ] `deployment-artifacts.zip` exists.
- [ ] `mop-bundle.zip` exists and contains the complete bundle root.
- [ ] Raw generated ConfigMaps are present under `deployment-artifacts/kubernetes-manifests/raw/` when the MoP Creation Agent returned ConfigMap YAML payloads.
- [ ] No source Secret values are present.
- [ ] No mutation was performed.

## Failure Conditions

Stop and ask for human input when:

- ESDA cannot be inspected.
- Helm release exists but chart source cannot be identified.
- Chart repo is private and credentials are missing.
- Helm render fails.
- Required service port or ingress backend cannot be determined.
- Secret values would be required to render or reconstruct.
- The only possible action would require cluster mutation.

## Final Response Shape

Respond with:

```text
Generated ESDA MoP and deployment artifact bundle.

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



