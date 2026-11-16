// Actual native doctor screens, synthetic local database only. Lifecycle events
// exercise ownership, not browser BFCache admission; no CLS claim is made.
//
// Port of tests/e2e/admin-doctor-lifecycle.e2e.mjs (13 tests, same titles, same order).
//
// Differences from the node:test original:
// - The tests share one record (`measurements`, checked for completeness by the last test and
//   written to measurements.json after the file), so the file is `serial`: a failure skips the
//   rest and a retry re-runs the whole file on a fresh worker with a fresh record.
// - The original's `test.before` (admin enables the English catalog through the settings API)
//   is `test.beforeAll`; the `test.after` that writes measurements.json is `test.afterAll`.
// - `session(...)` builds the doctor's (or admin's) context with the `newContext` fixture
//   (closed by the runner); the original's `finally { context.close() }` blocks are gone. The
//   login keeps the original sequence (no `next`, the doctor lands role-aware).
// - Screenshots go through `saveShot` to the same files as before and are attached to the report.
// - Waits are web-first (`expect(locator)...`; `waitFor({ state: "detached" })` is `toHaveCount(0)`);
//   the 12 s wait for the late copy of the held import is an explicit `{ timeout: 12000 }`.
// - The `waitForTimeout` «disposed response cannot replace the next owner» is a `holdsFor`
//   window of the same length (250 ms): the claim there IS that nothing changes after the held
//   request is released. (The held import now mounts late, 2026-11-16.)
// - `try/finally { release() }` is `holdGate(cleanup)`, which releases on teardown.
// - Locators that stay CSS: the screen hosts (`#sec-mis-turnos`, `#sec-mis-pacientes`), `[data-retry]`
//   and `#gate-*` are the markup contract the lifecycle is about; geometry is read with `evaluate`.
import fs from "node:fs/promises";
import {
  test, expect, saveShot, holdGate, holdsFor, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_doctor_native_lifecycle", doctorPatients: true },
  viewport: { width: 1280, height: 900 },
});
test.describe.configure({ mode: "serial" });

const evidence = artifactPath("human-rounds", "doctor-native-lifecycle-audit", "browser-" + new Date().toISOString().replaceAll(":", "-"));
const widths = [360, 390, 768, 1024, 1280];
const screens = [
  { path: "/admin/mis-turnos", host: "#sec-mis-turnos", stem: "admin-my-appointments", api: "/api/admin/my-appointments?days=14", empty: { medico: null, turnos: [] } },
  { path: "/admin/my-patients", host: "#sec-mis-pacientes", stem: "admin-doctor-patients", api: "/api/admin/doctor-patients", empty: { items: [] } },
];
const measurements = [];

async function session({ newContext, app }, lang = "es", admin = false) {
  const context = await newContext({ viewport: { width: 1280, height: 900 } });
  await context.addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/admin/login");
  await expect(page.locator("#gate-form")).toBeVisible();
  await page.fill("#gate-user", admin ? ADMIN_USER : app.fixture.doctor_patients.medico_user);
  await page.fill("#gate-pass", admin ? ADMIN_PASS : app.fixture.doctor_patients.medico_pass);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login");
  return { context, page, errors };
}

test.beforeAll(async ({ app, browser }) => {
  expect(app.ok, app.reason).toBeTruthy();
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await page.goto("/admin/login");
    await expect(page.locator("#gate-form")).toBeVisible();
    await page.fill("#gate-user", ADMIN_USER);
    await page.fill("#gate-pass", ADMIN_PASS);
    await page.click("#gate-btn");
    await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login");
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.ok(), "enable synthetic English and Spanish catalogs").toBeTruthy();
  } finally { await context.close(); }
});

test.afterAll(async () => {
  await fs.mkdir(evidence, { recursive: true });
  await fs.writeFile(evidence + "/measurements.json", JSON.stringify({ expected: 20, measurements }, null, 2));
});

