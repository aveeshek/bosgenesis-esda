const activityState = {
  nodes: [],
  selectedIds: new Set(),
  activeRunId: null,
  activeDetail: null,
  chatSessionId: null,
  chatBusy: false,
};

const activityEls = {
  workflowFilter: document.getElementById("activity-workflow-filter"),
  timeRange: document.getElementById("activity-time-range"),
  statusFilter: document.getElementById("activity-status-filter"),
  publishedFilter: document.getElementById("activity-published-filter"),
  refresh: document.getElementById("activity-refresh"),
  status: document.getElementById("activity-status"),
  graph: document.getElementById("activity-graph"),
  graphScroll: document.getElementById("activity-graph-scroll"),
  tooltip: document.getElementById("activity-tooltip"),
  nodeCount: document.getElementById("activity-node-count"),
  publishedCount: document.getElementById("activity-published-count"),
  detailTitle: document.getElementById("activity-detail-title"),
  detailStatus: document.getElementById("activity-detail-status"),
  detailSummary: document.getElementById("activity-detail-summary"),
  stageChain: document.getElementById("activity-stage-chain"),
  artifactActions: document.getElementById("activity-artifact-actions"),
  chatPanel: document.getElementById("activity-chat-panel"),
  chatCanvas: document.getElementById("activity-chat-canvas"),
  chatMode: document.getElementById("activity-chat-mode"),
  chatStatus: document.getElementById("activity-chat-status"),
  chatTranscript: document.getElementById("activity-chat-transcript"),
  chatInput: document.getElementById("activity-chat-input"),
  chatSend: document.getElementById("activity-chat-send"),
  chatSphereStage: document.getElementById("activity-chat-sphere-stage"),
  chatSphereDock: document.getElementById("activity-chat-sphere-dock"),
  chatSphereCanvas: document.getElementById("activity-chat-sphere-canvas"),
  modelProfile: document.getElementById("model_profile"),
};

const statusLabels = {
  created: "Created",
  running: "Running",
  completed: "Completed",
  published: "Published",
  failed: "Failed",
  stopped: "Stopped",
  recovered: "Recovered",
  pending: "Pending",
};

function statusClass(status) {
  return `is-${String(status || "pending").replace(/[^a-z0-9_-]/gi, "").toLowerCase() || "pending"}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDate(value) {
  if (!value) return "Unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return date.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString([], { month: "short", day: "2-digit" });
}

function compactText(value, max = 46) {
  const text = String(value || "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}...`;
}

function setStatus(message) {
  if (activityEls.status) activityEls.status.textContent = message;
}

function buildQuery() {
  const query = new URLSearchParams();
  const workflow = activityEls.workflowFilter?.value || "all";
  const status = activityEls.statusFilter?.value || "all";
  const model = activityEls.modelProfile?.value || "";
  const published = activityEls.publishedFilter?.value || "all";
  query.set("workflow_type", workflow);
  query.set("time_range", activityEls.timeRange?.value || "30d");
  query.set("limit", "200");
  if (status !== "all") query.set("status", status);
  if (model) query.set("model", model);
  if (published !== "all") query.set("published", published);
  return query.toString();
}

