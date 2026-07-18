(function () {
  "use strict";

  var fixtures = window.ESDA_TWIN_FIXTURES_V1;
  var HISTORY_KEY = "esda.digital-twin.mock-history.v1";
  var ACTIVE_KEY = "esda.digital-twin.active-run.v1";
  var RESPONSE_MODE_KEY = "esda.digital-twin.response-mode.v1";

  function MockAdapterError(message, retryable) {
    this.name = "MockAdapterError";
    this.message = message;
    this.retryable = retryable !== false;
  }
  MockAdapterError.prototype = Object.create(Error.prototype);
  function mockAction(code, label, confirmation) {
    return {
      code: code,
      label: label,
      enabled: true,
      visible: true,
      method: code === "open_twin" ? "GET" : "POST",
      href: null,
      reason_code: "eligible",
      disabled_reason: null,
      requires_confirmation: Boolean(confirmation)
    };
  }

  function TwinDataAdapter() {}

  [
    "listScenarios",
    "listTwins",
    "getTwin",
    "getActiveTwin",
    "getTab",
    "getActions",
    "refreshDrift",
    "startGeneration",
    "advanceGeneration",
    "regenerate",
    "cancelGeneration",
    "requestApproval",
    "approveTwin",
    "rejectTwin",
    "getGate",
    "getMockHistory",
    "clearMockHistory",
    "invalidateCache"
  ].forEach(function (method) {
    TwinDataAdapter.prototype[method] = function () {
      return Promise.reject(new Error(method + " must be implemented by a TwinDataAdapter."));
    };
  });

  function safeStorageGet(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      return false;
    }
    return true;
  }

  function safeStorageRemove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (error) {
      return false;
    }
    return true;
  }

  function BrowserFixtureTwinAdapter(options) {
    TwinDataAdapter.call(this);
    this.fixtureVersion = fixtures.version;
    this.latencyMs = options && Number.isFinite(options.latencyMs) ? options.latencyMs : 320;
    this.pageSize = options && options.pageSize ? options.pageSize : 6;
    this.tabCache = new Map();
    this.responseMode = safeStorageGet(RESPONSE_MODE_KEY, "success");
  }

  BrowserFixtureTwinAdapter.prototype = Object.create(TwinDataAdapter.prototype);
  BrowserFixtureTwinAdapter.prototype.constructor = BrowserFixtureTwinAdapter;

  BrowserFixtureTwinAdapter.prototype._delay = function (value, multiplier) {
    var duration = Math.max(0, Math.round(this.latencyMs * (multiplier == null ? 1 : multiplier)));
    return new Promise(function (resolve) {
      window.setTimeout(function () { resolve(fixtures.clone(value)); }, duration);
    });
  };

  BrowserFixtureTwinAdapter.prototype._history = function () {
    return safeStorageGet(HISTORY_KEY, []);
  };

  BrowserFixtureTwinAdapter.prototype._writeHistory = function (history) {
    safeStorageSet(HISTORY_KEY, history.slice(0, 40));
  };

  BrowserFixtureTwinAdapter.prototype._allTwins = function () {
    var byId = {};
    fixtures.twins.forEach(function (twin) { byId[twin.twin_id] = fixtures.clone(twin); });
    this._history().forEach(function (twin) { byId[twin.twin_id] = fixtures.clone(twin); });
    return Object.keys(byId).map(function (id) { return byId[id]; });
  };

  BrowserFixtureTwinAdapter.prototype._saveTwin = function (twin) {
    var history = this._history().filter(function (item) { return item.twin_id !== twin.twin_id; });
    history.unshift(fixtures.clone(twin));
    this._writeHistory(history);
    if (["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(twin.lifecycle_status) >= 0) {
      safeStorageSet(ACTIVE_KEY, { twin_id: twin.twin_id, selected_at: new Date().toISOString() });
    } else {
      var active = safeStorageGet(ACTIVE_KEY, null);
      if (active && active.twin_id === twin.twin_id) safeStorageRemove(ACTIVE_KEY);
    }
    return twin;
  };

  BrowserFixtureTwinAdapter.prototype.setResponseMode = function (mode) {
    if (fixtures.response_modes.indexOf(mode) < 0) mode = "success";
    this.responseMode = mode;
    safeStorageSet(RESPONSE_MODE_KEY, mode);
    return mode;
  };

  BrowserFixtureTwinAdapter.prototype.getResponseMode = function () {
    return this.responseMode;
  };

  BrowserFixtureTwinAdapter.prototype.listScenarios = function () {
    return this._delay(fixtures.scenarios, 0.2);
  };

  BrowserFixtureTwinAdapter.prototype.listTwins = function (query) {
    var self = this;
    query = query || {};
    var mode = query.mock_state || this.responseMode;
    if (mode === "failed") {
      return new Promise(function (_, reject) {
        window.setTimeout(function () { reject(new MockAdapterError("The fixture adapter simulated a retryable list failure.")); }, self.latencyMs);
      });
    }

    var items = this._allTwins();
    if (mode === "empty") items = [];
    var search = String(query.search || "").trim().toLowerCase();
    if (search) {
      items = items.filter(function (item) {
        return [item.twin_id, item.display_name, item.target.cluster_name, item.target.namespace, item.bundle.bundle_name, item.bundle.release_version, item.created_by_display]
          .join(" ").toLowerCase().indexOf(search) >= 0;
      });
    }

    function matches(value, expected) { return !expected || expected === "all" || value === expected; }
    items = items.filter(function (item) {
      var linked = item.relationships.execution_status !== "unlinked" ? "linked" : "unlinked";
      var createdDay = item.created_at.slice(0, 10);
      return matches(item.decision, query.decision)
        && matches(item.lifecycle_status, query.lifecycle)
        && matches(item.freshness.status, query.freshness)
        && matches(item.target.namespace, query.target)
        && (!query.bundle || query.bundle === "all" || item.bundle.bundle_name.indexOf(query.bundle) >= 0)
        && matches(item.created_by_display, query.creator)
        && (!query.date || createdDay === query.date)
        && matches(linked, query.linked_execution);
    });

    var sort = query.sort || "created_at";
    var direction = query.direction === "asc" ? 1 : -1;
    items.sort(function (left, right) {
      var leftValue = sort === "risk" ? left.risk.score : sort === "decision" ? left.decision : left[sort] || left.created_at;
      var rightValue = sort === "risk" ? right.risk.score : sort === "decision" ? right.decision : right[sort] || right.created_at;
      if (leftValue == null) return 1;
      if (rightValue == null) return -1;
      return leftValue > rightValue ? direction : leftValue < rightValue ? -direction : 0;
    });

    if (mode === "stale") {
      items = items.map(function (item) {
        item.freshness.status = "stale";
        item.freshness.message = "The fixture response was forced into a stale state.";
        return item;
      });
    }

    var metrics = { total: items.length, green: 0, amber: 0, red: 0, generating: 0, stale: 0, linked: 0 };
    items.forEach(function (item) {
      if (metrics[item.decision] != null) metrics[item.decision] += 1;
      if (["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(item.lifecycle_status) >= 0) metrics.generating += 1;
      if (["stale", "drifted", "expired"].indexOf(item.freshness.status) >= 0) metrics.stale += 1;
      if (item.relationships.execution_status !== "unlinked") metrics.linked += 1;
    });

    var resultCount = items.length;
    var limit = Number(query.limit) || this.pageSize;
    var offset = query.cursor ? Number(String(query.cursor).replace("cursor_", "")) || 0 : 0;
    var pageItems = items.slice(offset, offset + limit);
    var nextOffset = offset + pageItems.length;
    var response = {
      schema_version: fixtures.version,
      generated_at: fixtures.generated_at,
      items: pageItems,
      metrics: metrics,
      page: { limit: limit, has_more: nextOffset < resultCount, next_cursor: nextOffset < resultCount ? "cursor_" + nextOffset : null, previous_cursor: offset > 0 ? "cursor_" + Math.max(0, offset - limit) : null, result_count: resultCount, offset: offset },
      applied_query: fixtures.clone(query),
      partial: mode === "partial",
      warning: mode === "partial" ? "Runtime-behavior enrichment is unavailable; core decision rows are complete." : null
    };
    if (mode === "partial") response.items = pageItems.slice(0, Math.max(1, pageItems.length - 1));
    return this._delay(response);
  };

  BrowserFixtureTwinAdapter.prototype.getTwin = function (twinId) {
    var twin = this._allTwins().find(function (item) { return item.twin_id === twinId; });
    if (!twin) return Promise.reject(new MockAdapterError("Twin " + twinId + " was not found.", false));
    return this._delay(twin);
  };

  BrowserFixtureTwinAdapter.prototype.getActiveTwin = function () {
    var active = safeStorageGet(ACTIVE_KEY, null);
    if (!active) return this._delay(null, 0.1);
    var twin = this._allTwins().find(function (item) { return item.twin_id === active.twin_id; });
    if (!twin || ["requested", "generating", "awaiting_dry_run", "decision_calculating"].indexOf(twin.lifecycle_status) < 0) {
      safeStorageRemove(ACTIVE_KEY);
      return this._delay(null, 0.1);
    }
    return this._delay(twin, 0.1);
  };

  BrowserFixtureTwinAdapter.prototype.getTab = function (twinId, slug, decisionVersion) {
    var self = this;
    return this.getTwin(twinId).then(function (twin) {
      var cacheKey = [twinId, decisionVersion || twin.decision_version, slug].join(":");
      if (self.tabCache.has(cacheKey)) return self._delay(self.tabCache.get(cacheKey), 0.1);
      var tab = fixtures.tabFor(twin, slug);
      if (twin.lifecycle_status === "generating") {
        var availableCount = Math.min(fixtures.tab_slugs.length, Math.max(1, twin.progress_index + 1));
        if (fixtures.tab_slugs.indexOf(slug) >= availableCount) {
          tab.state = "loading";
          tab.summary = "This evidence module is waiting for an earlier generation stage.";
        }
      }
      self.tabCache.set(cacheKey, fixtures.clone(tab));
      return self._delay(tab, slug === "dependency-graph" ? 1.4 : 0.7);
    });
  };

  BrowserFixtureTwinAdapter.prototype.getActions = function (twinId) {
    return this.getTwin(twinId).then(function (twin) { return twin.actions; });
  };

  BrowserFixtureTwinAdapter.prototype.startGeneration = function (scenarioId) {
    var source = fixtures.twins.find(function (item) { return item.scenario_id === scenarioId; }) || fixtures.twins[0];
    var timestamp = Date.now();
    var twin = fixtures.clone(source);
    twin.twin_id = "twin_mock_" + timestamp;
    twin.display_name = "Mock generation - " + source.display_name;
    twin.decision_version = 1;
    twin.decision = "pending";
    twin.decision_is_final = false;
    twin.lifecycle_status = "requested";
    twin.visible_lifecycle = "requested";
    twin.risk = { level: "preliminary", score: null };
    twin.autonomy_eligibility = "not_available";
    twin.recommended_action = "Generation was requested. Final eligibility is not available.";
    twin.progress_index = 0;
    twin.created_at = new Date().toISOString();
    twin.updated_at = twin.created_at;
    twin.actions = [mockAction("open_twin", "Open Twin", false), mockAction("cancel_generation", "Cancel Generation", true)];
    this._saveTwin(twin);
    return this._delay(twin);
  };

  BrowserFixtureTwinAdapter.prototype.advanceGeneration = function (twinId) {
    var self = this;
    return this.getTwin(twinId).then(function (twin) {
      var states = fixtures.progress_states;
      var index = Math.min(states.length - 1, (twin.progress_index || 0) + 1);
      twin.progress_index = index;
      twin.lifecycle_status = states[index];
      twin.visible_lifecycle = states[index];
      twin.updated_at = new Date().toISOString();
      if (states[index] === "green") {
        twin.decision = "green";
        twin.decision_is_final = true;
        twin.risk = { level: "low", score: 18 };
        twin.autonomy_eligibility = "eligible";
        twin.recommended_action = "Proceed through normal execution controls.";
        twin.actions = fixtures.clone(fixtures.twins[0].actions);
      } else {
        twin.decision = "pending";
        twin.decision_is_final = false;
        twin.risk = { level: "preliminary", score: index * 11 || null };
      }
      self._saveTwin(twin);
      return self._delay(twin, 0.4);
    });
  };

  BrowserFixtureTwinAdapter.prototype.regenerate = function (twinId) {
    var self = this;
    return this.getTwin(twinId).then(function (prior) {
      var superseded = fixtures.clone(prior);
      superseded.decision = "superseded";
      superseded.lifecycle_status = "superseded";
      superseded.visible_lifecycle = "superseded";
      superseded.autonomy_eligibility = "superseded";
      superseded.freshness.status = "expired";
      var timestamp = Date.now();
      var next = fixtures.clone(prior);
      next.twin_id = "twin_mock_" + timestamp;
      next.decision_version = prior.decision_version + 1;
      next.decision = "pending";
      next.decision_is_final = false;
      next.lifecycle_status = "requested";
      next.visible_lifecycle = "requested";
      next.autonomy_eligibility = "not_available";
      next.risk = { level: "preliminary", score: null };
      next.progress_index = 0;
      next.prior_decision = { twin_id: prior.twin_id, decision: prior.decision, risk: prior.risk, decision_version: prior.decision_version };
      next.created_at = new Date().toISOString();
      next.updated_at = next.created_at;
      superseded.freshness.superseded_by = next.twin_id;
      self._saveTwin(superseded);
      self._saveTwin(next);
      self.invalidateCache(twinId);
      return self._delay(next);
    });
  };

  BrowserFixtureTwinAdapter.prototype.cancelGeneration = function (twinId) {
    var self = this;
    return this.getTwin(twinId).then(function (twin) {
      twin.lifecycle_status = "cancelled";
      twin.visible_lifecycle = "cancelled";
      twin.decision = "cancelled";
      twin.decision_is_final = false;
      twin.autonomy_eligibility = "not_available";
      twin.recommended_action = "Generation was cancelled before a final decision.";
      self._saveTwin(twin);
      return self._delay(twin);
    });
  };

  BrowserFixtureTwinAdapter.prototype._approvalUpdate = function (twinId, status) {
    var self = this;
    return this.getTwin(twinId).then(function (twin) {
      twin.relationships.approval_status = status;
      twin.relationships.approval_id = status === "approved" ? "approval_mock_" + Date.now() : null;
      if (status === "approved") {
        twin.autonomy_eligibility = "eligible_with_approval";
        twin.recommended_action = "Approval is valid for this decision version. Start Bundle Execution when ready.";
      }
      if (status === "rejected") {
        twin.autonomy_eligibility = "blocked_by_rejection";
        twin.recommended_action = "Approval was rejected. Regenerate after addressing the rationale.";
      }
      self._saveTwin(twin);
      return self._delay(twin);
    });
  };

  BrowserFixtureTwinAdapter.prototype.requestApproval = function (twinId) {
    return this._approvalUpdate(twinId, "pending");
  };
  BrowserFixtureTwinAdapter.prototype.approveTwin = function (twinId) {
    return this._approvalUpdate(twinId, "approved");
  };
  BrowserFixtureTwinAdapter.prototype.rejectTwin = function (twinId) {
    return this._approvalUpdate(twinId, "rejected");
  };

  BrowserFixtureTwinAdapter.prototype.getGate = function (twinId) {
    return this.getTwin(twinId).then(function (twin) {
      return {
        schema_version: fixtures.version,
        twin: twin,
        decision: twin.decision,
        risk: twin.risk,
        policy: twin.decision === "red" ? "blocked" : twin.decision === "amber" ? "review" : twin.decision === "green" ? "passed" : "pending",
        evidence: twin.decision_is_final ? "complete" : "collecting",
        freshness: twin.freshness,
        dry_run: twin.optional_states["dry-run"] || (twin.decision_is_final ? "passed" : "queued"),
        rollback: twin.decision === "green" ? "high" : twin.decision === "amber" ? "medium" : "not_available",
        drift: twin.freshness.status === "drifted" ? "material" : "none_material",
        reasons: twin.top_reasons,
        approval: twin.relationships.approval_status
      };
    });
  };

  BrowserFixtureTwinAdapter.prototype.getMockHistory = function () {
    return this._delay(this._history(), 0.2);
  };

  BrowserFixtureTwinAdapter.prototype.clearMockHistory = function () {
    safeStorageRemove(HISTORY_KEY);
    safeStorageRemove(ACTIVE_KEY);
    this.tabCache.clear();
    return this._delay({ cleared: true }, 0.2);
  };

  BrowserFixtureTwinAdapter.prototype.invalidateCache = function (twinId) {
    var prefix = twinId + ":";
    Array.from(this.tabCache.keys()).forEach(function (key) {
      if (key.indexOf(prefix) === 0) this.tabCache.delete(key);
    }, this);
  };

  window.ESDATwinData = {
    TwinDataAdapter: TwinDataAdapter,
    BrowserFixtureTwinAdapter: BrowserFixtureTwinAdapter,
    MockAdapterError: MockAdapterError,
    historyKey: HISTORY_KEY,
    activeKey: ACTIVE_KEY
  };
  window.esdaTwinAdapter = new BrowserFixtureTwinAdapter({ latencyMs: 320, pageSize: 6 });
})();
