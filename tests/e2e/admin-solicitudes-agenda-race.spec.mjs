// Race in the Solicitudes detail (found by the maintainer's agent, 2026-10-28): the card is painted with the
// time the SERVER proposed (the same for every card) and the doctor's real agenda arrives a moment
// later through /api/admin/appointment-slots. Aprobar pressed in between sent the stale time and hit
// an occupied slot (409; no double booking, but the desk sees an error it did not cause).
//
// Three things are proved against the real ASGI app, with the agenda request held by a gate (the
// 1.5 s delay of the report, without a timer):
//   1. until the agenda arrives the time is marked as loading and «Aprobar» is off; when it arrives
//      the approval goes out with a real time of the agenda, no 409.
//   2. an agenda that fails is asked again by the card itself (no error line, no «Reintentar»,
//      critical review 2026-11-08): the time stays «Cargando horarios…» and the button off until
//      it loads.
//   3. a 409 for a taken time leaves the desk with the agenda refreshed (the taken time is gone) and
//      a sentence that says what to do.
// Waits are retrying expects; nothing sleeps.
import { join } from "node:path";
import { homedir } from "node:os";
import { test, expect, holdGate, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_solicitudes_agenda_race", queue: true, requests: 4 } });
test.describe.configure({ mode: "serial" });

const SHOTS = join(homedir(), ".claude/artifacts/pinamar-turnos/aprobar-race");
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };
const SLOTS = "**/api/admin/appointment-slots?*";
const isApprove = (r) => /\/api\/admin\/requests\/\d+\/approve$/.test(new URL(r.url()).pathname)
  && r.request().method() === "POST";

async function openFirstPending(page) {
  await page.goto("/admin/solicitudes", { waitUntil: "load" });
  await expect(page.locator("#list [data-sol-row]").first()).toBeVisible({ timeout: 12000 });
  if (await page.locator("#f-estado").inputValue() !== "pendiente") await page.locator("#f-estado").selectOption("pendiente");
  await page.locator("#list .sol-row").first().click();
  const detail = page.locator("aside.sol-det[data-id]");
  await expect(detail).toBeVisible();
  return detail;
}

test("Aprobar waits for the doctor's agenda and then approves with a time of it", async ({ adminPage: page, cleanup }, testInfo) => {
  const gate = holdGate(cleanup);
  let asked = false;
  await page.route(SLOTS, async (route) => {
    asked = true;
    await gate.held;
    await route.continue().catch(() => {});
  });
  const approvals = [];
  page.on("request", (r) => { if (r.method() === "POST" && /\/approve$/.test(r.url())) approvals.push(r.url()); });
  await page.setViewportSize(DESKTOP);
  const detail = await openFirstPending(page);
  await expect.poll(() => asked, { message: "the card asked for its agenda" }).toBe(true);

  const aprobar = detail.locator('[data-act="aprobar"]');
  const select = detail.locator("select.turno-sel");
  await expect(select, "the time is marked as loading").toHaveAttribute("aria-busy", "true");
  await expect(select).toBeDisabled();
  await expect(select).toContainText(/Cargando horarios/);
  await expect(aprobar, "Aprobar is off while the agenda is on its way").toBeDisabled();
  await saveShot(testInfo, detail, join(SHOTS, "tarjeta-cargando-escritorio.png"));
  await page.setViewportSize(PHONE);
  await expect(detail).toBeVisible();
  await saveShot(testInfo, detail, join(SHOTS, "tarjeta-cargando-390.png"));
  expect(approvals, "nothing was approved with the proposed time").toEqual([]);

  gate.release();
  await expect(aprobar, "the agenda arrived: Aprobar is on").toBeEnabled({ timeout: 10000 });
  await expect(select).toBeEnabled();
  await expect(select).not.toHaveAttribute("aria-busy", "true");
  const times = await select.locator("option").evaluateAll((o) => o.map((x) => x.value).filter((v) => v && v !== "__otro"));
  expect(times.length, "the agenda offers times").toBeGreaterThan(0);
  await saveShot(testInfo, detail, join(SHOTS, "tarjeta-agenda-390.png"));
  await page.setViewportSize(DESKTOP);
  await expect(detail).toBeVisible();
  await saveShot(testInfo, detail, join(SHOTS, "tarjeta-agenda-escritorio.png"));

  const chosen = await detail.locator("input.turno").inputValue();
  expect(times, "the preselected time is one of the agenda").toContain(chosen);
  const approved = page.waitForResponse(isApprove);
  await aprobar.click();
  const response = await approved;
  expect(response.status(), "the approval with the agenda's time is accepted (no 409)").toBe(200);
  expect(JSON.parse(response.request().postData()).turno_dt).toContain(chosen);
});

