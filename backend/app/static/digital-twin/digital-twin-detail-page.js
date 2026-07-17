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
  var graphFilters = {
    kind: "",
    risk: "",
    status: "",
    namespace: "",
    relationship: "",
    confidence: "",
    edge_status: "",
    search: "",
    missing_only: "",
    resource: ui.params().get("resource") || "",
    node_cursor: null,
    edge_cursor: null,
    limit: 50
  };
  var graphNodeCursorHistory = [];
  var graphEdgeCursorHistory = [];
  var graphDisplayMode = "canvas";
  var auditPage = 0;
  var policyFilter = "all";

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
    var data = tab.data || {};
    if (!tab.data && tab.graph) {
      var mockNodes = Array.isArray(tab.graph.nodes) ? tab.graph.nodes : [];
      var mockEdges = Array.isArray(tab.graph.edges) ? tab.graph.edges : [];
      var mockNodeLabels = {};
      mockNodes.forEach(function (node) { mockNodeLabels[node.id] = node.kind + "/" + node.name; });
      data = {
        summary: {
          nodes: mockNodes.length,
          edges: mockEdges.length,
          missing_nodes: 0,
          uncertain_nodes: mockNodes.filter(function (node) { return node.impact === "review"; }).length,
          high_risk_nodes: 0,
          cycles: 0
        },
        nodes: mockNodes.map(function (node) {
          return {
            node_id: node.id,
            resource_identity: "mock:" + node.kind + ":" + node.name,
            kind: node.kind,
            namespace: "mock-browser",
            name: node.name,
            status: node.impact === "review" ? "uncertain" : "present",
            risk: node.impact === "review" ? "medium" : "low",
            confidence: "mock",
            evidence_refs: []
          };
        }),
        edges: mockEdges.map(function (edge, index) {
          return {
            edge_id: "mock-edge-" + index,
            source: edge.source,
            target: edge.target,
            relationship: edge.relationship,
            status: "valid",
            confidence: "mock",
            evidence_refs: []
          };
        }),
        table_rows: mockEdges.map(function (edge, index) {
          return {
            edge_id: "mock-edge-" + index,
            source_label: mockNodeLabels[edge.source] || edge.source,
            target_label: mockNodeLabels[edge.target] || edge.target,
            relationship: edge.relationship,
            status: "valid",
            confidence: "mock",
            evidence_refs: []
          };
        }),
        node_page: { result_count: mockNodes.length, has_more: false, next_cursor: null },
        edge_page: { result_count: mockEdges.length, has_more: false, next_cursor: null },
        selected_context: { found: false }
      };
    }
    var nodes = Array.isArray(data.nodes) ? data.nodes : [];
    var edges = Array.isArray(data.edges) ? data.edges : [];
    var rows = Array.isArray(data.table_rows) ? data.table_rows : [];
    var counts = data.summary || {};
    var nodePage = data.node_page || {};
    var edgePage = data.edge_page || {};
    var selected = data.selected_context || { found: false };
    var columns = Math.min(6, Math.max(1, Math.ceil(Math.sqrt(nodes.length * 1.4))));
    var rowCount = Math.max(1, Math.ceil(nodes.length / columns));
    var canvasHeight = Math.max(360, rowCount * 118);
    var positions = {};
    nodes.forEach(function (node, index) {
      var column = index % columns;
      var row = Math.floor(index / columns);
      positions[node.node_id] = {
        x: columns === 1 ? 500 : 80 + column * (840 / (columns - 1)),
        y: 62 + row * 108
      };
    });
    function option(value, selectedValue) {
      return '<option value="' + ui.escapeHtml(value) + '"' + (value === selectedValue ? " selected" : "") + ">" + ui.escapeHtml(value ? ui.label(value) : "All") + "</option>";
    }
    function stateClass(value) {
      if (value === "missing" || value === "critical" || value === "high") return "red";
      if (value === "uncertain" || value === "medium" || value === "unknown") return "amber";
      return "green";
    }
    var kindOptions = ["", "ConfigMap", "Secret", "PersistentVolumeClaim", "ServiceAccount", "Service", "Ingress", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Role", "RoleBinding", "ClusterRole", "CustomResourceDefinition", "HelmRelease", "PlanPhase", "ManifestArtifact"];
    var relationshipOptions = ["", "owner_reference", "selector_matches", "route_backend", "configmap_ref", "secret_name_ref", "pvc_ref", "service_account_ref", "helm_owns_resource", "rbac_role_ref", "rbac_subject", "crd_owns_custom_resource", "plan_applies", "plan_depends_on"];
    var summaryCards = [
      ["Nodes", counts.nodes || 0],
      ["Edges", counts.edges || 0],
      ["Missing", counts.missing_nodes || counts.missing || 0],
      ["Uncertain", counts.uncertain_nodes || counts.uncertain || 0],
      ["High Risk", counts.high_risk_nodes || 0],
      ["Cycles", counts.cycles || 0]
    ].map(function (item) {
      return '<article class="graph-summary-card"><span>' + ui.escapeHtml(item[0]) + '</span><strong>' + ui.escapeHtml(item[1]) + "</strong></article>";
    }).join("");
    var svgEdges = edges.map(function (edge) {
      var source = positions[edge.source];
      var target = positions[edge.target];
      if (!source || !target) return "";
      return '<line class="graph-edge ' + stateClass(edge.status) + '" x1="' + source.x + '" y1="' + source.y + '" x2="' + target.x + '" y2="' + target.y + '"><title>' + ui.escapeHtml(ui.label(edge.relationship) + " / " + edge.confidence) + "</title></line>";
    }).join("");
    var nodeButtons = nodes.map(function (node) {
      var point = positions[node.node_id];
      var selectedClass = graphFilters.resource === node.node_id ? " selected" : "";
      return '<button class="mock-graph-node ' + stateClass(node.status) + selectedClass + '" style="left:' + (point.x / 10) + "%;top:" + point.y + 'px" type="button" data-graph-node="' + ui.escapeHtml(node.node_id) + '" title="' + ui.escapeHtml(node.resource_identity) + '"><strong>' + ui.escapeHtml(node.kind) + '</strong><span>' + ui.escapeHtml(node.name) + '</span><small>' + ui.escapeHtml(ui.label(node.status)) + "</small></button>";
    }).join("");
    var tableRows = rows.map(function (edge) {
      return '<tr class="' + stateClass(edge.status) + '"><td>' + ui.escapeHtml(edge.source_label) + '</td><td>' + ui.escapeHtml(ui.label(edge.relationship)) + '</td><td>' + ui.escapeHtml(edge.target_label) + '</td><td>' + ui.badge(stateClass(edge.status), ui.label(edge.status)) + '</td><td>' + ui.escapeHtml(ui.label(edge.confidence)) + '</td><td>' + ui.escapeHtml((edge.evidence_refs || []).length) + "</td></tr>";
    }).join("");
    if (!tableRows) tableRows = '<tr><td colspan="6"><div class="empty-inline">No dependency edges match the selected filters.</div></td></tr>';
    var explanation = tab.safe_explanation
      ? '<article class="content-block graph-explanation"><p class="eyebrow">SIGMA 5 PRO / Bounded Impact Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">Generated only from server-returned graph facts and impact paths. The browser infers no relationships.</p></article>'
      : "";
    var selectedPanel = "";
    if (selected.found) {
      var selectedNode = selected.node || {};
      var paths = (selected.impact_paths || []).map(function (path) {
        return '<li><strong>' + ui.escapeHtml((path.nodes || []).join(" -> ")) + '</strong><span>' + ui.escapeHtml((path.relationships || []).map(ui.label).join(" -> ")) + ' / ' + ui.escapeHtml(ui.label(path.status)) + ' / ' + ui.escapeHtml(ui.label(path.confidence)) + "</span></li>";
      }).join("");
      selectedPanel = '<article class="content-block graph-selected-context"><div class="tab-toolbar"><div><p class="eyebrow">Selected Resource</p><h3>' + ui.escapeHtml(selectedNode.kind + "/" + selectedNode.name) + '</h3></div><button class="btn" type="button" data-graph-clear-selection>Clear selection</button></div><p>' + ui.escapeHtml(selectedNode.resource_identity) + '</p><div class="graph-context-metrics"><span>' + (selected.inbound_edges || []).length + ' inbound</span><span>' + (selected.outbound_edges || []).length + ' outbound</span><span>' + (selected.impact_paths || []).length + ' impact paths</span></div><ol class="reason-list">' + (paths || "<li>No bounded downstream impact path was returned.</li>") + "</ol></article>";
    }
    var filters = '<div class="graph-filter-grid"><label>Search<input type="search" data-graph-filter="search" value="' + ui.escapeHtml(graphFilters.search) + '" placeholder="Kind, name, namespace"></label><label>Kind<select data-graph-filter="kind">' + kindOptions.map(function (value) { return option(value, graphFilters.kind); }).join("") + '</select></label><label>Risk<select data-graph-filter="risk">' + ["", "low", "medium", "high", "critical", "unknown"].map(function (value) { return option(value, graphFilters.risk); }).join("") + '</select></label><label>Node status<select data-graph-filter="status">' + ["", "present", "missing", "uncertain"].map(function (value) { return option(value, graphFilters.status); }).join("") + '</select></label><label>Relationship<select data-graph-filter="relationship">' + relationshipOptions.map(function (value) { return option(value, graphFilters.relationship); }).join("") + '</select></label><label>Edge status<select data-graph-filter="edge_status">' + ["", "valid", "missing", "uncertain"].map(function (value) { return option(value, graphFilters.edge_status); }).join("") + '</select></label><label>Confidence<select data-graph-filter="confidence">' + ["", "deterministic", "high", "medium", "uncertain"].map(function (value) { return option(value, graphFilters.confidence); }).join("") + '</select></label><label class="graph-check"><input type="checkbox" data-graph-filter="missing_only" value="true"' + (graphFilters.missing_only ? " checked" : "") + "> Missing or uncertain only</label></div>";
    return '<div class="graph-summary-grid">' + summaryCards + "</div>" + explanation + selectedPanel
      + '<div class="tab-toolbar graph-toolbar">' + filters + '<div class="inline-actions"><button class="btn" type="button" data-graph-mode="canvas">Graph</button><button class="btn" type="button" data-graph-mode="table">Table</button><button class="btn" type="button" data-copy-tab>Copy facts</button></div></div>'
      + '<div data-graph-canvas class="mock-graph-canvas" style="min-height:' + canvasHeight + 'px"' + (graphDisplayMode === "canvas" ? "" : " hidden") + '><svg class="dependency-edge-layer" viewBox="0 0 1000 ' + canvasHeight + '" preserveAspectRatio="none" aria-label="Server supplied dependency edges">' + svgEdges + "</svg>" + nodeButtons + '<div class="graph-density-note">Rendering ' + nodes.length + " server-supplied nodes and " + edges.length + " server-supplied edges</div></div>"
      + '<div data-graph-table class="table-scroll"' + (graphDisplayMode === "table" ? "" : " hidden") + '><table class="dependency-table"><thead><tr><th>Source</th><th>Relationship</th><th>Target</th><th>Status</th><th>Confidence</th><th>Evidence</th></tr></thead><tbody>' + tableRows + "</tbody></table></div>"
      + '<div class="table-footer graph-page-footer"><span>Nodes ' + nodes.length + " of " + Number(nodePage.result_count || 0) + " / Edges " + rows.length + " of " + Number(edgePage.result_count || 0) + '</span><div class="pagination"><button class="btn" type="button" data-graph-page="nodes-previous"' + (graphNodeCursorHistory.length ? "" : " disabled") + '>Previous nodes</button><button class="btn" type="button" data-graph-page="nodes-next"' + (nodePage.has_more ? "" : " disabled") + '>Next nodes</button><button class="btn" type="button" data-graph-page="edges-previous"' + (graphEdgeCursorHistory.length ? "" : " disabled") + '>Previous edges</button><button class="btn" type="button" data-graph-page="edges-next"' + (edgePage.has_more ? "" : " disabled") + ">Next edges</button></div></div>";
  }
  function renderFindings(tab) {
    var data = tab.data || {};
    var findings = Array.isArray(tab.findings) ? tab.findings : (data.findings || []);
    var passedGroups = Array.isArray(tab.passed_groups) ? tab.passed_groups : (data.passed_groups || []);
    var evidenceAxis = data.evidence_axis || {};
    var riskAxis = data.risk_axis || {};
    var projection = data.decision_projection || {};
    var explanation = tab.safe_explanation
      ? '<article class="content-block span-12 policy-explanation"><p class="eyebrow">SIGMA 5 PRO / Bounded Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">The explanation cannot change any axis, contribution, precedence rule, or decision.</p></article>'
      : "";
    var axes = [
      { label: "Policy", value: data.verdict || "not_available", tone: data.verdict === "deny" ? "red" : data.verdict === "allow_with_approval" ? "amber" : "green", note: data.policy_version || "Unversioned" },
      { label: "Evidence", value: (evidenceAxis.completeness || "unknown") + " / " + (evidenceAxis.freshness || "unknown"), tone: evidenceAxis.classification === "complete" ? "green" : "amber", note: String(evidenceAxis.present_count || 0) + " of " + String(evidenceAxis.required_count || 0) + " checks present" },
      { label: "Change Risk", value: (riskAxis.level || "unknown") + " / " + String(riskAxis.score == null ? "n/a" : riskAxis.score), tone: riskAxis.level === "critical" || riskAxis.level === "high" ? "red" : riskAxis.level === "medium" ? "amber" : "green", note: riskAxis.rules_version || "Unversioned" },
      { label: "Decision Projection", value: projection.label || "Unknown", tone: projection.level || "amber", note: (projection.preliminary ? "Preliminary / " : "Final / ") + (projection.precedence_rule || "no precedence") }
    ].map(function (axis) {
      return '<article class="policy-axis ' + ui.escapeHtml(axis.tone) + '"><span class="fact-label">' + ui.escapeHtml(axis.label) + '</span><strong>' + ui.escapeHtml(ui.label(axis.value)) + '</strong><small>' + ui.escapeHtml(axis.note) + '</small></article>';
    }).join("");
    var findingRows = findings.map(function (finding) {
      var findingId = finding.finding_id || finding.id;
      var severity = finding.severity === "critical" || finding.severity === "high" || finding.effect === "deny" ? "red" : "amber";
      return '<li id="' + ui.escapeHtml(findingId) + '" data-finding><div>' + ui.badge(severity, finding.effect || finding.severity) + '<strong>' + ui.escapeHtml((finding.code || "POLICY_FINDING") + " / " + (finding.title || "Policy finding")) + '</strong></div><p>' + ui.escapeHtml(finding.summary || finding.detail || finding.message || "No safe summary supplied.") + '</p><button class="btn" type="button" data-evidence-modal="' + ui.escapeHtml(findingId) + '">Evidence</button></li>';
    }).join("");
    if (!findingRows) findingRows = '<li class="empty-inline">No findings match this filter. Deterministic axes remain unchanged.</li>';
    var filters = ["all", "review", "block"].map(function (filter) {
      return '<button class="btn' + (policyFilter === filter ? " active" : "") + '" type="button" data-policy-filter="' + filter + '" aria-pressed="' + String(policyFilter === filter) + '">' + ui.escapeHtml(ui.label(filter === "block" ? "blocking" : filter)) + '</button>';
    }).join("");
    var contributions = (data.rule_contributions || []).map(function (item) {
      return '<tr data-rule-contribution class="' + (item.matched ? "matched" : "") + '"><td>' + ui.escapeHtml(ui.label(item.axis)) + '</td><td><strong>' + ui.escapeHtml(ui.label(item.rule)) + '</strong></td><td>' + ui.badge(item.matched ? (item.effect === "deny" || item.effect === "red" ? "red" : "amber") : "green", item.matched ? "matched" : "clear") + '</td><td>' + ui.escapeHtml(ui.label(item.effect || "none")) + '</td><td>' + ui.escapeHtml(item.contribution == null ? 0 : item.contribution) + '</td><td>' + ui.escapeHtml(item.reason || "Deterministic rule evaluated.") + '</td></tr>';
    }).join("");
    var passed = passedGroups.length
      ? passedGroups.map(function (group) { return '<li><span class="policy-pass-mark">OK</span>' + ui.escapeHtml(ui.label(group)) + '</li>'; }).join("")
      : '<li>No passed groups were returned for this filter.</li>';
    return '<div class="notice policy-authority">Server-authoritative deterministic axes. The browser renders supplied facts and never infers policy, evidence, risk, or a decision.</div>'
      + '<div class="policy-axis-grid">' + axes + '</div>'
      + explanation
      + '<div class="content-grid"><article class="content-block span-8"><div class="tab-toolbar"><h3>Policy Findings</h3><div class="inline-actions">' + filters + '</div></div><ul class="finding-list policy-findings">' + findingRows + '</ul></article><article class="content-block span-4"><h3>Passed Groups</h3><ul class="list-clean policy-passed-groups">' + passed + '</ul><div class="notice green">Passing a group does not bypass approval, evidence, risk, or dry-run requirements.</div></article><article class="content-block span-12"><div class="tab-toolbar"><h3>Full Rule Contribution Breakdown</h3><button class="btn" type="button" data-copy-tab>Copy facts</button></div><div class="table-scroll"><table class="policy-rule-table"><thead><tr><th>Axis</th><th>Rule</th><th>Match</th><th>Effect</th><th>Score</th><th>Deterministic reason</th></tr></thead><tbody>' + contributions + '</tbody></table></div></article></div>';
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
    panel.dataset.loading = "true";
    panel.innerHTML = ui.stateView("loading", "Loading " + ui.label(slug), "Reading this evidence module through the configured data adapter.", false);
    var tabQuery = {};
    if (slug === "release-delta") tabQuery = Object.assign({}, deltaFilters);
    if (slug === "dependency-graph") tabQuery = Object.assign({}, graphFilters);
    if (slug === "policy" && policyFilter !== "all") tabQuery = { effect: policyFilter === "block" ? "deny" : "approval_required" };
    adapter.getTab(twin.twin_id, slug, twin.decision_version, tabQuery).then(function (tabData) {
      if (slug === "audit" && ui.params().get("event")) {
        var eventIndex = tabData.events.findIndex(function (item) { return item.event_id === ui.params().get("event"); });
        if (eventIndex >= 0) auditPage = Math.floor(eventIndex / 20);
      }
      panel.innerHTML = '<div class="tab-intro"><div><p class="eyebrow">' + (tabData.non_authoritative ? "Mock / Non-authoritative Module" : "Real Evidence") + ' · ' + ui.escapeHtml(ui.label(tabData.state)) + '</p><h2>' + ui.escapeHtml(tabData.title) + '</h2><p>' + ui.escapeHtml(tabData.summary) + '</p></div>' + ui.badge(tabData.state) + '</div><div data-tab-content>' + renderTab(tabData) + '</div>';
      panel._tabData = tabData;
      delete panel.dataset.loading;
      applyDeepLink(panel);
    }).catch(function (error) {
      delete panel.dataset.loading;
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
    var previousDecisionVersion = twin && twin.decision_version;
    twin = item;
    summary.hidden = false;
    preview.hidden = false;
    selectedTwinId = item.twin_id;
    document.title = item.display_name + " | Digital Twins";
    renderHeader(item);
    renderSummary(item);
    var activePanel = document.getElementById("panel-" + selectedTab);
    var decisionVersionChanged = previousDecisionVersion != null
      && previousDecisionVersion !== item.decision_version;
    if (decisionVersionChanged || (!activePanel._tabData && activePanel.dataset.loading !== "true")) {
      activateTab(selectedTab, true);
    }
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
    var policyButton = event.target.closest("[data-policy-filter]");
    if (policyButton && twin) {
      policyFilter = policyButton.getAttribute("data-policy-filter") || "all";
      activateTab("policy", true);
      return;
    }
    var evidence = event.target.closest("[data-evidence-modal]");
    if (evidence) {
      var policyPanel = document.getElementById("panel-policy");
      var policyData = (policyPanel._tabData || {}).data || {};
      var findingId = evidence.getAttribute("data-evidence-modal");
      var selectedFinding = (policyData.findings || []).find(function (item) { return (item.finding_id || item.id) === findingId; });
      ui.showModal({
        eyebrow: "Policy Evidence / Server supplied",
        title: findingId,
        body: '<pre class="log-view">' + ui.escapeHtml(JSON.stringify(selectedFinding || { finding_id: findingId, evidence_refs: [] }, null, 2)) + '</pre>'
      });
    }    var graphNode = event.target.closest("[data-graph-node]");
    if (graphNode && twin) {
      graphFilters.resource = graphNode.getAttribute("data-graph-node");
      ui.updateUrl({ resource: graphFilters.resource }, true);
      activateTab("dependency-graph", true);
    }
    var clearGraphSelection = event.target.closest("[data-graph-clear-selection]");
    if (clearGraphSelection && twin) {
      graphFilters.resource = "";
      ui.updateUrl({ resource: null }, true);
      activateTab("dependency-graph", true);
    }
    var auditModal = event.target.closest("[data-audit-modal]");
    if (auditModal) ui.showModal({ eyebrow: "Audit Event", title: auditModal.getAttribute("data-audit-modal"), body: '<div class="log-view">Safe event summary only.\nActor and correlation metadata are preserved.\nHidden model reasoning is not stored.</div>' });
    var graphMode = event.target.closest("[data-graph-mode]");
    if (graphMode) {
      var panel = document.getElementById("panel-dependency-graph");
      graphDisplayMode = graphMode.getAttribute("data-graph-mode");
      panel.querySelector("[data-graph-canvas]").hidden = graphDisplayMode !== "canvas";
      panel.querySelector("[data-graph-table]").hidden = graphDisplayMode !== "table";
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
    var graphPager = event.target.closest("[data-graph-page]");
    if (graphPager && twin) {
      var graphPanel = document.getElementById("panel-dependency-graph");
      var graphData = graphPanel._tabData.data || {};
      var graphDirection = graphPager.getAttribute("data-graph-page");
      if (graphDirection === "nodes-next" && graphData.node_page.next_cursor) {
        graphNodeCursorHistory.push(graphFilters.node_cursor);
        graphFilters.node_cursor = graphData.node_page.next_cursor;
      } else if (graphDirection === "nodes-previous" && graphNodeCursorHistory.length) {
        graphFilters.node_cursor = graphNodeCursorHistory.pop() || null;
      } else if (graphDirection === "edges-next" && graphData.edge_page.next_cursor) {
        graphEdgeCursorHistory.push(graphFilters.edge_cursor);
        graphFilters.edge_cursor = graphData.edge_page.next_cursor;
      } else if (graphDirection === "edges-previous" && graphEdgeCursorHistory.length) {
        graphFilters.edge_cursor = graphEdgeCursorHistory.pop() || null;
      }
      activateTab("dependency-graph", true);
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
    var deltaFilter = event.target.closest("[data-delta-filter]");
    if (deltaFilter && twin) {
      deltaFilters[deltaFilter.getAttribute("data-delta-filter")] = deltaFilter.value;
      deltaFilters.cursor = null;
      deltaCursorHistory = [];
      activateTab("release-delta", true);
      return;
    }
    var graphFilter = event.target.closest("[data-graph-filter]");
    if (graphFilter && twin) {
      var filterName = graphFilter.getAttribute("data-graph-filter");
      graphFilters[filterName] = graphFilter.type === "checkbox"
        ? (graphFilter.checked ? "true" : "")
        : graphFilter.value;
      graphFilters.resource = "";
      graphFilters.node_cursor = null;
      graphFilters.edge_cursor = null;
      graphNodeCursorHistory = [];
      graphEdgeCursorHistory = [];
      ui.updateUrl({ resource: null }, true);
      activateTab("dependency-graph", true);
    }
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
    graphFilters.resource = ui.params().get("resource") || "";
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
