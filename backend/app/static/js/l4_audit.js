const l4Form = document.getElementById("l4-eligibility-form");
const l4Result = document.getElementById("l4-result");
const l4AuditBody = document.getElementById("l4-audit-body");
const l4Refresh = document.getElementById("l4-refresh");

function l4StatusBadge(audit) {
  const cls = audit.eligible ? "text-bg-success" : "text-bg-secondary";
  return `<span class="badge ${cls}">${audit.decision}</span>`;
}

function l4Target(audit) {
  return `${audit.environment || "local"} / ${audit.namespace || "no namespace"}`;
}

async function loadL4Audits() {
  const response = await fetch("/api/l4/audit");
  if (!response.ok) {
    l4AuditBody.innerHTML = `<tr><td colspan="6" class="text-danger">Failed to load audits: ${response.status}</td></tr>`;
    return;
  }
  const data = await response.json();
  const audits = data.audits || [];
  if (!audits.length) {
    l4AuditBody.innerHTML = '<tr><td colspan="6" class="text-secondary">No L4 audits yet.</td></tr>';
    return;
  }
  l4AuditBody.innerHTML = "";
  audits.forEach((audit) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${l4StatusBadge(audit)}</td>
      <td>${audit.workflow_type}</td>
      <td>${l4Target(audit)}</td>
      <td>${(audit.reasons || []).slice(0, 2).join("; ") || "None"}</td>
      <td>${new Date(audit.created_at).toLocaleString()}</td>
      <td class="text-end">
        <a class="btn btn-sm btn-outline-primary" href="/api/l4/audit/${audit.audit_id}/export">Export</a>
      </td>
    `;
    l4AuditBody.appendChild(row);
  });
}

function selectedToolStep() {
  const tool = document.getElementById("l4-tool").value;
  if (tool === "rest.get") {
    return {
      title: "Read health endpoint",
      tool_name: tool,
      arguments: {url: "http://localhost:8080/health"},
      risk_level: "low",
    };
  }
  if (tool === "helm.status") {
    return {
      title: "Read Helm release status",
      tool_name: tool,
      arguments: {action: "status", release: "sample"},
      risk_level: "low",
    };
  }
  if (tool === "k8s.restart") {
    return {
      title: "Restart workload",
      tool_name: tool,
      arguments: {action: "restart", resource: "deployment/sample"},
      risk_level: "high",
    };
  }
  return {
    title: "List pods",
    tool_name: tool,
    arguments: {tool_name: "list_pods", arguments: {namespace: document.getElementById("l4-namespace").value}},
    risk_level: "low",
  };
}

l4Form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    workflow_type: document.getElementById("l4-workflow").value,
    environment: document.getElementById("l4-environment").value,
    namespace: document.getElementById("l4-namespace").value,
    tool_sequence: [selectedToolStep()],
    rollback_metadata: {
      rollback_plan: "No state change in probe path.",
      pre_change_state: "Probe only.",
      validation_plan: "Verify read-only response.",
      owner: "admin",
    },
    validation_checks: [{type: "status", expected: "success"}],
    logging_available: true,
    create_audit: true,
  };
  const response = await fetch("/api/l4/eligibility", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  l4Result.textContent = JSON.stringify(data, null, 2);
  await loadL4Audits();
});

l4Refresh.addEventListener("click", loadL4Audits);
loadL4Audits();
