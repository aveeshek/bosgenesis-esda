(function () {
  "use strict";

  var TwinDataAdapter = window.ESDATwinData && window.ESDATwinData.TwinDataAdapter;
  if (!TwinDataAdapter) return;

  function HttpTwinAdapterError(message, options) {
    options = options || {};
    this.name = "HttpTwinAdapterError";
    this.message = message;
    this.status = options.status || 0;
    this.code = options.code || "request_failed";
    this.retryable = Boolean(options.retryable);
    this.details = options.details || {};
  }
  HttpTwinAdapterError.prototype = Object.create(Error.prototype);

  function adapterMode() {
    try {
      return window.frameElement && window.frameElement.dataset.adapterMode;
    } catch (error) {
      return null;
    }
  }

  function selectedModelProfile() {
    try {
      var parentDocument = window.parent && window.parent.document;
      var selector = parentDocument && parentDocument.getElementById("model_profile");
      return selector ? selector.value : "azure_gpt5_pro";
    } catch (error) {
      return "azure_gpt5_pro";
    }
  }


  function HttpTwinAdapter(options) {
    TwinDataAdapter.call(this);
    options = options || {};
    this.baseUrl = options.baseUrl || "/api/digital-twins";
    this.responseMode = "success";
    this.controllers = new Map();
    this.maxGetRetries = 1;
  }

  HttpTwinAdapter.prototype = Object.create(TwinDataAdapter.prototype);
  HttpTwinAdapter.prototype.constructor = HttpTwinAdapter;

  HttpTwinAdapter.prototype._cancel = function (key) {
    var current = this.controllers.get(key);
    if (current) current.abort();
    this.controllers.delete(key);
  };

  HttpTwinAdapter.prototype._request = function (method, path, options) {
    var self = this;
    options = options || {};
    var key = options.key || method + ":" + path;
    this._cancel(key);
    var controller = new AbortController();
    this.controllers.set(key, controller);
    var query = new URLSearchParams();
    Object.keys(options.query || {}).forEach(function (name) {
      var value = options.query[name];
      if (value != null && value !== "" && value !== "all") query.set(name, value);
    });
    var url = this.baseUrl + path + (query.toString() ? "?" + query.toString() : "");
    var requestOptions = {
      method: method,
      credentials: "same-origin",
      signal: controller.signal,
      headers: { Accept: "application/json" }
    };
    if (options.body != null) {
      requestOptions.headers["Content-Type"] = "application/json";
      requestOptions.body = JSON.stringify(options.body);
    }

    function execute(attempt) {
      return window.fetch(url, requestOptions).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (response.ok) return payload;
          var error = payload.error || payload.detail || {};
          var retryable = Boolean(error.retryable) || [408, 429, 502, 503, 504].indexOf(response.status) >= 0;
          if (method === "GET" && retryable && attempt < self.maxGetRetries) {
            return new Promise(function (resolve) { window.setTimeout(resolve, 180); }).then(function () { return execute(attempt + 1); });
          }
          var statusMessage = {
            401: "Your ESDA session expired. Sign in and retry.",
            403: "You are not authorized to read this Digital Twin.",
            404: "The requested Digital Twin or evidence module was not found.",
            409: "The Digital Twin changed before this action completed. Refresh and retry.",
            504: "The Digital Twin gateway timed out. Retry the safe read."
          }[response.status];
          throw new HttpTwinAdapterError(error.message || statusMessage || "Digital Twin request failed.", {
            status: response.status,
            code: error.code,
            retryable: retryable,
            details: error.details
          });
        });
      }).catch(function (error) {
        if (error && error.name === "AbortError") throw error;
        if (error instanceof HttpTwinAdapterError) throw error;
        if (method === "GET" && attempt < self.maxGetRetries) {
          return new Promise(function (resolve) { window.setTimeout(resolve, 180); }).then(function () { return execute(attempt + 1); });
        }
        throw new HttpTwinAdapterError("The Digital Twin gateway is unreachable.", { code: "network_error", retryable: true });
      });
    }

    return execute(0).finally(function () {
      if (self.controllers.get(key) === controller) self.controllers.delete(key);
    });
  };

  HttpTwinAdapter.prototype.setResponseMode = function (mode) { this.responseMode = mode || "success"; return this.responseMode; };
  HttpTwinAdapter.prototype.getResponseMode = function () { return this.responseMode; };
  HttpTwinAdapter.prototype.listScenarios = function () { return this._request("GET", "/scenarios", { key: "scenarios" }); };
  HttpTwinAdapter.prototype.listTwins = function (query) {
    query = Object.assign({}, query || {});
    var mapped = {
      q: query.search,
      decision: query.decision,
      lifecycle: query.lifecycle,
      freshness: query.freshness,
      namespace: query.target,
      bundle: query.bundle,
      created_by: query.creator,
      linked_execution: query.linked_execution,
      sort: query.sort,
      direction: query.direction,
      cursor: query.cursor,
      limit: query.limit,
      mock_state: query.mock_state || this.responseMode
    };
    if (query.date) { mapped.created_from = query.date; mapped.created_to = query.date; }
    return this._request("GET", "", { query: mapped, key: "list" });
  };
  HttpTwinAdapter.prototype.getTwin = function (twinId) { return this._request("GET", "/" + encodeURIComponent(twinId), { key: "twin:" + twinId }); };
  HttpTwinAdapter.prototype.getActiveTwin = function () { return this._request("GET", "/active", { key: "active" }); };
  HttpTwinAdapter.prototype.getTab = function (twinId, slug, decisionVersion, query) {
    query = Object.assign({}, query || {}, {
      decision_version: decisionVersion,
      model_profile: selectedModelProfile()
    });
    return this._request(
      "GET",
      "/" + encodeURIComponent(twinId) + "/tabs/" + encodeURIComponent(slug),
      {
        query: query,
        key: "tab"
      });
  };
  HttpTwinAdapter.prototype.getActions = function (twinId) { return this._request("GET", "/" + encodeURIComponent(twinId) + "/actions", { key: "actions:" + twinId }); };
  HttpTwinAdapter.prototype.startGeneration = function (scenarioId) { return this._request("POST", "", { body: { scenario_id: scenarioId }, key: "generate" }); };
  HttpTwinAdapter.prototype.advanceGeneration = function (twinId) { return this._request("POST", "/" + encodeURIComponent(twinId) + "/advance", { key: "advance:" + twinId }); };
  HttpTwinAdapter.prototype.regenerate = function (twinId) { return this._request("POST", "/" + encodeURIComponent(twinId) + "/regenerate", { key: "regenerate:" + twinId }); };
  HttpTwinAdapter.prototype.cancelGeneration = function (twinId) { return this._request("POST", "/" + encodeURIComponent(twinId) + "/cancel", { key: "cancel:" + twinId }); };
  HttpTwinAdapter.prototype.requestApproval = function (twinId) { return this._request("POST", "/" + encodeURIComponent(twinId) + "/approval/request", { body: {}, key: "approval:" + twinId }); };
  HttpTwinAdapter.prototype.approveTwin = function (twinId) { return this._request("POST", "/" + encodeURIComponent(twinId) + "/approval/approve", { body: {}, key: "approval:" + twinId }); };
  HttpTwinAdapter.prototype.rejectTwin = function (twinId) { return this._request("POST", "/" + encodeURIComponent(twinId) + "/approval/reject", { body: {}, key: "approval:" + twinId }); };
  HttpTwinAdapter.prototype.getGate = function (twinId) { return this._request("GET", "/" + encodeURIComponent(twinId) + "/gate", { key: "gate:" + twinId }); };
  HttpTwinAdapter.prototype.getMockHistory = function () { return this._request("GET", "/history", { key: "history" }); };
  HttpTwinAdapter.prototype.clearMockHistory = function () { return this._request("DELETE", "/history", { key: "history" }); };
  HttpTwinAdapter.prototype.invalidateCache = function (twinId) {
    var self = this;
    Array.from(this.controllers.keys()).forEach(function (key) { if (key.indexOf(twinId) >= 0 || key === "tab") self._cancel(key); });
  };

  window.ESDATwinData.HttpTwinAdapter = HttpTwinAdapter;
  window.ESDATwinData.HttpTwinAdapterError = HttpTwinAdapterError;
  if (["mock_server", "real_core"].indexOf(adapterMode()) >= 0) {
    window.esdaTwinAdapter = new HttpTwinAdapter({ baseUrl: "/api/digital-twins" });
  }
})();
