# Namespace Readiness Twin Phase 0 UX Contract

**Contract version:** `1.0.0`  
**Status:** Frozen for product, frontend, and backend review  
**Machine-readable manifest:** `contracts/v1/contract-manifest.json`  
**JSON Schema root:** `contracts/v1/schemas/`  
**Breaking-change rule:** A breaking field, route, state, label, tab-order, or ownership change requires a new major contract directory.

## 1. Decision Ownership

The MoP Execution Agent is the deterministic authority for lifecycle, Green/Amber/Red decision, policy verdict, evidence completeness, change risk, risk score, freshness, action eligibility, and immutable decision versions. ESDA renders and links those facts. Browser JavaScript never calculates or upgrades them. SIGMA 5 PRO may explain redacted structured evidence, but its output cannot modify an authoritative field or unlock an action.

| Layer | Owns | Must not do |
|---|---|---|
| MoP Execution Agent | Twin lifecycle, evidence, policy, risk, freshness, decision, eligibility | Delegate a decision to GPT or browser code |
| ESDA backend | Authentication, gateway view models, base-path links, safe display joins, approval capture | Recalculate policy, risk, decision, or eligibility |
| ESDA browser | Rendering, URL state, local loading state, accessible interaction | Infer enabled actions or final decisions from tab contents |
| SIGMA 5 PRO | Safe explanation of redacted deterministic facts | Approve, mutate facts, expose hidden reasoning, invent evidence |

## 2. Navigation and Routes

The top navigation order is frozen:

1. LLM Chat
2. Health Check
3. Release Notes
4. Bundle Generation
5. Digital Twins
6. Bundle Execution
7. Environment Chat
8. Activity
9. Approvals
10. L4 Audit

`Digital Twins` is always between `Bundle Generation` and `Bundle Execution`.

Canonical internal routes:

| Surface | Route |
|---|---|
| List workspace | `/digital-twins` |
| Detail cockpit | `/digital-twins/{twin_id}` |
| Compact execution gate | `/mop-execution?twin_id={twin_id}` |
| Approval relationship | `/approvals?twin_id={twin_id}` |
| Activity relationship | `/activity?workflow=namespace_twin&twin_id={twin_id}` |

Routes are base-path agnostic. With `APP_BASE_PATH=/esda`, link generation prefixes `/esda`; route handlers and stored relative links do not hard-code that prefix.

Supported detail deep links:

```text
/digital-twins/{twin_id}?tab=overview
/digital-twins/{twin_id}?tab=release-delta
/digital-twins/{twin_id}?tab=dependency-graph&resource={resource_identity}
/digital-twins/{twin_id}?tab=policy&finding={finding_id}
/digital-twins/{twin_id}?tab=dry-run&observation={observation_id}
/digital-twins/{twin_id}?tab=rollback
/digital-twins/{twin_id}?tab=drift&resource={resource_identity}
/digital-twins/{twin_id}?tab=mop-replay
/digital-twins/{twin_id}?tab=runtime-behavior
/digital-twins/{twin_id}?tab=release-note-validation&claim={claim_id}
/digital-twins/{twin_id}?tab=audit&event={event_id}
```

Unknown tab slugs fall back to `overview` and replace the invalid query value. Unknown resource, finding, observation, claim, or event IDs keep the selected tab open and show a scoped `Item unavailable` notice.

## 3. Digital Twins List Contract

### 3.1 Columns

Desktop columns and order are frozen:

1. Twin Run ID / display name
2. Decision
3. Risk Score
4. Lifecycle
5. Freshness
6. Target Cluster / Namespace
7. MoP Bundle / Release Version
8. Created By / Created At
9. Linked Execution
10. Actions

The first column and actions remain visible during horizontal scrolling. Tablet/mobile uses the same field order in a stacked row; it does not drop safety fields.

### 3.2 Filters and URL Query

| UI control | Query key | Values/format |
|---|---|---|
| Search | `q` | Trimmed UTF-8 string, maximum 200 characters |
| Decision | `decision` | Comma-separated `green,amber,red,pending,none` |
| Lifecycle | `lifecycle` | Comma-separated backend lifecycle values |
| Freshness | `freshness` | Comma-separated freshness values |
| Target cluster | `cluster` | Opaque cluster ID |
| Target namespace | `namespace` | Exact namespace |
| Bundle/release | `bundle` | Bundle ID, hash prefix, or release text |
| Created by | `created_by` | Opaque user ID or safe display name |
| Created date | `created_from`, `created_to` | RFC 3339 timestamp |
| Linked execution | `linked_execution` | `all`, `linked`, `unlinked`, `used` |
| Sort | `sort` | Frozen sort field |
| Direction | `direction` | `asc` or `desc` |
| Cursor | `cursor` | Opaque backend token |
| Page size | `limit` | `25`, `50`, or `100`; default `25` |

