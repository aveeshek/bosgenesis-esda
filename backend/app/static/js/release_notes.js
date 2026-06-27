const releaseNoteForm = document.getElementById("release-note-form");
const timeline = document.getElementById("timeline");
const timelineScroll = document.getElementById("timeline-scroll");
const statusBadge = document.getElementById("run-status");
const finalReport = document.getElementById("final-report");
const artifactLinks = document.getElementById("artifact-links");
const copyProgressButton = document.getElementById("copy-progress");
const copyProgressStatus = document.getElementById("copy-progress-status");
const progressPanel = document.getElementById("release-progress-panel");
const sphereCanvas = document.getElementById("release-sphere-canvas");
const spherePhase = document.getElementById("release-sphere-phase");
const sphereTitle = document.getElementById("release-sphere-title");
const transactionToggle = document.getElementById("transaction-sidebar-toggle");
const transactionSidebar = document.getElementById("transaction-sidebar");
const transactionClose = document.getElementById("transaction-sidebar-close");
const transactionBackdrop = document.getElementById("transaction-sidebar-backdrop");
const transactionList = document.getElementById("transaction-list");
const transactionStatus = document.getElementById("transaction-sidebar-status");
const activeRunStorageKey = "bosgenesis.releaseNotes.activeRunId";
const activityRailPinnedStorageKey = "bosgenesis.releaseNotes.activityRailPinned";
const activityRailAutoHideMs = 30000;
const ephemeralWorkingPanel = document.getElementById("ephemeral-working-panel");
const ephemeralWorkingStream = document.getElementById("ephemeral-working-stream");
const safeSummaryPanel = document.getElementById("safe-summary-panel");
const safeSummaryList = document.getElementById("safe-summary-list");
const agentActivityRail = document.getElementById("agent-activity-rail");
const agentActivityGraph = document.getElementById("agent-activity-graph");
const agentActivityStatus = document.getElementById("agent-activity-status");
const agentActivityToggle = document.getElementById("agent-activity-toggle");
const agentActivityPin = document.getElementById("agent-activity-pin");

let timelineEvents = [];
let activeRunId = null;
let currentEventSource = null;
let lastEventId = null;
let lastEventSequence = 0;
let transactionLoadFailed = false;
let workingNoteCounter = 0;
let workingNotePhases = new Set();
let safeSummaryItems = [];
let activityState = {};
let activityRailAutoHideTimer = null;
let activityRevealDelayTimer = null;
let activityRevealDelayUntil = 0;
let delayedActivityRevealReason = "";
let planningWarmupTimer = null;
let activityRailPinned = false;
const seenEventIds = new Set();
const safeSummaryKeys = new Set();

function valueOf(id) {
  const value = document.getElementById(id).value.trim();
  return value.length ? value : null;
}

function timelineText() {
  if (!timelineEvents.length) return "No progress events yet.";
  return timelineEvents.map((event, index) => {
    const payload = JSON.stringify(displayPayloadForTimeline(event), null, 2);
    return `${index + 1}. ${event.event_type}: ${event.message}\n${payload}`;
  }).join("\n\n");
}

function setCopyStatus(message) {
  if (!copyProgressStatus) return;
  copyProgressStatus.textContent = message;
}

function setProgressVisualState(state) {
  if (!progressPanel) return;
  const states = ["is-idle", "is-working", "is-complete", "is-failed"];
  progressPanel.classList.remove(...states);
  progressPanel.classList.add(`is-${state}`);

  const copy = {
    idle: {
      phase: "Ready for release analysis",
      title: "",
    },
    working: {
      phase: "Thinking through release evidence",
      title: "Release-note generation in progress.",
    },
    complete: {
      phase: "Release draft ready",
      title: "Review the generated release-note artifacts.",
    },
    failed: {
      phase: "Run needs review",
      title: "Release-note generation stopped before completion.",
    },
  }[state] || {};

  if (spherePhase && copy.phase) spherePhase.textContent = copy.phase;
  if (sphereTitle) sphereTitle.textContent = copy.title || "";
  scheduleSphereResize();
}

function scheduleSphereResize() {
  if (!window.releaseSphereRuntime?.resize) return;
  window.setTimeout(window.releaseSphereRuntime.resize, 80);
  window.setTimeout(window.releaseSphereRuntime.resize, 920);
}

