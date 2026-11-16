// A turno created straight in HSI takes its hour on the public site too. The mirror
// row is a fixture row (what the HSI sync writes), not a live HSI: the app, the
// database and the browser are real. Before the row the hour is offered by
// /api/service-slots/<service>; after it the hour is gone from the API and from
// the wizard; clearing the row gives it back.
import { test, expect, saveShot } from "./lib/fixtures.mjs";
import { mirrorHsiBusy } from "./lib/harness.mjs";
import { artifactPath } from "./lib/artifacts.mjs";

const SERVICE = "medicina-general-riverside-clinic";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_hsi_busy_hour", hsi: true, env: { TURNOS_HSI_SYNC_OFF: "0" } } });

async function offered(page, taken) {
  const res = await page.request.get(`/api/service-slots/${SERVICE}`);
  const text = await res.text();
  const body = JSON.parse(text);
  const days = body.dias || body.days || body.slots || body;
  return JSON.stringify(days).includes(`"${taken.hora}"`)
    && JSON.stringify(days).includes(taken.dia)
    && countSlot(days, taken) > 0;
}

function countSlot(node, taken, day = null) {
  if (Array.isArray(node)) return node.reduce((n, x) => n + countSlot(x, taken, day), 0);
  if (node && typeof node === "object") {
    const d = node.dia || node.fecha || node.date || day;
    if ((node.hora || node.time) === taken.hora && d === taken.dia) return 1;
    return Object.values(node).reduce((n, x) => n + countSlot(x, taken, d), 0);
  }
  return 0;
}

for (const vp of [{ width: 390, height: 844, label: "390" }, { width: 1440, height: 900, label: "1440" }]) {
  test(`hour created in HSI is not offered (${vp.label})`, async ({ page, app }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const DB = app.dbName;
    await mirrorHsiBusy(DB, { clear: true });
    const taken = await mirrorHsiBusy(DB, { service: SERVICE });
    await mirrorHsiBusy(DB, { clear: true });

    await page.goto(`/service/${SERVICE}/book`, { waitUntil: "domcontentloaded" });
    const slot = `.wz-seg-opt[data-slot][data-dia="${taken.dia}"]`;
    await expect(page.locator(".wz-seg-opt[data-slot][data-dia]").first()).toBeVisible({ timeout: 15000 });
    const hourBtn = page.locator(`${slot}[data-slot="${taken.hora}"], ${slot}[data-hora="${taken.hora}"]`);
    expect(await offered(page, taken), "hour offered before the HSI row").toBe(true);
    await saveShot(testInfo, page, artifactPath("human-rounds", "hsi-busy-hour", `before-${vp.label}.png`));

    await mirrorHsiBusy(DB, { service: SERVICE });
    await expect.poll(() => offered(page, taken), { timeout: 15000, message: "hour must vanish after the HSI row" }).toBe(false);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator(".wz-seg-opt[data-slot][data-dia]").first()).toBeVisible({ timeout: 15000 });
    await expect(hourBtn).toHaveCount(0);
    await saveShot(testInfo, page, artifactPath("human-rounds", "hsi-busy-hour", `after-${vp.label}.png`));

    await mirrorHsiBusy(DB, { clear: true });
    await expect.poll(() => offered(page, taken), { timeout: 15000, message: "hour returns when the row is gone" }).toBe(true);
  });
}