for (const screen of screens) {
  for (const lang of ["es", "en"]) {
    test(`${screen.path} populated ${lang}, five actual viewport measurements`, async ({ newContext, app }, testInfo) => {
      const { page, errors } = await session({ newContext, app }, lang);
      await page.goto(screen.path);
      await expect(page.locator(screen.host + " tbody tr").first()).toBeVisible();
      await expect(page.locator(screen.host)).toContainText(/Roster/, { useInnerText: true });
      for (const width of widths) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(() => document.fonts.ready);
        const geometry = await page.evaluate((hostSelector) => {
          const host = document.querySelector(hostSelector), r = host.getBoundingClientRect();
          return { viewport: innerWidth, documentWidth: document.documentElement.scrollWidth,
            left: r.left, right: r.right, rows: host.querySelectorAll("tbody tr").length,
            title: document.querySelector("#adm-h1").textContent.trim() };
        }, screen.host);
        measurements.push({ path: screen.path, lang, width, ...geometry });
        expect(geometry.rows).toBeGreaterThan(0);
        expect(geometry.documentWidth, JSON.stringify(geometry)).toBeLessThanOrEqual(geometry.viewport + 1);
        expect(geometry.left, JSON.stringify(geometry)).toBeGreaterThanOrEqual(-1);
        expect(geometry.right, JSON.stringify(geometry)).toBeLessThanOrEqual(geometry.viewport + 1);
        await saveShot(testInfo, page, `${evidence}/${screen.stem}-${lang}-${width}.png`, { fullPage: true });
      }
      expect(errors).toEqual([]);
    });
  }
  test(`${screen.path} malformed success is recoverable, then valid empty response`, async ({ newContext, app }) => {
    const { page, errors } = await session({ newContext, app });
    let malformed = true;
    await page.route(app.baseUrl + screen.api, (route) => route.fulfill({ json: malformed ? {} : screen.empty }));
    await page.goto(screen.path);
    const retry = page.locator(screen.host + " [data-retry]");
    await expect(retry).toBeVisible();
    malformed = false;
    await retry.click();
    await expect(retry).toHaveCount(0);
    expect(await page.locator(screen.host + " tbody tr").count()).toBe(0);
    expect((await page.locator(screen.host).innerText()).trim().length).toBeGreaterThan(0);
    expect(errors).toEqual([]);
  });
  // 2026-11-16 (static/platform/screen-boot.mjs): the 8 s deadline only says the load is
  // late, beside the screen; the held import still mounts it and clears the copy.
  test(`${screen.path} held import past the deadline shows the late copy, then mounts`, async ({ newContext, app, cleanup }) => {
    const { page, errors } = await session({ newContext, app });
    const gate = holdGate(cleanup);
    const pattern = new RegExp("/static/platform/" + screen.stem + "\\.[a-f0-9]+\\.mjs");
    const late = page.locator(".screen-load-slow [data-retry]");
    await page.route(pattern, async (route) => { await gate.held; await route.continue(); });
    const navigation = page.goto(screen.path, { waitUntil: "domcontentloaded" });
    await expect(late).toBeVisible({ timeout: 12000 });
    expect(await page.locator(screen.host + " [data-retry]").count(), "the copy never replaces the screen").toBe(0);
    gate.release();
    await navigation;
    await expect(page.locator(screen.host + " tbody tr").first()).toBeVisible();
    await expect(page.locator(".screen-load-slow")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
  test(`${screen.path} rejected import offers working reload`, async ({ newContext, app }, testInfo) => {
    const { page, errors } = await session({ newContext, app });
    const pattern = new RegExp("/static/platform/" + screen.stem + "\\.[a-f0-9]+\\.mjs");
    await page.route(pattern, (route) => route.abort("failed"));
    await page.goto(screen.path);
    await expect(page.locator(screen.host + " [data-retry]")).toBeVisible();
    await saveShot(testInfo, page, `${evidence}/${screen.stem}-recovery-1280.png`, { fullPage: true });
    await page.unroute(pattern);
    await page.locator(screen.host + " [data-retry]").click();
    await expect(page.locator(screen.host + " tbody tr").first()).toBeVisible();
    expect(errors).toEqual([]);
  });
  test(`${screen.path} disposed response cannot replace the next owner`, async ({ newContext, app, cleanup }) => {
    const { page, errors } = await session({ newContext, app });
    const gate = holdGate(cleanup);
    await page.route(app.baseUrl + screen.api, async (route) => {
      const response = await route.fetch();
      gate.arrive();
      await gate.held;
      await route.fulfill({ response });
    });
    await page.goto(screen.path, { waitUntil: "domcontentloaded" });
    await gate.arrived;
    await page.evaluate((selector) => {
      dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
      document.querySelector(selector).textContent = "Replacement owner";
    }, screen.host);
    gate.release();
    // The stale response, now delivered, must leave the replacement owner untouched.
    await holdsFor(async () => {
      expect(await page.locator(screen.host).innerText()).toBe("Replacement owner");
    }, { during: 250 });
    await page.unroute(app.baseUrl + screen.api);
    await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
    await expect(page.locator(screen.host + " tbody tr").first()).toBeVisible();
    expect(errors).toEqual([]);
  });
}

// A full day fits its panel (2026-10-27). The columns are measured at their
// natural width: measured at 100 % the browser spread the spare width over
// every column, so at 1440 a long name was cut while Hora and DNI stood wide,
// and at 1280 the day's table ran past its panel. The name is not elastic any
// more: it keeps what it measures and the summary and the contact give way;
// the spare width goes to an empty last column. When not even the minimums
// fit, a lower-rank column hides — the pre-visit interview last — and time,
// patient, contact and status always stay. Checked with the interview on and
// a paragraph-long summary, the widest day there is, down to 1101 px (at 1100
// the day becomes two-line rows).
test("/admin/mis-turnos a day of long names, contacts and summaries fits its panel from 1101 to 1440", async ({ newContext, app }, testInfo) => {
  const { page, errors } = await session({ newContext, app });
  const largos = [
    ["Graciela Beatriz Sosa de Benítez", "graciela.sosa.benitez@correo-ejemplo.com.ar", "no", true],
    ["María de los Ángeles Fernández", "+54 9 2254 41-0012", "si", false],
    ["Roberto Carlos Domínguez", "+54 9 2254 10-0099", null, true],
    ["Juan Pablo Ruiz Díaz", "juanpablo.ruizdiaz@correo-ejemplo.com.ar", "si", false],
  ];
  const resumen = "Dolor lumbar de tres semanas que empeora al estar sentada; toma ibuprofeno sin alivio; sin fiebre ni pérdida de peso.";
  await page.route(app.baseUrl + "/api/admin/my-appointments?days=14", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const base = body.turnos[0];
    body.entrevista_activa = true;
    body.turnos = largos.map(([paciente, contacto, confirmacion, sobreturno], i) => ({
      ...base, id: `${base.id}-${i}`, hora: `${String(9 + i).padStart(2, "0")}:00`, paciente, contacto, confirmacion, sobreturno,
      ranking: { banda: "falta-a-veces", etiqueta: "Falta a veces", nivel: "mid", puntaje: 60, motivos: [], sin_historial: false },
      entrevista: { ...(base.entrevista || {}), triage: "AMARILLO", resumen },
    }));
    await route.fulfill({ response, json: body });
  });
  await page.goto("/admin/mis-turnos");
  await expect(page.locator("#sec-mis-turnos tbody tr")).toHaveCount(largos.length);
  for (const width of [1101, 1180, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => document.fonts.ready);
    await expect.poll(() => page.evaluate(() => {
      const out = { width: innerWidth, documentOver: document.documentElement.scrollWidth - innerWidth, tableOver: 0, cut: [], hidden: [] };
      for (const table of document.querySelectorAll("#sec-mis-turnos .mt-day table")) {
        out.tableOver = Math.max(out.tableOver, Math.round(table.getBoundingClientRect().width - table.closest(".cnt-table-wrap").clientWidth));
        for (const td of table.querySelectorAll("td[data-col='paciente']")) {
          if (td.scrollWidth > td.clientWidth + 1) out.cut.push(td.textContent.trim());
        }
        for (const key of ["hora", "paciente", "contacto", "estado"]) {
          const td = table.querySelector(`tbody td[data-col='${key}']`);
          if (!td || td.getBoundingClientRect().width === 0) out.hidden.push(key);
        }
      }
      return out.documentOver <= 1 && out.tableOver <= 1 && !out.cut.length && !out.hidden.length ? "fits" : JSON.stringify(out);
    })).toBe("fits");
    await saveShot(testInfo, page, `${evidence}/admin-my-appointments-long-names-${width}.png`, { fullPage: true });
  }
  expect(errors).toEqual([]);
});

// The doctor's calendar subscription (2026-10-27): «Agregar a mi calendario»
// opens the personal address with the Google / Apple steps; «Cambiar la
// dirección» reissues it and the old one is gone from the panel. The address
// never widens the page, open or closed, phone or desktop.
test("/admin/mis-turnos calendar subscription opens, copies and rotates the address", async ({ newContext, app }, testInfo) => {
  const { page, errors } = await session({ newContext, app });
  await page.goto("/admin/mis-turnos");
  await expect(page.locator("#sec-mis-turnos tbody tr").first()).toBeVisible();
  await expect(page.locator("#mt-cal")).toBeHidden();
  await page.click("#mt-cal-open");
  const address = page.locator("#mt-cal .mt-cal-url input");
  await expect(address).toBeVisible();
  const url = await address.inputValue();
  expect(url).toMatch(/\/feed\/doctor\/[A-Za-z0-9_-]+\.ics$/);
  await expect(page.locator("#mt-cal")).toContainText("Google Calendar");
  for (const width of [390, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width + 1);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await saveShot(testInfo, page, `${evidence}/admin-my-appointments-cal-1280.png`, { fullPage: true });
  await page.click("#mt-cal [data-cal-rotate]");
  await expect(page.locator("#mt-cal")).toContainText("deja de funcionar");
  await page.click("#mt-cal [data-cal-rotate-yes]");
  await expect(address).not.toHaveValue(url);
  // The old address's 404 and the new token resolving are the API's contract,
  // pinned server-side in test_doctor_appointments.py: in this harness the
  // painted address carries the DEFAULT_ORIGIN (127.0.0.1:7700), which is not
  // this app, so it cannot be probed from here.
  // Shown once (alembic 0039): the next visit names only its last 4 characters.
  const rotated = await address.inputValue();
  await page.reload();
  await expect(page.locator("#sec-mis-turnos tbody tr").first()).toBeVisible();
  await page.click("#mt-cal-open");
  await expect(page.locator("#mt-cal")).toContainText(`la que termina en ${rotated.slice(-8, -4)}`);
  await expect(page.locator("#mt-cal .mt-cal-url input")).toHaveCount(0);
  expect(await page.content(), "the address is not on the page again").not.toContain(rotated.slice(-26, -4));
  await saveShot(testInfo, page, `${evidence}/admin-my-appointments-cal-hint-1280.png`, { fullPage: true });
  expect(errors).toEqual([]);
});

test("measurement report has every required unique populated combination", () => {
  expect(measurements).toHaveLength(20);
  expect(new Set(measurements.map((row) => `${row.path}/${row.lang}/${row.width}`)).size).toBe(20);
});