async function initReleaseSphere() {
  if (!sphereCanvas || !progressPanel) return;
  const sphereStage = document.getElementById("release-sphere-stage");
  const sphereDock = document.getElementById("release-sphere-dock");
  if (!sphereStage || !sphereDock) return;

  try {
    const THREE = await import("https://unpkg.com/three@0.165.0/build/three.module.js");

    let running = true;
    let thinkingMix = 0;

    const renderer = new THREE.WebGLRenderer({
      canvas: sphereCanvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xffffff, 0);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    camera.position.set(0, 0.22, 7.6);

    const root = new THREE.Group();
    scene.add(root);

    const ambient = new THREE.AmbientLight(0xffffff, 0.72);
    scene.add(ambient);

    const keyLight = new THREE.PointLight(0xff9aa8, 1.75, 16);
    keyLight.position.set(3.4, 2.8, 5.4);
    scene.add(keyLight);

    const goldLight = new THREE.PointLight(0xf1c977, 1.15, 12);
    goldLight.position.set(-3.7, -1.6, 3.4);
    scene.add(goldLight);

    const vertexShader = `
      uniform float uTime;
      uniform float uTwist;
      uniform float uPulse;
      varying vec3 vNormal;
      varying vec3 vViewPosition;
      varying vec3 vWorldPosition;

      mat2 rotate2d(float a) {
        float s = sin(a);
        float c = cos(a);
        return mat2(c, -s, s, c);
      }

      void main() {
        vec3 p = position;

        float wave = sin(p.y * 3.4 + uTime * 2.2) * 0.18;
        float ripple = sin(length(p.xy) * 4.2 - uTime * 2.7) * 0.045;
        float twist = p.y * uTwist + sin(uTime + p.x * 1.8) * 0.35;

        p.xz = rotate2d(twist) * p.xz;
        p.xy = rotate2d(wave) * p.xy;
        p += normal * (ripple + sin(uTime * 1.7) * uPulse);

        vec4 worldPosition = modelMatrix * vec4(p, 1.0);
        vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);

        vNormal = normalize(normalMatrix * normal);
        vViewPosition = -mvPosition.xyz;
        vWorldPosition = worldPosition.xyz;

        gl_Position = projectionMatrix * mvPosition;
      }
    `;

    const fragmentShader = `
      uniform float uTime;
      uniform float uOpacity;
      varying vec3 vNormal;
      varying vec3 vViewPosition;
      varying vec3 vWorldPosition;

      void main() {
        vec3 normal = normalize(vNormal);
        vec3 viewDir = normalize(vViewPosition);

        float facing = dot(normal, viewDir) * 0.5 + 0.5;
        float rim = pow(1.0 - max(dot(normal, viewDir), 0.0), 2.15);
        float stripe = smoothstep(0.47, 0.51, sin((vWorldPosition.y + vWorldPosition.x * 0.18) * 16.0 + uTime * 1.6) * 0.5 + 0.5);
        float liquid = sin((vWorldPosition.x - vWorldPosition.y) * 5.4 + uTime * 1.15) * 0.5 + 0.5;

        float shade = 0.08 + facing * 0.78 + rim * 0.30;
        shade -= stripe * 0.055;

        vec3 deepPlum = vec3(0.055, 0.035, 0.155);
        vec3 midViolet = vec3(0.330, 0.170, 0.520);
        vec3 warmCoral = vec3(0.980, 0.315, 0.300);
        vec3 softRose = vec3(0.980, 0.620, 0.700);
        vec3 champagne = vec3(0.945, 0.760, 0.440);

        vec3 color = mix(deepPlum, midViolet, clamp(shade, 0.0, 1.0));
        color = mix(color, warmCoral, rim * 0.34 + liquid * 0.055);
        color = mix(color, softRose, pow(facing, 4.0) * 0.18);
        color = mix(color, champagne, stripe * 0.10);
        color += stripe * vec3(0.075, 0.020, 0.070);

        gl_FragColor = vec4(color, uOpacity);
      }
    `;

    const loaderMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uTwist: { value: 2.2 },
        uPulse: { value: 0.045 },
        uOpacity: { value: 0.98 },
      },
      vertexShader,
      fragmentShader,
      transparent: true,
    });

    const sphere = new THREE.Mesh(new THREE.SphereGeometry(1.72, 128, 128), loaderMaterial);
    root.add(sphere);

    const fishnetMaterial = new THREE.MeshBasicMaterial({
      color: 0xffd6e4,
      transparent: true,
      opacity: 0.48,
      wireframe: true,
    });
    const fishnet = new THREE.Mesh(new THREE.SphereGeometry(1.735, 64, 64), fishnetMaterial);
    root.add(fishnet);

    const goldNetMaterial = new THREE.MeshBasicMaterial({
      color: 0xff8f77,
      transparent: true,
      opacity: 0.30,
      wireframe: true,
    });
    const goldNet = new THREE.Mesh(new THREE.SphereGeometry(1.755, 32, 32), goldNetMaterial);
    root.add(goldNet);

    const haloMaterial = new THREE.MeshBasicMaterial({
      color: 0xff6f75,
      transparent: true,
      opacity: 0.085,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const halo = new THREE.Mesh(new THREE.SphereGeometry(1.98, 64, 64), haloMaterial);
    root.add(halo);

    const rings = [];
    for (let i = 0; i < 4; i += 1) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(2.08 + i * 0.035, 0.006, 10, 260),
        new THREE.MeshBasicMaterial({
          color: i % 2 ? 0xff8f77 : 0xc5a4ff,
          transparent: true,
          opacity: i % 2 ? 0.20 : 0.15,
          blending: THREE.AdditiveBlending,
        })
      );
      ring.rotation.set(Math.PI * (0.20 + i * 0.12), Math.PI * (0.16 + i * 0.17), Math.PI * i * 0.22);
      root.add(ring);
      rings.push(ring);
    }

    const nodeGroup = new THREE.Group();
    root.add(nodeGroup);

    const nodeNames = ["NODE", "POD", "SVC", "PVC", "NS", "ING", "CM", "JOB", "API", "LOG"];
    const labels = [];
    const nodes = [];
    const dotGeometry = new THREE.SphereGeometry(0.045, 18, 18);
    const dotMaterial = new THREE.MeshBasicMaterial({ color: 0xff7f86, transparent: true, opacity: 0.88 });
    const lineMaterial = new THREE.LineBasicMaterial({ color: 0xd8bbff, transparent: true, opacity: 0.18 });
    const radius = 2.75;

    nodeNames.forEach((nodeName, i) => {
      const angle = (i / nodeNames.length) * Math.PI * 2;
      const y = Math.sin(i * 1.7) * 0.55;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const dot = new THREE.Mesh(dotGeometry, dotMaterial.clone());
      dot.position.set(x, y, z);
      dot.userData = { base: dot.position.clone(), phase: i * 0.6 };
      nodeGroup.add(dot);
      nodes.push(dot);

      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(x, y, z),
        new THREE.Vector3(x * 0.33, y * 0.2, z * 0.33),
        new THREE.Vector3(0, 0, 0),
      ]);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(32)), lineMaterial.clone());
      line.material.opacity = 0.07 + (i % 3) * 0.035;
      nodeGroup.add(line);

      const label = document.createElement("div");
      label.className = "release-node-label";
      label.textContent = nodeName;
      sphereStage.appendChild(label);
      labels.push({ el: label, target: dot });
    });

    const particlesGeometry = new THREE.BufferGeometry();
    const particleCount = 520;
    const positions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i += 1) {
      const r = 2.25 + Math.random() * 1.75;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi);
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    particlesGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const particles = new THREE.Points(
      particlesGeometry,
      new THREE.PointsMaterial({ color: 0xff9aa8, size: 0.012, transparent: true, opacity: 0.34, depthWrite: false })
    );
    root.add(particles);

    function resizeRenderer() {
      const rect = sphereDock.getBoundingClientRect();
      const size = Math.max(64, Math.floor(Math.min(rect.width, rect.height)));
      renderer.setSize(size, size, false);
      camera.aspect = 1;
      camera.updateProjectionMatrix();
    }

    function updateLabels() {
      const stageRect = sphereStage.getBoundingClientRect();
      const dockRect = sphereDock.getBoundingClientRect();
      const vector = new THREE.Vector3();
      labels.forEach(({ el, target }) => {
        target.getWorldPosition(vector);
        vector.project(camera);
        const x = dockRect.left - stageRect.left + (vector.x * 0.5 + 0.5) * dockRect.width;
        const y = dockRect.top - stageRect.top + (-vector.y * 0.5 + 0.5) * dockRect.height;
        const visible = vector.z < 1 && Math.abs(vector.x) < 1.08 && Math.abs(vector.y) < 1.08;
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
        el.style.opacity = visible ? "1" : "0";
      });
    }

    const resizeObserver = new ResizeObserver(resizeRenderer);
    resizeObserver.observe(sphereDock);
    window.addEventListener("resize", resizeRenderer);

    const clock = new THREE.Clock();

    function animate() {
      requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();
      const isThinking = progressPanel.classList.contains("is-working");

      if (running) {
        thinkingMix += ((isThinking ? 1 : 0) - thinkingMix) * 0.07;
        loaderMaterial.uniforms.uTime.value = elapsed;
        loaderMaterial.uniforms.uTwist.value = 2.25 + Math.sin(elapsed * 0.7) * 0.55 + thinkingMix * 1.25;
        loaderMaterial.uniforms.uPulse.value = 0.032 + (Math.sin(elapsed * 2.2) * 0.5 + 0.5) * 0.035 + thinkingMix * 0.035;

        root.rotation.y = elapsed * (0.22 + thinkingMix * 0.20);
        root.rotation.x = Math.sin(elapsed * 0.42) * 0.09;
        sphere.rotation.z = elapsed * (0.18 + thinkingMix * 0.10);
        fishnet.rotation.y = elapsed * 0.18;
        fishnet.rotation.z = -elapsed * 0.10;
        goldNet.rotation.y = -elapsed * 0.15;
        goldNet.rotation.x = Math.sin(elapsed * 0.48) * 0.10;

        halo.scale.setScalar(1.0 + Math.sin(elapsed * 1.9) * 0.03 + thinkingMix * 0.10);
        haloMaterial.opacity = 0.075 + thinkingMix * 0.040;
        fishnetMaterial.opacity = 0.48 + thinkingMix * 0.10;
        goldNetMaterial.opacity = 0.30 + thinkingMix * 0.10;

        rings.forEach((ring, i) => {
          ring.rotation.x += 0.0022 + i * 0.0008 + thinkingMix * 0.0015;
          ring.rotation.y -= 0.0014 + i * 0.0005 + thinkingMix * 0.001;
        });

        nodeGroup.rotation.y = elapsed * -0.16;
        particles.rotation.y = elapsed * 0.055;
        particles.rotation.x = Math.sin(elapsed * 0.25) * 0.12;

        nodes.forEach((dot, i) => {
          const base = dot.userData.base;
          const amp = 0.075 + (i % 4) * 0.011 + thinkingMix * 0.035;
          dot.position.set(
            base.x + Math.sin(elapsed * 1.3 + dot.userData.phase) * amp,
            base.y + Math.cos(elapsed * 1.7 + dot.userData.phase) * amp,
            base.z + Math.sin(elapsed * 1.1 + dot.userData.phase) * amp
          );
          dot.scale.setScalar(1 + Math.sin(elapsed * 2.4 + i) * 0.22 + thinkingMix * 0.18);
        });
      }

      updateLabels();
      renderer.render(scene, camera);
    }

    resizeRenderer();
    animate();

    window.releaseSphereRuntime = {
      resize: resizeRenderer,
      pause: () => { running = false; },
      play: () => { running = true; },
    };
  } catch (error) {
    console.warn("Release sphere failed to initialize", error);
    progressPanel.classList.add("sphere-fallback");
  }
}

