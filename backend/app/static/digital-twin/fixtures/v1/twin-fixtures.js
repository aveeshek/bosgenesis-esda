(function () {
  "use strict";

  var VERSION = "1.0.0";
  var BASE_TIME = Date.UTC(2026, 6, 14, 14, 0, 0);

  function iso(minutesAgo) {
    return new Date(BASE_TIME - minutesAgo * 60000).toISOString();
  }

  function action(code, label, enabled, reason, confirmation) {
    return {
      code: code,
      label: label,
      enabled: enabled,
      visible: true,
      method: code === "open_twin" || code.indexOf("download") === 0 ? "GET" : "POST",
      href: null,
      reason_code: enabled ? "eligible" : "not_eligible",
      disabled_reason: enabled ? null : reason,
      requires_confirmation: Boolean(confirmation)
    };
  }

  function makeTwin(config) {
    var lifecycle = config.lifecycle || config.decision;
    var isFinal = ["green", "amber", "red", "superseded"].indexOf(config.decision) >= 0;
    var approvalStatus = config.approval || (config.decision === "amber" ? "required" : "not_required");
    var executionStatus = config.execution || "unlinked";
    var freshnessStatus = config.freshness || "fresh";
    var ageMinutes = config.ageMinutes == null ? 18 : config.ageMinutes;
    var actions = [
      action("open_twin", "Open Twin", true, null, false),
      action("regenerate", "Regenerate", lifecycle !== "generating", "Generation is already active.", true),
      action("download_report", "Download Report", isFinal, "A final decision report is not available.", false),
      action(
        "open_execution",
        "Open Execution",
        executionStatus !== "unlinked",
        "No Bundle Execution is linked to this decision.",
        false
      ),
      action(
        "request_approval",
        "Request Approval",
        config.decision === "amber" && approvalStatus === "required" && freshnessStatus === "fresh",
        "Approval is available only for a fresh Amber decision.",
        true
      )
    ];

    return {
      schema_version: VERSION,
      scenario_id: config.scenario,
      twin_id: config.id,
      display_name: config.name,
      decision_version: config.version || 1,
      decision: config.decision,
      decision_is_final: isFinal,
      lifecycle_status: lifecycle,
      visible_lifecycle: lifecycle,
      risk: { level: config.riskLevel || "medium", score: config.riskScore == null ? 50 : config.riskScore },
      autonomy_eligibility: config.eligibility || "not_eligible",
      recommended_action: config.recommendedAction || "Review the available evidence.",
      freshness: {
        status: freshnessStatus,
        captured_at: iso(ageMinutes),
        expires_at: iso(ageMinutes - 120),
        superseded_by: config.supersededBy || null,
        message: config.freshnessMessage || (freshnessStatus === "fresh" ? "Evidence is fresh." : "Regenerate before execution.")
      },
      target: {
        cluster_id: config.clusterId || "cluster_lab_01",
        cluster_name: config.cluster || "BOS Genesis Lab",
        namespace: config.namespace || "agent-testing"
      },
      bundle: {
        bundle_id: config.bundleId || ("mop_" + config.id.replace("twin_", "")),
        bundle_name: config.bundle || "mop-bundle.zip",
        bundle_hash: config.bundleHash || "a6f90e64c2f988feb2d2b1aaf5f3857da64955c61fa0b7ea30887a7afba3f41d",
        release_version: config.release || "v1.0.0",
        open_href: null
      },
      created_by: config.creatorId || "usr_admin",
      created_by_display: config.creator || "admin",
      created_at: iso(config.createdMinutes == null ? ageMinutes + 8 : config.createdMinutes),
      updated_at: iso(ageMinutes),
      relationships: {
        dry_run_job_id: config.dryRunId || null,
        approval_id: config.approvalId || null,
        approval_status: approvalStatus,
        execution_id: config.executionId || null,
        execution_status: executionStatus,
        used_for_execution: executionStatus === "completed"
      },
      top_reasons: config.reasons || [],
      actions: actions,
      optional_states: config.optionalStates || {},
      prior_decision: config.priorDecision || null,
      progress_index: config.progressIndex || 0
    };
  }

  var scenarios = [
    {
      id: "green-helm",
      label: "Green low-risk Helm change",
      description: "Fresh namespace-scoped Helm update with complete evidence and high rollback confidence."
    },
    {
      id: "amber-pvc-rbac",
      label: "Amber PVC/RBAC approval",
      description: "Storage and Role changes require a bounded human approval."
    },
    {
      id: "red-cluster-secret",
      label: "Red cluster-scope and Secret",
      description: "Forbidden cluster-scope mutation and Secret data block execution."
    },
    { id: "generating", label: "Generating twin", description: "Progressive evidence availability before a final decision." },
    { id: "failed-dry-run", label: "Failed dry-run", description: "Authoritative dry-run failed safely." },
    { id: "stale-snapshot", label: "Stale live snapshot", description: "Evidence freshness is outside the configured window." },
    { id: "material-drift", label: "Material drift", description: "Live drift invalidates the previous execution decision." },
    { id: "superseded", label: "Superseded decision", description: "A newer decision version replaces historical evidence." },
    { id: "missing-replay", label: "Missing MoP replay", description: "Optional replay evidence is explicitly Not Run." },
    { id: "missing-runtime", label: "Missing runtime history", description: "Historical runtime evidence is Not Available." },
    { id: "large-delta", label: "Large release delta", description: "A deterministic 520-row resource delta." },
    { id: "large-graph", label: "Large dependency graph", description: "A deterministic 320-node graph fixture." },
    { id: "long-audit", label: "Long audit timeline", description: "A deterministic 180-event audit history." }
  ];

  var twins = [
    makeTwin({
      scenario: "green-helm",
      id: "twin_signal_scout_001",
      name: "Signal Scout - signoz to agent-testing",
      decision: "green",
      lifecycle: "green",
      riskLevel: "low",
      riskScore: 18,
      eligibility: "eligible",
      recommendedAction: "Proceed through the normal Bundle Execution approval controls.",
      bundle: "signoz-mop-bundle.zip",
      release: "signoz-0.122.0",
      execution: "completed",
      executionId: "mopx_7f4a",
      dryRunId: "job_green_001",
      ageMinutes: 8,
      creator: "admin",
      reasons: [
        { code: "POLICY_PASS", summary: "All mutations are namespace-scoped.", severity: "low", tab_slug: "policy" },
        { code: "DRY_RUN_PASS", summary: "The authoritative dry-run accepted every operation.", severity: "low", tab_slug: "dry-run" }
      ]
    }),
    makeTwin({
      scenario: "amber-pvc-rbac",
      id: "twin_beacon_pilot_002",
      name: "Beacon Pilot - payments 4.8 to agent-testing",
      decision: "amber",
      lifecycle: "amber",
      riskLevel: "medium",
      riskScore: 63,
      eligibility: "approval_required",
      recommendedAction: "Review PVC and namespace Role findings, then request approval.",
      bundle: "payments-4.8-mop-bundle.zip",
      release: "4.8.0",
      creator: "release.bot",
      creatorId: "usr_release_bot",
      dryRunId: "job_amber_002",
      ageMinutes: 21,
      reasons: [
        { code: "PVC_CHANGE_REQUIRES_REVIEW", summary: "The storage class changes from standard to fast-rwo.", severity: "medium", tab_slug: "rollback", finding_id: "finding_pvc_002" },
        { code: "RBAC_EXPANSION", summary: "A namespace Role adds the watch verb.", severity: "medium", tab_slug: "policy", finding_id: "finding_rbac_002" }
      ],
      optionalStates: { "mop-replay": "not_run", "release-note-validation": "not_run" }
    }),
    makeTwin({
      scenario: "red-cluster-secret",
      id: "twin_core_gateway_003",
      name: "Core Gateway 9.3 - payment-core",
      decision: "red",
      lifecycle: "red",
      riskLevel: "critical",
      riskScore: 91,
      eligibility: "blocked",
      recommendedAction: "Remove cluster-scoped mutation and Secret data, then generate a new bundle.",
      cluster: "Production Central",
      clusterId: "cluster_prod_01",
      namespace: "payment-core",
      bundle: "gateway-9.3-mop-bundle.zip",
      release: "9.3.0",
      ageMinutes: 34,
      reasons: [
        { code: "CLUSTER_SCOPE_FORBIDDEN", summary: "ClusterRole creation is outside the configured ODD.", severity: "critical", tab_slug: "policy" },
        { code: "SECRET_DATA_DETECTED", summary: "An unredacted Secret data pattern was detected.", severity: "critical", tab_slug: "policy" }
      ]
    }),
    makeTwin({
      scenario: "generating",
      id: "twin_signoz_upgrade_004",
      name: "Signoz Upgrade - evidence collection",
      decision: "pending",
      lifecycle: "generating",
      riskLevel: "preliminary",
      riskScore: 47,
      eligibility: "not_available",
      recommendedAction: "Wait for deterministic evaluation to finish.",
      namespace: "signoz",
      bundle: "signoz-upgrade-mop-bundle.zip",
      release: "v0.123.0",
      ageMinutes: 3,
      progressIndex: 2
    }),
    makeTwin({
      scenario: "failed-dry-run",
      id: "twin_nginx_canary_005",
      name: "Nginx Canary - failed dry-run",
      decision: "failed",
      lifecycle: "failed",
      riskLevel: "not_calculated",
      riskScore: null,
      eligibility: "not_available",
      recommendedAction: "Correct the immutable selector conflict and regenerate.",
      bundle: "nginx-canary-mop-bundle.zip",
      release: "18.2.1",
      ageMinutes: 57,
      optionalStates: { "dry-run": "failed" }
    }),
    makeTwin({
      scenario: "stale-snapshot",
      id: "twin_telemetry_store_006",
      name: "Telemetry Store - stale snapshot",
      decision: "amber",
      lifecycle: "amber",
      riskLevel: "medium",
      riskScore: 58,
      eligibility: "stale",
      freshness: "stale",
      freshnessMessage: "The live snapshot is 28 hours old.",
      ageMinutes: 1680,
      namespace: "observability",
      bundle: "telemetry-store-mop-bundle.zip",
      release: "2.6.4",
      creator: "ops.user"
    }),
    makeTwin({
      scenario: "material-drift",
      id: "twin_inventory_api_007",
      name: "Inventory API - material drift",
      decision: "amber",
      lifecycle: "amber",
      riskLevel: "high",
      riskScore: 72,
      eligibility: "drifted",
      freshness: "drifted",
      freshnessMessage: "A Deployment image and Role changed after the decision.",
      namespace: "agent-testing",
      bundle: "inventory-api-mop-bundle.zip",
      release: "7.2.0",
      ageMinutes: 48,
      optionalStates: { drift: "stale" }
    }),
    makeTwin({
      scenario: "superseded",
      id: "twin_signal_scout_000",
      name: "Signal Scout - historical decision",
      decision: "superseded",
      lifecycle: "superseded",
      riskLevel: "low",
      riskScore: 24,
      eligibility: "superseded",
      freshness: "expired",
      supersededBy: "twin_signal_scout_001",
      bundle: "signoz-mop-bundle.zip",
      release: "signoz-0.122.0",
      execution: "completed",
      executionId: "mopx_6c21",
      ageMinutes: 2400
    }),
    makeTwin({
      scenario: "missing-replay",
      id: "twin_missing_replay_008",
      name: "Payments Worker - replay unavailable",
      decision: "green",
      riskLevel: "low",
      riskScore: 29,
      eligibility: "eligible",
      bundle: "payments-worker-mop-bundle.zip",
      release: "5.1.0",
      ageMinutes: 66,
      optionalStates: { "mop-replay": "not_run" }
    }),
    makeTwin({
      scenario: "missing-runtime",
      id: "twin_missing_runtime_009",
      name: "New Service - no runtime history",
      decision: "amber",
      riskLevel: "medium",
      riskScore: 54,
      eligibility: "approval_required",
      bundle: "new-service-mop-bundle.zip",
      release: "1.0.0",
      ageMinutes: 73,
      optionalStates: { "runtime-behavior": "not_available" }
    }),
    makeTwin({
      scenario: "large-delta",
      id: "twin_large_delta_010",
      name: "Platform Suite - large delta",
      decision: "amber",
      riskLevel: "high",
      riskScore: 77,
      eligibility: "approval_required",
      bundle: "platform-suite-mop-bundle.zip",
      release: "12.0.0",
      ageMinutes: 81
    }),
    makeTwin({
      scenario: "large-graph",
      id: "twin_large_graph_011",
      name: "Observability Mesh - large graph",
      decision: "green",
      riskLevel: "medium",
      riskScore: 42,
      eligibility: "eligible",
      namespace: "observability",
      bundle: "observability-mesh-mop-bundle.zip",
      release: "3.4.0",
      ageMinutes: 92
    }),
    makeTwin({
      scenario: "long-audit",
      id: "twin_long_audit_012",
      name: "Core Banking - long audit",
      decision: "amber",
      riskLevel: "medium",
      riskScore: 61,
      eligibility: "approval_required",
      namespace: "banking-core",
      bundle: "banking-core-mop-bundle.zip",
      release: "8.7.2",
      ageMinutes: 104
    })
  ];

  function deltaRows(count) {
    var kinds = ["Deployment", "Service", "ConfigMap", "StatefulSet", "PersistentVolumeClaim", "Role"];
    var changes = ["modified", "unchanged", "created", "modified"];
    var rows = [];
    for (var index = 0; index < count; index += 1) {
      rows.push({
        id: "delta-" + String(index + 1).padStart(3, "0"),
        resource: kinds[index % kinds.length].toLowerCase() + "/component-" + String(index + 1).padStart(3, "0"),
        kind: kinds[index % kinds.length],
        change: changes[index % changes.length],
        before: index % 3 === 0 ? "v1 / 2 replicas" : "present",
        after: index % 3 === 0 ? "v2 / 3 replicas" : "updated",
        impact: index % 17 === 0 ? "operator review" : "bounded"
      });
    }
    return rows;
  }

  function graphFixture(count) {
    var nodes = [];
    var edges = [];
    for (var index = 0; index < count; index += 1) {
      nodes.push({ id: "node-" + index, kind: index % 4 === 0 ? "Service" : index % 4 === 1 ? "Deployment" : index % 4 === 2 ? "ConfigMap" : "Pod", name: "component-" + index, impact: index % 23 === 0 ? "review" : "normal" });
      if (index > 0) edges.push({ source: "node-" + Math.floor((index - 1) / 2), target: "node-" + index, relationship: "depends_on" });
    }
    return { nodes: nodes, edges: edges };
  }

  function auditEvents(count) {
    var types = ["generation_requested", "evidence_captured", "policy_evaluated", "dry_run_completed", "decision_recorded"];
    var events = [];
    for (var index = 0; index < count; index += 1) {
      events.push({
        event_id: "evt_" + String(index + 1).padStart(4, "0"),
        event_type: types[index % types.length],
        created_at: iso(104 + index),
        actor: index % 5 === 0 ? "admin" : "deterministic-twin-engine",
        summary: "Safe audit event " + (index + 1) + " recorded for decision provenance.",
        correlation_id: "corr_" + Math.floor(index / 5)
      });
    }
    return events;
  }

  var commonTabs = {
    overview: {
      title: "Overview",
      state: "available",
      kind: "overview",
      summary: "Deterministic release decision across policy, evidence, dry-run fidelity, rollback, freshness, and drift.",
      metrics: [
        { label: "Decision", value: "Amber", note: "Conditionally eligible" },
        { label: "Risk Score", value: "63", note: "Medium" },
        { label: "Evidence", value: "92%", note: "11 of 12 signals" },
        { label: "Autonomy", value: "L3", note: "Human approval gate" }
      ],
      reasons: [
        { title: "PVC storage class change", detail: "Two stateful resources require a confirmed migration window.", tab: "rollback" },
        { title: "RBAC Role expansion", detail: "Three verbs are added inside the target namespace only.", tab: "policy", finding: "finding_rbac_002" },
        { title: "Medium rollback confidence", detail: "Data restore has not been rehearsed.", tab: "rollback" }
      ]
    },
    "release-delta": {
      title: "Release Delta Twin",
      state: "available",
      kind: "delta",
      summary: "Structured resource differences between the observed release and the proposed bundle.",
      rows: deltaRows(18),
      total_rows: 18,
      diff: { resource: "Deployment/signoz-api", before: "image: v0.121.1\nreplicas: 2\nperiodSeconds: 15", after: "image: v0.122.0\nreplicas: 3\nperiodSeconds: 10" }
    },
    "dependency-graph": {
      title: "Dependency Graph Twin",
      state: "available",
      kind: "graph",
      summary: "Namespace-scoped topology with declared and evidence-backed relationships.",
      graph: graphFixture(18)
    },
    policy: {
      title: "Policy Twin",
      state: "available",
      kind: "findings",
      summary: "Deterministic policy findings inside the configured ODD.",
      findings: [
        { id: "finding_pvc_002", severity: "review", code: "POL-STORAGE-014", title: "PVC class change", detail: "Storage migration confirmation is required." },
        { id: "finding_rbac_002", severity: "review", code: "POL-RBAC-008", title: "Role verb expansion", detail: "The watch verb is added inside agent-testing." },
        { id: "finding_ha_003", severity: "review", code: "POL-HA-003", title: "Replica increase", detail: "Capacity acknowledgment is required above two replicas." }
      ],
      passed_groups: ["Namespace boundary", "Secret handling", "Image provenance", "Destructive commands"]
    },
    "dry-run": {
      title: "Dry-run / Diff Twin",
      state: "available",
      kind: "dry-run",
      summary: "Authoritative MoP Execution Agent dry-run evidence. No second simulation path is used.",
      metrics: [{ label: "Commands", value: "27" }, { label: "Warnings", value: "2" }, { label: "Fidelity", value: "94%" }, { label: "Duration", value: "41s" }],
      observations: ["Namespace agent-testing observed.", "Helm template rendered 38 resources.", "Server dry-run accepted Deployment/signoz-api.", "PVC storage class warning recorded.", "Namespace Role expansion warning recorded."]
    },
    rollback: {
      title: "Rollback Twin",
      state: "available",
      kind: "rollback",
      summary: "Rollback confidence is evaluated separately from release risk.",
      confidence: 68,
      steps: ["Freeze traffic changes.", "Run Helm rollback to revision 7.", "Restore workload scale to two replicas.", "Retain PVCs; data restoration remains separately approved."],
      evidence: [{ asset: "Helm release", status: "high", gap: "None" }, { asset: "Deployment", status: "high", gap: "None" }, { asset: "PVC data", status: "medium", gap: "No restore rehearsal" }, { asset: "External route", status: "medium", gap: "DNS timing unknown" }]
    },
    drift: {
      title: "Drift Twin",
      state: "available",
      kind: "drift",
      summary: "Latest allowed live snapshot compared with decision evidence.",
      snapshot_age: "21m",
      material: false,
      changes: [{ resource: "Pod/signoz-api-7f4", change: "Runtime UID rotated", materiality: "expected" }, { resource: "EndpointSlice/signoz", change: "Endpoint addresses updated", materiality: "transient" }, { resource: "Event/signoz-api", change: "Readiness warning cleared", materiality: "positive" }]
    },
    "mop-replay": {
      title: "MoP Replay Twin",
      state: "not_run",
      kind: "state",
      summary: "No compatible historical execution exists for this bundle lineage and target topology.",
      reason: "Optional replay evidence is explicitly Not Run and does not fail the decision."
    },
    "runtime-behavior": {
      title: "Runtime Behavior Twin",
      state: "available",
      kind: "runtime",
      summary: "Rules-first historical signals; model text cannot assign risk or eligibility.",
      signals: [{ label: "Readiness recovery", score: 88, detail: "P95 recovery: 71 seconds" }, { label: "Error rate", score: 95, detail: "Below release SLO" }, { label: "CPU headroom", score: 74, detail: "26% peak headroom" }, { label: "Scale evidence", score: 34, detail: "No prior 2 to 3 replica transition" }]
    },
    "release-note-validation": {
      title: "Release Note Validation Twin",
      state: "not_run",
      kind: "state",
      summary: "No release-note artifact is linked to this bundle checksum.",
      reason: "Bundle evidence remains available; release-note validation is optional."
    },
    audit: {
      title: "Audit Timeline",
      state: "available",
      kind: "audit",
      summary: "Safe event summaries, contract versions, actor identity, and evidence hashes.",
      events: auditEvents(25),
      total_events: 25
    }
  };

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function tabFor(twin, slug) {
    var tab = clone(commonTabs[slug]);
    var override = twin.optional_states && twin.optional_states[slug];
    if (override) {
      tab.state = override;
      if (override === "failed") tab.summary = "The authoritative operation failed safely. Retry is available after correction.";
      if (override === "not_available") tab.summary = "Historical runtime evidence is not available for this new workload.";
      if (override === "stale") tab.summary = "Material drift invalidated the evidence used by the prior decision.";
    }
    if (twin.scenario_id === "large-delta" && slug === "release-delta") {
      tab.rows = deltaRows(520);
      tab.total_rows = 520;
    }
    if (twin.scenario_id === "large-graph" && slug === "dependency-graph") tab.graph = graphFixture(320);
    if (twin.scenario_id === "long-audit" && slug === "audit") {
      tab.events = auditEvents(180);
      tab.total_events = 180;
    }
    if (twin.scenario_id === "material-drift" && slug === "drift") {
      tab.material = true;
      tab.snapshot_age = "48m";
      tab.changes.unshift({ resource: "Deployment/inventory-api", change: "Image digest changed after decision", materiality: "material" });
    }
    if (twin.scenario_id === "red-cluster-secret" && slug === "policy") {
      tab.findings = [
        { id: "finding_cluster", severity: "block", code: "POL-ODD-001", title: "Cluster-scoped mutation", detail: "ClusterRole creation is forbidden." },
        { id: "finding_secret", severity: "block", code: "POL-SECRET-002", title: "Secret data detected", detail: "Unredacted Secret material is present." }
      ];
    }
    return tab;
  }

  window.ESDA_TWIN_FIXTURES_V1 = Object.freeze({
    version: VERSION,
    generated_at: new Date(BASE_TIME).toISOString(),
    scenarios: scenarios,
    twins: twins,
    tab_slugs: ["overview", "release-delta", "dependency-graph", "policy", "dry-run", "rollback", "drift", "mop-replay", "runtime-behavior", "release-note-validation", "audit"],
    progress_states: ["requested", "generating", "awaiting_dry_run", "decision_calculating", "green"],
    response_modes: ["success", "partial", "empty", "stale", "failed"],
    tabFor: tabFor,
    clone: clone,
    makeTwin: makeTwin
  });
})();
