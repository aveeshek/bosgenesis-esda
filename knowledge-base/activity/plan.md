# Activity Page Design and Implementation Plan

## 1. Purpose

The Activity page is the observability and artifact exploration page for Release Note and MoP Generation activity. It must use the Release Note page as the baseline visual and interaction design for the whole ESDA web application.

The page has two primary areas:

- A left-side scrollable, animated, modern time-series graph showing Release Note and MoP Generation activity across time.
- A right-side chatbot that can answer questions about selected nodes, groups of nodes, run events, generated Markdown files, PDFs, and published artifact metadata.

This document started as a design and planning artifact. Phases A through G are implemented and the page now supports both Release Note and MoP Generation workflow history.

---

## 2. Baseline Design Rule

The Release Note page is now the baseline design language for ESDA.

The Activity page must reuse the same product feel:

- Matte glass panels over the same vibrant AI-themed background.
- Same top navigation, model selector, profile menu, spacing rhythm, typography, and status colors.
- Same state-machine UX principle: backend and PostgreSQL own truth; browser renders state.
- Same safety principle: hidden chain-of-thought is never stored or displayed.
- Same artifact principle: Markdown/PDF outputs are durable and auditable.
- Same autonomy principle: show what the agent did through safe summaries, status nodes, evidence links, and artifact references.

The Activity page should feel like the analytical twin of the Release Note page: less form-heavy, more timeline and conversation heavy.

---

## 3. Page Objective

The Activity page must let a user answer questions like:

- When was a release-note request raised?
- Which repository and release did it target?
- Which model and agent performed it?
- How did the request move through intake, classification, planning, evidence collection, clone, security scan, quality scan, draft, validation, artifact save, and publish?
- Did it complete, fail, recover, or publish successfully?
- Where are the generated Markdown and PDF artifacts?
- What changed across multiple release-note runs?
- What did the agent conclude in the final Markdown?
- What security or quality risks were reported in the generated document?

---

## 4. Non-Goals for V1

- Do not support in-browser Markdown/PDF editing or release-note regeneration from the Activity page. Controlled upload of reviewed local Markdown/PDF replacements is allowed for selected runs.
- Do not regenerate release notes from this page in V1.
- Do not allow arbitrary GitHub repository browsing.
- Do not expose raw hidden model reasoning.
- Do not query unrelated runs outside the workflow filters and user authorization boundary.
- Do not require Qdrant in V1 unless semantic search over large artifact history becomes necessary.

---

## 5. Layout Design

### 5.1 Desktop Layout

```text
+----------------------------------------------------------------------------------+
| Top Nav: AI brand | LLM Chat | Health Check | Release Notes | Activity | Profile |
+----------------------------------------------------------------------------------+
|                                                                                  |
| +--------------------------------------------------------+  +-------------------+ |
| | Activity Timeline                                      |  | Activity Chatbot  | |
| |                                                        |  |                   | |
| | Scrollable animated release-note time-series graph     |  | Context chips     | |
| |                                                        |  | Conversation      | |
| | Node click -> details + downloads                      |  |                   | |
| | Hover -> status/execution summary                      |  | Ask about nodes   | |
| |                                                        |  | or artifacts      | |
| +--------------------------------------------------------+  +-------------------+ |
|                                                                                  |
+----------------------------------------------------------------------------------+
```

Recommended width split:

| Area | Desktop width | Notes |
|---|---:|---|
| Timeline graph | 65 percent | Primary analytical surface. |
| Chatbot | 35 percent | Persistent right-side assistant. |

The page must not use nested card-heavy UI. The two primary panes should be glass panels, with internal sections separated by subtle dividers and spacing.

### 5.2 Mobile Layout

- Timeline panel appears first.
- Chatbot collapses into a bottom drawer or tabbed pane.
- Node details open as a bottom sheet.
- Download buttons remain visible and reachable without horizontal overflow.

---

## 6. Time-Series Graph Design

### 6.1 Graph Concept

The left pane displays release-note requests as animated nodes on a chronological time-series graph.

