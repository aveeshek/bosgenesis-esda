(function () {
  "use strict";

  var adapter = window.esdaTwinAdapter;
  var ui = window.ESDATwinUI;
  var fixtures = window.ESDA_TWIN_FIXTURES_V1;
  var header = document.querySelector(".detail-header");
  var summary = document.querySelector(".sticky-summary");
  var preview = document.querySelector(".status-preview-row");
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[role='tab']"));
  var panels = Array.prototype.slice.call(document.querySelectorAll("[role='tabpanel']"));
  var twin = null;
  var progressTimer = 0;
  var progressPolls = 0;
  var selectedTwinId = ui.params().get("twin_id");
  var selectedTab = ui.params().get("tab") || "overview";
  var deltaPage = 0;
  var auditPage = 0;

  if (!adapter || !header || !summary || !tabs.length) return;

  function tabSlug(tab) {
    return tab.getAttribute("aria-controls").replace("panel-", "");
  }

  function decisionLabel(item) {
    return item.decision === "pending" ? ui.label(item.lifecycle_status) : ui.label(item.decision);
  }

  function actionButton(action) {
    var classes = action.code === "regenerate" || action.code === "generate" ? "btn primary" : action.code === "approve" ? "btn success" : action.code === "reject" ? "btn danger" : "btn";
    return '<button class="' + classes + '" type="button" data-detail-action="' + ui.escapeHtml(action.code) + '"' + (action.enabled ? "" : " disabled") + ' title="' + ui.escapeHtml(action.enabled ? action.label : action.reason) + '">' + ui.escapeHtml(action.label) + "</button>";
  }

  function actionsFor(item) {
    var active = ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0;
    var approved = item.relationships.approval_status === "approved";
    return [
      { code: active ? "cancel" : "regenerate", label: active ? "Cancel Generation" : "Generate / Regenerate", enabled: true, reason: "" },
      { code: "open_bundle", label: "Open Bundle", enabled: true, reason: "" },
      { code: "open_execution", label: "Open Execution", enabled: item.relationships.execution_status !== "unlinked", reason: "No Bundle Execution is linked." },
      { code: "start_execution", label: "Start Execution", enabled: item.decision === "green" || (item.decision === "amber" && approved), reason: item.decision === "amber" ? "A valid approval is required." : "The final decision is not eligible." },
      { code: "request_approval", label: "Request Approval", enabled: item.decision === "amber" && item.relationships.approval_status === "required", reason: "Approval is available only for a fresh Amber decision." },
      { code: "approve", label: "Approve", enabled: item.decision === "amber" && ["required", "pending"].indexOf(item.relationships.approval_status) >= 0, reason: "No approvable relationship is available." },
      { code: "reject", label: "Reject", enabled: item.decision === "amber" && ["required", "pending"].indexOf(item.relationships.approval_status) >= 0, reason: "No approvable relationship is available." },
      { code: "download", label: "Download Report", enabled: item.decision_is_final, reason: "A final report is not available." },
      { code: "export", label: "Export JSON", enabled: true, reason: "" }
    ];
  }

  function renderHeader(item) {
    var status = item.decision === "pending" ? item.lifecycle_status : item.decision;
    header.innerHTML = '<div><p class="eyebrow">Digital Twin · ' + (item.decision_is_final ? "Final Decision" : "Preliminary State") + '</p><div class="title-line"><h1 id="twin-title">' + ui.escapeHtml(item.display_name) + "</h1>" + ui.badge(status, decisionLabel(item)) + ui.badge(item.lifecycle_status) + '</div><p class="muted">Risk ' + ui.escapeHtml(item.risk.score == null ? "not calculated" : item.risk.score) + " · " + ui.escapeHtml(ui.label(item.autonomy_eligibility)) + " · " + ui.escapeHtml(item.recommended_action) + '</p></div><div class="action-row" aria-label="Twin actions">' + actionsFor(item).map(actionButton).join("") + "</div>";

    if (item.prior_decision && !item.decision_is_final) {
      preview.innerHTML = '<article class="status-preview"><span class="badge generating">Generating v' + item.decision_version + '</span><div><strong>New evidence is being evaluated</strong><p>Preliminary values cannot authorize execution.</p></div></article><article class="status-preview"><span class="badge ' + ui.badgeClass(item.prior_decision.decision) + '">Prior ' + ui.escapeHtml(ui.label(item.prior_decision.decision)) + '</span><div><strong>Previous evidence remains visible</strong><p>Decision v' + item.prior_decision.decision_version + " · risk " + ui.escapeHtml(item.prior_decision.risk.score) + " · now superseded.</p></div></article>";
    } else {
      preview.innerHTML = '<article class="status-preview"><span class="badge generating">Preliminary</span><div><strong>Generation states never authorize execution</strong><p>Requested, generating, dry-run, and calculation states stay visibly provisional.</p></div></article><article class="status-preview">' + ui.badge(status, item.decision_is_final ? "Final " + decisionLabel(item) : decisionLabel(item)) + '<div><strong>' + ui.escapeHtml(item.decision_is_final ? item.recommended_action : "Decision evidence is still being assembled.") + '</strong><p>' + ui.escapeHtml(item.freshness.message) + "</p></div></article>";
    }
  }

  function renderSummary(item) {
    var values = [
      ["Target", item.target.cluster_name + " / " + item.target.namespace, null],
      ["Bundle", item.bundle.bundle_name, "bundle"],
      ["Twin ID", item.twin_id, null],
      ["Release", item.bundle.release_version, null],
      ["Created By", item.created_by_display, null],
      ["Created", ui.formatDate(item.created_at), null],
      ["Freshness", ui.label(item.freshness.status), "drift"],
      ["Execution", ui.label(item.relationships.execution_status), "execution"],
      ["Approval", ui.label(item.relationships.approval_status), "approval"]
    ];
    summary.innerHTML = values.map(function (entry) {
      var fullValue = String(entry[1] == null ? "" : entry[1]);
      var content = entry[2] ? '<button class="summary-link" type="button" data-summary-target="' + entry[2] + '" title="' + ui.escapeHtml(fullValue) + '">' + ui.escapeHtml(fullValue) + "</button>" : '<strong title="' + ui.escapeHtml(fullValue) + '">' + ui.escapeHtml(fullValue) + "</strong>";
      return '<div class="meta-item"><span>' + ui.escapeHtml(entry[0]) + "</span>" + content + "</div>";
    }).join("");
  }

  function unavailableView(tab) {
    var titles = { loading: "Evidence is still generating", failed: "Evidence collection failed", not_run: "Module not run", not_available: "Evidence not available", stale: "Evidence is stale", empty: "No evidence found" };
    return ui.stateView(tab.state, titles[tab.state] || "Evidence state", tab.summary, tab.state === "failed");
  }

  function metricCards(metrics) {
    return '<div class="content-grid">' + metrics.map(function (metric) { return '<article class="content-block span-3"><span class="fact-label">' + ui.escapeHtml(metric.label) + '</span><div class="big-number">' + ui.escapeHtml(metric.value) + '</div><p class="muted">' + ui.escapeHtml(metric.note || "Fixture evidence") + "</p></article>"; }).join("") + "</div>";
  }

  function renderOverview(tab) {
    var reasons = tab.reasons.map(function (reason) { return '<li><button class="evidence-link" type="button" data-jump-tab="' + ui.escapeHtml(reason.tab) + '"' + (reason.finding ? ' data-jump-finding="' + ui.escapeHtml(reason.finding) + '"' : "") + '><strong>' + ui.escapeHtml(reason.title) + '</strong><span>' + ui.escapeHtml(reason.detail) + "</span></button></li>"; }).join("");
    return metricCards(tab.metrics) + '<div class="content-grid tab-followup"><article class="content-block span-7"><h3>Top Decision Reasons</h3><ol class="reason-list">' + reasons + '</ol></article><article class="content-block span-5"><h3>Recommended Action</h3><div class="notice amber">' + ui.escapeHtml(twin.recommended_action) + '</div><button class="btn" type="button" data-jump-tab="policy">Review policy evidence</button></article></div>';
  }

  function renderDelta(tab) {
    var pageSize = 25;
    var start = deltaPage * pageSize;
    var visible = tab.rows.slice(start, start + pageSize);
    var rows = visible.map(function (row) { return '<tr><td><button class="table-link" type="button" data-delta-row="' + ui.escapeHtml(row.id) + '">' + ui.escapeHtml(row.resource) + '</button></td><td>' + ui.escapeHtml(row.kind) + '</td><td>' + ui.badge(row.change === "created" ? "green" : row.change === "modified" ? "amber" : "info", row.change) + '</td><td>' + ui.escapeHtml(row.before) + '</td><td>' + ui.escapeHtml(row.after) + '</td><td>' + ui.escapeHtml(row.impact) + "</td></tr>"; }).join("");
    return '<div class="tab-toolbar"><span>' + tab.total_rows + ' resource deltas</span><div><button class="btn" type="button" data-copy-tab>Copy rows</button><button class="btn" type="button" data-open-diff>Side-by-side diff</button></div></div><div class="table-scroll"><table><thead><tr><th>Resource</th><th>Kind</th><th>Change</th><th>Before</th><th>After</th><th>Impact</th></tr></thead><tbody>' + rows + '</tbody></table></div><div class="table-footer"><span>Rows ' + (start + 1) + '–' + Math.min(start + pageSize, tab.total_rows) + ' of ' + tab.total_rows + '</span><div class="pagination"><button class="btn" type="button" data-delta-page="previous"' + (deltaPage === 0 ? " disabled" : "") + '>Previous</button><button class="btn" type="button" data-delta-page="next"' + (start + pageSize >= tab.total_rows ? " disabled" : "") + ">Next</button></div></div>";
  }

  function renderGraph(tab) {
    var graph = tab.graph;
    var visible = graph.nodes.slice(0, 48);
    var nodes = visible.map(function (node, index) {
      var left = 5 + (index % 8) * 12;
      var top = 8 + Math.floor(index / 8) * 15;
      return '<button class="mock-graph-node ' + (node.impact === "review" ? "review" : "") + '" style="left:' + left + "%;top:" + top + '%" type="button" data-graph-node="' + ui.escapeHtml(node.id) + '"><strong>' + ui.escapeHtml(node.kind) + '</strong><span>' + ui.escapeHtml(node.name) + "</span></button>";
    }).join("");
    var tableRows = graph.edges.slice(0, 30).map(function (edge) { return "<tr><td>" + ui.escapeHtml(edge.source) + "</td><td>" + ui.escapeHtml(edge.relationship) + "</td><td>" + ui.escapeHtml(edge.target) + "</td></tr>"; }).join("");
    return '<div class="tab-toolbar"><span>' + graph.nodes.length + " nodes · " + graph.edges.length + ' edges</span><div><button class="btn" type="button" data-graph-mode="canvas">Canvas</button><button class="btn" type="button" data-graph-mode="table">Table</button></div></div><div data-graph-canvas class="mock-graph-canvas">' + nodes + '<div class="graph-density-note">Showing 48 of ' + graph.nodes.length + ' nodes</div></div><div data-graph-table hidden class="table-scroll"><table><thead><tr><th>Source</th><th>Relationship</th><th>Target</th></tr></thead><tbody>' + tableRows + "</tbody></table></div>";
  }

  function renderFindings(tab) {
    var findings = tab.findings.map(function (finding) { return '<li id="' + ui.escapeHtml(finding.id) + '" data-finding><div>' + ui.badge(finding.severity === "block" ? "red" : "amber", finding.severity) + '<strong>' + ui.escapeHtml(finding.code + " · " + finding.title) + '</strong></div><p>' + ui.escapeHtml(finding.detail) + '</p><button class="btn" type="button" data-evidence-modal="' + ui.escapeHtml(finding.id) + '">Evidence</button></li>'; }).join("");
    return '<div class="content-grid"><article class="content-block span-8"><div class="tab-toolbar"><h3>Policy Findings</h3><div><button class="btn" type="button" data-policy-filter="all">All</button><button class="btn" type="button" data-policy-filter="review">Review</button><button class="btn" type="button" data-policy-filter="block">Blocking</button></div></div><ul class="finding-list policy-findings">' + findings + '</ul></article><article class="content-block span-4"><h3>Passed Groups</h3><ul class="list-clean">' + tab.passed_groups.map(function (group) { return "<li>✓ " + ui.escapeHtml(group) + "</li>"; }).join("") + '</ul><div class="notice green">All proposed mutations remain inside the selected namespace unless a blocking finding states otherwise.</div></article></div>';
  }

  function renderDryRun(tab) {
    return metricCards(tab.metrics) + '<div class="content-grid tab-followup"><article class="content-block span-8"><h3>Observations</h3><div class="log-view">' + tab.observations.map(function (line, index) { return "[" + String(index + 1).padStart(2, "0") + "] " + ui.escapeHtml(line); }).join("\n") + '</div></article><article class="content-block span-4"><h3>Fidelity Limits</h3><div class="notice amber">Runtime readiness, external DNS, storage attach latency, and application behavior remain outside server-side dry-run evidence.</div></article></div>';
  }

  function renderRollback(tab) {
    var rows = tab.evidence.map(function (row) { return "<tr><td>" + ui.escapeHtml(row.asset) + "</td><td>" + ui.badge(row.status === "high" ? "green" : "amber", row.status) + "</td><td>" + ui.escapeHtml(row.gap) + "</td></tr>"; }).join("");
    return '<div class="content-grid"><article class="content-block span-4"><span class="fact-label">Rollback confidence</span><div class="big-number">' + tab.confidence + '%</div><div class="progress-track"><div class="progress-fill" style="width:' + tab.confidence + '%"></div></div></article><article class="content-block span-8"><h3>Recovery Sequence</h3><ol class="reason-list">' + tab.steps.map(function (step) { return "<li>" + ui.escapeHtml(step) + "</li>"; }).join("") + '</ol></article><article class="content-block span-12"><table><thead><tr><th>Asset</th><th>Confidence</th><th>Gap</th></tr></thead><tbody>' + rows + "</tbody></table></article></div>";
  }

  function renderDrift(tab) {
    var rows = tab.changes.map(function (change) { return "<tr><td>" + ui.escapeHtml(change.resource) + "</td><td>" + ui.escapeHtml(change.change) + "</td><td>" + ui.badge(change.materiality === "material" ? "red" : "info", change.materiality) + "</td></tr>"; }).join("");
    return '<div class="content-grid"><article class="content-block span-4"><span class="fact-label">Snapshot age</span><div class="big-number">' + ui.escapeHtml(tab.snapshot_age) + '</div><p class="muted">Freshness limit: 2h</p></article><article class="content-block span-8"><div class="notice ' + (tab.material ? "red" : "green") + '">' + (tab.material ? "Material drift invalidates execution eligibility. Regenerate the twin." : "No material drift. The decision remains eligible inside its freshness window.") + '</div></article><article class="content-block span-12"><table><thead><tr><th>Resource</th><th>Observed change</th><th>Materiality</th></tr></thead><tbody>' + rows + "</tbody></table></article></div>";
  }

  function renderRuntime(tab) {
    return '<div class="content-grid">' + tab.signals.map(function (signal) { return '<article class="content-block span-6"><div class="mini-bar-row"><span>' + ui.escapeHtml(signal.label) + '</span><div class="progress-track"><div class="progress-fill" style="width:' + signal.score + '%"></div></div><strong>' + signal.score + '</strong></div><p class="muted">' + ui.escapeHtml(signal.detail) + "</p></article>"; }).join("") + "</div>";
  }

  function renderAudit(tab) {
    var pageSize = 20;
    var start = auditPage * pageSize;
    var events = tab.events.slice(start, start + pageSize);
    return '<div class="tab-toolbar"><span>' + tab.total_events + ' immutable safe-summary events</span><div><button class="btn" type="button" data-copy-tab>Copy page</button></div></div><ol class="timeline">' + events.map(function (event) { return '<li id="' + ui.escapeHtml(event.event_id) + '" data-audit-event><time>' + ui.escapeHtml(ui.formatDate(event.created_at) + " · " + event.event_id) + '</time><button class="timeline-link" type="button" data-audit-modal="' + ui.escapeHtml(event.event_id) + '"><strong>' + ui.escapeHtml(ui.label(event.event_type)) + '</strong></button><p>' + ui.escapeHtml(event.summary) + " · actor " + ui.escapeHtml(event.actor) + '</p></li>'; }).join("") + '</ol><div class="table-footer"><span>Events ' + (start + 1) + '–' + Math.min(start + pageSize, tab.total_events) + ' of ' + tab.total_events + '</span><div class="pagination"><button class="btn" type="button" data-audit-page="previous"' + (auditPage === 0 ? " disabled" : "") + '>Previous</button><button class="btn" type="button" data-audit-page="next"' + (start + pageSize >= tab.total_events ? " disabled" : "") + ">Next</button></div></div>";
  }

  function renderTab(tab) {
    if (tab.state !== "available") return unavailableView(tab);
    if (tab.kind === "overview") return renderOverview(tab);
    if (tab.kind === "delta") return renderDelta(tab);
    if (tab.kind === "graph") return renderGraph(tab);
    if (tab.kind === "findings") return renderFindings(tab);
    if (tab.kind === "dry-run") return renderDryRun(tab);
    if (tab.kind === "rollback") return renderRollback(tab);
    if (tab.kind === "drift") return renderDrift(tab);
    if (tab.kind === "runtime") return renderRuntime(tab);
    if (tab.kind === "audit") return renderAudit(tab);
    return unavailableView(tab);
  }

  function activateTab(slug, replace) {
    if (fixtures.tab_slugs.indexOf(slug) < 0) slug = "overview";
    selectedTab = slug;
    tabs.forEach(function (tab) {
      var selected = tabSlug(tab) === slug;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach(function (panel) { panel.hidden = panel.id !== "panel-" + slug; });
    deltaPage = slug === "release-delta" ? deltaPage : 0;
    auditPage = slug === "audit" ? auditPage : 0;
    ui.updateUrl({ tab: slug, twin_id: twin.twin_id }, replace);
    var panel = document.getElementById("panel-" + slug);
    panel.innerHTML = ui.stateView("loading", "Loading " + ui.label(slug), "Reading the versioned browser fixture through TwinDataAdapter.", false);
    adapter.getTab(twin.twin_id, slug, twin.decision_version).then(function (tabData) {
      if (slug === "audit" && ui.params().get("event")) {
        var eventIndex = tabData.events.findIndex(function (item) { return item.event_id === ui.params().get("event"); });
        if (eventIndex >= 0) auditPage = Math.floor(eventIndex / 20);
      }
      panel.innerHTML = '<div class="tab-intro"><div><p class="eyebrow">Fixture Evidence · ' + ui.escapeHtml(ui.label(tabData.state)) + '</p><h2>' + ui.escapeHtml(tabData.title) + '</h2><p>' + ui.escapeHtml(tabData.summary) + '</p></div>' + ui.badge(tabData.state) + '</div><div data-tab-content>' + renderTab(tabData) + '</div>';
      panel._tabData = tabData;
      applyDeepLink(panel);
    }).catch(function (error) {
      panel.innerHTML = ui.stateView("failed", "Evidence unavailable", error.message, error.retryable);
    });
  }

  function applyDeepLink(panel) {
    var finding = ui.params().get("finding");
    var eventId = ui.params().get("event");
    var escapeSelector = window.CSS && window.CSS.escape ? window.CSS.escape : function (value) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); };
    var target = finding ? panel.querySelector("#" + escapeSelector(finding)) : eventId ? panel.querySelector("#" + escapeSelector(eventId)) : null;
    if (target) {
      target.classList.add("deep-link-target");
      target.scrollIntoView({ block: "center" });
    }
  }

  function renderTwin(item) {
    twin = item;
    summary.hidden = false;
    preview.hidden = false;
    selectedTwinId = item.twin_id;
    document.title = item.display_name + " | Digital Twins";
    renderHeader(item);
    renderSummary(item);
    activateTab(selectedTab, true);
    setupProgress(item);
  }

  function noSelection() {
    header.innerHTML = '<div><p class="eyebrow">Digital Twin</p><h1>Select a twin run</h1><p class="muted">Terminal evidence is restored only when a run is explicitly selected. Active browser-only runs restore automatically.</p></div><a class="btn primary" href="digital-twins.html">Open Digital Twins</a>';
    summary.hidden = true;
    preview.hidden = true;
    panels.forEach(function (panel, index) {
      panel.hidden = index !== 0;
      if (index === 0) panel.innerHTML = ui.stateView("empty", "No twin selected", "Choose a twin from the list or start a browser-only mock generation.", false);
    });
  }

  function setupProgress(item) {
    window.clearInterval(progressTimer);
    progressPolls = 0;
    if (item.twin_id.indexOf("twin_mock_") !== 0 || ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) < 0) return;
    progressTimer = window.setInterval(function () {
      progressPolls += 1;
      adapter.advanceGeneration(item.twin_id).then(renderTwin);
      if (progressPolls >= 5) window.clearInterval(progressTimer);
    }, 1800);
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { if (twin) activateTab(tabSlug(tab), false); });
  });

  document.addEventListener("click", function (event) {
    var action = event.target.closest("[data-detail-action]");
    if (action && twin) {
      var code = action.getAttribute("data-detail-action");
      if (code === "regenerate") adapter.regenerate(twin.twin_id).then(function (next) { selectedTab = "overview"; renderTwin(next); ui.showToast("New mock decision version started.", "success"); });
      if (code === "cancel") adapter.cancelGeneration(twin.twin_id).then(renderTwin);
      if (code === "open_bundle") {
        var drawer = document.getElementById("twin-evidence-drawer");
        drawer.querySelector("[data-drawer-content]").innerHTML = '<div class="log-view">' + ui.escapeHtml(JSON.stringify({ bundle_id: twin.bundle.bundle_id, sha256: twin.bundle.bundle_hash, release_version: twin.bundle.release_version, target: twin.target }, null, 2)) + "</div>";
        drawer.hidden = false;
      }
      if (code === "open_execution" || code === "start_execution") window.location.href = "bundle-execution-twin-gate.html?twin_id=" + encodeURIComponent(twin.twin_id);
      if (code === "request_approval") adapter.requestApproval(twin.twin_id).then(function (updated) { renderTwin(updated); ui.showToast("Mock approval requested.", "success"); });
      if (code === "approve") adapter.approveTwin(twin.twin_id).then(function (updated) { renderTwin(updated); ui.showToast("Mock approval accepted.", "success"); });
      if (code === "reject") adapter.rejectTwin(twin.twin_id).then(function (updated) { renderTwin(updated); ui.showToast("Mock approval rejected.", "info"); });
      if (code === "download") ui.mockDownload(twin.twin_id + "-decision-report.json", twin);
      if (code === "export") ui.mockDownload(twin.twin_id + ".json", twin);
      return;
    }
    var jump = event.target.closest("[data-jump-tab]");
    if (jump && twin) {
      ui.updateUrl({ finding: jump.getAttribute("data-jump-finding") || null, event: null }, true);
      activateTab(jump.getAttribute("data-jump-tab"), false);
    }
    var summaryTarget = event.target.closest("[data-summary-target]");
    if (summaryTarget && twin) {
      var target = summaryTarget.getAttribute("data-summary-target");
      if (target === "bundle") ui.showModal({ title: twin.bundle.bundle_name, body: '<div class="log-view">' + ui.escapeHtml(twin.bundle.bundle_hash) + "</div>" });
      else if (target === "execution") window.location.href = "bundle-execution-twin-gate.html?twin_id=" + encodeURIComponent(twin.twin_id);
      else if (target === "approval") activateTab("overview", false);
      else activateTab(target, false);
    }
    var diff = event.target.closest("[data-open-diff]");
    if (diff) {
      var data = document.getElementById("panel-release-delta")._tabData.diff;
      ui.showModal({ eyebrow: "Release Delta", title: data.resource, body: '<div class="diff-grid"><pre class="diff-pane">CURRENT\n' + ui.escapeHtml(data.before) + '</pre><pre class="diff-pane">PROPOSED\n' + ui.escapeHtml(data.after) + "</pre></div>" });
    }
    var evidence = event.target.closest("[data-evidence-modal]");
    if (evidence) ui.showModal({ eyebrow: "Policy Evidence", title: evidence.getAttribute("data-evidence-modal"), body: '<div class="log-view">Evidence hash: 5eb1c90a...\nPolicy pack: esda-release-safety@2.4.1\nDecision authority: deterministic-twin-engine\nHidden reasoning stored: false</div>' });
    var graphNode = event.target.closest("[data-graph-node]");
    if (graphNode) ui.showModal({ eyebrow: "Dependency Node", title: graphNode.textContent.trim(), body: '<div class="notice amber">Node detail is read from the selected graph fixture. Relationship evidence is namespace-scoped and redacted.</div>' });
    var auditModal = event.target.closest("[data-audit-modal]");
    if (auditModal) ui.showModal({ eyebrow: "Audit Event", title: auditModal.getAttribute("data-audit-modal"), body: '<div class="log-view">Safe event summary only.\nActor and correlation metadata are preserved.\nHidden model reasoning is not stored.</div>' });
    var graphMode = event.target.closest("[data-graph-mode]");
    if (graphMode) {
      var panel = document.getElementById("panel-dependency-graph");
      panel.querySelector("[data-graph-canvas]").hidden = graphMode.getAttribute("data-graph-mode") !== "canvas";
      panel.querySelector("[data-graph-table]").hidden = graphMode.getAttribute("data-graph-mode") !== "table";
    }
    var deltaPager = event.target.closest("[data-delta-page]");
    if (deltaPager) {
      deltaPage = Math.max(0, deltaPage + (deltaPager.getAttribute("data-delta-page") === "next" ? 1 : -1));
      var deltaPanel = document.getElementById("panel-release-delta");
      deltaPanel.querySelector("[data-tab-content]").innerHTML = renderDelta(deltaPanel._tabData);
    }
    var auditPager = event.target.closest("[data-audit-page]");
    if (auditPager) {
      auditPage = Math.max(0, auditPage + (auditPager.getAttribute("data-audit-page") === "next" ? 1 : -1));
      var auditPanel = document.getElementById("panel-audit");
      auditPanel.querySelector("[data-tab-content]").innerHTML = renderAudit(auditPanel._tabData);
    }
    var copy = event.target.closest("[data-copy-tab]");
    if (copy) {
      var activePanel = document.querySelector("[role='tabpanel']:not([hidden])");
      ui.copyText(JSON.stringify(activePanel._tabData, null, 2), "Tab fixture copied.");
    }
    var retry = event.target.closest("[data-retry]");
    if (retry && twin) activateTab(selectedTab, true);
  });

  window.addEventListener("popstate", function () {
    var requestedTwinId = ui.params().get("twin_id");
    if (!requestedTwinId) {
      twin = null;
      noSelection();
      return;
    }
    if (!twin || twin.twin_id !== requestedTwinId) {
      adapter.getTwin(requestedTwinId).then(renderTwin).catch(noSelection);
      return;
    }
    var slug = ui.params().get("tab") || "overview";
    activateTab(slug, true);
  });

  function begin() {
    if (selectedTwinId) {
      adapter.getTwin(selectedTwinId).then(renderTwin).catch(noSelection);
      return;
    }
    adapter.getActiveTwin().then(function (active) { if (active) renderTwin(active); else noSelection(); });
  }

  begin();
})();
