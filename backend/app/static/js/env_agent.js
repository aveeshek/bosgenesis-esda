const $ = (id) => document.getElementById(id);

const chatForm = $("env-chat-form");
const chatInput = $("env-chat-input");
const chatTranscript = $("env-chat-transcript");
const statusBadge = $("run-status");
const progressPanel = $("env-progress-panel");
const sphereCanvas = $("env-sphere-canvas");
const sphereStage = $("env-sphere-stage");
const sphereDock = $("env-sphere-dock");
const spherePhase = $("env-sphere-phase");
const sphereTitle = $("env-sphere-title");
const workingPanel = $("ephemeral-working-panel");
const workingStream = $("ephemeral-working-stream");
const safeList = $("safe-summary-list");
const timeline = $("timeline");
const copyProgressButton = $("copy-progress");
const copyLogsButton = $("copy-logs");
const copyProgressStatus = $("copy-progress-status");
const autonomyModal = $("env-autonomy-modal");
const autonomyMaximize = $("autonomy-maximize");
const autonomyModalLive = $("autonomy-modal-live");
const autonomyModalSummary = $("autonomy-modal-summary");
const autonomyModalJson = $("autonomy-modal-json");
const autonomyModalCopyJson = $("autonomy-modal-copy-json");
const txToggle = $("transaction-sidebar-toggle");
const txSidebar = $("transaction-sidebar");
const txClose = $("transaction-sidebar-close");
const txBackdrop = $("transaction-sidebar-backdrop");
const txList = $("transaction-list");
const txStatus = $("transaction-sidebar-status");
const txClearAll = $("transaction-clear-all");
const remediationCard = $("env-remediation-card");
const remediationTitle = $("env-remediation-title");
const remediationBody = $("env-remediation-body");
const remediationStatus = $("env-remediation-status");
const remediationApprove = $("env-remediation-approve");
const remediationFeedback = $("env-remediation-feedback");

let contract = null;
let events = [];
let activeRunId = null;
let activeSessionId = null;

const defs = [
  ["intake", "Intake", "Prompt, model, user, namespace, and mode are captured."],
  ["scope", "Scope", "Namespace, environment scope, resource kind, and role boundaries are checked."],
  ["classify", "Classify", "Intent is classified as diagnostic, propose-only, remediation, follow-up, or unsafe."],
  ["plan", "Plan", "A typed MCP tool plan is prepared with risk and stop-condition checks."],
  ["inspect", "Inspect", "Read-only Kubernetes and Helm evidence is gathered."],
  ["correlate", "Correlate", "Evidence is correlated across pods, events, logs, services, PVCs, and Helm releases."],
  ["diagnose", "Diagnose", "The LLM produces a safe diagnosis summary with confidence and missing evidence."],
  ["propose", "Propose", "A bounded remediation plan is proposed when requested and policy allows it."],
  ["approve", "Approve", "High-risk remediation waits for scoped human approval."],
  ["execute", "Execute", "Approved typed MCP actions execute with redaction and idempotency controls."],
  ["verify", "Verify", "Post-action evidence is checked before marking success."],
  ["report", "Report", "A safe report, logs, and Activity-visible summary are saved."],
  ["complete", "Complete", "Run reaches completed, blocked, or needs-review state."],
];

let activity = Object.fromEntries(defs.map(([id, label, hint]) => [id, {status: "pending", label, detail: hint}]));

function setText(el, text) {
  if (el) el.textContent = text || "";
}

function selectedModelLabel() {
  const select = $("model_profile");
  return select?.selectedOptions?.[0]?.textContent?.trim() || "selected model";
}

function selectedModeLabel(mode) {
  const labels = {
    diagnostic_only: "diagnostic",
    propose_only: "proposal",
    approval_gated_remediation: "approval-gated remediation",
  };
  return labels[mode] || "prompt-selected";
}

