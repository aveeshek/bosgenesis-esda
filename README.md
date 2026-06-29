# BOS Genesis ESDA

BOS Genesis ESDA is a local Python/FastAPI web console for bounded autonomous SRE and DevOps workflows. The current implemented baseline includes:

- One authenticated UI with the final matte-glass AI theme.
- Azure OpenAI model profiles with GPT-5 as the default target and GPT-4.1 mini / Ollama-compatible profiles when configured.
- Release Note generation with `bosgenesis-release-note-agent`, repository scan enrichment, Markdown/PDF output, Git publishing, and Activity review upload.
- Activity timeline and artifact chatbot for Release Note and MoP Generation runs.
- MoP Generation bundle workflow using k8s-inspector, helm-manager, and mop-creation-agent MCP adapters where available.
- PostgreSQL for run state, UI events, tool logs, LLM review logs, artifact metadata, Activity chat, and transaction history.
- Qdrant only when semantic memory lookup is needed.
- ClickHouse and SQLite are not part of the active V1 path.

## Local Setup

### Local app with cluster ingress

This is the preferred local mode. The Python app runs on your workstation, PostgreSQL stores all V1 run/log/review data, and BOS Genesis tools are called through ingress.

```powershell
Copy-Item .env.ingress.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
python -m uvicorn backend.app.main:app --reload --port 8080
```

Open http://localhost:8080 and log in with `admin` / `admin`.

For this BOS Genesis workstation setup, use host `10.99.52.176`, port `5432`, database `esda`, user `postgres`, with the password kept only in ignored `.env`.

The ingress profile uses:

- `DATABASE_URL=postgresql+psycopg://postgres:<password>@10.99.52.176:5432/esda`
- `MCP_K8S_INSPECTOR_URL=http://k8s-inspector.bosgenesis.local`
- `RELEASE_NOTE_AGENT_MCP_URL=http://release-note-agent.bosgenesis.local`
- `RELEASE_NOTE_AGENT_URL=http://release-note-agent.bosgenesis.local` for artifact download hydration
- `QDRANT_URL=http://qdrant.bosgenesis.local`
- `ALLOWED_REST_HOSTS=localhost,127.0.0.1,.bosgenesis.local`

### Helm Deployment Profile

Use [.env.helm.example](C:/tmobile/genesis/agent-mop/bosgenesis-esda/.env.helm.example) as the environment contract for chart values and Kubernetes secrets.

Helm deployment should use:

- `DATABASE_URL` pointing to the in-cluster PostgreSQL service.
- `RELEASE_NOTE_AGENT_MCP_URL` pointing to `http://bosgenesis-release-note-agent-mcp.bosgenesis.svc.cluster.local:8090`.
- `RELEASE_NOTE_AGENT_URL` pointing to `http://bosgenesis-release-note-agent-api.bosgenesis.svc.cluster.local:8080` for artifact download hydration.
- PostgreSQL tables for run state, UI events, tool logs, and LLM review logs.
- Secrets from Kubernetes Secret objects, not plain values files.

### Docker-backed Local Mode

This remains useful when you want disposable local dependencies.

```powershell
Copy-Item .env.example .env
docker compose up -d postgres qdrant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
python -m uvicorn backend.app.main:app --reload --port 8080
```

Default demo credentials are `admin` / `admin`. Change them before any shared environment.

## Release Note Flow

ESDA calls the release-note-agent MCP compatibility tools for release-note generation. The webapp starts `github_release_scan_start`, polls `github_release_scan_status` when needed, calls `github_release_generate_note`, hydrates the generated Markdown artifact, and then asks Azure GPT to produce the final human-readable Markdown draft from that agent-produced document.

`RELEASE_NOTE_AGENT_TRANSPORT=auto` prefers MCP whenever `RELEASE_NOTE_AGENT_MCP_URL` is configured. `RELEASE_NOTE_AGENT_URL` remains useful because the current release-note-agent MCP surface returns artifact metadata, while the REST API exposes artifact downloads.

## Artifacts

Release-note drafts are saved as Markdown and PDF artifacts under `ARTIFACT_STORAGE_DIR` (`var/artifacts` by default). The UI shows download links when a release-note run completes, and the backend serves artifacts through `/api/artifacts/{artifact_id}` with the same run ownership checks used by run APIs.

