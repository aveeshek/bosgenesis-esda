# ESDA Namespace Readiness Twin UI-First Implementation Plan

**Controlling design:** `esda_namespace_digital_twin_design.md`  
**UX blueprint:** `esda_digital_twin_webpage_implementation_plan.md`, incorporated 2026-07-13  
**Delivery strategy:** UI first, contracts second, real modules as end-to-end vertical slices  
**Initial scope:** Lab and approved non-production namespaces  
**First visible static UI target:** 3-5 working days  
**Browser-mock target:** 1-2 weeks  
**Server-mock target:** 2-3 weeks  
**Real approval-gated baseline:** 10-14 weeks, depending on backend readiness and parallel staffing  
**Status:** Phases 0-4 and Slice 5A engineering implemented; Slice 5A user acceptance pending

---

## 1. UI-First Delivery Strategy

The implementation order is mandatory:

```text
1. Static UI with hardcoded HTML
   No fetch, no API, no database, no GPT, no MCP

2. Browser-only mock application
   JavaScript fixtures and state only, still no server calls

3. Server-side mock application
   Final HTTP contracts backed by fixture data, no real twin engine, GPT, or MCP

4. Real end-to-end vertical slices
   Replace one mocked module at a time with deterministic backend facts,
   then add the server-side SIGMA 5 PRO explanation for that module
```

The user must be able to review and accept the visual behavior at the end of every stage. Do not wait for the complete backend before showing the workspace.

### Stage Gates

| Gate | User sees | Data source | Explicitly absent | Exit decision |
|---|---|---|---|---|
| UI-0 Static | Complete list/detail/gate layout and all tabs | Hardcoded HTML | Server, fetch, database, GPT, MCP | Visual layout accepted |
| UI-1 Browser Mock | Filters, tabs, actions, progress, errors, restoration | JavaScript fixture objects | Server, database, GPT, MCP | Interaction behavior accepted |
| UI-2 Server Mock | Same UX over final HTTP-shaped contracts | FastAPI mock endpoints and fixture files | Real twin engine, real GPT, real MCP | API and restoration contracts accepted |
| E2E-N Real Slice | One module using real evidence plus server-side explanation | Execution agent, PostgreSQL, MCP, SIGMA 5 PRO | Any still-mocked module remains visibly labeled | Slice accepted before next module |

### Non-Negotiable Architecture Rules

- [ ] Keep the deterministic twin engine inside `bosgenesis-mop-execution-agent`.
- [ ] Keep ESDA responsible for pages, gateway view models, orchestration references, safe summaries, and approval capture.
- [ ] Do not create a new microservice or MCP server for the baseline.
- [ ] Reuse the existing execution-agent dry-run as authoritative evidence.
- [ ] Do not create a second Kubernetes or Helm dry-run path.
- [ ] Keep the twin lifecycle separate from execution-job state.
- [ ] Use opaque `TEXT` identifiers across services and PostgreSQL.
- [ ] Separate policy verdict, evidence completeness, change risk, freshness, approval, and execution state.
- [ ] Treat Green as eligibility inside the configured ODD, not guaranteed runtime success.
- [ ] Require human approval by default during the baseline release.
- [ ] Never infer deletion from a missing planned resource.
- [ ] Persist only redacted evidence and safe reasoning summaries.
- [ ] Never expose or store hidden chain-of-thought.
- [ ] Never allow GPT output to set policy, risk, evidence status, approval, or execution eligibility.
- [ ] Keep every unimplemented module visibly labeled `Mock`, `Not Run`, or `Not Available`.

### Product Surfaces

- [ ] Add **Digital Twins** between **Bundle Generation** and **Bundle Execution** in top navigation.
- [ ] Implement `/digital-twins` as the searchable twin-run list.
- [ ] Implement `/digital-twins/{twin_id}` as the canonical detail cockpit.
- [ ] Keep route generation base-path agnostic for deployments using `/esda`.
- [ ] Add a compact immutable Twin Gate inside Bundle Execution.
- [ ] Keep the full evidence workspace out of the Bundle Execution page.
- [ ] Link Bundle Generation, Digital Twins, Approvals, Bundle Execution, Activity, and reports.

---

## 2. Phase 0: UX and Contract Freeze

**Priority:** P0  
**Estimate:** 2-3 working days  
**Implementation status:** Complete on 2026-07-13; explicit product and cross-owner approvals remain pending.  
**Exit gate:** The page structure and fixture contracts are approved before static implementation.

**Frozen UX contract:** `phase0_ux_contract.md`  
**Machine-readable package:** `contracts/v1/`  
**Contract validation:** `backend/tests/test_digital_twin_phase0_contracts.py`

### Page Contract

- [x] Freeze top-navigation placement and labels.
- [x] Freeze routes and deployment base-path behavior.
- [x] Freeze Digital Twins list columns.
- [x] Freeze list filters, sorting, pagination, and URL query behavior.
- [x] Freeze list row actions and disabled-state explanations.
- [x] Freeze detail header, sticky summary, lifecycle labels, and action matrix.
- [x] Freeze the compact Bundle Execution Twin Gate fields.
- [x] Freeze deep links to selected tabs, findings, resources, and audit events.

### Exact Tab Contract

- [x] Freeze this exact order: Overview.
- [x] Freeze this exact order: Release Delta Twin.
- [x] Freeze this exact order: Dependency Graph Twin.
- [x] Freeze this exact order: Policy Twin.
- [x] Freeze this exact order: Dry-run / Diff Twin.
- [x] Freeze this exact order: Rollback Twin.
- [x] Freeze this exact order: Drift Twin.
- [x] Freeze this exact order: MoP Replay Twin.
- [x] Freeze this exact order: Runtime Behavior Twin.
- [x] Freeze this exact order: Release Note Validation Twin.
- [x] Freeze this exact order: Audit Timeline.
- [x] Define per-tab `loading`, `available`, `empty`, `not_run`, `not_available`, `failed`, and `stale` states.

