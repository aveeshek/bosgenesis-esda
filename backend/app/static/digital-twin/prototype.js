(function () {
  "use strict";

  function selectAll(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function setupMobileNavigation() {
    var trigger = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector("[data-main-nav]");
    if (!trigger || !nav) return;

    trigger.addEventListener("click", function () {
      var isOpen = nav.classList.toggle("is-open");
      trigger.setAttribute("aria-expanded", String(isOpen));
    });
  }

  function setupProfileMenu() {
    var trigger = document.querySelector("[data-profile-toggle]");
    var menu = document.querySelector("[data-profile-menu]");
    if (!trigger || !menu) return;

    function closeMenu() {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }

    trigger.addEventListener("click", function (event) {
      event.stopPropagation();
      menu.hidden = !menu.hidden;
      trigger.setAttribute("aria-expanded", String(!menu.hidden));
    });

    document.addEventListener("click", function (event) {
      if (!menu.contains(event.target) && event.target !== trigger) closeMenu();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeMenu();
    });
  }

  function setupTabs() {
    var tabList = document.querySelector("[role='tablist']");
    if (!tabList) return;
    var tabs = selectAll("[role='tab']", tabList);
    var panels = selectAll("[role='tabpanel']");

    function activate(tab, moveFocus) {
      tabs.forEach(function (item) {
        var selected = item === tab;
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
      });
      panels.forEach(function (panel) {
        panel.hidden = panel.id !== tab.getAttribute("aria-controls");
      });
      if (moveFocus) tab.focus();
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { activate(tab, false); });
      tab.addEventListener("keydown", function (event) {
        var nextIndex = null;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = tabs.length - 1;
        if (nextIndex !== null) {
          event.preventDefault();
          activate(tabs[nextIndex], true);
          tabs[nextIndex].scrollIntoView({ block: "nearest", inline: "nearest" });
        }
      });
    });
  }

  function setupDrawers() {
    selectAll("[data-drawer-toggle]").forEach(function (trigger) {
      var drawer = document.getElementById(trigger.getAttribute("aria-controls"));
      if (!drawer) return;
      trigger.addEventListener("click", function () {
        drawer.hidden = !drawer.hidden;
        trigger.setAttribute("aria-expanded", String(!drawer.hidden));
      });
    });

    selectAll("[data-drawer-close]").forEach(function (trigger) {
      trigger.addEventListener("click", function () {
        var drawer = trigger.closest(".drawer");
        if (!drawer) return;
        drawer.hidden = true;
        var opener = document.querySelector("[aria-controls='" + drawer.id + "']");
        if (opener) {
          opener.setAttribute("aria-expanded", "false");
          opener.focus();
        }
      });
    });
  }

  function setupGateVariants() {
    var controls = selectAll("[data-gate-variant]");
    var variants = selectAll("[data-gate-panel]");
    if (!controls.length || !variants.length) return;

    controls.forEach(function (control) {
      control.addEventListener("click", function () {
        var state = control.getAttribute("data-gate-variant");
        controls.forEach(function (item) {
          item.setAttribute("aria-pressed", String(item === control));
        });
        variants.forEach(function (panel) {
          panel.hidden = panel.getAttribute("data-gate-panel") !== state;
        });
        window.dispatchEvent(new Event("resize"));
      });
    });
  }

  function drawSphere(canvas) {
    var context = canvas.getContext("2d");
    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var phase = 0;
    var frame = 0;

    function sizeCanvas() {
      var rect = canvas.getBoundingClientRect();
      var ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(rect.width * ratio));
      canvas.height = Math.max(1, Math.round(rect.height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function project(latitude, longitude, radius, cx, cy, rotation) {
      var x = Math.cos(latitude) * Math.cos(longitude + rotation);
      var y = Math.sin(latitude);
      var z = Math.cos(latitude) * Math.sin(longitude + rotation);
      var tilt = 0.22;
      var tiltedY = y * Math.cos(tilt) - z * Math.sin(tilt);
      var depth = y * Math.sin(tilt) + z * Math.cos(tilt);
      var scale = 0.90 + depth * 0.08;
      return {
        x: cx + x * radius * scale,
        y: cy + tiltedY * radius * scale,
        depth: depth
      };
    }

    function strokePath(points, alpha, width, color) {
      if (points.length < 2) return;
      context.beginPath();
      context.moveTo(points[0].x, points[0].y);
      for (var i = 1; i < points.length; i += 1) {
        context.lineTo(points[i].x, points[i].y);
      }
      context.lineWidth = width;
      context.strokeStyle = color.replace("ALPHA", String(alpha));
      context.stroke();
    }

    function render() {
      var width = canvas.clientWidth;
      var height = canvas.clientHeight;
      if (!width || !height) return;
      context.clearRect(0, 0, width, height);

      var cx = width / 2;
      var cy = height / 2;
      var radius = Math.min(width, height) * 0.34;

      var halo = context.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius * 1.3);
      halo.addColorStop(0, "rgba(255, 121, 120, 0.18)");
      halo.addColorStop(0.67, "rgba(255, 113, 123, 0.08)");
      halo.addColorStop(1, "rgba(93, 151, 255, 0)");
      context.fillStyle = halo;
      context.beginPath();
      context.arc(cx, cy, radius * 1.35, 0, Math.PI * 2);
      context.fill();

      context.save();
      context.translate(cx, cy);
      context.rotate(-0.25);
      context.scale(1.12, 0.46);
      context.beginPath();
      context.arc(0, 0, radius * 1.12, 0, Math.PI * 2);
      context.strokeStyle = "rgba(255, 215, 211, 0.33)";
      context.lineWidth = 1;
      context.stroke();
      context.restore();

      for (var latIndex = -8; latIndex <= 8; latIndex += 1) {
        var latitude = latIndex * Math.PI / 18;
        var latitudePoints = [];
        for (var lonIndex = 0; lonIndex <= 80; lonIndex += 1) {
          latitudePoints.push(project(latitude, lonIndex * Math.PI / 40, radius, cx, cy, phase));
        }
        strokePath(latitudePoints, 0.56, 0.72, "rgba(255, 199, 196, ALPHA)");
      }

      for (var meridian = 0; meridian < 30; meridian += 1) {
        var longitude = meridian * Math.PI / 15;
        var longitudePoints = [];
        for (var latStep = -40; latStep <= 40; latStep += 1) {
          longitudePoints.push(project(latStep * Math.PI / 80, longitude, radius, cx, cy, phase));
        }
        strokePath(longitudePoints, 0.47, 0.66, "rgba(255, 154, 164, ALPHA)");
      }

      context.beginPath();
      context.arc(cx, cy, radius, 0, Math.PI * 2);
      context.strokeStyle = "rgba(255, 225, 214, 0.72)";
      context.lineWidth = 1.3;
      context.stroke();

      var labels = ["API", "POD", "SVC", "POL", "MOP", "RBAC"];
      context.font = "700 9px Inter, sans-serif";
      context.fillStyle = "rgba(255, 238, 243, 0.72)";
      labels.forEach(function (label, index) {
        var angle = phase * 0.6 + index * Math.PI / 3;
        context.fillText(label, cx + Math.cos(angle) * radius * 1.18 - 10, cy + Math.sin(angle) * radius * 0.72);
      });

      if (!reducedMotion) phase += 0.0035;
    }

    function animate() {
      render();
      if (!reducedMotion) frame = window.requestAnimationFrame(animate);
    }

    sizeCanvas();
    animate();
    window.addEventListener("resize", function () {
      if (frame) window.cancelAnimationFrame(frame);
      sizeCanvas();
      animate();
    });
  }

  function setupSpheres() {
    selectAll("canvas[data-sphere]").forEach(drawSphere);
  }

  window.ESDAPrototype = {
    drawSphere: drawSphere,
    setupSpheres: setupSpheres
  };

  setupMobileNavigation();
  setupProfileMenu();
  setupTabs();
  setupDrawers();
  setupGateVariants();
  setupSpheres();
})();
