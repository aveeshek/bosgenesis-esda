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
  var refreshCount = 0;
  var searchTimer = 0;
  var lastResponse = null;

  if (!adapter || !form || !body) return;

  function queryFromUrl() {
    var search = ui.params();
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
      sort: search.get("sort") || "created_at",
      direction: search.get("direction") || "desc",
      cursor: search.get("cursor") || null,
      limit: 6,
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
    return { open_twin: "↗", regenerate: "↻", download_report: "↓", open_execution: "▶", request_approval: "✓", cancel_generation: "×" }[code] || "•";
  }

  function actionsMarkup(twin) {
    return twin.actions.filter(function (action) { return action.visible; }).map(function (action) {
      return '<button class="btn" type="button" data-row-action="' + ui.escapeHtml(action.code) + '" data-twin-id="' + ui.escapeHtml(twin.twin_id) + '" title="' + ui.escapeHtml(action.enabled ? action.label : action.disabled_reason) + '" aria-label="' + ui.escapeHtml(action.enabled ? action.label : action.label + ": " + action.disabled_reason) + '"' + (action.enabled ? "" : " disabled") + ">" + actionIcon(action.code) + "</button>";
    }).join("");
  }

  function rowMarkup(twin) {
    var linked = twin.relationships.execution_status === "unlinked"
      ? '<span class="cell-main">Not linked</span><span class="cell-sub">No execution selected</span>'
      : '<span class="cell-main">' + ui.escapeHtml(twin.relationships.execution_id || twin.relationships.execution_status) + '</span><span class="cell-sub">' + ui.escapeHtml(ui.label(twin.relationships.execution_status)) + "</span>";
    return '<tr data-twin-row="' + ui.escapeHtml(twin.twin_id) + '" tabindex="0" aria-label="Open ' + ui.escapeHtml(twin.display_name) + '"><td><span class="cell-main">' + ui.escapeHtml(twin.twin_id) + '</span><span class="cell-sub">' + ui.escapeHtml(ui.label(twin.lifecycle_status)) + " · v" + ui.escapeHtml(twin.decision_version) + "</span></td><td>" + ui.badge(twin.decision === "pending" ? twin.lifecycle_status : twin.decision) + "</td><td>" + riskMarkup(twin.risk) + '</td><td><span class="cell-main">' + ui.escapeHtml(twin.target.cluster_name) + '</span><span class="cell-sub">' + ui.escapeHtml(twin.target.cluster_id) + '</span></td><td><code>' + ui.escapeHtml(twin.target.namespace) + '</code></td><td><span class="cell-main">' + ui.escapeHtml(twin.display_name) + '</span><span class="cell-sub">' + ui.escapeHtml(twin.bundle.bundle_name) + '</span></td><td>' + ui.escapeHtml(twin.bundle.release_version) + '</td><td>' + ui.badge(twin.freshness.status, ui.label(twin.freshness.status)) + '</td><td>' + ui.escapeHtml(twin.created_by_display) + '</td><td class="nowrap">' + ui.escapeHtml(ui.formatDate(twin.created_at)) + "</td><td>" + linked + '</td><td><div class="row-actions">' + actionsMarkup(twin) + "</div></td></tr>";
  }

  function loading() {
    stateHost.hidden = true;
    body.innerHTML = ui.loadingRows(12, 5);
    summary.textContent = "Loading twin runs...";
    warning.hidden = true;
  }

  function renderPagination(page) {
    pagination.innerHTML = '<button class="btn" type="button" data-page-cursor="' + ui.escapeHtml(page.previous_cursor || "") + '"' + (page.previous_cursor ? "" : " disabled") + ' aria-label="Previous page">‹</button><span class="pagination-label">' + (page.result_count ? page.offset + 1 : 0) + "–" + Math.min(page.offset + page.limit, page.result_count) + " of " + page.result_count + '</span><button class="btn" type="button" data-page-cursor="' + ui.escapeHtml(page.next_cursor || "") + '"' + (page.has_more ? "" : " disabled") + ' aria-label="Next page">›</button>';
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
    summary.textContent = response.page.result_count + " results · " + (realCore ? "real lifecycle contract " : "fixture contract ") + response.schema_version;
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
    window.clearInterval(refreshTimer);
    refreshCount = 0;
    var active = items.filter(function (item) { return item.twin_id.indexOf("twin_mock_") === 0 && ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0; });
    if (!active.length) return;
    refreshTimer = window.setInterval(function () {
      refreshCount += 1;
      Promise.all(active.map(function (item) { return adapter.advanceGeneration(item.twin_id); })).then(function () { return load({ silent: true }); });
      if (refreshCount >= 5) window.clearInterval(refreshTimer);
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
      if (code === "open_twin") ui.navigate(ui.detailHref(twinId, "overview"));
      if (code === "open_execution") ui.navigate("/mop-execution?twin_id=" + encodeURIComponent(twinId));
      if (code === "download_report") adapter.getTwin(twinId).then(function (twin) { ui.mockDownload(twinId + "-report.json", twin); });
      if (code === "request_approval") adapter.requestApproval(twinId).then(function () { ui.showToast("Mock approval requested.", "success"); load({ silent: true }); });
      if (code === "regenerate") adapter.regenerate(twinId).then(function (next) { ui.navigate(ui.detailHref(next.twin_id, "overview", { progress: "1" })); });
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

  document.getElementById("reset-filters").addEventListener("click", function () {
    form.reset();
    ui.updateUrl({ search: null, decision: null, lifecycle: null, freshness: null, target: null, bundle: null, creator: null, date: null, linked_execution: null, cursor: null }, false);
    load();
  });

  responseMode.addEventListener("change", function () {
    adapter.setResponseMode(responseMode.value);
    ui.updateUrl({ mock_state: responseMode.value, cursor: null }, false);
    load();
  });

  document.getElementById("generate-fixture").addEventListener("click", function () {
    adapter.startGeneration(scenarioSelect.value).then(function (twin) {
      ui.navigate(ui.detailHref(twin.twin_id, "overview", { progress: "1" }));
    });
  });

  document.getElementById("clear-mock-history").addEventListener("click", function () {
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
    generateButton.disabled = true;
    generateButton.title = "Real twin creation requires a validated bundle source and target namespace through the execution-agent contract.";
    clearButton.disabled = true;
    clearButton.title = "Durable real audit history cannot be cleared from this page.";
    if (controlBand) controlBand.setAttribute("aria-label", "Real core and mock module status");
    if (marker) marker.textContent = "Real Core + Mock Modules";
    if (footer) footer.textContent = "Real lifecycle and bundle facts | Mock evidence modules are non-authoritative | contract 1.0.0";
  }

  adapter.listScenarios().then(function (scenarios) {
    scenarioSelect.innerHTML = scenarios.map(function (scenario) { return '<option value="' + ui.escapeHtml(scenario.id) + '">' + ui.escapeHtml(scenario.label) + "</option>"; }).join("");
  });
  load();
})();
