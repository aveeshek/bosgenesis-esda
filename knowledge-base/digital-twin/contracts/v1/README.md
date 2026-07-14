# Digital Twin Contract Package v1

This directory is the Phase 0 machine-readable contract for the Namespace Readiness Twin UI.

- `contract-manifest.json` freezes navigation, routes, query keys, tab order, and schema mapping.
- `schemas/common.schema.json` contains shared deterministic types.
- `schemas/twin-list.schema.json` defines the list endpoint and row projection.
- `schemas/twin-detail.schema.json` defines the detail header and sticky summary.
- `schemas/action-eligibility.schema.json` defines backend-authored UI actions.
- `schemas/safe-explanation.schema.json` defines safe optional SIGMA 5 PRO explanation blocks.
- `schemas/event-timeline.schema.json` defines lifecycle events and Audit Timeline records.
- `schemas/tabs/` contains one response schema for each of the eleven frozen tabs.

All payloads use `schema_version: "1.0.0"`. Fixture adapters, mock HTTP routes, and real routes must return the same names and enums. A breaking change creates `contracts/v2`; it does not rewrite `v1` in place.

Run the contract lint with:

```powershell
python -m pytest backend/tests/test_digital_twin_phase0_contracts.py
```

This Phase 0 package freezes contracts only. It does not add routes, pages, server calls, GPT calls, MCP calls, or database behavior.