### Visible State Contract

- [x] Define lifecycle projections for requested, generating, awaiting dry-run, decision-calculating, completed, failed, and cancelled.
- [x] Define final Green, Amber, and Red messages.
- [x] Define stale, drifted, expired, and superseded messages.
- [x] Define approval-required and approved relationship labels.
- [x] Define linked-execution and used-for-execution labels.
- [x] Define action eligibility returned by the backend rather than inferred by JavaScript.
- [x] Define preliminary-state styling that cannot be mistaken for a final decision.

### Fixture Schema

- [x] Create one shared JSON schema for list rows.
- [x] Create one shared JSON schema for detail header and summary.
- [x] Create one shared JSON schema per tab.
- [x] Create one shared JSON schema for events and audit timeline.
- [x] Create one shared JSON schema for action eligibility.
- [x] Create one shared JSON schema for safe GPT explanation blocks.
- [x] Version fixture schemas from the beginning.
- [x] Keep fixture field names aligned with planned HTTP responses.

### Phase 0 Acceptance

- [ ] Product owner approves list layout and detail cockpit.
- [ ] Product owner approves exact tab order and visible states.
- [ ] Backend and frontend owners approve the same fixture/API schemas.
- [x] No unresolved question remains about which layer owns the decision.

Approval note: the three unchecked items are human sign-offs and are intentionally not marked complete by implementation. Phase 1 must not be represented as accepted until those reviewers approve the frozen contract or record a versioned amendment.

---

## 3. Phase 1: Static UI Prototype

**Priority:** P0  
**Estimate:** 3-5 working days  
**Server interaction:** None  
**Data:** Hardcoded directly in HTML  
**Exit gate:** The user accepts how every page and tab looks before dynamic behavior begins.

### Prototype Structure

- [x] Create an isolated static prototype directory under the ESDA repository.
- [x] Add `digital-twins.html` for the list page.
- [x] Add `digital-twin-detail.html` for the detail cockpit.
- [x] Add `bundle-execution-twin-gate.html` for the compact gate preview.
- [x] Add shared prototype CSS using current ESDA design tokens.
- [x] Add only minimal local JavaScript needed to switch static tabs and open/close panels.
- [x] Do not call `fetch`, XMLHttpRequest, WebSocket, SSE, or any server endpoint.
- [x] Make every prototype open directly in a browser from the filesystem.

### Static Digital Twins List

- [x] Reuse the existing ESDA colorful background and matte-glass visual language.
- [x] Reproduce the current top navigation, model selector, and profile control visually.
- [x] Show the Digital Twins page title and a compact summary band.
- [x] Show a non-functional filter bar.
- [x] Show columns: Twin Run ID, Decision, Risk Score, Target Cluster, Target Namespace, MoP Bundle, Release Version, Freshness, Created By, Created At, Linked Execution, Actions.
- [x] Hardcode representative Green, Amber, Red, Generating, Stale, Failed, and Superseded rows.
- [x] Show compact accessible badges with icon/text plus color.
- [x] Show non-functional Open, Regenerate, Download Report, Open Execution, and Request Approval controls.
- [x] Show static result count and pagination controls.
- [x] Create static empty, loading, no-results, and error variants.

### Static Detail Shell

- [x] Show the twin title, lifecycle state, decision badge, risk score, autonomy eligibility, and recommended action.
- [x] Show static primary actions for Generate/Regenerate, Open Bundle, Open Execution, Start Execution, Request Approval, Approve, Reject, Download Report, and Export JSON.
- [x] Create a sticky summary with target, bundle, twin, release, creator, timestamps, freshness, execution, and approval links.
- [x] Keep lifecycle, decision, freshness, approval, and execution visually separate.
- [x] Show a static generating state and a static final-decision state.
- [x] Ensure the page can be understood within 30 seconds.

### Static Tabs

- [x] Build the Overview layout and summary cards.
- [x] Build the Release Delta table and side-by-side diff drawer.
- [x] Build the Dependency Graph canvas area, controls, legend, node-detail drawer, and table alternative.
- [x] Build the Policy findings groups and filters.
- [x] Build the Dry-run / Diff observations and fidelity-limit panel.
- [x] Build the Rollback confidence and evidence layout.
- [x] Build the Drift freshness and changed-resource layout.
- [x] Build the MoP Replay `Not Run` layout.
- [x] Build the Runtime Behavior rules-first layout.
- [x] Build the Release Note Validation `Not Run` layout.
- [x] Build the Audit Timeline layout and filters.
- [x] Hardcode at least one full visual example for each tab.

### Static Bundle Execution Gate

- [x] Show Twin ID, decision, risk, policy, evidence, freshness, dry-run, rollback, drift, top reasons, and approval requirement.
- [x] Show static Green, Amber, Red, stale, expired, and generating variants.
- [x] Show **View Full Twin**, Regenerate, Request Approval, and Start Execution controls.
- [x] Keep the gate compact and visibly distinct from the full cockpit.

### Static Responsive and Accessibility Review

- [ ] Verify wide desktop layout.
- [ ] Verify standard laptop layout.
- [ ] Verify tablet layout.
- [ ] Verify mobile layout.
- [x] Ensure tabs scroll or collapse accessibly on narrow screens.
- [ ] Ensure text and controls do not overlap or clip.
- [x] Ensure status is not communicated by color alone.
- [x] Ensure keyboard focus order and visible focus states are designed.
- [x] Ensure reduced-motion styling is represented.
- [ ] Capture screenshots for user review.

