(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function params() {
    return new URLSearchParams(window.location.search);
  }

  function isServerHosted() {
    try {
      return Boolean(
        window.frameElement &&
        ["mock_server", "real_core"].indexOf(window.frameElement.dataset.adapterMode) >= 0
      );
    } catch (error) {
      return false;
    }
  }

  function updateUrl(updates, replace) {
    var search = params();
    Object.keys(updates).forEach(function (key) {
      var value = updates[key];
      if (value == null || value === "" || value === "all") search.delete(key);
      else search.set(key, value);
    });
    var base = window.location.href.split(/[?#]/)[0];
    var next = base + (search.toString() ? "?" + search.toString() : "") + window.location.hash;
    try {
      window.history[replace ? "replaceState" : "pushState"]({}, "", next);
      if (isServerHosted() && window.parent && window.parent !== window) {
        var hostPath = window.frameElement.dataset.hostPath || "/digital-twins";
        window.parent.history[replace ? "replaceState" : "pushState"]({}, "", hostPath + (search.toString() ? "?" + search.toString() : ""));
      }
    } catch (error) {
      window.location.href = next;
    }
  }

  function formatDate(value) {
    if (!value) return "Not available";
    var date = new Date(value);
    return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function label(value) {
    return String(value || "not_available").replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
  }

  function badgeClass(value) {
    var normalized = String(value || "").toLowerCase();
    if (["green", "fresh", "passed", "approved", "completed", "eligible"].indexOf(normalized) >= 0) return "green";
    if (["amber", "stale", "drifted", "review", "required", "pending", "expired"].indexOf(normalized) >= 0) return "amber";
    if (["red", "failed", "blocked", "rejected", "critical", "cancelled"].indexOf(normalized) >= 0) return "red";
    if (["superseded"].indexOf(normalized) >= 0) return "superseded";
    if (["generating", "requested", "awaiting_dry_run", "decision_calculating", "loading"].indexOf(normalized) >= 0) return "generating";
    return "info";
  }

  function badge(value, overrideLabel) {
    return '<span class="badge ' + badgeClass(value) + '">' + escapeHtml(overrideLabel || label(value)) + "</span>";
  }

  function stateView(state, title, message, retryable) {
    var icon = state === "loading" ? "..." : state === "failed" ? "x" : state === "empty" ? "0" : "i";
    return '<div class="mock-state-view ' + escapeHtml(state) + '"><span class="mock-state-icon" aria-hidden="true">' + icon + '</span><h3>' + escapeHtml(title) + '</h3><p>' + escapeHtml(message) + '</p>' + (retryable ? '<button class="btn" type="button" data-retry>Retry</button>' : "") + "</div>";
  }

  function loadingRows(columns, rows) {
    var output = [];
    for (var row = 0; row < (rows || 5); row += 1) {
      output.push("<tr>");
      for (var column = 0; column < columns; column += 1) output.push('<td><div class="skeleton-line"></div></td>');
      output.push("</tr>");
    }
    return output.join("");
  }

  function showToast(message, tone) {
    var host = document.querySelector("[data-toast-host]");
    if (!host) {
      host = document.createElement("div");
      host.className = "toast-host";
      host.setAttribute("data-toast-host", "");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    var toast = document.createElement("div");
    toast.className = "app-toast " + (tone || "info");
    toast.textContent = message;
    host.appendChild(toast);
    window.setTimeout(function () { toast.remove(); }, 3600);
  }

  function showModal(options) {
    var existing = document.querySelector("[data-mock-modal]");
    if (existing) existing.remove();
    var shell = document.createElement("div");
    shell.className = "mock-modal-shell";
    shell.setAttribute("data-mock-modal", "");
    shell.innerHTML = '<section class="mock-modal" role="dialog" aria-modal="true" aria-labelledby="mock-modal-title"><div class="mock-modal-header"><div><p class="eyebrow">' + escapeHtml(options.eyebrow || "Evidence") + '</p><h2 id="mock-modal-title">' + escapeHtml(options.title) + '</h2></div><button class="btn icon-only" type="button" data-modal-close aria-label="Close dialog">x</button></div><div class="mock-modal-body">' + options.body + '</div><div class="mock-modal-actions">' + (options.actions || '<button class="btn primary" type="button" data-modal-close>Done</button>') + "</div></section>";
    document.body.appendChild(shell);
    var closeButtons = shell.querySelectorAll("[data-modal-close]");
    function close() { shell.remove(); }
    closeButtons.forEach(function (button) { button.addEventListener("click", close); });
    shell.addEventListener("click", function (event) { if (event.target === shell) close(); });
    document.addEventListener("keydown", function escapeOnce(event) { if (event.key === "Escape") { close(); document.removeEventListener("keydown", escapeOnce); } });
    var closeButton = shell.querySelector("[data-modal-close]");
    if (closeButton) closeButton.focus();
    return shell;
  }

  function mockDownload(filename, value, mimeType) {
    var body = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    var blob = new Blob([body], { type: mimeType || "application/json" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function copyText(value, successMessage) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value).then(function () { showToast(successMessage || "Copied.", "success"); });
    }
    var area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    showToast(successMessage || "Copied.", "success");
    return Promise.resolve();
  }

  function detailHref(twinId, tab, extras) {
    var query = new URLSearchParams();
    query.set("twin_id", twinId);
    if (tab) query.set("tab", tab);
    Object.keys(extras || {}).forEach(function (key) { if (extras[key]) query.set(key, extras[key]); });
    if (isServerHosted()) {
      query.delete("twin_id");
      return "/digital-twins/" + encodeURIComponent(twinId) + (query.toString() ? "?" + query.toString() : "");
    }
    return "digital-twin-detail.html?" + query.toString();
  }

  function navigate(href) {
    if (isServerHosted() && window.parent && window.parent !== window) window.parent.location.href = href;
    else window.location.href = href;
  }

  window.ESDATwinUI = {
    escapeHtml: escapeHtml,
    params: params,
    updateUrl: updateUrl,
    formatDate: formatDate,
    label: label,
    badgeClass: badgeClass,
    badge: badge,
    stateView: stateView,
    loadingRows: loadingRows,
    showToast: showToast,
    showModal: showModal,
    mockDownload: mockDownload,
    copyText: copyText,
    detailHref: detailHref,
    navigate: navigate,
    isServerHosted: isServerHosted
  };
})();
