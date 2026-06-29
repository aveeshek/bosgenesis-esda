const $ = (id) => document.getElementById(id);
const form = $("mop-generation-form");
const nsSelect = $("mop_namespace");
const statusBadge = $("run-status");
const finalReport = $("final-report");
const artifactLinks = $("artifact-links");
const timeline = $("timeline");
const timelineScroll = $("timeline-scroll");
const copyProgressButton = $("copy-progress");
const copyProgressStatus = $("copy-progress-status");
const copyLogsButton = $("copy-logs");
const formStatus = $("mop-form-status");
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
const workingPanel = $("ephemeral-working-panel");
const workingStream = $("ephemeral-working-stream");
const safePanel = $("safe-summary-panel");
const safeList = $("safe-summary-list");
const rail = $("agent-activity-rail");
const railGraph = $("agent-activity-graph");
const railStatus = $("agent-activity-status");
const railToggle = $("agent-activity-toggle");
const railPin = $("agent-activity-pin");
const autonomyModal = $("mop-autonomy-modal");
const autonomyMaximize = $("autonomy-maximize");
const autonomyModalLive = $("autonomy-modal-live");
const autonomyModalSummary = $("autonomy-modal-summary");
const autonomyModalJson = $("autonomy-modal-json");
const autonomyModalCopyJson = $("autonomy-modal-copy-json");
const activeKey = "bosgenesis.mopGeneration.activeRunId";
const pinKey = "bosgenesis.mopGeneration.activityRailPinned";
const autoHideMs = 30000;
let events = [], activeRunId = null, es = null, lastSeq = 0, txFailed = false;
let workingOrder = 0, workingKeys = new Set(), safeItems = [], safeKeys = new Set();
let activity = {}, pinned = false, autoHideTimer = null, revealTimer = null, revealAfter = 0, warmupTimer = null;
const seen = new Set();
const defs = [
  ["intake", "Intake", "Request received and model context prepared."],
  ["classify", "Classify", "Confirm MoP document generation workflow."],
  ["plan", "Plan", "Create evidence, agent, validation, and artifact plan."],
  ["namespace", "Scope", "Validate namespace against the configured allowlist."],
  ["k8s", "K8s", "Collect read-only namespace evidence from k8s-inspector."],
  ["helm", "Helm", "Collect read-only release evidence from helm-manager."],
  ["mop_agent", "MoP Agent", "Ask mop-creation-agent for the initial draft."],
  ["draft", "Draft", "Convert evidence into a human-reviewable MoP."],
  ["validate", "Validate", "Check rollback, validation, risk, and review sections."],
  ["recover", "Recover", "Choose continue, retry, or escalation behavior."],
  ["artifacts", "Bundle", "Save MoP documents, metadata, machine plan, and deployment zip."],
  ["publish", "Export Github", "Export the MoP bundle to Github."],
  ["complete", "Complete", "Finalize run status and safe summaries."],
];
const stageIds = new Set(defs.map(([id]) => id));
const stageNumbers = Object.fromEntries(defs.map(([id], index) => [id, index + 1]));
const stageLabels = Object.fromEntries(defs.map(([id, label]) => [id, label]));
function stageForPhase(phase) {
  const value = String(phase || "").toLowerCase();
  if (stageIds.has(value)) return value;
  if (value === "creating_plan") return "creating_plan";
  if (["planning", "planner"].includes(value)) return "plan";
  if (["scope", "namespace_validated"].includes(value)) return "namespace";
  if (value.includes("k8s") || value.includes("namespace_summary")) return "k8s";
  if (value.includes("helm")) return "helm";
  if (value.includes("mop_agent") || value.includes("mop_creation")) return "mop_agent";
  if (value.includes("draft")) return "draft";
  if (value.includes("validat") || value.includes("verifier")) return "validate";
  if (value.includes("recover")) return "recover";
  if (value.includes("artifact") || value.includes("bundle")) return "artifacts";
  if (value.includes("publish") || value.includes("github")) return "publish";
  if (value.includes("complete") || value.includes("final")) return "complete";
  return null;
}
function stageForTool(toolName) {
  const value = String(toolName || "").toLowerCase();
  if (value.includes("k8s")) return "k8s";
  if (value.includes("helm")) return "helm";
  if (value.includes("creation")) return "mop_agent";
  return null;
}
function stageForEvent(e) {
  if (!e) return null;
  const type = e.event_type;
  if (type === "tool_call_started" || type === "tool_call_completed") return stageForTool(e.payload?.tool_name);
  const map = {
    run_started: "intake",
    workflow_classified: "classify",
    planning_started: "plan",
    plan_created: "plan",
    reasoning_summary: "plan",
    namespace_validated: "namespace",
    k8s_evidence_completed: "k8s",
    helm_evidence_completed: "helm",
    mop_agent_completed: "mop_agent",
    draft_started: "draft",
    draft_completed: "draft",
    validation_completed: "validate",
    recovery_recommendation: "recover",
    artifact_created: "artifacts",
    artifact_bundle_created: "artifacts",
    artifact_publish_started: "publish",
    artifact_publish_completed: "publish",
    artifact_publish_failed: "publish",
    safe_reasoning_summary: stageForPhase(e.payload?.phase),
    run_completed: "complete",
    run_failed: "complete",
  };
  return map[type] || stageForPhase(e.payload?.phase);
}
function workingStageNumber(stageId) {
  return stageId === "creating_plan" ? 0 : stageNumbers[stageId] || 99;
}
function stageNoteLabel(stageId) {
  if (stageId === "creating_plan") return "00 / Creating Plan";
  const number = String(workingStageNumber(stageId)).padStart(2, "0");
  return `${number} / ${stageLabels[stageId] || titleFromPhase(stageId)}`;
}
function eventWorkingDetail(e) {
  const p = e?.payload || {};
  if (p.result?.status) return `Status: ${p.result.status}. ${p.result.error?.message || "Evidence captured for this stage."}`;
  if (p.artifact?.metadata?.filename) return `${p.artifact.metadata.filename} saved as ${p.artifact.artifact_type || "artifact"}.`;
  if (p.bundle_artifact?.metadata?.filename) return `${p.bundle_artifact.metadata.filename} is ready for download.`;
  if (p.artifact_publish?.folder_name) return `Published folder: ${p.artifact_publish.folder_name}.`;
  if (p.artifact_publish?.error?.message) return p.artifact_publish.error.message;
  if (p.validation?.message) return p.validation.message;
  if (p.reasoning_summary) return p.reasoning_summary;
  return p.detail || "";
}
function addStageWorking(stageId, message, detail, key) {
  if (!workingStream || !workingPanel || !stageId) return;
  const stableKey = key || `${stageId}:${message}:${detail}`;
  if (workingKeys.has(stableKey)) return;
  workingKeys.add(stableKey);
  workingPanel.classList.remove("is-hidden");
  if (workingPanel.classList.contains("is-empty")) { workingStream.innerHTML = ""; workingPanel.classList.remove("is-empty"); }
  const item = document.createElement("article"); item.className = "working-note-item";
  item.dataset.stageNumber = String(workingStageNumber(stageId));
  item.dataset.order = String(++workingOrder);
  const label = document.createElement("div"); label.className = "working-note-label"; label.textContent = stageNoteLabel(stageId);
  const text = document.createElement("p"); streamText(text, `${message || "Working"}. ${detail || ""}`.trim());
  item.append(label, text); workingStream.appendChild(item);
  Array.from(workingStream.querySelectorAll(".working-note-item"))
    .sort((a, b) => Number(a.dataset.stageNumber) - Number(b.dataset.stageNumber) || Number(a.dataset.order) - Number(b.dataset.order))
    .forEach((node) => workingStream.appendChild(node));
  workingStream.scrollTop = 0;
  refreshOpenAutonomyModal();
}
function addWorkingForEvent(e) {
  const stageId = stageForEvent(e);
  if (!stageId) return;
  addStageWorking(stageId, e.message || summarize(e), eventWorkingDetail(e), `event:${e.event_id || e.sequence || e.event_type}:${stageId}`);
}
function valueOf(id) { const v = $(id)?.value?.trim() || ""; return v || null; }
function setText(el, text) { if (el) el.textContent = text || ""; }
function terminal(s) { return ["completed", "failed", "cancelled", "stopped"].includes(s); }
function fresh(t, mins = 120) { const d = Date.parse(t?.updated_at || t?.created_at || ""); return Number.isFinite(d) && Date.now() - d <= mins * 60000; }
function visual(s) { if (s === "completed") return "complete"; if (terminal(s)) return "failed"; return ["created", "planning", "running", "waiting_for_approval"].includes(s) ? "working" : "idle"; }
function setStatus(s) {
  if (!statusBadge) return;
  const v = s || "Idle";
  statusBadge.textContent = v;
  statusBadge.className = "badge mb-2 align-self-start";
  statusBadge.classList.add(v === "completed" ? "text-bg-success" : terminal(v) ? "text-bg-danger" : ["created", "planning", "running"].includes(v) ? "text-bg-primary" : "text-bg-secondary");
}
function setVisual(state) {
  progressPanel?.classList.remove("is-idle", "is-working", "is-complete", "is-failed");
  progressPanel?.classList.add(`is-${state}`);
  const copy = {
    idle: ["Ready for MoP planning", ""],
    working: ["Thinking through operational evidence", "MoP generation in progress."],
    complete: ["MoP draft ready", "Review the generated Method of Procedure draft."],
    failed: ["Run needs review", "MoP generation stopped before completion."],
  }[state] || ["Ready for MoP planning", ""];
  setText(spherePhase, copy[0]); setText(sphereTitle, copy[1]);
  window.mopSphereRuntime?.setMode?.(state);
  setTimeout(() => window.mopSphereRuntime?.resize?.(), 80);
}
function clone(v) { return JSON.parse(JSON.stringify(v || {})); }
function scrub(v) { if (Array.isArray(v)) return v.map(scrub); if (!v || typeof v !== "object") return v; const o = {}; Object.entries(v).forEach(([k, c]) => { if (k !== "reasoning_summary") o[k] = scrub(c); }); return o; }
function displayPayload(e) { return scrub(clone(e?.payload)); }
function timelineText() { return events.length ? events.map((e, i) => `${i + 1}. ${e.event_type}: ${e.message}\n${JSON.stringify(displayPayload(e), null, 2)}`).join("\n\n") : "No progress events yet."; }
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
function refreshOpenAutonomyModal() {
  if (autonomyModal?.classList.contains("show")) updateAutonomyModal();
}
function logsText() {
  if (!events.length) return "No progress events yet.";
  return JSON.stringify(
    events.map((event, index) => ({
      index: index + 1,
      sequence: event.sequence,
      event_type: event.event_type,
      message: event.message,
      payload: displayPayload(event),
    })),
    null,
    2
  );
}
async function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const t = document.createElement("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0"; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
}
function resetWorking() {
  workingOrder = 0; workingKeys = new Set();
  if (!workingStream || !workingPanel) return;
  workingStream.innerHTML = '<div class="stream-empty-state">Live model and agent working notes will appear only while this page is connected.</div>';
  workingPanel.classList.add("is-empty"); workingPanel.classList.remove("is-hidden");
}
function streamText(el, text) { let i = 0; el.textContent = ""; const timer = setInterval(() => { i += Math.max(1, Math.ceil(text.length / 36)); el.textContent = text.slice(0, i); if (i >= text.length) clearInterval(timer); }, 18); }
function addWorking(e) {
  const stageId = stageForPhase(e.payload?.phase) || "plan";
  addStageWorking(stageId, e.message || "Working", e.payload?.detail || "", `live:${e.event_id || e.payload?.phase || e.message}:${stageId}`);
}
function finishWorking() {
  if (!workingStream || !workingPanel) return;
  workingStream.innerHTML = '<div class="stream-empty-state">Ephemeral live notes were cleared after completion. Persisted safe summaries are shown below in this pane.</div>';
  workingPanel.classList.add("is-empty");
}
function resetSafe() { safeItems = []; safeKeys = new Set(); renderSafe(false); }
function isProviderFallbackSummary(summary) { return /Planner fallback used|Failed to invoke the Azure CLI|AzureCliCredential/i.test(summary); }
function summaries(e) {
  const p = e?.payload || {}, out = [];
  if (p.reasoning_summary) out.push(p.reasoning_summary);
  ["classification", "plan", "validation", "recovery"].forEach((k) => { if (p[k]?.reasoning_summary) out.push(p[k].reasoning_summary); });
  return out.map(String).map((s) => s.trim()).filter(Boolean);
}
function titleFromPhase(phase) { return String(phase || "Agent").split("_").map((part) => part ? part[0].toUpperCase() + part.slice(1) : part).join(" "); }
function summaryLabel(e) {
  if (e?.event_type === "safe_reasoning_summary") return e.payload?.stage_label || titleFromPhase(e.payload?.phase);
  return {workflow_classified: "Classifier", plan_created: "Planner", reasoning_summary: "Planner", draft_completed: "Draft Writer", validation_completed: "Verifier", recovery_recommendation: "Recovery", run_completed: "Final", run_failed: "Final"}[e?.event_type] || "Agent";
}
function summaryKey(summary) { return String(summary || "").toLowerCase().replace(/\s+/g, " ").trim(); }
function collectSafe(e) { summaries(e).filter((s) => !isProviderFallbackSummary(s)).forEach((s) => { const key = summaryKey(s); if (!safeKeys.has(key)) { safeKeys.add(key); safeItems.push({label: summaryLabel(e), summary: s}); } }); }
function renderSafe(show = false) {
  if (!safeList) return;
  safeList.innerHTML = "";
  if (!safeItems.length) safeList.innerHTML = '<div class="stream-empty-state">No safe reasoning summaries are available yet.</div>';
  safeItems.forEach((item, i) => { const row = document.createElement("article"); row.className = "safe-summary-item"; row.innerHTML = `<div class="safe-summary-label">${i + 1}. ${item.label}</div><p></p>`; row.querySelector("p").textContent = item.summary; safeList.appendChild(row); });
  safeList.hidden = !show;
  if (safePanel) safePanel.classList.toggle("is-hidden", !show);
  refreshOpenAutonomyModal();
}
function setRailStatus(s) { setText(railStatus, s); }
function clearAutoHide() { if (autoHideTimer) clearTimeout(autoHideTimer); autoHideTimer = null; }
function controls() { const c = rail?.classList.contains("is-collapsed"); rail?.setAttribute("aria-expanded", String(!c)); setText(railToggle, c ? "Show" : "Hide"); setText(railPin, pinned ? "Pinned" : "Pin"); railPin?.setAttribute("aria-pressed", String(pinned)); }
function collapseRail(force = false) { if (pinned && !force) return; clearAutoHide(); rail?.classList.add("is-collapsed"); rail?.classList.remove("is-revealed"); controls(); }
function hideRail() { clearAutoHide(); rail?.classList.add("is-dormant", "is-collapsed"); rail?.classList.remove("is-revealed"); controls(); }
function revealRail(reason) { clearAutoHide(); rail?.classList.remove("is-dormant", "is-collapsed"); rail?.classList.add("is-revealed"); if (reason) setRailStatus(reason); controls(); if (!pinned) autoHideTimer = setTimeout(() => collapseRail(), autoHideMs); }
function revealForEvent(reason) { const delay = Math.max(0, revealAfter - Date.now()); if (delay > 0) { if (!revealTimer) revealTimer = setTimeout(() => { revealTimer = null; revealAfter = 0; revealRail(reason || "Execution plan created"); }, delay); return; } revealRail(reason); }
function resetActivity() { activity = Object.fromEntries(defs.map(([id, label, hint]) => [id, {status: "pending", label, detail: hint}])); setRailStatus("Awaiting run"); renderActivity(); if (!pinned) collapseRail(true); }
function mark(id, status, e, detail) { if (activity[id]) activity[id] = {...activity[id], status, detail: detail || summarize(e)}; }
function summarize(e) { const p = e?.payload || {}; if (p.result?.status) return `${e.message} Status: ${p.result.status}.`; if (p.validation?.message) return p.validation.message; if (p.recovery?.action) return `Recovery action: ${p.recovery.action}.`; if (p.final_report) return "Final Markdown draft is ready for review."; return e?.message || "Activity updated."; }
function resultStatus(r) {
  const s = r?.status || "success";
  if (s === "success") return "success";
  // MoP evidence tools are read-only inputs. If one is blocked/unavailable, the workflow
  // continues with an explicit evidence gap, so show it as recovered/warning instead of fatal red.
  return "recovered";
}
function failRunning(e) { const d = defs.find(([id]) => activity[id]?.status === "running"); if (d) mark(d[0], "failed", e); }
function renderActivity() {
  if (!railGraph) return; railGraph.innerHTML = "";
  defs.forEach(([id, label, hint], i) => { const s = activity[id] || {status: "pending", detail: hint}; const n = document.createElement("div"); n.className = `activity-node is-${s.status}`; n.role = "listitem"; n.tabIndex = 0; n.innerHTML = `<span class="activity-node-dot">${i + 1}</span><span class="activity-node-label"></span><span class="activity-node-popover"></span>`; n.querySelector(".activity-node-label").textContent = label; n.querySelector(".activity-node-popover").textContent = s.detail || hint; railGraph.appendChild(n); if (i < defs.length - 1) { const c = document.createElement("span"); c.className = `activity-connector is-${s.status}`; railGraph.appendChild(c); } });
}
function toolActivity(e) { const t = e.payload?.tool_name, r = e.payload?.result, s = e.event_type === "tool_call_started" ? "running" : resultStatus(r); if (t === "mop.k8s_inspector") mark("k8s", s, e); if (t === "mop.helm_manager") mark("helm", s, e); if (t === "mop.creation_agent") mark("mop_agent", s, e); }
function activityEvent(e, opt = {}) {
  switch (e.event_type) {
    case "run_started": mark("intake", "success", e); mark("classify", "running", e, "Classifying MoP workflow intent."); setRailStatus("Classifying request"); break;
    case "workflow_classified": mark("classify", "success", e); mark("plan", "running", e, "Creating evidence-first MoP plan."); setRailStatus("Creating execution plan"); break;
    case "planning_started": mark("plan", "running", e); break;
    case "plan_created": mark("plan", "success", e); mark("namespace", "running", e, "Validating namespace guardrail."); setRailStatus("Plan created"); break;
    case "namespace_validated": mark("namespace", e.payload?.valid ? "success" : "failed", e); if (e.payload?.valid) mark("k8s", "running", e, "Collecting Kubernetes evidence."); break;
    case "tool_call_started": case "tool_call_completed": toolActivity(e); break;
    case "k8s_evidence_completed": mark("k8s", resultStatus(e.payload?.result), e); mark("helm", "running", e, "Collecting Helm release evidence."); break;
    case "helm_evidence_completed": mark("helm", resultStatus(e.payload?.result), e); mark("mop_agent", "running", e, "Calling MoP creation agent."); break;
    case "mop_agent_completed": mark("mop_agent", resultStatus(e.payload?.result), e); mark("draft", "running", e, "Drafting Markdown from evidence."); break;
    case "draft_started": mark("draft", "running", e); break;
    case "draft_completed": mark("draft", "success", e); mark("validate", "running", e, "Validating the draft."); break;
    case "validation_completed": mark("validate", e.payload?.valid ? "success" : "failed", e); mark("recover", "running", e, "Choosing recovery or continue behavior."); break;
    case "recovery_recommendation": mark("recover", e.payload?.escalation_required ? "recovered" : "success", e); mark("artifacts", "running", e, "Generating MoP deployment artifact bundle."); break;
    case "artifact_created": mark("artifacts", "running", e, summarize(e)); setRailStatus("Saving MoP bundle files"); break;
    case "artifact_bundle_created": mark("artifacts", "success", e, "Complete MoP bundle saved."); setRailStatus("MoP bundle ready"); break;
    case "artifact_publish_started": mark("publish", "running", e, "Exporting MoP bundle to Github."); setRailStatus("Exporting MoP bundle to Github"); break;
    case "artifact_publish_completed": mark("publish", "success", e); mark("complete", "running", e, "Finalizing run after Github export."); setRailStatus("MoP artifacts exported"); break;
    case "artifact_publish_failed": mark("publish", "failed", e); setRailStatus("Github export failed"); break;
    case "run_completed": if (activity.publish?.status === "pending") mark("publish", "recovered", e, "Github export was disabled or not configured."); mark("complete", "success", e); setRailStatus("Autonomy sequence completed"); break;
    case "run_failed": failRunning(e); mark("complete", "failed", e); setRailStatus("Autonomy sequence needs review"); break;
  }
  renderActivity();
  if (opt.reveal !== false && ["planning_started", "plan_created", "namespace_validated", "tool_call_started", "tool_call_completed", "draft_completed", "validation_completed", "artifact_created", "artifact_bundle_created", "artifact_publish_started", "artifact_publish_completed", "artifact_publish_failed", "run_completed", "run_failed"].includes(e.event_type)) revealForEvent(summarize(e));
}