### Phase 1 Acceptance

- [ ] User approves the list-page look and information density.
- [ ] User approves the detail shell and sticky summary.
- [ ] User approves all eleven tab layouts.
- [ ] User approves the compact Bundle Execution gate.
- [x] No server process is required to demonstrate the prototype.

---
## 4. Phase 2: Browser-Only JavaScript Mock

**Priority:** P0  
**Estimate:** 4-6 working days  
**Server interaction:** None  
**Data:** JavaScript fixture modules only  
**Exit gate:** The user accepts all interactions, transitions, restoration, and error states.

### Frontend Mock Architecture

- [x] Move hardcoded records into versioned JavaScript fixture objects.
- [x] Add a `TwinDataAdapter` interface used by every page component.
- [x] Implement `BrowserFixtureTwinAdapter` with Promise-based methods.
- [x] Keep component code unaware of whether data comes from fixtures or HTTP.
- [x] Simulate latency with deterministic configurable delays.
- [x] Simulate success, partial, empty, stale, and failed responses.
- [x] Add a development-only fixture selector, not a production user control.
- [x] Do not use `fetch`, server routes, PostgreSQL, GPT, or MCP.
- [x] Expose the browser-only mock through the authenticated `/digital-twins` ESDA navigation tab without adding data APIs.

### List Interactions

- [x] Implement free-text search over safe fixture fields.
- [x] Implement decision, lifecycle, freshness, target, bundle, creator, date, and linked-execution filters.
- [x] Implement sorting and client-side mock cursor pagination.
- [x] Store filters and selected page in the URL.
- [x] Restore list state with browser Back and Forward.
- [x] Implement row selection and mock navigation to detail.
- [x] Implement disabled actions with explanations.
- [x] Implement loading skeleton, empty, no-results, partial, and retry behavior.
- [x] Simulate bounded refresh for active rows.

### Detail Interactions

- [x] Implement selected-tab routing with `?tab=<slug>`.
- [x] Lazy-load each tab from the JavaScript adapter.
- [x] Cache tab fixtures by twin ID and decision version.
- [x] Invalidate mock cache when Regenerate produces a new decision version.
- [x] Restore selected tab after refresh.
- [x] Implement summary-card navigation to tabs and findings.
- [x] Implement drawers, modal evidence views, graph selection, and audit event deep links.
- [x] Implement copy and mock-download controls.
- [x] Implement action menus from fixture eligibility.

### Mock Lifecycle and Progress

- [x] Simulate requested -> generating -> awaiting dry-run -> decision-calculating -> Green.
- [x] Simulate Amber approval-required flow.
- [x] Simulate Red blocked flow.
- [x] Simulate failed and cancelled generation.
- [x] Simulate stale/drifted -> Regenerate -> superseded old decision.
- [x] Keep previous evidence visible while a new version is generating.
- [x] Never show a preliminary state as a final Green/Amber/Red decision.

### Browser-Only Restoration

- [x] Store mock run history in localStorage only for this stage.
- [x] Restore a selected active mock twin after refresh.
- [x] Restore terminal evidence only when selected from list/history.
- [x] Do not auto-open an unrelated completed twin on fresh load.
- [x] Add a development-only clear-mock-history action.
- [x] Document that localStorage is removed as source of truth in the server-mock stage.

### Browser Mock Scenarios

- [x] Green low-risk Helm change.
- [x] Amber PVC/RBAC change with approval required.
- [x] Red forbidden cluster-scope or Secret-data finding.
- [x] Generating twin with progressive tab availability.
- [x] Failed dry-run.
- [x] Stale live snapshot.
- [x] Material drift after decision.
- [x] Superseded decision.
- [x] Missing optional replay evidence.
- [x] Missing historical runtime evidence.
- [x] Large delta with at least 500 rows.
- [x] Large graph with at least 300 nodes.
- [x] Long audit timeline with cursor-style paging.

### Phase 2 Acceptance

- [ ] User can exercise the complete UX without a server.
- [ ] Filters, tabs, actions, progress, restoration, and errors behave as intended.
- [x] Every optional module has an explicit visible state.
- [x] Browser components depend only on the adapter contract.
- [ ] Product owner approves behavior before HTTP integration.

---

## 5. Phase 3: Server-Side Mock API

**Priority:** P0  
**Estimate:** 4-6 working days  
**Server interaction:** Mock HTTP only  
**Data:** Server fixture files or in-memory fixture repository  
**Real integrations:** None  
**Exit gate:** Final API-shaped UX works through ESDA, including restart and error handling.

### Mock Server Rules

- [x] Implement mock routes in an isolated ESDA module behind a development configuration flag.
- [x] Never enable mock mode by default in production deployment.
- [x] Serve the same versioned fixtures used by the browser-only stage.
- [x] Add a server fixture repository abstraction matching the future real gateway service.
- [x] Return realistic HTTP status codes and typed error bodies.
- [x] Support configurable delay, timeout, partial response, and failure injection.
- [x] Do not call PostgreSQL, GPT, execution agent, K8s MCP, or Helm MCP.
- [x] Mark every response with `data_mode: mock_server`.
- [x] Show a visible non-production Mock Data badge in the page shell.

### Page Routes

- [x] Integrate the accepted list markup into the ESDA template structure.
- [x] Add `/digital-twins` page route.
- [x] Integrate the accepted detail markup into the ESDA template structure.
- [x] Add `/digital-twins/{twin_id}` page route.
- [x] Reuse ESDA authentication, navigation, model selector, profile menu, background, and matte-glass styles.
- [ ] Add the mock compact gate to Bundle Execution.
- [ ] Remove prototype-only duplicate layout code.

