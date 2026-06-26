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

let timelineEvents = [];

function valueOf(id) {
  const value = document.getElementById(id).value.trim();
  return value.length ? value : null;
}

function timelineText() {
  if (!timelineEvents.length) return "No progress events yet.";
  return timelineEvents.map((event, index) => {
    const payload = JSON.stringify(event.payload || {}, null, 2);
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
      title: "Release intelligence is standing by.",
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
  if (sphereTitle && copy.title) sphereTitle.textContent = copy.title;
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

    const keyLight = new THREE.PointLight(0x7ed8ff, 2.0, 16);
    keyLight.position.set(3.4, 2.8, 5.4);
    scene.add(keyLight);

    const goldLight = new THREE.PointLight(0xd8ba68, 1.0, 12);
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

        vec3 deepBlue = vec3(0.010, 0.058, 0.160);
        vec3 midBlue = vec3(0.012, 0.190, 0.520);
        vec3 electricBlue = vec3(0.045, 0.520, 1.000);
        vec3 iceBlue = vec3(0.720, 0.925, 1.000);
        vec3 gold = vec3(0.820, 0.660, 0.270);

        vec3 color = mix(deepBlue, midBlue, clamp(shade, 0.0, 1.0));
        color = mix(color, electricBlue, rim * 0.42 + liquid * 0.06);
        color = mix(color, iceBlue, pow(facing, 4.0) * 0.16);
        color = mix(color, gold, stripe * 0.08);
        color += stripe * vec3(0.010, 0.060, 0.120);

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
      color: 0xdfe8f3,
      transparent: true,
      opacity: 0.52,
      wireframe: true,
    });
    const fishnet = new THREE.Mesh(new THREE.SphereGeometry(1.735, 64, 64), fishnetMaterial);
    root.add(fishnet);

    const goldNetMaterial = new THREE.MeshBasicMaterial({
      color: 0xd8ba68,
      transparent: true,
      opacity: 0.24,
      wireframe: true,
    });
    const goldNet = new THREE.Mesh(new THREE.SphereGeometry(1.755, 32, 32), goldNetMaterial);
    root.add(goldNet);

    const haloMaterial = new THREE.MeshBasicMaterial({
      color: 0x42bdfd,
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
          color: i % 2 ? 0xd8ba68 : 0x1685ff,
          transparent: true,
          opacity: i % 2 ? 0.18 : 0.16,
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
    const dotMaterial = new THREE.MeshBasicMaterial({ color: 0x005ee8, transparent: true, opacity: 0.88 });
    const lineMaterial = new THREE.LineBasicMaterial({ color: 0x39a9ff, transparent: true, opacity: 0.20 });
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
      new THREE.PointsMaterial({ color: 0x43b8ff, size: 0.012, transparent: true, opacity: 0.38, depthWrite: false })
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
        haloMaterial.opacity = 0.085 + thinkingMix * 0.045;
        fishnetMaterial.opacity = 0.52 + thinkingMix * 0.12;
        goldNetMaterial.opacity = 0.24 + thinkingMix * 0.12;

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

function addTimeline(event) {
  timelineEvents.push(event);
  const item = document.createElement("li");
  const title = document.createElement("div");
  title.className = "fw-semibold";
  title.textContent = `${event.event_type}: ${event.message}`;
  const detail = document.createElement("pre");
  detail.className = "small text-secondary mb-0";
  detail.textContent = JSON.stringify(event.payload || {}, null, 2);
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

async function refreshRun(runId) {
  const response = await fetch(`/api/runs/${runId}`);
  if (!response.ok) return;
  const run = await response.json();
  setStatus(run.status);
  if (run.final_report) {
    finalReport.textContent = run.final_report;
  }
  await refreshArtifacts(runId);
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

releaseNoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  timelineEvents = [];
  timeline.innerHTML = "";
  artifactLinks.innerHTML = "";
  finalReport.textContent = "Generating release-note draft...";
  setCopyStatus("");
  setStatus("created");
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

  const response = await fetch("/api/release-notes", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    finalReport.textContent = `Failed to start release-note run: ${response.status}`;
    setStatus("failed");
    setProgressVisualState("failed");
    return;
  }

  const created = await response.json();
  const source = new EventSource(created.events_url);

  source.onmessage = async (message) => {
    const eventData = JSON.parse(message.data);
    addTimeline(eventData);
    if (eventData.event_type === "artifact_created") {
      if (eventData.payload?.preview) {
        finalReport.textContent = eventData.payload.preview;
      }
      await refreshArtifacts(created.run_id);
    }
    if (eventData.event_type === "run_completed" || eventData.event_type === "run_failed") {
      setProgressVisualState(eventData.event_type === "run_completed" ? "complete" : "failed");
      if (eventData.payload?.artifact) {
        renderArtifactLinks(eventData.payload.artifacts || [eventData.payload.artifact]);
      }
      source.close();
      await refreshRun(created.run_id);
    }
  };

  source.onerror = () => {
    setProgressVisualState("failed");
    source.close();
    refreshRun(created.run_id);
  };
});

setProgressVisualState("idle");
initReleaseSphere();
