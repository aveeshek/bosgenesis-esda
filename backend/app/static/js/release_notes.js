const releaseNoteForm = document.getElementById("release-note-form");
const timeline = document.getElementById("timeline");
const timelineScroll = document.getElementById("timeline-scroll");
const statusBadge = document.getElementById("run-status");
const finalReport = document.getElementById("final-report");
const artifactLinks = document.getElementById("artifact-links");
const copyProgressButton = document.getElementById("copy-progress");
const copyProgressStatus = document.getElementById("copy-progress-status");

let timelineEvents = [];

function valueOf(id) {
  const value = document.getElementById(id).value.trim();
  return value.length ? value : null;
}

function timelineText() {
  if (!timelineEvents.length) return "No progress events yet.";
  return timelineEvents.map((event, index) => {
    const payload = JSON.stringify(event.payload || {}, null, 2);
    return `${index + 1}. ${event.event_type}: ${event.message}\n${payload}`;
  }).join("\n\n");
}

function setCopyStatus(message) {
  if (!copyProgressStatus) return;
  copyProgressStatus.textContent = message;
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function addTimeline(event) {
  timelineEvents.push(event);
  const item = document.createElement("li");
  const title = document.createElement("div");
  title.className = "fw-semibold";
  title.textContent = `${event.event_type}: ${event.message}`;
  const detail = document.createElement("pre");
  detail.className = "small text-secondary mb-0";
  detail.textContent = JSON.stringify(event.payload || {}, null, 2);
  item.appendChild(title);
  item.appendChild(detail);
  timeline.appendChild(item);
  if (timelineScroll) {
    timelineScroll.scrollTop = timelineScroll.scrollHeight;
  }
}

function setStatus(status) {
  statusBadge.textContent = status;
  statusBadge.className = "badge mb-2 align-self-start " + (
    status === "completed" ? "text-bg-success" :
    status === "failed" ? "text-bg-danger" :
    "text-bg-secondary"
  );
}

function artifactDownloadLabel(artifact) {
  const mime = (artifact.mime_type || "").toLowerCase();
  const type = (artifact.artifact_type || "").toLowerCase();
  if (mime.includes("pdf") || type.includes("pdf")) return "Download PDF";
  if (mime.includes("markdown") || type === "release_note") return "Download Markdown";
  return "Download Artifact";
}

function artifactButtonClass(artifact) {
  const mime = (artifact.mime_type || "").toLowerCase();
  const type = (artifact.artifact_type || "").toLowerCase();
  if (mime.includes("pdf") || type.includes("pdf")) return "btn-outline-danger";
  return "btn-outline-primary";
}

function renderArtifactLinks(artifacts) {
  artifactLinks.innerHTML = "";
  (artifacts || []).forEach((artifact) => {
    if (!artifact?.artifact_id) return;
    const group = document.createElement("div");
    group.className = "d-flex align-items-center gap-2 mb-2 flex-wrap";
    const link = document.createElement("a");
    link.className = `btn btn-sm ${artifactButtonClass(artifact)}`;
    link.href = `/api/artifacts/${artifact.artifact_id}`;
    link.textContent = artifactDownloadLabel(artifact);
    link.setAttribute("download", "");
    const meta = document.createElement("span");
    meta.className = "small text-secondary";
    meta.textContent = artifact.title || artifact.artifact_type || "release note";
    group.appendChild(link);
    group.appendChild(meta);
    artifactLinks.appendChild(group);
  });
}

function renderArtifactLink(artifact) {
  renderArtifactLinks(artifact ? [artifact] : []);
}

async function refreshArtifacts(runId) {
  const response = await fetch(`/api/runs/${runId}/artifacts`);
  if (!response.ok) return;
  const result = await response.json();
  renderArtifactLinks(result.artifacts || []);
}

async function refreshRun(runId) {
  const response = await fetch(`/api/runs/${runId}`);
  if (!response.ok) return;
  const run = await response.json();
  setStatus(run.status);
  if (run.final_report) {
    finalReport.textContent = run.final_report;
  }
  await refreshArtifacts(runId);
}

if (copyProgressButton) {
  copyProgressButton.addEventListener("click", async () => {
    try {
      await copyText(timelineText());
      setCopyStatus("Progress copied.");
    } catch (error) {
      setCopyStatus(`Copy failed: ${error.message}`);
    }
  });
}

releaseNoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  timelineEvents = [];
  timeline.innerHTML = "";
  artifactLinks.innerHTML = "";
  finalReport.textContent = "Generating release-note draft...";
  setCopyStatus("");
  setStatus("created");

  const payload = {
    github_url: valueOf("github_url"),
    release_name: valueOf("release_name"),
    branch: valueOf("branch"),
    tag: valueOf("tag"),
    commit_sha: valueOf("commit_sha"),
    analysis_depth: valueOf("analysis_depth") || "fast",
    model_profile: valueOf("model_profile"),
  };

  const response = await fetch("/api/release-notes", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    finalReport.textContent = `Failed to start release-note run: ${response.status}`;
    setStatus("failed");
    return;
  }

  const created = await response.json();
  const source = new EventSource(created.events_url);

  source.onmessage = async (message) => {
    const eventData = JSON.parse(message.data);
    addTimeline(eventData);
    if (eventData.event_type === "artifact_created") {
      if (eventData.payload?.preview) {
        finalReport.textContent = eventData.payload.preview;
      }
      await refreshArtifacts(created.run_id);
    }
    if (eventData.event_type === "run_completed" || eventData.event_type === "run_failed") {
      if (eventData.payload?.artifact) {
        renderArtifactLinks(eventData.payload.artifacts || [eventData.payload.artifact]);
      }
      source.close();
      await refreshRun(created.run_id);
    }
  };

  source.onerror = () => {
    source.close();
    refreshRun(created.run_id);
  };
});