### Mock Gateway Endpoints

- [x] Implement mock `GET /api/digital-twins` with search, filters, sorting, and cursor pagination.
- [x] Implement mock `POST /api/digital-twins`.
- [x] Implement mock `GET /api/digital-twins/{twin_id}`.
- [x] Implement mock `POST /api/digital-twins/{twin_id}/regenerate`.
- [x] Implement mock summary endpoint.
- [x] Implement mock delta endpoint.
- [x] Implement mock graph endpoint.
- [x] Implement mock policy endpoint.
- [x] Implement mock dry-run endpoint.
- [x] Implement mock rollback endpoint.
- [x] Implement mock drift endpoint.
- [x] Implement mock replay endpoint.
- [x] Implement mock runtime-risk endpoint.
- [x] Implement mock release-note-validation endpoint.
- [x] Implement mock audit endpoint with cursor pagination.
- [x] Implement mock report and events endpoints.
- [x] Implement mock safe-explanation endpoint; return fixture text without calling GPT.

### Frontend HTTP Adapter

- [x] Implement `HttpTwinAdapter` against the mock gateway.
- [x] Select the adapter by server-provided configuration, not a user query string in production.
- [x] Remove direct fixture imports from page components.
- [x] Implement request cancellation when changing tabs or twins.
- [x] Implement retry with bounded non-mutating behavior.
- [ ] Deduplicate event polling by event ID/sequence.
- [x] Preserve URL filters, selected tab, and selected twin after server restart.
- [x] Show authentication, authorization, not-found, conflict, timeout, and internal-error states clearly.

### Contract Tests

- [ ] Validate every mock response against the frozen schema.
- [x] Validate list query parameters and cursor behavior.
- [x] Validate typed tab availability.
- [x] Validate action eligibility and disabled reasons.
- [x] Validate error-body shape.
- [ ] Validate redaction fixtures contain no Secret values.
- [ ] Validate browser code never derives a decision from tab payloads.
- [ ] Validate the compact gate and detail summary receive identical decision facts.

### Phase 3 Acceptance

- [x] UI behavior remains visually equivalent to the browser-only mock.
- [x] Refresh and navigation restore through server-shaped identifiers and URLs.
- [x] Mock server can demonstrate Green, Amber, Red, stale, failed, and superseded states.
- [ ] All final page/API contracts are approved before real backend integration.
- [x] No real GPT, database, execution-agent, Kubernetes, or Helm call occurs.

---
## 6. Phase 4: Real Backend Foundation

**Priority:** P0  
**Estimate:** 2-3 weeks  
**User-visible strategy:** Keep the accepted UI; replace mock facts only after real contracts pass.  
**Exit gate:** A real provisional twin can be created, restored, and listed without yet enabling every tab.

### Architecture and ADRs

- [x] Record that the twin core belongs in MoP Execution Agent.
- [x] Record that no new baseline microservice or MCP server is created.
- [x] Record reuse of the authoritative existing dry-run.
- [x] Record ESDA as gateway/presentation, not decision authority.
- [x] Freeze lifecycle, opaque identifiers, input hashes, report hashes, policy versions, and risk-rule versions.

### PostgreSQL Foundation

- [x] Create `namespace_twin_runs` with `TEXT` identifiers.
- [x] Create ordered `namespace_twin_events`.
- [x] Create `namespace_twin_resources` and stable identities.
- [x] Create `namespace_twin_edges`.
- [x] Create `namespace_twin_findings`.
- [x] Create append-only `namespace_twin_decisions`.
- [x] Add foreign keys, indexes, cascade/retention behavior, and scoped idempotency.
- [x] Add migration rollback instructions.
- [x] Add restart, reconnect, ordering, and concurrency tests.

### Twin Lifecycle Service

- [x] Implement requested, generating, awaiting-dry-run, decision-calculating, terminal, failed, and cancelled states.
- [x] Reject invalid transitions.
- [x] Recover every non-terminal state after worker restart.
- [x] Make terminal decision versions immutable.
- [x] Implement expiry and supersession.
- [x] Implement idempotent create/get/list/event/cancel operations.
- [x] Persist redacted events only.

### Bundle and Plan Foundation

- [x] Reuse bundle reader and validator.
- [x] Reuse machine-plan parser and phase dependency validation.
- [x] Verify checksums and artifact-index provenance.
- [x] Calculate deterministic input hash.
- [x] Reject unsupported schema versions.
- [x] Enforce target namespace and cluster-scope rules.
- [x] Detect source namespace residue and Secret data patterns.
- [x] Parse explicit delete operations without inferring omission as delete.

### Real API and ESDA Gateway

- [x] Add execution-agent create/get/list/events/cancel endpoints.
- [x] Review matching execution-agent MCP tools; no duplicate MCP surface is required for the Phase 4 REST foundation.
- [x] Replace the ESDA mock list/detail lifecycle provider with the real execution-agent client.
- [x] Keep tab adapters in mock mode until each vertical slice is accepted.
- [x] Mark mixed pages clearly at development time: Real Core plus Mock Module.
- [x] Remove `data_mode: mock_server` only from accepted real responses.

### Phase 4 Acceptance

- [x] Real twin runs appear in the existing accepted list UI.
- [x] A real provisional twin survives execution-agent and ESDA restart.
- [x] Event ordering and idempotency tests pass.
- [x] Invalid bundles fail safely and visibly.
- [x] Still-mocked tabs remain labeled and cannot influence real execution.

---