const activityDefinitions = [
  { id: "start", label: "Intake", hint: "Run accepted and boundaries loaded." },
  { id: "classify", label: "Classify", hint: "Identify workflow and confidence." },
  { id: "plan", label: "Plan", hint: "Create evidence-first action plan." },
  { id: "evidence", label: "Evidence", hint: "Call release-note-agent." },
  { id: "clone", label: "Clone", hint: "Download a temporary local checkout." },
  { id: "security", label: "Security", hint: "Scan common vulnerability signals and summarize through the selected LLM." },
  { id: "quality", label: "Quality", hint: "Run pylint for Python or a safe static fallback for other projects." },
  { id: "cleanup", label: "Cleanup", hint: "Remove temporary checkout after analysis." },
  { id: "draft", label: "Draft", hint: "Write from collected evidence and scan results." },
  { id: "validate", label: "Validate", hint: "Check structure and support." },
  { id: "recover", label: "Recover", hint: "Continue, retry, or escalate." },
  { id: "artifacts", label: "Artifacts", hint: "Save Markdown/PDF outputs." },
  { id: "publish", label: "Publish", hint: "Commit MD/PDF outputs to bosgenesis-artifacts." },
  { id: "complete", label: "Complete", hint: "Finalize run status." },
];

function clonePlain(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function scrubReasoningFields(value) {
  if (Array.isArray(value)) return value.map(scrubReasoningFields);
  if (!value || typeof value !== "object") return value;
  const scrubbed = {};
  Object.entries(value).forEach(([key, child]) => {
    if (key === "reasoning_summary") return;
    scrubbed[key] = scrubReasoningFields(child);
  });
  return scrubbed;
}

function displayPayloadForTimeline(event) {
  return scrubReasoningFields(clonePlain(event?.payload));
}

function resetActivityState() {
  activityState = Object.fromEntries(
    activityDefinitions.map((definition) => [
      definition.id,
      {
        status: "pending",
        detail: definition.hint,
        label: definition.label,
      },
    ])
  );
  setActivityStatus("Awaiting run");
  renderActivityGraph();
  if (!activityRailPinned) collapseActivityRail({ force: true });
}
function setActivityStatus(message) {
  if (agentActivityStatus) agentActivityStatus.textContent = message;
}
function updateActivityRailControls() {
  if (!agentActivityRail) return;
  const collapsed = agentActivityRail.classList.contains("is-collapsed");
  agentActivityRail.setAttribute("aria-expanded", String(!collapsed));
  agentActivityRail.dataset.autoHideMs = String(activityRailAutoHideMs);
  if (agentActivityToggle) {
    agentActivityToggle.textContent = collapsed ? "Show" : "Hide";
    agentActivityToggle.setAttribute("aria-expanded", String(!collapsed));
    agentActivityToggle.setAttribute(
      "aria-label",
      collapsed ? "Show agent activity feed" : "Collapse agent activity feed"
    );
  }
  if (agentActivityPin) {
    agentActivityPin.textContent = activityRailPinned ? "Pinned" : "Pin";
    agentActivityPin.setAttribute("aria-pressed", String(activityRailPinned));
    agentActivityPin.classList.toggle("is-active", activityRailPinned);
    agentActivityPin.setAttribute(
      "aria-label",
      activityRailPinned ? "Unpin agent activity feed" : "Pin agent activity feed open"
    );
  }
}

function clearActivityRailAutoHide() {
  if (!activityRailAutoHideTimer) return;
  window.clearTimeout(activityRailAutoHideTimer);
  activityRailAutoHideTimer = null;
}

function clearDelayedActivityReveal() {
  if (activityRevealDelayTimer) {
    window.clearTimeout(activityRevealDelayTimer);
    activityRevealDelayTimer = null;
  }
  activityRevealDelayUntil = 0;
  delayedActivityRevealReason = "";
}

function clearPlanningWarmupTimer() {
  if (planningWarmupTimer) {
    window.clearTimeout(planningWarmupTimer);
    planningWarmupTimer = null;
  }
}

function deferActivityReveal(delayMs) {
  clearDelayedActivityReveal();
  activityRevealDelayUntil = Date.now() + Math.max(0, delayMs);
}

function scheduleDelayedActivityReveal(reason, delayMs) {
  delayedActivityRevealReason = reason || delayedActivityRevealReason;
  if (activityRevealDelayTimer) return;
  activityRevealDelayTimer = window.setTimeout(() => {
    activityRevealDelayTimer = null;
    activityRevealDelayUntil = 0;
    revealActivityRail(delayedActivityRevealReason || "Execution plan is being composed.");
    delayedActivityRevealReason = "";
  }, Math.max(0, delayMs));
}

function revealActivityRailForEvent(reason) {
  const delayMs = Math.max(0, activityRevealDelayUntil - Date.now());
  if (delayMs > 0) {
    scheduleDelayedActivityReveal(reason, delayMs);
    return;
  }
  revealActivityRail(reason);
}

function revealPlanningWarmupActivity(payload) {
  const event = {
    event_type: "planning_started",
    message: "Creating release-note plan",
    payload: payload || {},
    created_at: new Date().toISOString(),
  };
  if (activityState.start?.status === "pending") {
    markActivity("start", "success", event, "Request received; ESDA is preparing the workflow envelope.");
  }
  if (activityState.classify?.status === "pending") {
    markActivity("classify", "running", event, "Classifying the request before tool execution.");
  }
  if (activityState.plan?.status === "pending") {
    markActivity("plan", "running", event, "Creating a read-only evidence plan and source-reference strategy.");
  }
  setActivityStatus("Creating execution plan");
  renderActivityGraph();
  revealActivityRail("Execution plan is being composed.");
}

function schedulePlanningWarmupActivity(payload, delayMs) {
  clearPlanningWarmupTimer();
  planningWarmupTimer = window.setTimeout(() => {
    planningWarmupTimer = null;
    revealPlanningWarmupActivity(payload);
  }, delayMs);
}

function addPlanningWarmupNote(payload) {
  const githubUrl = payload?.github_url || "the selected repository";
  addWorkingNote({
    event_type: "ephemeral_working_note",
    message: "Creating execution plan.",
    payload: {
      phase: "CREATING PLAN",
      display_index: "00",
      detail: `Reading ${githubUrl}, source refs, selected model, and guardrails before the autonomy map is revealed.`,
      ephemeral: true,
      persisted: false,
    },
  });
}
function hideActivityRailUntilRun() {
  if (!agentActivityRail) return;
  clearActivityRailAutoHide();
  clearDelayedActivityReveal();
  clearPlanningWarmupTimer();
  agentActivityRail.classList.add("is-dormant", "is-collapsed");
  agentActivityRail.classList.remove("is-revealed");
  updateActivityRailControls();
}

function collapseActivityRail(options = {}) {
  if (!agentActivityRail) return;
  if (activityRailPinned && !options.force) return;
  clearActivityRailAutoHide();
  agentActivityRail.classList.add("is-collapsed");
  agentActivityRail.classList.remove("is-revealed");
  updateActivityRailControls();
}

function scheduleActivityRailAutoHide() {
  if (!agentActivityRail || activityRailPinned) return;
  clearActivityRailAutoHide();
  activityRailAutoHideTimer = window.setTimeout(() => {
    collapseActivityRail();
  }, activityRailAutoHideMs);
}

function revealActivityRail(reason = "", options = {}) {
  if (!agentActivityRail) return;
  agentActivityRail.classList.remove("is-dormant", "is-collapsed");
  agentActivityRail.classList.add("is-revealed");
  if (reason) agentActivityRail.dataset.lastRevealReason = reason;
  updateActivityRailControls();
  if (options.autoHide !== false) scheduleActivityRailAutoHide();
}

function setActivityRailPinned(pinned) {
  activityRailPinned = Boolean(pinned);
  window.localStorage.setItem(activityRailPinnedStorageKey, activityRailPinned ? "true" : "false");
  agentActivityRail?.classList.toggle("is-pinned", activityRailPinned);
  if (activityRailPinned) {
    clearActivityRailAutoHide();
    revealActivityRail("Pinned by user.", { autoHide: false });
  } else if (agentActivityRail && !agentActivityRail.classList.contains("is-collapsed")) {
    scheduleActivityRailAutoHide();
  }
  updateActivityRailControls();
}

function initializeActivityRail() {
  activityRailPinned = window.localStorage.getItem(activityRailPinnedStorageKey) === "true";
  agentActivityRail?.classList.toggle("is-pinned", activityRailPinned);
  agentActivityRail?.setAttribute("data-auto-hide-ms", String(activityRailAutoHideMs));
  hideActivityRailUntilRun();
  updateActivityRailControls();
}

function bindActivityRailControls() {
  agentActivityToggle?.addEventListener("click", () => {
    if (!agentActivityRail) return;
    if (agentActivityRail.classList.contains("is-collapsed")) {
      revealActivityRail("Opened manually.");
      return;
    }
    if (activityRailPinned) setActivityRailPinned(false);
    collapseActivityRail({ force: true });
  });
  agentActivityPin?.addEventListener("click", () => {
    setActivityRailPinned(!activityRailPinned);
  });
}

function shouldRevealActivityEvent(event) {
  return [
    "run_started",
    "workflow_classified",
    "plan_created",
    "tool_call_started",
    "tool_call_completed",
    "repo_clone_started",
    "repo_clone_completed",
    "vulnerability_scan_completed",
    "quality_scan_completed",
    "repo_cleanup_completed",
    "draft_started",
    "validation_completed",
    "recovery_recommendation",
    "artifact_created",
    "artifact_warning",
    "artifact_publish_started",
    "artifact_publish_completed",
    "artifact_publish_failed",
    "run_completed",
    "run_failed",
  ].includes(event?.event_type);
}

function markActivity(id, status, event, detail = null) {
  if (!activityState[id]) return;
  const previous = activityState[id];
  activityState[id] = {
    ...previous,
    status,
    detail: detail || summarizeEvent(event) || previous.detail,
    eventType: event?.event_type || previous.eventType,
    timestamp: event?.created_at || previous.timestamp,
  };
}

function markFirstRunningAsFailed(event) {
  const current = activityDefinitions.find((definition) => activityState[definition.id]?.status === "running");
  if (current) markActivity(current.id, "failed", event, summarizeEvent(event));
}

function applyActivityEvent(event, options = {}) {
  if (!event || event.event_type === "ephemeral_working_note") return;
  switch (event.event_type) {
    case "run_started":
      markActivity("start", "success", event);
      markActivity("classify", "running", event, "Classifying the request against allowed workflow families.");
      setActivityStatus("Autonomy sequence running");
      break;
    case "workflow_classified":
      markActivity("classify", "success", event);
      markActivity("plan", "running", event, "Creating an evidence-first plan.");
      break;
    case "planning_started":
      markActivity("plan", "running", event);
      break;
    case "plan_created":
      markActivity("plan", "success", event);
      markActivity("evidence", "running", event, "Preparing release-note-agent evidence collection.");
      break;
    case "tool_call_started":
      markActivity("evidence", "running", event);
      break;
    case "tool_call_completed":
      markActivity("evidence", event.payload?.result?.status === "success" ? "success" : "recovered", event);
      markActivity("clone", "running", event, "Downloading a temporary repository checkout.");
      break;
    case "repo_clone_started":
      markActivity("clone", "running", event);
      break;
    case "repo_clone_completed":
      markActivity("clone", event.payload?.clone?.status === "success" ? "success" : "recovered", event);
      markActivity("security", "running", event, "Scanning common vulnerability signals.");
      break;
    case "vulnerability_scan_completed":
      markActivity("security", event.payload?.status === "completed" ? "success" : "recovered", event);
      markActivity("quality", "running", event, "Running code quality checks.");
      break;
    case "quality_scan_completed":
      markActivity("quality", event.payload?.quality?.status === "completed" ? "success" : "recovered", event);
      markActivity("cleanup", "running", event, "Removing temporary repository checkout.");
      break;
    case "repo_cleanup_completed":
      markActivity("cleanup", event.payload?.cleanup?.removed === false ? "recovered" : "success", event);
      markActivity("draft", "running", event, "Drafting from release-note-agent output and repository scan results.");
      break;
    case "draft_started":
      markActivity("draft", "running", event);
      break;
    case "validation_completed":
      markActivity("draft", "success", event, "Draft generated and ready for validation.");
      markActivity("validate", event.payload?.valid === false ? "failed" : "success", event);
      markActivity("recover", "running", event, "Selecting bounded recovery or continue action.");
      break;
    case "recovery_recommendation":
      markActivity("recover", event.payload?.action === "escalate" ? "recovered" : "success", event);
      markActivity("artifacts", "running", event, "Saving reviewable output artifacts.");
      break;
    case "artifact_created":
      markActivity("artifacts", "success", event);
      break;
    case "artifact_warning":
      markActivity("artifacts", "recovered", event);
      break;
    case "artifact_publish_started":
      markActivity("publish", "running", event, "Committing Markdown and PDF files to the configured artifact repository.");
      setActivityStatus("Publishing artifacts");
      break;
    case "artifact_publish_completed":
      markActivity("publish", "success", event);
      markActivity("complete", "running", event, "Finalizing run after artifact repo commit.");
      setActivityStatus("Artifacts published");
      break;
    case "artifact_publish_failed":
      markActivity("publish", "failed", event);
      setActivityStatus("Artifact publish failed");
      break;
    case "run_completed":
      markActivity("complete", "success", event);
      setActivityStatus("Autonomy sequence completed");
      break;
    case "run_failed":
      markFirstRunningAsFailed(event);
      markActivity("complete", "failed", event);
      setActivityStatus("Autonomy sequence needs review");
      break;
    default:
      break;
  }
  renderActivityGraph();
  if (options.reveal !== false && shouldRevealActivityEvent(event)) {
    revealActivityRailForEvent(summarizeEvent(event));
  }
}

function renderActivityGraph() {
  if (!agentActivityGraph) return;
  agentActivityGraph.innerHTML = "";
  activityDefinitions.forEach((definition, index) => {
    const state = activityState[definition.id] || { status: "pending", detail: definition.hint };
    const node = document.createElement("div");
    node.className = `activity-node is-${state.status}`;
    node.setAttribute("role", "listitem");
    node.tabIndex = 0;

    const dot = document.createElement("span");
    dot.className = "activity-node-dot";
    dot.textContent = index + 1;
    const label = document.createElement("span");
    label.className = "activity-node-label";
    label.textContent = definition.label;
    const popover = document.createElement("span");
    popover.className = "activity-node-popover";
    popover.textContent = state.detail || definition.hint;

    node.appendChild(dot);
    node.appendChild(label);
    node.appendChild(popover);
    agentActivityGraph.appendChild(node);

    if (index < activityDefinitions.length - 1) {
      const connector = document.createElement("span");
      connector.className = `activity-connector is-${state.status}`;
      agentActivityGraph.appendChild(connector);
    }
  });
}

function extractSummaryCandidates(event) {
  const payload = event?.payload || {};
  const candidates = [];
  if (payload.reasoning_summary) candidates.push(payload.reasoning_summary);
  if (payload.llm_review?.reasoning_summary) candidates.push(payload.llm_review.reasoning_summary);
  if (payload.repository_scan?.llm_review?.reasoning_summary) {
    candidates.push(payload.repository_scan.llm_review.reasoning_summary);
  }
  ["classification", "validation", "recovery"].forEach((key) => {
    if (payload[key]?.reasoning_summary) candidates.push(payload[key].reasoning_summary);
  });
  return candidates.filter(Boolean).map((summary) => String(summary).trim()).filter(Boolean);
}

function safeSummaryLabel(event) {
  const labels = {
    workflow_classified: "Classifier",
    plan_created: "Planner",
    reasoning_summary: "Planner",
    validation_completed: "Verifier",
    vulnerability_scan_completed: "Security Scan",
    quality_scan_completed: "Quality Scan",
    recovery_recommendation: "Recovery",
    run_completed: "Final",
    run_failed: "Final",
  };
  return labels[event?.event_type] || "Agent";
}

function collectSafeSummaries(event) {
  extractSummaryCandidates(event).forEach((summary) => {
    const key = `${safeSummaryLabel(event)}:${summary}`;
    if (safeSummaryKeys.has(key)) return;
    safeSummaryKeys.add(key);
    safeSummaryItems.push({ label: safeSummaryLabel(event), summary, eventType: event.event_type });
  });
}

function renderSafeSummaries(show = false) {
  if (!safeSummaryList || !safeSummaryPanel) return;
  safeSummaryList.innerHTML = "";
  if (!safeSummaryItems.length) {
    const empty = document.createElement("div");
    empty.className = "stream-empty-state";
    empty.textContent = "No safe reasoning summaries are available yet.";
    safeSummaryList.appendChild(empty);
  } else {
    safeSummaryItems.forEach((item, index) => {
      const row = document.createElement("article");
      row.className = "safe-summary-item";
      const label = document.createElement("div");
      label.className = "safe-summary-label";
      label.textContent = `${index + 1}. ${item.label}`;
      const text = document.createElement("p");
      text.textContent = item.summary;
      row.appendChild(label);
      row.appendChild(text);
      safeSummaryList.appendChild(row);
    });
  }
  safeSummaryPanel.classList.toggle("is-hidden", !show);
}

function resetSafeSummaries() {
  safeSummaryItems = [];
  safeSummaryKeys.clear();
  renderSafeSummaries(false);
}

function setWorkingStreamVisible(visible) {
  if (!ephemeralWorkingPanel) return;
  ephemeralWorkingPanel.classList.toggle("is-hidden", !visible);
}

function resetWorkingStream() {
  workingNoteCounter = 0;
  workingNotePhases = new Set();
  if (!ephemeralWorkingStream || !ephemeralWorkingPanel) return;
  ephemeralWorkingStream.innerHTML = '<div class="stream-empty-state">Live model and agent working notes will appear only while this page is connected.</div>';
  ephemeralWorkingPanel.classList.add("is-empty");
  setWorkingStreamVisible(true);
}

function appendStreamingText(element, text) {
  let index = 0;
  element.textContent = "";
  const timer = window.setInterval(() => {
    index += Math.max(1, Math.ceil(text.length / 38));
    element.textContent = text.slice(0, index);
    if (index >= text.length) window.clearInterval(timer);
  }, 18);
}

function addWorkingNote(event) {
  if (!ephemeralWorkingStream || !ephemeralWorkingPanel) return;
  const phaseKey = event.payload?.phase;
  if (phaseKey && workingNotePhases.has(phaseKey)) return;
  if (phaseKey) workingNotePhases.add(phaseKey);
  if (ephemeralWorkingPanel.classList.contains("is-hidden")) setWorkingStreamVisible(true);
  if (ephemeralWorkingPanel.classList.contains("is-empty")) {
    ephemeralWorkingStream.innerHTML = "";
    ephemeralWorkingPanel.classList.remove("is-empty");
  }
  const payload = event.payload || {};
  const item = document.createElement("article");
  item.className = "working-note-item";
  const label = document.createElement("div");
  label.className = "working-note-label";
  const displayIndex = payload.display_index || String(++workingNoteCounter).padStart(2, "0");
  label.textContent = `${displayIndex} / ${payload.phase || "agent"}`;
  const text = document.createElement("p");
  const noteText = payload.detail ? `${event.message} ${payload.detail}` : event.message;
  item.appendChild(label);
  item.appendChild(text);
  ephemeralWorkingStream.appendChild(item);
  appendStreamingText(text, noteText);
  ephemeralWorkingStream.scrollTop = ephemeralWorkingStream.scrollHeight;
}


function liveWorkingNoteForEvent(event) {
  const payload = event?.payload || {};
  const notes = {
    run_started: ["start", "Starting autonomous release-note workflow.", "Preparing selected model context and read-only execution boundaries."],
    workflow_classified: ["classify", "Workflow classified.", "The request matched the release-note creation workflow."],
    plan_created: ["plan", "Evidence-first release-note plan created.", "The agent selected the source-reference and verification path."],
    tool_call_started: ["evidence", "Calling release-note-agent for source evidence.", "The external agent is collecting release evidence."],
    repo_clone_started: ["clone", "Downloading repository for local analysis.", "The checkout is temporary and read-only."],
    vulnerability_scan_completed: ["security", "Repository vulnerability scan completed.", `${payload.finding_count ?? 0} common vulnerability signal(s) identified for review.`],
    quality_scan_completed: ["quality", "Repository code quality scan completed.", payload.quality?.summary || "Quality checks finished."],
    repo_cleanup_completed: ["cleanup", "Temporary repository checkout removed.", payload.cleanup?.status || "Cleanup completed."],
    draft_started: ["draft", "Drafting release notes from evidence and scan results.", "The model is combining release-note-agent output with repository scan findings."],
    validation_completed: ["validate", "Validating release-note structure and evidence.", payload.message || "Verifier finished."],
    recovery_recommendation: ["recover", "Selecting continue, recovery, or escalation behavior.", payload.action ? `Recommended action: ${payload.action}.` : "Recovery decision recorded."],
    artifact_created: ["artifacts", "Saving reviewable release-note artifacts.", payload.artifact?.title || "Artifact saved."],
    artifact_publish_started: ["publish", "Publishing release-note artifacts.", "Committing Markdown and PDF files into the configured bosgenesis-artifacts folder."],
    artifact_publish_completed: ["publish", "Release-note artifacts published.", payload.artifact_publish?.folder_name ? `Folder: ${payload.artifact_publish.folder_name}.` : "Artifact repository commit completed."],
    artifact_publish_failed: ["publish", "Release-note artifact publish failed.", payload.artifact_publish?.error?.message || "Review Git credentials and repository access."],
    run_completed: ["finalize", "Finalizing run status.", "Live working notes will be replaced by persisted safe summaries."],
    run_failed: ["finalize", "Finalizing failed run status.", "Review the persisted event log and safe summaries."],
  };
  const note = notes[event?.event_type];
  if (!note) return null;
  return {
    event_type: "ephemeral_working_note",
    message: note[1],
    payload: {
      phase: note[0],
      detail: note[2],
      ephemeral: true,
      persisted: false,
    },
  };
}

function addLiveWorkingNoteForEvent(event) {
  const note = liveWorkingNoteForEvent(event);
  if (note) addWorkingNote(note);
}
function finalizeWorkingStream() {
  if (ephemeralWorkingStream) ephemeralWorkingStream.innerHTML = "";
  if (ephemeralWorkingPanel) {
    ephemeralWorkingPanel.classList.add("is-empty", "is-hidden");
  }
  renderSafeSummaries(true);
}

function summarizeEvent(event) {
  if (!event) return "";
  const payload = event.payload || {};
  if (payload.reasoning_summary) return payload.reasoning_summary;
  if (payload.message) return payload.message;
  if (payload.tool_name) return `${event.message}: ${payload.tool_name}`;
  if (payload.result?.status) return `${event.message}: ${payload.result.status}`;
  if (payload.clone?.status) return `${event.message}: ${payload.clone.status}`;
  if (typeof payload.finding_count !== "undefined") return `${event.message}: ${payload.finding_count} finding(s)`;
  if (payload.quality?.summary) return payload.quality.summary;
  if (payload.cleanup?.status) return `${event.message}: ${payload.cleanup.status}`;
  if (payload.validation?.message) return payload.validation.message;
  if (payload.action) return `${event.message}: ${payload.action}`;
  if (payload.artifact?.title) return `${event.message}: ${payload.artifact.title}`;
  if (payload.artifact_publish?.folder_name) return `${event.message}: ${payload.artifact_publish.folder_name}`;
  if (payload.artifact_publish?.error?.message) return payload.artifact_publish.error.message;
  if (payload.error?.message) return payload.error.message;
  return event.message || "No detail available.";
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function addTimeline(event, options = {}) {
  if (!event) return;
  if (event.event_type === "ephemeral_working_note") {
    addWorkingNote(event);
    return;
  }
  if (event.event_id && seenEventIds.has(event.event_id)) return;
  if (event.event_id) {
    seenEventIds.add(event.event_id);
    lastEventId = event.event_id;
  }
  if (Number.isFinite(Number(event.sequence))) {
    lastEventSequence = Number(event.sequence);
  }
  timelineEvents.push(event);
  collectSafeSummaries(event);
  applyActivityEvent(event, { reveal: options.revealActivity !== false });
  const item = document.createElement("li");
  const title = document.createElement("div");
  title.className = "fw-semibold";
  title.textContent = `${event.event_type}: ${event.message}`;
  const detail = document.createElement("pre");
  detail.className = "small text-secondary mb-0";
  detail.textContent = JSON.stringify(displayPayloadForTimeline(event), null, 2);
  item.appendChild(title);
  item.appendChild(detail);
  timeline.appendChild(item);
  if (timelineScroll) {
    timelineScroll.scrollTop = timelineScroll.scrollHeight;
  }
}

function setStatus(status) {
  statusBadge.textContent = status;
  statusBadge.className = "badge mb-2 align-self-start " + (
    status === "completed" ? "text-bg-success" :
    status === "failed" ? "text-bg-danger" :
    "text-bg-secondary"
  );
}

function artifactDownloadLabel(artifact) {
  const mime = (artifact.mime_type || "").toLowerCase();
  const type = (artifact.artifact_type || "").toLowerCase();
  if (mime.includes("pdf") || type.includes("pdf")) return "Download PDF";
  if (mime.includes("markdown") || type === "release_note") return "Download Markdown";
  return "Download Artifact";
}

function artifactButtonClass(artifact) {
  const mime = (artifact.mime_type || "").toLowerCase();
  const type = (artifact.artifact_type || "").toLowerCase();
  if (mime.includes("pdf") || type.includes("pdf")) return "btn-outline-danger";
  return "btn-outline-primary";
}

function renderArtifactLinks(artifacts) {
  artifactLinks.innerHTML = "";
  (artifacts || []).forEach((artifact) => {
    if (!artifact?.artifact_id) return;
    const group = document.createElement("div");
    group.className = "d-flex align-items-center gap-2 mb-2 flex-wrap";
    const link = document.createElement("a");
    link.className = `btn btn-sm ${artifactButtonClass(artifact)}`;
    link.href = `/api/artifacts/${artifact.artifact_id}`;
    link.textContent = artifactDownloadLabel(artifact);
    link.setAttribute("download", "");
    const meta = document.createElement("span");
    meta.className = "small text-secondary";
    meta.textContent = artifact.title || artifact.artifact_type || "release note";
    group.appendChild(link);
    group.appendChild(meta);
    artifactLinks.appendChild(group);
  });
}

function renderArtifactLink(artifact) {
  renderArtifactLinks(artifact ? [artifact] : []);
}

async function refreshArtifacts(runId) {
  const response = await fetch(`/api/runs/${runId}/artifacts`);
  if (!response.ok) return;
  const result = await response.json();
  renderArtifactLinks(result.artifacts || []);
}

function resetTimelineState() {
  timelineEvents = [];
  seenEventIds.clear();
  lastEventId = null;
  lastEventSequence = 0;
  timeline.innerHTML = "";
  resetSafeSummaries();
  resetActivityState();
  resetWorkingStream();
}

function setActiveRun(runId) {
  activeRunId = runId;
  if (runId) {
    window.localStorage.setItem(activeRunStorageKey, runId);
  } else {
    window.localStorage.removeItem(activeRunStorageKey);
  }
}

function isTerminalStatus(status) {
  return ["completed", "failed", "cancelled", "stopped"].includes(status);
}

function isFreshTransaction(transaction, maxAgeMinutes = 120) {
  if (!transaction) return false;
  const timestamp = Date.parse(transaction.updated_at || transaction.created_at || "");
  if (!Number.isFinite(timestamp)) return false;
  return Date.now() - timestamp <= maxAgeMinutes * 60 * 1000;
}

function shouldAutoRestoreTransaction(transaction) {
  if (!transaction) return false;
  return !isTerminalStatus(transaction.status) && isFreshTransaction(transaction);
}

function visualStateForStatus(status) {
  if (status === "completed") return "complete";
  if (["failed", "cancelled", "stopped"].includes(status)) return "failed";
  if (["created", "planning", "running", "waiting_for_approval"].includes(status)) return "working";
  return "idle";
}

async function refreshRun(runId) {
  const response = await fetch(`/api/runs/${runId}`);
  if (!response.ok) return null;
  const run = await response.json();
  setStatus(run.status);
  setProgressVisualState(visualStateForStatus(run.status));
  if (run.final_report) {
    finalReport.textContent = run.final_report;
  }
  await refreshArtifacts(runId);
  return run;
}

if (copyProgressButton) {
  copyProgressButton.addEventListener("click", async () => {
    try {
      await copyText(timelineText());
      setCopyStatus("Progress copied.");
    } catch (error) {
      setCopyStatus(`Copy failed: ${error.message}`);
    }
  });
}

function setSidebarOpen(open) {
  if (!transactionSidebar) return;
  document.body.classList.toggle("transaction-sidebar-open", open);
  transactionSidebar.setAttribute("aria-hidden", open ? "false" : "true");
  transactionToggle?.setAttribute("aria-expanded", open ? "true" : "false");
}

function transactionTime(value) {
  if (!value) return "";
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch (_error) {
    return value;
  }
}

function renderTransactions(transactions) {
  if (!transactionList) return;
  transactionList.innerHTML = "";
  if (!transactions.length) {
    transactionStatus.textContent = "No release-note transactions yet.";
    return;
  }
  transactionStatus.textContent = `${transactions.length} release-note transaction${transactions.length === 1 ? "" : "s"}`;
  transactions.forEach((transaction) => {
    const card = document.createElement("article");
    card.className = `transaction-card${transaction.run_id === activeRunId ? " is-active" : ""}`;
    card.tabIndex = 0;
    card.setAttribute("role", "listitem");

    const row = document.createElement("div");
    row.className = "transaction-card-row";
    const status = document.createElement("span");
    status.className = "transaction-status-pill";
    status.textContent = transaction.status || "unknown";
    const clear = document.createElement("button");
    clear.className = "transaction-clear";
    clear.type = "button";
    clear.textContent = "Clear";
    clear.addEventListener("click", async (event) => {
      event.stopPropagation();
      await clearTransaction(transaction.run_id);
    });
    row.appendChild(status);
    row.appendChild(clear);

    const title = document.createElement("div");
    title.className = "transaction-card-title";
    title.textContent = transaction.title || transaction.goal || transaction.run_id;

    const subtitle = document.createElement("div");
    subtitle.className = "transaction-card-subtitle";
    subtitle.textContent = transaction.target_url || transaction.workflow_type || "release_note_creation";

    const meta = document.createElement("div");
    meta.className = "transaction-card-meta";
    const artifacts = Number(transaction.artifact_count || 0);
    meta.textContent = `${transactionTime(transaction.updated_at)} | ${artifacts} artifact${artifacts === 1 ? "" : "s"}`;

    card.appendChild(row);
    card.appendChild(title);
    card.appendChild(subtitle);
    card.appendChild(meta);
    card.addEventListener("click", () => openRun(transaction.run_id, { closeSidebar: true }));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openRun(transaction.run_id, { closeSidebar: true });
      }
    });
    transactionList.appendChild(card);
  });
}