Frozen sort fields: `created_at`, `updated_at`, `risk_score`, `decision`, `freshness`, `target_namespace`, `release_version`. Default sort is `created_at desc`. Filter changes clear `cursor`. Unknown query values are ignored with a non-blocking notice and removed when the URL is next written. Browser Back/Forward restores filters, sort, cursor, and result focus.

Filtering, sorting, and cursor pagination are server-side once HTTP integration begins. The browser-only mock must reproduce the same behavior through its adapter without changing the component contract.

### 3.3 Row Actions

Frozen action order:

1. Open
2. Regenerate
3. Download Report
4. Open Bundle
5. Open Execution
6. Request Approval

The backend returns every action's `enabled`, `reason_code`, and safe `disabled_reason`. The browser may hide only actions marked `visible=false`; it never infers eligibility. A disabled control remains discoverable and exposes its explanation by tooltip and adjacent accessible description.

### 3.4 List States

- `loading`: skeleton rows preserve table geometry.
- `available`: rows and result count are shown.
- `empty`: no twins exist; show **Generate Digital Twin**.
- `no_results`: filters match no rows; show **Clear Filters**.
- `partial`: retain successful rows and show a scoped warning.
- `failed`: retain previously loaded rows where possible and show **Retry**.
- `unauthorized`: show a scoped access message without twin metadata.

Active rows use bounded polling. Terminal rows change only after explicit refresh or a backend event for that row.

## 4. Detail Cockpit Contract

### 4.1 Header

The title is `Digital Twin: {bundle_or_release} -> {target_namespace}`. Header facts remain distinct:

- lifecycle badge;
- final or pending decision badge;
- change-risk level and score;
- autonomy eligibility;
- recommended next action.

Frozen action order:

1. Generate/Regenerate Twin
2. Open MoP Bundle
3. Open/Start Bundle Execution
4. Request/Open Approval
5. Download Report
6. Export Evidence JSON
7. Cancel Generation, when returned as eligible

Approve and Reject appear only on the authorized Approvals surface.

### 4.2 Sticky Summary

Frozen field order:

1. Target cluster
2. Target namespace
3. Bundle ID and bundle hash
4. Twin ID and decision version
5. Release version
6. Created by
7. Created at and updated at
8. Evidence freshness and expiry
9. Linked dry-run job
10. Linked approval
11. Linked execution

The summary stays below the application header. On narrow screens the first six fields form two rows and the remainder moves into **More details**. Lifecycle, decision, freshness, approval, and execution are never merged into one badge.

### 4.3 Lifecycle Projection

| Backend state | Visible label | Decision display | Terminal |
|---|---|---|---|
| `requested`, `bundle_validating` | Preparing | Pending | No |
| `normalizing`, `snapshot_collecting`, `rendering` | Generating | Pending | No |
| `graph_building`, `diffing`, `policy_checking` | Analyzing | Preliminary | No |
| `awaiting_dry_run` | Waiting for Dry-run | Pending | No |
| `dry_run_evidence_attached`, `decision_calculating` | Finalizing | Pending | No |
| `green` | Ready | Green | Yes |
| `amber` | Review Required | Amber | Yes |
| `red` | Blocked | Red | Yes |
| `failed` | Failed | None or last valid historical decision | Yes |
| `cancelled` | Cancelled | None | Yes |

Preliminary states use a neutral `PRELIMINARY` label, subdued border, and no Green/Amber/Red fill. They never display **Ready**, **Review Required**, **Blocked**, **Start Execution**, or **Request Approval** unless the backend explicitly returns an eligible action.

## 5. Exact Tab Contract

The order and slugs are frozen:

| Position | Label | Slug |
|---:|---|---|
| 1 | Overview | `overview` |
| 2 | Release Delta Twin | `release-delta` |
| 3 | Dependency Graph Twin | `dependency-graph` |
| 4 | Policy Twin | `policy` |
| 5 | Dry-run / Diff Twin | `dry-run` |
| 6 | Rollback Twin | `rollback` |
| 7 | Drift Twin | `drift` |
| 8 | MoP Replay Twin | `mop-replay` |
| 9 | Runtime Behavior Twin | `runtime-behavior` |
| 10 | Release Note Validation Twin | `release-note-validation` |
| 11 | Audit Timeline | `audit` |

Every tab response and browser fixture has exactly one availability state:

| State | Required presentation |
|---|---|
| `loading` | Stable skeleton and tab-specific loading label |
| `available` | Render evidence and provenance |
| `empty` | Successful evaluation with no records; never imply `not_run` |
| `not_run` | Capability exists but has not been invoked; show allowed next action if any |
| `not_available` | Capability/evidence source is unavailable or unsupported; explain why |
| `failed` | Evaluation attempted and failed; show safe error and retry eligibility |
| `stale` | Render historical evidence read-only with capture/expiry and Regenerate action |

Blank tab panels are prohibited. Tab schemas in `contracts/v1/schemas/tabs/` freeze the data shape.

## 6. Visible Decision and Relationship Copy

### 6.1 Final Decisions

