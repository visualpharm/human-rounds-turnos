// Real ASGI login/template/assets/dependencies and throwaway database.
// Geometry and error cases intercept only consultation API responses, never
// the page/controller/shared session module. C01 and C08 use the real API.
//
// Port of tests/e2e/admin-consultation-lifecycle.e2e.mjs (25 tests, same titles, same order,
// plus one closing test, see below).
//
// Differences from the node:test original:
// - The cases append to one shared `records` array that the closing completeness check reads,
//   so the file is `serial`: a failure skips the rest and a retry re-runs the whole file on a
//   fresh worker with a fresh record. The original's `test.after` assertion («all unique cases
//   require assertions and nonempty durable captures») is the extra last test, «evidence report
//   is complete ...»; `test.afterAll` only writes report.json (also when an earlier case failed).
// - The evidence directory is reserved in `test.beforeAll` (still with `allocateEvidenceRunDir`),
//   not at import time: Playwright imports a spec file in the runner process too, which would
//   create an empty run directory for every listing.
// - `session(...)` builds the context with the `newContext` fixture (viewport = the case's width,
//   closed by the runner instead of `t.after`) and registers the same layout-shift/font observer
//   init script; `requireOk` is the automatic `_gate` fixture.
// - Screenshots go through `saveShot` to the same files as before; the `{ path, bytes }` it returns
//   feeds `record.assets` exactly as the original's `stat` did.
// - Waits and field checks are web-first (`toBeVisible`, `toHaveCount`, `toHaveAttribute`,
//   `toBeChecked`, `toContainText`); the geometry, the CLS and every «absent at this moment» check
//   (no form painted while the fonts load, zero reads, request counters) stay point-in-time reads.
// - `t.after(() => release())` for the font gate is `holdGate(cleanup)`.
// - Locators that stay CSS: the consultation form's ids/classes/data attributes (`#cst-*`, `.cst-*`,
//   `[data-med-field]`, `[data-consult-retry]`...) are the contract that is measured and driven here;
//   the geometry sweep is one in-page evaluation exactly as before.
import { join } from "node:path";
import fs from "node:fs/promises";
import {
  test, expect, saveShot, holdGate, queryConsultation, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";
import { cumulativeLayoutShift } from "./lib/layout-shift-evidence.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_consult_native", intake: true, doorMedico: "gonzalez-juan", doctorPatients: true,
    env: { LANGUAGES: "es,en" },
  },
});
test.describe.configure({ mode: "serial" });

const WIDTHS = [360, 390, 768, 834, 1024, 1280, 1440];
const IDS = ["C01", ...["es", "en"].flatMap((lang) => WIDTHS.map((width) => `C02-${lang}-${width}`)), "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C13", "C14", "C15"];
const ENTRY = /\/static\/modules\/intake\/admin-consult\.[a-f0-9]+\.mjs/;
const records = [];
let evidence;
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const sample = {
  id: "fixture-consult", nombre: "Persona de prueba", status: "done",
  analyzed_at: "2026-09-01T09:00:00Z", summary: "Entrevista ficticia para evaluar el formulario.",
  mensajes: [{ role: "user", content: "Mensaje sintético, sin datos de pacientes." }],
  brief: [{ point: "Relato ficticio extenso para comprobar que el resumen conserva todo el texto al cambiar el ancho de pantalla." }],
  red_flags: [{ label: "Advertencia sintética de prueba", because: "Este mensaje comprueba la visibilidad del aviso y no representa información de una persona real." }],
  perfil: { alergias: ["Antecedente sintético descrito con una frase larga para evaluar el ajuste de texto."] },
  differential: [{ id: "dx-test", prob: 0.8, label: "Diagnóstico sintético con una descripción suficientemente larga para ocupar varias líneas", reason: "Motivo ficticio de verificación." }], suggested_exams: ["exam-test"],
  catalogos: {
    diagnosticos: [{ id: "dx-test", label: "Diagnóstico sintético con una descripción suficientemente larga para ocupar varias líneas" }],
    estudios: [{ id: "exam-test", label: "Estudio sintético con descripción extensa para evaluar los controles seleccionables", categoria: "Categoría de prueba" }],
    medicamentos: [], frecuencias: ["Frecuencia de prueba"],
  },
};