test("a failed agenda is asked again by the card itself and keeps Aprobar off until it loads", async ({ adminPage: page }, testInfo) => {
  let calls = 0;
  let failedOnce;
  const failed = new Promise((resolve) => { failedOnce = resolve; });
  await page.route(SLOTS, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
      failedOnce();
    } else {
      await route.continue().catch(() => {});
    }
  });
  await page.setViewportSize(PHONE);
  const detail = await openFirstPending(page);
  const aprobar = detail.locator('[data-act="aprobar"]');
  const select = detail.locator("select.turno-sel");
  await failed;
  await expect(detail.getByRole("alert"), "no error line for the desk to read").toHaveCount(0);
  await expect(detail.getByRole("button", { name: "Reintentar" })).toHaveCount(0);
  await expect(aprobar, "the agenda has not arrived yet").toBeDisabled();
  await saveShot(testInfo, detail, join(SHOTS, "tarjeta-agenda-reintento-390.png"));
  await expect(aprobar, "the card asked again and the agenda loaded").toBeEnabled({ timeout: 10000 });
  await expect(select).toBeEnabled();
  expect(calls, "a second agenda request went out by itself").toBeGreaterThan(1);
});

test("a 409 for a taken time leaves the agenda refreshed and says to pick another", async ({ adminPage: page }, testInfo) => {
  let taken = "";
  await page.route(SLOTS, async (route) => {
    const response = await route.fetch();
    if (!taken) return route.fulfill({ response });
    const body = await response.json();
    body.libres = (body.libres || []).filter((t) => `${t.dia}T${t.hora}` !== taken);
    return route.fulfill({ response, body: JSON.stringify(body), contentType: "application/json" });
  });
  await page.route(/\/api\/admin\/requests\/\d+\/approve$/, (route) => route.fulfill({
    status: 409, contentType: "application/json", body: JSON.stringify({ error: "turno_tomado" }),
  }));
  await page.setViewportSize(DESKTOP);
  const detail = await openFirstPending(page);
  const aprobar = detail.locator('[data-act="aprobar"]');
  const select = detail.locator("select.turno-sel");
  await expect(aprobar).toBeEnabled({ timeout: 10000 });
  taken = await detail.locator("input.turno").inputValue();
  expect(taken, "a time was preselected").not.toBe("");

  const refreshed = page.waitForResponse((r) => /appointment-slots\?/.test(r.url()));
  await aprobar.click();
  await refreshed;
  const hint = detail.locator(".sol-act-err");
  await expect(hint).toContainText(/se ocupó mientras tanto/);
  await expect(hint).toContainText(/elegí otro horario/);
  await expect(aprobar, "the desk can approve again once it picks another time").toBeEnabled({ timeout: 10000 });
  await expect(select).toBeEnabled();
  await expect(detail.locator("input.turno"), "nothing is chosen on the desk's behalf").toHaveValue("");
  const offered = await select.locator("option").evaluateAll((o) => o.map((x) => x.value).filter((v) => v && v !== "__otro"));
  expect(offered, "the refreshed agenda no longer holds the taken time").not.toContain(taken);
  await saveShot(testInfo, detail, join(SHOTS, "tarjeta-409-escritorio.png"));
});