Each major node represents one release-note generation run. The node should show:

| Field | Description |
|---|---|
| Request time | When the release-note request was raised. |
| Job name | Generated catchy session name or release name. |
| Repository | GitHub repository name. |
| Release | Release/version name. |
| Status | Running, completed, failed, recovered, published. |
| Duration | Time from run start to final status. |
| Model | GPT-5, GPT-4.1 mini, Llama, Gemma, or selected profile. |
| Agent | `bosgenesis-release-note-agent` plus ESDA enrichment. |
| Artifacts | Markdown/PDF availability and publish status. |

### 6.2 Node Expansion

Clicking a node opens a detail panel for that run. The detail panel shows the run-level activity chain:

1. Intake
2. Classify
3. Plan
4. Evidence
5. Clone
6. Security
7. Quality
8. Cleanup
9. Draft
10. Validate
11. Recover
12. Artifacts
13. Publish
14. Complete

Each stage should show:

- Start time.
- End time if available.
- Status.
- Safe summary.
- Tool or model involved.
- Important outcome.
- Link to detailed events if needed.

### 6.3 Graph Behavior

Required interactions:

- Scroll through historical release-note requests.
- Zoom by time range: today, 7 days, 30 days, custom.
- Filter by status, repository, model, published/unpublished, and release name.
- Hover a node to show a compact tooltip.
- Click a node to select it and open details.
- Shift-click or checkbox-select multiple nodes for comparison chat context.
- Animated status pulse for active/running nodes.
- Smooth reveal for newly completed/published nodes.
- Distinct status colors that match the Release Note page theme.

Recommended status mapping:

| Status | Visual treatment |
|---|---|
| Running | Blue/purple pulse, moving connector. |
| Completed | Green success ring. |
| Published | Green success ring plus artifact badge. |
| Recovered | Amber accent ring. |
| Failed | Red accent ring. |
| Cleared from sidebar | Muted, only visible if audit filter is enabled. |

### 6.4 Download Behavior

When a user clicks a completed or published node, the detail panel must show:

- Download Markdown.
- Download PDF.
- Open artifact repository folder.
- Copy artifact repository path.
- Upload reviewed Markdown to GitHub.
- Upload reviewed PDF to GitHub.

Primary artifact source should be the published repository when available:

```text
https://github.com/aveeshek/bosgenesis-artifacts/tree/main/<YYMMDD_HHMMSS_job-name>
```

Expected files:

```text
release-notes.md
release-notes.pdf
```

If published artifacts are unavailable but local ESDA artifacts exist, the UI shows local download links as fallback and labels them clearly as local ESDA artifacts. The user may also upload a reviewed Markdown or PDF file; ESDA creates a stable GitHub folder for that run and records publish metadata so future uploads overwrite the same location.

---

## 7. Right-Side Activity Chatbot Design

### 7.1 Purpose

The chatbot answers questions about the timeline, selected nodes, run events, generated Markdown content, PDF metadata, scan matrices, and artifact publishing status.

The chatbot must be context-aware. It should know which node or nodes are selected in the graph.

### 7.2 Example Questions

The user should be able to ask:

- Why did this run fail?
- Which run generated this PDF?
- Compare these two release-note runs.
- Show me the vulnerability findings for this node.
- Summarize the Markdown file for the selected run.
- Which artifacts were published to GitHub?
- Which model generated this release note?
- What evidence did release-note-agent return?
- Which run had the longest duration?
- Did any run skip the quality scan?

### 7.3 Chatbot Context Sources

The chatbot should answer from controlled data sources only:

| Source | Use |
|---|---|
| PostgreSQL `agent_runs` | Run status, timing, workflow type, model profile, generated title. |
| PostgreSQL `run_events` | Ordered activity events and safe summaries. |
| PostgreSQL `tool_calls` | Tool names, status, duration, sanitized inputs/outputs. |
| PostgreSQL `artifacts` | Local artifact metadata, MIME type, storage path, artifact IDs. |
| PostgreSQL LLM review logs | Prompt version, prompt hash, safe reasoning summaries, validation/recovery summaries. |
| Published artifact repository | Final `release-notes.md` and `release-notes.pdf` from `bosgenesis-artifacts`. |
| Local artifact storage fallback | Download or parse artifacts if publish did not happen. |