async function loadActivity() {
  setStatus("Loading activity timeline...");
  try {
    const response = await fetch(`/api/activity/runs?${buildQuery()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    activityState.nodes = result.nodes || [];
    pruneSelection();
    renderMetrics();
    renderGraph();
    renderSelection();
    setStatus(
      activityState.nodes.length
        ? `${activityState.nodes.length} activity run${activityState.nodes.length === 1 ? "" : "s"} loaded.`
        : "No Activity run matches the current filters."
    );
  } catch (error) {
    activityState.nodes = [];
    renderMetrics();
    renderGraph();
    setStatus(`Could not load activity: ${error.message}`);
  }
}

function pruneSelection() {
  const known = new Set(activityState.nodes.map((node) => node.run_id));
  activityState.selectedIds = new Set([...activityState.selectedIds].filter((id) => known.has(id)));
  if (activityState.activeRunId && !known.has(activityState.activeRunId)) activityState.activeRunId = null;
}

function renderMetrics() {
  const publishedCount = activityState.nodes.filter((node) => node.publish_state?.published).length;
  if (activityEls.nodeCount) {
    activityEls.nodeCount.textContent = `${activityState.nodes.length} run${activityState.nodes.length === 1 ? "" : "s"}`;
  }
  if (activityEls.publishedCount) {
    activityEls.publishedCount.textContent = `${publishedCount} published`;
  }
}

function renderGraph() {
  if (!activityEls.graph) return;
  activityEls.graph.innerHTML = "";
  if (!activityState.nodes.length) {
    const empty = document.createElement("div");
    empty.className = "activity-graph-empty";
    empty.textContent = "No activity yet. Generated Release Note and MoP runs will appear here as timeline nodes.";
    activityEls.graph.appendChild(empty);
    return;
  }

  const nodes = [...activityState.nodes].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );
  const times = nodes.map((node) => new Date(node.created_at).getTime()).filter(Number.isFinite);
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const graphWidth = Math.max(900, nodes.length * 150);
  const graphHeight = 340;
  const leftPad = 72;
  const usableWidth = graphWidth - 130;
  const laneTops = [70, 128, 186, 244];

  activityEls.graph.style.width = `${graphWidth}px`;
  activityEls.graph.style.minHeight = `${graphHeight}px`;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "activity-graph-lines");
  svg.setAttribute("viewBox", `0 0 ${graphWidth} ${graphHeight}`);
  svg.setAttribute("aria-hidden", "true");

  const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  polyline.setAttribute("class", "activity-graph-polyline");
  const points = [];

  nodes.forEach((node, index) => {
    const time = new Date(node.created_at).getTime();
    const ratio = maxTime === minTime ? index / Math.max(1, nodes.length - 1) : (time - minTime) / (maxTime - minTime);
    const x = leftPad + ratio * usableWidth;
    const y = laneTops[index % laneTops.length];
    points.push(`${x},${y + 14}`);
    renderNode(node, index, x, y);
  });

  polyline.setAttribute("points", points.join(" "));
  svg.appendChild(polyline);
  activityEls.graph.prepend(svg);
  renderAxis(nodes, minTime, maxTime, graphWidth);
}

function renderAxis(nodes, minTime, maxTime, graphWidth) {
  const axis = document.createElement("div");
  axis.className = "activity-time-axis";
  const labels = [];
  if (nodes.length === 1) {
    labels.push(nodes[0].created_at);
  } else {
    labels.push(new Date(minTime).toISOString(), new Date((minTime + maxTime) / 2).toISOString(), new Date(maxTime).toISOString());
  }
  axis.innerHTML = labels
    .map((label) => `<span>${escapeHtml(shortDate(label))}</span>`)
    .join("");
  axis.style.width = `${graphWidth - 110}px`;
  activityEls.graph.appendChild(axis);
}

function renderNode(node, index, x, y) {
  const button = document.createElement("button");
  const visualStatus = node.visual_status || node.status;
  button.type = "button";
  button.className = `activity-timeline-node ${statusClass(visualStatus)}${activityState.selectedIds.has(node.run_id) ? " is-selected" : ""}`;
  button.style.left = `${x}px`;
  button.style.top = `${y}px`;
  button.style.animationDelay = `${Math.min(index * 55, 640)}ms`;
  button.dataset.runId = node.run_id;
  button.setAttribute("role", "listitem");
  button.setAttribute(
    "aria-label",
    `${node.title}, ${node.workflow_label || "Activity"}, ${node.repository}, ${statusLabels[visualStatus] || visualStatus}`
  );
  button.innerHTML = `
    <span class="activity-node-orbit" aria-hidden="true"></span>
    <span class="activity-node-main">
      <span class="activity-node-title">${escapeHtml(compactText(node.repository, 24))}</span>
      <span class="activity-node-workflow">${escapeHtml(node.workflow_badge || node.workflow_label || "ACT")}</span>
      <span class="activity-node-meta">${escapeHtml(formatDate(node.created_at))} | ${escapeHtml(node.duration_label)}</span>
    </span>
    <span class="activity-node-status">${escapeHtml(statusLabels[visualStatus] || visualStatus)}</span>
  `;
  button.addEventListener("click", (event) => selectNode(node.run_id, { additive: event.shiftKey || event.ctrlKey || event.metaKey }));
  button.addEventListener("mouseenter", (event) => showTooltip(node, event));
  button.addEventListener("mousemove", (event) => positionTooltip(event));
  button.addEventListener("mouseleave", hideTooltip);
  activityEls.graph.appendChild(button);
}

function selectNode(runId, options = {}) {
  if (options.additive) {
    if (activityState.selectedIds.has(runId)) {
      activityState.selectedIds.delete(runId);
    } else {
      activityState.selectedIds.add(runId);
    }
  } else {
    activityState.selectedIds = new Set([runId]);
  }
  activityState.activeRunId = runId;
  renderGraph();
  renderSelection();
  loadNodeDetail(runId);
}

function renderSelection() {
  updateChatControls();
}

async function loadNodeDetail(runId) {
  if (!activityEls.stageChain) return;
  activityEls.detailTitle.textContent = "Loading run detail...";
  activityEls.detailStatus.textContent = "Loading";
  activityEls.stageChain.innerHTML = "";
  try {
    const response = await fetch(`/api/activity/runs/${encodeURIComponent(runId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detail = await response.json();
    renderDetail(detail);
  } catch (error) {
    activityEls.detailTitle.textContent = "Could not load run detail";
    activityEls.detailStatus.textContent = "Error";
    activityEls.detailSummary.textContent = error.message;
  }
}

function renderDetail(detail) {
  const node = detail.node || {};
  const visualStatus = node.visual_status || node.status || "unknown";
  activityEls.detailTitle.textContent = node.title || node.run_id || "Activity run";
  activityEls.detailStatus.textContent = statusLabels[visualStatus] || visualStatus;
  activityEls.detailStatus.className = `activity-detail-status ${statusClass(visualStatus)}`;
  const artifactSummary = node.artifact_summary || {};
  activityEls.detailSummary.innerHTML = `
    <strong>${escapeHtml(node.repository || "Unknown resource")}</strong>
    <span>${escapeHtml(node.workflow_label || "Activity")}</span>
    <span>${escapeHtml(node.release_name || node.namespace || "current")}</span>
    <span>${escapeHtml(formatDate(node.created_at))}</span>
    <span>${escapeHtml(node.model_profile?.label || "Unknown model")}</span>
    <span>${artifactSummary.has_markdown ? "Markdown ready" : "Markdown missing"}</span>
    <span>${artifactSummary.has_pdf ? "PDF ready" : "PDF missing"}</span>
  `;
  activityState.activeDetail = detail;
  renderArtifactActions(detail.artifact_actions || {});
  activityEls.stageChain.innerHTML = (detail.stages || []).map(renderStage).join("");
  updateChatControls();
}

function renderStage(stage, index) {
  return `
    <article class="activity-stage-item ${statusClass(stage.status)}" role="listitem">
      <div class="activity-stage-index">${String(index + 1).padStart(2, "0")}</div>
      <div>
        <div class="activity-stage-title">${escapeHtml(stage.label)}</div>
        <div class="activity-stage-summary">${escapeHtml(stage.summary || "No activity yet.")}</div>
        <div class="activity-stage-meta">${escapeHtml(stage.event_type || "pending")} ${stage.completed_at ? `| ${escapeHtml(formatDate(stage.completed_at))}` : ""}</div>
      </div>
      <div class="activity-stage-status">${escapeHtml(statusLabels[stage.status] || stage.status)}</div>
    </article>
  `;
}

function showTooltip(node, event) {
  if (!activityEls.tooltip) return;
  activityEls.tooltip.innerHTML = `
    <strong>${escapeHtml(node.title)}</strong>
    <span>${escapeHtml(node.workflow_label || "Activity")} | ${escapeHtml(node.repository)} | ${escapeHtml(node.release_name || node.namespace || "current")}</span>
    <span>${escapeHtml(statusLabels[node.visual_status] || node.visual_status)} | ${escapeHtml(node.duration_label)}</span>
    <span>${node.artifact_summary?.has_markdown ? "MD" : "No MD"} / ${node.artifact_summary?.has_pdf ? "PDF" : "No PDF"}</span>
  `;
  activityEls.tooltip.setAttribute("aria-hidden", "false");
  activityEls.tooltip.classList.add("is-visible");
  positionTooltip(event);
}

function positionTooltip(event) {
  if (!activityEls.tooltip) return;
  const x = Math.min(event.clientX + 14, window.innerWidth - 260);
  const y = Math.min(event.clientY + 14, window.innerHeight - 130);
  activityEls.tooltip.style.left = `${x}px`;
  activityEls.tooltip.style.top = `${y}px`;
}

function hideTooltip() {
  if (!activityEls.tooltip) return;
  activityEls.tooltip.classList.remove("is-visible");
  activityEls.tooltip.setAttribute("aria-hidden", "true");
}

function bindControls() {
  [activityEls.workflowFilter, activityEls.timeRange, activityEls.statusFilter, activityEls.publishedFilter].forEach((element) => {
    element?.addEventListener("change", loadActivity);
  });
  activityEls.modelProfile?.addEventListener("change", loadActivity);
  activityEls.refresh?.addEventListener("click", loadActivity);
  activityEls.chatInput?.addEventListener("input", updateChatControls);
  activityEls.chatInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      sendActivityChatMessage();
    }
  });
  activityEls.chatSend?.addEventListener("click", sendActivityChatMessage);
}

