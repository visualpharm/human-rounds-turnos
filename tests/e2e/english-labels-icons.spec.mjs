// English panel labels computed at module evaluation (before the async i18n
// import settles) stayed Spanish: the «Médicos» crumb on a doctor's editor,
// the agenda's day checkboxes, the Requests group titles. The DNI photo
// buttons and the review-report topics carried pictographs instead of the
// project's Icons8 icons.
// Real ASGI app, synthetic example-town database; screenshots are evidence.
//
// Port of tests/e2e/english-labels-icons.e2e.mjs (8 tests: 4 cases x 2 widths, same titles,
// same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `requests: true`). The original's `test.before` (sign in, enable the synthetic English
//   catalog through the settings API) is a `test.beforeAll` on a throwaway context of the
//   runner's browser; each test gets the runner's context and page, sets the width with
//   page.setViewportSize(), adds the `lang=en` cookie and signs in with loginAdmin().
// - Evidence screenshots go through saveShot(): the same ~/.claude/artifacts/pinamar-turnos/
//   shots-andres-followups/ files as before (the directory is created by saveShot) and the
//   test report. The original's try/finally context close is the runner's.
// - Waits are web-first (toBeVisible with the original 8-15 s timeouts, toHaveText with
//   innerText). The geometry and computed-style reads (icon mask, 18px/20px sizes, list of
//   day labels) stay point-in-time evaluate() snapshots, which is what they assert on.
// - The route handler of the Centers report no longer asserts inside the handler (an
//   exception there is not a test failure): it records that no analysed center was found and
//   the test asserts that after the page is loaded.
// - Locators left as CSS: `.adm-crumb`, `.cnt-daychk`, `#list .sol-row`, `#f-verdict`,
//   `.rep-triage-*`, `#sc-scan-*`/`.sc-scan-ico` - class/id hooks whose computed style or
//   geometry is the subject, or no accessible name exists to query.
import os from "node:os";
import path from "node:path";
import { test, expect, loginAdmin, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_english_labels_icons", requests: true } });

const evidence = path.join(os.homedir(), ".claude/artifacts/pinamar-turnos/shots-andres-followups");
const PICTOGRAPH = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;

test.beforeAll(async ({ browser, app }) => {
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await loginAdmin(page, app.baseUrl);
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.ok(), "enable the synthetic English catalog").toBe(true);
  } finally { await context.close(); }
});

/** English panel session at `width`: lang cookie, signed in, page errors collected. */
async function signInEnglish({ context, page, app }, width) {
  await page.setViewportSize({ width, height: 900 });
  await context.addCookies([{ name: "lang", value: "en", url: app.baseUrl }]);
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await loginAdmin(page, app.baseUrl);
  return errors;
}

