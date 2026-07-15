(function () {
  "use strict";

  var adapter = window.esdaTwinAdapter;
  var ui = window.ESDATwinUI;
  var tabSlugs = ["overview", "release-delta", "dependency-graph", "policy", "dry-run", "rollback", "drift", "mop-replay", "runtime-behavior", "release-note-validation", "audit"];
  var header = document.querySelector(".detail-header");
  var summary = document.querySelector(".sticky-summary");
  var preview = document.querySelector(".status-preview-row");
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[role='tab']"));
  var panels = Array.prototype.slice.call(document.querySelectorAll("[role='tabpanel']"));
  var twin = null;
  var progressTimer = 0;
  var selectedTwinId = ui.params().get("twin_id");
  var selectedTab = ui.params().get("tab") || "overview";
  var deltaFilters = { action: "", risk: "", kind: "", limit: 25, cursor: null };
  var deltaCursorHistory = [];
  var auditPage = 0;

  if (!adapter || !header || !summary || !tabs.length) return;

  function tabSlug(tab) {
    return tab.getAttribute("aria-controls").replace("panel-", "");
  }

  function decisionLabel(item) {
    return item.decision === "pending" ? ui.label(item.visible_lifecycle) : ui.label(item.decision);
  }

  function actionButton(action) {
    var primary = ["regenerate_twin", "start_execution", "request_approval"].indexOf(action.code) >= 0;
    var classes = primary ? "btn primary" : "btn";
    var title = action.enabled ? action.label : action.disabled_reason || action.reason_code || "Unavailable";
    return '<button class="' + classes + '" type="button" data-detail-action="' + ui.escapeHtml(action.code) + '"' + (action.enabled ? "" : " disabled") + ' title="' + ui.escapeHtml(title) + '">' + ui.escapeHtml(action.label) + "</button>";
  }

  function actionsFor(item) {
    return Array.isArray(item.actions)
      ? item.actions.filter(function (action) { return action.visible !== false; })
      : [];
  }

  function renderHeader(item) {
    item = Object.assign({}, item, { lifecycle_status: item.visible_lifecycle || item.lifecycle_status });
    var status = item.decision === "pending" ? item.visible_lifecycle : item.decision;
    header.innerHTML = '<div><p class="eyebrow">Digital Twin · ' + (item.decision_is_final ? "Final Decision" : "Preliminary State") + '</p><div class="title-line"><h1 id="twin-title">' + ui.escapeHtml(item.display_name) + "</h1>" + ui.badge(status, decisionLabel(item)) + ui.badge(item.lifecycle_status) + '</div><p class="muted">Risk ' + ui.escapeHtml(item.risk.score == null ? "not calculated" : item.risk.score) + " · " + ui.escapeHtml(ui.label(item.autonomy_eligibility)) + " · " + ui.escapeHtml(item.recommended_action) + '</p></div><div class="action-row" aria-label="Twin actions">' + actionsFor(item).map(actionButton).join("") + "</div>";

    if (item.prior_decision && !item.decision_is_final) {
      preview.innerHTML = '<article class="status-preview"><span class="badge generating">Generating v' + item.decision_version + '</span><div><strong>New evidence is being evaluated</strong><p>Preliminary values cannot authorize execution.</p></div></article><article class="status-preview"><span class="badge ' + ui.badgeClass(item.prior_decision.decision) + '">Prior ' + ui.escapeHtml(ui.label(item.prior_decision.decision)) + '</span><div><strong>Previous evidence remains visible</strong><p>Decision v' + item.prior_decision.decision_version + " · risk " + ui.escapeHtml(item.prior_decision.risk.score) + " · now superseded.</p></div></article>";
    } else {
      preview.innerHTML = '<article class="status-preview"><span class="badge generating">Preliminary</span><div><strong>Generation states never authorize execution</strong><p>Requested, generating, dry-run, and calculation states stay visibly provisional.</p></div></article><article class="status-preview">' + ui.badge(status, item.decision_is_final ? "Final " + decisionLabel(item) : decisionLabel(item)) + '<div><strong>' + ui.escapeHtml(item.decision_is_final ? item.recommended_action : "Decision evidence is still being assembled.") + '</strong><p>' + ui.escapeHtml(item.freshness.message) + "</p></div></article>";
    }
  }

  function renderSummary(item) {
    var values = [
      ["Target Cluster", item.target.cluster_name, null],
      ["Namespace", item.target.namespace, null],
      ["Bundle", item.bundle.bundle_name + " / " + item.bundle.bundle_id, "bundle"],
      ["Bundle Hash", item.bundle.bundle_hash, "bundle"],
      ["Twin", item.twin_id + " / v" + item.decision_version, null],
      ["Release", item.bundle.release_version, null],
      ["Created By", item.created_by_display, null],
      ["Created / Updated", ui.formatDate(item.created_at) + " / " + ui.formatDate(item.updated_at), null],
      ["Freshness / Expiry", ui.label(item.freshness.status) + " / " + ui.formatDate(item.freshness.expires_at), "drift"],
      ["Dry-run", item.relationships.dry_run_job_id || "Not linked", null],
      ["Approval", ui.label(item.relationships.approval_status), "approval"],
      ["Execution", ui.label(item.relationships.execution_status), "execution"]
    ];
    summary.innerHTML = values.map(function (entry) {
      var content = entry[2] ? '<button class="summary-link" type="button" data-summary-target="' + entry[2] + '">' + ui.escapeHtml(entry[1]) + "</button>" : "<strong>" + ui.escapeHtml(entry[1]) + "</strong>";
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
    var explanation = tab.safe_explanation ? '<article class="content-block span-12"><p class="eyebrow">SIGMA 5 PRO / Bounded Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">Deterministic decisions, risk, freshness, and action eligibility remain unchanged.</p></article>' : "";
    return metricCards(tab.metrics) + '<div class="content-grid tab-followup">' + explanation + '<article class="content-block span-7"><h3>Top Decision Reasons</h3><ol class="reason-list">' + reasons + '</ol></article><article class="content-block span-5"><h3>Recommended Action</h3><div class="notice amber">' + ui.escapeHtml(tab.recommended_action) + '</div><button class="btn" type="button" data-jump-tab="policy">Review policy evidence</button></article></div>';
  }

  function renderDelta(tab) {
    var tabData = tab.data || {};
    var changes = Array.isArray(tabData.changes) ? tabData.changes : [];
    if (!changes.length && Array.isArray(tab.rows)) {
      changes = tab.rows.map(function (row) {
        var parts = String(row.resource || "Unknown/unknown").split("/");
        var action = row.change === "modified" ? "update" : row.change === "created" ? "create" : row.change === "unchanged" ? "no_op" : "unknown";
        return {
          change_id: row.id,
          resource_identity: "fixture:" + String(row.resource || row.id),
          kind: row.kind || parts[0],
          namespace: twin && twin.target ? twin.target.namespace : "fixture",
          name: parts.slice(1).join("/") || row.id,
          action: action,
          current_summary: row.before,
          planned_summary: row.after,
          risk: row.impact === "operator review" ? "high" : "low",
          reason: "Browser fixture projection: " + String(row.impact || "bounded") + ".",
          canonical_diff: JSON.stringify({ current: row.before, planned: row.after, field_changes: [] })
        };
      });
    }
    var counts = tabData.summary || changes.reduce(function (result, row) {
      result.total += 1;
      result[row.action] = (result[row.action] || 0) + 1;
      return result;
    }, { total: 0, create: 0, update: 0, explicit_delete: 0, no_op: 0, unknown: 0, immutable_conflict: 0 });
    var page = tabData.page || { limit: changes.length || 25, has_more: false, next_cursor: null, result_count: Number(tab.total_rows || changes.length) };
    var actionOptions = ["", "create", "update", "explicit_delete", "no_op", "unknown", "immutable_conflict", "namespace_rewrite"];
    var riskOptions = ["", "low", "medium", "high", "critical", "unknown"];
    var kindOptions = ["", "ConfigMap", "Deployment", "StatefulSet", "Service", "Ingress", "PersistentVolumeClaim", "Secret", "Role", "RoleBinding", "ServiceAccount"];
    function option(value, selected) {
      var label = value ? ui.label(value) : "All";
      return '<option value="' + ui.escapeHtml(value) + '"' + (value === selected ? " selected" : "") + ">" + ui.escapeHtml(label) + "</option>";
    }
    function actionClass(value) {
      if (value === "immutable_conflict" || value === "explicit_delete") return "red";
      if (value === "update" || value === "unknown") return "amber";
      if (value === "no_op") return "info";
      return "green";
    }
    function riskClass(value) {
      if (value === "critical" || value === "high") return "red";
      if (value === "medium" || value === "unknown") return "amber";
      return "green";
    }
    var summaryKeys = ["total", "create", "update", "explicit_delete", "no_op", "unknown", "immutable_conflict"];
    var summaryCards = summaryKeys.map(function (key) {
      return '<article class="delta-summary-card"><span>' + ui.escapeHtml(ui.label(key)) + '</span><strong>' + ui.escapeHtml(counts[key] || 0) + "</strong></article>";
    }).join("");
    var rows = changes.map(function (row) {
      var riskRow = row.risk === "critical" || row.risk === "high" ? ' class="delta-risk-row"' : "";
      return "<tr" + riskRow + '><td><button class="table-link" type="button" data-delta-row="' + ui.escapeHtml(row.change_id) + '">' + ui.escapeHtml(row.name) + '</button><small>' + ui.escapeHtml(row.resource_identity) + '</small></td><td>' + ui.escapeHtml(row.kind) + '</td><td>' + ui.escapeHtml(row.namespace || "cluster-scoped") + '</td><td>' + ui.badge(actionClass(row.action), ui.label(row.action)) + '</td><td>' + ui.escapeHtml(row.current_summary || "Absent / unavailable") + '</td><td>' + ui.escapeHtml(row.planned_summary || "Explicit deletion") + '</td><td>' + ui.badge(riskClass(row.risk), ui.label(row.risk)) + '</td><td>' + ui.escapeHtml(row.reason) + "</td></tr>";
    }).join("");
    if (!rows) rows = '<tr><td colspan="8"><div class="empty-inline">No Release Delta rows match the selected filters.</div></td></tr>';
    var explanation = tab.safe_explanation
      ? '<article class="content-block delta-explanation"><p class="eyebrow">SIGMA 5 PRO / Bounded Delta Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">Generated only from structured, redacted delta facts. Hidden reasoning is not retained.</p></article>'
      : "";
    var start = page.result_count ? deltaCursorHistory.length * Number(page.limit || 25) + 1 : 0;
    var end = page.result_count ? start + changes.length - 1 : 0;
    return '<div class="delta-summary-grid">' + summaryCards + '</div>' + explanation
      + '<div class="tab-toolbar delta-toolbar"><div class="delta-filter-grid"><label>Action<select data-delta-filter="action">' + actionOptions.map(function (value) { return option(value, deltaFilters.action); }).join("") + '</select></label><label>Risk<select data-delta-filter="risk">' + riskOptions.map(function (value) { return option(value, deltaFilters.risk); }).join("") + '</select></label><label>Kind<select data-delta-filter="kind">' + kindOptions.map(function (value) { return option(value, deltaFilters.kind); }).join("") + '</select></label></div><button class="btn" type="button" data-copy-tab>Copy facts</button></div>'
      + '<div class="table-scroll"><table class="delta-table"><thead><tr><th>Resource</th><th>Kind</th><th>Namespace</th><th>Action</th><th>Current</th><th>Planned</th><th>Risk</th><th>Reason</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      + '<div class="table-footer"><span>Rows ' + start + "-" + end + " of " + Number(page.result_count || 0) + '</span><div class="pagination"><button class="btn" type="button" data-delta-page="previous"' + (deltaCursorHistory.length ? "" : " disabled") + '>Previous</button><button class="btn" type="button" data-delta-page="next"' + (page.has_more ? "" : " disabled") + ">Next</button></div></div>";
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
    if (tabSlugs.indexOf(slug) < 0) slug = "overview";
    selectedTab = slug;
    tabs.forEach(function (tab) {
      var selected = tabSlug(tab) === slug;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach(function (panel) { panel.hidden = panel.id !== "panel-" + slug; });
    auditPage = slug === "audit" ? auditPage : 0;
    ui.updateUrl({ tab: slug, twin_id: twin.twin_id }, replace);
    var panel = document.getElementById("panel-" + slug);
    panel.innerHTML = ui.stateView("loading", "Loading " + ui.label(slug), "Reading this evidence module through the configured data adapter.", false);
    var tabQuery = slug === "release-delta" ? Object.assign({}, deltaFilters) : {};
    adapter.getTab(twin.twin_id, slug, twin.decision_version, tabQuery).then(function (tabData) {
      if (slug === "audit" && ui.params().get("event")) {
        var eventIndex = tabData.events.findIndex(function (item) { return item.event_id === ui.params().get("event"); });
        if (eventIndex >= 0) auditPage = Math.floor(eventIndex / 20);
      }
      panel.innerHTML = '<div class="tab-intro"><div><p class="eyebrow">' + (tabData.non_authoritative ? "Mock / Non-authoritative Module" : "Real Evidence") + ' · ' + ui.escapeHtml(ui.label(tabData.state)) + '</p><h2>' + ui.escapeHtml(tabData.title) + '</h2><p>' + ui.escapeHtml(tabData.summary) + '</p></div>' + ui.badge(tabData.state) + '</div><div data-tab-content>' + renderTab(tabData) + '</div>';
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
    header.innerHTML = '<div><p class="eyebrow">Digital Twin</p><h1>Select a twin run</h1><p class="muted">Terminal evidence is restored only when a run is explicitly selected. Active runs restore automatically.</p></div><a class="btn primary" href="digital-twins.html">Open Digital Twins</a>';
    summary.hidden = true;
    preview.hidden = true;
    panels.forEach(function (panel, index) {
      panel.hidden = index !== 0;
      if (index === 0) panel.innerHTML = ui.stateView("empty", "No twin selected", "Choose a twin from the list or start a new generation.", false);
    });
  }

  function setupProgress(item) {
    window.clearTimeout(progressTimer);
    var active = ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0;
    if (!active) return;
    progressTimer = window.setTimeout(function () {
      var refresh = item.twin_id.indexOf("twin_mock_") === 0
        ? adapter.advanceGeneration(item.twin_id)
        : adapter.getTwin(item.twin_id);
      refresh.then(renderTwin).catch(function () {
        progressTimer = window.setTimeout(function () { setupProgress(item); }, 2500);
      });
    }, 1800);
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { if (twin) activateTab(tabSlug(tab), false); });
  });

  document.addEventListener("click", function (event) {
    var action = event.target.closest("[data-detail-action]");
    if (action && twin) {
      var code = action.getAttribute("data-detail-action");
      var contract = actionsFor(twin).find(function (item) { return item.code === code; });
      if (!contract || !contract.enabled) return;
      if (code === "regenerate_twin") ui.navigate(contract.href);
      if (code === "cancel_generation") adapter.cancelGeneration(twin.twin_id).then(renderTwin);
      if (code === "open_bundle") {
        var drawer = document.getElementById("twin-evidence-drawer");
        drawer.querySelector("[data-drawer-content]").innerHTML = '<div class="log-view">' + ui.escapeHtml(JSON.stringify({ bundle_id: twin.bundle.bundle_id, sha256: twin.bundle.bundle_hash, release_version: twin.bundle.release_version, target: twin.target }, null, 2)) + "</div>";
        drawer.hidden = false;
      }
      if (code === "start_execution" || code === "request_approval") ui.navigate(contract.href);
      if (code === "download_report") ui.navigate(contract.href);
      if (code === "export_evidence") ui.navigate(contract.href);
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
      else if (target === "execution") ui.navigate("/mop-execution?twin_id=" + encodeURIComponent(twin.twin_id));
      else if (target === "approval") activateTab("overview", false);
      else activateTab(target, false);
    }
    var deltaRow = event.target.closest("[data-delta-row]");
    if (deltaRow) {
      var deltaTab = document.getElementById("panel-release-delta")._tabData;
      var change = (deltaTab.data.changes || []).find(function (item) { return item.change_id === deltaRow.getAttribute("data-delta-row"); });
      if (change) {
        var canonical = {};
        try { canonical = JSON.parse(change.canonical_diff || "{}"); } catch (error) { canonical = { parse_error: error.message }; }
        ui.showModal({
          eyebrow: ui.label(change.action) + " / " + ui.label(change.risk) + " risk",
          title: change.resource_identity,
          body: '<p>' + ui.escapeHtml(change.reason) + '</p><div class="diff-grid"><pre class="diff-pane">CURRENT (CANONICAL)\n' + ui.escapeHtml(JSON.stringify(canonical.current, null, 2)) + '</pre><pre class="diff-pane">PLANNED (CANONICAL)\n' + ui.escapeHtml(JSON.stringify(canonical.planned, null, 2)) + '</pre></div><pre class="log-view">' + ui.escapeHtml(JSON.stringify(canonical.field_changes || [], null, 2)) + '</pre>'
        });
      }
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
      var deltaPanel = document.getElementById("panel-release-delta");
      var deltaPageData = (deltaPanel._tabData.data || {}).page || {};
      if (deltaPager.getAttribute("data-delta-page") === "next" && deltaPageData.next_cursor) {
        deltaCursorHistory.push(deltaFilters.cursor);
        deltaFilters.cursor = deltaPageData.next_cursor;
      } else if (deltaPager.getAttribute("data-delta-page") === "previous" && deltaCursorHistory.length) {
        deltaFilters.cursor = deltaCursorHistory.pop() || null;
      }
      activateTab("release-delta", true);
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
      ui.copyText(JSON.stringify(activePanel._tabData, null, 2), "Tab data copied.");
    }
    var retry = event.target.closest("[data-retry]");
    if (retry && twin) activateTab(selectedTab, true);
  });

  document.addEventListener("change", function (event) {
    var filter = event.target.closest("[data-delta-filter]");
    if (!filter || !twin) return;
    deltaFilters[filter.getAttribute("data-delta-filter")] = filter.value;
    deltaFilters.cursor = null;
    deltaCursorHistory = [];
    activateTab("release-delta", true);
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