function complete(rows) {
  return rows.length === IDS.length && new Set(rows.map((r) => r.id)).size === IDS.length
    && IDS.every((id) => rows.some((r) => r.id === id))
    && rows.every((r) => r.assertionComplete && r.assets.length > 0 && r.assets.every((a) => a.bytes > 0))
    && new Set(rows.flatMap((r) => r.assets.map((a) => a.path))).size === rows.flatMap((r) => r.assets).length;
}

test.beforeAll(() => {
  evidence = allocateEvidenceRunDir({ root: artifactPath("human-rounds", "consultation-native-lifecycle"), label: "run" });
});
test.afterAll(async () => {
  await fs.writeFile(join(evidence, "report.json"), JSON.stringify({ expectedIDs: IDS, complete: complete(records), records,
    boundary: "Local real ASGI and synthetic-pack database. C02/error cases intercept consultation responses only. No production services.",
    missingCoverage: ["Actual BFCache admission", "Clinical decision validation"] }, null, 2));
});

async function session({ newContext, app }, id, width = 390, locale = undefined, pinFallback = false) {
  const context = await newContext({ viewport: { width, height: 900 }, ...(locale ? { locale } : {}) });
  await context.addInitScript((pin) => {
    // Before the typeface arrives the page paints in `Manrope Fallback` (Arial / Arimo / Liberation
    // Sans with Manrope's metrics, static/fonts/humanrounds.css). Until 2026-11-10 it was the
    // browser's own `system-ui`, different per machine: the 2FA nudge was green on a Mac and
    // 0.17-0.46 in GitLab (2026-10-29). Fonts-settlement cases pin the production stack without
    // `system-ui`, so a machine without Arial or its metric twins still paints the same face.
    if (pin) document.addEventListener("DOMContentLoaded", () => {
      const s = document.createElement("style");
      s.textContent = ":root,html[data-theme]{--font:'Manrope','Manrope Fallback',sans-serif !important}";
      document.head.append(s);
    });
    window.__consultShifts = [];
    window.__consultFonts = [];
    const fontSnapshot = (event) => {
      const target = document.querySelector("#sec-consulta .cst-h3");
      const css = target && getComputedStyle(target);
      window.__consultFonts.push({ event, time: performance.now(), status: document.fonts.status,
        headingFont: css?.font, headingHeight: target?.getBoundingClientRect().height,
        faces: [...document.fonts].map((f) => ({ family: f.family, status: f.status })) });
    };
    document.fonts.addEventListener("loading", () => fontSnapshot("loading"));
    document.fonts.addEventListener("loadingdone", () => fontSnapshot("loadingdone"));
    window.__consultShiftRecord = (e) => ({ value: e.value, startTime: e.startTime,
      fonts: [...document.fonts].map((f) => ({ family: f.family, status: f.status })),
      sources: (e.sources || []).map((s) => ({ node: s.node?.id || s.node?.className || s.node?.nodeName,
        previous: s.previousRect.toJSON(), current: s.currentRect.toJSON() })) });
    window.__consultObserver = new PerformanceObserver((list) => {
      window.__consultShifts.push(...list.getEntries().filter((e) => !e.hadRecentInput).map(window.__consultShiftRecord));
    });
    window.__consultObserver.observe({ type: "layout-shift", buffered: true });
  }, pinFallback);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto("/admin/login");
  await expect(page.locator("#gate-form")).toBeVisible();
  await page.fill("#gate-user", ADMIN_USER); await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForFunction(() => location.pathname.startsWith("/admin/") && location.pathname !== "/admin/login");
  const record = { id, assets: [], errors, assertionComplete: false };
  records.push(record);
  return { page, context, record };
}
async function ready(page, app, query = `interview=${app.fixture.interview_id}`) {
  await page.goto("/admin/consult?" + query);
  // Nothing is preselected: the form's ready action is «Revisar y generar
  // documentos»; #cst-confirmar only exists inside the explicit review.
  await expect(page.locator("#cst-review")).toBeVisible({ timeout: 12000 });
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function capture(testInfo, page, record) {
  // Playwright scrolls to edited controls. Return to the top before a full-page
  // capture so fixed chrome does not appear stranded halfway down the image.
  await page.evaluate(() => scrollTo(0, 0));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  record.assets.push(await saveShot(testInfo, page, join(evidence, record.id + ".png"), { fullPage: true }));
}
function pass(record) { expect(record.errors).toEqual([]); record.assertionComplete = true; }
async function sampleApi(page) { await page.route("**/api/admin/intake/consult/fixture-consult", (route) => route.fulfill(json(sample))); }

test("C01 actual server interview and native module load with no classic entry", async ({ newContext, app }, testInfo) => {
  const { page, context, record } = await session({ newContext, app }, "C01");
  const response = await context.request.get("/admin/consult");
  expect(response.status()).toBe(200); expect(response.headers()["cache-control"]).toBe("no-store");
  expect(await response.text()).toMatch(ENTRY); expect(await response.text()).not.toMatch(/admin-consulta\.js/);
  for (const path of ["/admin-consulta.js", "/static/admin-consulta.deadbeef.js"]) {
    const retired = await context.request.get(path);
    expect(retired.status()).toBe(404); expect(retired.headers()["cache-control"]).toBe("no-store");
  }
  await ready(page, app);
  expect(await page.evaluate(() => performance.getEntriesByType("resource").some((r) => /\/modules\/intake\/admin-consult\.[a-f0-9]+\.mjs/.test(r.name)))).toBe(true);
  expect(await page.locator('a[href="/admin/cola"]').count()).toBeGreaterThan(0);
  await capture(testInfo, page, record); pass(record);
});

for (const lang of ["es", "en"]) for (const width of WIDTHS) {
  test(`C02-${lang}-${width} native real-page controls fit and observer CLS is bounded`, async ({ newContext, app }, testInfo) => {
    const { page, context, record } = await session({ newContext, app }, `C02-${lang}-${width}`, width);
    await page.goto("about:blank");
    await context.addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
    await sampleApi(page); await ready(page, app, `interview=fixture-consult&lang=${lang}`);
    await page.locator(".cst-transcript summary").click();
    await expect(page.locator(".cst-transcript")).toHaveAttribute("open", "");
    await expect(page.locator(".cst-flag")).toHaveCount(1);
    await page.locator("[data-add-med]").click();
    await page.locator('[data-med-field="nombre"]').fill("Medicamento de prueba");
    await page.locator("details:has(#cst-profile-reviewed) > summary").click();
    await page.locator("#cst-profile-reviewed").check();
    await expect(page.locator("#cst-profile-reviewed")).toBeChecked();
    await capture(testInfo, page, record);
    record.geometry = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const host = document.querySelector("#sec-consulta");
      const visible = (el) => el.getBoundingClientRect().width > 0 && getComputedStyle(el).visibility !== "hidden";
      const elements = [...host.querySelectorAll("*")].filter(visible);
      const bad = elements.filter((el) => { const box = el.getBoundingClientRect(); return box.left < -1 || box.right > vw + 1; });
      const pickerOrder = ["#cst-dx-search", "#cst-exam-search"].map((selector) => {
        const search = host.querySelector(selector), list = search.nextElementSibling, additions = list.nextElementSibling;
        return search.getBoundingClientRect().bottom <= list.getBoundingClientRect().top + 1
          && list.getBoundingClientRect().bottom <= additions.getBoundingClientRect().top + 1;
      });
      const pen = document.createElement("canvas").getContext("2d");
      const clippedValues = [...host.querySelectorAll("input:not([type=checkbox]):not([type=radio])")].filter((el) => el.value && visible(el)).flatMap((el) => {
        const css = getComputedStyle(el); pen.font = css.font;
        const needed = pen.measureText(el.value).width;
        const available = el.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
        return needed > available + 1 ? [{ field: el.dataset.medField || el.id, needed, available }] : [];
      });
      return { width: vw, lang: document.documentElement.lang, overflow: document.documentElement.scrollWidth - vw,
        fontEvents: window.__consultFonts,
        clippedValues, medicationLabels: [...host.querySelectorAll("[data-med-field]")].map((el) => el.getAttribute("aria-label")),
        pickerOrder,
        addMedicationBelowTable: host.querySelector(".cst-med-table").getBoundingClientRect().bottom <= host.querySelector("[data-add-med]").getBoundingClientRect().top + 1,
        escaped: bad.map((el) => ({ tag: el.tagName, cls: el.className, right: el.getBoundingClientRect().right })),
        smallText: elements.filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(el).fontSize) < 16).map((el) => ({ text: el.textContent.slice(0, 70), px: getComputedStyle(el).fontSize })),
        smallTargets: [...host.querySelectorAll("button,label,input,textarea,select,a,summary,[role=button]")].filter(visible).flatMap((el) => {
          const target = el.matches("input[type=checkbox],input[type=radio]") ? el.labels?.[0] : el;
          if (!target || !visible(target)) return [{ control: el.id || el.tagName, error: "no visible associated target" }];
          const rect = target.getBoundingClientRect();
          return rect.height < 44 || rect.width < 44 ? [{ control: el.id || el.tagName, height: rect.height, width: rect.width }] : [];
        }),
        shifts: [...window.__consultShifts, ...window.__consultObserver.takeRecords().filter((e) => !e.hadRecentInput).map(window.__consultShiftRecord)] };
    });
    record.geometry.cls = cumulativeLayoutShift(record.geometry.shifts);
    expect(record.geometry.width).toBe(width); expect(record.geometry.lang).toBe(lang);
    expect(record.geometry.overflow).toBe(0); expect(record.geometry.escaped).toEqual([]);
    expect(record.geometry.pickerOrder).toEqual([true, true]); expect(record.geometry.addMedicationBelowTable).toBe(true);
    expect(record.geometry.clippedValues, "typical entered values must fit, not merely their boxes").toEqual([]);
    expect(record.geometry.medicationLabels).toEqual(lang === "en" ? ["Medication", "Dose", "Frequency", "Duration"] : ["Medicamento", "Dosis", "Frecuencia", "Duración"]);
    expect(record.geometry.smallText).toEqual([]); expect(record.geometry.smallTargets).toEqual([]);
    expect(record.geometry.cls, `observed CLS ${record.geometry.cls}`).toBeLessThanOrEqual(0.1);
    pass(record);
  });
}