When `ARTIFACT_GIT_PUBLISH_ENABLED=true`, a successful release-note run also commits the generated `release-notes.md` and `release-notes.pdf` to `ARTIFACT_GIT_REPO_URL` (`https://github.com/aveeshek/bosgenesis-artifacts.git` by default). ESDA creates a folder named `YYMMDD_HHMMSS_<job-name>` on `ARTIFACT_GIT_BRANCH` and pushes a commit using the configured Git identity. Local publishing relies on the workstation Git credential manager or other non-interactive GitHub credentials; Helm deployments should provide credentials through Kubernetes Secrets and standard git credential configuration.

The `/activity` page can later upload reviewed local Markdown/PDF replacements. For already-published runs, ESDA overwrites the exact `release-notes.md` or `release-notes.pdf` in the existing published folder. For local-only runs, the first Activity upload creates a stable GitHub folder for that run and records publish metadata so future uploads target the same folder. Upload is constrained to those two artifact filenames and is not a general GitHub editor.

## MoP Generation Bundle Flow

The `/mop-generation` page generates a read-only Method of Procedure bundle. The page follows the Release Notes baseline design: source namespace inputs, shared sphere animation, Live Working Stream plus Safe Reasoning Summaries, icon-only log copy action, maximizeable Autonomy Notes modal, copyable JSON logs, transaction history, and Agent Activity Feed.

Current MoP behavior:

- Source namespace is selected from the backend allowlist, currently including `bosgenesis`, `signoz`, and `agent-testing`.
- Target namespace is treated as a placeholder for later MoP Execution, with configured choices such as `generic-namespace` and `agent-testing`.
- Environment defaults to `Kubernetes with Helm`, with OpenShift, Kustomize, and Flux available as future-oriented modes.
- ESDA calls k8s-inspector, helm-manager, and mop-creation-agent adapters when configured and records partial evidence honestly when a service is unavailable.
- The MoP Creation Agent professional Markdown/PDF is preserved when returned, including the agent renderer metadata.
- ESDA assembles `deployment-artifacts/`, `deployment-artifacts.zip`, and the complete `mop-bundle.zip`.
- Raw generated ConfigMap YAMLs returned by the MoP Creation Agent are copied, when available, into `deployment-artifacts/kubernetes-manifests/raw/` before `deployment-artifacts.zip` is produced.
- Successful MoP runs publish the unextracted `mop-bundle.zip` into the configured artifact GitHub repository under `YYMMDD_HHMMSS_mop_<job-name>`.
- The UI exposes a single `Download MoP Bundle` action for the complete bundle.
- Activity shows MoP runs alongside Release Notes and grounds Artifact Chat on selected MoP artifacts/events.

## LLM Model Profiles

The UI exposes a model selector for the chatbot and release-note generation. The default profile is GPT-5 Pro on Azure OpenAI using `DefaultAzureCredential`:

```powershell
LLM_DEFAULT_MODEL_PROFILE=azure_gpt5_pro
AZURE_OPENAI_GPT5_ENDPOINT=https://aiservicesprjbossdcdevh23aw001.openai.azure.com/
AZURE_OPENAI_GPT5_PRO_DEPLOYMENT=bos-trainium-gpt-5.0
AZURE_OPENAI_GPT5_MODEL_NAME=gpt-5
AZURE_OPENAI_GPT5_API_VERSION=2024-12-01-preview
```

GPT-4.1 mini remains selectable and follows the legacy Azure auth settings, so local ingress mode can still use `AzureCliCredential` after `az login`:

```powershell
AZURE_OPENAI_AUTH_MODE=azure_cli
AZURE_OPENAI_ENDPOINT=https://aiservicesprjbossdcdevh23aw001.openai.azure.com/
OPENAI_DEPLOYMENT=bos-trainium-sigma-gpt-4.1-mini
OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_USE_V1_API=false
```

The UI also lists OpenAI-compatible Ollama profiles using ingress URLs: `http://ollama-llama70b.bosgenesis.local/v1` for Llama 3.3 70B (`llama3.3:70b`) and `http://ollama.bosgenesis.local/v1` for Gemma4 26B (`gemma4:26b`).