The chatbot must cite the run ID, artifact ID, or published folder when making factual claims.

### 7.4 Chatbot Guardrails

- Answer only from selected nodes, visible filtered nodes, or explicitly requested release-note artifacts.
- If the answer requires artifact content that is missing, say that the artifact is not available.
- Never reveal hidden chain-of-thought.
- Store user questions, assistant answers, selected node IDs, and cited artifact IDs in PostgreSQL.
- Store safe answer summaries for audit, not raw hidden reasoning.
- If multiple nodes are selected, answer comparatively and identify which claim belongs to which node.

---

## 8. Data Model and API Plan

### 8.1 Reused Data

The Activity page should reuse existing tables first:

- `agent_runs`
- `run_events`
- `tool_calls`
- `artifacts`
- `llm_reasoning_review_logs`
- `user_transaction_views`

### 8.2 Optional New Tables or Views

Add only if query performance or chat persistence requires them:

| Object | Purpose |
|---|---|
| `activity_chat_sessions` | Store Activity page chatbot sessions. |
| `activity_chat_messages` | Store Activity page chatbot messages and selected node context. |
| `release_note_activity_view` | SQL view/materialized view joining runs, events, artifacts, and publish metadata. |
| `artifact_text_cache` | Store normalized Markdown/PDF text snippets and checksums for chatbot retrieval. |

### 8.3 Implemented API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/activity` | Render Activity page. |
| `GET` | `/api/activity/runs` | Return Release Note and MoP Generation timeline node list with filters. |
| `GET` | `/api/activity/runs/{run_id}` | Return one run summary and workflow-specific expanded stage chain. |
| `GET` | `/api/activity/runs/{run_id}/artifacts` | Return workflow artifact metadata, published repo links, and bundle actions. |
| `GET` | `/api/activity/runs/{run_id}/artifact/{kind}/download` | Download workflow artifact, preferring published artifact repo when available and falling back to local storage. |
| `POST` | `/api/activity/runs/{run_id}/artifact/{kind}/upload` | Upload a reviewed Release Note Markdown/PDF replacement to the configured GitHub artifact repo. Overwrites existing published file, or creates a stable run folder for local-only runs. |
| `POST` | `/api/activity/chat` | Ask the Activity chatbot about selected nodes/artifacts. |
| `GET` | `/api/activity/chat/{session_id}` | Restore Activity chatbot conversation. |

### 8.4 Artifact Resolution Order

When a node offers download options, resolve artifacts in this order:

1. Published `bosgenesis-artifacts` folder from run publish metadata.
2. ESDA local artifact metadata and storage path.
3. Release-note-agent artifact reference if still resolvable.
4. Disabled download with clear reason.

Upload resolution uses a narrower write path:

1. If publish metadata exists, overwrite the exact `release-notes.md` or `release-notes.pdf` file in that folder.
2. If publish metadata is absent but the run is visible and has artifact actions, create a stable GitHub folder for that run and upload the selected file.
3. Reject all other filenames, unsupported extensions, over-large uploads, invalid PDF content, and inaccessible runs.

---

## 9. LLM Design for Activity Chatbot

### 9.1 Chain Responsibilities

The Activity chatbot should use a lightweight RAG-style flow:

1. Parse the user's question.
2. Resolve selected node IDs and any mentioned run/job/artifact identifiers.
3. Retrieve run summaries, ordered events, safe reasoning summaries, artifact metadata, and Markdown text.
4. Build a bounded answer context.
5. Ask the selected model to answer only from that context.
6. Return answer with citations to run IDs, artifact IDs, published folders, or document sections.
7. Store the question, answer, selected context IDs, and citations.

### 9.2 Response Style

Answers should be operationally useful and concise:

- Direct answer first.
- Relevant evidence next.
- Artifact/download references when useful.
- Gaps or uncertainty clearly stated.

Example citation style:

```text
Source: run_id=run_abc123, artifact=release-notes.md, section=Vulnerability Matrix
```

---

## 10. Frontend Component Plan

| Component | Responsibility |
|---|---|
| `ActivityPage` | Overall page state and layout. |
| `ActivityTimelinePane` | Left matte glass timeline panel. |
| `ActivityTimeSeriesGraph` | Animated time-series node graph. |
| `ActivityFilters` | Time range, status, repository, model, published filters. |
| `ActivityNodeTooltip` | Hover summary. |
| `ActivityNodeDetail` | Clicked node details, stage chain, download options. |
| `ArtifactDownloadActions` | Download MD/PDF, open/copy repo folder, and launch reviewed GitHub upload. |
| `ArtifactGitUploadPanel` | File picker and upload status for reviewed Markdown/PDF replacement. |
| `ActivityChatPane` | Right-side chatbot shell. |
| `ActivityContextChips` | Selected node/artifact chips. |
| `ActivityChatMessages` | Conversation transcript. |
| `ActivityChatComposer` | Prompt input and send action. |

Recommended graph implementation for V1:

- Prefer plain SVG/HTML/CSS/JavaScript to avoid adding a heavy dependency too early.
- Use Plotly.js later only if zooming, dense timelines, or comparisons become difficult to maintain manually.
- Keep all colors and animations aligned with Release Note page CSS variables.

---

## 11. Backend Component Plan

| Component | Responsibility |
|---|---|
| `routes_activity.py` | Page route and Activity API endpoints. |
| `activity_service.py` | Query release-note runs, build timeline nodes, resolve artifacts. |
| `activity_chat.py` | Context retrieval and LLM answer orchestration. |
| `artifact_content_loader.py` | Load Markdown/PDF text from published repo or local fallback. |
| `ArtifactGitPublisher.overwrite_release_artifact` | Overwrite published artifact file or create a missing run folder for Activity upload. |
| `activity_schemas.py` | Pydantic response models for nodes, details, filters, chat. |
| `activity_repository.py` | PostgreSQL query boundary for runs/events/artifacts. |

No new external datastore is required for V1. Qdrant can be added later if the number of artifacts grows enough to require semantic retrieval across many Markdown files.

---

## 12. Implementation Checklist

### Phase A: Page Shell and Navigation

- [x] Add `Activity` item to the top navigation using the Release Note page style.
- [x] Create `/activity` page route.
- [x] Build matte glass two-pane layout.
- [x] Add responsive behavior for tablet and mobile.
- [x] Reuse global model selector and profile menu.

### Phase B: Timeline Data API

- [x] Add release-note activity query service.
- [x] Return run-level timeline nodes from PostgreSQL.
- [x] Include generated title, repo, release, status, timestamps, duration, model, publish state, and artifact availability.
- [x] Add filter support for time range, status, repo, model, and published state.
- [x] Add expanded run detail endpoint with activity stages.

### Phase C: Animated Time-Series Graph

- [x] Render chronological release-note nodes.
- [x] Add status color/ring/pulse treatments.
- [x] Add hover tooltip.
- [x] Add click selection.
- [x] Add multi-select support.
- [x] Add scroll/zoom controls.
- [x] Animate new/running/completed nodes.


## 12.1 Implementation Update - 2026-06-27

Phases A, B, and C are implemented:

- `/activity` page shell is available from the primary navigation.
- Left timeline pane renders a scrollable animated time-series graph for release-note runs.
- Right chatbot pane is present as a Phase E shell and receives selected-node context copy.
- Activity API returns release-note nodes with status, repository, release, timestamps, duration, model, publish state, and artifact availability.
- Activity detail API returns sanitized stage chains and artifact summaries.
- UI supports time range, status, repository, model, and published filters.
- Graph supports hover tooltips, click selection, multi-select with Shift/Ctrl/Cmd, and selected context chips.
- Focused verification passed: Python compile, JS syntax check, ruff, and `python -m pytest backend\tests\test_phase1_app.py -q`.