for (const width of [390, 1280]) {
  test(`English doctor editor: crumb and agenda days are English at ${width}px`, async ({ context, page, app }, testInfo) => {
    const errors = await signInEnglish({ context, page, app }, width);
    await page.goto("/admin/doctors?item=gonzalez-juan", { waitUntil: "load", timeout: 20000 });
    await expect(page.locator("#cnt-form")).toBeVisible({ timeout: 12000 });
    const crumb = page.locator(".adm-crumb").first();
    await expect(crumb).toBeVisible({ timeout: 8000 });
    await expect(crumb).toHaveText("Doctors", { useInnerText: true });
    // textContent: the agenda tab may be closed; its checkboxes still render.
    const days = await page.locator(".cnt-daychk span").evaluateAll((els) => els.map((el) => el.textContent));
    for (const day of days) expect(["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"], `Spanish day ${day}`).not.toContain(day);
    await page.evaluate(() => window.scrollTo(0, 0));
    await saveShot(testInfo, page, path.join(evidence, `admin-doctor-en-${width}.png`));
    expect(errors).toEqual([]);
  });

  test(`English Requests: group title and AI filter are English at ${width}px`, async ({ context, page, app }, testInfo) => {
    const errors = await signInEnglish({ context, page, app }, width);
    await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 20000 });
    await expect(page.locator("#list .sol-row").first()).toBeVisible({ timeout: 15000 });
    const text = await page.locator("main").innerText();
    expect(text).not.toMatch(/Revisar a mano|Aprobar según sugerencia|Rechazar según sugerencia/);
    const options = await page.locator("#f-verdict option").allInnerTexts();
    for (const label of options) {
      expect(label).not.toMatch(/Parece correcta|Dudosa|Revisión manual|Ilegible|Documento equivocado|Pendiente de análisis/);
    }
    await saveShot(testInfo, page, path.join(evidence, `admin-requests-en-${width}.png`));
    expect(errors).toEqual([]);
  });

  test(`Centers report: each triage topic carries an Icons8 icon, no pictograph, at ${width}px`, async ({ context, page, app }, testInfo) => {
    const errors = await signInEnglish({ context, page, app }, width);
    // example-town's review analysis carries no triage: extend the REAL
    // response with two fictional topics, one mapped and one unmapped
    // (the clipboard fallback), one per group.
    let analysedCenter = false;
    await page.route("**/api/admin/reports/reviews", async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      const slug = Object.keys(data.centers || {})[0];
      analysedCenter = !!slug;
      if (slug) {
        data.centers[slug].triage = {
          fix: [{ topic: "Espera en guardia", action: "Synthetic action for the waiting room." }],
          decline: [{ topic: "Synthetic unmapped topic", action: "Synthetic declined action." }],
        };
      }
      await route.fulfill({ response, json: data });
    });
    await page.goto("/admin/reportes?view=centros", { waitUntil: "load", timeout: 20000 });
    const first = page.locator(".rep-triage-topic-h").first();
    await expect(first).toBeVisible({ timeout: 15000 });
    expect(analysedCenter, "example-town has an analysed center").toBe(true);
    const heads = await page.locator(".rep-triage-grupo, .rep-triage-topic-h").evaluateAll((els) => els.map((el) => {
      const ico = el.querySelector(".rep-triage-ico");
      const s = ico && getComputedStyle(ico), r = ico && ico.getBoundingClientRect();
      return { text: el.textContent, mask: s ? (s.maskImage || s.webkitMaskImage) : "", w: r ? r.width : 0 };
    }));
    expect(heads.length).toBeGreaterThanOrEqual(2);
    for (const head of heads) {
      expect(head.text).not.toMatch(PICTOGRAPH);
      expect(head.mask).toMatch(/\/static\/icons\/report-[a-z-]+-sf-regular\.png/);
      expect(head.w).toBe(18);
    }
    const masks = heads.map((head) => head.mask).join(" ");
    for (const name of ["lightning", "stopwatch", "compass", "clipboard"]) {
      expect(masks, name).toMatch(new RegExp(`report-${name}-sf-regular`));
    }
    await first.scrollIntoViewIfNeeded();
    await saveShot(testInfo, page, path.join(evidence, `admin-report-triage-${width}.png`));
    expect(errors).toEqual([]);
  });

  test(`DNI photo buttons carry the Icons8 camera, no pictograph, at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/account?mode=register", { waitUntil: "load", timeout: 20000 });
    for (const id of ["#sc-scan-f", "#sc-scan-d"]) {
      const button = page.locator(id);
      await expect(button).toBeVisible({ timeout: 10000 });
      expect(await button.innerText()).not.toMatch(PICTOGRAPH);
      const icon = await button.locator(".sc-scan-ico").evaluate((el) => {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return { mask: s.maskImage || s.webkitMaskImage, w: r.width, h: r.height };
      });
      expect(icon.mask).toMatch(/camera-sf-regular\.png/);
      expect(icon.w).toBe(20);
      expect(icon.h).toBe(20);
    }
    const mask = await page.evaluate(() => fetch("/static/icons/camera-sf-regular.png").then((r) => r.status));
    expect(mask).toBe(200);
    await page.locator(".sc-scan").scrollIntoViewIfNeeded();
    await saveShot(testInfo, page, path.join(evidence, `account-dni-buttons-${width}.png`));
  });
}