async function loadTransactions() {
  if (!transactionList) return [];
  try {
    const response = await fetch("/api/transactions?workflow_type=release_note_creation");
    if (!response.ok) {
      transactionLoadFailed = true;
      throw new Error(`HTTP ${response.status}`);
    }
    transactionLoadFailed = false;
    const result = await response.json();
    const transactions = result.transactions || [];
    renderTransactions(transactions);
    return transactions;
  } catch (error) {
    transactionStatus.textContent = `Could not load transactions: ${error.message}`;
    return [];
  }
}

async function clearTransaction(runId) {
  const response = await fetch(`/api/transactions/${runId}/clear`, { method: "POST" });
  if (!response.ok) {
    clearDelayedActivityReveal();
    clearPlanningWarmupTimer();
    transactionStatus.textContent = `Clear failed: HTTP ${response.status}`;
    return;
  }
  await loadTransactions();
  if (runId === activeRunId) {
    setActiveRun(null);
    resetRunView("Transaction hidden from history. Start a new run or choose another transaction.");
    setStatus("Idle");
    setProgressVisualState("idle");
  }
}

function latestPreviewFromEvents(events) {
  const previewEvent = [...events].reverse().find((event) => event.payload?.preview || event.payload?.final_report);
  return previewEvent?.payload?.preview || previewEvent?.payload?.final_report || null;
}