function escapeRegex(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function inferNamespaceFromText(text) {
  const prompt = String(text || "");
  const patterns = [
    /\bin\s+([a-z0-9][a-z0-9-]*)\s+namespace\b/i,
    /\b-n\s+([a-z0-9][a-z0-9-]*)\b/i,
    /\b(?:namespace|ns)\s*[:=]\s*([a-z0-9][a-z0-9-]*)\b/i,
    /\b(?:namespace|ns)\s+(?:named|called)\s+([a-z0-9][a-z0-9-]*)\b/i,
  ];
  for (const pattern of patterns) {
    const match = prompt.match(pattern);
    if (match?.[1]) return match[1];
  }
  const lowered = prompt.toLowerCase();
  for (const namespace of contract?.namespaces || []) {
    const expression = new RegExp(`(^|[^a-z0-9-])${escapeRegex(namespace.toLowerCase())}([^a-z0-9-]|$)`);
    if (expression.test(lowered)) return namespace;
  }
  return null;
}

function inferModeFromText(text) {
  const lowered = String(text || "").toLowerCase();
  if (/(\bfix\b|\brepair\b|\brestart\b|\bscale\b|\bpatch\b|\bapply\b|\bcreate\b|\bdelete\b|\bremove\b|\buninstall\b|\brollback\b|\binstall\b|\bupgrade\b|\bdeploy\b|\bremediate\b|\bheal\b)/.test(lowered)) {
    return "approval_gated_remediation";
  }
  if (/(\bpropose\b|\brecommend\b|\bplan\b|\bwhat should\b)/.test(lowered)) {
    return "propose_only";
  }
  return "diagnostic_only";
}
function setStatus(value, tone = "secondary") {
  if (!statusBadge) return;
  statusBadge.textContent = value;
  statusBadge.className = `badge mb-2 align-self-start text-bg-${tone}`;
}

function setVisual(state) {
  progressPanel?.classList.remove("is-idle", "is-working", "is-complete", "is-failed");
  progressPanel?.classList.add(`is-${state}`);
  const copy = {
    idle: ["Ready for environment reasoning", ""],
    working: ["Thinking through environment evidence", "Runtime guardrails are being prepared."],
    complete: ["Environment answer ready", "Review the formatted environment response."],
    failed: ["Run needs review", "Environment reasoning stopped before completion."],
  }[state] || ["Ready for environment reasoning", ""];
  setText(spherePhase, copy[0]);
  setText(sphereTitle, copy[1]);
  setTimeout(() => window.envSphereRuntime?.resize?.(), 80);
}

function resetActivity() {
  activity = Object.fromEntries(defs.map(([id, label, hint]) => [id, {status: "pending", label, detail: hint}]));
  renderActivity();
}

function mark(id, status, detail) {
  if (!activity[id]) return;
  activity[id] = {...activity[id], status, detail: detail || activity[id].detail};
  renderActivity();
}

function renderActivity() {}

function timelineText() {
  return events.length ? JSON.stringify(events, null, 2) : "No environment events yet.";
}

function logsText() {
  return timelineText();
}

function addEvent(eventType, message, payload = {}) {
  const event = {
    index: events.length + 1,
    sequence: events.length + 1,
    event_type: eventType,
    message,
    payload,
  };
  events.push(event);
  if (timeline) {
    const item = document.createElement("li");
    item.innerHTML = `<strong>${event.sequence}. ${event.event_type}: ${event.message}</strong><pre></pre>`;
    item.querySelector("pre").textContent = JSON.stringify(payload, null, 2);
    timeline.appendChild(item);
  }
  updateAutonomyModal();
  return event;
}

function addWorking(label, text) {
  if (!workingStream || !workingPanel) return;
  if (workingPanel.classList.contains("is-empty")) {
    workingStream.innerHTML = "";
    workingPanel.classList.remove("is-empty");
  }
  const item = document.createElement("article");
  item.className = "working-note-item";
  item.innerHTML = '<div class="working-note-label"></div><p></p>';
  item.querySelector(".working-note-label").textContent = label;
  item.querySelector("p").textContent = text;
  workingStream.appendChild(item);
  updateAutonomyModal();
}

function renderSafeSummaries(items) {
  if (!safeList) return;
  safeList.hidden = false;
  safeList.innerHTML = "";
  items.forEach((item, index) => {
    const row = document.createElement("article");
    row.className = "safe-summary-item";
    row.innerHTML = '<div class="safe-summary-label"></div><p></p>';
    row.querySelector(".safe-summary-label").textContent = `${index + 1}. ${item.label}`;
    row.querySelector("p").textContent = item.summary;
    safeList.appendChild(row);
  });
  updateAutonomyModal();
}

function clonePanelContent(source, emptyText) {
  const wrapper = document.createElement("div");
  const hasNotes = source && source.querySelector(".working-note-item, .safe-summary-item");
  if (hasNotes) {
    source.querySelectorAll(".working-note-item, .safe-summary-item").forEach((node) => wrapper.appendChild(node.cloneNode(true)));
  } else {
    wrapper.innerHTML = `<div class="stream-empty-state">${emptyText}</div>`;
  }
  return wrapper.innerHTML;
}

function updateAutonomyModal() {
  if (autonomyModalLive) autonomyModalLive.innerHTML = clonePanelContent(workingStream, "No live reasoning stream is available in this page session.");
  if (autonomyModalSummary) autonomyModalSummary.innerHTML = clonePanelContent(safeList, "No safe reasoning summaries are available yet.");
  if (autonomyModalJson) autonomyModalJson.textContent = logsText();
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const t = document.createElement("textarea");
  t.value = text;
  t.style.position = "fixed";
  t.style.opacity = "0";
  document.body.appendChild(t);
  t.select();
  document.execCommand("copy");
  t.remove();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function formatEnvChatContent(content) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let listItems = [];
  const flushList = () => {
    if (!listItems.length) return;
    html.push(`<ul>${listItems.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
    listItems = [];
  };
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }
    if (/^#{1,4}\s+/.test(trimmed)) {
      flushList();
      const level = Math.min(4, trimmed.match(/^#+/)[0].length);
      html.push(`<h${level}>${inlineMarkdown(trimmed.replace(/^#{1,4}\s+/, ""))}</h${level}>`);
    } else if (/^[-*]\s+/.test(trimmed)) {
      listItems.push(trimmed.replace(/^[-*]\s+/, ""));
    } else if (/^\|.*\|$/.test(trimmed)) {
      flushList();
      html.push(`<pre class="env-chat-table-line">${escapeHtml(trimmed)}</pre>`);
    } else {
      flushList();
      html.push(`<p>${inlineMarkdown(trimmed)}</p>`);
    }
  }
  flushList();
  return html.join("") || "<p>No answer was returned.</p>";
}
function appendChat(role, body) {
  if (!chatTranscript) return;
  const empty = chatTranscript.querySelector(".activity-chat-empty");
  if (empty) empty.remove();
  const row = document.createElement("article");
  row.className = `activity-chat-message ${role === "user" ? "is-user" : "is-assistant"}`;
  row.innerHTML = '<div class="activity-chat-message-label"></div><div class="activity-chat-message-body"></div>';
  row.querySelector(".activity-chat-message-label").textContent = role === "user" ? "You" : "Environment Chat";
  const bodyEl = row.querySelector(".activity-chat-message-body");
  if (role === "user") {
    bodyEl.textContent = body;
  } else {
    bodyEl.classList.add("env-formatted-answer");
    bodyEl.innerHTML = formatEnvChatContent(body);
  }
  chatTranscript.appendChild(row);
  chatTranscript.scrollTop = chatTranscript.scrollHeight;
}

function latestRemediationEvent(runEvents) {
  const interesting = new Set([
    "remediation_approval_requested",
    "remediation_approval_blocked",
    "remediation_approval_confirmed",
    "remediation_execution_blocked",
    "remediation_action_executed",
    "remediation_verified",
  ]);
  return [...(runEvents || [])].reverse().find((event) => interesting.has(event.event_type));
}

function hideRemediationCard() {
  remediationCard?.classList.add("d-none");
  if (remediationApprove) remediationApprove.disabled = true;
  if (remediationCard) {
    delete remediationCard.dataset.approvalId;
    delete remediationCard.dataset.runId;
  }
  setText(remediationFeedback, "");
}

function renderRemediationCard(snapshot) {
  if (!remediationCard) return;
  const event = latestRemediationEvent(snapshot?.events || []);
  if (!event) {
    hideRemediationCard();
    return;
  }
  const payload = event.payload || {};
  const approval = payload.approval || {};
  const request = payload.request || approval.request || {};
  const args = request.arguments || {};
  const nested = args.arguments || {};
  const action = payload.action || {};
  const status = payload.status || approval.status || event.event_type.replace("remediation_", "");
  const resource = args.resource || nested.resource_name || nested.release_name || action.target_name || "selected target";
  const toolName = request.tool_name || approval.tool_name || "typed MCP action";
  remediationCard.classList.remove("d-none");
  remediationCard.dataset.approvalId = approval.approval_id || payload.approval_id || "";
  remediationCard.dataset.runId = snapshot?.run?.run_id || payload.run_id || activeRunId || "";
  setText(remediationTitle, event.event_type === "remediation_approval_blocked" ? "Remediation blocked" : `Approve ${args.action || action.action_type || "remediation"}`);
  setText(remediationStatus, String(status).replaceAll("_", " "));
  if (remediationBody) {
    remediationBody.textContent = [
      `Tool: ${toolName}`,
      `Namespace: ${request.namespace || nested.namespace || "n/a"}`,
      `Target: ${resource}`,
      `Risk: ${payload.decision?.risk_level || approval.risk_level || action.risk || "high"}`,
      `Impact: ${payload.decision?.expected_impact || approval.expected_impact || "Typed remediation may alter workload state."}`,
      `Rollback: ${approval.rollback_note || (args.rollback_plan || []).join("; ") || "Review the generated rollback plan before approval."}`,
      `Verification: ${(args.verification_plan || action.verification_plan || []).join("; ") || "ESDA will run read-only verification after execution."}`,
    ].join("\n");
  }
  const pending = event.event_type === "remediation_approval_requested" && approval.status === "pending" && Boolean(approval.approval_id);
  if (remediationApprove) remediationApprove.disabled = !pending;
  setText(remediationFeedback, pending ? "Review the proposal, then approve and execute the typed MCP action." : payload.reason || "Remediation approval state recorded.");
}

async function approveAndExecuteRemediation() {
  const approvalId = remediationCard?.dataset.approvalId;
  const runId = remediationCard?.dataset.runId || activeRunId;
  if (!approvalId || !runId) return;
  if (remediationApprove) remediationApprove.disabled = true;
  setText(remediationFeedback, "Approving and executing typed MCP remediation...");
  try {
    const approvalResponse = await fetch(`/api/approvals/${approvalId}/approve`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({notes: "Approved from Environment Chat remediation card."}),
    });
    if (!approvalResponse.ok) throw new Error(`Approval HTTP ${approvalResponse.status}`);
    const executionResponse = await fetch("/api/env-agent/remediation/execute", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({run_id: runId, approval_id: approvalId, model_profile: $("model_profile")?.value || null}),
    });
    if (!executionResponse.ok) {
      let detail = `Execution HTTP ${executionResponse.status}`;
      try {
        const payload = await executionResponse.json();
        detail = payload.detail?.message || payload.detail || payload.error || detail;
      } catch (_error) {
        // Keep the HTTP detail.
      }
      throw new Error(detail);
    }
    const result = await executionResponse.json();
    renderSnapshot(result.snapshot, {preserveLiveStream: true});
    appendChat("assistant", result.summary || "Approved remediation completed.");
    setText(remediationFeedback, result.summary || "Approved remediation completed.");
    await loadTransactions();
  } catch (error) {
    setText(remediationFeedback, `Remediation failed: ${error.message}`);
    if (remediationApprove) remediationApprove.disabled = false;
    mark("execute", "failed", error.message);
  }
}
function isTerminalStatus(status) {
  return ["completed", "needs_review", "failed", "blocked", "proposal_ready", "needs_clarification", "stopped"].includes(String(status || "").toLowerCase());
}

function toneForStatus(status) {
  const value = String(status || "idle").toLowerCase();
  if (["completed", "proposal_ready"].includes(value)) return "success";
  if (["failed", "blocked"].includes(value)) return "danger";
  if (["needs_review", "needs_clarification"].includes(value)) return "warning";
  if (["running", "planning", "created"].includes(value)) return "primary";
  return "secondary";
}

function visualForStatus(status) {
  const value = String(status || "idle").toLowerCase();
  if (["completed", "proposal_ready"].includes(value)) return "complete";
  if (["failed", "blocked"].includes(value)) return "failed";
  if (["created", "running", "planning"].includes(value)) return "working";
  if (["needs_review", "needs_clarification"].includes(value)) return "failed";
  return "idle";
}

function renderTimelineEvents(runEvents) {
  events = (runEvents || []).map((event, index) => ({...event, sequence: event.sequence || index + 1}));
  if (!timeline) return;
  timeline.innerHTML = "";
  events.forEach((event) => {
    const item = document.createElement("li");
    item.innerHTML = `<strong>${event.sequence}. ${event.event_type}: ${event.message}</strong><pre></pre>`;
    item.querySelector("pre").textContent = JSON.stringify(event.payload || {}, null, 2);
    timeline.appendChild(item);
  });
  updateAutonomyModal();
}

function stageForEvent(event) {
  const type = event?.event_type;
  if (type === "safe_reasoning_summary") return event.payload?.stage;
  return {
    run_started: "intake",
    workflow_classified: "classify",
    plan_created: "plan",
    inspection_completed: "inspect",
    evidence_correlated: "correlate",
    diagnosis_completed: "diagnose",
    remediation_proposal_created: "propose",
    remediation_approval_requested: "approve",
    remediation_approval_blocked: "approve",
    remediation_approval_confirmed: "approve",
    remediation_execution_blocked: "execute",
    remediation_execution_started: "execute",
    remediation_action_executed: "execute",
    remediation_verified: "verify",
    verification_completed: "verify",
    recovery_recommendation: "report",
    run_completed: "complete",
    run_needs_review: "complete",
    run_failed: "complete",
  }[type];
}

function detailForEvent(event) {
  const payload = event?.payload || {};
  return payload.reasoning_summary || payload.summary || payload.diagnosis?.summary || payload.verification?.message || event?.message || "Activity updated.";
}

function applyActivityFromEvents(run, runEvents) {
  resetActivity();
  (runEvents || []).forEach((event) => {
    const stage = stageForEvent(event);
    if (!stage || !activity[stage]) return;
    let state = "success";
    if (event.event_type === "run_failed" || run?.status === "failed") state = "failed";
    if (event.event_type === "run_needs_review" || ["needs_review", "needs_clarification"].includes(run?.status)) state = "recovered";
    mark(stage, state, detailForEvent(event));
  });
}

function safeSummaryItemsFromEvents(runEvents) {
  const summaries = [];
  (runEvents || []).forEach((event) => {
    if (event.event_type === "safe_reasoning_summary") {
      summaries.push({
        label: event.payload?.stage_label || event.payload?.stage || "Summary",
        summary: event.payload?.reasoning_summary || event.message,
      });
    }
    (event.payload?.safe_reasoning_summaries || []).forEach((item) => {
      summaries.push({label: item.stage_label || item.stage || "Summary", summary: item.reasoning_summary || item.summary || "Safe summary recorded."});
    });
  });
  const seen = new Set();
  return summaries.filter((item) => {
    const key = `${item.label}:${item.summary}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderChatFromSnapshot(snapshot) {
  if (!chatTranscript) return;
  chatTranscript.innerHTML = "";
  const run = snapshot?.run || {};
  if (run.goal) appendChat("user", run.goal);
  if (run.final_report) appendChat("assistant", run.final_report);
  if (!run.goal && !run.final_report) {
    chatTranscript.innerHTML = '<div class="activity-chat-empty">Ask about pod health, restarts, services, Helm releases, or a bounded remediation proposal.</div>';
  }
}

function renderChatMessages(messages) {
  if (!chatTranscript) return;
  chatTranscript.innerHTML = "";
  (messages || []).forEach((message) => appendChat(message.role, message.content));
  if (!messages?.length) {
    chatTranscript.innerHTML = '<div class="activity-chat-empty">Ask about pod health, restarts, services, Helm releases, or a bounded remediation proposal.</div>';
  }
}

function renderSnapshot(snapshot, {preserveLiveStream = false} = {}) {
  if (!snapshot?.run) return;
  activeRunId = snapshot.run.run_id;
  if (snapshot.chat_session_id) activeSessionId = snapshot.chat_session_id;
  if (!preserveLiveStream) resetWorking();
  renderTimelineEvents(snapshot.events || []);
  setStatus(snapshot.run.status || "Idle", toneForStatus(snapshot.run.status));
  setVisual(visualForStatus(snapshot.run.status));
  const summaries = safeSummaryItemsFromEvents(snapshot.events || []);
  if (summaries.length) renderSafeSummaries(summaries);
  applyActivityFromEvents(snapshot.run, snapshot.events || []);
  renderChatFromSnapshot(snapshot);
  renderRemediationCard(snapshot);
}

function renderSessionSnapshot(sessionSnapshot, {preserveLiveStream = false} = {}) {
  if (!sessionSnapshot?.session) return;
  activeSessionId = sessionSnapshot.session.session_id;
  const latest = sessionSnapshot.latest_snapshot;
  if (latest) {
    latest.chat_session_id = activeSessionId;
    renderSnapshot(latest, {preserveLiveStream});
  } else {
    activeRunId = null;
    resetWorking();
    resetActivity();
    setVisual("idle");
    setStatus("Idle");
  }
  renderChatMessages(sessionSnapshot.messages || []);
}

async function openRun(runId, {closeSidebar = true} = {}) {
  const response = await fetch(`/api/runs/${runId}/snapshot`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const snapshot = await response.json();
  renderSnapshot(snapshot);
  if (closeSidebar) setSidebar(false);
}

async function openSession(sessionId, {closeSidebar = true, preserveLiveStream = false} = {}) {
  const response = await fetch(`/api/env-agent/sessions/${sessionId}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const snapshot = await response.json();
  renderSessionSnapshot(snapshot, {preserveLiveStream});
  if (closeSidebar) setSidebar(false);
}
async function handlePrompt(prompt) {
  const namespace = inferNamespaceFromText(prompt);
  const mode = inferModeFromText(prompt);
  const scope = "prompt";
  const modelProfile = $("model_profile")?.value || null;
  const model = selectedModelLabel();
  const namespaceLabel = namespace || "prompt/MCP-selected context";
  activeRunId = null;
  setVisual("working");
  setStatus("planning", "primary");
  mark("intake", "running", "Prompt captured for Environment Chat.");
  mark("scope", "running", `Runtime scope will be resolved from prompt and MCP response: ${namespaceLabel}.`);
  addWorking("00 / CREATING PLAN", `Creating a prompt-first execution plan. Context ${namespaceLabel}, mode ${selectedModeLabel(mode)}, model ${model}, and MCP/policy guardrails will decide the allowed boundary.`);
  try {
    const response = await fetch("/api/env-agent/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: prompt, namespace, mode, scope, model_profile: modelProfile, session_id: activeSessionId}),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail?.message || payload.detail || payload.error || detail;
      } catch (_error) {
        // Keep the HTTP status as the error detail.
      }
      throw new Error(detail);
    }
    const result = await response.json();
    activeSessionId = result.chat_session_id || activeSessionId;
    renderSnapshot(result.snapshot, {preserveLiveStream: true});
    if (activeSessionId) {
      await openSession(activeSessionId, {closeSidebar: false, preserveLiveStream: true});
    }
    await loadTransactions();
  } catch (error) {
    setVisual("failed");
    setStatus("failed", "danger");
    mark("complete", "failed", error.message);
    addWorking("16 / FAILED", `Environment Chat failed: ${error.message}`);
    appendChat("assistant", `Environment Chat failed: ${error.message}`);
  }
}

function resetWorking() {
  events = [];
  if (timeline) timeline.innerHTML = "";
  if (workingStream && workingPanel) {
    workingStream.innerHTML = '<div class="stream-empty-state">Live model and agent working notes will appear while this page is connected.</div>';
    workingPanel.classList.add("is-empty");
  }
  if (safeList) {
    safeList.hidden = true;
    safeList.innerHTML = '<div class="stream-empty-state">Safe environment summaries will be stored after runtime phases are implemented.</div>';
  }
  hideRemediationCard();
  updateAutonomyModal();
}

function setSidebar(open) {
  if (!txSidebar) return;
  document.body.classList.toggle("transaction-sidebar-open", open);
  txSidebar.setAttribute("aria-hidden", open ? "false" : "true");
  txToggle?.setAttribute("aria-expanded", open ? "true" : "false");
}

async function loadTransactions() {
  if (!txList || !txStatus) return [];
  try {
    const response = await fetch("/api/env-agent/sessions");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const rows = data.sessions || [];
    txList.innerHTML = "";
    txClearAll.disabled = rows.length === 0;
    if (!rows.length) {
      txStatus.textContent = "No Environment Chat sessions yet.";
      return rows;
    }
    txStatus.textContent = `${rows.length} Environment Chat session${rows.length === 1 ? "" : "s"}.`;
    rows.forEach((session) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `transaction-card${session.session_id === activeSessionId ? " is-active" : ""}`;
      card.innerHTML = '<div class="transaction-card-row"><span class="transaction-status-pill"></span></div><div class="transaction-card-title"></div><div class="transaction-card-subtitle"></div><div class="transaction-card-meta"></div>';
      card.querySelector(".transaction-status-pill").textContent = session.status || "unknown";
      card.querySelector(".transaction-card-title").textContent = session.title || session.goal || session.session_id;
      card.querySelector(".transaction-card-subtitle").textContent = session.namespace || "prompt-selected context";
      card.querySelector(".transaction-card-meta").textContent = `${new Date(session.updated_at || session.created_at).toLocaleString()} | ${session.message_count || 0} messages | ${session.run_count || 0} runs`;
      card.addEventListener("click", async () => {
        try {
          await openSession(session.session_id);
          await loadTransactions();
        } catch (error) {
          txStatus.textContent = `Could not restore Environment Chat session: ${error.message}`;
        }
      });
      txList.appendChild(card);
    });
    return rows;
  } catch (error) {
    txStatus.textContent = `Could not load Environment Chat sessions: ${error.message}`;
    return [];
  }
}
function bindSidebar() {
  const title = txSidebar?.querySelector("h2");
  const eyebrow = txSidebar?.querySelector(".transaction-sidebar-eyebrow");
  if (title) title.textContent = "Environment Chat Sessions";
  if (eyebrow) eyebrow.textContent = "Memory";
  txToggle?.addEventListener("click", () => { setSidebar(true); loadTransactions(); });
  txClose?.addEventListener("click", () => setSidebar(false));
  txBackdrop?.addEventListener("click", () => setSidebar(false));
  txClearAll?.addEventListener("click", async () => {
    txClearAll.disabled = true;
    try {
      const response = await fetch("/api/transactions/clear?workflow_type=env_agent", {method: "POST"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      activeRunId = null;
      activeSessionId = null;
      resetWorking();
      resetActivity();
      setVisual("idle");
      setStatus("Idle");
      if (chatTranscript) {
        chatTranscript.innerHTML = '<div class="activity-chat-empty">Ask about pod health, restarts, services, Helm releases, or a bounded remediation proposal.</div>';
      }
      await loadTransactions();
    } catch (error) {
      if (txStatus) txStatus.textContent = `Clear failed: ${error.message}`;
    }
  });
}

async function initSphere() {
  if (!sphereCanvas || !sphereStage || !sphereDock) return;
  try {
    const THREE = await import("https://unpkg.com/three@0.165.0/build/three.module.js");
    let thinkingMix = 0;
    const renderer = new THREE.WebGLRenderer({canvas: sphereCanvas, antialias: true, alpha: true, powerPreference: "high-performance"});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0xffffff, 0);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    camera.position.set(0, 0.18, 7.7);

    const root = new THREE.Group();
    scene.add(root);
    scene.add(new THREE.AmbientLight(0xffffff, 0.72));
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
    const sphereMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uTime: {value: 0},
        uTwist: {value: 2.2},
        uPulse: {value: 0.045},
        uOpacity: {value: 0.98},
      },
      vertexShader,
      fragmentShader,
      transparent: true,
    });
    const sphere = new THREE.Mesh(new THREE.SphereGeometry(1.72, 128, 128), sphereMaterial);
    root.add(sphere);

    const fishnetMaterial = new THREE.MeshBasicMaterial({color: 0xffd6e4, transparent: true, opacity: 0.50, wireframe: true});
    const fishnet = new THREE.Mesh(new THREE.SphereGeometry(1.735, 64, 64), fishnetMaterial);
    root.add(fishnet);
    const coralNetMaterial = new THREE.MeshBasicMaterial({color: 0xff8f77, transparent: true, opacity: 0.32, wireframe: true});
    const coralNet = new THREE.Mesh(new THREE.SphereGeometry(1.755, 32, 32), coralNetMaterial);
    root.add(coralNet);
    const haloMaterial = new THREE.MeshBasicMaterial({color: 0xff6f75, transparent: true, opacity: 0.085, depthWrite: false, blending: THREE.AdditiveBlending});
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
    const labels = [];
    const nodes = [];
    const nodeNames = ["NODE", "POD", "SVC", "PVC", "NS", "ING", "CM", "JOB", "API", "LOG"];
    const dotGeometry = new THREE.SphereGeometry(0.045, 18, 18);
    const dotMaterial = new THREE.MeshBasicMaterial({color: 0xff9aa8, transparent: true, opacity: 0.88});
    const lineMaterial = new THREE.LineBasicMaterial({color: 0xd8bbff, transparent: true, opacity: 0.16});
    nodeNames.forEach((nodeName, index) => {
      const angle = (index / nodeNames.length) * Math.PI * 2;
      const y = Math.sin(index * 1.7) * 0.55;
      const x = Math.cos(angle) * 2.72;
      const z = Math.sin(angle) * 2.72;
      const dot = new THREE.Mesh(dotGeometry, dotMaterial.clone());
      dot.position.set(x, y, z);
      dot.userData = {base: dot.position.clone(), phase: index * 0.6};
      nodeGroup.add(dot);
      nodes.push(dot);
      const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(x, y, z), new THREE.Vector3(x * 0.33, y * 0.2, z * 0.33), new THREE.Vector3(0, 0, 0)]);
      nodeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(32)), lineMaterial.clone()));
      const label = document.createElement("div");
      label.className = "release-node-label";
      label.textContent = nodeName;
      sphereStage.appendChild(label);
      labels.push({el: label, target: dot});
    });

    const particlePositions = new Float32Array(520 * 3);
    for (let i = 0; i < 520; i += 1) {
      const r = 2.25 + Math.random() * 1.75;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      particlePositions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      particlePositions[i * 3 + 1] = r * Math.cos(phi);
      particlePositions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const particlesGeometry = new THREE.BufferGeometry();
    particlesGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    const particles = new THREE.Points(
      particlesGeometry,
      new THREE.PointsMaterial({color: 0xff9aa8, size: 0.012, transparent: true, opacity: 0.34, depthWrite: false})
    );
    root.add(particles);

    function resizeRenderer() {
      const rect = sphereDock.getBoundingClientRect();
      const size = Math.max(72, Math.floor(Math.min(rect.width, rect.height)));
      renderer.setSize(size, size, false);
      camera.aspect = 1;
      camera.updateProjectionMatrix();
    }

    function updateLabels() {
      const stageRect = sphereStage.getBoundingClientRect();
      const dockRect = sphereDock.getBoundingClientRect();
      const vector = new THREE.Vector3();
      labels.forEach(({el, target}) => {
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
      const isThinking = progressPanel?.classList.contains("is-working");
      thinkingMix += ((isThinking ? 1 : 0) - thinkingMix) * 0.07;
      sphereMaterial.uniforms.uTime.value = elapsed;
      sphereMaterial.uniforms.uTwist.value = 2.25 + Math.sin(elapsed * 0.7) * 0.55 + thinkingMix * 1.25;
      sphereMaterial.uniforms.uPulse.value = 0.032 + (Math.sin(elapsed * 2.2) * 0.5 + 0.5) * 0.035 + thinkingMix * 0.035;
      root.rotation.y = elapsed * (0.22 + thinkingMix * 0.20);
      root.rotation.x = Math.sin(elapsed * 0.42) * 0.09;
      sphere.rotation.z = elapsed * (0.18 + thinkingMix * 0.10);
      fishnet.rotation.y = elapsed * 0.18;
      fishnet.rotation.z = -elapsed * 0.10;
      coralNet.rotation.y = -elapsed * 0.15;
      coralNet.rotation.x = Math.sin(elapsed * 0.48) * 0.10;
      halo.scale.setScalar(1.0 + Math.sin(elapsed * 1.9) * 0.03 + thinkingMix * 0.10);
      haloMaterial.opacity = 0.075 + thinkingMix * 0.040;
      fishnetMaterial.opacity = 0.48 + thinkingMix * 0.10;
      coralNetMaterial.opacity = 0.30 + thinkingMix * 0.10;
      rings.forEach((ring, index) => {
        ring.rotation.x += 0.0022 + index * 0.0008 + thinkingMix * 0.0015;
        ring.rotation.y -= 0.0014 + index * 0.0005 + thinkingMix * 0.001;
      });
      nodeGroup.rotation.y = elapsed * -0.16;
      particles.rotation.y = elapsed * 0.055;
      particles.rotation.x = Math.sin(elapsed * 0.25) * 0.12;
      nodes.forEach((dot, index) => {
        const base = dot.userData.base;
        const amp = 0.075 + (index % 4) * 0.011 + thinkingMix * 0.035;
        dot.position.set(
          base.x + Math.sin(elapsed * 1.3 + dot.userData.phase) * amp,
          base.y + Math.cos(elapsed * 1.7 + dot.userData.phase) * amp,
          base.z + Math.sin(elapsed * 1.1 + dot.userData.phase) * amp
        );
        dot.scale.setScalar(1 + Math.sin(elapsed * 2.4 + index) * 0.22 + thinkingMix * 0.18);
      });
      updateLabels();
      renderer.render(scene, camera);
    }

    resizeRenderer();
    animate();
    window.envSphereRuntime = {resize: resizeRenderer};
  } catch (error) {
    console.warn("Environment sphere failed to initialize", error);
    progressPanel?.classList.add("sphere-fallback");
  }
}

async function loadContract() {
  const response = await fetch("/api/env-agent/contract");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  contract = await response.json();
  addEvent("contract_loaded", "Environment contract loaded", {
    route: contract.route,
    workflow_type: contract.workflow_type,
    namespaces: contract.namespaces,
    modes: contract.remediation_modes?.map((item) => item.mode),
    stop_conditions: contract.policy_stop_conditions?.map((item) => item.condition),
  });
}

function bindEvents() {
  chatForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const prompt = chatInput?.value?.trim();
    if (!prompt) return;
    appendChat("user", prompt);
    chatInput.value = "";
    resetWorking();
    resetActivity();
    handlePrompt(prompt);
  });
  copyProgressButton?.addEventListener("click", async () => {
    try {
      await copyText(timelineText());
      setText(copyProgressStatus, "Progress copied.");
    } catch (error) {
      setText(copyProgressStatus, `Copy failed: ${error.message}`);
    }
  });
  copyLogsButton?.addEventListener("click", async () => {
    try {
      await copyText(logsText());
      setText(copyProgressStatus, "Logs copied.");
    } catch (error) {
      setText(copyProgressStatus, `Copy failed: ${error.message}`);
    }
  });
  autonomyMaximize?.addEventListener("click", updateAutonomyModal);
  autonomyModal?.addEventListener("shown.bs.modal", updateAutonomyModal);
  remediationApprove?.addEventListener("click", approveAndExecuteRemediation);
  autonomyModalCopyJson?.addEventListener("click", async () => {
    try {
      await copyText(logsText());
    } catch (error) {
      setText(copyProgressStatus, `Copy failed: ${error.message}`);
    }
  });
}

async function boot() {
  setVisual("idle");
  setStatus("Idle");
  resetActivity();
  resetWorking();
  bindSidebar();
  bindEvents();
  initSphere();
  try {
    await loadContract();
    const rows = await loadTransactions();
    const active = rows.find((item) => !isTerminalStatus(item.status));
    if (active) {
      await openSession(active.session_id, {closeSidebar: false});
    } else {
      resetWorking();
    }
  } catch (error) {
    setStatus("contract_error", "danger");
    setVisual("failed");
    addWorking("00 / CONTRACT", `Could not load environment contract: ${error.message}`);
  }
}

document.addEventListener("DOMContentLoaded", boot);














