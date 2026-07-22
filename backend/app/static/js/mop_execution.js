const $ = (id) => document.getElementById(id);
const form = $("mop-execution-form");
const statusBadge = $("run-status");
const finalReport = $("final-report");
const artifactLinks = $("artifact-links");
const timeline = $("timeline");
const timelineScroll = $("timeline-scroll");
const copyProgressButton = $("copy-progress");
const copyProgressStatus = $("copy-progress-status");
const copyLogsButton = $("copy-logs");
const formStatus = $("mop-execution-form-status");
const startNewButton = $("mop-execution-start-new");
const bundleSource = $("mop_execution_bundle_source");
const activityRunGroup = $("mop_execution_activity_run_group");
const activityRunSelect = $("mop_execution_activity_run");
const repoFolderGroup = $("mop_execution_repo_folder_group");
const repoFolderInput = $("mop_execution_repo_folder");
const bundleFileGroup = $("mop_execution_bundle_file_group");
const bundleFileInput = $("mop_execution_bundle_file");
const bundleMetadataPanel = $("mop-execution-bundle-metadata");
const twinGatePanel = $("namespace-twin-gate");
const twinGateRequired = twinGatePanel?.dataset.required === "true";
const twinGateTitle = $("namespace-twin-gate-title");
const twinGateDecision = $("namespace-twin-gate-decision");
const twinGateFacts = $("namespace-twin-gate-facts");
const twinGateReasons = $("namespace-twin-gate-reasons");
const twinGateActions = $("namespace-twin-gate-actions");
const targetNamespaceSelect = $("mop_execution_target_namespace");
const correlationInput = $("mop_execution_correlation_id");
const progressPanel = $("mop-progress-panel");
const sphereCanvas = $("mop-sphere-canvas");
const spherePhase = $("mop-sphere-phase");
const sphereTitle = $("mop-sphere-title");
const txToggle = $("transaction-sidebar-toggle");
const txSidebar = $("transaction-sidebar");
const txClose = $("transaction-sidebar-close");
const txBackdrop = $("transaction-sidebar-backdrop");
const txList = $("transaction-list");
const txStatus = $("transaction-sidebar-status");
const txClearAll = $("transaction-clear-all");
const workingPanel = $("ephemeral-working-panel");
const workingStream = $("ephemeral-working-stream");
const safeList = $("safe-summary-list");
const rail = $("agent-activity-rail");
const railGraph = $("agent-activity-graph");
const railStatus = $("agent-activity-status");
const railToggle = $("agent-activity-toggle");
const railPin = $("agent-activity-pin");
const autonomyModal = $("mop-execution-autonomy-modal");
const autonomyMaximize = $("autonomy-maximize");
const autonomyModalLive = $("autonomy-modal-live");
const autonomyModalSummary = $("autonomy-modal-summary");
const autonomyModalJson = $("autonomy-modal-json");
const autonomyModalCopyJson = $("autonomy-modal-copy-json");
const decisionCard = $("decision-required-card");
const decisionStatus = $("decision-card-status");
const decisionReason = $("decision-reason-code");
const decisionPhase = $("decision-phase");
const decisionStep = $("decision-step");
const decisionError = $("decision-redacted-error");
const decisionSafeOptions = $("decision-safe-options");
const decisionAllowedSchema = $("decision-allowed-schema");
const decisionUnsafeExamples = $("decision-unsafe-examples");
const decisionInstructionForm = $("decision-instruction-form");
const decisionAction = $("decision_instruction_action");
const decisionInstruction = $("decision_instruction_text");
const decisionScope = $("decision_instruction_scope");
const decisionRationale = $("decision_instruction_rationale");
const decisionSubmit = $("decision-submit");
const decisionFeedback = $("decision-feedback");
const approvalCard = $("approval-gate-card");
const approvalStatus = $("approval-gate-status");
const approvalSummary = $("approval-report-summary");
const approvalResourcePreview = $("approval-resource-preview");
const approvalPolicyPreview = $("approval-policy-preview");
const approvalFingerprints = $("approval-command-fingerprints");
const approvalForm = $("approval-gate-form");
const approvalRationale = $("approval_rationale");
const approvalScope = $("approval_scope");
const approvalExpiry = $("approval_expiry_minutes");
const approvalSubmit = $("approval-submit");
const mutationStrategy = $("mutation_strategy");
const mutationStart = $("mutation-start");
const cleanupStart = $("cleanup-revert-start");
const cleanupFeedback = $("cleanup-revert-feedback");
const approvalFeedback = $("approval-feedback");
const activeKey = "bosgenesis.mopExecution.activeRunId";
const pinKey = "bosgenesis.mopExecution.activityRailPinned";
const autoHideMs = 30000;
let events = [], activeRunId = null, es = null, lastSeq = 0, pinned = false, autoHideTimer = null, viewGeneration = 0;
let approvalHydrationInFlight = new Set();
let bundleCandidates = [];
let selectedTwinGate = null;
let requestedTwinIdConsumed = false;
let twinGateLoadVersion = 0;
let twinGateAbortController = null;
let twinGenerationPollTimer = 0;
let twinGenerationInFlight = false;
let workingOrder = 0, workingKeys = new Set();
const seen = new Set();
const defs = [
  ["intake", "Intake", "Execution request, bundle source, target namespace, mode, and operator identity."],
  ["preflight", "Preflight", "Local bundle, namespace, ODD, and policy checks."],
  ["agent_health", "Agent Health", "MoP Execution Agent health, readiness, and capabilities."],
  ["bundle_validate", "Bundle Validate", "Agent-side bundle validation and bundle ID creation."],
  ["dry_run_job", "Dry-run Job", "Dry-run execution job creation with idempotency key."],
  ["dry_run", "Dry-run", "Helm and Kubernetes dry-run operations."],
  ["observations", "Observations", "Job state, observations, audit events, and policy decisions."],
  ["decision", "Decision Gate", "Decision-required context and scoped instruction handling."],
  ["dry_run_report", "Dry-run Report", "Dry-run report metadata and downloads."],
  ["approval", "Approval Gate", "Human approval submission and acceptance."],
  ["mutation_job", "Mutation Job", "Approved mutation job creation or continuation."],
  ["mutation", "Mutation", "Approval-gated Helm and Kubernetes mutation."],
  ["validation", "Validation", "Post-mutation validation and rollout checks."],
  ["reports", "Reports", "Execution, validation, rollback, cleanup, and evidence reports."],
  ["rollback_cleanup", "Rollback/Cleanup", "Optional rollback, cleanup, or revert work."],
  ["complete", "Complete", "Final state, report links, and Activity visibility."],
];
let activity = Object.fromEntries(defs.map(([id, label, hint]) => [id, {status: "pending", label, detail: hint}]));
const stageNumbers = Object.fromEntries(defs.map(([id], index) => [id, index + 1]));
const stageLabels = Object.fromEntries(defs.map(([id, label]) => [id, label]));
function valueOf(id) { const v = $(id)?.value?.trim() || ""; return v || null; }
function approvedMutationMode() { return valueOf("mop_execution_mode") === "approved_mutation"; }
function setText(el, text) { if (el) el.textContent = text || ""; }
function requestErrorMessage(error, phaseLabel = "request") {
  const raw = error?.message || String(error || "Unknown error");
  if (/NetworkError|Failed to fetch|Load failed|attempting to fetch resource/i.test(raw)) {
    return `${phaseLabel}: ESDA local API connection was lost. Confirm the local web app is running on http://127.0.0.1:8080, then click Start New and retry. Browser reported: ${raw}`;
  }
  return raw;
}
function renderPhaseRequestFailure(phase, detail) {
  const stage = phase?.stage || "complete";
  const label = phase?.label || "MoP Execution request";
  const summary = `${label} failed.`;
  addTimeline({event_type: `${stage}_request_failed`, message: summary, sequence: events.length + 1, payload: {phase: label, error: detail}});
  mark(stage, "failed", detail);
  mark("complete", "failed", summary);
  if (finalReport) {
    finalReport.textContent = [
      `# ${summary}`,
      "",
      `- Phase: ${label}`,
      `- Target namespace: ${valueOf("mop_execution_target_namespace") || "agent-testing"}`,
      `- Correlation ID: ${valueOf("mop_execution_correlation_id") || "not assigned"}`,
      "",
      "## Error",
      detail,
    ].join("\n");
  }
  if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-danger">Request failed</span>';
  setStatus("failed");
  setVisual("failed");
  setText(formStatus, detail);
  renderActivity();
  renderSafeFromEvents(true);
}
const terminalStatuses = new Set(["completed", "completed_with_review", "cleanup_completed", "cleanup_needs_review", "failed", "validation_failed", "cleanup_failed", "cancelled", "stopped"]);
const failedStatuses = new Set(["failed", "validation_failed", "cleanup_failed", "cancelled", "stopped"]);
const reviewStatuses = new Set(["completed_with_review", "cleanup_needs_review"]);
const workingStatuses = new Set(["created", "planning", "running", "waiting_for_approval", "approved_for_mutation", "mutation_paused", "rollback_required", "mutation_unknown", "cleanup_running"]);
function normalizedStatus(s) { return String(s || "").trim().toLowerCase(); }
function terminal(s) { return terminalStatuses.has(normalizedStatus(s)); }
function visual(s) {
  const value = normalizedStatus(s);
  if (["completed", "completed_with_review", "mutation_succeeded", "cleanup_completed"].includes(value)) return "complete";
  if (failedStatuses.has(value)) return "failed";
  return workingStatuses.has(value) ? "working" : "idle";
}
function setStatus(s) {
  if (!statusBadge) return;
  const value = s || "Idle";
  const normalized = normalizedStatus(value);
  statusBadge.textContent = value;
  statusBadge.className = "badge mb-2 align-self-start";
  const badgeClass = ["completed", "mutation_succeeded", "cleanup_completed"].includes(normalized)
    ? "text-bg-success"
    : reviewStatuses.has(normalized)
      ? "text-bg-warning"
      : failedStatuses.has(normalized)
        ? "text-bg-danger"
        : workingStatuses.has(normalized)
          ? "text-bg-primary"
          : "text-bg-secondary";
  statusBadge.classList.add(badgeClass);
  updateCleanupButton(value);
}
function applyRunStatus(statusValue) {
  const normalized = normalizedStatus(statusValue);
  setStatus(statusValue);
  setVisual(visual(statusValue));
  if (normalized === "cleanup_completed") {
    hideApprovalCard();
    hideDecisionCard();
    setText(spherePhase, "Cleanup/revert completed");
    setText(sphereTitle, "Target namespace is empty and ready for retest.");
    setCleanupFeedback("Cleanup/revert completed. agent-testing is ready for a fresh demo run.");
    if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-success">Namespace cleanup completed</span>';
  } else if (normalized === "cleanup_running") {
    setText(spherePhase, "Cleanup/revert running");
    setText(sphereTitle, "Waiting for namespace-empty confirmation.");
  } else if (normalized === "cleanup_needs_review") {
    setText(spherePhase, "Cleanup needs review");
    setText(sphereTitle, "Verify the target namespace before retrying.");
  }
}
function setVisual(state) {
  progressPanel?.classList.remove("is-idle", "is-working", "is-complete", "is-failed");
  progressPanel?.classList.add(`is-${state}`);
  const copy = {
    idle: ["Ready for execution planning", ""],
    working: ["Thinking through execution evidence", "MoP execution planning in progress."],
    complete: ["Execution report ready", "Review the generated execution evidence."],
    failed: ["Run needs review", "MoP execution stopped before completion."],
  }[state] || ["Ready for execution planning", ""];
  setText(spherePhase, copy[0]); setText(sphereTitle, copy[1]);
  setTimeout(() => window.mopSphereRuntime?.resize?.(), 80);
}
function scrub(v) { if (Array.isArray(v)) return v.map(scrub); if (!v || typeof v !== "object") return v; const o = {}; Object.entries(v).forEach(([k, c]) => { o[k] = /secret|password|token|key|credential|archive_base64|content_base64|bundle_base64/i.test(k) ? "***" : scrub(c); }); return o; }
function displayPayload(e) { return scrub(JSON.parse(JSON.stringify(e?.payload || {}))); }
function findDeep(value, key) {
  if (!value || typeof value !== "object") return null;
  if (Object.prototype.hasOwnProperty.call(value, key)) return value[key];
  for (const child of Object.values(value)) {
    const found = findDeep(child, key);
    if (found !== null && found !== undefined && found !== "") return found;
  }
  return null;
}
function extractDecisionResponse(context) {
  if (!context || typeof context !== "object") return {};
  if (context.response && typeof context.response === "object") return context.response;
  if (context.agent_response?.response && typeof context.agent_response.response === "object") return context.agent_response.response;
  if (context.decision_required && typeof context.decision_required === "object") return extractDecisionResponse(context.decision_required);
  return context;
}
function decisionScopeDefault(card, result = {}) {
  const target = result.target_namespace || valueOf("mop_execution_target_namespace") || "agent-testing";
  const scope = {phase: card.phase || result.current_phase || "dry_run", target_namespace: target};
  if (card.reason_code) scope.reason_code = card.reason_code;
  if (card.step && card.step !== "not_specified") scope.step_id = card.step;
  return scope;
}
function setDecisionFeedback(text, isError = false) {
  if (!decisionFeedback) return;
  decisionFeedback.textContent = text || "";
  decisionFeedback.classList.toggle("text-danger", Boolean(isError));
}
function hideDecisionCard() {
  decisionCard?.classList.add("d-none");
  if (decisionCard) { decisionCard.dataset.runId = ""; decisionCard.dataset.jobId = ""; }
  setDecisionFeedback("");
}
function setCleanupFeedback(text, isError = false) {
  if (!cleanupFeedback) return;
  cleanupFeedback.textContent = text || "";
  cleanupFeedback.classList.toggle("text-danger", Boolean(isError));
}
function cleanupReadyStatus(value) { return ["mutation_succeeded", "completed", "completed_with_review", "validation_failed", "cleanup_failed", "cleanup_needs_review"].includes(normalizedStatus(value)); }
function updateCleanupButton(statusValue) {
  if (!cleanupStart) return;
  cleanupStart.disabled = !activeRunId || !cleanupReadyStatus(statusValue || statusBadge?.textContent);
}
function setApprovalFeedback(text, isError = false) {
  if (!approvalFeedback) return;
  approvalFeedback.textContent = text || "";
  approvalFeedback.classList.toggle("text-danger", Boolean(isError));
}
function hideApprovalCard() {
  approvalCard?.classList.add("d-none");
  progressPanel?.classList.remove("is-approval-ready");
  if (approvalCard) { approvalCard.dataset.runId = ""; approvalCard.dataset.jobId = ""; approvalCard.dataset.commandFingerprints = "[]"; }
  if (approvalSummary) approvalSummary.textContent = "";
  if (approvalResourcePreview) approvalResourcePreview.textContent = "[]";
  if (approvalPolicyPreview) approvalPolicyPreview.textContent = "{}";
  if (approvalFingerprints) approvalFingerprints.textContent = "[]";
  if (approvalRationale) approvalRationale.value = "";
  if (approvalScope) approvalScope.value = "";
  setText(approvalStatus, "");
  setApprovalFeedback("");
}
function reportDownloads(reports = {}) {
  const rows = [];
  (reports.reports || []).forEach((report) => (report.downloads || []).forEach((download) => rows.push({...download, report_title: report.title || report.report_id || "Dry-run report"})));
  return rows;
}
const approvalReadyStates = new Set(["dry_run_succeeded", "waiting_for_approval", "ready_for_approval", "approval_required", "reports_available"]);
function isApprovalReadyState(value) { return approvalReadyStates.has(String(value || "").toLowerCase()); }
function latestEventValue(key) {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const found = findDeep(events[i]?.payload || {}, key);
    if (found !== null && found !== undefined && found !== "") return found;
  }
  return null;
}
function latestDryRunReports() {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const payload = events[i]?.payload || {};
    const reports = payload.reports || payload.result?.reports;
    if (reports && typeof reports === "object" && reports.phase !== "post_mutation") return reports;
  }
  return {};
}
function approvalCandidateFromEvents(runId, state = "waiting_for_approval") {
  const reports = latestDryRunReports();
  const gate = latestEventValue("approval_gate") || {};
  const dryRunJobId = latestEventValue("dry_run_job_id") || gate.dry_run_job_id || reports.job_id || "";
  const target = latestEventValue("target_namespace") || valueOf("mop_execution_target_namespace") || "agent-testing";
  return {
    valid: true,
    status: "waiting_for_approval",
    run_id: runId || activeRunId,
    dry_run_job_id: dryRunJobId,
    target_namespace: target,
    current_state: state,
    dry_run_succeeded: true,
    mutation_controls_enabled: false,
    approval_required: true,
    approval_gate: {
      required: true,
      status: "waiting_for_human_approval",
      target_namespace: target,
      dry_run_job_id: dryRunJobId,
      command_fingerprints: gate.command_fingerprints || reports.command_fingerprints || [],
    },
    reports,
    summary: latestEventValue("summary") || "Dry-run succeeded. Review the report evidence and submit human approval before mutation.",
  };
}
function staleRun(runId, generation) {
  return generation !== viewGeneration || !runId || runId !== activeRunId;
}
async function ensureApprovalGateForRun(runId, options = {}) {
  if (!runId || !approvalCard) return;
  const generation = viewGeneration;
  if (staleRun(runId, generation)) return;
  const state = options.state || latestEventValue("current_state") || statusBadge?.textContent || "waiting_for_approval";
  if (!options.force && !isApprovalReadyState(state)) return;
  const cached = approvalCandidateFromEvents(runId, state);
  if (!staleRun(runId, generation) && (cached.dry_run_job_id || Object.keys(cached.reports || {}).length)) {
    renderReportLinks(cached.reports || {});
    renderApprovalCard(cached);
    mark("approval", "running", "Dry-run succeeded; waiting for human approval before mutation.");
    renderActivity();
  }
  if (approvalHydrationInFlight.has(runId)) return;
  approvalHydrationInFlight.add(runId);
  try {
    const response = await fetch(`/api/mop-execution/dry-run-report?run_id=${encodeURIComponent(runId)}`);
    if (staleRun(runId, generation)) return;
    if (!response.ok) {
      if (response.status === 409 && !approvalCard.classList.contains("d-none")) return;
      throw new Error(`HTTP ${response.status}`);
    }
    const result = await response.json();
    if (staleRun(runId, generation)) return;
    (result.events || []).forEach((event) => processEvent(event, {live: false, reveal: false}));
    if (staleRun(runId, generation)) return;
    renderReportLinks(result.reports || {});
    renderApprovalCard(result);
    mark("dry_run_report", "success", result.summary || "Dry-run report metadata is available for approval.");
    mark("approval", "running", "Dry-run evidence is ready for human approval.");
    setText(formStatus, "Dry-run succeeded. Submit approval from the Approval Gate card before starting mutation.");
    renderActivity();
    renderSafeFromEvents(true);
  } catch (error) {
    if (!staleRun(runId, generation)) setApprovalFeedback(`Approval gate is ready, but dry-run report refresh failed: ${error.message}`, true);
  } finally {
    approvalHydrationInFlight.delete(runId);
  }
}
function renderReportLinks(reports = {}, fallbackLabel = "Dry-run report") {
  if (!artifactLinks) return;
  const downloads = reportDownloads(reports);
  const label = reports.badge_label || (reports.phase === "post_mutation" ? "Execution reports ready" : "Dry-run report ready");
  const badges = [`<span class="badge text-bg-success">${escapeHtml(label)}</span>`];
  if (Array.isArray(reports.command_fingerprints) && reports.command_fingerprints.length) badges.push(`<span class="badge text-bg-secondary">${reports.command_fingerprints.length} fingerprint${reports.command_fingerprints.length === 1 ? "" : "s"}</span>`);
  if (reports.phase === "post_mutation" && Array.isArray(reports.validation_matrix)) badges.push(`<span class="badge text-bg-secondary">${reports.validation_matrix.length} validation row${reports.validation_matrix.length === 1 ? "" : "s"}</span>`);
  const links = downloads.map((download) => `<a class="btn btn-sm btn-outline-light" href="${escapeHtml(download.url)}" target="_blank" rel="noopener">Download ${escapeHtml(download.label || download.artifact || fallbackLabel)}</a>`);
  artifactLinks.innerHTML = [...badges, ...links].join(" ");
}
function showApprovalShortcut() {
  if (!artifactLinks || artifactLinks.querySelector("[data-approval-shortcut]")) return;
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.approvalShortcut = "true";
  button.className = "btn btn-sm btn-primary ms-1";
  button.textContent = "Review Approval Gate";
  button.addEventListener("click", () => {
    approvalCard?.scrollIntoView({behavior: "smooth", block: "center"});
    setTimeout(() => approvalRationale?.focus?.(), 350);
  });
  artifactLinks.append(" ", button);
}
function promoteApprovalCard() {
  const autonomyShell = document.querySelector(".autonomy-stream-shell");
  if (approvalCard && autonomyShell && approvalCard.nextElementSibling !== autonomyShell) {
    autonomyShell.parentNode?.insertBefore(approvalCard, autonomyShell);
  }
  progressPanel?.classList.add("is-approval-ready");
}