function applySnapshot(snapshot) {
  const run = snapshot.run;
  setActiveRun(run.run_id);
  resetTimelineState();
  (snapshot.events || []).forEach((event) => addTimeline(event, { revealActivity: false }));
  setStatus(run.status);
  setProgressVisualState(visualStateForStatus(run.status));
  renderArtifactLinks(snapshot.artifacts || []);
  finalReport.textContent = run.final_report || latestPreviewFromEvents(snapshot.events || []) || (
    (snapshot.events || []).length ? "Run is still in progress. Live updates will continue here." : "No progress events yet."
  );
  if (run.target_url) {
    const githubInput = document.getElementById("github_url");
    if (githubInput && !githubInput.value) githubInput.value = run.target_url;
  }
}

async function openRun(runId, options = {}) {
  if (!runId) return;
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  finalReport.textContent = "Loading persisted run state...";
  const response = await fetch(`/api/runs/${runId}/snapshot`);
  if (!response.ok) {
    clearDelayedActivityReveal();
    clearPlanningWarmupTimer();
    if (response.status === 404) {
      setActiveRun(null);
      resetRunView("Saved run was not found. Start a new run or choose another transaction from history.");
      setStatus("Idle");
      setProgressVisualState("idle");
      await loadTransactions();
      return;
    }
    finalReport.textContent = `Could not restore run: HTTP ${response.status}`;
    setStatus("failed");
    setProgressVisualState("failed");
    return;
  }
  const snapshot = await response.json();
  applySnapshot(snapshot);
  await loadTransactions();
  if (options.closeSidebar) setSidebarOpen(false);
  if (!isTerminalStatus(snapshot.run.status)) {
    connectRunEvents(runId, snapshot.last_event_id || lastEventId);
  }
}

