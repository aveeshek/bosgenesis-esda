# Phase 7 Product Journey E2E Evidence

## Result

- Suite: Phase 7 Product Journey E2E
- Result: 11 passed, 0 failed
- Browser: Microsoft Edge through Playwright's Chromium API
- Evidence manifest: `product-journey-results.json`
- Screenshots: eight PNG artifacts covering list and detail pages at four viewport sizes

## Covered Journeys

1. List search, filters, sorting, pagination, and historical-run reopening.
2. Direct detail and selected-tab links after refresh and a new page load.
3. Progressive evidence availability while a twin is generating.
4. Green decision to execution handoff.
5. Amber decision through approval, detail return, and execution handoff.
6. Red decision block, corrected bundle identity, and regeneration.
7. Stale and material-drift regeneration.
8. Forward execution, rollback evidence, and cleanup linkage.
9. Browser Back and Forward across list, detail, and tab history entries.
10. Desktop, laptop, tablet, and mobile list/detail screenshots.
11. Keyboard navigation, visible focus, form labels, textual status, and reduced motion.

## Viewports

| View | Width | Height |
|---|---:|---:|
| Desktop | 1440 | 1000 |
| Laptop | 1280 | 800 |
| Tablet | 820 | 1180 |
| Mobile | 390 | 844 |

Each screenshot step asserts that the document does not overflow horizontally. The
accessibility journey additionally checks the skip link, row keyboard activation,
Arrow/Home/End tab navigation, a 3px visible focus outline, associated form labels,
text inside status cells, and reduced animation/transition durations.

## Reproduce

```powershell
$env:NODE_PATH="<workspace-node-modules>;<workspace-pnpm-node-modules>"
node backend/tests/browser/digital_twin_product_journey.e2e.cjs
```

The runner starts an isolated static HTTP server, uses browser-only fixture data,
captures the screenshots, writes the JSON result, and shuts the server down.