function addTimeline(e) {
  if (!timeline) return;
  if (e.event_id && seen.has(e.event_id)) return;
  if (e.event_id) seen.add(e.event_id);
  events.push(e); lastSeq = Math.max(lastSeq, Number(e.sequence || 0));
  const li = document.createElement("li");
  const title = document.createElement("strong"); title.textContent = `${e.event_type}: ${e.message || ""}`;
  const pre = document.createElement("pre"); pre.textContent = JSON.stringify(displayPayload(e), null, 2);
  li.append(title, pre); timeline.appendChild(li); timelineScroll?.scrollTo({top: timelineScroll.scrollHeight, behavior: "smooth"});
}
function artifactFilename(artifact) {
  return artifact?.metadata?.filename || artifact?.title || artifact?.artifact_type || "artifact";
}
function bundleArtifact(artifacts) {
  const list = artifacts || [];
  return list.find((artifact) => (artifact.artifact_type || "").toLowerCase() === "mop_bundle_zip")
    || list.find((artifact) => artifactFilename(artifact).toLowerCase() === "mop-bundle.zip")
    || list.find((artifact) => (artifact.mime_type || "").toLowerCase().includes("zip"));
}
function renderArtifactLinks(artifacts) {
  if (!artifactLinks) return;
  artifactLinks.innerHTML = "";
  const artifact = bundleArtifact(artifacts || []);
  const hasArtifacts = (artifacts || []).some((item) => item?.artifact_id);
  if (!artifact?.artifact_id && !hasArtifacts) {
    artifactLinks.innerHTML = '<span class="small text-secondary">Download links appear after artifact rendering.</span>';
    return;
  }
  const group = document.createElement("div");
  group.className = "d-flex align-items-center gap-2 mb-2 flex-wrap";
  const link = document.createElement("a");
  link.className = "btn btn-sm btn-outline-warning";
  link.href = artifact?.artifact_id ? `/api/artifacts/${artifact.artifact_id}` : `/api/runs/${activeRunId}/bundle`;
  link.textContent = "Download MoP Bundle";
  link.setAttribute("download", "");
  const meta = document.createElement("span");
  meta.className = "small text-secondary";
  meta.textContent = artifact?.artifact_id ? artifactFilename(artifact) : "generated from available run artifacts";
  group.append(link, meta);
  artifactLinks.appendChild(group);
}
async function refreshArtifacts(runId) {
  const response = await fetch(`/api/runs/${runId}/artifacts`);
  if (!response.ok) return;
  const result = await response.json();
  renderArtifactLinks(result.artifacts || []);
}