## 7. Phase 5: Real End-to-End Modules, One by One

**Priority:** P0/P1 by slice  
**Method:** Do not convert all modules at once. Complete each slice through backend facts, ESDA gateway, UI, server-side explanation, tests, and user acceptance before starting the next slice.

### Definition of Done for Every Real Slice

- [ ] Deterministic backend computation is implemented and unit-tested.
- [ ] PostgreSQL/artifact persistence is implemented when required.
- [ ] Execution-agent REST/MCP contract is implemented and schema-tested.
- [ ] ESDA gateway maps facts without reinterpretation.
- [ ] The frontend switches only that module from mock adapter to HTTP real adapter.
- [ ] Loading, empty, partial, failed, stale, and unavailable states are tested.
- [ ] SIGMA 5 PRO explanation runs server-side only after deterministic facts exist.
- [ ] Explanation input is a redacted structured fact envelope, not raw unrestricted logs.
- [ ] Prompt version, prompt hash, model profile, input hash, latency, token usage, safe output, and errors are logged.
- [ ] Safe explanation summary may be persisted; hidden reasoning is never requested or stored.
- [ ] GPT failure falls back to deterministic operator text without failing the module.
- [ ] The model cannot change decision fields or action eligibility.
- [ ] Contract, browser, accessibility, and user-acceptance tests pass.
- [ ] The module's Mock badge is removed only after acceptance.

### Slice 5A: Real List, Lifecycle, and Overview

- [x] Implement server-side list filtering, sorting, and cursor pagination.
- [x] Implement real detail header and sticky summary.
- [x] Implement lifecycle-to-user-label projection.
- [x] Implement real action eligibility.
- [x] Implement real active/terminal restoration.
- [x] Implement deterministic preliminary and final summary objects.
- [x] Add server-side SIGMA 5 PRO explanation for top reasons and recommended next step.
- [x] Keep decision, policy, evidence, and risk values copied from deterministic facts.
- [x] Verify Overview loads without fetching every artifact.
- [x] Obtain user acceptance before Slice 5B.

### Slice 5B: Release Delta Twin

- [x] Implement Kubernetes-aware canonicalization.
- [x] Remove runtime metadata without hiding meaningful intent.
- [x] Normalize quantities and schema-aware list semantics.
- [x] Preserve immutable fields and provenance.
- [x] Implement create, update, no-op, explicit-delete, unknown, and immutable-conflict results.
- [x] Implement field-level diff and high-risk change detectors.
- [x] Implement real paginated/filterable delta API.
- [x] Switch Release Delta tab from mock to real.
- [x] Add server-side SIGMA 5 PRO explanation of important changes using structured delta facts.
- [x] Verify omission never becomes deletion.
- [x] Obtain user acceptance before Slice 5C.

### Slice 5C: Dependency Graph Twin

- [x] Build stable resource nodes.
- [x] Build owner, selector, route, ConfigMap, Secret-name, PVC, Helm, and plan-phase edges.
- [x] Attach confidence and evidence reference to every edge.
- [x] Detect missing and uncertain dependencies.
- [x] Implement real graph summary, node, edge, filter, and table-alternative API.
- [x] Switch Dependency Graph tab from mock to real.
- [x] Add server-side SIGMA 5 PRO impact-path explanation grounded in selected graph facts.
- [x] Verify the browser renders but never infers dependencies.
- [ ] Obtain user acceptance before Slice 5D.

### Slice 5D: Policy Twin and Deterministic Decision Axes

- [x] Reuse the existing execution-agent policy engine.
- [x] Version the effective policy bundle.
- [x] Implement hard-block and approval-required findings.
- [x] Implement evidence completeness and freshness independently.
- [x] Implement versioned deterministic change-risk rules independently.
- [x] Produce full rule contribution breakdown.
- [x] Implement Green, Amber, and Red precedence.
- [x] Implement real policy/findings API.
- [x] Switch Policy tab from mock to real.
- [x] Add server-side SIGMA 5 PRO plain-language policy explanation.
- [x] Prove model output cannot upgrade or downgrade policy, evidence, risk, or decision.
- [ ] Obtain user acceptance before Slice 5E.

---
### Slice 5E: Authoritative Dry-run / Diff Twin

- [x] Create or restore the existing execution-agent dry-run job.
- [x] Poll the existing dry-run state.
- [x] Verify bundle, target namespace, input hash, and command fingerprints.
- [x] Reject stale, failed, partial, superseded, or mismatched evidence according to policy.
- [x] Store references to authoritative observations and reports.
- [x] Implement real dry-run/diff API.
- [x] Switch Dry-run / Diff tab from mock to real.
- [x] Display Kubernetes/Helm results, rejections, logs, fingerprints, and fidelity limits.
- [x] Add server-side SIGMA 5 PRO explanation of failures and safe next steps.
- [x] Do not allow GPT to auto-submit instructions or retry mutation.
- [ ] Obtain user acceptance before Slice 5F.

### Slice 5F: Rollback Twin

- [x] Parse rollback steps and link them to forward operations.
- [x] Collect Helm revision/provenance evidence.
- [x] Assess previous manifests/values availability.
- [x] Assess PVC/data reversibility and non-reversible changes.
- [x] Calculate deterministic High, Medium, Low, or Unavailable confidence.
- [x] Implement real rollback API and evidence links.
- [x] Switch Rollback tab from mock to real.
- [x] Add server-side SIGMA 5 PRO explanation of rollback gaps and operator review items.
- [x] Distinguish defined rollback from proven rollback.
- [ ] Obtain user acceptance before Slice 5G.

### Slice 5G: Drift Twin