function connectRunEvents(runId, afterEventId = null) {
  if (currentEventSource) currentEventSource.close();
  const params = afterEventId ? `?after_event_id=${encodeURIComponent(afterEventId)}` : "";
  currentEventSource = new EventSource(`/api/runs/${runId}/events${params}`);
  currentEventSource.onmessage = async (message) => {
    const eventData = JSON.parse(message.data);
    await handleRunEvent(eventData, runId);
  };
  currentEventSource.onerror = async () => {
    currentEventSource?.close();
    currentEventSource = null;
    const run = await refreshRun(runId);
    if (!run || isTerminalStatus(run.status)) return;
    setCopyStatus("Live connection paused; persisted progress is still available after refresh.");
  };
}

async function handleRunEvent(eventData, runId) {
  if (eventData.event_type === "ephemeral_working_note") {
    addWorkingNote(eventData);
    return;
  }
  addLiveWorkingNoteForEvent(eventData);
  addTimeline(eventData);
  if (["run_started", "plan_created", "tool_call_started", "workflow_classified", "repo_clone_started", "vulnerability_scan_completed", "quality_scan_completed", "draft_started"].includes(eventData.event_type)) {
    setStatus("running");
    setProgressVisualState("working");
  }
  if (eventData.event_type === "artifact_created") {
    if (eventData.payload?.preview) {
      finalReport.textContent = eventData.payload.preview;
    }
    await refreshArtifacts(runId);
  }
  if (eventData.event_type === "run_completed" || eventData.event_type === "run_failed") {
    clearDelayedActivityReveal();
    clearPlanningWarmupTimer();
    setProgressVisualState(eventData.event_type === "run_completed" ? "complete" : "failed");
    if (eventData.payload?.artifact) {
      renderArtifactLinks(eventData.payload.artifacts || [eventData.payload.artifact]);
    }
    currentEventSource?.close();
    currentEventSource = null;
    finalizeWorkingStream();
    await refreshRun(runId);
    await loadTransactions();
  }
}