test("C03 actual session loss redirects through real whoami to login", async ({ newContext, app }, testInfo) => {
  const { page, context, record } = await session({ newContext, app }, "C03");
  await ready(page, app); await context.clearCookies();
  await page.evaluate(() => dispatchEvent(new PopStateEvent("popstate")));
  await page.waitForURL("**/admin/login?next=**");
  expect(new URL(page.url()).searchParams.get("next")).toBe(`/admin/consult?interview=${app.fixture.interview_id}`);
  await capture(testInfo, page, record); pass(record);
});
test("C04 non-session role rejection stays local", async ({ newContext, app }, testInfo) => {
  const { page, record } = await session({ newContext, app }, "C04");
  await ready(page, app);
  await page.route("**/api/admin/intake/consult/*", (route) => route.fulfill(json({ error: "forbidden" }, 403)));
  await page.evaluate(() => dispatchEvent(new PopStateEvent("popstate")));
  await expect(page.locator("[data-consult-retry]")).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/admin/consult");
  await capture(testInfo, page, record); pass(record);
});
test("C05 explicit MFA repair uses real shared session navigation", async ({ newContext, app }, testInfo) => {
  const { page, record } = await session({ newContext, app }, "C05");
  await ready(page, app);
  await page.route("**/api/admin/intake/consult/*", (route) => route.fulfill(json({ error: "2fa_requerido" }, 403)));
  await page.evaluate(() => dispatchEvent(new PopStateEvent("popstate")));
  await page.waitForURL("**/admin/account?setup=2fa");
  await expect(page.locator("#acct-email-edit")).toBeVisible();
  await capture(testInfo, page, record); pass(record);
});
test("C06 rejected real entry recovers through reload", async ({ newContext, app }, testInfo) => {
  const { page, record } = await session({ newContext, app }, "C06");
  let rejected = false;
  await page.route(ENTRY, (route) => { if (!rejected) { rejected = true; return route.abort(); } return route.continue(); });
  await page.goto("/admin/consult?interview=" + app.fixture.interview_id);
  await expect(page.locator("[data-retry]")).toBeVisible(); await capture(testInfo, page, record);
  await page.locator("[data-retry]").click(); await expect(page.locator("#cst-review")).toBeVisible();
  expect(rejected).toBe(true); pass(record);
});
test("C07 no interview identifier lands on the queue without reading an interview", async ({ newContext, app }, testInfo) => {
  const { page, record } = await session({ newContext, app }, "C07");
  let reads = 0; page.on("request", (request) => { if (request.url().includes("/api/admin/intake/consult/")) reads++; });
  await page.goto("/admin/consult");
  await expect(page).toHaveURL(/\/admin\/cola$/);
  expect(reads).toBe(0); await capture(testInfo, page, record); pass(record);
});
test("C08 real confirmation persists into synthetic database and returns print links", async ({ newContext, app }, testInfo) => {
  const { page, context, record } = await session({ newContext, app }, "C08");
  await ready(page, app, `interview=${app.fixture.doctor_patients.consultation_id}`);
  await page.fill("#cst-notes", "Synthetic verification note");
  await page.locator("#cst-review").click();
  await expect(page.locator("#cst-review-card")).toBeVisible();
  await expect(page.locator("#cst-review-card")).toContainText(/Synthetic verification note/, { useInnerText: true });
  const responseWait = page.waitForResponse((r) => r.url().endsWith("/confirm") && r.request().method() === "POST");
  await page.locator("#cst-confirmar").click();
  const response = await responseWait; expect(response.status()).toBe(200);
  const result = await response.json(); expect(result.ok).toBe(true);
  const stored = await queryConsultation(app.dbName, app.fixture.doctor_patients.consultation_id);
  expect(stored.found, "confirmation must create a database row").toBe(true);
  expect(stored.notas).toBe("Synthetic verification note");
  expect(stored.receta).toBe(true); expect(stored.orden).toBe(true);
  record.persistence = { found: stored.found, noteMatches: stored.notas === "Synthetic verification note", receta: stored.receta, orden: stored.orden };
  await expect(page.locator(".cst-done")).toBeVisible();
  for (const url of [result.receta_url, result.orden_url]) {
    expect(url).toBeTruthy();
    const document = await context.request.get(url);
    expect(document.status()).toBe(200);
    expect((await document.text()).includes("Synthetic verification note"), "returned document must belong to this confirmation").toBe(true);
  }
  await capture(testInfo, page, record); pass(record);
});
test("C09 read failure recovers through owned retry", async ({ newContext, app }, testInfo) => {
  const { page, record } = await session({ newContext, app }, "C09");
  let fail = true;
  await page.route("**/api/admin/intake/consult/fixture-consult", (route) => route.fulfill(fail ? json({ error: "unavailable" }, 503) : json(sample)));
  await page.goto("/admin/consult?interview=fixture-consult");
  await expect(page.locator("[data-consult-retry]")).toBeVisible(); fail = false;
  await page.locator("[data-consult-retry]").click(); await expect(page.locator("#cst-review")).toBeVisible();
  await capture(testInfo, page, record); pass(record);
});
test("C10 evidence and CLS negative controls reject absent or misleading proof", async ({ newContext, app }, testInfo) => {
  const { page, record } = await session({ newContext, app }, "C10"); await ready(page, app);
  expect(complete([])).toBe(false); expect(complete(records.slice(1))).toBe(false);
  expect(cumulativeLayoutShift([{ value: 0.2, startTime: 100 }])).toBeGreaterThan(0.1);
  expect(() => cumulativeLayoutShift([{ value: NaN, startTime: 1 }])).toThrow();
  // Deliberate rendered regression proves that a real observer, not just the
  // arithmetic helper, sees a large movement. It touches only this test page.
  record.observerNegativeControl = await page.evaluate(async () => {
    const start = performance.now();
    const spacer = document.createElement("div");
    spacer.style.height = "300px";
    document.body.prepend(spacer);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return [...window.__consultShifts, ...window.__consultObserver.takeRecords()
      .filter((e) => !e.hadRecentInput).map((e) => ({ value: e.value, startTime: e.startTime }))].filter((e) => e.startTime >= start);
  });
  expect(cumulativeLayoutShift(record.observerNegativeControl), "actual layout regression must fail the same CLS gate").toBeGreaterThan(0.1);
  await capture(testInfo, page, record); pass(record);
});

