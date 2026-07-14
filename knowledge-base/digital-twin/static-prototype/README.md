# Digital Twin Browser Prototype

This directory contains the Phase 1 static shell and the Phase 2 browser-only JavaScript mock for the ESDA Namespace Digital Twin experience.

Open these files directly in a browser:

- digital-twins.html - adapter-driven list, filters, mock scenarios, pagination, progress, and restoration
- digital-twin-detail.html - lazy detail cockpit with all eleven frozen evidence tabs
- bundle-execution-twin-gate.html - compact decision gate with Green, Amber, Red, stale, expired, and generating behavior

## Phase 2 Data Boundary

All records come from the versioned fixtures/v1/twin-fixtures.js package through TwinDataAdapter and BrowserFixtureTwinAdapter. Components do not read fixture objects directly.

The prototype does not contact ESDA, PostgreSQL, GPT, MCP servers, Kubernetes, Helm, or remote asset hosts. It contains no HTTP client. Configurable deterministic latency makes loading states visible without a server.

## Browser-Only Restoration

Phase 2 stores generated fixture runs and the active-run pointer in localStorage:

- esda.digital-twin.mock-history.v1
- esda.digital-twin.active-run.v1
- esda.digital-twin.response-mode.v1

Only active fixture runs restore automatically. Terminal evidence opens only when the user explicitly selects it from the list. The development-only **Clear mock history** command removes these values.

localStorage is a Phase 2 demonstration mechanism only. In Phase 3, the server-side mock API becomes the source of truth and these browser persistence keys must be removed from application behavior.

## Fixture Scenarios

The scenario selector covers Green, Amber, Red, generating, failed dry-run, stale, drifted, superseded, missing optional evidence, missing historical evidence, 500-plus delta rows, 300-plus graph nodes, and a long audit timeline.

The response selector separately demonstrates success, partial, empty, forced-stale, and retryable failed list responses.

## Keyboard Review

- Use Tab to move through controls.
- Use Left/Right, Home, and End within the detail tab list.
- Use Enter or Space to open a selected list row.
- Use Escape to close profile menus and evidence dialogs.
- Narrow layouts expose a keyboard-operable navigation menu and horizontally scrollable evidence tabs.