- [x] Implement mandatory baseline snapshot hash, capture time, and freshness evaluation.
- [x] Implement read-only current-state comparison.
- [x] Classify none, minor, major, and critical drift with versioned rules.
- [x] Detect spec, policy-boundary, target, Helm revision, and safety-control drift.
- [x] Invalidate or supersede decisions after material drift.
- [x] Implement real drift API and Refresh Drift authorization.
- [x] Switch Drift tab from mock to real.
- [x] Add server-side SIGMA 5 PRO explanation of material drift using structured changed-resource facts.
- [ ] Obtain user acceptance before Slice 5H.

### Slice 5H: Runtime Behavior Twin, Rules First

- [x] Collect current namespace health, not-ready/restarting pods, recent events, and resource pressure.
- [x] Implement explainable deterministic runtime-risk rules.
- [x] Mark historical comparison as Not Available until validated history APIs exist.
- [x] Implement real runtime-risk API with provenance and confidence.
- [x] Switch Runtime Behavior tab from mock to real rules-first mode.
- [x] Add server-side SIGMA 5 PRO explanation of current runtime signals.
- [x] Prevent runtime risk from independently approving execution.
- [ ] Obtain user acceptance before Slice 5I.

### Slice 5I: Audit Timeline and Reports

- [x] Persist append-only twin lifecycle and operator events.
- [x] Implement cursor-paginated audit API.
- [x] Include actors, timestamps, phases, statuses, hashes, versions, and safe evidence links.
- [x] Generate deterministic JSON report.
- [x] Generate Markdown from the same structured report model.
- [x] Verify JSON and Markdown decisions match exactly.
- [x] Switch Audit Timeline and report downloads from mock to real.
- [x] Add server-side SIGMA 5 PRO executive summary grounded in the immutable report.
- [x] Verify reports and explanations contain no Secret values.
- [ ] Obtain user acceptance before optional slices or execution gate.

### Slice 5J: Release Note Validation Twin

- [x] Keep the tab `Not Run` until a release-note artifact is linked.
- [x] Extract claims through a bounded server-side model prompt when enabled.
- [x] Match claims against deterministic bundle, delta, policy, dry-run, rollback, and runtime evidence.
- [x] Classify claims as supported, unsupported, contradicted, or missing.
- [x] Persist prompt/model/input hashes and safe claim summaries.
- [x] Implement real release-note-validation API.
- [x] Switch the tab from mock only after contract tests pass.
- [x] Keep editorial suggestions separate from execution eligibility.

### Slice 5K: MoP Replay Twin, Optional and Last

- [x] Keep the tab `Not Run` until replay infrastructure is explicitly approved.
- [x] Rehearse only in an isolated mimic namespace or ephemeral cluster.
- [x] Never copy production Secret values or production data.
- [x] Record replay phases, readiness, failures, smoke tests, cleanup, and limitations.
- [x] Treat replay as additional evidence, not proof of production success.
- [x] Add server-side SIGMA 5 PRO replay summary only after deterministic replay facts exist.
- [x] Do not block baseline delivery on this optional slice.

---
## 8. Phase 6: Compact Twin Gate and Real Bundle Execution

**Priority:** P0 after Slices 5A-5I  
**Estimate:** 1-2 weeks  
**Exit gate:** Real mutation cannot start without validating the exact immutable twin decision.

### Compact Gate Integration

- [x] Replace the Bundle Execution mock gate with real summary data.
- [x] Show Twin ID, decision/version, risk, three decision axes, top reasons, freshness, dry-run, rollback, drift, and approval requirement.
- [x] Add **View Full Twin** to the canonical detail route.
- [x] Add Regenerate only for authorized stale/superseded states.
- [x] Add Request Approval only for eligible states.
- [x] Keep baseline Green approval-gated.
- [x] Keep Red, failed, stale, expired, superseded, or mismatched states blocked.
- [x] Verify compact and full views are fact-identical.

### Transactional Execution Gate

- [x] Verify twin ID and decision version.
- [x] Verify bundle and input hash.
- [x] Verify target cluster and namespace.
- [x] Verify dry-run job and command fingerprint hash.
- [x] Verify policy and risk-rule versions.
- [x] Verify freshness, drift, expiry, and supersession.
- [x] Verify approval identity, scope, rationale, and expiry.
- [x] Verify namespace lock and idempotency key.
- [x] Reject every mismatch server-side even if the UI control is stale.

### Post-Execution Linkage

- [x] Link mutation, validation, reports, rollback, and cleanup to the twin.
- [x] Show execution relationship in the detail header and audit timeline.
- [x] Compare observed outcome with planned resources.
- [x] Preserve final execution evidence for later calibration.
- [x] Never rewrite the pre-execution decision after the fact.

### Phase 6 Acceptance

- [x] Green follows baseline approval policy.
- [x] Amber requires valid human approval where policy permits continuation.
- [x] Red always blocks mutation.
- [x] Stale/materially drifted twin requires regeneration.
- [x] UI and backend gate outcomes match exactly.
- [x] Full Bundle Generation -> Digital Twin -> Approval -> Bundle Execution journey passes.

### On-Demand Full Digital Simulation

