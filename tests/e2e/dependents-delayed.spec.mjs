// Delayed native Dependents module on /account#deps and the pediatric wizard.
// Existing account-delayed e2e holds Account (the DOB control). This file
// holds platform/dependents.mjs itself: delayed decision, rejected-load
// recovery, back/cancel while in flight, and picker selection.
//
// Differences from the node:test original (tests/e2e/dependents-delayed.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), same setupHarness
//     options: account); the runner's `context` carries the patient session cookie (beforeEach) and its
//     `page` replaces the per-test context + page. The original passed { viewport } to
//     context.newPage(), which ignores it, so every page ran at the default 1280x720: that is the
//     viewport here too (nothing is set). The failed-import half of the wizard test needs a clean
//     context, so it takes a second one from newContext (cookie added again).
//   - The held module is a promise gate (holdGate) released by the test, as in the original. The
//     "Dependents was requested" Promise.race against a setTimeout(8 s) is expect.poll over the URL the
//     route saw (8 s); the route handler no longer asserts the pathname itself (an exception inside a
//     route handler would not fail the test): the same toMatch runs on the captured URL after the poll.
//   - holdsFor: "changing the time while Dependents is delayed cannot resurrect a stale patient
//     step" released the module, slept 350 ms and looked once; the absence of #dep-form is now checked
//     continuously for 350 ms after the release.
//   - waitForFunction/waitFor({state}) are expect(...).toBeVisible()/toHaveCount(0)/toContainText with
//     the original timeouts. Snapshots taken while the module is held, and the absence checks after
//     recovery, stay point-in-time page.evaluate reads.
//   - Locators: #acc-dependientes, #dep-form, .dep-choice, .wz-* stay CSS: they are the account/wizard
//     markup contract; the globals checked (SanaDependents, Dependents) are the subject of the test.
import { test, expect, patientCookie, holdGate, holdsFor } from "./lib/fixtures.mjs";

const MODULE_PATH = /\/static\/platform\/dependents\.[a-f0-9]{8}\.mjs$/;
const CLASSIC = /(?:^|\/)dependents\.js$/;
const PEDIATRIC_SERVICE_ID = "pediatria-riverside-clinic";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_dependents_delayed", account: true } });

test.beforeEach(async ({ context, app }) => {
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
});

/** Hold platform/dependents.mjs behind `gate`; getURL() is the URL the route saw (empty until requested). */
async function holdDependents(page, gate) {
  let moduleURL = "";
  await page.route("**/static/platform/dependents.*.mjs", async (route) => {
    moduleURL = route.request().url();
    gate.arrive();
    await gate.held;
    await route.continue();
  });
  return { getURL: () => moduleURL };
}

async function moduleRequested(held, message) {
  await expect.poll(() => held.getURL(), { message, timeout: 8_000 }).not.toBe("");
  expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);
}

test("delayed Dependents on /account#deps: no retired global, panel appears after release", async ({ page, app, cleanup }) => {
  expect(app.fixture.account_session, "fixture did not return the authenticated patient session").toBeTruthy();

  const requested = [];
  const failures = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  const gate = holdGate(cleanup);
  try {
    const held = await holdDependents(page, gate);
    const navigation = page.goto(app.baseUrl + "/account#deps", {
      waitUntil: "domcontentloaded", timeout: 20_000,
    });
    await moduleRequested(held, "platform/dependents.mjs was not requested from the manifest");
    const whileDelayed = await page.evaluate(() => ({
      title: !!document.querySelector("#acc-dependientes .dep h2"),
      form: !!document.getElementById("dep-form"),
      legacyGlobal: typeof window.SanaDependents,
      replacementGlobal: typeof window.Dependents,
      manifestURL: window.SANA_STATIC?.["platform/dependents.mjs"] || "",
      classic: !!document.querySelector('script[src*="dependents.js"]'),
    }));
    expect(whileDelayed.title, "the dependents panel must not paint while the module is held").toBe(false);
    expect(whileDelayed.form).toBe(false);
    expect(whileDelayed.legacyGlobal).toBe("undefined");
    expect(whileDelayed.replacementGlobal).toBe("undefined");
    expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);
    expect(whileDelayed.classic).toBe(false);

    gate.release();
    await navigation;
    await expect(page.locator("#acc-dependientes .dep h2")).toBeVisible({ timeout: 15_000 });
    const after = await page.evaluate(() => ({
      title: document.querySelector("#acc-dependientes .dep h2")?.textContent?.trim() || "",
      legacyGlobal: typeof window.SanaDependents,
      replacementGlobal: typeof window.Dependents,
    }));
    expect(after.title).toMatch(/Personas a cargo/);
    expect(after.legacyGlobal).toBe("undefined");
    expect(after.replacementGlobal).toBe("undefined");
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);
    expect(requested.some((path) => CLASSIC.test(path))).toBe(false);
    expect(failures).toEqual([]);
  } finally {
    gate.release();
  }
});

