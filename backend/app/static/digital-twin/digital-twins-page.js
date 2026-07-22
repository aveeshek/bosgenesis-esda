(function () {
  "use strict";

  var adapter = window.esdaTwinAdapter;
  var ui = window.ESDATwinUI;
  var form = document.getElementById("twin-filter-form");
  var body = document.getElementById("twin-list-body");
  var summary = document.getElementById("twin-result-summary");
  var warning = document.getElementById("twin-list-warning");
  var pagination = document.getElementById("twin-pagination");
  var stateHost = document.getElementById("twin-list-state");
  var responseMode = document.getElementById("fixture-response-mode");
  var scenarioSelect = document.getElementById("fixture-scenario");
  var adapterMode = (function () {
    try { return window.frameElement ? window.frameElement.dataset.adapterMode : "browser_fixture"; }
    catch (error) { return "browser_fixture"; }
  })();
  var realCore = adapterMode === "real_core";
  var refreshTimer = 0;
  var searchTimer = 0;
  var lastResponse = null;

  if (!adapter || !form || !body) return;

  function queryFromUrl() {
    var search = ui.params();
    var requestedSort = search.get("sort") || "created_at";
    return {
      search: search.get("search") || "",
      decision: search.get("decision") || "all",
      lifecycle: search.get("lifecycle") || "all",
      freshness: search.get("freshness") || "all",
      target: search.get("target") || "all",
      bundle: search.get("bundle") || "all",
      creator: search.get("creator") || "all",
      date: search.get("date") || "",
      linked_execution: search.get("linked_execution") || "all",
      sort: requestedSort === "risk" ? "risk_score" : requestedSort,
      direction: search.get("direction") || "desc",
      cursor: search.get("cursor") || null,
      // Real-core query contract remains limit: 25; fixtures use six rows to exercise pagination.
      limit: realCore ? 25 : 6,
      mock_state: search.get("mock_state") || adapter.getResponseMode()
    };
  }

  function syncForm(query) {
    Object.keys(query).forEach(function (key) {
      var field = form.elements.namedItem(key);
      if (field && query[key] != null) field.value = query[key];
    });
    if (responseMode) responseMode.value = query.mock_state;
  }

  function updateMetrics(items) {
    var counts = lastResponse && lastResponse.metrics
      ? lastResponse.metrics
      : { total: items.length, green: 0, amber: 0, red: 0, generating: 0, stale: 0, linked: 0 };
    if (!(lastResponse && lastResponse.metrics)) {
      items.forEach(function (item) {
        if (counts[item.decision] != null) counts[item.decision] += 1;
        if (["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0) counts.generating += 1;
        if (["stale", "drifted", "expired"].indexOf(item.freshness.status) >= 0) counts.stale += 1;
        if (item.relationships.execution_status !== "unlinked") counts.linked += 1;
      });
    }
    Object.keys(counts).forEach(function (key) {
      var element = document.querySelector("[data-metric='" + key + "']");
      if (element) element.textContent = counts[key];
    });
  }

  function riskMarkup(risk) {
    if (!risk || risk.score == null) return '<span class="faint">Not calculated</span>';
    var tone = risk.score >= 75 ? "high" : risk.score >= 45 ? "medium" : "low";
    return '<span class="score ' + tone + '" aria-label="Risk score ' + ui.escapeHtml(risk.score) + '">' + ui.escapeHtml(risk.score) + "</span>";
  }

  function actionIcon(code) {
    return { open_twin: "\u2197", regenerate: "\u21bb", download_report: "\u2193", open_execution: "\u25b6", request_approval: "\u2713", cancel_generation: "\u00d7" }[code] || "\u2022";
  }

  function actionsMarkup(twin) {
    var actions = Array.isArray(twin.actions) ? twin.actions : [];
    return actions.filter(function (action) { return action.visible !== false; }).map(function (action) {
      return '<button class="btn" type="button" data-row-action="' + ui.escapeHtml(action.code) + '" data-twin-id="' + ui.escapeHtml(twin.twin_id) + '" title="' + ui.escapeHtml(action.enabled ? action.label : action.disabled_reason) + '" aria-label="' + ui.escapeHtml(action.enabled ? action.label : action.label + ": " + action.disabled_reason) + '"' + (action.enabled ? "" : " disabled") + ">" + actionIcon(action.code) + "</button>";
    }).join("");
  }

  function rowMarkup(twin) {
    twin = Object.assign({}, twin, { lifecycle_status: twin.visible_lifecycle || twin.lifecycle_status });
    var linked = twin.relationships.execution_status === "unlinked"
      ? '<span class="cell-main">Not linked</span><span class="cell-sub">No execution selected</span>'
      : '<span class="cell-main">' + ui.escapeHtml(twin.relationships.execution_id || twin.relationships.execution_status) + '</span><span class="cell-sub">' + ui.escapeHtml(ui.label(twin.relationships.execution_status)) + "</span>";
    return '<tr data-twin-row="' + ui.escapeHtml(twin.twin_id) + '" tabindex="0" aria-label="Open ' + ui.escapeHtml(twin.display_name) + '"><td><span class="cell-main">' + ui.escapeHtml(twin.twin_id) + '</span><span class="cell-sub">' + ui.escapeHtml(ui.label(twin.lifecycle_status)) + " \u00b7 v" + ui.escapeHtml(twin.decision_version) + "</span></td><td>" + ui.badge(twin.decision === "pending" ? twin.lifecycle_status : twin.decision) + "</td><td>" + riskMarkup(twin.risk) + '</td><td><span class="cell-main">' + ui.escapeHtml(twin.target.cluster_name) + '</span><span class="cell-sub">' + ui.escapeHtml(twin.target.cluster_id) + '</span></td><td><code>' + ui.escapeHtml(twin.target.namespace) + '</code></td><td><span class="cell-main">' + ui.escapeHtml(twin.display_name) + '</span><span class="cell-sub">' + ui.escapeHtml(twin.bundle.bundle_name) + '</span></td><td>' + ui.escapeHtml(twin.bundle.release_version) + '</td><td>' + ui.badge(twin.freshness.status, ui.label(twin.freshness.status)) + '</td><td>' + ui.escapeHtml(twin.created_by_display) + '</td><td class="nowrap">' + ui.escapeHtml(ui.formatDate(twin.created_at)) + "</td><td>" + linked + '</td><td><div class="row-actions">' + actionsMarkup(twin) + "</div></td></tr>";
  }

  function loading() {
    stateHost.hidden = true;
    body.innerHTML = ui.loadingRows(12, 5);
    summary.textContent = "Loading twin runs...";
    warning.hidden = true;
  }

  function renderPagination(page) {
    pagination.innerHTML = '<button class="btn" type="button" data-page-cursor="' + ui.escapeHtml(page.previous_cursor || "") + '"' + (page.previous_cursor ? "" : " disabled") + ' aria-label="Previous page">&lsaquo;</button><span class="pagination-label">' + (page.result_count ? page.offset + 1 : 0) + "&ndash;" + Math.min(page.offset + page.limit, page.result_count) + " of " + page.result_count + '</span><button class="btn" type="button" data-page-cursor="' + ui.escapeHtml(page.next_cursor || "") + '"' + (page.has_more ? "" : " disabled") + ' aria-label="Next page">&rsaquo;</button>';
  }

  function renderResponse(response) {
    lastResponse = response;
    warning.hidden = !response.warning;
    warning.textContent = response.warning || "";
    if (!response.items.length) {
      body.innerHTML = "";
      var hasFilter = Object.keys(response.applied_query || {}).some(function (key) { return ["search", "decision", "lifecycle", "freshness", "target", "bundle", "creator", "date", "linked_execution"].indexOf(key) >= 0 && response.applied_query[key] && response.applied_query[key] !== "all"; });
      stateHost.hidden = false;
      stateHost.innerHTML = ui.stateView("empty", hasFilter ? "No matching twins" : "No Digital Twins yet", hasFilter ? "Clear one or more filters to restore the full result set." : (realCore ? "Create a real provisional twin through the execution-agent contract." : "Generate the first mock Digital Twin."), false);
    } else {
      stateHost.hidden = true;
      body.innerHTML = response.items.map(rowMarkup).join("");
    }
    summary.textContent = response.page.result_count + " results \u00b7 " + (realCore ? "real lifecycle contract " : "fixture contract ") + response.schema_version;
    renderPagination(response.page);
    updateMetrics(response.items);
    setupBoundedRefresh(response.items);
  }

  function renderFailure(error) {
    lastResponse = null;
    body.innerHTML = "";
    stateHost.hidden = false;
    stateHost.innerHTML = ui.stateView("failed", "Twin runs unavailable", error.message, error.retryable);
    warning.hidden = true;
    summary.textContent = "Fixture response failed";
    pagination.innerHTML = "";
  }

  function load(options) {
    options = options || {};
    var query = queryFromUrl();
    syncForm(query);
    if (!options.silent) loading();
    return adapter.listTwins(query).then(renderResponse).catch(renderFailure);
  }

  function formatBytes(value) {
    var bytes = Number(value || 0);
    if (!bytes) return "size not recorded";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function launchToken() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  }

  function openRealSimulationLauncher() {
    var shell = ui.showModal({
      eyebrow: "On-Demand Namespace Twin",
      title: "Run Digital Simulation",
      body: '<div class="state-view compact"><div class="spinner" aria-hidden="true"></div><h3>Loading eligible bundles</h3><p>Reading persisted MoP runs and allowed targets from ESDA.</p></div>',
      actions: '<button class="btn" type="button" data-modal-close>Cancel</button>'
    });
    var bodyHost = shell.querySelector(".mock-modal-body");
    var actionHost = shell.querySelector(".mock-modal-actions");

    adapter.listGenerationSources().then(function (catalog) {
      var bundles = (catalog.bundles || []).filter(function (item) { return item.eligible; });
      var targets = catalog.target_namespaces || [];
      if (!bundles.length || !targets.length) {
        bodyHost.innerHTML = ui.stateView(
          "empty",
          "No eligible simulation source",
          !bundles.length
            ? "Complete and publish a MoP bundle, then retry this action."
            : "No target namespace is allowed by the current ESDA policy.",
          false
        );
        return;
      }

      bodyHost.innerHTML =
        '<form id="real-twin-launch-form">' +
          '<div class="simulation-launch-grid">' +
            '<div class="field simulation-bundle-field"><label for="simulation-bundle">Published MoP Bundle</label><select id="simulation-bundle" required>' +
              bundles.map(function (item, index) {
                return '<option value="' + ui.escapeHtml(item.run_id) + '"' + (index ? "" : " selected") + '>' +
                  ui.escapeHtml((item.title || item.run_id) + " \u00b7 " + (item.source_namespace || "unknown source")) +
                "</option>";
              }).join("") +
            '</select></div>' +
            '<div class="field"><label for="simulation-target">Target Namespace</label><select id="simulation-target" required>' +
              targets.map(function (target) {
                return '<option value="' + ui.escapeHtml(target) + '"' +
                  (target === catalog.default_target_namespace ? " selected" : "") + ">" +
                  ui.escapeHtml(target) + "</option>";
              }).join("") +
            '</select></div>' +
            '<div class="field"><label for="simulation-cluster">Target Cluster</label><input id="simulation-cluster" maxlength="253" required value="' +
              ui.escapeHtml(catalog.default_target_cluster || "configured-cluster") + '"></div>' +
          '</div>' +
          '<div class="simulation-bundle-facts" id="simulation-bundle-facts"></div>' +
          '<p class="muted simulation-contract">ESDA resolves the published artifact on the server, creates one authoritative dry-run job, and reconciles its evidence into the final deterministic decision. The browser never submits an artifact URL and no mutation is performed.</p>' +
          '<div class="notice red" id="simulation-launch-status" role="status" hidden></div>' +
        "</form>";
      actionHost.innerHTML =
        '<button class="btn" type="button" data-modal-close>Cancel</button>' +
        '<button class="btn primary" type="submit" form="real-twin-launch-form" id="run-digital-simulation">Run Simulation</button>';
      actionHost.querySelector("[data-modal-close]").addEventListener("click", function () { shell.remove(); });

      var form = shell.querySelector("#real-twin-launch-form");
      var bundleSelect = shell.querySelector("#simulation-bundle");
      var facts = shell.querySelector("#simulation-bundle-facts");
      var status = shell.querySelector("#simulation-launch-status");
      var submit = shell.querySelector("#run-digital-simulation");

      function selectedBundle() {
        return bundles.find(function (item) { return item.run_id === bundleSelect.value; });
      }

      function renderBundleFacts() {
        var item = selectedBundle();
        if (!item) return;
        facts.innerHTML =
          '<span><strong>Artifact</strong>' + ui.escapeHtml(item.filename || "mop-bundle.zip") + "</span>" +
          '<span><strong>Generated</strong>' + ui.escapeHtml(item.generated_at || "not recorded") + "</span>" +
          '<span><strong>Published</strong>' + ui.escapeHtml(item.publish_folder || "not published") + "</span>" +
          '<span><strong>Size</strong>' + ui.escapeHtml(formatBytes(item.size_bytes)) + "</span>";
      }

      bundleSelect.addEventListener("change", renderBundleFacts);
      renderBundleFacts();

      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var bundle = selectedBundle();
        var targetNamespace = shell.querySelector("#simulation-target").value;
        var targetCluster = shell.querySelector("#simulation-cluster").value.trim();
        if (!bundle || !targetNamespace || !targetCluster) {
          status.hidden = false;
          status.textContent = "Select a bundle, namespace, and target cluster.";
          return;
        }
        submit.disabled = true;
        submit.textContent = "Starting...";
        status.className = "notice";
        status.hidden = false;
        status.textContent = "Registering the immutable bundle and starting the server-side twin lifecycle.";

        adapter.startGeneration({
          bundle_run_id: bundle.run_id,
          bundle_artifact_id: bundle.artifact_id,
          target_namespace: targetNamespace,
          target_cluster: targetCluster,
          run_authoritative_dry_run: true,
          idempotency_key: ("esda-twin-" + bundle.run_id + "-" + targetNamespace + "-" + launchToken()).slice(0, 200)
        }).then(function (twin) {
          status.className = "notice green";
          status.textContent = "Full simulation started. Opening authoritative live progress.";
          ui.navigate(ui.detailHref(twin.twin_id, "overview", { progress: "1" }));
        }).catch(function (error) {
          submit.disabled = false;
          submit.textContent = "Run Simulation";
          status.className = "notice red";
          status.hidden = false;
          status.textContent = error.message + (error.retryable ? " You can retry this request safely." : "");
        });
      });
    }).catch(function (error) {
      bodyHost.innerHTML = ui.stateView("failed", "Simulation sources unavailable", error.message, error.retryable);
    });
  }

  function formQuery() {
    var values = {};
    new FormData(form).forEach(function (value, key) { values[key] = String(value); });
    values.cursor = null;
    return values;
  }

  function applyForm(replace) {
    ui.updateUrl(formQuery(), replace);
    load();
  }

  function setupBoundedRefresh(items) {
    window.clearTimeout(refreshTimer);
    var active = items.filter(function (item) { return ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0; });
    if (!active.length) return;
    refreshTimer = window.setTimeout(function () {
      if (realCore) {
        load({ silent: true });
        return;
      }
      Promise.all(active.map(function (item) { return adapter.advanceGeneration(item.twin_id); })).then(function () { return load({ silent: true }); });
    }, 1800);
  }

  form.addEventListener("change", function () { applyForm(false); });
  form.addEventListener("submit", function (event) { event.preventDefault(); applyForm(false); });
  form.elements.search.addEventListener("input", function () {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(function () { applyForm(false); }, 260);
  });

  document.addEventListener("click", function (event) {
    var retry = event.target.closest("[data-retry]");
    if (retry) {
      adapter.setResponseMode("success");
      ui.updateUrl({ mock_state: "success" }, true);
      load();
      return;
    }
    var pageButton = event.target.closest("[data-page-cursor]");
    if (pageButton && !pageButton.disabled) {
      ui.updateUrl({ cursor: pageButton.getAttribute("data-page-cursor") || null }, false);
      load();
      return;
    }
    var actionButton = event.target.closest("[data-row-action]");
    if (actionButton) {
      var twinId = actionButton.getAttribute("data-twin-id");
      var code = actionButton.getAttribute("data-row-action");
      var selectedTwin = (lastResponse.items || []).find(function (item) { return item.twin_id === twinId; });
      var contract = selectedTwin && (selectedTwin.actions || []).find(function (item) { return item.code === code; });
      if (!contract || !contract.enabled) return;
      if (code === "open_twin") ui.navigate(ui.detailHref(twinId, "overview"));
      if (["open_bundle", "download_report", "export_evidence", "request_approval", "start_execution", "regenerate_twin"].indexOf(code) >= 0) ui.navigate(contract.href);
      if (code === "cancel_generation") adapter.cancelGeneration(twinId).then(function () { ui.showToast("Mock generation cancelled.", "info"); load({ silent: true }); });
      return;
    }
    var row = event.target.closest("[data-twin-row]");
    if (row) ui.navigate(ui.detailHref(row.getAttribute("data-twin-row"), "overview"));
    var sortButton = event.target.closest("[data-sort]");
    if (sortButton) {
      var current = queryFromUrl();
      var sort = sortButton.getAttribute("data-sort");
      ui.updateUrl({ sort: sort, direction: current.sort === sort && current.direction === "desc" ? "asc" : "desc", cursor: null }, false);
      load();
    }
  });

  document.addEventListener("keydown", function (event) {
    var row = event.target.closest && event.target.closest("[data-twin-row]");
    if (row && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      ui.navigate(ui.detailHref(row.getAttribute("data-twin-row"), "overview"));
    }
  });

  var refreshButton = document.querySelector("[data-list-refresh]");
  if (refreshButton) refreshButton.addEventListener("click", function () { load(); });

  var resetFiltersButton = document.getElementById("reset-filters");
  if (resetFiltersButton) resetFiltersButton.addEventListener("click", function () {
    form.reset();
    ui.updateUrl({ search: null, decision: null, lifecycle: null, freshness: null, target: null, bundle: null, creator: null, date: null, linked_execution: null, cursor: null }, false);
    load();
  });

  if (responseMode) responseMode.addEventListener("change", function () {
    adapter.setResponseMode(responseMode.value);
    ui.updateUrl({ mock_state: responseMode.value, cursor: null }, false);
    load();
  });

  var generateTwinButton = document.getElementById("generate-fixture");
  if (generateTwinButton) generateTwinButton.addEventListener("click", function () {
    try {
      if (realCore) {
        openRealSimulationLauncher();
        return;
      }
      adapter.startGeneration(scenarioSelect ? scenarioSelect.value : "").then(function (twin) {
        ui.navigate(ui.detailHref(twin.twin_id, "overview", { progress: "1" }));
      }).catch(function (error) {
        ui.showToast(error.message || "Twin generation could not be started.", "info");
      });
    } catch (error) {
      ui.showModal({
        eyebrow: "Digital Twin",
        title: "Simulation launcher unavailable",
        body: "<p>" + ui.escapeHtml(error.message || "The simulation launcher could not be opened.") + "</p>"
      });
    }
  });

  var clearMockHistoryButton = document.getElementById("clear-mock-history");
  if (clearMockHistoryButton) clearMockHistoryButton.addEventListener("click", function () {
    adapter.clearMockHistory().then(function () { ui.showToast("Server mock history cleared.", "success"); load(); });
  });

  window.addEventListener("popstate", function () { load(); });

  if (realCore) {
    var controlBand = document.querySelector(".mock-control-band");
    var marker = document.querySelector("[data-mode-marker]");
    var footer = document.querySelector("[data-mode-footer]");
    var generateButton = document.getElementById("generate-fixture");
    var clearButton = document.getElementById("clear-mock-history");
    responseMode.disabled = true;
    scenarioSelect.disabled = true;
    generateButton.disabled = false;
    generateButton.textContent = "Run Digital Simulation";
    generateButton.title = "Select a published MoP bundle and run the real server-side Namespace Twin lifecycle.";
    clearButton.disabled = true;
    clearButton.title = "Durable real audit history cannot be cleared from this page.";
    if (controlBand) controlBand.hidden = true;
    if (marker) marker.textContent = "Authoritative Real Core";
    if (footer) footer.textContent = "Authoritative lifecycle, bundle, policy, dry-run, drift, and runtime evidence | contract 1.0.0";
  }

  if (!realCore) {
    adapter.listScenarios().then(function (scenarios) {
      scenarioSelect.innerHTML = scenarios.map(function (scenario) { return '<option value="' + ui.escapeHtml(scenario.id) + '">' + ui.escapeHtml(scenario.label) + "</option>"; }).join("");
    });
  }
  load();
})();