// C13/C14: the same font settlement for an English-speaking doctor (Accept-Language en-US, no
// cookie), where the admin header used to re-lay out when the typeface arrived.
for (const [id, failFonts, locale] of [["C11", false], ["C12", true], ["C13", false, "en-US"], ["C14", true, "en-US"]]) {
  test(`${id} consultation waits for real font settlement and survives font failure`, async ({ newContext, app, cleanup }, testInfo) => {
    const { page, record } = await session({ newContext, app }, id, 1024, locale, true);
    const gate = holdGate(cleanup);
    let requests = 0;
    // The theme's typefaces are self-hosted (core/theme.py, 2026-09-17):
    // hold the real first-party .woff2 requests the page makes.
    await page.route(/\/static\/fonts\/[^/?]+\.woff2(\?|$)/, async (route) => {
      requests++; await gate.held;
      return failFonts ? route.abort() : route.continue();
    });
    await sampleApi(page);
    const loaded = page.waitForResponse((r) => r.url().endsWith("/api/admin/intake/consult/fixture-consult"));
    await page.goto("/admin/consult?interview=fixture-consult", { waitUntil: "domcontentloaded" });
    await loaded;
    await page.waitForFunction(() => document.fonts.status === "loading");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(requests, "the real font requests must be held, not an empty fixture").toBeGreaterThan(0);
    expect(await page.locator("#cst-review").count(), "do not paint the form in a font that will immediately reflow").toBe(0);
    gate.release();
    await expect(page.locator("#cst-review")).toBeVisible({ timeout: 12000 });
    record.fontSettlement = await page.evaluate(() => ({ status: document.fonts.status, lang: document.documentElement.lang,
      shifts: [...window.__consultShifts, ...window.__consultObserver.takeRecords().filter((e) => !e.hadRecentInput).map(window.__consultShiftRecord)] }));
    expect(record.fontSettlement.status).toBe("loaded");
    expect(record.fontSettlement.lang, "the page speaks the visitor's language").toBe(locale ? "en" : "es");
    expect(cumulativeLayoutShift(record.fontSettlement.shifts)).toBeLessThanOrEqual(0.1);
    await capture(testInfo, page, record); pass(record);
  });
}

