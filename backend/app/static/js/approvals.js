const approvalTableBody = document.getElementById("approval-table-body");
const approvalStatusFilter = document.getElementById("approval-status-filter");
const approvalRefresh = document.getElementById("approval-refresh");
const approvalAlert = document.getElementById("approval-alert");
const detailModalElement = document.getElementById("approval-detail-modal");
const detailModal = bootstrap.Modal.getOrCreateInstance(detailModalElement);
const detailSummary = document.getElementById("approval-detail-summary");
const detailJson = document.getElementById("approval-detail-json");
const approvalNotes = document.getElementById("approval-notes");
const approvalApprove = document.getElementById("approval-approve");
const approvalReject = document.getElementById("approval-reject");
const policyProbeForm = document.getElementById("policy-probe-form");
const policyProbeResult = document.getElementById("policy-probe-result");

let selectedApproval = null;

function showApprovalError(message) {
  approvalAlert.textContent = message;
  approvalAlert.classList.remove("d-none");
}

function clearApprovalError() {
  approvalAlert.textContent = "";
  approvalAlert.classList.add("d-none");
}

function statusBadge(status) {
  const classes = {
    approved: "text-bg-success",
    expired: "text-bg-secondary",
    pending: "text-bg-warning",
    rejected: "text-bg-danger",
  };
  return `<span class="badge ${classes[status] || "text-bg-secondary"}">${status}</span>`;
}

function targetLabel(approval) {
  const environment = approval.environment || "local";
  const namespace = approval.namespace || "no namespace";
  return `${environment} / ${namespace}`;
}

function renderApprovals(approvals) {
  if (!approvals.length) {
    approvalTableBody.innerHTML = '<tr><td colspan="7" class="text-secondary">No approvals found.</td></tr>';
    return;
  }
  approvalTableBody.innerHTML = "";
  approvals.forEach((approval) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${statusBadge(approval.status)}</td>
      <td><code>${approval.tool_name}</code></td>
      <td>${approval.workflow_type}</td>
      <td>${targetLabel(approval)}</td>
      <td>${approval.risk_level}</td>
      <td>${new Date(approval.expires_at).toLocaleString()}</td>
      <td class="text-end">
        <button class="btn btn-sm btn-outline-primary" type="button">Review</button>
      </td>
    `;
    row.querySelector("button").addEventListener("click", () => openApproval(approval));
    approvalTableBody.appendChild(row);
  });
}

async function loadApprovals() {
  clearApprovalError();
  const status = approvalStatusFilter.value;
  const url = status ? `/api/approvals?status=${encodeURIComponent(status)}` : "/api/approvals";
  const response = await fetch(url);
  if (!response.ok) {
    showApprovalError(`Failed to load approvals: ${response.status}`);
    return;
  }
  const data = await response.json();
  renderApprovals(data.approvals || []);
}

function openApproval(approval) {
  selectedApproval = approval;
  approvalNotes.value = approval.review_notes || "";
  detailSummary.innerHTML = `
    <div class="approval-summary-grid">
      <div><span class="text-secondary">Status</span><strong>${approval.status}</strong></div>
      <div><span class="text-secondary">Tool</span><strong>${approval.tool_name}</strong></div>
      <div><span class="text-secondary">Workflow</span><strong>${approval.workflow_type}</strong></div>
      <div><span class="text-secondary">Target</span><strong>${targetLabel(approval)}</strong></div>
      <div><span class="text-secondary">Impact</span><strong>${approval.expected_impact}</strong></div>
      <div><span class="text-secondary">Rollback</span><strong>${approval.rollback_note}</strong></div>
    </div>
  `;
  detailJson.textContent = JSON.stringify(approval, null, 2);
  const locked = approval.status !== "pending";
  approvalApprove.disabled = locked;
  approvalReject.disabled = locked;
  detailModal.show();
}

async function decideApproval(action) {
  if (!selectedApproval) return;
  const response = await fetch(`/api/approvals/${selectedApproval.approval_id}/${action}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({notes: approvalNotes.value}),
  });
  if (!response.ok) {
    showApprovalError(`Failed to ${action} approval: ${response.status}`);
    return;
  }
  detailModal.hide();
  selectedApproval = null;
  await loadApprovals();
}

function probeArguments(toolName) {
  if (toolName === "powershell.raw") {
    return {command: "Get-Secret"};
  }
  if (toolName.startsWith("helm.")) {
    return {action: toolName.split(".")[1], release: "sample"};
  }
  return {action: toolName.split(".")[1], resource: "deployment/sample"};
}

policyProbeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const toolName = document.getElementById("probe-tool").value;
  const payload = {
    tool_name: toolName,
    workflow_type: document.getElementById("probe-workflow").value,
    environment: document.getElementById("probe-environment").value,
    namespace: document.getElementById("probe-namespace").value,
    arguments: probeArguments(toolName),
    create_approval: true,
  };
  const response = await fetch("/api/policy/evaluate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  policyProbeResult.textContent = JSON.stringify(data, null, 2);
  await loadApprovals();
});

approvalRefresh.addEventListener("click", loadApprovals);
approvalStatusFilter.addEventListener("change", loadApprovals);
approvalApprove.addEventListener("click", () => decideApproval("approve"));
approvalReject.addEventListener("click", () => decideApproval("reject"));
loadApprovals();