function selectedActivityRunIds() {
  const selected = [...activityState.selectedIds];
  if (selected.length) return selected;
  return [...activityState.nodes]
    .filter((node) => node.run_id)
    .sort((a, b) => activityNodeTime(b) - activityNodeTime(a))
    .slice(0, 1)
    .map((node) => node.run_id);
}

function activityNodeTime(node) {
  const time = new Date(node.updated_at || node.created_at || 0).getTime();
  return Number.isFinite(time) ? time : 0;
}

function hasActivityChatContext() {
  return activityState.selectedIds.size > 0 || activityState.nodes.some((node) => node.run_id);
}

function setChatStatus(message, mode = "grounded") {
  if (activityEls.chatStatus) activityEls.chatStatus.textContent = message;
  if (activityEls.chatMode) activityEls.chatMode.textContent = mode;
}

function updateChatControls() {
  const hasSelection = activityState.selectedIds.size > 0;
  const hasContext = hasActivityChatContext();
  const hasMessage = Boolean(activityEls.chatInput?.value.trim());
  if (activityEls.chatSend) activityEls.chatSend.disabled = !hasContext || !hasMessage || activityState.chatBusy;
  if (activityEls.chatInput) activityEls.chatInput.disabled = activityState.chatBusy;
  if (!hasContext) {
    setChatStatus("No Activity runs are loaded yet.", "Grounded");
  } else if (!hasSelection && !activityState.chatBusy) {
    setChatStatus("Ask will use the latest visible Activity run. Select timeline nodes for a narrower answer.", "Grounded");
  } else if (!activityState.chatBusy) {
    setChatStatus("Ready to answer from selected Activity context only.", "Grounded");
  }
}

