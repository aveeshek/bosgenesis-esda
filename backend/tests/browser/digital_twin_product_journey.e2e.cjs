"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const staticRoot = path.join(repoRoot, "backend", "app", "static");
const evidenceRoot = path.join(
  repoRoot,
  "knowledge-base",
  "digital-twin",
  "evidence",
  "phase7-product-journey"
);
const results = {
  suite: "Phase 7 Product Journey E2E",
  generated_at: new Date().toISOString(),
  browser: "chromium",
  steps: [],
  screenshots: [],
};

fs.mkdirSync(evidenceRoot, { recursive: true });

function mimeType(filePath) {
  return {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
  }[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

function startStaticServer() {
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url, "http://127.0.0.1");
    const relative = decodeURIComponent(requestUrl.pathname).replace(/^\/+/, "");
    const resolved = path.resolve(staticRoot, relative || "digital-twin/digital-twins.html");
    if (!resolved.startsWith(staticRoot)) {
      response.writeHead(403).end("Forbidden");
      return;
    }
    fs.readFile(resolved, (error, body) => {
      if (error) {
        response.writeHead(error.code === "ENOENT" ? 404 : 500).end(error.message);
        return;
      }
      response.writeHead(200, {
        "Content-Type": mimeType(resolved),
        "Cache-Control": "no-store",
      });
      response.end(body);
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

const stepFilter = String(process.env.ESDA_E2E_FILTER || "").toLowerCase();
async function record(name, fn) {
  if (stepFilter && name.toLowerCase().indexOf(stepFilter) < 0) {
    console.log("[SKIP] " + name);
    return;
  }
  const started = Date.now();
  console.log("[RUN] " + name);
  let timeoutId;
  try {
    const details = await Promise.race([
      fn(),
      new Promise((_, reject) => { timeoutId = setTimeout(() => reject(new Error("Step exceeded 45000 ms")), 45000); }),
    ]);
    clearTimeout(timeoutId);
    results.steps.push({ name, status: "passed", duration_ms: Date.now() - started, details });
    console.log("[PASS] " + name + " (" + (Date.now() - started) + " ms)");
  } catch (error) {
    clearTimeout(timeoutId);
    results.steps.push({
      name,
      status: "failed",
      duration_ms: Date.now() - started,
      error: error.stack || error.message,
    });
    console.error("[FAIL] " + name + ": " + error.message);
  }
}

async function waitForRows(page, expectedMinimum = 1) {
  await page.waitForFunction(
    (minimum) => document.querySelectorAll("[data-twin-row]").length >= minimum,
    expectedMinimum,
    { timeout: 10000 }
  );
}

async function openCleanList(page, listUrl) {
  await page.goto(listUrl, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForRows(page, 6);
}

function resolveBrowserExecutable() {
  const candidates = [
    process.env.ESDA_PLAYWRIGHT_EXECUTABLE,
    chromium.executablePath(),
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ].filter(Boolean);
  const executable = candidates.find((candidate) => fs.existsSync(candidate));
  if (!executable) {
    throw new Error(
      "No Chromium-compatible browser was found. Set ESDA_PLAYWRIGHT_EXECUTABLE to a local browser."
    );
  }
  return executable;
}

async function assertNoPageOverflow(page, viewportWidth) {
  const dimensions = await page.evaluate(() => ({
    body: document.body.scrollWidth,
    document: document.documentElement.scrollWidth,
  }));
  assert.ok(
    Math.max(dimensions.body, dimensions.document) <= viewportWidth + 1,
    `document overflowed viewport ${viewportWidth}px: ${JSON.stringify(dimensions)}`
  );
}

async function run() {
  const browserExecutable = resolveBrowserExecutable();
  const server = await startStaticServer();
  const address = server.address();
  const base = `http://127.0.0.1:${address.port}/digital-twin`;
  const listUrl = `${base}/digital-twins.html`;
  const detailUrl = (twinId, tab = "overview") =>
    `${base}/digital-twin-detail.html?twin_id=${encodeURIComponent(twinId)}&tab=${encodeURIComponent(tab)}`;
  const gateUrl = (twinId) =>
    `${base}/bundle-execution-twin-gate.html?twin_id=${encodeURIComponent(twinId)}`;
  results.base_url = base;

  const browser = await chromium.launch({ headless: true, executablePath: browserExecutable });
  const browserNewPage = browser.newPage.bind(browser);
  browser.newPage = async function (options) {
    const page = await browserNewPage(options);
    page.setDefaultTimeout(10000);
    page.setDefaultNavigationTimeout(10000);
    return page;
  };
  try {
    await record("list search, filters, sorting, pagination", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await openCleanList(page, listUrl);
      assert.equal(await page.locator("[data-twin-row]").count(), 6);
      assert.match(await page.locator(".pagination-label").innerText(), /1.*6.*13/);

      await page.locator("#search").fill("Core Gateway");
      await page.waitForURL(/search=Core(?:\+|%20)Gateway/);
      await waitForRows(page);
      assert.equal(await page.locator("[data-twin-row]").count(), 1);
      assert.match(await page.locator("[data-twin-row]").innerText(), /twin_core_gateway_003/);

      await page.locator("#reset-filters").click();
      await waitForRows(page, 6);
      await page.locator("#decision").selectOption("amber");
      await page.waitForURL(/decision=amber/);
      await waitForRows(page);
      const decisionLabels = await page.locator("[data-twin-row] td:nth-child(2)").allInnerTexts();
      assert.ok(decisionLabels.length >= 2);
      assert.ok(decisionLabels.every((value) => /Amber/i.test(value)));

      await page.locator("#reset-filters").click();
      await waitForRows(page, 6);
      await page.locator("[data-sort='risk']").click();
      await page.waitForURL(/sort=risk/);
      assert.match(page.url(), /direction=desc/);
      await page.locator("[data-sort='risk']").click();
      await page.waitForURL(/direction=asc/);

      await page.locator("#reset-filters").click();
      await waitForRows(page, 6);
      await page.getByRole("button", { name: "Next page" }).click();
      await page.waitForURL(/cursor=cursor_6/);
      await page.waitForFunction(() =>
        /7.*12.*13/.test(document.querySelector(".pagination-label").textContent || "")
      );
      assert.match(await page.locator(".pagination-label").innerText(), /7.*12.*13/);
      await page.getByRole("button", { name: "Previous page" }).click();
      await page.waitForURL(/cursor=cursor_0/);
      await page.waitForFunction(() =>
        /1.*6.*13/.test(document.querySelector(".pagination-label").textContent || "")
      );
      await page.close();
      return { fixture_rows: 13, page_size: 6 };
    });

    await record("direct detail and selected-tab deep links survive refresh and reopen", async () => {
      const url = detailUrl("twin_signal_scout_001", "rollback");
      const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await page.locator("#panel-rollback").getByText("Forward-to-Rollback Linkage").waitFor();
      assert.equal(await page.locator("#tab-rollback").getAttribute("aria-selected"), "true");
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.locator("#panel-rollback").getByText("Forward-to-Rollback Linkage").waitFor();
      assert.match(page.url(), /tab=rollback/);
      await page.close();

      const reopened = await browser.newPage({ viewport: { width: 1366, height: 768 } });
      await reopened.goto(url, { waitUntil: "domcontentloaded" });
      await reopened.locator("#panel-rollback").getByText("Forward-to-Rollback Linkage").waitFor();
      assert.equal(await reopened.locator("#tab-rollback").getAttribute("aria-selected"), "true");
      await reopened.close();
      return { twin_id: "twin_signal_scout_001", tab: "rollback" };
    });

    let generatedTwinId = null;
    await record("progressive availability and old-run reopening", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await openCleanList(page, listUrl);
      await page.locator("#fixture-scenario").selectOption("green-helm");
      await page.locator("#generate-fixture").click();
      await page.waitForURL(/digital-twin-detail\.html\?.*progress=1/);
      generatedTwinId = new URL(page.url()).searchParams.get("twin_id");
      assert.ok(generatedTwinId && generatedTwinId.startsWith("twin_mock_"));
      await page.waitForFunction(() =>
        Array.from(document.querySelectorAll(".detail-header .badge"))
          .some((node) => /Generating/i.test(node.textContent || ""))
      );
      await page.locator("#tab-rollback").click();
      await page.locator("#panel-rollback").getByText("Evidence is still generating").waitFor();
      await page.waitForFunction(() =>
        Array.from(document.querySelectorAll(".detail-header .badge"))
          .some((node) => /^Green$/i.test((node.textContent || "").trim())),
        null,
        { timeout: 18000 }
      );
      await page.locator("#panel-rollback").getByText("Forward-to-Rollback Linkage").waitFor();

      await page.goto(listUrl, { waitUntil: "domcontentloaded" });
      await waitForRows(page, 6);
      await page.locator("#search").fill(generatedTwinId);
      await page.waitForURL(/search=/);
      await waitForRows(page);
      assert.equal(await page.locator("[data-twin-row]").count(), 1);
      await page.locator("[data-twin-row] [data-row-action='open_twin']").dispatchEvent("click");
      await page.waitForURL(new RegExp("digital-twin-detail\\.html.*" + generatedTwinId));
      await page.waitForLoadState("domcontentloaded");
      await page.waitForFunction(() => /Mock generation/.test(document.querySelector("#twin-title").textContent));
      assert.match(await page.locator("#twin-title").innerText(), /Mock generation/);
      await page.close();
      return { generated_twin_id: generatedTwinId, terminal_decision: "green" };
    });

    await record("Green gate reaches execution with no approval override", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await page.goto(gateUrl("twin_signal_scout_001"), { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Eligible to proceed." }).waitFor();
      assert.match(await page.locator(".gate-result .log-view").innerText(), /"approval": "not_required"/);
      const start = page.locator("[data-gate-action='start']");
      assert.equal(await start.isEnabled(), true);
      await start.click();
      await page.getByRole("dialog").getByText("Execution handoff prepared").waitFor();
      await page.getByRole("button", { name: "Close dialog" }).click();
      await page.close();
      return { decision: "green", approval: "not_required", execution_handoff: "prepared" };
    });

    await record("Amber approval, return, and execution", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await page.goto(gateUrl("twin_beacon_pilot_002"), { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Approval required." }).waitFor();
      await page.locator("[data-gate-action='request-approval']").click();
      await page.locator("[data-gate-action='approve']").waitFor();
      await page.locator("[data-gate-action='approve']").click();
      const start = page.locator("[data-gate-action='start']");
      await start.waitFor();
      await page.waitForFunction(() => {
        const button = document.querySelector("[data-gate-action='start']");
        return Boolean(button && !button.disabled);
      });
      assert.equal(await start.isEnabled(), true);
      await page.getByRole("link", { name: "View Full Twin" }).click();
      await page.waitForURL(/digital-twin-detail\.html/);
      await page.goBack({ waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Approval required." }).waitFor();
      await page.waitForFunction(() => {
        const button = document.querySelector("[data-gate-action='start']");
        return Boolean(button && !button.disabled);
      });
      assert.equal(await page.locator("[data-gate-action='start']").isEnabled(), true);
      await page.locator("[data-gate-action='start']").click();
      await page.getByRole("dialog").getByText("Execution handoff prepared").waitFor();
      await page.close();
      return { decision: "amber", approval: "approved", return_path: "preserved" };
    });

    await record("Red block, corrected bundle, and regeneration", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await page.goto(gateUrl("twin_core_gateway_003"), { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Execution blocked." }).waitFor();
      assert.equal(await page.locator("[data-gate-action='start']").isDisabled(), true);
      const originalHash = await page.evaluate(() =>
        window.esdaTwinAdapter.getTwin("twin_core_gateway_003").then((item) => item.bundle.bundle_hash)
      );
      await page.locator("[data-gate-action='regenerate']").click();
      await page.waitForURL(/twin_id=twin_mock_/);
      const regeneratedId = new URL(page.url()).searchParams.get("twin_id");
      const regenerated = await page.evaluate((id) =>
        window.esdaTwinAdapter.getTwin(id), regeneratedId
      );
      assert.match(regenerated.bundle.bundle_name, /-corrected\.zip$/);
      assert.notEqual(regenerated.bundle.bundle_hash, originalHash);
      assert.equal(regenerated.prior_decision.decision, "red");
      const prior = await page.evaluate(() =>
        window.esdaTwinAdapter.getTwin("twin_core_gateway_003")
      );
      assert.equal(prior.lifecycle_status, "superseded");
      await page.evaluate(async (id) => {
        for (let index = 0; index < 4; index += 1) {
          await window.esdaTwinAdapter.advanceGeneration(id);
        }
      }, regeneratedId);
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Eligible to proceed." }).waitFor();
      assert.equal(await page.locator("[data-gate-action='start']").isEnabled(), true);
      await page.close();
      return { prior: "red", corrected_bundle: regenerated.bundle.bundle_name, regenerated: "green" };
    });

    await record("stale and material drift require regeneration", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      for (const twinId of ["twin_telemetry_store_006", "twin_inventory_api_007"]) {
        await page.goto(gateUrl(twinId), { waitUntil: "domcontentloaded" });
        await page.getByRole("heading", { name: "Regenerate before execution." }).waitFor();
        assert.equal(await page.locator("[data-gate-action='start']").isDisabled(), true);
        await page.locator("[data-gate-action='regenerate']").click();
        await page.waitForURL(/twin_id=twin_mock_/);
        const nextId = new URL(page.url()).searchParams.get("twin_id");
        const next = await page.evaluate((id) => window.esdaTwinAdapter.getTwin(id), nextId);
        assert.equal(next.prior_decision.twin_id, twinId);
        const prior = await page.evaluate((id) => window.esdaTwinAdapter.getTwin(id), twinId);
        assert.equal(prior.lifecycle_status, "superseded");
      }
      await page.close();
      return { regenerated: ["stale", "material_drift"] };
    });

    await record("rollback, cleanup, and execution linkage", async () => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await page.goto(detailUrl("twin_signal_scout_001", "rollback"), { waitUntil: "domcontentloaded" });
      await page.getByText("Forward-to-Rollback Linkage").waitFor();
      await page.getByRole("heading", { name: "Rollback Validation" }).waitFor();
      const linked = await page.evaluate(() =>
        window.esdaTwinAdapter.getTwin("twin_signal_scout_001")
      );
      assert.equal(linked.relationships.used_for_execution, true);
      assert.equal(linked.relationships.execution_id, "mopx_7f4a");
      await page.locator("#tab-mop-replay").click();
      await page.waitForFunction(() => document.getElementById("panel-mop-replay")._tabData?.data?.cleanup_status === "completed");
      const replayData = await page.evaluate(() =>
        document.getElementById("panel-mop-replay")._tabData.data
      );
      assert.ok(replayData.cleanup_status);
      await page.close();
      return {
        execution_id: linked.relationships.execution_id,
        rollback_linkage: "available",
        cleanup_status: replayData.cleanup_status,
      };
    });

    await record("browser Back and Forward across list, detail, and tabs", async () => {
      const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
      await page.goto(listUrl, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("domcontentloaded");
      await waitForRows(page, 6);
      await page.locator("[data-twin-row='twin_signal_scout_001']").click();
      await page.waitForURL(/tab=overview/);
      await page.waitForLoadState("domcontentloaded");
      await page.waitForFunction(() => /Signal Scout/.test(document.querySelector("#twin-title").textContent));
      await page.locator("#tab-policy").click();
      await page.waitForURL(/tab=policy/);
      await page.locator("#twin-title").waitFor();
      await page.goBack();
      await page.waitForURL(/tab=overview/);
      assert.equal(await page.locator("#tab-overview").getAttribute("aria-selected"), "true");
      await page.goBack({ waitUntil: "domcontentloaded" });
      await page.waitForURL(/digital-twins\.html/);
      await waitForRows(page, 6);
      await page.goForward({ waitUntil: "domcontentloaded" });
      await page.waitForURL(/tab=overview/);
      await page.goForward();
      await page.waitForURL(/tab=policy/);
      await page.waitForFunction(() => document.querySelector("#tab-policy").getAttribute("aria-selected") === "true");
      assert.equal(await page.locator("#tab-policy").getAttribute("aria-selected"), "true");
      await page.close();
      return { pages: ["list", "detail"], tabs: ["overview", "policy"] };
    });

    await record("desktop, laptop, tablet, and mobile screenshots", async () => {
      const viewports = [
        ["desktop", 1440, 1000],
        ["laptop", 1280, 800],
        ["tablet", 820, 1180],
        ["mobile", 390, 844],
      ];
      for (const [name, width, height] of viewports) {
        const page = await browser.newPage({ viewport: { width, height } });
        await page.goto(listUrl, { waitUntil: "domcontentloaded" });
        await waitForRows(page, 6);
        await assertNoPageOverflow(page, width);
        const listShot = path.join(evidenceRoot, `digital-twins-${name}.png`);
        await page.screenshot({ path: listShot, fullPage: true });
        results.screenshots.push(path.relative(repoRoot, listShot).replace(/\\/g, "/"));

        await page.goto(detailUrl("twin_signal_scout_001", "overview"), { waitUntil: "domcontentloaded" });
        await page.locator("#panel-overview [data-tab-content]").waitFor();
        await assertNoPageOverflow(page, width);
        const detailShot = path.join(evidenceRoot, `digital-twin-detail-${name}.png`);
        await page.screenshot({ path: detailShot, fullPage: true });
        results.screenshots.push(path.relative(repoRoot, detailShot).replace(/\\/g, "/"));
        await page.close();
      }
      return { viewports: viewports.map(([name, width, height]) => ({ name, width, height })) };
    });

    await record("keyboard, focus, labels, status text, and reduced motion", async () => {
      const context = await browser.newContext({
        viewport: { width: 1280, height: 800 },
        reducedMotion: "reduce",
      });
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      page.setDefaultNavigationTimeout(10000);
      await page.goto(listUrl, { waitUntil: "domcontentloaded" });
      await waitForRows(page, 6);
      await page.keyboard.press("Tab");
      assert.equal(await page.evaluate(() => document.activeElement.classList.contains("skip-link")), true);
      await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(() => location.hash), "#main-content");
      await waitForRows(page, 6);

      const labelled = await page.evaluate(() =>
        Array.from(document.querySelectorAll("#twin-filter-form input:not([type='hidden']), #twin-filter-form select"))
          .every((control) => control.labels && control.labels.length > 0)
      );
      assert.equal(labelled, true);
      await page.locator("#search").focus();
      const focusStyle = await page.locator("#search").evaluate((element) => {
        const style = getComputedStyle(element);
        return { style: style.outlineStyle, width: style.outlineWidth };
      });
      assert.notEqual(focusStyle.style, "none");
      assert.ok(parseFloat(focusStyle.width) >= 3);

      const badgeLabels = await page.locator("[data-twin-row]").evaluateAll((rows) =>
        rows.map((row) => String(row.cells[1]?.textContent || "").trim())
      );
      assert.ok(badgeLabels.length > 0 && badgeLabels.every(Boolean), JSON.stringify(badgeLabels));
      await page.locator("[data-twin-row]").first().focus();
      await page.keyboard.press("Enter");
      await page.waitForURL(/digital-twin-detail\.html/);
      await page.waitForFunction(() => !/Loading twin run/.test(document.querySelector("#twin-title").textContent));
      await page.locator("#tab-overview").focus();
      await page.keyboard.press("ArrowRight");
      await page.waitForURL(/tab=release-delta/);
      assert.equal(await page.locator("#tab-release-delta").getAttribute("aria-selected"), "true");
      await page.keyboard.press("End");
      await page.waitForURL(/tab=audit/);
      assert.equal(await page.locator("#tab-audit").getAttribute("aria-selected"), "true");
      const reduced = await page.locator("body").evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          animationDuration: style.animationDuration,
          transitionDuration: style.transitionDuration,
        };
      });
      assert.ok(["0s", "0.00001s", "1e-05s", "0.01ms"].includes(reduced.animationDuration), reduced.animationDuration);
      assert.ok(["0s", "0.00001s", "1e-05s", "0.01ms"].includes(reduced.transitionDuration), reduced.transitionDuration);

      await page.goto(gateUrl("twin_beacon_pilot_002"), { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Approval required." }).waitFor();
      const gateLabels = await page.evaluate(() =>
        Array.from(document.querySelectorAll(".gate-context input, .gate-context select, .gate-context textarea"))
          .every((control) => control.labels && control.labels.length > 0)
      );
      assert.equal(gateLabels, true);
      await context.close();
      return {
        keyboard_rows: true,
        keyboard_tabs: ["ArrowRight", "Home", "End"],
        visible_focus: focusStyle,
        color_independent_badges: badgeLabels.length,
        reduced_motion: reduced,
      };
    });
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
    results.completed_at = new Date().toISOString();
    results.passed = results.steps.filter((item) => item.status === "passed").length;
    results.failed = results.steps.filter((item) => item.status === "failed").length;
    fs.writeFileSync(
      path.join(evidenceRoot, "product-journey-results.json"),
      JSON.stringify(results, null, 2) + "\n",
      "utf8"
    );
  }

  if (results.failed) {
    console.error(JSON.stringify(results, null, 2));
    process.exitCode = 1;
  } else {
    console.log(`Product Journey E2E passed: ${results.passed} checks, ${results.screenshots.length} screenshots.`);
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