test("a rejected Dependents import on /account#deps shows localized recovery and recovers on reload", async ({ page, app }) => {
  let failFirst = true;
  await page.route("**/static/platform/dependents.*.mjs", async (route) => {
    if (failFirst) {
      failFirst = false;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });
  await page.goto(app.baseUrl + "/account#deps", {
    waitUntil: "domcontentloaded", timeout: 20_000,
  });
  await expect(page.locator("#acc-dependientes")).toContainText(/No se pudo cargar esta sección|Recargá la página/i, { timeout: 10_000 });
  expect(await page.evaluate(() => typeof window.SanaDependents)).toBe("undefined");
  expect(await page.evaluate(() => !!document.querySelector("#acc-dependientes .dep"))).toBe(false);

  await page.reload({ waitUntil: "domcontentloaded", timeout: 20_000 });
  await expect(page.locator("#acc-dependientes .dep h2")).toBeVisible({ timeout: 15_000 });
  expect(await page.evaluate(() => typeof window.SanaDependents)).toBe("undefined");
});

test("pediatric wizard waits for Dependents; failed import shows reload recovery instead of skipping", async ({ page, app, newContext, cleanup }) => {
  const gate = holdGate(cleanup);
  try {
    const held = await holdDependents(page, gate);
    await page.goto(app.baseUrl + `/service/${PEDIATRIC_SERVICE_ID}/book`, {
      waitUntil: "load", timeout: 20_000,
    });
    await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15_000 });
    await page.locator(".wz-seg-opt").first().click();
    await moduleRequested(held, "platform/dependents.mjs was not requested when deciding the patient");
    const whileDelayed = await page.evaluate(() => ({
      form: !!document.getElementById("dep-form"),
      confirm: !!document.getElementById("wz-enviar") || !!document.querySelector(".wz-cta-lg"),
      legacyGlobal: typeof window.SanaDependents,
    }));
    expect(whileDelayed.form, "the add-child form must not paint while Dependents is held").toBe(false);
    expect(whileDelayed.legacyGlobal).toBe("undefined");

    gate.release();
    await expect(page.locator("#dep-form")).toBeVisible({ timeout: 15_000 });
    expect(await page.evaluate(() => typeof window.SanaDependents)).toBe("undefined");
  } finally {
    gate.release();
  }

  const failContext = await newContext();
  await failContext.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const failed = await failContext.newPage();
  await failed.route("**/static/platform/dependents.*.mjs", async (route) => {
    await route.abort("failed");
  });
  await failed.goto(app.baseUrl + `/service/${PEDIATRIC_SERVICE_ID}/book`, {
    waitUntil: "load", timeout: 20_000,
  });
  await expect(failed.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15_000 });
  await failed.locator(".wz-seg-opt").first().click();
  await expect(failed.locator(".wz-soft")).toContainText(/No pudimos cargar la selección de paciente/i, { timeout: 10_000 });
  const recovery = await failed.evaluate(() => ({
    text: document.querySelector(".wz-soft")?.textContent?.trim() || "",
    href: document.querySelector(".wz-soft a")?.getAttribute("href") || "",
    form: !!document.getElementById("dep-form"),
    legacyGlobal: typeof window.SanaDependents,
  }));
  expect(recovery.text).toMatch(/Recargar la página/);
  expect(recovery.href, "wizard recovery reloads this booking, not another patient's page").toBe(failed.url());
  expect(recovery.form).toBe(false);
  expect(recovery.legacyGlobal).toBe("undefined");
});

test("changing the time while Dependents is delayed cannot resurrect a stale patient step", async ({ page, app, cleanup }) => {
  const gate = holdGate(cleanup);
  try {
    const held = await holdDependents(page, gate);
    await page.goto(app.baseUrl + `/service/${PEDIATRIC_SERVICE_ID}/book`, {
      waitUntil: "load", timeout: 20_000,
    });
    await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15_000 });
    await page.locator(".wz-seg-opt").first().click();
    await moduleRequested(held, "Dependents was not requested");
    const changeTime = page.locator("#wz-auth-change-time, .wz-change-link").first();
    if (await changeTime.count()) {
      await changeTime.click();
    } else {
      await page.goBack({ waitUntil: "domcontentloaded", timeout: 10_000 });
    }
    await expect(page.locator("#dep-form")).toHaveCount(0, { timeout: 10_000 });
    gate.release();
    await holdsFor(async () => {
      const after = await page.evaluate(() => ({
        form: !!document.getElementById("dep-form"),
        title: document.getElementById("svc-title")?.textContent?.trim() || "",
      }));
      expect(after.form, "a detached patient host must not be repainted after leaving the step").toBe(false);
    }, { during: 350 });
  } finally {
    gate.release();
  }
});

test("picker selection uses the native module: two eligible children, choosing the second is final", async ({ page, app }) => {
  await page.route("**/api/account/dependents", async (route) => {
    if (route.request().method() !== "GET") { await route.continue(); return; }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        dependientes: [
          { dni: "50111001", nombre: "Lila", apellido: "Test", nacimiento: "2018-06-15", vinculo: "hijo" },
          { dni: "50111002", nombre: "Mauro", apellido: "Test", nacimiento: "2014-03-02", vinculo: "hijo" },
        ],
      }),
    });
  });
  await page.goto(app.baseUrl + `/service/${PEDIATRIC_SERVICE_ID}/book`, {
    waitUntil: "load", timeout: 20_000,
  });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15_000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator(".dep-choice").first()).toBeVisible({ timeout: 15_000 });
  const names = await page.locator(".dep-choice .dep-name").allTextContents();
  expect(names.some((n) => /Lila/.test(n))).toBe(true);
  expect(names.some((n) => /Mauro/.test(n))).toBe(true);
  await page.locator(".dep-choice").nth(1).click();
  await expect(page.locator(".dep-choice")).toHaveCount(0, { timeout: 10_000 });
  const after = await page.evaluate(() => ({
    form: !!document.getElementById("dep-form"),
    choices: document.querySelectorAll(".dep-choice").length,
    confirmVisible: !!(document.getElementById("wz-enviar") || document.querySelector(".wz-cta-lg")),
    legacyGlobal: typeof window.SanaDependents,
  }));
  expect(after.choices, "selection must leave the picker").toBe(0);
  expect(after.legacyGlobal).toBe("undefined");
});