function renderArtifactActions(artifactActions) {
  if (!activityEls.artifactActions) return;
  const actions = artifactActions.actions || {};
  const publishState = artifactActions.publish_state || {};
  const canUpload = Boolean(activityState.activeRunId && Object.keys(actions).length);
  const uploadReason = publishState.published && publishState.folder_name
    ? "Overwrite the published GitHub artifact for this run."
    : "Create this run artifact folder in GitHub, then overwrite that file for future uploads.";
  if (!Object.keys(actions).length) {
    activityEls.artifactActions.innerHTML = `
    <div class="activity-artifact-action-row">
      ${githubUploadButton("markdown", "Upload Markdown GITHUB", false, uploadReason)}
      ${githubUploadButton("pdf", "Upload PDF GITHUB", false, uploadReason)}
    </div>
    <div class="activity-artifact-empty">No Markdown/PDF artifact actions are available for this run.</div>
  `;
    bindArtifactActionControls();
    return;
  }
  const markdown = actions.markdown || {};
  const pdf = actions.pdf || {};
  const openRepo = actions.open_repo || {};
  const copyRepo = actions.copy_repo_path || {};
  activityEls.artifactActions.innerHTML = `
    <div class="activity-artifact-action-row">
      ${artifactActionLink(markdown)}
      ${artifactActionLink(pdf)}
      ${repoActionLink(openRepo)}
      ${copyRepoButton(copyRepo)}
      ${githubUploadButton("markdown", "Upload Markdown GITHUB", canUpload, uploadReason)}
      ${githubUploadButton("pdf", "Upload PDF GITHUB", canUpload, uploadReason)}
    </div>
    <div class="activity-artifact-source">
      ${escapeHtml(artifactActions.repo_path || "Local ESDA artifact fallback will be used when published artifacts are unavailable.")}
    </div>
  `;
  bindArtifactActionControls();
}

