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
  var auditCursor = null;
  var auditCursorHistory = [];
  var policyFilter = "all";
  var dryRunFilters = { phase: "", step: "", resource: "", tool: "", outcome: "" };

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
    header.innerHTML = '<div><p class="eyebrow">Digital Twin - ' + (item.decision_is_final ? "Final Decision" : "Preliminary State") + '</p><div class="title-line"><h1 id="twin-title">' + ui.escapeHtml(item.display_name) + "</h1>" + ui.badge(status, decisionLabel(item)) + ui.badge(item.lifecycle_status) + '</div><p class="muted">Risk ' + ui.escapeHtml(item.risk.score == null ? "not calculated" : item.risk.score) + " - " + ui.escapeHtml(ui.label(item.autonomy_eligibility)) + " - " + ui.escapeHtml(item.recommended_action) + '</p></div><div class="action-row" aria-label="Twin actions">' + actionsFor(item).map(actionButton).join("") + "</div>";

    if (item.prior_decision && !item.decision_is_final) {
      preview.innerHTML = '<article class="status-preview"><span class="badge generating">Generating v' + item.decision_version + '</span><div><strong>New evidence is being evaluated</strong><p>Preliminary values cannot authorize execution.</p></div></article><article class="status-preview"><span class="badge ' + ui.badgeClass(item.prior_decision.decision) + '">Prior ' + ui.escapeHtml(ui.label(item.prior_decision.decision)) + '</span><div><strong>Previous evidence remains visible</strong><p>Decision v' + item.prior_decision.decision_version + " - risk " + ui.escapeHtml(item.prior_decision.risk.score) + " - now superseded.</p></div></article>";
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
    var data = tab.data || {};
    var observations = data.observations || [];
    var validations = data.validations || [];
    var diffRows = (data.structured_diff || {}).rows || [];
    var artifacts = data.artifacts || [];
    var fingerprints = data.command_fingerprints || [];
    var snapshot = data.snapshot || {};
    var filters = [
      ["phase", "Phase", "e.g. dry_run"],
      ["step", "Step", "e.g. helm_template"],
      ["resource", "Resource", "kind or name"],
      ["tool", "Tool", "helm or kubectl"]
    ].map(function (entry) {
      return '<label>' + entry[1] + '<input type="search" data-dry-run-filter="' + entry[0] + '" value="' + ui.escapeHtml(dryRunFilters[entry[0]]) + '" placeholder="' + entry[2] + '"></label>';
    }).join("") + '<label>Outcome<select data-dry-run-filter="outcome"><option value="">All</option>' + ["accepted", "rejected", "warning", "skipped", "unknown"].map(function (value) {
      return '<option value="' + value + '"' + (dryRunFilters.outcome === value ? " selected" : "") + '>' + ui.escapeHtml(ui.label(value)) + '</option>';
    }).join("") + '</select></label>';
    var validationRows = validations.map(function (item) {
      var tone = item.status === "passed" ? "green" : item.status === "failed" ? "red" : "amber";
      return '<tr><td><strong>' + ui.escapeHtml(ui.label(item.type)) + '</strong></td><td>' + ui.badge(tone, item.status) + '</td><td>' + ui.escapeHtml(item.summary) + '</td></tr>';
    }).join("") || '<tr><td colspan="3" class="empty-inline">No Kubernetes or Helm validation facts were returned.</td></tr>';
    var observationRows = observations.map(function (item) {
      var tone = item.outcome === "accepted" ? "green" : item.outcome === "rejected" ? "red" : item.outcome === "warning" ? "amber" : "info";
      return '<tr class="dry-run-outcome-' + ui.escapeHtml(item.outcome) + '"><td>' + ui.badge(tone, item.outcome) + '</td><td><strong>' + ui.escapeHtml(item.phase) + '</strong><small>' + ui.escapeHtml(item.step) + '</small></td><td>' + ui.escapeHtml(item.tool) + '</td><td><strong>' + ui.escapeHtml(item.resource_identity || "General") + '</strong><small>' + ui.escapeHtml(item.summary) + '</small></td><td>' + ui.escapeHtml(String((item.evidence_refs || []).length)) + '</td></tr>';
    }).join("") || '<tr><td colspan="5" class="empty-inline">No observations match the selected filters.</td></tr>';
    var diffTableRows = diffRows.map(function (row) {
      var risk = row.risk || "unknown";
      var tone = risk === "high" || risk === "critical" ? "red" : risk === "medium" ? "amber" : "green";
      return '<tr><td><strong>' + ui.escapeHtml(row.resource_identity || row.resource || "Unknown resource") + '</strong></td><td>' + ui.badge("info", row.action || "unknown") + '</td><td>' + ui.badge(tone, risk) + '</td><td>' + ui.escapeHtml(row.current_summary || row.current || "Not observed") + '</td><td>' + ui.escapeHtml(row.planned_summary || row.planned || "Not supplied") + '</td><td>' + ui.escapeHtml(row.reason || "Authoritative dry-run delta") + '</td></tr>';
    }).join("") || '<tr><td colspan="6" class="empty-inline">No structured resource deltas were returned.</td></tr>';
    var explanation = tab.safe_explanation
      ? '<article class="content-block span-12 dry-run-explanation"><p class="eyebrow">SIGMA 5 PRO / Bounded Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">This explanation is audit logged and cannot submit instructions, retry a mutation, or alter authoritative outcomes.</p></article>'
      : "";
    var artifactLinks = artifacts.map(function (artifact) {
      return '<li><a class="table-link" href="' + ui.escapeHtml(artifact.download_href) + '">' + ui.escapeHtml(artifact.filename) + '</a><small>' + ui.escapeHtml(artifact.media_type) + '</small></li>';
    }).join("") || '<li>No report artifacts were returned.</li>';
    var fidelity = (data.fidelity_limitations || []).map(function (item) {
      return '<li>' + ui.escapeHtml(item) + '</li>';
    }).join("") || '<li>No fidelity statement was returned.</li>';
    return '<div class="notice dry-run-authority">Execution-agent evidence only. Job status, validation outcomes, observations, hashes, and fingerprints are rendered without browser inference.</div>'
      + metricCards(tab.metrics || [])
      + '<div class="dry-run-identity-grid"><article><span>Dry-run job</span><strong>' + ui.escapeHtml(data.dry_run_job_id || "Not attached") + '</strong></article><article><span>Qualification</span><strong>' + ui.escapeHtml(ui.label(data.qualification_status || data.status || "unknown")) + '</strong></article><article><span>Target</span><strong>' + ui.escapeHtml(data.target_namespace || "Unknown") + '</strong></article><article><span>Snapshot</span><strong>' + ui.escapeHtml(snapshot.snapshot_id || "Unknown") + '</strong><small>' + ui.escapeHtml(snapshot.captured_at ? ui.formatDate(snapshot.captured_at) : "No timestamp") + '</small></article></div>'
      + '<article class="content-block dry-run-filter-block"><div class="tab-toolbar"><h3>Evidence Filters</h3><button class="btn" type="button" data-dry-run-clear>Clear filters</button></div><div class="dry-run-filter-grid">' + filters + '</div></article>'
      + explanation
      + '<div class="content-grid"><article class="content-block span-12"><h3>Kubernetes and Helm Validation</h3><div class="table-scroll"><table><thead><tr><th>Validation</th><th>Status</th><th>Authoritative summary</th></tr></thead><tbody>' + validationRows + '</tbody></table></div></article>'
      + '<article class="content-block span-12"><div class="tab-toolbar"><h3>Redacted Agent Observations</h3><button class="btn" type="button" data-copy-tab>Copy safe facts</button></div><div class="table-scroll"><table class="dry-run-observation-table"><thead><tr><th>Outcome</th><th>Phase / Step</th><th>Tool</th><th>Resource / Safe log</th><th>Evidence</th></tr></thead><tbody>' + observationRows + '</tbody></table></div></article>'
      + '<article class="content-block span-12"><h3>Structured Dry-run Diff</h3><div class="table-scroll"><table class="dry-run-diff-table"><thead><tr><th>Resource</th><th>Action</th><th>Risk</th><th>Current</th><th>Planned</th><th>Reason</th></tr></thead><tbody>' + diffTableRows + '</tbody></table></div></article>'
      + '<article class="content-block span-7"><div class="tab-toolbar"><h3>Command Fingerprints</h3><button class="btn" type="button" data-copy-fingerprints>Copy fingerprints</button></div><p class="muted">Canonical hash: <code>' + ui.escapeHtml(data.command_fingerprint_hash || "Not available") + '</code></p><ol class="fingerprint-list">' + (fingerprints.map(function (item) { return '<li><code>' + ui.escapeHtml(item) + '</code></li>'; }).join("") || '<li>No command fingerprints were returned.</li>') + '</ol></article>'
      + '<article class="content-block span-5"><h3>Reports</h3><ul class="artifact-list">' + artifactLinks + '</ul><h3>Fidelity Limits</h3><ul class="list-clean dry-run-fidelity">' + fidelity + '</ul></article></div>';
  }
  function renderRollback(tab) {
    var data = tab.data || {};
    var coverage = data.coverage || {};
    var helm = data.helm || {};
    var previous = data.previous_artifacts || {};
    var proof = data.proof || {};
    var steps = data.machine_plan_steps || [];
    var gaps = data.gaps || [];
    var findings = data.non_reversible_changes || [];
    var explanation = tab.safe_explanation
      ? '<article class="content-block span-12 rollback-explanation"><p class="eyebrow">SIGMA 5 PRO / Bounded Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">SIGMA explains deterministic rollback facts only. It cannot change confidence, prove rollback, or execute recovery.</p></article>'
      : "";
    var stepRows = steps.map(function (step) {
      return '<tr><td><strong>' + ui.escapeHtml(step.summary) + '</strong><small>' + ui.escapeHtml(step.step_id) + '</small></td><td>' + ui.escapeHtml((step.forward_step_ids || []).join(", ") || "Unlinked") + '</td><td>' + ui.badge(step.reversible ? "green" : "red", step.reversible ? "reversible" : "not reversible") + '</td><td>' + ui.escapeHtml(ui.label(step.mechanism)) + '</td><td>' + ui.badge(step.dry_run_capable ? "green" : "amber", step.dry_run_capable ? "available" : "not available") + '</td></tr>';
    }).join("") || '<tr><td colspan="5" class="empty-inline">No rollback steps are defined in the machine plan.</td></tr>';
    var gapRows = gaps.map(function (gap) {
      var tone = gap.severity === "critical" || gap.severity === "high" ? "red" : "amber";
      return '<tr><td><strong>' + ui.escapeHtml(gap.code) + '</strong></td><td>' + ui.badge(tone, gap.severity) + '</td><td>' + ui.escapeHtml(gap.summary) + '</td></tr>';
    }).join("") || '<tr><td colspan="3" class="empty-inline">No deterministic rollback gaps were found.</td></tr>';
    var findingRows = findings.map(function (finding) {
      return '<li><strong>' + ui.escapeHtml(finding.title) + '</strong><small>' + ui.escapeHtml(finding.summary) + '</small></li>';
    }).join("") || '<li>No non-reversible changes were identified.</li>';
    var previousRows = (previous.manifest_paths || []).concat(previous.values_paths || []).map(function (path) {
      return '<li><code>' + ui.escapeHtml(path) + '</code></li>';
    }).join("") || '<li>No previous manifests or values were supplied.</li>';
    var manual = (data.manual_steps || []).map(function (item) { return '<li>' + ui.escapeHtml(item) + '</li>'; }).join("") || '<li>No manual rollback steps were supplied.</li>';
    var validation = (data.validation_checks || []).map(function (item) { return '<li>' + ui.escapeHtml(item) + '</li>'; }).join("") || '<li>No rollback validation checks were supplied.</li>';
    return '<div class="notice rollback-authority">Deterministic rollback readiness only. Defined rollback and proven rollback are separate facts; this tab does not execute recovery.</div>'
      + metricCards(tab.metrics || [])
      + '<div class="rollback-status-grid"><article><span>Rule version</span><strong>' + ui.escapeHtml(data.rule_version || "Not available") + '</strong></article><article><span>Plan coverage</span><strong>' + ui.escapeHtml(String(coverage.coverage_percent || 0)) + '%</strong><small>' + ui.escapeHtml(String(coverage.linked_operations || 0)) + ' of ' + ui.escapeHtml(String(coverage.mutating_operations || 0)) + ' operations linked</small></article><article><span>Runtime proof</span><strong>' + ui.escapeHtml(ui.label(proof.status || "not_run")) + '</strong><small>' + ui.escapeHtml(proof.summary || "No proof summary") + '</small></article><article><span>PVC / data</span><strong>' + ui.escapeHtml(ui.label(data.pvc_data_reversibility || "unknown")) + '</strong></article></div>'
      + explanation
      + '<div class="content-grid"><article class="content-block span-12"><h3>Forward-to-Rollback Linkage</h3><div class="table-scroll"><table><thead><tr><th>Rollback step</th><th>Forward operations</th><th>Reversibility</th><th>Mechanism</th><th>Dry-run</th></tr></thead><tbody>' + stepRows + '</tbody></table></div></article>'
      + '<article class="content-block span-6"><h3>Helm Revision and Provenance</h3><dl class="fact-list"><div><dt>Required</dt><dd>' + ui.escapeHtml(helm.required ? "Yes" : "No") + '</dd></div><div><dt>Release</dt><dd>' + ui.escapeHtml(helm.release_name || "Not supplied") + '</dd></div><div><dt>Current revision</dt><dd>' + ui.escapeHtml(String(helm.current_revision || "Not supplied")) + '</dd></div><div><dt>Previous revision</dt><dd>' + ui.escapeHtml(String(helm.previous_revision || "Not supplied")) + '</dd></div><div><dt>Provenance</dt><dd>' + ui.escapeHtml(ui.label(helm.provenance || "not_available")) + '</dd></div></dl></article>'
      + '<article class="content-block span-6"><h3>Previous Manifests and Values</h3><p>' + ui.badge(previous.manifests_available ? "green" : "amber", previous.manifests_available ? "manifests available" : "manifests missing") + ' ' + ui.badge(previous.values_available ? "green" : "amber", previous.values_available ? "values available" : "values missing") + '</p><ul class="artifact-list">' + previousRows + '</ul></article>'
      + '<article class="content-block span-12"><h3>Evidence Gaps</h3><div class="table-scroll"><table><thead><tr><th>Rule</th><th>Severity</th><th>Gap</th></tr></thead><tbody>' + gapRows + '</tbody></table></div></article>'
      + '<article class="content-block span-4"><h3>Non-reversible Changes</h3><ul class="artifact-list">' + findingRows + '</ul></article><article class="content-block span-4"><h3>Manual Review</h3><ol class="reason-list">' + manual + '</ol></article><article class="content-block span-4"><h3>Rollback Validation</h3><ol class="reason-list">' + validation + '</ol></article></div>';
  }

  function renderDrift(tab) {
    var data = tab.data || {};
    var changes = Array.isArray(data.changes) ? data.changes : [];
    var baseline = data.baseline || data.snapshot || {};
    var current = data.current_capture || {};
    var freshness = data.freshness || {};
    var counts = data.change_counts || {};
    var refreshContract = twin && actionsFor(twin).find(function (item) { return item.code === "refresh_drift"; });
    var canRefresh = Boolean(refreshContract && refreshContract.enabled);
    function tone(value) {
      if (value === "critical" || value === "major") return "red";
      if (value === "minor" || value === "unknown") return "amber";
      return "green";
    }
    var rows = changes.map(function (change) {
      var axes = Object.keys(change.axes || {}).filter(function (key) { return change.axes[key]; }).map(ui.label).join(", ") || "spec";
      return "<tr><td><strong>" + ui.escapeHtml(change.name || change.resource_identity) + "</strong><small>" + ui.escapeHtml(change.resource_identity) + "</small></td><td>" + ui.escapeHtml(change.kind || "Unknown") + "</td><td>" + ui.badge(tone(change.classification), ui.label(change.classification)) + "</td><td>" + ui.escapeHtml(ui.label(change.change_type)) + "</td><td>" + ui.escapeHtml(axes) + "</td><td>" + ui.escapeHtml(change.summary) + "</td></tr>";
    }).join("");
    if (!rows) rows = '<tr><td colspan="6"><div class="empty-inline">No changed resources were detected.</div></td></tr>';
    var explanation = tab.safe_explanation
      ? '<article class="content-block span-12"><p class="eyebrow">SIGMA 5 PRO / Bounded Drift Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">The model explains structured changed-resource facts only. Deterministic rules retain classification and execution authority.</p></article>'
      : "";
    var materialNotice = data.execution_disabled
      ? '<div class="notice red">Execution eligibility is disabled. Regenerate the twin after reviewing material, unknown, or stale drift evidence.</div>'
      : '<div class="notice green">No material drift blocks the current decision inside its freshness window.</div>';
    return '<div class="drift-status-grid">'
      + '<article class="drift-status-card"><span>Overall drift</span><strong>' + ui.escapeHtml(ui.label(data.status || "unknown")) + '</strong>' + ui.badge(tone(data.status), data.status || "unknown") + '</article>'
      + '<article class="drift-status-card"><span>Baseline</span><strong>' + ui.escapeHtml(String(baseline.resource_count == null ? "n/a" : baseline.resource_count)) + ' resources</strong><small>' + ui.escapeHtml(String(baseline.hash || "").slice(0, 16) || "No hash") + '</small></article>'
      + '<article class="drift-status-card"><span>Current</span><strong>' + ui.escapeHtml(String(current.resource_count == null ? "n/a" : current.resource_count)) + ' resources</strong><small>' + ui.escapeHtml(String(current.hash || "").slice(0, 16) || "No hash") + '</small></article>'
      + '<article class="drift-status-card"><span>Freshness</span><strong>' + ui.escapeHtml(ui.label(freshness.status || "unknown")) + '</strong><small>' + ui.escapeHtml(String(freshness.age_seconds || 0)) + ' seconds</small></article></div>'
      + materialNotice + explanation
      + '<div class="content-grid tab-followup"><article class="content-block span-4"><h3>Decision Effect</h3><dl class="fact-list"><div><dt>Material</dt><dd>' + (data.material ? "Yes" : "No") + '</dd></div><div><dt>Superseded</dt><dd>' + (data.decision_invalidated ? "Yes" : "No") + '</dd></div><div><dt>Rules</dt><dd>' + ui.escapeHtml(data.rules_version || "unavailable") + '</dd></div></dl></article>'
      + '<article class="content-block span-4"><h3>Drift Axes</h3><p>' + ui.badge(data.helm_revision_drift ? "red" : "green", data.helm_revision_drift ? "Helm revision drift" : "No Helm revision drift") + '</p><p class="muted">' + ui.escapeHtml((data.manual_patch_indicators || []).length) + ' manual-patch indicator(s)</p></article>'
      + '<article class="content-block span-4"><h3>Change Counts</h3><p>Minor ' + Number(counts.minor || 0) + ' / Major ' + Number(counts.major || 0) + ' / Critical ' + Number(counts.critical || 0) + '</p><p class="muted">' + Number(counts.total || 0) + ' total changed resources</p></article></div>'
      + '<div class="tab-toolbar"><span>Read-only comparison of persisted baseline and current namespace state.</span><button class="btn" type="button" data-refresh-drift' + (canRefresh ? '' : ' disabled') + '>Refresh Drift</button></div>'
      + '<div class="table-scroll"><table class="drift-change-table"><thead><tr><th>Resource</th><th>Kind</th><th>Class</th><th>Change</th><th>Axes</th><th>Summary</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
  }
  function renderRuntime(tab) {
    var data = tab.data || {};
    var health = data.current_health || {};
    var factors = Array.isArray(data.factors) ? data.factors : [];
    var pods = Array.isArray(data.pod_details) ? data.pod_details : [];
    var refreshContract = twin && actionsFor(twin).find(function (item) { return item.code === "refresh_runtime_behavior"; });
    var canRefresh = Boolean(refreshContract && refreshContract.enabled);
    function tone(value) {
      if (["critical", "high", "unhealthy"].indexOf(value) >= 0) return "red";
      if (["medium", "unknown", "degraded"].indexOf(value) >= 0) return "amber";
      return "green";
    }
    var factorRows = factors.map(function (factor) {
      return "<tr><td><strong>" + ui.escapeHtml(factor.title || factor.factor_id) + "</strong><small>" + ui.escapeHtml(factor.factor_id || "rule") + "</small></td><td>" + ui.badge(factor.impact === "reduces_risk" ? "green" : factor.impact === "unknown" ? "amber" : "red", ui.label(factor.impact || "unknown")) + "</td><td>" + ui.escapeHtml(String(factor.confidence == null ? "n/a" : factor.confidence)) + "</td><td>" + ui.escapeHtml(factor.summary || "") + "</td><td>" + ui.escapeHtml(String((factor.evidence_refs || []).length)) + "</td></tr>";
    }).join("");
    if (!factorRows) factorRows = '<tr><td colspan="5"><div class="empty-inline">No deterministic runtime factors were produced.</div></td></tr>';
    var podRows = pods.map(function (pod) {
      return "<tr><td><strong>" + ui.escapeHtml(pod.name || "unknown") + "</strong></td><td>" + ui.escapeHtml(pod.phase || "Unknown") + "</td><td>" + ui.badge(pod.ready ? "green" : "red", pod.ready ? "Ready" : "Not ready") + "</td><td>" + ui.escapeHtml(String(pod.restarts || 0)) + "</td></tr>";
    }).join("");
    if (!podRows) podRows = '<tr><td colspan="4"><div class="empty-inline">No pod rows were returned by the current snapshot.</div></td></tr>';
    var explanation = tab.safe_explanation
      ? '<article class="content-block span-12"><p class="eyebrow">SIGMA 5 PRO / Bounded Runtime Explanation</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">SIGMA explains supplied deterministic facts only. It cannot reclassify runtime risk or approve execution.</p></article>'
      : "";
    var historyMessage = data.historical_context_message || "Not Available: validated historical runtime-comparison APIs are not configured.";
    return '<div class="notice runtime-authority">Rules-first and read-only. Runtime behavior can restrict execution eligibility but can never independently approve it.</div>'
      + metricCards(tab.metrics || [])
      + '<div class="drift-status-grid"><article class="drift-status-card"><span>Runtime risk</span><strong>' + ui.escapeHtml(ui.label(data.risk || "unknown")) + '</strong>' + ui.badge(tone(data.risk), data.risk || "unknown") + '</article><article class="drift-status-card"><span>Current health</span><strong>' + ui.escapeHtml(ui.label(health.status || "unknown")) + '</strong>' + ui.badge(tone(health.status), health.status || "unknown") + '</article><article class="drift-status-card"><span>Resource pressure</span><strong>' + ui.escapeHtml(ui.label(health.resource_pressure || "unknown")) + '</strong></article><article class="drift-status-card"><span>Confidence</span><strong>' + ui.escapeHtml(String(data.confidence == null ? "n/a" : data.confidence)) + '</strong><small>' + ui.escapeHtml(data.rules_version || "rules unavailable") + '</small></article></div>'
      + explanation
      + '<div class="content-grid tab-followup"><article class="content-block span-8"><h3>Current Signal Summary</h3><p>' + ui.escapeHtml(data.summary || "No summary returned.") + '</p><dl class="fact-list"><div><dt>Not-ready pods</dt><dd>' + Number(health.not_ready_pods || 0) + '</dd></div><div><dt>Restarting pods</dt><dd>' + Number(health.restarting_pods || 0) + '</dd></div><div><dt>Event anomalies</dt><dd>' + Number(health.event_anomalies || 0) + '</dd></div><div><dt>Execution effect</dt><dd>' + ui.escapeHtml(ui.label(data.execution_effect || "require_review")) + '</dd></div></dl></article><article class="content-block span-4"><h3>Historical Comparison</h3><p>' + ui.badge("amber", ui.label(data.historical_context_status || "not_available")) + '</p><p class="muted">' + ui.escapeHtml(historyMessage) + '</p></article></div>'
      + '<div class="content-grid"><article class="content-block span-12"><h3>Deterministic Runtime Factors</h3><div class="table-scroll"><table><thead><tr><th>Factor</th><th>Impact</th><th>Confidence</th><th>Summary</th><th>Evidence</th></tr></thead><tbody>' + factorRows + '</tbody></table></div></article><article class="content-block span-12"><h3>Current Pod Health</h3><div class="table-scroll"><table><thead><tr><th>Pod</th><th>Phase</th><th>Readiness</th><th>Restarts</th></tr></thead><tbody>' + podRows + '</tbody></table></div></article></div>'
      + '<div class="tab-toolbar"><span>Collected from namespace-scoped Kubernetes evidence; no browser-side risk inference.</span><button class="btn" type="button" data-refresh-runtime' + (canRefresh ? '' : ' disabled') + '>Refresh Runtime</button></div>';
  }

  function renderMopReplay(tab) {
    var data = tab.data || {};
    var timeline = Array.isArray(data.timeline) ? data.timeline : [];
    var checks = Array.isArray(data.checks) ? data.checks : [];
    function tone(value) {
      if (value === "passed" || value === "completed") return "green";
      if (value === "failed") return "red";
      return "amber";
    }
    var timelineRows = timeline.map(function (item) {
      return "<tr><td>" + ui.escapeHtml(String(item.sequence)) + "</td><td><strong>" + ui.escapeHtml(ui.label(item.phase)) + "</strong></td><td>" + ui.badge(tone(item.status), ui.label(item.status)) + "</td><td>" + ui.escapeHtml(item.summary || "") + "</td><td>" + ui.escapeHtml(ui.formatDate(item.created_at)) + "</td></tr>";
    }).join("");
    if (!timelineRows) timelineRows = '<tr><td colspan="5"><div class="empty-inline">No replay timeline facts were recorded.</div></td></tr>';
    var checkRows = checks.map(function (item) {
      return "<tr><td><strong>" + ui.escapeHtml(ui.label(item.type)) + "</strong></td><td>" + ui.badge(tone(item.status), ui.label(item.status)) + "</td><td>" + ui.escapeHtml(item.summary || "") + "</td></tr>";
    }).join("");
    if (!checkRows) checkRows = '<tr><td colspan="3"><div class="empty-inline">No replay check facts were recorded.</div></td></tr>';
    var limitations = (data.limitations || []).map(function (item) { return "<li>" + ui.escapeHtml(item) + "</li>"; }).join("") || "<li>No limitations were recorded.</li>";
    var explanation = tab.safe_explanation
      ? '<article class="content-block span-12"><p class="eyebrow">SIGMA 5 PRO / Bounded Replay Summary</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><p class="muted">SIGMA summarizes deterministic replay facts only. It cannot run replay, alter the decision, or grant execution eligibility.</p></article>'
      : "";
    return '<div class="notice runtime-authority">Replay is separately approved, isolated rehearsal evidence. It never copies production Secret values or data and does not prove production success.</div>'
      + metricCards(tab.metrics || [])
      + '<div class="drift-status-grid"><article class="drift-status-card"><span>Replay result</span><strong>' + ui.escapeHtml(ui.label(data.status || "unknown")) + '</strong>' + ui.badge(tone(data.status), data.status || "unknown") + '</article><article class="drift-status-card"><span>Isolation</span><strong>' + ui.escapeHtml(data.isolation || "unavailable") + '</strong><small>Approval ' + ui.escapeHtml(data.approval_id || "unavailable") + '</small></article><article class="drift-status-card"><span>Cleanup</span><strong>' + ui.escapeHtml(ui.label(data.cleanup_status || "unknown")) + '</strong>' + ui.badge(tone(data.cleanup_status), data.cleanup_status || "unknown") + '</article><article class="drift-status-card"><span>Retention</span><strong>' + Number(data.retention_seconds || 0) + ' seconds</strong><small>' + ui.escapeHtml(data.rules_version || "rules unavailable") + '</small></article></div>'
      + explanation
      + '<div class="content-grid tab-followup"><article class="content-block span-8"><h3>Synthetic Secret Strategy</h3><p>' + ui.escapeHtml(data.synthetic_secret_strategy || "Not recorded.") + '</p><dl class="fact-list"><div><dt>Production Secrets copied</dt><dd>No</dd></div><div><dt>Production data copied</dt><dd>No</dd></div><div><dt>Decision effect</dt><dd>None</dd></div></dl></article><article class="content-block span-4"><h3>Limitations</h3><ul class="artifact-list">' + limitations + '</ul></article></div>'
      + '<article class="content-block"><h3>Replay Timeline</h3><div class="table-scroll"><table><thead><tr><th>#</th><th>Phase</th><th>Status</th><th>Summary</th><th>Recorded</th></tr></thead><tbody>' + timelineRows + '</tbody></table></div></article>'
      + '<article class="content-block"><h3>Readiness, Smoke Tests, and Cleanup</h3><div class="table-scroll"><table><thead><tr><th>Check</th><th>Status</th><th>Summary</th></tr></thead><tbody>' + checkRows + '</tbody></table></div></article>'
      + '<div class="notice amber">No Run Replay control is exposed. An independently approved replay worker must submit terminal, redacted evidence through the authenticated server API.</div>';
  }
  function renderReleaseNoteValidation(tab) {
    var data = tab.data || {};
    var counts = data.claim_counts || {};
    var claims = Array.isArray(data.claims) ? data.claims : [];
    var extraction = data.extraction || {};
    function tone(value) {
      if (value === "passed" || value === "supported") return "green";
      if (value === "failed" || value === "contradicted") return "red";
      return "amber";
    }
    var form = '<article class="content-block release-note-linker"><p class="eyebrow">Link Artifact</p><h3>Validate Release-note Markdown</h3><p>Paste the generated Markdown and provide its artifact ID. SIGMA extracts bounded claims; deterministic twin evidence classifies them.</p><div class="release-note-link-grid"><label class="field"><span>Artifact ID</span><input type="text" data-release-note-artifact-id maxlength="500" placeholder="art_release_note_..." value="' + ui.escapeHtml(data.release_note_artifact_id || "") + '"></label><label class="field release-note-content-field"><span>Release-note Markdown</span><textarea data-release-note-content maxlength="200000" placeholder="# Release notes&#10;&#10;## Configuration&#10;- Updated runtime configuration..."></textarea></label></div><div class="tab-toolbar"><span>Content is sent for bounded claim extraction and is not persisted by the twin. Hashes and safe claim summaries are persisted.</span><button class="btn primary" type="button" data-validate-release-note>Validate Artifact</button></div><p class="muted" data-release-note-status></p></article>';
    if (!data.release_note_artifact_id) {
      return '<div class="notice amber">Not Run. Link a release-note artifact to begin validation.</div>' + form + '<div class="notice runtime-authority">Editorial only. Validation cannot overwrite an artifact or change execution eligibility.</div>';
    }
    var rows = claims.map(function (item) {
      return '<tr><td><strong>' + ui.escapeHtml(ui.label(item.category || "other")) + '</strong></td><td>' + ui.escapeHtml(item.claim || "") + '</td><td>' + ui.badge(tone(item.status), ui.label(item.status || "unknown")) + '</td><td>' + ui.escapeHtml(item.summary || "") + '</td><td>' + ui.escapeHtml(String((item.evidence_refs || []).length)) + '</td></tr>';
    }).join("");
    if (!rows) rows = '<tr><td colspan="5"><div class="empty-inline">No bounded claims were extracted.</div></td></tr>';
    var missing = (data.missing_operational_notes || []).map(function (item) { return '<li>' + ui.escapeHtml(item) + '</li>'; }).join("") || '<li>No missing operational notes.</li>';
    var corrections = (data.suggested_corrections || []).map(function (item) { return '<li>' + ui.escapeHtml(item) + '</li>'; }).join("") || '<li>No editorial corrections suggested.</li>';
    return '<div class="notice ' + tone(data.status) + '">Validation ' + ui.escapeHtml(ui.label(data.status || "unknown")) + '. Editorial findings do not modify execution eligibility.</div>'
      + metricCards(tab.metrics || [])
      + '<div class="content-grid tab-followup"><article class="content-block span-8"><h3>Artifact</h3><dl class="fact-list"><div><dt>Artifact ID</dt><dd>' + ui.escapeHtml(data.release_note_artifact_id) + '</dd></div><div><dt>SHA-256</dt><dd>' + ui.escapeHtml((data.release_note_artifact_hash || "").slice(0, 24)) + '...</dd></div><div><dt>Validated</dt><dd>' + ui.escapeHtml(ui.formatDate(data.validated_at)) + '</dd></div><div><dt>Rules</dt><dd>' + ui.escapeHtml(data.rules_version || "unavailable") + '</dd></div></dl></article><article class="content-block span-4"><h3>Bounded Extraction</h3><p>' + ui.badge(extraction.fallback_used ? "amber" : "green", extraction.fallback_used ? "deterministic fallback" : "model extracted") + '</p><p>' + ui.escapeHtml(extraction.safe_summary || "No extraction summary.") + '</p><small>Prompt ' + ui.escapeHtml((extraction.prompt_hash || "").slice(0, 16) || "not available") + ' / Input ' + ui.escapeHtml((extraction.input_hash || "").slice(0, 16) || "not available") + '</small></article></div>'
      + '<article class="content-block"><h3>Claim Map</h3><div class="table-scroll"><table><thead><tr><th>Category</th><th>Claim</th><th>Status</th><th>Deterministic assessment</th><th>Evidence</th></tr></thead><tbody>' + rows + '</tbody></table></div></article>'
      + '<div class="content-grid"><article class="content-block span-6"><h3>Missing Operational Notes</h3><ul class="artifact-list">' + missing + '</ul></article><article class="content-block span-6"><h3>Suggested Corrections</h3><ul class="artifact-list">' + corrections + '</ul></article></div>'
      + '<div class="notice runtime-authority">Automatic overwrite: disabled. Execution eligibility effect: none. The model extracts claims only; deterministic evidence retains classification authority.</div>'
      + form;
  }
  function renderAudit(tab) {
    var events = Array.isArray(tab.events) ? tab.events : [];
    var page = tab.page || {};
    var report = tab.report || {};
    var explanation = tab.safe_explanation && tab.safe_explanation.content
      ? '<article class="content-block audit-executive"><p class="eyebrow">SIGMA 5 PRO Executive Summary</p><p>' + ui.escapeHtml(tab.safe_explanation.content) + '</p><small>Grounded in report ' + ui.escapeHtml(report.report_id || "not available") + '. Model authority: none.</small></article>'
      : "";
    var reportPanel = '<article class="content-block audit-report-card"><div><p class="eyebrow">Deterministic Report</p><h3>' + ui.escapeHtml(report.report_id || "Report not available") + '</h3><p>Decision ' + ui.badge(report.decision || "pending") + ' <span class="muted">Hash ' + ui.escapeHtml((report.report_hash || "").slice(0, 18) || "not available") + '</span></p></div><div class="action-row"><button class="btn" type="button" data-audit-download="json">Download JSON</button><button class="btn" type="button" data-audit-download="markdown">Download Markdown</button><button class="btn" type="button" data-copy-tab>Copy page</button></div></article>';
    var rows = events.map(function (event) {
      var actor = event.actor || {};
      var hashes = Object.keys(event.hashes || {}).map(function (key) { return ui.label(key) + " " + String(event.hashes[key]).slice(0, 12); }).join(" · ");
      var versions = Object.keys(event.versions || {}).map(function (key) { return ui.label(key) + " " + event.versions[key]; }).join(" · ");
      return '<li id="' + ui.escapeHtml(event.event_id) + '" data-audit-event><time>' + ui.escapeHtml(ui.formatDate(event.created_at) + " · sequence " + event.sequence) + '</time><button class="timeline-link" type="button" data-audit-modal="' + ui.escapeHtml(event.event_id) + '"><strong>' + ui.escapeHtml(ui.label(event.event_type)) + '</strong></button><p>' + ui.badge(event.status || "completed") + ' <strong>' + ui.escapeHtml(ui.label(event.phase || "lifecycle")) + '</strong> · ' + ui.escapeHtml(event.safe_summary || "") + '</p><small>Actor ' + ui.escapeHtml(actor.display_name || actor.id || "execution-agent") + (hashes ? " · " + ui.escapeHtml(hashes) : "") + (versions ? " · " + ui.escapeHtml(versions) : "") + '</small></li>';
    }).join("");
    if (!rows) rows = '<li><p>No audit events were returned for this page.</p></li>';
    var offset = Number(page.offset || 0);
    var total = Number(page.result_count || tab.total_events || events.length);
    return '<div class="notice runtime-authority">Append-only, redacted audit facts. Hidden model reasoning and Secret values are not stored in reports.</div>' + reportPanel + explanation + '<div class="tab-toolbar"><span>' + total + ' immutable safe-summary events</span></div><ol class="timeline">' + rows + '</ol><div class="table-footer"><span>Events ' + (events.length ? offset + 1 : 0) + '–' + (offset + events.length) + ' of ' + total + '</span><div class="pagination"><button class="btn" type="button" data-audit-page="previous"' + (auditCursorHistory.length ? "" : " disabled") + '>Previous</button><button class="btn" type="button" data-audit-page="next"' + (page.has_more ? "" : " disabled") + '>Next</button></div></div>';
  }

  function renderTab(tab) {
    if (tab.kind === "release-note-validation") return renderReleaseNoteValidation(tab);
    if (tab.kind === "mop-replay" && tab.state === "available") return renderMopReplay(tab);
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
    if (slug !== "audit") { auditCursor = null; auditCursorHistory = []; }
    ui.updateUrl({ tab: slug, twin_id: twin.twin_id }, replace);
    var panel = document.getElementById("panel-" + slug);
    panel.dataset.loading = "true";
    panel.innerHTML = ui.stateView("loading", "Loading " + ui.label(slug), "Reading this evidence module through the configured data adapter.", false);
    var tabQuery = {};
    if (slug === "release-delta") tabQuery = Object.assign({}, deltaFilters);
    if (slug === "dependency-graph") tabQuery = Object.assign({}, graphFilters);
    if (slug === "policy" && policyFilter !== "all") tabQuery = { effect: policyFilter === "block" ? "deny" : "approval_required" };
    if (slug === "dry-run") tabQuery = Object.assign({}, dryRunFilters);
    if (slug === "audit") tabQuery = { cursor: auditCursor, limit: 25 };
    adapter.getTab(twin.twin_id, slug, twin.decision_version, tabQuery).then(function (tabData) {

      panel.innerHTML = '<div class="tab-intro"><div><p class="eyebrow">' + (tabData.non_authoritative ? "Mock / Non-authoritative Module" : "Real Evidence") + ' - ' + ui.escapeHtml(ui.label(tabData.state)) + '</p><h2>' + ui.escapeHtml(tabData.title) + '</h2><p>' + ui.escapeHtml(tabData.summary) + '</p></div>' + ui.badge(tabData.state) + '</div><div data-tab-content>' + renderTab(tabData) + '</div>';
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
      if (code === "refresh_runtime_behavior") {
        adapter.refreshRuntimeBehavior(twin.twin_id).then(function () {
          adapter.invalidateCache(twin.twin_id);
          return adapter.getTwin(twin.twin_id);
        }).then(function (item) {
          renderTwin(item);
          activateTab("runtime-behavior", false);
        }).catch(function (error) {
          ui.showModal({ title: "Runtime refresh failed", body: "<p>" + ui.escapeHtml(error.message) + "</p>" });
        });
        return;
      }
      if (code === "refresh_drift") {
        adapter.refreshDrift(twin.twin_id).then(function () {
          adapter.invalidateCache(twin.twin_id);
          return adapter.getTwin(twin.twin_id);
        }).then(function (item) {
          renderTwin(item);
          activateTab("drift", false);
        }).catch(function (error) {
          ui.showModal({ title: "Drift refresh failed", body: "<p>" + ui.escapeHtml(error.message) + "</p>" });
        });
        return;
      }
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
    var refreshDrift = event.target.closest("[data-refresh-drift]");
    if (refreshDrift && twin) {
      var contract = actionsFor(twin).find(function (item) { return item.code === "refresh_drift"; });
      if (contract && contract.enabled) {
        adapter.refreshDrift(twin.twin_id).then(function () {
          adapter.invalidateCache(twin.twin_id);
          return adapter.getTwin(twin.twin_id);
        }).then(function (item) {
          renderTwin(item);
          activateTab("drift", false);
        }).catch(function (error) {
          ui.showModal({ title: "Drift refresh failed", body: "<p>" + ui.escapeHtml(error.message) + "</p>" });
        });
      }
      return;
    }
    var refreshRuntime = event.target.closest("[data-refresh-runtime]");
    if (refreshRuntime && twin) {
      var runtimeContract = actionsFor(twin).find(function (item) { return item.code === "refresh_runtime_behavior"; });
      if (runtimeContract && runtimeContract.enabled) {
        adapter.refreshRuntimeBehavior(twin.twin_id).then(function () {
          adapter.invalidateCache(twin.twin_id);
          return adapter.getTwin(twin.twin_id);
        }).then(function (item) {
          renderTwin(item);
          activateTab("runtime-behavior", false);
        }).catch(function (error) {
          ui.showModal({ title: "Runtime refresh failed", body: "<p>" + ui.escapeHtml(error.message) + "</p>" });
        });
      }
      return;
    }    var validateReleaseNote = event.target.closest("[data-validate-release-note]");
    if (validateReleaseNote && twin) {
      var panel = document.getElementById("panel-release-note-validation");
      var artifactInput = panel.querySelector("[data-release-note-artifact-id]");
      var contentInput = panel.querySelector("[data-release-note-content]");
      var statusNode = panel.querySelector("[data-release-note-status]");
      var artifactId = artifactInput ? artifactInput.value.trim() : "";
      var content = contentInput ? contentInput.value.trim() : "";
      if (!artifactId || !content) {
        if (statusNode) statusNode.textContent = "Artifact ID and release-note Markdown are required.";
        return;
      }
      validateReleaseNote.disabled = true;
      validateReleaseNote.textContent = "Validating...";
      if (statusNode) statusNode.textContent = "Extracting bounded claims and matching deterministic twin evidence.";
      adapter.validateReleaseNote(twin.twin_id, {
        release_note_artifact_id: artifactId,
        content: content
      }).then(function () {
        adapter.invalidateCache(twin.twin_id);
        return adapter.getTwin(twin.twin_id);
      }).then(function (item) {
        renderTwin(item);
        activateTab("release-note-validation", false);
      }).catch(function (error) {
        validateReleaseNote.disabled = false;
        validateReleaseNote.textContent = "Validate Artifact";
        if (statusNode) statusNode.textContent = error.message;
      });
      return;
    }    var jump = event.target.closest("[data-jump-tab]");
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
    var clearDryRun = event.target.closest("[data-dry-run-clear]");
    if (clearDryRun && twin) {
      dryRunFilters = { phase: "", step: "", resource: "", tool: "", outcome: "" };
      activateTab("dry-run", true);
      return;
    }
    var copyFingerprints = event.target.closest("[data-copy-fingerprints]");
    if (copyFingerprints) {
      var dryRunPanel = document.getElementById("panel-dry-run");
      var dryRunData = (dryRunPanel._tabData || {}).data || {};
      ui.copyText((dryRunData.command_fingerprints || []).join("\n"), "Command fingerprints copied.");
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
    }
    var graphNode = event.target.closest("[data-graph-node]");
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
    if (auditModal) { var auditPanel = document.getElementById("panel-audit"); var auditEvent = ((auditPanel && auditPanel._tabData && auditPanel._tabData.events) || []).find(function (item) { return item.event_id === auditModal.getAttribute("data-audit-modal"); }); ui.showModal({ eyebrow: "Immutable Audit Event", title: auditModal.getAttribute("data-audit-modal"), body: '<div class="log-view">' + ui.escapeHtml(JSON.stringify(auditEvent || {}, null, 2)) + '</div>' }); }
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
    if (auditPager && twin) {
      var auditPanel = document.getElementById("panel-audit");
      var auditData = auditPanel._tabData || {};
      if (auditPager.getAttribute("data-audit-page") === "next" && auditData.page && auditData.page.next_cursor) {
        auditCursorHistory.push(auditCursor);
        auditCursor = auditData.page.next_cursor;
      } else if (auditPager.getAttribute("data-audit-page") === "previous" && auditCursorHistory.length) {
        auditCursor = auditCursorHistory.pop() || null;
      }
      activateTab("audit", true);
      return;
    }
    var auditDownload = event.target.closest("[data-audit-download]");
    if (auditDownload && twin) {
      window.location.href = "/api/digital-twins/" + encodeURIComponent(twin.twin_id) + "/reports/" + encodeURIComponent(auditDownload.getAttribute("data-audit-download"));
      return;
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
      return;
    }
    var dryRunFilter = event.target.closest("[data-dry-run-filter]");
    if (dryRunFilter && twin) {
      dryRunFilters[dryRunFilter.getAttribute("data-dry-run-filter")] = dryRunFilter.value.trim();
      activateTab("dry-run", true);
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