| Decision | Frozen headline | Frozen next-action copy |
|---|---|---|
| Green | `Eligible inside the configured operating boundary` | `Review the evidence and continue with the backend-authorized approval or execution action.` |
| Amber | `Human review is required before execution` | `Resolve the listed evidence or policy conditions, or request approval when eligible.` |
| Red | `Execution is blocked` | `Open blocking findings, correct the bundle or target condition, and generate a new twin.` |

Green does not mean guaranteed runtime success. It means eligible under the versioned policy, evidence, freshness, and risk rules.

### 6.2 Freshness and Historical State

| State | Frozen message |
|---|---|
| `approaching_expiry` | `Evidence is approaching its freshness limit. Complete the next authorized action before expiry.` |
| `stale` | `Evidence is stale. Approval and execution are disabled until a new twin is generated.` |
| `drifted` | `The target changed after evidence capture. Review drift and generate a new twin.` |
| `expired` | `This decision expired. It remains available for audit but cannot authorize execution.` |
| `superseded` | `A newer twin decision exists. Open the current twin to continue.` |

### 6.3 Approval and Execution Relationships

| Relationship | Label | Meaning |
|---|---|---|
| Approval required | `APPROVAL REQUIRED` | Current fresh decision requires an approval before execution. |
| Approval pending | `APPROVAL PENDING` | A linked approval request is open. |
| Approved | `APPROVED` | A linked approval matches this immutable decision version and remains valid. |
| Approval expired/rejected | `APPROVAL EXPIRED` / `APPROVAL REJECTED` | Relationship is historical and cannot unlock execution. |
| Execution linked | `LINKED EXECUTION` | An execution record references this twin. |
| Used for execution | `USED FOR EXECUTION` | This immutable decision version authorized an execution attempt. |

These labels never replace the Green/Amber/Red decision.

## 7. Compact Bundle Execution Twin Gate

Frozen field order:

1. Twin ID and decision version
2. Decision
3. Policy verdict
4. Evidence completeness
5. Freshness and expiry
6. Change risk and score
7. Top three reasons
8. Dry-run status and linked job
9. Rollback confidence
10. Drift status
11. Approval requirement/relationship

Frozen action order: **View Full Twin**, **Generate/Regenerate Twin**, **Request/Open Approval**, **Start Execution**. The backend supplies eligibility for each action.

| Gate state | Behavior |
|---|---|
| No twin | Mutation disabled; show **Generate Digital Twin**. |
| Running | Mutation disabled; show progress and **View Full Twin**. |
| Green/Fresh | Show only policy-authorized approval or execution action. |
| Amber/Fresh | Mutation disabled until a matching valid approval exists. |
| Red | Mutation and approval disabled; show blocking reason. |
| Stale, Drifted, Expired, Superseded | Mutation disabled; show **Regenerate Twin** or **Open Current Twin**. |
| Used/Archived | Mutation disabled; show linked execution and immutable report. |

## 8. Action Eligibility Contract

Eligibility is returned by the backend through `action-eligibility.schema.json`. Frozen action codes:

```text
generate_twin
regenerate_twin
open_twin
open_bundle
download_report
export_evidence
request_approval
open_approval
open_execution
start_execution
cancel_generation
refresh_drift
run_replay
```

Frozen reason-code families:

```text
eligible
not_created
running
decision_red
approval_required
approval_pending
approval_invalid
stale
drifted
expired
superseded
used_for_execution
archived
failed
unauthorized
unsupported
```

The browser renders the returned state. It may perform presentation-only checks such as disabling a button while its request is in flight, but it may not turn a backend-disabled action into an enabled action.

## 9. Safe Explanation Contract

Safe explanation blocks are optional and subordinate to deterministic facts. They include model profile, prompt version/hash, input hash, generated timestamp, safe Markdown/text, cited evidence references, and fallback status. Hidden chain-of-thought, raw prompts containing secrets, and unredacted tool payloads are forbidden.

If SIGMA 5 PRO is unavailable, the server returns deterministic operator copy with `status=fallback`. Explanation failure never changes the twin decision or makes the page fail.

## 10. Versioning and Compatibility

- All schema IDs use `https://bosgenesis.local/contracts/digital-twin/v1/...`.
- Every payload carries `schema_version: "1.0.0"`.
- Additive optional fields may be introduced in `v1` only after frontend/backend compatibility review.
- Renaming/removing fields, changing enums, changing tab order, changing ownership, or changing route/query semantics requires `v2`.
- Unknown additive fields are tolerated by clients; authoritative enums are not guessed.
- Fixtures, server mocks, and real gateway responses use the same field names.

## 11. Phase 0 Review Record

| Review | Status | Evidence |
|---|---|---|
| List layout and detail cockpit product approval | Pending | Phase 1 visual prototype is the approval surface. |
| Exact tab order and visible-state product approval | Pending | Contract frozen here; explicit product-owner sign-off still required. |
| Frontend/backend schema approval | Pending | Review `contracts/v1/` together before Phase 1 acceptance. |
| Decision ownership resolved | Complete | Section 1 freezes execution-agent authority and browser/GPT limits. |