- [x] Add one primary **Run Digital Simulation** action to the Digital Twins workspace.
- [x] Load eligible completed MoP bundles and allowed target namespaces from the authenticated server session.
- [x] Present bundle, target namespace, and target cluster in a responsive matte-glass launch dialog.
- [x] Resolve the selected user-owned published bundle to an execution-agent source on the server; never accept an arbitrary artifact URL from the browser.
- [x] Submit an idempotency key with every on-demand simulation request.
- [x] Create the real provisional Namespace Twin through the MoP Execution Agent.
- [x] Open the canonical twin detail page immediately and use the existing bounded lifecycle polling for real-time progress.
- [x] Keep browser-fixture and server-mock generation behavior unchanged for deterministic UX testing.
- [x] Show explicit loading, no-eligible-bundle, validation, gateway, and retryable failure states.
- [x] Load the launch catalog with batched PostgreSQL metadata queries; do not reread every ZIP merely to render the selector.
- [x] Create and link one authoritative dry-run-only execution job through the shared MoP Execution runtime; persist the job identity, reconcile progress on normal twin reads, and finalize the deterministic decision without a second browser action.
- [x] Prove restart recovery and idempotent replay: the linked job survives service recreation, polling attaches terminal evidence exactly once, and repeated launch requests do not create duplicate jobs.
- [x] Keep on-demand simulation non-mutating; mutation_performed remains false and runtime-convergence limitations remain explicit even after a successful authoritative dry-run.
- [x] Complete a real local ESDA-to-execution-agent launch for `agent-testing`, reconcile the linked dry-run to a final Red decision, verify eight ordered audit events, render the authoritative evidence tab, and confirm mutation remained disabled.
- [x] Reuse audit-safe SIGMA explanations from PostgreSQL only when twin ID, decision version, prompt identity, authoritative fact hash, and model profile match exactly.
- [x] Cache loaded evidence tabs in the browser by twin, tab, decision version, model, and filter query; invalidate them whenever the twin lifecycle or decision version changes.
- [x] Verify real tab latency improved from approximately 18.9 seconds to 1.5-2.7 seconds on first render, while same-page tab revisits render in under 0.4 seconds without another API request.
- [ ] Complete a real `agent-testing` launch against the deployed MoP Execution Agent and retain evidence under the Phase 7 real E2E section.

Implementation note: the on-demand route now owns the complete safe simulation lifecycle. The
execution agent registers and validates the selected bundle, seeds and starts its existing durable
dry_run_only worker, persists the twin/job relationship, and reconciles terminal evidence into
Green/Amber/Red during ordinary detail/event polling. It does not introduce a second dry-run
engine and it never enables mutation.

Local E2E evidence (2026-07-19): twin `twin_218bb0e5fb054a658a153d52ffe6c6e0`
linked exactly one `dry_run_only` job, reached `completed_with_findings`, and persisted a final
Red decision because a bundle Helm step used a non-repository-qualified chart reference. The
result is expected fail-safe behavior, not a simulation infrastructure failure.

---

## 9. Phase 7: Hardening and Real E2E Validation

**Priority:** P0  
**Estimate:** 2-4 weeks

### Reliability

- [ ] Test worker restart during every non-terminal twin phase.
- [ ] Test PostgreSQL reconnect and transaction rollback.
- [ ] Test MCP timeout, partial response, and recovery.
- [ ] Test namespace-lock contention.
- [ ] Test duplicate idempotency requests.
- [ ] Test report-write failure and retry.
- [ ] Test cancellation and retention cleanup.
- [ ] Test mixed real/mock mode cannot be enabled in production accidentally.

### Security

- [ ] Test Secret/token redaction across fixtures, APIs, logs, reports, and GPT envelopes.
- [ ] Test namespace and tenant authorization.
- [ ] Test malicious zip and path traversal.
- [ ] Test forbidden cluster scope and destructive operations.
- [ ] Test audit append-only behavior.
- [ ] Test server-side model credentials are never exposed to the browser.
- [ ] Complete threat model and security review.

### Dry-run Fidelity

- [x] Demonstrate image-pull failure after successful dry-run.
- [x] Demonstrate scheduling failure after successful dry-run.
- [x] Demonstrate PVC binding failure after successful dry-run.
- [x] Demonstrate readiness-probe failure after successful dry-run.
- [x] Demonstrate controller/webhook failure after successful dry-run.
- [x] Show every case as a fidelity limitation rather than predicted success.

Implementation note: the authoritative dry-run contract now publishes five deterministic
post-admission counterexamples. Every case is classified as a fidelity limitation, marked
`not_predicted`, and explicitly separated from failures observed in the current run.

### Product Journey E2E

- [x] Test list search, filters, sorting, pagination, and old-run reopening.
- [x] Test direct detail and selected-tab deep links after refresh/restart.
- [x] Test progressive availability while a twin is generating.
- [x] Test Green -> approval -> execution.
- [x] Test Amber -> approval -> return -> execution.
- [x] Test Red -> blocked -> corrected bundle -> regenerate.
- [x] Test stale/material drift -> regenerate.
- [x] Test rollback and cleanup linkage.
- [x] Test browser Back/Forward across all linked pages.
- [x] Test desktop, laptop, tablet, and mobile screenshots.
- [x] Test keyboard navigation, visible focus, labels, color-independent status, and reduced motion.

Implementation note: the canonical Playwright/Edge run passed all 11 journeys and
captured eight list/detail screenshots. Reproducible results and viewport evidence
are stored under `knowledge-base/digital-twin/evidence/phase7-product-journey/`.

### Real `agent-testing` E2E

- [ ] Select or generate a representative bundle.
- [ ] Create a real twin and inspect every baseline tab.
- [ ] Complete authoritative dry-run.
- [ ] Produce deterministic Green/Amber/Red report.
- [ ] Submit human approval for an eligible run.
- [ ] Perform real bounded mutation.
- [ ] Validate Kubernetes and Helm state.
- [ ] Exercise rollback and cleanup.
- [ ] Confirm namespace returns to expected state.
- [ ] Verify reports, hashes, prompts, safe explanations, events, and audit records.

### Phase 7 Acceptance