function bindArtifactActionControls() {
  activityEls.artifactActions?.querySelectorAll("[data-copy-repo-path]").forEach((button) => {
    button.addEventListener("click", () => copyRepoPath(button.dataset.copyRepoPath));
  });
  activityEls.artifactActions?.querySelectorAll("[data-github-upload-kind]").forEach((button) => {
    button.addEventListener("click", () => showGithubUploadPanel(button.dataset.githubUploadKind));
  });
}

function artifactActionLink(action) {
  if (!action?.enabled) {
    return `<button class="btn btn-sm btn-outline-secondary activity-artifact-button" type="button" disabled title="${escapeHtml(action?.reason || "Artifact missing")}">${escapeHtml(action?.label || "Download")}</button>`;
  }
  const source = action.source === "published" ? "repo" : "local";
  return `<a class="btn btn-sm btn-primary activity-artifact-button" href="${escapeHtml(action.url)}" target="_blank" rel="noreferrer"><span>${escapeHtml(action.label)}</span><em>${escapeHtml(source)}</em></a>`;
}

function repoActionLink(action) {
  if (!action?.enabled) {
    return `<button class="btn btn-sm btn-outline-secondary activity-artifact-button" type="button" disabled title="${escapeHtml(action?.reason || "Repository folder missing")}">${escapeHtml(action?.label || "Open Repo")}</button>`;
  }
  return `<a class="btn btn-sm btn-outline-secondary activity-artifact-button" href="${escapeHtml(action.url)}" target="_blank" rel="noreferrer"><span>${escapeHtml(action.label)}</span><em>GitHub</em></a>`;
}

function copyRepoButton(action) {
  if (!action?.enabled) {
    return `<button class="btn btn-sm btn-outline-secondary activity-artifact-button" type="button" disabled title="${escapeHtml(action?.reason || "Path missing")}">${escapeHtml(action?.label || "Copy Path")}</button>`;
  }
  return `<button class="btn btn-sm btn-outline-secondary activity-artifact-button" type="button" data-copy-repo-path="${escapeHtml(action.value)}"><span>${escapeHtml(action.label)}</span><em>path</em></button>`;
}

function githubUploadButton(kind, label, enabled, reason) {
  const disabled = enabled ? "" : " disabled";
  const hint = enabled ? reason : "Select a run with Markdown/PDF artifacts before uploading.";
  return `<button class="btn btn-sm btn-outline-secondary activity-artifact-button activity-github-upload-button" type="button" data-github-upload-kind="${escapeHtml(kind)}" title="${escapeHtml(hint)}"${disabled}><span>${escapeHtml(label)}</span><em>overwrite</em></button>`;
}

