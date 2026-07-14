(function () {
  "use strict";

  var adapter = window.esdaTwinAdapter;
  var ui = window.ESDATwinUI;
  var gateHost = document.querySelector(".twin-gate");
  var resultHost = document.querySelector(".gate-result");
  var contextHost = document.querySelector(".gate-context");
  var selectedId = ui.params().get("twin_id");
  var allRows = [];
  var current = null;

  if (!adapter || !gateHost || !resultHost) return;

  function variantFor(twin) {
    if (["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(twin.lifecycle_status) >= 0) return "generating";
    if (twin.lifecycle_status === "superseded") return "expired";
    if (["stale", "drifted", "expired"].indexOf(twin.freshness.status) >= 0) return "stale";
    return twin.decision;
  }

  function gateHeadline(variant) {
    return {
      green: ["Eligible to proceed.", "Policy, evidence, freshness, rollback, and dry-run conditions are inside the configured ODD."],
      amber: ["Approval required.", "The release is conditionally eligible for this exact target, checksum, and decision version."],
      red: ["Execution blocked.", "A deterministic policy block cannot be overridden by approval or model output."],
      stale: ["Regenerate before execution.", "Freshness or drift invalidated the previous final decision."],
      expired: ["Decision version expired.", "A newer decision supersedes this immutable historical evidence."],
      generating: ["Decision not yet available.", "Preliminary evidence cannot authorize Bundle Execution."]
    }[variant] || ["Decision unavailable.", "Select a completed Digital Twin."];
  }

  function selectForVariant(variant) {
    var predicates = {
      green: function (item) { return item.decision === "green" && item.freshness.status === "fresh"; },
      amber: function (item) { return item.decision === "amber" && item.freshness.status === "fresh"; },
      red: function (item) { return item.decision === "red"; },
      stale: function (item) { return ["stale", "drifted"].indexOf(item.freshness.status) >= 0; },
      expired: function (item) { return item.lifecycle_status === "superseded"; },
      generating: function (item) { return ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0; }
    };
    return allRows.find(predicates[variant]);
  }

  function field(label, value) {
    return '<div class="gate-fact"><span class="fact-label">' + ui.escapeHtml(label) + '</span><strong>' + ui.escapeHtml(value) + "</strong></div>";
  }

  function renderContext(twin) {
    contextHost.innerHTML = '<p class="eyebrow">Bundle Execution</p><h2>Execution Context</h2><div class="field"><label>Bundle Source</label><select><option>Digital Twin fixture</option></select></div><div class="field"><label>Selected Twin</label><select id="gate-twin-select">' + allRows.map(function (item) { return '<option value="' + ui.escapeHtml(item.twin_id) + '"' + (item.twin_id === twin.twin_id ? " selected" : "") + '>' + ui.escapeHtml(item.display_name) + "</option>"; }).join("") + '</select></div><div class="content-block"><span class="fact-label">Bundle Metadata</span><p class="muted">' + ui.escapeHtml(twin.bundle.bundle_name) + '<br>Release: ' + ui.escapeHtml(twin.bundle.release_version) + '<br>SHA-256: ' + ui.escapeHtml(twin.bundle.bundle_hash.slice(0, 18)) + '...</p></div><div class="field"><label>Target Namespace</label><input value="' + ui.escapeHtml(twin.target.namespace) + '" readonly></div><div class="field"><label>Execution Mode</label><select><option>Approved mutation</option></select></div><div class="field"><label>Approval Rationale</label><textarea>Reviewed Digital Twin decision and evidence.</textarea></div><button class="btn primary" type="button" data-gate-action="prepare">Prepare Execution</button><p class="compact-footer">Browser-only Twin Gate · no server interaction</p>';
  }

  function renderGate(gate) {
    current = gate.twin;
    var variant = variantFor(current);
    var headline = gateHeadline(variant);
    var approval = gate.approval || "not_required";
    var actions = '<a class="btn" href="' + ui.detailHref(current.twin_id, "overview") + '">View Full Twin</a><button class="btn" type="button" data-gate-action="regenerate">Regenerate</button>';
    if (variant === "amber" && approval === "required") actions += '<button class="btn primary" type="button" data-gate-action="request-approval">Request Approval</button>';
    if (variant === "amber" && approval === "pending") actions += '<button class="btn primary" type="button" data-gate-action="approve">Approve</button><button class="btn" type="button" data-gate-action="reject">Reject</button>';
    actions += '<button class="btn primary" type="button" data-gate-action="start"' + ((variant === "green" || (variant === "amber" && approval === "approved")) ? "" : " disabled title=\"Current gate is not execution eligible.\"") + ">Start Execution</button>";

    gateHost.innerHTML = '<div class="tab-intro"><div><p class="eyebrow">Immutable Release Safety Check</p><h2>Digital Twin Gate</h2><p>Compact eligibility for one target, bundle checksum, and decision version.</p></div><a class="btn" href="digital-twins.html">All Twins</a></div><div class="variant-switcher" aria-label="Browser fixture gate variants"><button class="btn" type="button" data-gate-fixture="green">Green</button><button class="btn" type="button" data-gate-fixture="amber">Amber</button><button class="btn" type="button" data-gate-fixture="red">Red</button><button class="btn" type="button" data-gate-fixture="stale">Stale</button><button class="btn" type="button" data-gate-fixture="expired">Expired</button><button class="btn" type="button" data-gate-fixture="generating">Generating</button></div><div class="gate-hero"><div class="sphere-stage"><canvas data-sphere aria-label="Animated Digital Twin sphere"></canvas></div><div class="gate-decision">' + ui.badge(variant) + '<h1>' + ui.escapeHtml(headline[0]) + '</h1><p>' + ui.escapeHtml(headline[1]) + '</p></div></div><div class="gate-matrix">' + field("Twin ID", current.twin_id) + field("Risk", current.risk.score == null ? "Not calculated" : current.risk.score + " · " + ui.label(current.risk.level)) + field("Policy", ui.label(gate.policy)) + field("Evidence", ui.label(gate.evidence)) + field("Freshness", ui.label(gate.freshness.status)) + field("Dry-run", ui.label(gate.dry_run)) + field("Rollback", ui.label(gate.rollback)) + field("Drift", ui.label(gate.drift)) + field("Approval", ui.label(approval)) + '</div><ol class="reason-list">' + (gate.reasons.length ? gate.reasons.map(function (reason) { return '<li><strong>' + ui.escapeHtml(reason.code) + '</strong><br>' + ui.escapeHtml(reason.summary) + "</li>"; }).join("") : '<li><strong>No additional decision reasons.</strong><br>The fixture gate is driven by the final deterministic state.</li>') + '</ol><div class="action-row">' + actions + "</div>";

    resultHost.innerHTML = '<p class="eyebrow">Execution Result</p><h2>Eligibility Summary</h2><div class="inline-actions">' + ui.badge(variant) + ui.badge(gate.dry_run) + '</div><div class="log-view">' + ui.escapeHtml(JSON.stringify({ decision: current.decision, twin_id: current.twin_id, decision_version: current.decision_version, target: current.target, bundle_hash: current.bundle.bundle_hash, risk: current.risk, policy: gate.policy, evidence: gate.evidence, freshness: gate.freshness.status, dry_run: gate.dry_run, rollback: gate.rollback, drift: gate.drift, approval: approval }, null, 2)) + '</div><div class="action-row"><button class="btn" type="button" data-gate-action="copy">Copy Summary</button><button class="btn" type="button" data-gate-action="download">Download JSON</button></div>';

    var canvas = gateHost.querySelector("canvas[data-sphere]");
    if (canvas && window.ESDAPrototype && window.ESDAPrototype.drawSphere) window.ESDAPrototype.drawSphere(canvas);
    renderContext(current);
  }

  function loadTwin(twinId, replace) {
    gateHost.innerHTML = ui.stateView("loading", "Loading Twin Gate", "Reading the selected decision through TwinDataAdapter.", false);
    return adapter.getGate(twinId).then(function (gate) {
      ui.updateUrl({ twin_id: twinId }, replace);
      renderGate(gate);
    }).catch(function (error) {
      gateHost.innerHTML = ui.stateView("failed", "Twin Gate unavailable", error.message, error.retryable);
    });
  }

  function noSelection() {
    current = null;
    contextHost.innerHTML = ui.stateView("empty", "No execution context selected", "Choose a browser fixture to inspect its immutable Twin Gate.", false);
    gateHost.innerHTML = ui.stateView("empty", "No Digital Twin selected", "Open Bundle Execution from a Digital Twin row or choose a browser fixture below.", false) + '<div class="variant-switcher">' + ["green", "amber", "red", "stale", "expired", "generating"].map(function (variant) { return '<button class="btn" type="button" data-gate-fixture="' + variant + '">' + ui.label(variant) + "</button>"; }).join("") + "</div>";
    resultHost.innerHTML = '<p class="eyebrow">Execution Result</p><h2>No selection</h2><div class="log-view">Select a Digital Twin before preparing Bundle Execution.</div>';
  }

  document.addEventListener("change", function (event) {
    if (event.target.id === "gate-twin-select") loadTwin(event.target.value, false);
  });

  document.addEventListener("click", function (event) {
    var fixture = event.target.closest("[data-gate-fixture]");
    if (fixture) {
      var match = selectForVariant(fixture.getAttribute("data-gate-fixture"));
      if (match) loadTwin(match.twin_id, false);
      else ui.showToast("No fixture currently matches that gate variant.", "info");
      return;
    }
    var action = event.target.closest("[data-gate-action]");
    if (!action || !current) return;
    var code = action.getAttribute("data-gate-action");
    if (code === "regenerate") adapter.regenerate(current.twin_id).then(function (next) { loadTwin(next.twin_id, false); });
    if (code === "request-approval") adapter.requestApproval(current.twin_id).then(function () { ui.showToast("Browser fixture approval requested.", "success"); return loadTwin(current.twin_id, true); });
    if (code === "approve") adapter.approveTwin(current.twin_id).then(function () { ui.showToast("Browser fixture approval accepted.", "success"); return loadTwin(current.twin_id, true); });
    if (code === "reject") adapter.rejectTwin(current.twin_id).then(function () { ui.showToast("Browser fixture approval rejected.", "info"); return loadTwin(current.twin_id, true); });
    if (code === "start" || code === "prepare") ui.showModal({ eyebrow: "Browser Mock", title: "Execution handoff prepared", body: '<div class="notice green">The interaction is complete. No server or Kubernetes operation was performed.</div>' });
    if (code === "copy") ui.copyText(resultHost.querySelector(".log-view").textContent, "Gate summary copied.");
    if (code === "download") ui.mockDownload(current.twin_id + "-gate.json", current);
  });

  window.addEventListener("popstate", function () {
    var twinId = ui.params().get("twin_id");
    if (twinId) loadTwin(twinId, true); else noSelection();
  });

  adapter.listTwins({ limit: 100, mock_state: "success" }).then(function (response) {
    allRows = response.items;
    if (selectedId) return loadTwin(selectedId, true);
    return adapter.getActiveTwin().then(function (active) { if (active) return loadTwin(active.twin_id, true); noSelection(); });
  });
})();