function approvalScopeDefault(result = {}) {
  const reports = result.reports || result.payload?.reports || {};
  const gate = result.approval_gate || {};
  const target = result.target_namespace || gate.target_namespace || valueOf("mop_execution_target_namespace") || "agent-testing";
  const dryRunJobId = result.dry_run_job_id || gate.dry_run_job_id || reports.job_id || result.job_id || "";
  return {
    phase: "dry_run",
    target_namespace: target,
    dry_run_job_id: dryRunJobId,
    approval_scope: "dry_run_evidence_to_mutation_gate",
  };
}
function renderApprovalCard(result = {}) {
  if (!approvalCard) return;
  setStatus("waiting_for_approval");
  setVisual("working");
  setText(spherePhase, "Dry-run completed");
  setText(sphereTitle, "Approval is required before mutation.");
  const reports = result.reports || result.payload?.reports || {};
  const gate = result.approval_gate || {};
  const fingerprints = gate.command_fingerprints || reports.command_fingerprints || [];
  const dryRunJobId = result.dry_run_job_id || gate.dry_run_job_id || reports.job_id || result.job_id || "";
  approvalCard.dataset.runId = result.run_id || activeRunId || "";
  approvalCard.dataset.jobId = dryRunJobId;
  approvalCard.dataset.commandFingerprints = JSON.stringify(fingerprints || []);
  setText(approvalStatus, gate.status || "waiting for approval");
  setText(approvalSummary, reports.summary || result.summary || "Dry-run succeeded. Review report evidence before approval.");
  if (approvalResourcePreview) approvalResourcePreview.textContent = JSON.stringify(scrub(reports.resources || []), null, 2);
  if (approvalPolicyPreview) approvalPolicyPreview.textContent = JSON.stringify(scrub({policy_gates: reports.policy_gates || [], warnings: reports.warnings || []}), null, 2);
  if (approvalFingerprints) approvalFingerprints.textContent = JSON.stringify(fingerprints || [], null, 2);
  if (approvalScope) approvalScope.value = JSON.stringify(approvalScopeDefault(result), null, 2);
  if (approvalRationale && !approvalRationale.value.trim()) approvalRationale.value = valueOf("mop_execution_rationale") || "";
  if (approvalSubmit) approvalSubmit.disabled = false;
  promoteApprovalCard();
  approvalCard.classList.remove("d-none");
  showApprovalShortcut();
  setApprovalFeedback("Submit approval only after reviewing the dry-run report and fingerprints.");
}
function validateApprovalPayload(payload) {
  const errors = [];
  const rationale = payload.rationale || "";
  const scope = payload.scope || {};
  if (rationale.trim().length < 10) errors.push("Approval rationale must be at least 10 characters.");
  if (!Object.keys(scope).length) errors.push("Approval scope JSON must not be empty.");
  const scopedNamespace = scope.target_namespace || scope.namespace || valueOf("mop_execution_target_namespace");
  if (scopedNamespace !== valueOf("mop_execution_target_namespace")) errors.push("Approval scope namespace must match the selected target namespace.");
  if (String(scopedNamespace || "").includes("*") || ["all", "all-namespaces", "cluster"].includes(String(scopedNamespace || "").toLowerCase())) errors.push("Approval scope cannot be wildcarded or cluster-wide.");
  const expected = JSON.parse(approvalCard?.dataset.commandFingerprints || "[]");
  if (expected.length && JSON.stringify([...payload.command_fingerprints].sort()) !== JSON.stringify([...expected].sort())) errors.push("Approval command fingerprints must match the dry-run report fingerprints.");
  return errors;
}
function approvalErrorSummary(result = {}) {
  const agentError = result.agent_error || result.approval_response?.error || result.approval_response?.agent_response?.error;
  const pieces = [];
  if (agentError?.status_code) pieces.push(`MoP Execution Agent HTTP ${agentError.status_code}`);
  if (agentError?.message) pieces.push(agentError.message);
  if (agentError?.payload?.rest_status_code && agentError?.payload?.mcp_status_code) pieces.push(`REST ${agentError.payload.rest_status_code}, MCP ${agentError.payload.mcp_status_code}: redeploy or repair bosgenesis-mop-execution-agent approval handling.`);
  const payloadText = agentError?.payload?.text || agentError?.payload?.message || agentError?.payload?.detail;
  if (payloadText) pieces.push(payloadText);
  if (Array.isArray(result.errors)) pieces.push(...result.errors);
  if (!pieces.length && result.summary) pieces.push(result.summary);
  return [...new Set(pieces.filter(Boolean))].join(" | ") || "Approval was not accepted by the MoP Execution Agent.";
}
function buildApprovalPayload() {
  let scope = {};
  try {
    scope = approvalScope?.value?.trim() ? JSON.parse(approvalScope.value) : {};
  } catch (_error) {
    return {payload: null, errors: ["Approval scope JSON is not valid."]};
  }
  const fingerprints = JSON.parse(approvalCard?.dataset.commandFingerprints || "[]");
  const payload = {
    run_id: approvalCard?.dataset.runId || activeRunId,
    job_id: approvalCard?.dataset.jobId || null,
    rationale: approvalRationale?.value?.trim() || valueOf("mop_execution_rationale") || "",
    scope,
    expires_minutes: Number(approvalExpiry?.value || 60),
    command_fingerprints: fingerprints,
    correlation_id: valueOf("mop_execution_correlation_id"),
    model_profile: valueOf("model_profile"),
    ...twinGateRequestFields(),
  };
  const errors = validateApprovalPayload(payload);
  if (!payload.run_id) errors.push("No active MoP Execution run is selected.");
  return {payload, errors};
}
async function submitApprovalPayload(statusText = "Submitting approval to MoP Execution Agent...") {
  if (!approvalCard) return;
  const {payload, errors} = buildApprovalPayload();
  if (errors.length || !payload) {
    setApprovalFeedback(errors.join(" "), true);
    return;
  }
  if (approvalSubmit) approvalSubmit.disabled = true;
  if (mutationStart) mutationStart.disabled = true;
  setApprovalFeedback(statusText);
  try {
    const response = await fetch("/api/mop-execution/approval", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const result = await response.json();
    renderApprovalResult(result);
  } catch (error) {
    setApprovalFeedback(`Approval failed: ${error.message}`, true);
    if (approvalSubmit) approvalSubmit.disabled = false;
  }
}
async function submitApproval(event) {
  event?.preventDefault?.();
  await submitApprovalPayload();
}
async function submitApprovedMutationApproval(result = {}) {
  renderApprovalCard(result);
  if (approvalRationale && !approvalRationale.value.trim()) approvalRationale.value = valueOf("mop_execution_rationale") || "";
  const rationale = approvalRationale?.value?.trim() || "";
  if (rationale.length < 10) {
    setApprovalFeedback("Approved mutation requires an approval rationale before ESDA can submit the gate automatically.", true);
    setText(formStatus, "Approved mutation paused: enter an approval rationale, then submit approval.");
    mark("approval", "running", "Waiting for the operator approval rationale required by approved mutation mode.");
    renderActivity();
    approvalCard?.scrollIntoView({behavior: "smooth", block: "center"});
    if (approvalSubmit) approvalSubmit.disabled = false;
    return;
  }
  setStatus("running");
  setVisual("working");
  setText(spherePhase, "Dry-run completed");
  setText(sphereTitle, "Submitting approved mutation gate.");
  mark("approval", "running", "Approved mutation mode selected; ESDA is submitting the operator approval after the successful dry-run.");
  addStageWorking("approval", "Submitting approved mutation gate", "Using the operator rationale and dry-run evidence to request execution-agent approval before mutation starts.", `approved-mutation:${result.run_id || activeRunId}:${result.dry_run_job_id || "job"}`);
  renderActivity();
  await submitApprovalPayload("Approved mutation mode: submitting approval to MoP Execution Agent...");
}
function renderApprovalResult(result = {}) {
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const lines = [
    `# MoP Execution Approval: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || activeRunId || "not available"}`,
    `- Dry-run job ID: ${result.dry_run_job_id || approvalCard?.dataset.jobId || "not available"}`,
    `- Accepted: ${result.accepted ? "yes" : "no"}`,
    `- Mutation controls enabled: ${result.mutation_controls_enabled ? "yes" : "no"}`,
    "",
    "## Summary",
    result.summary || (result.errors || []).join("\n") || "No approval summary returned.",
    "",
    "## Agent Response",
    JSON.stringify(scrub({approval: result.approval, approval_response: result.approval_response, agent_error: result.agent_error, errors: result.errors}), null, 2),
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  if (result.accepted) {
    if (approvedMutationMode() && mutationStrategy) mutationStrategy.value = "continue_existing";
    const autoStart = approvedMutationMode() || mutationStrategy?.value === "continue_existing";
    setApprovalFeedback(autoStart ? "Approval accepted. Starting approved mutation..." : (result.summary || "Approval accepted by the execution agent."));
    setText(approvalStatus, "approved");
    setStatus("approved_for_mutation");
    mark("approval", "success", result.summary || "Human approval accepted for the dry-run scope.");
    mark("mutation_job", autoStart ? "running" : "pending", autoStart ? "Starting the approved mutation path." : "Ready to create an approved mutation job through the execution agent.");
    if (mutationStart) mutationStart.disabled = false;
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-success">Approval accepted</span>';
    if (autoStart) window.setTimeout(() => { void startMutation(); }, 50);
  } else {
    const rejection = approvalErrorSummary(result);
    setApprovalFeedback(rejection, true);
    setStatus("waiting_for_approval");
    mark("approval", "recovered", rejection);
    addStageWorking("approval", "Approval was not accepted", rejection, `approval:${result.run_id || activeRunId}:${result.status || "failed"}:${result.dry_run_job_id || "job"}`);
    if (approvalSubmit) approvalSubmit.disabled = false;
  }
  renderActivity();
  renderSafeFromEvents(true);
  refreshOpenAutonomyModal();
  void loadTransactions();
}
async function startMutation() {
  const runId = approvalCard?.dataset.runId || activeRunId;
  if (!runId) {
    setApprovalFeedback("No approved MoP Execution run is selected.", true);
    return;
  }
  if (mutationStart) mutationStart.disabled = true;
  setApprovalFeedback("Starting approved mutation through MoP Execution Agent...");
  mark("mutation_job", "running", "Creating or continuing the approved mutation job.");
  addStageWorking("mutation_job", "Starting approved mutation", "The execution agent is receiving the accepted approval evidence, dry-run job ID, and command fingerprints. ESDA will not retry blindly.", "phase-j:mutation-start");
  renderActivity();
  try {
    const payload = {
      run_id: runId,
      strategy: approvedMutationMode() ? "continue_existing" : (mutationStrategy?.value || "continue_existing"),
      correlation_id: valueOf("mop_execution_correlation_id"),
      model_profile: valueOf("model_profile"),
      ...twinGateRequestFields(),
    };
    const response = await fetch("/api/mop-execution/mutation", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const result = await response.json();
    renderMutationResult(result);
  } catch (error) {
    setApprovalFeedback(`Mutation start failed: ${error.message}`, true);
    if (mutationStart) mutationStart.disabled = false;
  }
}
function renderMutationResult(result = {}) {
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const observationPayload = result.observations && Object.keys(result.observations).length ? JSON.stringify(scrub(result.observations), null, 2) : "No mutation observations returned yet.";
  const decisionPayload = result.decision_required ? JSON.stringify(scrub(result.decision_required), null, 2) : "No decision-required context.";
  const lines = [
    `# MoP Execution Mutation: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || activeRunId || "not available"}`,
    `- Dry-run job ID: ${result.dry_run_job_id || "not available"}`,
    `- Mutation job ID: ${result.mutation_job_id || "not available"}`,
    `- Target namespace: ${result.target_namespace || valueOf("mop_execution_target_namespace")}`,
    `- Current state: ${result.current_state || result.status || "unknown"}`,
    `- Current phase: ${result.current_phase || "mutation"}`,
    `- Mutation succeeded: ${result.mutation_succeeded ? "yes" : "no"}`,
    `- Rollback required: ${result.rollback_required ? "yes" : "no"}`,
    "",
    "## Summary",
    result.summary || (result.errors || []).join("\n") || "No mutation summary returned.",
    "",
    "## Mutation Observations and Audit Events",
    observationPayload,
    "",
    "## Decision Context",
    decisionPayload,
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  if (result.mutation_succeeded) {
    setStatus("mutation_succeeded");
    setVisual("complete");
    setText(spherePhase, "Mutation completed");
    setText(sphereTitle, "Post-mutation validation is next.");
    hideApprovalCard();
    setApprovalFeedback(result.summary || "Approved mutation completed. Validation is next.");
    mark("approval", "success", "Human approval was accepted before mutation.");
    mark("mutation_job", "success", `Mutation job ${result.mutation_job_id || "created"} completed.`);
    mark("mutation", "success", result.summary || "Approved mutation completed through execution agent.");
    mark("validation", "running", "Collecting post-mutation validation observations from the execution agent.");
    addStageWorking("validation", "Collecting post-mutation validation", "Retrieving Helm status/history, Kubernetes readiness, validation matrix, and report metadata from the MoP Execution Agent.", "phase-k:validation-start");
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-success">Mutation completed</span> <span class="badge text-bg-primary">Validation running</span>';
    void runPostMutationValidation(result.run_id || activeRunId).then(renderValidationReportResult).catch((error) => {
      mark("validation", "failed", `Post-mutation validation failed: ${error.message}`);
      mark("reports", "failed", "Execution report collection failed.");
      setStatus("validation_failed");
      setVisual("failed");
      setApprovalFeedback(`Post-mutation validation failed: ${error.message}`, true);
      if (finalReport) finalReport.textContent += `\n\n## Phase K Error\n${error.message}`;
      renderActivity();
    });
  } else if (["decision_required", "paused"].includes(String(result.current_state || result.status || "").toLowerCase())) {
    setStatus("mutation_paused");
    setVisual("working");
    setText(spherePhase, "Mutation paused");
    setText(sphereTitle, "Review the bounded decision context.");
    mark("mutation", "recovered", result.summary || "Mutation paused for decision-required handling.");
    mark("decision", "running", "Execution agent requires a bounded operator instruction.");
    renderDecisionCard(result);
    setApprovalFeedback(result.summary || "Mutation paused for decision-required context.");
  } else if (result.rollback_required) {
    setStatus("rollback_required");
    setVisual("failed");
    setText(spherePhase, "Rollback required");
    setText(sphereTitle, "Mutation outcome needs operator review.");
    mark("mutation", "recovered", result.summary || "Mutation ended in an ambiguous or rollback-required state.");
    mark("rollback_cleanup", "running", "Rollback/cleanup handling is required; ESDA did not retry mutation.");
    setApprovalFeedback(result.summary || "Rollback-required state surfaced. No automatic retry was attempted.", true);
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-warning">Rollback required</span>';
  } else {
    setStatus("failed");
    setVisual("failed");
    mark("mutation", "failed", result.summary || "Approved mutation failed.");
    mark("complete", "failed", result.summary || "Mutation stopped before validation.");
    setApprovalFeedback(result.summary || "Approved mutation failed. No automatic retry was attempted.", true);
    if (mutationStart && result.status === "mutation_blocked_by_approval") mutationStart.disabled = false;
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-danger">Mutation failed</span>';
  }
  renderActivity();
  renderSafeFromEvents(true);
  refreshOpenAutonomyModal();
  void loadTransactions();
}
async function runCleanupRevert() {
  const runId = activeRunId;
  if (!runId) {
    setStatus("Idle");
    setVisual("idle");
    setCleanupFeedback("No Bundle Execution run is selected. Choose Approved mutation to start a fresh run.", true);
    setText(formStatus, "Cleanup/revert needs an existing execution run. No request was sent.");
    return;
  }
  const rationale = approvalRationale?.value?.trim() || valueOf("mop_execution_rationale") || "";
  if (rationale.length < 10) {
    setCleanupFeedback("Enter a cleanup approval rationale first.", true);
    approvalRationale?.focus?.();
    return;
  }
  if (cleanupStart) cleanupStart.disabled = true;
  setCleanupFeedback("Submitting cleanup/revert request to the MoP Execution Agent...");
  setStatus("running");
  setVisual("working");
  setText(spherePhase, "Cleanup/revert requested");
  setText(sphereTitle, "Reverting generated demo resources.");
  mark("rollback_cleanup", "running", "Submitting cleanup/revert request through the MoP Execution Agent.");
  addStageWorking("rollback_cleanup", "Submitting cleanup/revert", "The execution agent will delete generated resources inside the selected target namespace according to the approved cleanup scope.", `cleanup:${runId}`);
  renderActivity();
  revealRail("Cleanup/revert request submitted.");
  try {
    const payload = {
      run_id: runId,
      target_namespace: valueOf("mop_execution_target_namespace"),
      rationale,
      cleanup_scope: "namespace_empty_state",
      correlation_id: valueOf("mop_execution_correlation_id"),
      model_profile: valueOf("model_profile"),
    };
    const response = await fetch("/api/mop-execution/cleanup", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const result = await response.json();
    renderCleanupResult(result);
  } catch (error) {
    setCleanupFeedback(`Cleanup/revert failed: ${error.message}`, true);
    mark("rollback_cleanup", "failed", `Cleanup/revert request failed: ${error.message}`);
    setStatus("cleanup_failed");
    setVisual("failed");
    if (cleanupStart) cleanupStart.disabled = false;
    renderActivity();
  }
}
function renderCleanupResult(result = {}) {
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const lines = [
    `# Bundle Execution Cleanup/Revert: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || activeRunId || "not available"}`,
    `- Job ID: ${result.job_id || "not available"}`,
    `- Target namespace: ${result.target_namespace || valueOf("mop_execution_target_namespace")}`,
    `- Cleanup scope: ${result.cleanup_scope || "namespace_empty_state"}`,
    `- Completed: ${result.valid ? "yes" : "no"}`,
    `- Namespace ready for retest: ${result.status === "cleanup_completed" && result.valid ? "yes" : "not confirmed"}`,
    "",
    "## Summary",
    result.summary || (result.errors || []).join("\n") || "No cleanup summary returned.",
    "",
    "## Agent Response",
    JSON.stringify(scrub({cleanup: result.cleanup, cleanup_response: result.cleanup_response, agent_error: result.agent_error, errors: result.errors}), null, 2),
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  if (result.status === "cleanup_completed" && result.valid) {
    hideDecisionCard();
    hideApprovalCard();
    setStatus("cleanup_completed");
    setVisual("complete");
    setText(spherePhase, "Cleanup/revert completed");
    setText(sphereTitle, "Target namespace is ready for retest.");
    mark("rollback_cleanup", "success", result.summary || "Cleanup/revert completed through the execution agent.");
    mark("complete", "success", "Bundle execution cleanup/revert completed.");
    setCleanupFeedback(result.summary || "Cleanup/revert completed. agent-testing is ready for a fresh demo run.");
    if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-success">Namespace cleanup completed</span>';
  } else if (result.status === "cleanup_running") {
    setStatus("cleanup_running");
    setVisual("working");
    setText(spherePhase, "Cleanup/revert running");
    setText(sphereTitle, "Waiting for namespace-empty confirmation.");
    mark("rollback_cleanup", "running", result.summary || "Cleanup/revert accepted but not terminal yet.");
    mark("complete", "pending", "Waiting for cleanup terminal state.");
    setCleanupFeedback(result.summary || "Cleanup/revert is still running.");
    if (cleanupStart) cleanupStart.disabled = true;
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-primary">Cleanup running</span>';
  } else if (result.status === "cleanup_needs_review") {
    setStatus("cleanup_needs_review");
    setVisual("failed");
    setText(spherePhase, "Cleanup needs review");
    setText(sphereTitle, "Verify the target namespace before retrying.");
    mark("rollback_cleanup", "recovered", result.summary || "Cleanup/revert needs review.");
    mark("complete", "recovered", "Cleanup/revert did not reach a verified terminal state.");
    setCleanupFeedback(result.summary || "Cleanup/revert needs manual review.", true);
    if (cleanupStart) cleanupStart.disabled = false;
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-warning">Cleanup needs review</span>';
  } else {
    setStatus("cleanup_failed");
    setVisual("failed");
    mark("rollback_cleanup", "failed", result.summary || "Cleanup/revert failed.");
    mark("complete", "failed", "Bundle execution cleanup/revert failed.");
    setCleanupFeedback(result.summary || (result.errors || []).join(" ") || "Cleanup/revert failed.", true);
    if (cleanupStart) cleanupStart.disabled = false;
    if (artifactLinks) artifactLinks.innerHTML += ' <span class="badge text-bg-danger">Cleanup failed</span>';
  }
  renderActivity();
  renderSafeFromEvents(true);
  revealRail(result.summary || "Cleanup/revert state updated.");
  refreshOpenAutonomyModal();
  void loadTransactions();
}
async function runPostMutationValidation(runId) {
  if (!runId) throw new Error("MoP Execution run_id is required for post-mutation validation.");
  const response = await fetch("/api/mop-execution/validation-report", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({run_id: runId, publish: true, model_profile: valueOf("model_profile")}),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function renderValidationMatrix(matrix = []) {
  if (!matrix.length) return "No validation matrix rows were returned.";
  const header = "| Kind | Name | Namespace | Expected | Observed | Status |\n|---|---|---|---|---|---|";
  const rows = matrix.map((row) => `| ${row.kind || "Unknown"} | ${row.name || "not reported"} | ${row.namespace || "not reported"} | ${JSON.stringify(row.expected ?? "not reported")} | ${JSON.stringify(row.observed ?? "not reported")} | ${row.status || "unknown"} |`);
  return [header, ...rows].join("\n");
}
function renderValidationReportResult(result = {}) {
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const validation = result.validation || {};
  const reports = result.reports || {};
  const publish = result.artifact_publish || {};
  const validationMatrix = validation.validation_matrix || reports.validation_matrix || [];
  const lines = [
    `# MoP Execution Validation and Reports: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || activeRunId || "not available"}`,
    `- Mutation job ID: ${result.mutation_job_id || "not available"}`,
    `- Target namespace: ${result.target_namespace || valueOf("mop_execution_target_namespace")}`,
    `- Validation status: ${validation.status || "unknown"}`,
    `- Report publish status: ${publish.status || "unknown"}`,
    "",
    "## Summary",
    result.summary || reports.summary || "No validation summary returned.",
    "",
    "## Validation Matrix",
    renderValidationMatrix(validationMatrix),
    "",
    "## Helm Status and History Evidence",
    JSON.stringify(scrub(validation.helm_evidence || reports.helm_evidence || []), null, 2),
    "",
    "## Kubernetes Readiness Evidence",
    JSON.stringify(scrub(validation.kubernetes_evidence || reports.kubernetes_evidence || []), null, 2),
    "",
    "## Report Metadata",
    JSON.stringify(scrub(reports), null, 2),
    "",
    "## Artifact Publishing",
    JSON.stringify(scrub(publish), null, 2),
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  renderReportLinks(reports, "Execution report");
  if (publish.tree_url && artifactLinks) artifactLinks.innerHTML += ` <a class="btn btn-sm btn-outline-light" href="${escapeHtml(publish.tree_url)}" target="_blank" rel="noopener">Open Git Bundle</a>`;
  if (result.valid) {
    setStatus("completed");
    setVisual("complete");
    setText(spherePhase, "Execution validated");
    setText(sphereTitle, "Reports and evidence are ready.");
    mark("validation", "success", result.summary || "Post-mutation validation passed.");
    mark("reports", "success", "Execution, validation, and evidence report metadata is available.");
    mark("complete", "success", "MoP execution completed with validation and report bundle.");
    setApprovalFeedback(result.summary || "Post-mutation validation passed and reports are ready.");
    setText(formStatus, result.summary || "MoP Execution completed.");
  } else if (result.completed_with_review || result.status === "completed_with_review" || validation.status === "needs_review") {
    const reviewSummary = result.summary || "Mutation completed. Post-mutation validation report is available, but ESDA received no validation matrix rows. Manual Kubernetes/Helm verification passed.";
    setStatus("completed_with_review");
    setVisual("complete");
    setText(spherePhase, "Execution completed");
    setText(sphereTitle, "Validation report needs review.");
    hideApprovalCard();
    mark("mutation", "success", "Approved mutation completed through execution agent.");
    mark("validation", "recovered", reviewSummary);
    mark("reports", "success", "Execution report metadata and local artifacts were collected.");
    mark("complete", "recovered", "MoP execution completed with validation review.");
    setApprovalFeedback(reviewSummary, false);
    setText(formStatus, reviewSummary);
  } else {
    setStatus("validation_failed");
    setVisual("failed");
    mark("validation", "failed", result.summary || "Post-mutation validation failed.");
    mark("reports", "success", "Execution report metadata and local artifacts were collected.");
    mark("complete", "failed", "MoP execution failed validation.");
    setApprovalFeedback(result.summary || "Post-mutation validation failed.", true);
    setText(formStatus, result.summary || "Post-mutation validation failed.");
  }
  renderActivity();
  renderSafeFromEvents(true);
  refreshOpenAutonomyModal();
  void loadTransactions();
}
function formatDecisionValue(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(scrub(value), null, 2);
  } catch (_error) {
    return String(value);
  }
}
function renderDecisionCard(result = {}) {
  if (!decisionCard) return;
  const card = result.decision_card || {};
  const context = card.context || result.decision_required || {};
  const response = extractDecisionResponse(context);
  const reason = card.reason_code || findDeep(response, "reason_code") || findDeep(response, "reason") || "decision_required";
  const phase = card.phase || findDeep(response, "phase") || result.current_phase || "dry_run";
  const step = card.step || findDeep(response, "step") || findDeep(response, "step_id") || "not_specified";
  const jobId = result.dry_run_job_id || card.job_id || findDeep(response, "job_id") || findDeep(context, "job_id") || "";
  decisionCard.dataset.runId = result.run_id || card.run_id || activeRunId || "";
  decisionCard.dataset.jobId = jobId;
  setText(decisionStatus, "decision required");
  setText(decisionReason, reason);
  setText(decisionPhase, phase);
  setText(decisionStep, step);
  const redactedError = card.redacted_error || findDeep(response, "error") || findDeep(response, "message") || "";
  if (decisionError) {
    decisionError.textContent = formatDecisionValue(redactedError);
    decisionError.classList.toggle("d-none", !redactedError);
  }
  const safeSummary = card.safe_options?.summary || result.safe_options?.summary || "Choose only a bounded, non-mutating instruction. Mutation remains disabled.";
  setText(decisionSafeOptions, safeSummary);
  const schema = card.allowed_instruction_schema || findDeep(response, "allowed_instruction_schema") || findDeep(response, "instruction_schema") || {action: ["continue", "retry_read_only", "use_default", "skip_optional", "abort"]};
  if (decisionAllowedSchema) decisionAllowedSchema.textContent = JSON.stringify(scrub(schema), null, 2);
  const unsafe = card.unsafe_examples || findDeep(response, "unsafe_examples") || ["Bypass approval and apply now.", "Run kubectl apply from ESDA.", "Expose Secret data."];
  if (decisionUnsafeExamples) {
    decisionUnsafeExamples.innerHTML = "";
    (Array.isArray(unsafe) ? unsafe : [unsafe]).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = String(item);
      decisionUnsafeExamples.appendChild(li);
    });
  }
  if (decisionScope) decisionScope.value = JSON.stringify(decisionScopeDefault({reason_code: reason, phase, step}, result), null, 2);
  if (decisionInstruction) decisionInstruction.value = "";
  if (decisionRationale) decisionRationale.value = "";
  if (decisionSubmit) decisionSubmit.disabled = false;
  decisionCard.classList.remove("d-none");
  setDecisionFeedback("Provide a bounded instruction only if the shown scope is correct.");
}
function validateDecisionInstruction(payload) {
  const errors = [];
  const instruction = payload.instruction || "";
  const scope = payload.scope || {};
  const action = payload.action || "";
  const text = `${action}\n${instruction}\n${payload.rationale || ""}\n${JSON.stringify(scope)}`;
  if (instruction.trim().length < 3) errors.push("Instruction must be at least 3 characters.");
  if (!Object.keys(scope).length) errors.push("Scope JSON must not be empty.");
  const scopedNamespace = scope.target_namespace || scope.namespace || valueOf("mop_execution_target_namespace");
  if (scopedNamespace && scopedNamespace !== valueOf("mop_execution_target_namespace")) errors.push("Scope namespace must match the selected target namespace.");
  if (/\b(secret|password|token|credential|api[_-]?key)\b/i.test(text)) errors.push("Instruction cannot reference secrets or credentials.");
  if (/\b(kubectl|helm)\s+(apply|delete|patch|replace|create|scale|upgrade|install|uninstall)\b/i.test(text)) errors.push("Instruction cannot contain direct Kubernetes or Helm mutation commands.");
  if (/\b(ignore|bypass|disable)\s+(approval|policy|guardrail|safety)\b/i.test(text)) errors.push("Instruction cannot bypass approval, policy, or safety gates.");
  return errors;
}
function timelineText() { return events.length ? events.map((e, i) => `${i + 1}. ${e.event_type}: ${e.message}\n${JSON.stringify(displayPayload(e), null, 2)}`).join("\n\n") : "No progress events yet."; }
function logsText() { return events.length ? JSON.stringify(events.map((event, index) => ({index: index + 1, sequence: event.sequence, event_type: event.event_type, message: event.message, payload: displayPayload(event)})), null, 2) : "No progress events yet."; }
async function copyText(text) { if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text); const box = document.createElement("textarea"); box.value = text; box.style.position = "fixed"; box.style.opacity = "0"; document.body.appendChild(box); box.select(); document.execCommand("copy"); box.remove(); }
function titleFromPhase(phase) { return String(phase || "Agent").split("_").map((part) => part ? part[0].toUpperCase() + part.slice(1) : part).join(" "); }
function stageForEvent(e) {
  const phase = String(e?.payload?.phase || e?.payload?.current_phase || "").toLowerCase();
  const map = {run_started: "intake", preflight_completed: "preflight", digital_twin_gate_bound: "preflight", digital_twin_gate_revalidated: "preflight", agent_health_checked: "agent_health", agent_readiness_checked: "agent_health", agent_capabilities_checked: "agent_health", bundle_validated: "bundle_validate", bundle_validation_completed: "bundle_validate", dry_run_job_created: "dry_run_job", dry_run_started: "dry_run", observations_received: "observations", job_observation: "observations", decision_required: "decision", dry_run_report_created: "dry_run_report", approval_submitted: "approval", mutation_job_created: "mutation_job", mutation_started: "mutation", validation_completed: "validation", reports_created: "reports", reports_updated: (e?.payload?.reports?.phase === "post_mutation" ? "reports" : "dry_run_report"), artifact_publish_completed: "reports", artifact_publish_failed: "reports", rollback_cleanup_updated: "rollback_cleanup", rollback_started: "rollback_cleanup", cleanup_started: "rollback_cleanup", run_completed: "complete", run_failed: "complete"};
  return map[e?.event_type] || (defs.some(([id]) => id === phase) ? phase : null);
}
function stageNoteLabel(stageId) { if (stageId === "creating_plan") return "00 / Creating Plan"; const n = String(stageNumbers[stageId] || 99).padStart(2, "0"); return `${n} / ${stageLabels[stageId] || titleFromPhase(stageId)}`; }
function addStageWorking(stageId, message, detail, key) {
  if (!workingStream || !workingPanel || !stageId) return;
  const stableKey = key || `${stageId}:${message}:${detail}`;
  if (workingKeys.has(stableKey)) return;
  workingKeys.add(stableKey);
  if (workingPanel.classList.contains("is-empty")) { workingStream.innerHTML = ""; workingPanel.classList.remove("is-empty"); }
  const item = document.createElement("article"); item.className = "working-note-item"; item.dataset.stageNumber = String(stageId === "creating_plan" ? 0 : stageNumbers[stageId] || 99); item.dataset.order = String(++workingOrder);
  const label = document.createElement("div"); label.className = "working-note-label"; label.textContent = stageNoteLabel(stageId);
  const text = document.createElement("p"); text.textContent = `${message || "Working"}. ${detail || ""}`.trim();
  item.append(label, text); workingStream.appendChild(item);
  Array.from(workingStream.querySelectorAll(".working-note-item")).sort((a, b) => Number(a.dataset.stageNumber) - Number(b.dataset.stageNumber) || Number(a.dataset.order) - Number(b.dataset.order)).forEach((node) => workingStream.appendChild(node));
  refreshOpenAutonomyModal();
}
function resetWorking() { workingOrder = 0; workingKeys.clear(); if (workingStream) workingStream.innerHTML = '<div class="stream-empty-state">Live model and agent working notes will appear while this page is connected.</div>'; workingPanel?.classList.add("is-empty"); }
function renderSafeFromEvents(show) {
  if (!safeList) return;
  const summaries = events.map((e) => e.payload?.reasoning_summary || e.payload?.summary || e.payload?.safe_reasoning_summary).filter(Boolean);
  safeList.innerHTML = summaries.length ? "" : '<div class="stream-empty-state">No safe reasoning summaries are available yet.</div>';
  summaries.forEach((summary, i) => { const row = document.createElement("article"); row.className = "safe-summary-item"; row.innerHTML = `<div class="safe-summary-label">${i + 1}. Summary</div><p></p>`; row.querySelector("p").textContent = summary; safeList.appendChild(row); });
  safeList.hidden = !show;
}
function clonePanelContent(source, emptyText) { const wrapper = document.createElement("div"); const hasNotes = source && source.querySelector(".working-note-item, .safe-summary-item"); if (hasNotes) source.querySelectorAll(".working-note-item, .safe-summary-item").forEach((node) => wrapper.appendChild(node.cloneNode(true))); else wrapper.innerHTML = `<div class="stream-empty-state">${emptyText}</div>`; return wrapper.innerHTML; }
function updateAutonomyModal() { if (autonomyModalLive) autonomyModalLive.innerHTML = clonePanelContent(workingStream, "No live reasoning stream is available in this page session."); if (autonomyModalSummary) autonomyModalSummary.innerHTML = clonePanelContent(safeList, "No safe reasoning summaries are available yet."); if (autonomyModalJson) autonomyModalJson.textContent = logsText(); }
function refreshOpenAutonomyModal() { if (autonomyModal?.classList.contains("show")) updateAutonomyModal(); }
function setRailStatus(s) { setText(railStatus, s); }
function clearAutoHide() { if (autoHideTimer) clearTimeout(autoHideTimer); autoHideTimer = null; }
function controls() { const collapsed = rail?.classList.contains("is-collapsed"); rail?.setAttribute("aria-expanded", String(!collapsed)); setText(railToggle, collapsed ? "Show" : "Hide"); setText(railPin, pinned ? "Pinned" : "Pin"); railPin?.setAttribute("aria-pressed", String(pinned)); }
function collapseRail(force = false) { if (pinned && !force) return; clearAutoHide(); rail?.classList.add("is-collapsed"); rail?.classList.remove("is-revealed"); controls(); }
function hideRail() { clearAutoHide(); rail?.classList.add("is-dormant", "is-collapsed"); rail?.classList.remove("is-revealed"); controls(); }
function revealRail(reason) { clearAutoHide(); rail?.classList.remove("is-dormant", "is-collapsed"); rail?.classList.add("is-revealed"); if (reason) setRailStatus(reason); controls(); if (!pinned) autoHideTimer = setTimeout(() => collapseRail(), autoHideMs); }
function resetActivity() { activity = Object.fromEntries(defs.map(([id, label, hint]) => [id, {status: "pending", label, detail: hint}])); setRailStatus("Awaiting execution"); renderActivity(); if (!pinned) collapseRail(true); }
function mark(id, status, detail) { if (activity[id]) activity[id] = {...activity[id], status, detail: detail || activity[id].detail}; }
function renderActivity() {
  if (!railGraph) return;
  railGraph.innerHTML = "";
  defs.forEach(([id, label, hint], i) => {
    const state = activity[id] || {status: "pending", detail: hint};
    const node = document.createElement("div"); node.className = `activity-node is-${state.status}`; node.role = "listitem"; node.tabIndex = 0;
    node.innerHTML = `<span class="activity-node-dot">${i + 1}</span><span class="activity-node-label"></span><span class="activity-node-popover"></span>`;
    node.querySelector(".activity-node-label").textContent = label;
    node.querySelector(".activity-node-popover").textContent = state.detail || hint;
    railGraph.appendChild(node);
    if (i < defs.length - 1) { const connector = document.createElement("span"); connector.className = `activity-connector is-${state.status}`; railGraph.appendChild(connector); }
  });
}
function addTimeline(e) {
  if (!timeline) return;
  if (e.event_id && seen.has(e.event_id)) return;
  if (e.event_id) seen.add(e.event_id);
  events.push(e); lastSeq = Math.max(lastSeq, Number(e.sequence || 0));
  const li = document.createElement("li");
  const title = document.createElement("strong"); title.textContent = `${events.length}. ${e.event_type}: ${e.message || ""}`;
  const pre = document.createElement("pre"); pre.textContent = JSON.stringify(displayPayload(e), null, 2);
  li.append(title, pre); timeline.appendChild(li); timelineScroll?.scrollTo({top: timelineScroll.scrollHeight, behavior: "smooth"});
  refreshOpenAutonomyModal();
}
function summarize(e) { const p = e?.payload || {}; if (p.current_state) return `${e.message || "Execution updated"}. State: ${p.current_state}.`; if (p.result?.status) return `${e.message || "Agent updated"}. Status: ${p.result.status}.`; if (p.reasoning_summary) return p.reasoning_summary; return e?.message || "Activity updated."; }
function stageStatusForEvent(e) {
  const payload = e?.payload || {};
  if (e.event_type === "run_failed") return "failed";
  if (e.event_type === "decision_required") return "recovered";
  if (["dry_run_started", "mutation_started", "job_started"].includes(e.event_type)) return "running";
  if (e.event_type === "job_state_polled") {
    const state = String(payload.current_state || payload.job?.state || "").toLowerCase();
    if (["failed", "failed_safe", "failure", "error", "cancelled", "stopped"].includes(state)) return "failed";
    if (["decision_required", "paused", "rollback_required", "rollback_needed", "requires_rollback", "unknown_outcome", "ambiguous", "indeterminate", "mutation_unknown"].includes(state)) return "recovered";
    if (["dry_run_succeeded", "waiting_for_approval", "ready_for_approval", "approval_required", "mutation_succeeded", "succeeded", "completed"].includes(state)) return "success";
    return "running";
  }
  if (e.event_type === "agent_health_checked" && payload.healthy === false) return "failed";
  if (e.event_type === "agent_readiness_checked" && payload.ready === false) return "failed";
  if (e.event_type === "agent_capabilities_checked" && payload.capabilities_ok === false) return "failed";
  if (e.event_type === "bundle_validated" && payload.bundle_validation?.valid === false) return "failed";
  if (e.event_type === "validation_completed") return payload.validation?.status === "passed" ? "success" : "recovered";
  if (e.event_type === "rollback_cleanup_updated") { const cleanupStatus = normalizedStatus(payload.state?.cleanup_status); if (cleanupStatus === "cleanup_failed") return "failed"; if (cleanupStatus === "cleanup_running") return "running"; if (cleanupStatus === "cleanup_needs_review") return "recovered"; return "success"; }
  if (e.event_type === "artifact_publish_failed") return "recovered";
  return "success";
}
function processEvent(e, opt = {}) {
  if (!e || typeof e !== "object") return;
  const stage = stageForEvent(e);
  if (opt.live !== false && stage) addStageWorking(stage, e.message || summarize(e), e.payload?.detail || e.payload?.reasoning_summary || "", `event:${e.event_id || e.sequence || e.event_type}:${stage}`);
  addTimeline(e);
  if (stage) mark(stage, stageStatusForEvent(e), summarize(e));
  if (["digital_twin_gate_bound", "digital_twin_gate_revalidated"].includes(e.event_type) && e.payload?.twin_gate) renderTwinGate(e.payload.twin_gate);
  renderActivity();
  if (opt.reveal !== false && stage) revealRail(summarize(e));
  const mapped = {run_started: "running", run_failed: "failed", rollback_cleanup_updated: e.payload?.state?.cleanup_status}[e.event_type] || (e.event_type === "run_completed" ? (e.payload?.status || "completed") : null);
  if (mapped) applyRunStatus(mapped);
  if (e.event_type === "decision_required") renderDecisionCard({run_id: e.run_id || activeRunId, decision_required: e.payload?.decision_required});
  if (opt.hydrateApproval !== false && e.event_type === "job_state_polled") { const state = String(e.payload?.current_state || e.payload?.job?.state || "").toLowerCase(); if (isApprovalReadyState(state)) void ensureApprovalGateForRun(e.run_id || activeRunId, {state, force: true}); }
  if (e.event_type === "instruction_submitted") { mark("decision", "success", "Bounded instruction submitted to execution agent."); renderActivity(); }
  if (e.event_type === "reports_updated") { const reportPayload = e.payload?.reports || {}; renderReportLinks(reportPayload, reportPayload.phase === "post_mutation" ? "Execution report" : "Dry-run report"); if (reportPayload.phase !== "post_mutation") renderApprovalCard({run_id: e.run_id || activeRunId, reports: reportPayload}); }
  if (e.event_type === "approval_submitted") { const accepted = Boolean(e.payload?.accepted); const detail = accepted ? "Approval accepted by execution agent." : approvalErrorSummary(e.payload || {}); mark("approval", accepted ? "success" : "recovered", detail); if (accepted && mutationStart) mutationStart.disabled = false; renderActivity(); }
  if (["run_completed", "run_failed"].includes(e.event_type)) { hideDecisionCard(); hideApprovalCard(); renderSafeFromEvents(true); if (e.payload?.final_report) finalReport.textContent = e.payload.final_report; }
}
function resetTimeline() { events = []; lastSeq = 0; seen.clear(); if (timeline) timeline.innerHTML = ""; renderSafeFromEvents(false); resetActivity(); resetWorking(); }
function setActive(runId) { activeRunId = runId; try { if (runId) localStorage.setItem(activeKey, runId); else localStorage.removeItem(activeKey); } catch (_e) {} }
function resetView(message) { viewGeneration += 1; if (es) { es.close(); es = null; } approvalHydrationInFlight.clear(); resetTimeline(); hideRail(); hideDecisionCard(); hideApprovalCard(); if (artifactLinks) artifactLinks.innerHTML = '<span class="small text-secondary">Execution report links appear after report generation.</span>'; if (finalReport) finalReport.textContent = message; setText(copyProgressStatus, ""); setCleanupFeedback(""); updateCleanupButton("Idle"); }
function normalizeFreshExecutionMode() {
  const modeSelect = $("mop_execution_mode");
  if (!activeRunId && modeSelect?.value === "cleanup_revert") {
    modeSelect.value = "approved_mutation";
    setCleanupFeedback("");
    setText(formStatus, "Ready for a fresh approved mutation run.");
  }
}
function startNewRun() { setActive(null); if ($("mop_execution_mode")) $("mop_execution_mode").value = "approved_mutation"; resetView("No MoP execution run yet."); setStatus("Idle"); setVisual("idle"); stampCorrelationId(); if (approvalSubmit) approvalSubmit.disabled = false; if (mutationStart) mutationStart.disabled = true; if (cleanupStart) cleanupStart.disabled = true; setCleanupFeedback(""); setText(formStatus, "Ready for a fresh approved mutation run."); void loadTransactions(); form?.scrollIntoView({behavior: "smooth", block: "start"}); }
function setSidebar(open) { if (!txSidebar) return; document.body.classList.toggle("transaction-sidebar-open", open); txSidebar.setAttribute("aria-hidden", open ? "false" : "true"); txToggle?.setAttribute("aria-expanded", open ? "true" : "false"); }
function time(value) { if (!value) return ""; try { return new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(value)); } catch (_e) { return ""; } }
function renderTransactions(list) {
  if (!txList || !txStatus) return;
  txList.innerHTML = "";
  if (txClearAll) txClearAll.disabled = !list.length;
  if (!list.length) { txStatus.textContent = "No MoP execution transactions yet."; return; }
  txStatus.textContent = `${list.length} MoP execution transaction${list.length === 1 ? "" : "s"}`;
  list.forEach((tx) => {
    const card = document.createElement("article"); card.className = `transaction-card${tx.run_id === activeRunId ? " is-active" : ""}`;
    card.tabIndex = 0; card.setAttribute("role", "button"); card.dataset.runId = tx.run_id;
    card.setAttribute("aria-label", `Open ${tx.title || tx.goal || tx.run_id}`);
    const row = document.createElement("div"); row.className = "transaction-card-row";
    const st = document.createElement("span"); st.className = "transaction-status-pill"; st.textContent = tx.status || "unknown";
    const clear = document.createElement("button"); clear.type = "button"; clear.className = "transaction-clear"; clear.textContent = "Clear";
    clear.addEventListener("click", async (ev) => { ev.stopPropagation(); await clearTransaction(tx.run_id); });
    row.append(st, clear);
    const title = document.createElement("div"); title.className = "transaction-card-title"; title.textContent = tx.title || tx.goal || tx.run_id;
    const sub = document.createElement("div"); sub.className = "transaction-card-subtitle"; sub.textContent = tx.namespace || tx.target_url || "mop_execution";
    const meta = document.createElement("div"); meta.className = "transaction-card-meta"; const artifacts = Number(tx.artifact_count || 0); meta.textContent = `${time(tx.updated_at)} | ${artifacts} artifact${artifacts === 1 ? "" : "s"}`;
    const restore = () => void openRun(tx.run_id, {closeSidebar: true});
    card.append(row, title, sub, meta); card.addEventListener("click", restore);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); restore(); }
    });
    txList.appendChild(card);
  });
}
async function loadTransactions() { try { const r = await fetch("/api/transactions?workflow_type=mop_execution"); if (!r.ok) throw new Error(`HTTP ${r.status}`); const data = await r.json(); renderTransactions(data.transactions || []); return data.transactions || []; } catch (err) { if (txStatus) txStatus.textContent = `Could not load transactions: ${err.message}`; return []; } }
async function clearTransaction(runId) { const r = await fetch(`/api/transactions/${runId}/clear`, {method: "POST"}); if (!r.ok) { if (txStatus) txStatus.textContent = `Clear failed: HTTP ${r.status}`; return; } if (runId === activeRunId) { setActive(null); resetView("No MoP execution run yet."); setStatus("Idle"); setVisual("idle"); } await loadTransactions(); }
async function clearAllTransactions() { if (txClearAll) txClearAll.disabled = true; const r = await fetch("/api/transactions/clear?workflow_type=mop_execution", {method: "POST"}); if (!r.ok) { if (txStatus) txStatus.textContent = `Clear all failed: HTTP ${r.status}`; await loadTransactions(); return; } setActive(null); resetView("No MoP execution run yet."); setStatus("Idle"); setVisual("idle"); if (txStatus) txStatus.textContent = "MoP execution history cleared."; await loadTransactions(); }
function bindSidebar() { txToggle?.addEventListener("click", () => setSidebar(true)); txClose?.addEventListener("click", () => setSidebar(false)); txBackdrop?.addEventListener("click", () => setSidebar(false)); txClearAll?.addEventListener("click", clearAllTransactions); document.addEventListener("keydown", (e) => { if (e.key === "Escape") setSidebar(false); }); }
function bindRail() { try { pinned = localStorage.getItem(pinKey) === "true"; } catch (_e) { pinned = false; } rail?.classList.toggle("is-pinned", pinned); controls(); railToggle?.addEventListener("click", () => rail?.classList.contains("is-collapsed") ? revealRail("Activity feed opened.") : collapseRail(true)); railPin?.addEventListener("click", () => { pinned = !pinned; try { localStorage.setItem(pinKey, String(pinned)); } catch (_e) {} rail?.classList.toggle("is-pinned", pinned); if (pinned) revealRail("Activity feed pinned open."); controls(); }); }
async function openRun(runId, opt = {}) {
  if (!runId) return;
  const generation = ++viewGeneration;
  if (es) { es.close(); es = null; }
  approvalHydrationInFlight.clear(); resetTimeline(); setActive(runId); if (opt.closeSidebar) setSidebar(false);
  setStatus("loading"); setVisual("working");
  if (finalReport) finalReport.textContent = "Loading persisted execution state...";
  setText(copyProgressStatus, "Restoring selected execution...");
  try {
    const r = await fetch(`/api/runs/${encodeURIComponent(runId)}/snapshot?compact=true`); if (!r.ok) throw new Error(`HTTP ${r.status}`);
    if (generation !== viewGeneration || runId !== activeRunId) return;
    const snap = await r.json(); const run = snap.run || {};
    applyRunStatus(run.status || "Idle");
    const restoredEvents = snap.events || [];
    restoredEvents.forEach((event) => processEvent(event, {live: false, reveal: false}));
    renderActivity();
    if (restoredEvents.length) {
      revealRail(terminal(run.status) ? "Loaded completed execution activity." : "Loaded active execution activity.");
    }
    if (run.final_report) finalReport.textContent = run.final_report;
    renderSafeFromEvents(terminal(run.status));
    const restoredState = run.status || latestEventValue("current_state");
    if (isApprovalReadyState(restoredState) || isApprovalReadyState(latestEventValue("current_state"))) void ensureApprovalGateForRun(runId, {state: restoredState, force: true});
    if (!terminal(run.status)) connect(runId);
    setText(copyProgressStatus, "Selected execution restored.");
  } catch (err) {
    if (generation !== viewGeneration || runId !== activeRunId) return;
    resetView(`Could not restore MoP execution run: ${err.message}`); setActive(null); setStatus("failed"); setVisual("failed");
  }
  await loadTransactions();
}
function connect(runId) { if (es) es.close(); es = new EventSource(`/api/runs/${runId}/events${lastSeq ? `?after_sequence=${lastSeq}` : ""}`); es.onmessage = async (msg) => { const event = JSON.parse(msg.data); processEvent(event, {live: true}); if (["run_completed", "run_failed"].includes(event.event_type)) { es?.close(); es = null; await loadTransactions(); } }; es.onerror = () => setText(copyProgressStatus, "Live event stream temporarily disconnected."); }
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
}
function formatBytes(bytes) {
  if (!Number.isFinite(Number(bytes))) return "unknown size";
  const value = Number(bytes);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
function shortHash(value) { return value ? `${String(value).slice(0, 12)}...` : "not calculated"; }
function compactTime(value) { if (!value) return "unknown time"; try { return new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(value)); } catch (_e) { return String(value); } }
function stampCorrelationId() {
  if (!correlationInput) return;
  const prefix = valueOf("mop_execution_name_prefix") || "agent-ai";
  const target = valueOf("mop_execution_target_namespace") || "target";
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  correlationInput.value = `${prefix}-${target}-execution-${stamp}`;
}
function selectedCandidate() {
  const runId = activityRunSelect?.value || "";
  return bundleCandidates.find((candidate) => candidate.run_id === runId) || null;
}
function renderBundleMetadata(candidate = selectedCandidate()) {
  if (!bundleMetadataPanel) return;
  const source = valueOf("mop_execution_bundle_source") || "activity_run";
  let lines = [];
  if (source === "activity_run" && candidate) {
    lines = [
      `Run: ${candidate.title || candidate.run_id}`,
      `Source namespace: ${candidate.source_namespace || "unknown"}`,
      `Generated: ${compactTime(candidate.generated_at)}`,
      `Bundle: ${candidate.filename || "mop-bundle.zip"} (${formatBytes(candidate.size_bytes)})`,
      `Bundle identity: ${shortHash(candidate.canonical_sha256 || candidate.sha256)}`,
      `Publish folder: ${candidate.publish_folder || "not published"}`,
    ];
  } else if (source === "artifact_repo_folder") {
    lines = [`Folder: ${repoFolderInput?.value || "not selected"}`, "Local lookup will map this folder back to a known MoP Generation run."];
  } else if (source === "upload_bundle") {
    const file = bundleFileInput?.files?.[0];
    lines = file ? [`Upload: ${file.name}`, `Size: ${formatBytes(file.size)}`] : ["No upload selected."];
  } else {
    lines = ["No MoP bundle selected."];
  }
  bundleMetadataPanel.innerHTML = `<div class="working-note-label">Bundle Metadata</div><p>${escapeHtml(lines.join("\n"))}</p>`;
}
function gateValue(value) {
  if (value == null || value === "") return "not available";
  if (typeof value === "object") return value.classification || value.level || value.status || value.score || "available";
  return String(value).replaceAll("_", " ");
}
function twinGateRequestFields() {
  if (!twinGateRequired) return {};
  const facts = selectedTwinGate?.gate_facts || {};
  return selectedTwinGate ? {
    twin_id: facts.twin_id,
    twin_decision_version: facts.decision_version,
    twin_gate_hash: selectedTwinGate.gate_hash,
  } : {};
}
function updateExecutionGateControl() {
  const submit = $("mop-execution-submit");
  if (!submit) return;
  const executionModeNeedsGate = ["approved_mutation", "dry_run_then_approval"].includes(valueOf("mop_execution_mode"));
  const requiresGate = twinGateRequired && executionModeNeedsGate;
  const decision = selectedTwinGate?.gate_facts?.decision;
  const actions = selectedTwinGate?.actions || {};
  const eligible = decision === "green"
    ? actions.start_execution?.enabled === true
    : decision === "amber" && actions.request_approval?.enabled === true;
  submit.disabled = Boolean(requiresGate && !eligible);
  if (!twinGateRequired && executionModeNeedsGate) {
    setText(
      formStatus,
      selectedTwinGate
        ? "Namespace Twin evidence is advisory. Standard preflight, dry-run, and human approval gates remain authoritative."
        : "Namespace Twin is optional. Execution will use standard preflight, dry-run, and human approval gates."
    );
  } else if (requiresGate && !selectedTwinGate) {
    setText(formStatus, "Select a bundle with a final authoritative Namespace Twin before approved execution.");
  }
}
function renderTwinGate(gate, message = "") {
  selectedTwinGate = gate || null;
  if (!twinGatePanel) return;
  twinGatePanel.className = "namespace-twin-gate mb-3";
  if (!gate) {
    twinGatePanel.classList.add("is-empty");
    setText(twinGateTitle, message || "No matching authoritative twin");
    setText(twinGateDecision, twinGateRequired ? "not bound" : "optional");
    twinGateDecision.className = "badge text-bg-secondary";
    if (twinGateFacts) twinGateFacts.innerHTML = "";
    if (twinGateReasons) twinGateReasons.innerHTML = "";
    if (twinGateActions) {
      twinGateActions.innerHTML = [
        '<button class="btn btn-sm btn-primary" type="button" data-twin-gate-generate>Run Digital Simulation</button>',
        '<button class="btn btn-sm btn-outline-light" type="button" data-twin-gate-retry>Retry matching</button>',
        '<a class="btn btn-sm btn-outline-light" href="/digital-twins">Open Digital Twins</a>',
      ].join("");
      wireTwinGenerationButton();
      twinGateActions.querySelector("[data-twin-gate-retry]")?.addEventListener("click", () => void loadTwinGate());
    }
    updateExecutionGateControl();
    return;
  }
  const facts = gate.gate_facts || {};
  const decision = facts.decision || "pending";
  twinGatePanel.classList.add(`is-${decision}`);
  setText(twinGateTitle, `${facts.twin_id} - decision v${facts.decision_version}`);
  setText(twinGateDecision, decision);
  twinGateDecision.className = `badge ${decision === "green" ? "text-bg-success" : decision === "amber" ? "text-bg-warning" : "text-bg-danger"}`;
  const matrix = [
    ["Risk", twinRiskLabel(gate.risk)], ["Policy", gateValue(gate.policy)], ["Evidence", gateValue(gate.evidence)],
    ["Freshness", gateValue(facts.freshness)], ["Dry-run", gateValue(facts.dry_run)], ["Rollback", gateValue(facts.rollback)],
    ["Drift", gateValue(facts.drift)], ["Approval", gateValue(facts.approval)], ["Gate", shortHash(gate.gate_hash)],
  ];
  if (twinGateFacts) twinGateFacts.innerHTML = matrix.map(([label, value]) => `<span><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></span>`).join("");
  if (twinGateReasons) twinGateReasons.innerHTML = (gate.reasons || []).slice(0, 3).map((reason) => `<li>${escapeHtml(reason.summary || reason.message || reason.code || String(reason))}</li>`).join("") || "<li>No additional blocking reasons.</li>";
  const action = (code, label) => {
    const contract = gate.actions?.[code] || {};
    return `<button class="btn btn-sm btn-outline-light" type="button" disabled title="${escapeHtml(contract.enabled ? `${label} is enabled by the canonical gate.` : contract.disabled_reason || `${label} is disabled by the canonical gate.`)}">${escapeHtml(label)}: ${contract.enabled ? "enabled" : "disabled"}</button>`;
  };
  if (twinGateActions) {
    twinGateActions.innerHTML = [
      `<a class="btn btn-sm btn-outline-light" href="/digital-twins/${encodeURIComponent(facts.twin_id)}?tab=overview">View Full Twin</a>`,
      '<button class="btn btn-sm btn-outline-light" type="button" data-twin-gate-generate>Run Again</button>',
      action("start_execution", "Start"),
      action("request_approval", "Approval"),
      action("regenerate_twin", "Regenerate"),
    ].join("");
    wireTwinGenerationButton();
  }
  updateExecutionGateControl();
}
function twinRiskLabel(risk) {
  if (!risk || typeof risk !== "object") return gateValue(risk);
  const score = risk.score;
  const level = risk.level || risk.classification || risk.status;
  if (score != null && level) return `${score} (${String(level).replaceAll("_", " ")})`;
  if (score != null) return String(score);
  return gateValue(risk);
}
function twinLaunchToken() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
async function twinApiError(response, fallback) {
  try {
    const payload = await response.json();
    return payload?.error?.message || payload?.detail || fallback || `HTTP ${response.status}`;
  } catch (_error) {
    return fallback || `HTTP ${response.status}`;
  }
}
function clearTwinGenerationPolling() {
  window.clearTimeout(twinGenerationPollTimer);
  twinGenerationPollTimer = 0;
}
function wireTwinGenerationButton() {
  const button = twinGateActions?.querySelector("[data-twin-gate-generate]");
  if (!button) return;
  const available = valueOf("mop_execution_bundle_source") === "activity_run" && Boolean(selectedCandidate());
  button.disabled = twinGenerationInFlight || !available;
  if (twinGenerationInFlight) button.textContent = "Simulation running...";
  button.title = available
    ? "Run a new server-side Namespace Twin for the selected bundle and target namespace."
    : "Select a persisted Activity-run MoP bundle first.";
  button.addEventListener("click", () => void runSelectedTwinSimulation());
}
async function pollGeneratedTwin(twinId, attempt = 0) {
  if (!twinGenerationInFlight) return;
  try {
    const response = await fetch(`/api/digital-twins/${encodeURIComponent(twinId)}/gate`);
    if (response.ok) {
      const gate = await response.json();
      renderTwinGate(gate);
      if (gate?.gate_facts?.decision_is_final) {
        twinGenerationInFlight = false;
        clearTwinGenerationPolling();
        wireTwinGenerationButton();
        setText(formStatus, "Digital simulation completed. The matching Namespace Twin score is shown above.");
        return;
      }
      setText(twinGateTitle, `${twinId} - simulation in progress`);
    } else if (![404, 409, 425].includes(response.status)) {
      throw new Error(await twinApiError(response, "Could not read the generated twin."));
    }
  } catch (error) {
    if (attempt >= 120) {
      twinGenerationInFlight = false;
      renderTwinGate(null, `Digital simulation status could not be restored: ${error.message}`);
      return;
    }
  }
  if (attempt >= 120) {
    twinGenerationInFlight = false;
    renderTwinGate(null, "Digital simulation is still running. Use Retry matching to restore it.");
    return;
  }
  twinGenerationPollTimer = window.setTimeout(() => void pollGeneratedTwin(twinId, attempt + 1), 2500);
}
async function runSelectedTwinSimulation() {
  if (twinGenerationInFlight) return;
  const source = valueOf("mop_execution_bundle_source") || "activity_run";
  const candidate = selectedCandidate();
  const targetNamespace = valueOf("mop_execution_target_namespace") || "agent-testing";
  if (source !== "activity_run" || !candidate) {
    renderTwinGate(null, "Select a persisted Activity-run MoP bundle before running a Digital Twin.");
    return;
  }
  twinGenerationInFlight = true;
  clearTwinGenerationPolling();
  renderTwinGateLoading("Loading the Digital Twin simulation contract...");
  try {
    const catalogResponse = await fetch("/api/digital-twins/sources");
    if (!catalogResponse.ok) {
      const fallback = catalogResponse.status === 404
        ? "The real Digital Twin backend is not active. Restart ESDA with DIGITAL_TWIN_BACKEND_MODE=real_core."
        : "Digital Twin sources could not be loaded.";
      throw new Error(await twinApiError(catalogResponse, fallback));
    }
    const catalog = await catalogResponse.json();
    const sourceBundle = (catalog.bundles || []).find((item) =>
      item.run_id === candidate.run_id && (!candidate.artifact_id || item.artifact_id === candidate.artifact_id)
    );
    if (!sourceBundle) throw new Error("The selected bundle is not available in the authenticated Digital Twin catalog.");
    if (!sourceBundle.eligible) throw new Error(sourceBundle.eligibility_message || "Publish this MoP bundle before simulation.");
    if (!(catalog.target_namespaces || []).includes(targetNamespace)) {
      throw new Error(`Target namespace ${targetNamespace} is outside the Digital Twin policy boundary.`);
    }
    renderTwinGateLoading("Starting the full server-side Digital Twin simulation...");
    const response = await fetch("/api/digital-twins", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        bundle_run_id: candidate.run_id,
        bundle_artifact_id: candidate.artifact_id,
        target_namespace: targetNamespace,
        target_cluster: catalog.default_target_cluster || "configured-cluster",
        run_authoritative_dry_run: true,
        idempotency_key: (`esda-execution-twin-${candidate.run_id}-${targetNamespace}-${twinLaunchToken()}`).slice(0, 200),
      }),
    });
    if (!response.ok) throw new Error(await twinApiError(response, "Digital simulation could not be started."));
    const twin = await response.json();
    if (!twin.twin_id) throw new Error("Digital Twin service did not return a twin ID.");
    setText(formStatus, "Digital simulation started. ESDA is restoring the decision and risk score here.");
    renderTwinGateLoading(`${twin.twin_id} - calculating authoritative decision...`);
    void pollGeneratedTwin(twin.twin_id);
  } catch (error) {
    twinGenerationInFlight = false;
    clearTwinGenerationPolling();
    renderTwinGate(null, `Digital simulation unavailable: ${error.message}`);
  }
}
function renderTwinGateLoading(message = "Matching authoritative twin...") {
  selectedTwinGate = null;
  if (!twinGatePanel) return;
  twinGatePanel.className = "namespace-twin-gate mb-3 is-loading";
  setText(twinGateTitle, message);
  setText(twinGateDecision, "matching");
  twinGateDecision.className = "badge text-bg-secondary";
  if (twinGateFacts) twinGateFacts.innerHTML = '<span><small>Status</small><strong>Checking the selected bundle and namespace</strong></span>';
  if (twinGateReasons) twinGateReasons.innerHTML = "<li>The authoritative gate will appear when matching completes.</li>";
  if (twinGateActions) twinGateActions.innerHTML = '<a class="btn btn-sm btn-outline-light" href="/digital-twins">Open Digital Twins</a>';
  updateExecutionGateControl();
}
async function loadTwinGate() {
  const loadVersion = ++twinGateLoadVersion;
  twinGateAbortController?.abort();
  twinGenerationInFlight = false;
  clearTwinGenerationPolling();

  const controller = new AbortController();
  twinGateAbortController = controller;
  const source = valueOf("mop_execution_bundle_source") || "activity_run";
  const candidate = selectedCandidate();
  const target = valueOf("mop_execution_target_namespace") || "agent-testing";
  const params = new URLSearchParams(window.location.search);
  const requestedTwinId = requestedTwinIdConsumed ? null : params.get("twin_id");
  requestedTwinIdConsumed = requestedTwinIdConsumed || Boolean(requestedTwinId);
  const requestedBundleHash = params.get("bundle_hash");
  if (requestedBundleHash && activityRunSelect) {
    const match = bundleCandidates.find((item) => (item.canonical_sha256 || item.sha256) === requestedBundleHash);
    if (match) activityRunSelect.value = match.run_id;
  }
  const activeCandidate = selectedCandidate() || candidate;
  const bundleHash = activeCandidate?.canonical_sha256 || activeCandidate?.sha256 || requestedBundleHash;
  const selectedRunId = activeCandidate?.run_id || "";
  const selectionIsCurrent = () => {
    if (loadVersion !== twinGateLoadVersion || controller.signal.aborted) return false;
    if ((valueOf("mop_execution_bundle_source") || "activity_run") !== source) return false;
    if ((valueOf("mop_execution_target_namespace") || "agent-testing") !== target) return false;
    return source !== "activity_run" || (selectedCandidate()?.run_id || "") === selectedRunId;
  };
  if (source !== "activity_run" && !requestedTwinId) {
    renderTwinGate(null, "Namespace Twin matching is available for persisted Activity-run bundles.");
    return;
  }
  if (!bundleHash && !requestedTwinId) {
    renderTwinGate(null, "Select a persisted MoP bundle to match its twin.");
    return;
  }
  renderTwinGateLoading();
  try {
    let twinId = requestedTwinId;
    if (!twinId) {
      const response = await fetch(`/api/digital-twins?namespace=${encodeURIComponent(target)}&limit=100`, {signal: controller.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const listing = await response.json();
      if (!selectionIsCurrent()) return;
      const match = (listing.items || []).find((item) => (item.bundle_hash || item.bundle?.bundle_hash) === bundleHash && (item.target_namespace || item.target?.namespace) === target);
      twinId = match?.twin_id;
    }
    if (!selectionIsCurrent()) return;
    if (!twinId) {
      renderTwinGate(null, "No final Namespace Twin matches this bundle hash and target.");
      return;
    }
    const response = await fetch(`/api/digital-twins/${encodeURIComponent(twinId)}/gate`, {signal: controller.signal});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const gate = await response.json();
    if (selectionIsCurrent()) renderTwinGate(gate);
  } catch (error) {
    if (error.name !== "AbortError" && selectionIsCurrent()) renderTwinGate(null, `Twin gate unavailable: ${error.message}`);
  } finally {
    if (loadVersion === twinGateLoadVersion) twinGateAbortController = null;
  }
}
function updateSourceVisibility() {
  const source = valueOf("mop_execution_bundle_source") || "activity_run";
  activityRunGroup?.classList.toggle("d-none", source !== "activity_run");
  repoFolderGroup?.classList.toggle("d-none", source !== "artifact_repo_folder");
  bundleFileGroup?.classList.toggle("d-none", source !== "upload_bundle");
  renderBundleMetadata();
}
function renderBundleOptions() {
  if (!activityRunSelect) return;
  activityRunSelect.innerHTML = "";
  if (!bundleCandidates.length) {
    const option = document.createElement("option"); option.value = ""; option.textContent = "No completed MoP bundles found"; activityRunSelect.appendChild(option); return;
  }
  bundleCandidates.forEach((candidate, index) => {
    const option = document.createElement("option");
    option.value = candidate.run_id;
    option.dataset.artifactId = candidate.artifact_id || "";
    option.textContent = `${candidate.source_namespace || "namespace"} | ${compactTime(candidate.generated_at)} | ${candidate.filename || "mop-bundle.zip"}`;
    if (index === 0) option.selected = true;
    activityRunSelect.appendChild(option);
  });
}
async function loadBundleCandidates() {
  if (!activityRunSelect) return;
  try {
    const response = await fetch("/api/mop-execution/bundles");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    bundleCandidates = data.bundles || [];
    renderBundleOptions();
    renderBundleMetadata();
    setText(formStatus, `${bundleCandidates.length} MoP bundle${bundleCandidates.length === 1 ? "" : "s"} available.`);
  } catch (error) {
    bundleCandidates = [];
    activityRunSelect.innerHTML = '<option value="">Could not load bundles</option>';
    setText(formStatus, `Could not load MoP bundles: ${error.message}`);
  }
}
async function runPreflight() {
  const target = valueOf("mop_execution_target_namespace") || "agent-testing";
  const source = valueOf("mop_execution_bundle_source") || "activity_run";
  if (source === "upload_bundle") {
    const file = bundleFileInput?.files?.[0];
    if (!file) throw new Error("Select a mop-bundle.zip file before preflight.");
    const formData = new FormData();
    formData.append("target_namespace", target);
    formData.append("file", file);
    const response = await fetch("/api/mop-execution/preflight/upload", {method: "POST", body: formData});
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
  const payload = {source_type: source, target_namespace: target};
  if (source === "activity_run") {
    const candidate = selectedCandidate();
    if (!candidate) throw new Error("Select a completed MoP Generation run with mop-bundle.zip.");
    payload.run_id = candidate.run_id;
    payload.artifact_id = candidate.artifact_id;
  } else {
    payload.folder_name = valueOf("mop_execution_repo_folder");
    if (!payload.folder_name) throw new Error("Enter an artifact repo folder name.");
  }
  const response = await fetch("/api/mop-execution/preflight", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function renderPreflightResult(result) {
  if (result?.correlation_id && correlationInput) correlationInput.value = result.correlation_id;
  const failures = result.failures || [];
  const warnings = result.warnings || [];
  const checks = result.checks || [];
  const metadata = result.metadata || {};
  const bundle = result.bundle || {};
  const lines = [
    `# MoP Execution Preflight: ${result.status || "unknown"}`,
    "",
    `- Target namespace: ${result.target_namespace || valueOf("mop_execution_target_namespace")}`,
    `- Correlation ID: ${result.correlation_id || valueOf("mop_execution_correlation_id")}`,
    `- Bundle: ${bundle.filename || "mop-bundle.zip"}`,
    `- Bundle SHA-256: ${bundle.sha256 || "not calculated"}`,
    `- Source namespace: ${metadata.source_namespace || "unknown"}`,
    `- Generated release: ${metadata.generated_release_name || "unknown"}`,
    "",
    "## Failures",
    failures.length ? failures.map((item) => `- ${item}`).join("\n") : "- None",
    "",
    "## Warnings",
    warnings.length ? warnings.map((item) => `- ${item}`).join("\n") : "- None",
    "",
    "## Checks",
    ...checks.map((check) => `- [${check.status}] ${check.label}: ${check.detail}`),
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  if (artifactLinks) {
    artifactLinks.innerHTML = `<span class="badge ${result.valid ? "text-bg-success" : "text-bg-danger"}">${result.valid ? "Preflight passed" : "Preflight failed"}</span>`;
  }
  addTimeline({event_type: "preflight_completed", message: result.summary || "MoP Execution preflight completed", sequence: events.length + 1, payload: result});
  mark("preflight", result.valid ? "success" : "failed", result.summary);
  if (result.valid) mark("agent_health", "pending", "Ready for execution-agent health validation in the next phase.");
  renderActivity();
  revealRail(result.summary || "Preflight completed");
  renderSafeFromEvents(true);
}
async function runAgentValidation(preflightResult) {
  const target = valueOf("mop_execution_target_namespace") || preflightResult?.target_namespace || "agent-testing";
  const source = valueOf("mop_execution_bundle_source") || preflightResult?.source_type || "activity_run";
  const common = {
    target_namespace: target,
    correlation_id: valueOf("mop_execution_correlation_id") || preflightResult?.correlation_id,
    execution_mode: valueOf("mop_execution_mode") || "approved_mutation",
    model_profile: valueOf("model_profile"),
    ...twinGateRequestFields(),
  };
  if (source === "upload_bundle") {
    const file = bundleFileInput?.files?.[0];
    if (!file) throw new Error("Select a mop-bundle.zip file before agent validation.");
    const formData = new FormData();
    Object.entries(common).forEach(([key, value]) => { if (value) formData.append(key, value); });
    formData.append("file", file);
    const response = await fetch("/api/mop-execution/validate/upload", {method: "POST", body: formData});
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
  const payload = {source_type: source, ...common};
  if (source === "activity_run") {
    const candidate = selectedCandidate();
    if (!candidate) throw new Error("Select a completed MoP Generation run with mop-bundle.zip.");
    payload.run_id = candidate.run_id;
    payload.artifact_id = candidate.artifact_id;
  } else {
    payload.folder_name = valueOf("mop_execution_repo_folder");
    if (!payload.folder_name) throw new Error("Enter an artifact repo folder name.");
  }
  const response = await fetch("/api/mop-execution/validate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function renderAgentValidationResult(result) {
  if (result?.run_id) setActive(result.run_id);
  if (result?.correlation_id && correlationInput) correlationInput.value = result.correlation_id;
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const failures = result.failures || [];
  const warnings = result.warnings || [];
  const validation = result.validation || {};
  const missing = result.missing_capabilities || [];
  const lines = [
    `# MoP Execution Agent Validation: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || "not created"}`,
    `- Correlation ID: ${result.correlation_id || valueOf("mop_execution_correlation_id")}`,
    `- Bundle ID: ${result.bundle_id || "not assigned"}`,
    `- Target namespace: ${result.target_namespace || valueOf("mop_execution_target_namespace")}`,
    "",
    "## Agent Checks",
    `- Health: ${result.agent_health ? "checked" : "not checked"}`,
    `- Readiness: ${result.agent_readiness ? "checked" : "not checked"}`,
    `- Capabilities: ${missing.length ? `missing ${missing.join(", ")}` : result.agent_capabilities ? "complete" : "not checked"}`,
    "",
    "## Validation",
    validation && Object.keys(validation).length ? JSON.stringify(scrub(validation), null, 2) : "No validation payload returned.",
    "",
    "## Failures",
    failures.length ? failures.map((item) => `- ${item}`).join("\n") : "- None",
    "",
    "## Warnings",
    warnings.length ? warnings.map((item) => `- ${item}`).join("\n") : "- None",
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  if (artifactLinks) {
    artifactLinks.innerHTML = `<span class="badge ${result.valid ? "text-bg-success" : "text-bg-danger"}">${result.valid ? "Agent validation passed" : "Agent validation failed"}</span>`;
  }
  if (result.valid) {
    setStatus("running");
    progressPanel?.classList.remove("is-idle", "is-working", "is-complete", "is-failed");
    progressPanel?.classList.add("is-complete");
    setText(spherePhase, "Bundle validated");
    setText(sphereTitle, "Ready for dry-run job creation.");
    mark("agent_health", "success", "Health, readiness, and required capabilities are available.");
    mark("bundle_validate", "success", result.summary || "Execution agent validated the bundle.");
    setText(formStatus, "Bundle validated by MoP Execution Agent. Ready for Phase G dry-run job creation.");
  } else {
    setStatus("failed");
    setVisual("failed");
    if (missing.length) mark("agent_health", "failed", `Missing required capabilities: ${missing.join(", ")}`);
    mark("bundle_validate", "failed", result.summary || "Execution agent validation failed.");
    setText(formStatus, result.summary || "Agent validation failed. Resolve the findings below.");
  }
  renderActivity();
  renderSafeFromEvents(true);
  void loadTransactions();
}
async function runDryRun(validationResult) {
  if (!validationResult?.run_id) throw new Error("MoP Execution run_id is required for dry-run.");
  if (!validationResult?.bundle_id) throw new Error("MoP Execution bundle_id is required for dry-run.");
  const payload = {
    run_id: validationResult.run_id,
    bundle_id: validationResult.bundle_id,
    target_namespace: validationResult.target_namespace || valueOf("mop_execution_target_namespace") || "agent-testing",
    correlation_id: validationResult.correlation_id || valueOf("mop_execution_correlation_id"),
    execution_mode: valueOf("mop_execution_mode") || "approved_mutation",
    model_profile: valueOf("model_profile"),
    ...twinGateRequestFields(),
  };
  const response = await fetch("/api/mop-execution/dry-run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function renderDryRunResult(result) {
  if (result?.run_id) setActive(result.run_id);
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const observationPayload = result.observations && Object.keys(result.observations).length ? JSON.stringify(scrub(result.observations), null, 2) : "No observations returned yet.";
  const decisionPayload = result.decision_required ? JSON.stringify(scrub(result.decision_required), null, 2) : "No decision-required context.";
  const reportsPayload = result.reports || {};
  const reportSummary = reportsPayload.summary || "No dry-run report metadata returned yet.";
  const resourcesPayload = JSON.stringify(scrub(reportsPayload.resources || []), null, 2);
  const policyPayload = JSON.stringify(scrub({policy_gates: reportsPayload.policy_gates || [], warnings: reportsPayload.warnings || []}), null, 2);
  const fingerprintPayload = JSON.stringify(reportsPayload.command_fingerprints || [], null, 2);
  const lines = [
    `# MoP Execution Dry-Run: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || "not available"}`,
    `- Bundle ID: ${result.bundle_id || "not available"}`,
    `- Dry-run job ID: ${result.dry_run_job_id || "not assigned"}`,
    `- Target namespace: ${result.target_namespace || valueOf("mop_execution_target_namespace")}`,
    `- Current state: ${result.current_state || result.status || "unknown"}`,
    `- Current phase: ${result.current_phase || "dry_run"}`,
    `- Mutation controls enabled: ${result.mutation_controls_enabled ? "yes" : "no"}`,
    `- Approval required: ${result.approval_required === false ? "no" : "yes"}`,
    "",
    "## Dry-Run Report Summary",
    reportSummary,
    "",
    "## Resource Change Preview",
    resourcesPayload,
    "",
    "## Policy Gates and Warnings",
    policyPayload,
    "",
    "## Command Fingerprints",
    fingerprintPayload,
    "",
    "## Observations and Audit Events",
    observationPayload,
    "",
    "## Decision Context",
    decisionPayload,
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");

  if (result.dry_run_succeeded) {
    const autoApproved = approvedMutationMode();
    setStatus(autoApproved ? "running" : "waiting_for_approval");
    setVisual("working");
    setText(spherePhase, "Dry-run completed");
    setText(sphereTitle, autoApproved ? "Submitting approved mutation gate." : "Review dry-run observations before approval.");
    mark("dry_run_job", "success", `Dry-run job ${result.dry_run_job_id || "created"} was created with mutation disabled.`);
    mark("dry_run", "success", result.summary || "Dry-run succeeded.");
    mark("observations", "success", "Dry-run observations and audit events are available in the result panel and logs.");
    mark("dry_run_report", "success", "Dry-run report metadata, resources, policy gates, and command fingerprints are available.");
    mark("approval", "running", autoApproved ? "Approved mutation mode selected; approval will be submitted automatically before mutation." : "Approval gate is next; mutation remains disabled.");
    hideDecisionCard();
    renderReportLinks(reportsPayload);
    renderApprovalCard(result);
    if (autoApproved) {
      setText(formStatus, "Dry-run succeeded. Approved mutation mode is submitting approval automatically.");
      void submitApprovedMutationApproval(result);
    } else {
      setText(formStatus, "Dry-run succeeded. Approval gate is next; mutation controls remain disabled.");
    }
  } else if (["decision_required", "paused"].includes(String(result.current_state || result.status || "").toLowerCase())) {
    setStatus("running");
    setVisual("working");
    setText(spherePhase, "Decision required");
    setText(sphereTitle, "Review the bounded decision context.");
    mark("dry_run_job", "success", `Dry-run job ${result.dry_run_job_id || "created"} was created.`);
    mark("dry_run", "recovered", result.summary || "Dry-run paused for a decision.");
    mark("decision", "running", "Execution agent requires a bounded operator instruction.");
    renderDecisionCard(result);
    if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-warning">Decision required</span> <span class="badge text-bg-secondary">Mutation disabled</span>';
    setText(formStatus, result.summary || "Dry-run paused for a decision-required context.");
  } else {
    setStatus("failed");
    setVisual("failed");
    mark("dry_run", "failed", result.summary || "Dry-run failed.");
    mark("complete", "failed", result.summary || "Dry-run failed before mutation could be considered.");
    hideDecisionCard();
    if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-danger">Dry-run failed</span> <span class="badge text-bg-secondary">Mutation disabled</span>';
    setText(formStatus, result.summary || "Dry-run failed. Resolve the observations before retrying.");
  }
  renderActivity();
  renderSafeFromEvents(true);
  refreshOpenAutonomyModal();
  void loadTransactions();
}
async function submitDecisionInstruction(event) {
  event.preventDefault();
  if (!decisionCard) return;
  let scope = {};
  try {
    scope = decisionScope?.value?.trim() ? JSON.parse(decisionScope.value) : {};
  } catch (_error) {
    setDecisionFeedback("Scope JSON is not valid.", true);
    return;
  }
  const payload = {
    run_id: decisionCard.dataset.runId || activeRunId,
    job_id: decisionCard.dataset.jobId || null,
    action: decisionAction?.value || "continue",
    instruction: decisionInstruction?.value?.trim() || "",
    rationale: decisionRationale?.value?.trim() || "",
    scope,
    correlation_id: valueOf("mop_execution_correlation_id"),
    model_profile: valueOf("model_profile"),
  };
  const errors = validateDecisionInstruction(payload);
  if (!payload.run_id) errors.push("No active MoP Execution run is selected.");
  if (errors.length) {
    setDecisionFeedback(errors.join(" "), true);
    return;
  }
  if (decisionSubmit) decisionSubmit.disabled = true;
  setDecisionFeedback("Submitting bounded instruction...");
  try {
    const response = await fetch("/api/mop-execution/instruction", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const result = await response.json();
    renderInstructionResult(result);
  } catch (error) {
    setDecisionFeedback(`Instruction failed: ${error.message}`, true);
    if (decisionSubmit) decisionSubmit.disabled = false;
  }
}

function renderInstructionResult(result) {
  (result.events || []).forEach((event) => processEvent(event, {live: true, reveal: true, hydrateApproval: false}));
  const lines = [
    `# MoP Execution Instruction: ${result.status || "unknown"}`,
    "",
    `- Run ID: ${result.run_id || activeRunId || "not available"}`,
    `- Dry-run job ID: ${result.dry_run_job_id || decisionCard?.dataset.jobId || "not available"}`,
    `- Accepted: ${result.accepted ? "yes" : "no"}`,
    `- Resumed: ${result.resumed ? "yes" : "no"}`,
    `- Mutation controls enabled: ${result.mutation_controls_enabled ? "yes" : "no"}`,
    "",
    "## Summary",
    result.summary || (result.errors || []).join("\n") || "No instruction summary returned.",
    "",
    "## Agent Response",
    JSON.stringify(scrub({instruction_response: result.instruction_response, resume_response: result.resume_response, observations: result.observations, errors: result.errors}), null, 2),
  ];
  if (finalReport) finalReport.textContent = lines.join("\n");
  if (result.accepted) {
    setDecisionFeedback(result.summary || "Instruction accepted and job resumed.");
    setText(decisionStatus, "accepted");
    mark("decision", "success", result.summary || "Instruction accepted by the execution agent.");
    mark("dry_run", "running", "Dry-run resumed after bounded instruction.");
    setStatus("running");
    setVisual("working");
    setText(spherePhase, "Instruction accepted");
    setText(sphereTitle, "Dry-run resumed with mutation disabled.");
    if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-success">Instruction accepted</span> <span class="badge text-bg-secondary">Mutation disabled</span>';
    if (result.run_id && !es) connect(result.run_id);
  } else {
    setDecisionFeedback((result.errors || [result.summary || "Instruction was not accepted."]).join(" "), true);
    mark("decision", "recovered", "Instruction was not accepted; decision gate remains active.");
    if (decisionSubmit) decisionSubmit.disabled = false;
    if (artifactLinks) artifactLinks.innerHTML = '<span class="badge text-bg-warning">Instruction not accepted</span> <span class="badge text-bg-secondary">Mutation disabled</span>';
  }
  renderActivity();
  renderSafeFromEvents(true);
  refreshOpenAutonomyModal();
  void loadTransactions();
}
async function startShell(event) {
  event.preventDefault();
  const target = valueOf("mop_execution_target_namespace") || "agent-testing";
  const mode = valueOf("mop_execution_mode") || "approved_mutation";
  const source = valueOf("mop_execution_bundle_source") || "activity_run";
  stampCorrelationId();
  if (mode === "cleanup_revert" && !activeRunId) {
    resetView("No Bundle Execution run is selected.");
    setStatus("Idle");
    setVisual("idle");
    setCleanupFeedback("Select an existing Bundle Execution run before cleanup/revert, or choose Approved mutation for a fresh run.", true);
    setText(formStatus, "Cleanup/revert needs an existing execution run. Switch Execution Mode to Approved mutation to start fresh.");
    collapseRail(true);
    return;
  }
  resetView("Preparing MoP execution controls..."); setStatus("planning"); setVisual("working"); revealRail("Creating execution plan");
  if (mode === "cleanup_revert") {
    setText(formStatus, "Submitting cleanup/revert for the selected execution run...");
    await runCleanupRevert();
    return;
  }
  addStageWorking("creating_plan", "Creating execution plan", `Bundle source ${source}, target namespace ${target}, mode ${mode}, and approval guardrails are being arranged before execution orchestration.`, "shell:plan");
  mark("intake", "success", "Execution inputs captured for the current page session.");
  mark("preflight", "running", "Validating local bundle, required files, namespace safety, Secret exposure, and cluster-scoped actions.");
  renderActivity(); setText(formStatus, "Running MoP bundle preflight...");
  addTimeline({event_type: "execution_shell_prepared", message: "MoP Execution page shell prepared", sequence: 1, payload: {bundle_source: source, target_namespace: target, execution_mode: mode, model_profile: valueOf("model_profile")}});
  let requestPhase = {stage: "preflight", label: "Preflight request", beforeAgent: true};
  try {
    const result = await runPreflight();
    renderPreflightResult(result);
    if (result.valid) {
      setStatus("running");
      setVisual("working");
      mark("agent_health", "running", "Calling MoP Execution Agent health, readiness, and capabilities endpoints.");
      mark("bundle_validate", "pending", "Waiting for agent-side bundle validation.");
      addStageWorking("agent_health", "Checking execution agent", "Calling healthz, readyz, and capabilities before submitting the selected bundle.", "phase-f:agent-health");
      renderActivity();
      setText(formStatus, "Checking MoP Execution Agent and validating bundle...");
      requestPhase = {stage: "bundle_validate", label: "Execution-agent validation", beforeAgent: false};
      const validation = await runAgentValidation(result);
      renderAgentValidationResult(validation);
      if (validation.valid) {
        connect(validation.run_id);
        mark("dry_run_job", "running", "Creating a dry-run-only job with idempotency protection.");
        addStageWorking("dry_run_job", "Creating dry-run job", "The execution agent is creating a dry-run-only job. Mutation controls remain disabled.", "phase-g:dry-run-job");
        renderActivity();
        setText(formStatus, "Creating and starting dry-run job...");
        requestPhase = {stage: "dry_run", label: "Dry-run execution", beforeAgent: false};
        const dryRun = await runDryRun(validation);
        renderDryRunResult(dryRun);
      }
    } else {
      setStatus("failed");
      setVisual("failed");
      setText(formStatus, "Preflight failed. Resolve the blocking findings below.");
      renderActivity();
    }
  } catch (error) {
    const detail = requestErrorMessage(error, requestPhase.label);
    if (requestPhase.beforeAgent) {
      const result = {
        valid: false,
        status: "failed",
        target_namespace: target,
        correlation_id: valueOf("mop_execution_correlation_id"),
        summary: "Preflight request could not reach the ESDA local API.",
        bundle: {},
        metadata: {},
        checks: [{status: "failed", label: requestPhase.label, detail}],
        failures: [detail],
        warnings: [],
      };
      renderPreflightResult(result);
      setStatus("failed");
      setVisual("failed");
      setText(formStatus, detail);
      return;
    }
    renderPhaseRequestFailure(requestPhase, detail);
  }
}
async function initSphere() {
  if (!sphereCanvas || !progressPanel) return;
  const sphereStage = $("mop-sphere-stage");
  const sphereDock = $("mop-sphere-dock");
  if (!sphereStage || !sphereDock) return;

  try {
    const THREE = await import("https://unpkg.com/three@0.165.0/build/three.module.js");

    let running = true;
    let thinkingMix = 0;

    const renderer = new THREE.WebGLRenderer({
      canvas: sphereCanvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xffffff, 0);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    camera.position.set(0, 0.22, 7.6);

    const root = new THREE.Group();
    scene.add(root);

    const ambient = new THREE.AmbientLight(0xffffff, 0.72);
    scene.add(ambient);

    const keyLight = new THREE.PointLight(0xff9aa8, 1.75, 16);
    keyLight.position.set(3.4, 2.8, 5.4);
    scene.add(keyLight);

    const goldLight = new THREE.PointLight(0xf1c977, 1.15, 12);
    goldLight.position.set(-3.7, -1.6, 3.4);
    scene.add(goldLight);

    const vertexShader = `
      uniform float uTime;
      uniform float uTwist;
      uniform float uPulse;
      varying vec3 vNormal;
      varying vec3 vViewPosition;
      varying vec3 vWorldPosition;

      mat2 rotate2d(float a) {
        float s = sin(a);
        float c = cos(a);
        return mat2(c, -s, s, c);
      }

      void main() {
        vec3 p = position;

        float wave = sin(p.y * 3.4 + uTime * 2.2) * 0.18;
        float ripple = sin(length(p.xy) * 4.2 - uTime * 2.7) * 0.045;
        float twist = p.y * uTwist + sin(uTime + p.x * 1.8) * 0.35;

        p.xz = rotate2d(twist) * p.xz;
        p.xy = rotate2d(wave) * p.xy;
        p += normal * (ripple + sin(uTime * 1.7) * uPulse);

        vec4 worldPosition = modelMatrix * vec4(p, 1.0);
        vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);

        vNormal = normalize(normalMatrix * normal);
        vViewPosition = -mvPosition.xyz;
        vWorldPosition = worldPosition.xyz;

        gl_Position = projectionMatrix * mvPosition;
      }
    `;

    const fragmentShader = `
      uniform float uTime;
      uniform float uOpacity;
      varying vec3 vNormal;
      varying vec3 vViewPosition;
      varying vec3 vWorldPosition;

      void main() {
        vec3 normal = normalize(vNormal);
        vec3 viewDir = normalize(vViewPosition);

        float facing = dot(normal, viewDir) * 0.5 + 0.5;
        float rim = pow(1.0 - max(dot(normal, viewDir), 0.0), 2.15);
        float stripe = smoothstep(0.47, 0.51, sin((vWorldPosition.y + vWorldPosition.x * 0.18) * 16.0 + uTime * 1.6) * 0.5 + 0.5);
        float liquid = sin((vWorldPosition.x - vWorldPosition.y) * 5.4 + uTime * 1.15) * 0.5 + 0.5;

        float shade = 0.08 + facing * 0.78 + rim * 0.30;
        shade -= stripe * 0.055;

        vec3 deepPlum = vec3(0.055, 0.035, 0.155);
        vec3 midViolet = vec3(0.330, 0.170, 0.520);
        vec3 warmCoral = vec3(0.980, 0.315, 0.300);
        vec3 softRose = vec3(0.980, 0.620, 0.700);
        vec3 champagne = vec3(0.945, 0.760, 0.440);

        vec3 color = mix(deepPlum, midViolet, clamp(shade, 0.0, 1.0));
        color = mix(color, warmCoral, rim * 0.34 + liquid * 0.055);
        color = mix(color, softRose, pow(facing, 4.0) * 0.18);
        color = mix(color, champagne, stripe * 0.10);
        color += stripe * vec3(0.075, 0.020, 0.070);

        gl_FragColor = vec4(color, uOpacity);
      }
    `;

    const loaderMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uTwist: { value: 2.2 },
        uPulse: { value: 0.045 },
        uOpacity: { value: 0.98 },
      },
      vertexShader,
      fragmentShader,
      transparent: true,
    });

    const sphere = new THREE.Mesh(new THREE.SphereGeometry(1.72, 128, 128), loaderMaterial);
    root.add(sphere);

    const fishnetMaterial = new THREE.MeshBasicMaterial({
      color: 0xffd6e4,
      transparent: true,
      opacity: 0.48,
      wireframe: true,
    });
    const fishnet = new THREE.Mesh(new THREE.SphereGeometry(1.735, 64, 64), fishnetMaterial);
    root.add(fishnet);

    const goldNetMaterial = new THREE.MeshBasicMaterial({
      color: 0xff8f77,
      transparent: true,
      opacity: 0.30,
      wireframe: true,
    });
    const goldNet = new THREE.Mesh(new THREE.SphereGeometry(1.755, 32, 32), goldNetMaterial);
    root.add(goldNet);

    const haloMaterial = new THREE.MeshBasicMaterial({
      color: 0xff6f75,
      transparent: true,
      opacity: 0.085,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const halo = new THREE.Mesh(new THREE.SphereGeometry(1.98, 64, 64), haloMaterial);
    root.add(halo);

    const rings = [];
    for (let i = 0; i < 4; i += 1) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(2.08 + i * 0.035, 0.006, 10, 260),
        new THREE.MeshBasicMaterial({
          color: i % 2 ? 0xff8f77 : 0xc5a4ff,
          transparent: true,
          opacity: i % 2 ? 0.20 : 0.15,
          blending: THREE.AdditiveBlending,
        })
      );
      ring.rotation.set(Math.PI * (0.20 + i * 0.12), Math.PI * (0.16 + i * 0.17), Math.PI * i * 0.22);
      root.add(ring);
      rings.push(ring);
    }

    const nodeGroup = new THREE.Group();
    root.add(nodeGroup);

    const nodeNames = ["NODE", "POD", "SVC", "PVC", "NS", "ING", "CM", "JOB", "API", "LOG"];
    const labels = [];
    const nodes = [];
    const dotGeometry = new THREE.SphereGeometry(0.045, 18, 18);
    const dotMaterial = new THREE.MeshBasicMaterial({ color: 0xff7f86, transparent: true, opacity: 0.88 });
    const lineMaterial = new THREE.LineBasicMaterial({ color: 0xd8bbff, transparent: true, opacity: 0.18 });
    const radius = 2.75;

    nodeNames.forEach((nodeName, i) => {
      const angle = (i / nodeNames.length) * Math.PI * 2;
      const y = Math.sin(i * 1.7) * 0.55;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const dot = new THREE.Mesh(dotGeometry, dotMaterial.clone());
      dot.position.set(x, y, z);
      dot.userData = { base: dot.position.clone(), phase: i * 0.6 };
      nodeGroup.add(dot);
      nodes.push(dot);

      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(x, y, z),
        new THREE.Vector3(x * 0.33, y * 0.2, z * 0.33),
        new THREE.Vector3(0, 0, 0),
      ]);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(32)), lineMaterial.clone());
      line.material.opacity = 0.07 + (i % 3) * 0.035;
      nodeGroup.add(line);

      const label = document.createElement("div");
      label.className = "release-node-label";
      label.textContent = nodeName;
      sphereStage.appendChild(label);
      labels.push({ el: label, target: dot });
    });

    const particlesGeometry = new THREE.BufferGeometry();
    const particleCount = 520;
    const positions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i += 1) {
      const r = 2.25 + Math.random() * 1.75;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi);
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    particlesGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const particles = new THREE.Points(
      particlesGeometry,
      new THREE.PointsMaterial({ color: 0xff9aa8, size: 0.012, transparent: true, opacity: 0.34, depthWrite: false })
    );
    root.add(particles);

    function resizeRenderer() {
      const rect = sphereDock.getBoundingClientRect();
      const size = Math.max(64, Math.floor(Math.min(rect.width, rect.height)));
      renderer.setSize(size, size, false);
      camera.aspect = 1;
      camera.updateProjectionMatrix();
    }

    function updateLabels() {
      const stageRect = sphereStage.getBoundingClientRect();
      const dockRect = sphereDock.getBoundingClientRect();
      const vector = new THREE.Vector3();
      labels.forEach(({ el, target }) => {
        target.getWorldPosition(vector);
        vector.project(camera);
        const x = dockRect.left - stageRect.left + (vector.x * 0.5 + 0.5) * dockRect.width;
        const y = dockRect.top - stageRect.top + (-vector.y * 0.5 + 0.5) * dockRect.height;
        const visible = vector.z < 1 && Math.abs(vector.x) < 1.08 && Math.abs(vector.y) < 1.08;
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
        el.style.opacity = visible ? "1" : "0";
      });
    }

    const resizeObserver = new ResizeObserver(resizeRenderer);
    resizeObserver.observe(sphereDock);
    window.addEventListener("resize", resizeRenderer);

    const clock = new THREE.Clock();

    function animate() {
      requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();
      const isThinking = progressPanel.classList.contains("is-working");

      if (running) {
        thinkingMix += ((isThinking ? 1 : 0) - thinkingMix) * 0.07;
        loaderMaterial.uniforms.uTime.value = elapsed;
        loaderMaterial.uniforms.uTwist.value = 2.25 + Math.sin(elapsed * 0.7) * 0.55 + thinkingMix * 1.25;
        loaderMaterial.uniforms.uPulse.value = 0.032 + (Math.sin(elapsed * 2.2) * 0.5 + 0.5) * 0.035 + thinkingMix * 0.035;

        root.rotation.y = elapsed * (0.22 + thinkingMix * 0.20);
        root.rotation.x = Math.sin(elapsed * 0.42) * 0.09;
        sphere.rotation.z = elapsed * (0.18 + thinkingMix * 0.10);
        fishnet.rotation.y = elapsed * 0.18;
        fishnet.rotation.z = -elapsed * 0.10;
        goldNet.rotation.y = -elapsed * 0.15;
        goldNet.rotation.x = Math.sin(elapsed * 0.48) * 0.10;

        halo.scale.setScalar(1.0 + Math.sin(elapsed * 1.9) * 0.03 + thinkingMix * 0.10);
        haloMaterial.opacity = 0.075 + thinkingMix * 0.040;
        fishnetMaterial.opacity = 0.48 + thinkingMix * 0.10;
        goldNetMaterial.opacity = 0.30 + thinkingMix * 0.10;

        rings.forEach((ring, i) => {
          ring.rotation.x += 0.0022 + i * 0.0008 + thinkingMix * 0.0015;
          ring.rotation.y -= 0.0014 + i * 0.0005 + thinkingMix * 0.001;
        });

        nodeGroup.rotation.y = elapsed * -0.16;
        particles.rotation.y = elapsed * 0.055;
        particles.rotation.x = Math.sin(elapsed * 0.25) * 0.12;

        nodes.forEach((dot, i) => {
          const base = dot.userData.base;
          const amp = 0.075 + (i % 4) * 0.011 + thinkingMix * 0.035;
          dot.position.set(
            base.x + Math.sin(elapsed * 1.3 + dot.userData.phase) * amp,
            base.y + Math.cos(elapsed * 1.7 + dot.userData.phase) * amp,
            base.z + Math.sin(elapsed * 1.1 + dot.userData.phase) * amp
          );
          dot.scale.setScalar(1 + Math.sin(elapsed * 2.4 + i) * 0.22 + thinkingMix * 0.18);
        });
      }

      updateLabels();
      renderer.render(scene, camera);
    }

    resizeRenderer();
    animate();

    window.mopSphereRuntime = {
      resize: resizeRenderer,
      pause: () => { running = false; },
      play: () => { running = true; },
    };
  } catch (error) {
    console.warn("Release sphere failed to initialize", error);
    progressPanel.classList.add("sphere-fallback");
  }
}


async function boot() {
  setVisual("idle");
  initSphere();
  bindSidebar();
  bindRail();
  stampCorrelationId();
  updateSourceVisibility();
  await loadBundleCandidates();
  await loadTwinGate();
  resetView("No MoP execution run yet.");
  setStatus("Idle");
  setVisual("idle");
  const txs = await loadTransactions();
  const storedId = (() => { try { return localStorage.getItem(activeKey); } catch (_e) { return null; } })();
  const stored = txs.find((item) => item.run_id === storedId && !terminal(item.status));
  const active = txs.find((item) => !terminal(item.status));
  const runId = stored?.run_id || active?.run_id;
  normalizeFreshExecutionMode();
  if (runId) await openRun(runId);
}
bundleSource?.addEventListener("change", () => { updateSourceVisibility(); stampCorrelationId(); void loadTwinGate(); });
activityRunSelect?.addEventListener("change", () => { renderBundleMetadata(); void loadTwinGate(); });
repoFolderInput?.addEventListener("input", () => renderBundleMetadata());
bundleFileInput?.addEventListener("change", () => renderBundleMetadata());
targetNamespaceSelect?.addEventListener("change", () => { stampCorrelationId(); renderBundleMetadata(); void loadTwinGate(); });
copyProgressButton?.addEventListener("click", async () => { try { await copyText(timelineText()); setText(copyProgressStatus, "Progress copied."); } catch (err) { setText(copyProgressStatus, `Copy failed: ${err.message}`); } });
copyLogsButton?.addEventListener("click", async () => { try { await copyText(logsText()); setText(copyProgressStatus, "Logs copied."); } catch (err) { setText(copyProgressStatus, `Copy failed: ${err.message}`); } });
$("mop_execution_mode")?.addEventListener("change", updateExecutionGateControl);
startNewButton?.addEventListener("click", startNewRun);
autonomyMaximize?.addEventListener("click", updateAutonomyModal);
autonomyModal?.addEventListener("shown.bs.modal", updateAutonomyModal);
autonomyModalCopyJson?.addEventListener("click", async () => { try { await copyText(logsText()); updateAutonomyModal(); } catch (err) { setText(copyProgressStatus, `Copy failed: ${err.message}`); } });
form?.addEventListener("submit", startShell);
decisionInstructionForm?.addEventListener("submit", submitDecisionInstruction);
approvalForm?.addEventListener("submit", submitApproval);
mutationStart?.addEventListener("click", startMutation);
cleanupStart?.addEventListener("click", runCleanupRevert);
window.addEventListener("pageshow", () => setTimeout(normalizeFreshExecutionMode, 0));
boot();