function showGithubUploadPanel(kind) {
  if (!activityEls.artifactActions || !activityState.activeRunId) return;
  activityEls.artifactActions.querySelector(".activity-github-upload-panel")?.remove();
  const normalizedKind = kind === "pdf" ? "pdf" : "markdown";
  const label = normalizedKind === "pdf" ? "PDF" : "Markdown";
  const accept = normalizedKind === "pdf" ? ".pdf,application/pdf" : ".md,.markdown,.txt,text/markdown,text/plain";
  const action = activityState.activeDetail?.artifact_actions?.actions?.[normalizedKind] || {};
  const workflowType = activityState.activeDetail?.node?.workflow_type;
  const filename = action.filename || (workflowType === "mop_generation"
    ? (normalizedKind === "pdf" ? "mop.pdf" : "mop.md")
    : (normalizedKind === "pdf" ? "release-notes.pdf" : "release-notes.md"));
  const folder = activityState.activeDetail?.artifact_actions?.publish_state?.folder_name || "new GitHub folder for this run";
  const panel = document.createElement("div");
  panel.className = "activity-github-upload-panel";
  panel.innerHTML = `
    <div class="activity-github-upload-copy">
      <strong>Overwrite ${escapeHtml(filename)}</strong>
      <span>${escapeHtml(folder)}</span>
    </div>
    <input class="form-control form-control-sm activity-github-upload-input" type="file" accept="${escapeHtml(accept)}">
    <div class="activity-github-upload-controls">
      <button class="btn btn-sm btn-primary" type="button" data-upload-submit disabled>Upload ${escapeHtml(label)}</button>
      <button class="btn btn-sm btn-outline-secondary" type="button" data-upload-cancel>Cancel</button>
    </div>
    <div class="activity-github-upload-status" data-upload-status>Choose the updated ${escapeHtml(label)} file. ESDA will overwrite the published file, or create this run folder if it was only local.</div>
  `;
  activityEls.artifactActions.appendChild(panel);
  const input = panel.querySelector(".activity-github-upload-input");
  const submit = panel.querySelector("[data-upload-submit]");
  const cancel = panel.querySelector("[data-upload-cancel]");
  input?.addEventListener("change", () => {
    const selected = input.files?.[0];
    if (submit) submit.disabled = !selected;
    setGithubUploadStatus(
      panel,
      selected ? `Ready to upload ${selected.name}.` : `Choose the updated ${label} file.`
    );
  });
  submit?.addEventListener("click", () => uploadGithubArtifact(normalizedKind, input?.files?.[0], panel));
  cancel?.addEventListener("click", () => panel.remove());
  input?.click();
}

function setGithubUploadStatus(panel, message, tone = "") {
  const status = panel?.querySelector("[data-upload-status]");
  if (!status) return;
  status.textContent = message;
  status.className = `activity-github-upload-status${tone ? ` is-${tone}` : ""}`;
}

function uploadErrorMessage(result, response) {
  const detail = result?.detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || item.message || JSON.stringify(item)).join("; ");
  if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
  return detail || `HTTP ${response.status}`;
}

async function uploadGithubArtifact(kind, file, panel) {
  if (!activityState.activeRunId || !file || !panel) return;
  const submit = panel.querySelector("[data-upload-submit]");
  const cancel = panel.querySelector("[data-upload-cancel]");
  const input = panel.querySelector(".activity-github-upload-input");
  const runId = activityState.activeRunId;
  const formData = new FormData();
  formData.append("file", file);
  submit.disabled = true;
  if (cancel) cancel.disabled = true;
  if (input) input.disabled = true;
  setGithubUploadStatus(panel, "Uploading to the published GitHub artifact folder...", "working");
  setStatus("Uploading reviewed artifact to GitHub...");
  try {
    const response = await fetch(`/api/activity/runs/${encodeURIComponent(runId)}/artifact/${encodeURIComponent(kind)}/upload`, {
      method: "POST",
      body: formData,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(uploadErrorMessage(result, response));
    const overwrite = result.artifact_overwrite || {};
    const commit = overwrite.commit_hash ? ` Commit ${String(overwrite.commit_hash).slice(0, 12)}.` : "";
    const message = overwrite.status === "unchanged"
      ? `GitHub artifact already matched ${file.name}.${commit}`
      : `GitHub artifact overwritten with ${file.name}.${commit}`;
    setGithubUploadStatus(panel, message, "success");
    await loadActivity();
    if (activityState.activeRunId === runId) await loadNodeDetail(runId);
    setStatus(message);
  } catch (error) {
    setGithubUploadStatus(panel, `Upload failed: ${error.message}`, "error");
    setStatus(`GitHub artifact upload failed: ${error.message}`);
    submit.disabled = false;
    if (cancel) cancel.disabled = false;
    if (input) input.disabled = false;
  }
}

async function copyRepoPath(value) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    setStatus("Published artifact repository path copied.");
  } catch (error) {
    setStatus(`Could not copy repository path: ${error.message}`);
  }
}