function processEvent(e, opt = {}) {
  if (!e || typeof e !== "object") return;
  if (e.event_type === "ephemeral_working_note") { if (opt.live !== false) addWorking(e); return; }
  if (opt.live !== false) addWorkingForEvent(e);
  collectSafe(e); addTimeline(e); activityEvent(e, opt);
  const mapped = {run_started: "running", run_completed: "completed", run_failed: "failed"}[e.event_type];
  if (mapped) { setStatus(mapped); setVisual(visual(mapped)); }
  if (["run_completed", "run_failed"].includes(e.event_type)) { if (e.payload?.final_report) finalReport.textContent = e.payload.final_report; if (e.payload?.artifacts) renderArtifactLinks(e.payload.artifacts); renderSafe(true); }
  if (["artifact_created", "artifact_bundle_created"].includes(e.event_type)) refreshArtifacts(e.run_id || activeRunId).catch(() => {});
  refreshOpenAutonomyModal();
  if (e.event_type === "draft_completed" && e.payload?.preview && finalReport.textContent.trim().startsWith("No MoP")) finalReport.textContent = e.payload.preview;
}
function clearTimers() { if (warmupTimer) clearTimeout(warmupTimer); if (revealTimer) clearTimeout(revealTimer); warmupTimer = null; revealTimer = null; revealAfter = 0; }
function resetTimeline() { events = []; lastSeq = 0; seen.clear(); if (timeline) timeline.innerHTML = ""; resetSafe(); resetActivity(); resetWorking(); }
function setActive(runId) { activeRunId = runId; if (runId) localStorage.setItem(activeKey, runId); else localStorage.removeItem(activeKey); }
function resetView(message) {
  if (es) { es.close(); es = null; }
  clearTimers(); resetTimeline(); hideRail();
  if (artifactLinks) artifactLinks.innerHTML = '<span class="small text-secondary">Download links appear after artifact rendering.</span>';
  if (finalReport) finalReport.textContent = message;
  setText(copyProgressStatus, "");
}
async function refreshRun(runId) { const r = await fetch(`/api/runs/${runId}`); if (!r.ok) return null; const run = await r.json(); setStatus(run.status || "Idle"); setVisual(visual(run.status)); if (run.final_report) finalReport.textContent = run.final_report; await refreshArtifacts(runId); return run; }
async function openRun(runId, opt = {}) {
  if (es) { es.close(); es = null; }
  resetTimeline(); setActive(runId); if (opt.closeSidebar) setSidebar(false);
  try {
    const r = await fetch(`/api/runs/${runId}/snapshot`); if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const snap = await r.json(), run = snap.run || {};
    setStatus(run.status || "Idle"); setVisual(visual(run.status));
    (snap.events || []).forEach((e) => processEvent(e, {live: false, reveal: false}));
    if (run.final_report) finalReport.textContent = run.final_report;
    renderArtifactLinks(snap.artifacts || []);
    renderSafe(terminal(run.status));
    if (!terminal(run.status)) connect(runId);
  } catch (err) {
    resetView(`Could not restore MoP run: ${err.message}`); setActive(null); setStatus("failed"); setVisual("failed");
  }
  await loadTransactions();
}
function connect(runId) {
  if (es) es.close();
  es = new EventSource(`/api/runs/${runId}/events${lastSeq ? `?after_sequence=${lastSeq}` : ""}`);
  es.onmessage = async (msg) => { const e = JSON.parse(msg.data); processEvent(e, {live: true}); if (["run_completed", "run_failed"].includes(e.event_type)) { es?.close(); es = null; await refreshRun(runId); await loadTransactions(); } };
  es.onerror = () => setText(copyProgressStatus, "Live event stream temporarily disconnected.");
}
function setSidebar(open) { if (!txSidebar) return; document.body.classList.toggle("transaction-sidebar-open", open); txSidebar.setAttribute("aria-hidden", open ? "false" : "true"); txToggle?.setAttribute("aria-expanded", open ? "true" : "false"); }
function time(value) { if (!value) return ""; try { return new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(value)); } catch (_e) { return ""; } }
function renderTransactions(list) {
  if (!txList || !txStatus) return; txList.innerHTML = "";
  if (!list.length) { txStatus.textContent = "No MoP generation transactions yet."; return; }
  txStatus.textContent = `${list.length} MoP generation transaction${list.length === 1 ? "" : "s"}`;
  list.forEach((tx) => {
    const card = document.createElement("button"); card.type = "button"; card.className = `transaction-card${tx.run_id === activeRunId ? " is-active" : ""}`;
    const row = document.createElement("div"); row.className = "transaction-card-row";
    const st = document.createElement("span"); st.className = "transaction-status-pill"; st.textContent = tx.status || "unknown";
    const clear = document.createElement("button"); clear.type = "button"; clear.className = "transaction-clear"; clear.textContent = "Clear";
    clear.addEventListener("click", async (ev) => { ev.stopPropagation(); await clearTransaction(tx.run_id); });
    row.append(st, clear);
    const title = document.createElement("div"); title.className = "transaction-card-title"; title.textContent = tx.title || tx.goal || tx.run_id;
    const sub = document.createElement("div"); sub.className = "transaction-card-subtitle"; sub.textContent = tx.namespace || tx.target_url || "mop_generation";
    const meta = document.createElement("div"); meta.className = "transaction-card-meta"; const artifacts = Number(tx.artifact_count || 0); meta.textContent = `${time(tx.updated_at)} | ${artifacts} artifact${artifacts === 1 ? "" : "s"}`;
    card.append(row, title, sub, meta); card.addEventListener("click", () => openRun(tx.run_id, {closeSidebar: true})); txList.appendChild(card);
  });
}
async function loadTransactions() {
  if (!txList) return [];
  try { const r = await fetch("/api/transactions?workflow_type=mop_generation"); if (!r.ok) throw new Error(`HTTP ${r.status}`); txFailed = false; const data = await r.json(); renderTransactions(data.transactions || []); return data.transactions || []; }
  catch (err) { txFailed = true; if (txStatus) txStatus.textContent = `Could not load transactions: ${err.message}`; return []; }
}
async function clearTransaction(runId) {
  const r = await fetch(`/api/transactions/${runId}/clear`, {method: "POST"});
  if (!r.ok) { if (txStatus) txStatus.textContent = `Clear failed: HTTP ${r.status}`; return; }
  if (runId === activeRunId) { setActive(null); resetView("Transaction hidden from history. Start a new MoP run or choose another transaction."); setStatus("Idle"); setVisual("idle"); }
  await loadTransactions();
}
function bindSidebar() { txToggle?.addEventListener("click", () => setSidebar(true)); txClose?.addEventListener("click", () => setSidebar(false)); txBackdrop?.addEventListener("click", () => setSidebar(false)); document.addEventListener("keydown", (e) => { if (e.key === "Escape") setSidebar(false); }); }
function bindRail() {
  try { pinned = localStorage.getItem(pinKey) === "true"; } catch (_e) { pinned = false; }
  rail?.classList.toggle("is-pinned", pinned); controls();
  railToggle?.addEventListener("click", () => rail?.classList.contains("is-collapsed") ? revealRail("Activity feed opened.") : collapseRail(true));
  railPin?.addEventListener("click", () => { pinned = !pinned; try { localStorage.setItem(pinKey, String(pinned)); } catch (_e) {} rail?.classList.toggle("is-pinned", pinned); if (pinned) revealRail("Activity feed pinned open."); controls(); });
}
async function loadNamespaces() {
  if (!nsSelect) return;
  try {
    const r = await fetch("/api/mop-generation/namespaces"); if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json(), namespaces = data.namespaces || [];
    nsSelect.innerHTML = ""; namespaces.forEach((item) => { const o = document.createElement("option"); o.value = item.name; o.textContent = item.display_name || item.name; nsSelect.appendChild(o); });
    const env = $("mop_target_environment");
    if (env) {
      const requested = data.default_environment || "kubernetes_generic";
      env.value = Array.from(env.options).some((option) => option.value === requested)
        ? requested
        : "kubernetes_generic";
    }
    setText(formStatus, `${namespaces.length} namespace${namespaces.length === 1 ? "" : "s"} available.`);
  } catch (err) { nsSelect.innerHTML = '<option value="">Could not load namespaces</option>'; setText(formStatus, `Could not load namespaces: ${err.message}`); }
}
function warmup(payload, delay) {
  addWorking({event_type: "ephemeral_working_note", message: "Creating execution plan", payload: {phase: "creating_plan", detail: `Reading namespace ${payload.namespace}, selected model, and guardrails before revealing the autonomy map.`}});
  warmupTimer = setTimeout(() => { warmupTimer = null; mark("intake", "success", {message: "MoP request received", payload}); mark("classify", "running", {message: "Classifying request", payload}); mark("plan", "running", {message: "Creating MoP plan", payload}); renderActivity(); revealRail("Creating execution plan"); }, delay);
}
async function startRun(e) {
  e.preventDefault(); resetView("Generating MoP draft..."); setStatus("planning"); setVisual("working");
  const payload = {namespace: valueOf("mop_namespace"), target_namespace: valueOf("mop_target_namespace") || "generic-namespace", change_intent: valueOf("mop_change_intent"), target_environment: valueOf("mop_target_environment"), helm_release: valueOf("mop_helm_release"), implementation_window: valueOf("mop_implementation_window"), analysis_depth: valueOf("mop_analysis_depth") || "standard", model_profile: valueOf("model_profile")};
  renderSafe(false); const delay = 2400 + Math.floor(Math.random() * 1600); revealAfter = Date.now() + delay; warmup(payload, delay);
  const r = await fetch("/api/mop-generation", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  if (!r.ok) { clearTimers(); finalReport.textContent = `Failed to start MoP generation: HTTP ${r.status}`; setStatus("failed"); setVisual("failed"); return; }
  const created = await r.json(); setActive(created.run_id); await loadTransactions(); connect(created.run_id);
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

const activityDefinitions = [
  { id: "start", label: "Intake", hint: "Run accepted and boundaries loaded." },
  { id: "classify", label: "Classify", hint: "Identify workflow and confidence." },
  { id: "plan", label: "Plan", hint: "Create evidence-first action plan." },
  { id: "evidence", label: "Evidence", hint: "Call release-note-agent." },
  { id: "clone", label: "Clone", hint: "Download a temporary local checkout." },
  { id: "security", label: "Security", hint: "Scan common vulnerability signals and summarize through the selected LLM." },
  { id: "quality", label: "Quality", hint: "Run pylint for Python or a safe static fallback for other projects." },
  { id: "cleanup", label: "Cleanup", hint: "Remove temporary checkout after analysis." },
  { id: "draft", label: "Draft", hint: "Write from collected evidence and scan results." },
  { id: "validate", label: "Validate", hint: "Check structure and support." },
  { id: "recover", label: "Recover", hint: "Continue, retry, or escalate." },
  { id: "artifacts", label: "Artifacts", hint: "Save Markdown/PDF outputs." },
  { id: "publish", label: "Publish", hint: "Commit MD/PDF outputs to bosgenesis-artifacts." },
  { id: "complete", label: "Complete", hint: "Finalize run status." },
];
async function boot() {
  setVisual("idle"); initSphere(); bindSidebar(); bindRail(); resetActivity(); resetWorking(); renderSafe(false); await loadNamespaces();
  const txs = await loadTransactions(); if (txFailed) return;
  const stored = txs.find((x) => x.run_id === localStorage.getItem(activeKey));
  const active = txs.find((x) => !terminal(x.status) && fresh(x));
  const runId = stored && !terminal(stored.status) && fresh(stored) ? stored.run_id : active?.run_id;
  if (runId) await openRun(runId); else { setActive(null); resetView("No MoP generation run yet."); setStatus("Idle"); setVisual("idle"); }
}
copyProgressButton?.addEventListener("click", async () => { try { await copyText(timelineText()); setText(copyProgressStatus, "Progress copied."); } catch (err) { setText(copyProgressStatus, `Copy failed: ${err.message}`); } });
copyLogsButton?.addEventListener("click", async () => { try { await copyText(logsText()); setText(copyProgressStatus, "Logs copied."); } catch (err) { setText(copyProgressStatus, `Copy failed: ${err.message}`); } });
autonomyMaximize?.addEventListener("click", updateAutonomyModal);
autonomyModal?.addEventListener("shown.bs.modal", updateAutonomyModal);
autonomyModalCopyJson?.addEventListener("click", async () => { try { await copyText(logsText()); updateAutonomyModal(); } catch (err) { setText(copyProgressStatus, `Copy failed: ${err.message}`); } });
form?.addEventListener("submit", startRun);
boot();