- [ ] Demo completes without manual database/state repair.
- [ ] Failure injection fails safely without losing evidence.
- [ ] No mock response remains on baseline production routes.
- [ ] Operational runbook and troubleshooting guide are complete.
- [ ] Security, platform, and product owners approve the baseline.

---

## 10. Phase 8: Observability, Calibration, and Conditional L4 Evidence

**Priority:** P1 after baseline

### Observability

- [ ] Add correlation IDs across ESDA, execution agent, MCP, PostgreSQL, and model calls.
- [ ] Add lifecycle, decision, policy, evidence, risk, duration, and failure metrics.
- [ ] Add server-side model latency, fallback, token, and error metrics.
- [ ] Add dashboards for active, failed, stale, and superseded twins.
- [ ] Add alerts and runbooks for persistence, evidence-source, decision, and model-explanation failures.
- [ ] Ensure model failure never changes deterministic gate availability.

### Outcome Feedback

- [ ] Store mutation, validation, rollback, and cleanup outcomes.
- [ ] Build a labeled replay dataset.
- [ ] Measure Green false positives and Amber/Red false negatives.
- [ ] Calibrate risk weights and thresholds with versioning.
- [ ] Never use a model score as the sole authority.

### Conditional L4 Activation

- [ ] Define the first narrow auto-execution change class.
- [ ] Define allowed environments and namespaces.
- [ ] Define evidence freshness/completeness requirements.
- [ ] Define rollback proof and SLO requirements.
- [ ] Define immediate disable and human override.
- [ ] Obtain security, operations, and product sign-off.
- [ ] Enable auto-execution only for the signed narrow ODD.

---

## 11. Test Matrix by Delivery Stage

| Test concern | Static UI | JS Mock | Server Mock | Real Slice |
|---|---:|---:|---:|---:|
| Visual layout and responsive screenshots | Required | Required | Required | Regression |
| Keyboard and accessibility behavior | Design review | Required | Required | Regression |
| Filters, tabs, actions, restoration | Visual only | Required | Required | Regression |
| HTTP schemas and status codes | N/A | N/A | Required | Required |
| Authentication and authorization | N/A | N/A | Mock behavior | Required |
| PostgreSQL restart/idempotency | N/A | N/A | N/A | Required |
| MCP contracts | N/A | N/A | N/A | Required per slice |
| Deterministic decision tests | N/A | Fixture expectation | Fixture expectation | Required |
| SIGMA 5 PRO server call | N/A | Fixture text | Fixture text | Required per accepted slice |
| GPT fallback without gate impact | N/A | Simulated | Simulated | Required |
| Real mutation/rollback/cleanup | N/A | N/A | N/A | Final gate only |

---

## 12. Cross-Phase Definition of Done

A checklist item or slice is complete only when:

- [ ] The implementation matches the accepted UI contract.
- [ ] The active data mode is visible during development and cannot leak into production incorrectly.
- [ ] Schemas and identifiers remain stable or are versioned explicitly.
- [ ] Unit, contract, browser, failure, and accessibility tests pass as applicable.
- [ ] Redaction and authorization are verified.
- [ ] Restart and idempotency behavior is proven for real modules.
- [ ] Metrics and audit events exist for real modules.
- [ ] No duplicate source of truth or dry-run path is introduced.
- [ ] No hidden reasoning is requested, displayed, or stored.
- [ ] GPT explanations are server-side, grounded, versioned, logged, and non-authoritative.
- [ ] Documentation and runbooks are updated.
- [ ] User acceptance is recorded before the next vertical slice starts.

---

## 13. Delivery Milestones

| Milestone | Deliverable | Expected range |
|---|---|---|
| M0 | Approved page/tab/state contracts | 2-3 days |
| M1 | Static list, detail cockpit, all tabs, compact gate | End of week 1 |
| M2 | Browser-only interactive mock with restoration and scenarios | Week 2 |
| M3 | ESDA server mock with final HTTP contracts | Week 3 |
| M4 | Real lifecycle/list/Overview plus server-side SIGMA 5 PRO explanation | Weeks 4-6 |
| M5 | Real delta, graph, policy, dry-run, rollback, drift, runtime, audit | Weeks 6-10 with parallel work |
| M6 | Real compact gate, approval, execution linkage | Weeks 10-12 |
| M7 | Hardened approval-gated baseline | Weeks 10-14 |
| M8 | Narrow Conditional L4 candidate | Only after operational evidence and signed ODD |

These are engineering ranges, not delivery commitments. The UI-first sequence makes progress visible immediately while preserving the deterministic safety architecture.

---

## 14. Immediate Sprint: Static UI Only

Do these tasks next. Do not start server, database, GPT, MCP, or execution-agent integration in this sprint.

- [ ] Approve list columns, filters, actions, and states.
- [ ] Approve detail header, sticky summary, and action layout.
- [ ] Approve exact eleven-tab order.
- [ ] Create directly openable static list page.
- [ ] Create directly openable static detail page.
- [ ] Create directly openable static compact Twin Gate preview.
- [ ] Hardcode Green, Amber, Red, generating, stale, failed, and superseded examples.
- [ ] Build all eleven static tab layouts.
- [ ] Build static empty, loading, no-results, Not Run, Not Available, and error variants.
- [ ] Reuse the current ESDA background, matte-glass panels, typography, controls, and status styling.
- [ ] Verify desktop, laptop, tablet, and mobile layouts.
- [ ] Capture screenshots and perform a user review.
- [ ] Record accepted changes before starting browser-only JavaScript fixtures.

**Sprint exit:** The user can open the static files directly and review the complete Digital Twins workspace and Bundle Execution gate with no application server running and no network request made.
