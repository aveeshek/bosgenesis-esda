const form = document.getElementById("diagnostic-form");
const timeline = document.getElementById("timeline");
const statusBadge = document.getElementById("run-status");
const finalReport = document.getElementById("final-report");
const planPanel = document.getElementById("plan-panel");
const evidencePanel = document.getElementById("evidence-panel");
const errorBox = document.getElementById("error-box");

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("d-none");
}

function clearError() {
  errorBox.textContent = "";
  errorBox.classList.add("d-none");
}

function addTimeline(event) {
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
}

function setStatus(status) {
  statusBadge.textContent = status;
  statusBadge.className = "badge " + (
    status === "completed" ? "text-bg-success" :
    status === "failed" || status === "stopped" ? "text-bg-danger" :
    "text-bg-secondary"
  );
}

function appendEvidence(eventData) {
  const current = evidencePanel.textContent === "No evidence yet." ? "" : evidencePanel.textContent + "\n\n";
  evidencePanel.textContent = current + JSON.stringify(eventData.payload || {}, null, 2);
}

async function refreshRun(runId) {
  const response = await fetch(`/api/runs/${runId}`);
  if (!response.ok) return;
  const run = await response.json();
  setStatus(run.status);
  if (run.final_report) {
    finalReport.textContent = run.final_report;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  timeline.innerHTML = "";
  planPanel.textContent = "Waiting for plan...";
  evidencePanel.textContent = "No evidence yet.";
  finalReport.textContent = "Running...";
  setStatus("created");

  const payload = {
    goal: document.getElementById("goal").value,
    target_url: document.getElementById("target_url").value,
    namespace: document.getElementById("namespace").value,
  };

  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const message = `Failed to start run: ${response.status}`;
    showError(message);
    finalReport.textContent = message;
    setStatus("failed");
    return;
  }

  const created = await response.json();
  const source = new EventSource(created.events_url);

  source.onmessage = async (message) => {
    const eventData = JSON.parse(message.data);
    addTimeline(eventData);
    if (eventData.event_type === "plan_created") {
      planPanel.textContent = JSON.stringify(eventData.payload || {}, null, 2);
    }
    if (eventData.event_type === "tool_call_completed") {
      appendEvidence(eventData);
    }
    if (eventData.event_type === "run_completed" || eventData.event_type === "run_failed") {
      source.close();
      await refreshRun(created.run_id);
    }
  };

  source.onerror = () => {
    source.close();
    refreshRun(created.run_id);
  };
});