### Phase D: Artifact Detail and Download

- [x] Resolve published artifact folder from run metadata.
- [x] Show Download Markdown, Download PDF, Open Repo Folder, Copy Repo Path.
- [x] Prefer downloads from `bosgenesis-artifacts` when available.
- [x] Fall back to local ESDA artifacts when publish metadata is unavailable.
- [x] Show clear disabled states when artifacts are missing.

### Phase E: Activity Chatbot

- [x] Add right-side chatbot pane.
- [x] Add selected node context chips.
- [x] Add `/api/activity/chat` endpoint.
- [x] Retrieve selected node summaries and artifact text.
- [x] Answer questions using selected nodes/artifacts only.
- [x] Cite run IDs, artifact IDs, published folders, and document sections.
- [x] Persist Activity chatbot messages and citations.


## 12.2 Implementation Update - 2026-06-28

Phases D and E are implemented:

- Activity detail now returns authoritative `artifact_actions` for Markdown, PDF, published repo folder, and copyable repo path.
- Published artifacts are preferred through the configured `bosgenesis-artifacts` GitHub repository raw URLs; local ESDA artifacts remain the fallback.
- `/api/activity/release-notes/{run_id}/artifacts` returns artifact action metadata.
- `/api/activity/release-notes/{run_id}/artifact/{kind}/download` downloads Markdown/PDF through published redirect or local fallback.
- Activity Chat now posts to `/api/activity/chat`, restores through `/api/activity/chat/{session_id}`, and persists user/assistant messages in PostgreSQL chat tables.
- Chat answers are bounded to selected release-note nodes and local Markdown text, with citations to runs, artifacts, published folders, stages, or document sections.
- The Activity Chat pane now uses the same coral/plum sphere visual language as Release Notes; the sphere shrinks into a compact thinking state while the model responds.
- Focused verification passed: Python compile, JS syntax check, ruff, and `python -m pytest backend\tests\test_phase1_app.py -q`.

## 12.3 Implementation Update - 2026-06-28 Final Activity Baseline

Additional Activity page fixes and final V1 baseline behavior are implemented:

- The right Artifact Chat panel is viewport-contained and uses internal scrolling so it does not extend outside the visible browser window.
- The shared left transaction sidebar launcher is not rendered on `/activity`; the timeline graph is the Activity page's historical navigation model.
- `Upload Markdown GITHUB` and `Upload PDF GITHUB` are enabled for selected runs with artifact actions.
- Published runs overwrite the exact `release-notes.md` or `release-notes.pdf` file in their existing `bosgenesis-artifacts` folder.
- Local-only runs can create a stable GitHub folder on first upload; the publish metadata is persisted so subsequent uploads target the same folder.
- Uploads validate authenticated run access, workflow type, file extension, file size, and PDF magic header before Git operations.
- Backend events record `artifact_overwrite_started`, optional `artifact_publish_completed`, `artifact_overwrite_completed`, and `artifact_overwrite_failed`.
- Latest verification: `python -m ruff check .`, `node --check backend/app/static/js/activity.js`, and `python -m pytest -q` passed with 84 tests.

## 12.4 Implementation Update - 2026-06-29 Multi-Workflow Activity

Activity now includes MoP Generation alongside Release Notes:

- `GET /api/activity/runs` returns Release Note and MoP Generation timeline nodes with workflow badges and filters.
- MoP nodes display source namespace, target namespace placeholder, selected environment, model profile, status, duration, bundle availability, and publish state.
- MoP run detail renders the MoP-specific stage chain: Intake, Classify, Plan, Scope, K8s, Helm, MoP Agent, Draft, Validate, Recover, Bundle, Export Github, Complete.
- MoP artifact actions expose complete bundle download for `mop-bundle.zip`; Release Note artifact upload remains constrained to reviewed `release-notes.md` and `release-notes.pdf` replacements.
- Activity Chat grounds answers on selected MoP run events, safe summaries, bundle metadata, and available artifact text.
- The Activity page remains free of the shared transaction sidebar launcher because the timeline is the page-specific historical navigation surface.
### Phase F: Safety and Audit

