// The eight-second deadline of the panel screens says a load is late and never
// abandons it (static/platform/screen-boot.mjs, 2026-11-16). Three screens of
// different kinds, each with its real controller module held at the network
// past the deadline: the queue (server-rendered table, the copy goes in its
// status line), the reports (an empty screen the controller draws, the copy
// goes beside it) and the intake consultation (a module of its own, served
// through module_static). For each: the copy appears while the module is
// held, the held module is released, the screen draws itself and the copy
// goes away. Before 2026-11-16 the deadline stopped the load and the late
// module was discarded, so the screen stayed on the copy for good. The copy is
// the shared late one («La carga tarda más de lo normal.» + «Recargar»,
// templates/_screen_load_late.html), not the screen's failure text.
import { test, expect, holdGate } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_screen_boot_slow",
    requests: true, queue: true, intake: true, doorMedico: "gonzalez-juan",
  },
});

// Hold the first request for the screen's own module until the test releases
// it; `arrive` marks that the browser asked for it.
async function holdModule(page, cleanup, pattern) {
  const gate = holdGate(cleanup);
  let held = false;
  await page.route(pattern, async (route) => {
    if (held) return route.continue();
    held = true;
    gate.arrive();
    await gate.held;
    await route.continue();
  });
  return gate;
}

test("queue: the module held past eight seconds still hydrates the table and takes the copy down", async ({ adminPage: page, cleanup }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  const gate = await holdModule(page, cleanup, /\/static\/platform\/admin-queue\.[a-f0-9]+\.mjs/);
  await page.goto("/admin/cola", { waitUntil: "commit" });
  await gate.arrived;
  const copy = page.locator("#queue-load-status [data-retry]");
  await expect(copy).toBeVisible({ timeout: 12000 });
  // Late, not failed (2026-11-16): above readable rows the line says the load
  // is taking longer, never the screen's failure text.
  await expect(page.locator("#queue-load-status")).toContainText("La carga tarda más de lo normal.");
  await expect(page.locator("#queue-load-status")).not.toContainText("No se pudo cargar");
  await expect(copy).toHaveText("Recargar");
  await expect(page.locator("#sec-cola table").first(), "the server's table stays readable").toBeVisible();
  gate.release();
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  await expect(copy).toHaveCount(0);
  await expect(page.locator("#sec-cola")).not.toHaveAttribute("aria-busy", "true");
  expect(errors).toEqual([]);
});

test("reports: the module held past eight seconds still draws the report and takes the copy down", async ({ adminPage: page, cleanup }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  const gate = await holdModule(page, cleanup, /\/static\/platform\/admin-reports\.[a-f0-9]+\.mjs/);
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "commit" });
  await gate.arrived;
  const copy = page.locator(".screen-load-slow [data-retry]");
  await expect(copy).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-reportes [data-retry]").count(), "the copy is beside the screen, not in it").toBe(0);
  gate.release();
  await page.waitForFunction(() => document.querySelector("#rep-body")?.innerText.trim().length > 20, null, { timeout: 12000 });
  await expect(copy).toHaveCount(0);
  await expect(page.locator(".screen-load-slow")).toHaveCount(0);
  expect(await page.locator('#sec-reportes [role="alert"]').count()).toBe(0);
  expect(errors).toEqual([]);
});

test("consultation: the intake module held past eight seconds still opens the interview and takes the copy down", async ({ adminPage: page, app, cleanup }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  const gate = await holdModule(page, cleanup, /\/static\/modules\/intake\/admin-consult\.[a-f0-9]+\.mjs/);
  await page.goto("/admin/consult?interview=" + app.fixture.interview_id, { waitUntil: "commit" });
  await gate.arrived;
  const copy = page.locator(".screen-load-slow [data-retry]");
  await expect(copy).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-consulta [data-retry]").count(), "the copy is beside the screen, not in it").toBe(0);
  gate.release();
  await expect(page.locator("#cst-review")).toBeVisible({ timeout: 12000 });
  await expect(copy).toHaveCount(0);
  expect(errors).toEqual([]);
});