function appendChatMessage(role, content, options = {}) {
  if (!activityEls.chatTranscript) return;
  const empty = activityEls.chatTranscript.querySelector(".activity-chat-empty");
  if (empty) empty.remove();
  const row = document.createElement("article");
  row.className = `activity-chat-message is-${role}`;
  const label = role === "user" ? "You" : "Artifact Chat";
  row.innerHTML = `
    <div class="activity-chat-message-label">${escapeHtml(label)}</div>
    <div class="activity-chat-message-body">${formatChatContent(content)}</div>
    ${renderChatCitations(options.citations || [])}
  `;
  activityEls.chatTranscript.appendChild(row);
  activityEls.chatTranscript.scrollTop = activityEls.chatTranscript.scrollHeight;
}

function formatChatContent(content) {
  return escapeHtml(content).replace(/\n/g, "<br>");
}

function renderChatCitations(citations) {
  if (!citations.length) return "";
  return `
    <div class="activity-chat-citations">
      ${citations.slice(0, 8).map((citation) => {
        const label = citation.label || citation.run_id || citation.artifact_id || citation.folder || citation.stage || citation.type;
        return `<span>${escapeHtml(label)}</span>`;
      }).join("")}
    </div>
  `;
}

async function sendActivityChatMessage() {
  const message = activityEls.chatInput?.value.trim() || "";
  const selectedRunIds = selectedActivityRunIds();
  if (!message || !selectedRunIds.length || activityState.chatBusy) return;
  appendChatMessage("user", message);
  activityEls.chatInput.value = "";
  activityState.chatBusy = true;
  activityEls.chatPanel?.classList.add("is-thinking");
  setChatStatus("Thinking across selected run context and artifacts...", "Thinking");
  updateChatControls();
  try {
    const response = await fetch("/api/activity/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        message,
        selected_run_ids: selectedRunIds,
        session_id: activityState.chatSessionId,
        model_profile: activityEls.modelProfile?.value || null,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    activityState.chatSessionId = result.session_id || activityState.chatSessionId;
    appendChatMessage("assistant", result.answer || "No answer was returned.", {citations: result.citations || []});
    setChatStatus(result.safe_summary || "Answered from selected Activity context.", "Grounded");
  } catch (error) {
    appendChatMessage("assistant", `Activity chat failed: ${error.message}`);
    setChatStatus("Activity chat failed. Check selected nodes and model configuration.", "Review");
  } finally {
    activityState.chatBusy = false;
    activityEls.chatPanel?.classList.remove("is-thinking");
    updateChatControls();
    window.activityChatSphereRuntime?.resize?.();
  }
}
async function initActivityChatSphere() {
  const canvas = activityEls.chatSphereCanvas;
  const stage = activityEls.chatSphereStage;
  const dock = activityEls.chatSphereDock;
  if (!canvas || !stage || !dock) return;
  try {
    const THREE = await import("https://unpkg.com/three@0.165.0/build/three.module.js");
    let thinkingMix = 0;
    const renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true, powerPreference: "high-performance"});
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
    const nodeNames = ["NODE", "POD", "SVC", "NS", "API", "LOG"];
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
      label.className = "activity-chat-node-label";
      label.textContent = nodeName;
      stage.appendChild(label);
      labels.push({el: label, target: dot});
    });

    const particlePositions = new Float32Array(340 * 3);
    for (let i = 0; i < 340; i += 1) {
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
      const rect = dock.getBoundingClientRect();
      const size = Math.max(72, Math.floor(Math.min(rect.width, rect.height)));
      renderer.setSize(size, size, false);
      camera.aspect = 1;
      camera.updateProjectionMatrix();
    }

    function updateLabels() {
      const stageRect = stage.getBoundingClientRect();
      const dockRect = dock.getBoundingClientRect();
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
    resizeObserver.observe(dock);
    window.addEventListener("resize", resizeRenderer);
    const clock = new THREE.Clock();

    function animate() {
      requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();
      const isThinking = activityEls.chatPanel?.classList.contains("is-thinking");
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
    window.activityChatSphereRuntime = {resize: resizeRenderer};
  } catch (error) {
    console.warn("Activity chat sphere failed to initialize", error);
    activityEls.chatPanel?.classList.add("sphere-fallback");
  }
}
function debounce(callback, delay) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

function initActivityPage() {
  if (!activityEls.graph) return;
  bindControls();
  initActivityChatSphere();
  loadActivity();
}

document.addEventListener("DOMContentLoaded", initActivityPage);