- [x] Redact secrets before sending context to the LLM.
- [x] Store safe answer summaries, not hidden reasoning.
- [x] Enforce user authorization for run/artifact visibility.
- [x] Ensure cleared transactions are hidden by default but remain auditable.
- [x] Add audit events for artifact GitHub upload started/completed/failed.
- [ ] Add audit events for artifact downloads and chatbot artifact analysis.

### Phase G: Testing and Acceptance

- [x] Unit test activity node query mapping through Activity API snapshots.
- [x] Unit test artifact resolution order through published/local action assertions.
- [x] Unit test chatbot context builder through selected-node chat endpoint coverage.
- [x] Unit test that hidden reasoning fields are scrubbed from Activity context.
- [x] Integration test Activity page loads with release-note run history.
- [x] Integration test node click exposes MD/PDF downloads and repo actions.
- [x] Integration test chatbot answers using selected node context.
- [x] Integration test GitHub artifact upload overwrites an existing published file.
- [x] Integration test GitHub artifact upload creates a folder for local-only runs.
- [x] UI contract test verifies Activity does not render the left transaction sidebar launcher.
- [ ] Browser-driven desktop/mobile visual test when the in-app browser connector is available.

---

## 13. Acceptance Criteria

The Activity page is complete when:

- User can open `/activity` from the top navigation.
- User sees a modern animated time-series graph of Release Note and MoP Generation runs.
- Each node shows request time, job name, workflow badge, repository or namespace, release or bundle scope, status, model, and artifact availability.
- Clicking a node shows the stage chain, download options, repo actions, and reviewed GitHub upload actions.
- Release Note Markdown/PDF downloads use the published artifact repo when available; MoP Generation exposes complete bundle download.
- Chatbot can answer questions about a selected node.
- Chatbot can summarize or compare selected generated Markdown files.
- Chatbot cites run IDs, artifact IDs, published folders, or document sections.
- Refreshing the page preserves selected filters and can restore selected node context through URL or session state.
- No hidden chain-of-thought is stored or displayed.
- Chat panel remains inside the visible browser viewport with internal scrolling.
- The global transaction sidebar launcher is absent on Activity because the timeline is the page-specific history surface.
- GitHub upload actions are constrained to reviewed `release-notes.md` and `release-notes.pdf` replacement behavior.
- All Activity interactions are auditable in PostgreSQL.

---

## 14. Recommended V1 Scope

The first Activity page shipped as release-note focused; it is now extended to include MoP Generation runs.

Recommended V1:

- Release-note and MoP Generation timeline nodes.
- Completed, failed, running, recovered, and published statuses.
- Single-node details.
- Basic multi-select for chatbot comparison.
- Markdown/PDF download from published repo or local fallback.
- Reviewed Markdown/PDF upload to the configured artifact GitHub repository for published and local-only runs.
- Chatbot answers from selected nodes and Markdown content.

Defer until later:

- Cross-workflow activity beyond Release Notes and MoP Generation, such as Helm, Kubernetes, and health checks.
- Semantic search across all artifacts using Qdrant.
- Advanced graph analytics and charts.
- Full artifact diffing between release-note files.
- Exporting Activity reports.

---

## 15. Open Questions

- Should Activity show only the current user's release-note runs or all release-note runs visible to the user's role?
- Should failed runs expose local partial artifacts if publishing never happened?
- Should downloads be proxied through ESDA for audit, or can the UI link directly to GitHub raw/blob URLs?
- Should Activity chatbot conversations be global per user or scoped to selected node sets?
- Should artifact Markdown text be cached in PostgreSQL after first read from the artifact repo?
- Should GitHub overwrite uploads require an explicit approval step after V1, or is authenticated run ownership sufficient for this artifact-review workflow?