function resetRunView(message) {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  resetTimelineState();
  hideActivityRailUntilRun();
  artifactLinks.innerHTML = "";
  finalReport.textContent = message;
  setCopyStatus("");
}

async function startReleaseNoteRun(event) {
  event.preventDefault();
  resetRunView("Generating release-note draft...");
  setStatus("planning");
  setProgressVisualState("working");
  const payload = {
    github_url: valueOf("github_url"),
    release_name: valueOf("release_name"),
    branch: valueOf("branch"),
    tag: valueOf("tag"),
    commit_sha: valueOf("commit_sha"),
    analysis_depth: valueOf("analysis_depth") || "fast",
    model_profile: valueOf("model_profile"),
  };
  setWorkingStreamVisible(true);
  renderSafeSummaries(false);
  const planningDelayMs = 2400 + Math.floor(Math.random() * 1600);
  deferActivityReveal(planningDelayMs);
  addPlanningWarmupNote(payload);
  schedulePlanningWarmupActivity(payload, planningDelayMs);

  const response = await fetch("/api/release-notes", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    clearDelayedActivityReveal();
    clearPlanningWarmupTimer();
    finalReport.textContent = `Failed to start release-note run: ${response.status}`;
    setStatus("failed");
    setProgressVisualState("failed");
    return;
  }

  const created = await response.json();
  setActiveRun(created.run_id);
  await loadTransactions();
  connectRunEvents(created.run_id);
}