test("C15 a malformed diagnosis code gets the red border and, in English, says ICD-10", async ({ newContext, app }, testInfo) => {
  const { page, context, record } = await session({ newContext, app }, "C15", 390);
  await page.goto("about:blank");
  await context.addCookies([{ name: "lang", value: "en", url: app.baseUrl }]);
  await sampleApi(page); await ready(page, app, "interview=fixture-consult&lang=en");
  await page.locator("#cst-dx-custom-input").fill("Bronchitis");
  await page.locator("#cst-dx-custom-code").fill("bronq");
  await page.locator("[data-add-dx]").click();
  await expect(page.locator("#cst-dx-custom-code")).toHaveAttribute("aria-invalid", "true");
  // The code field is focused right after the failed add: the focus ring must not hide the red.
  await expect(page.locator("#cst-dx-custom-code")).toBeFocused();
  const colors = await page.evaluate(() => ["#cst-dx-custom-code", "#cst-dx-custom-input"].map((s) => getComputedStyle(document.querySelector(s)).borderTopColor));
  const danger = await page.evaluate(() => { const probe = document.createElement("i"); probe.style.color = "var(--danger)"; document.body.append(probe); const c = getComputedStyle(probe).color; probe.remove(); return c; });
  expect(colors[0], "the bad code keeps the danger border even while focused").toBe(danger);
  expect(colors[1]).not.toBe(danger);
  await capture(testInfo, page, record);
  await page.locator("#cst-dx-custom-code").fill("j209");
  await page.locator("[data-add-dx]").click();
  await expect(page.locator("#cst-dx-custom")).toContainText("ICD-10 J20.9");
  await expect(page.locator("#cst-dx-custom")).not.toContainText("CIE-10");
  pass(record);
});

test("evidence report is complete: all unique cases require assertions and nonempty durable captures", () => {
  expect(complete(records), "all unique cases require assertions and nonempty durable captures").toBe(true);
});