function bindTransactionSidebar() {
  transactionToggle?.addEventListener("click", () => setSidebarOpen(true));
  transactionClose?.addEventListener("click", () => setSidebarOpen(false));
  transactionBackdrop?.addEventListener("click", () => setSidebarOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setSidebarOpen(false);
  });
}

async function bootReleaseNotesPage() {
  setProgressVisualState("idle");
  await initReleaseSphere();
  initializeActivityRail();
  resetActivityState();
  resetWorkingStream();
  renderSafeSummaries(false);
  bindTransactionSidebar();
  bindActivityRailControls();
  const transactions = await loadTransactions();
  if (transactionLoadFailed) return;
  const storedRunId = window.localStorage.getItem(activeRunStorageKey);
  const storedTransaction = transactions.find((item) => item.run_id === storedRunId);
  const activeTransaction = transactions.find(
    (item) => !isTerminalStatus(item.status) && isFreshTransaction(item)
  );
  const runToOpen = shouldAutoRestoreTransaction(storedTransaction)
    ? storedTransaction.run_id
    : activeTransaction?.run_id;
  if (runToOpen) {
    await openRun(runToOpen);
  } else {
    setActiveRun(null);
    resetRunView("No release-note run yet.");
    setStatus("Idle");
    setProgressVisualState("idle");
  }
}

releaseNoteForm.addEventListener("submit", startReleaseNoteRun);
bootReleaseNotesPage();
