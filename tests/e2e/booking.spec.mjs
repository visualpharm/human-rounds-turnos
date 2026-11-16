// tests/e2e/booking.spec.mjs -- Tier-2 pre-move gate: the patient booking
// wizard, driven with a real headless browser against a real running
// server (not a fetch/curl smoke -- service.js's step transitions only
// exist client-side). Run via `npm run e2e` -- the GATE: a missing
// Playwright/Chromium/Postgres prerequisite FAILS this suite (via
// the `_gate` fixture, see lib/fixtures.mjs), it never skips quietly. Local
// convenience mode that DOES skip on a missing prerequisite is
// `npm run e2e:dev` only.
//
// Service under test: electrocardiograma-hospital ("Electrocardiograma" at
// Hospital de Sampleton, packs/example-town/servicios.json -- the public
// synthetic pack the gate runs against, GATE_PACK_CHAIN in lib/harness.mjs;
// NEVER the private Pinamar pack, per Codex's item 4/5), modo
// solicitud_con_orden with a real Mon-Fri weekly agenda -- chosen because
// it exercises BOTH branches docs/architecture/wizard-y-servicio.md
// describes: the default order-first wizard, and (via "Más horarios") the
// reversed Cuándo-first wizard that lands on real slot selection.
//
// Port of tests/e2e/booking.e2e.mjs (4 tests, same titles, same order).
//
// Differences from the node:test original:
// - The browser, page and contexts come from the runner (`page`, `newContext`); the
//   file's `test.before/after` become the worker-scoped `app` fixture
//   (`test.use({ appOptions })`, same dbName and `account: true`).
// - The last two tests hand state to each other (the request the third one submits is
//   what the account page of the fourth one lists), so the file is `serial`.
// - Waits and assertions are web-first (`expect(locator)...` with the original explicit
//   timeouts) where the original called `waitFor` and then compared; the «calendar
//   interaction must not navigate» and «calendar closes after selecting a date» checks
//   stay point-in-time reads (they catch a reaction that happens right at the click).
// - The «home-search readiness failure» diagnostic (browser failures, page errors, search
//   module state) is kept: it is attached to the report as `home-search-readiness.json`
//   and the original error is re-thrown.
// - The 600 ms delay of `/api/services` is the SUBJECT of the first test (the visitor
//   types while the catalog is still loading), so it stays a timed route delay.
// - The file is chosen once, as soon as `#svc-title` is visible, exactly as in the original (no re-delivery
//   helper: the wizard re-rendering its upload step while it boots used to lose a file set on the replaced
//   input, which made this test float; cabc540d fixed that in the product and
//   booking-upload-chooser.spec.mjs pins it).
// - Locators: ids/classes/data-* stay CSS (#q, #resultados, #svc-title, #wz-file,
//   #wz-send, .wz-seg-opt, .wz-otras, [data-slot-date]): they are the wizard's markup
//   contract and some are read for their aria-* wiring; the service-name checks use
//   `getByText`.
import path from "node:path";
import {
  test, expect, queryRequest, patientCookie, ROOT,
} from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_booking", account: true } });
test.describe.configure({ mode: "serial" });

const SERVICE_ID = "electrocardiograma-hospital";
const SERVICE_NAME = "Electrocardiograma";

test("home search finds the service and its name link opens the booking wizard (order-first)", async ({ page }, testInfo) => {
  const browserFailures = [];
  page.on("requestfailed", (request) => {
    browserFailures.push({
      path: new URL(request.url()).pathname,
      error: request.failure()?.errorText || "request failed",
    });
  });
  page.on("response", (response) => {
    if (response.status() >= 400) {
      browserFailures.push({ path: new URL(response.url()).pathname, status: response.status() });
    }
  });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  // Make the historical startup race deterministic: the visitor types
  // while the catalog is still loading, before the listener is attached.
  // The catalog request is held until the visitor has typed (no timing involved).
  let releaseCatalog;
  const catalogHeld = new Promise((resolve) => { releaseCatalog = resolve; });
  await page.route("**/api/services", async (route) => {
    await catalogHeld;
    await route.continue();
  });
  await page.goto("/", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#q")).toBeVisible({ timeout: 5000 });

  await page.fill("#q", SERVICE_NAME);
  releaseCatalog();
  const nameLink = page
    .locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`)
    .first();
  try {
    await expect(nameLink).toBeVisible({ timeout: 15000 });
  } catch (error) {
    const stateAtFailure = await page.evaluate(() => ({
      query: document.getElementById("q")?.value || "",
      resultsText: document.getElementById("resultados")?.innerText || "",
      searchApi: typeof window.SanaSearch,
      searchModule: window.SANA_STATIC?.["platform/search.mjs"] || "",
    }));
    await testInfo.attach("home-search-readiness.json", {
      body: JSON.stringify({ browserFailures, pageErrors, stateAtFailure }, null, 2),
      contentType: "application/json",
    });
    throw error;
  }
  await expect(nameLink).toHaveText(SERVICE_NAME);

  await nameLink.click();
  await page.waitForURL(`**/service/${SERVICE_ID}/book`, { timeout: 10000 });

  // Order-first is the DEFAULT for solicitud_con_orden (no ?slots=1/?slot=)
  // -- this is "the referral-order branch at least loads".
  const title = page.locator("#svc-title");
  await expect(title).toBeVisible({ timeout: 8000 });
  await expect(title).toHaveText("Subí tu orden");

  await expect(page.locator("#svc-form-host")).toHaveText(/orden/i, { useInnerText: true });
});

test("\"Más horarios\" on the service page reaches real slot selection", async ({ page }) => {
  await page.goto(`/service/${SERVICE_ID}`, { waitUntil: "load", timeout: 15000 });

  const masHorarios = page.locator("a.mp-more");
  await expect(masHorarios).toBeVisible({ timeout: 8000 });
  await expect(masHorarios).toHaveText(/horario/i);

  await masHorarios.click();
  await page.waitForURL(`**/service/${SERVICE_ID}/book?slots=1`, { timeout: 10000 });

  // Real slot-selection screen: at least one clickable time button
  // (wz-seg-opt, service.js's stepHorario()/renderSlots()), not just a
  // 200 on the URL.
  const slotButtons = page.locator(".wz-seg-opt");
  await expect(slotButtons.first()).toBeVisible({ timeout: 8000 });
  expect(await slotButtons.count(), "expected at least one bookable time slot").toBeGreaterThan(0);

  // Exercise the canonical calendar-grid.mjs path in a real browser, not
  // merely the source/import contract.  Each agenda owns its own calendar;
  // opening it and selecting an available day must filter that agenda's
  // visible slots while keeping the wizard on the same URL.
  const otherDates = page.locator(".wz-otras").first();
  await expect(otherDates).toBeVisible({ timeout: 8000 });
  const beforeCalendarUrl = page.url();
  await otherDates.click();
  // Read once, right after the click, as the original did: the toggle sets
  // aria-expanded synchronously in its click handler.
  expect(await otherDates.getAttribute("aria-expanded")).toBe("true");
  const calendar = page.locator(`#${await otherDates.getAttribute("aria-controls")}`);
  await expect(calendar).toBeVisible({ timeout: 8000 });
  const availableDay = calendar.locator("[data-slot-date]:not([disabled])").first();
  await expect(availableDay).toBeVisible({ timeout: 8000 });
  const selectedDate = await availableDay.getAttribute("data-slot-date");
  await availableDay.click();
  expect(page.url(), "calendar interaction must not navigate").toBe(beforeCalendarUrl);
  expect(await calendar.isHidden(), "calendar closes after selecting a date").toBe(true);
  expect(selectedDate, "calendar day exposes its ISO date").toBeTruthy();
  await expect(
    page.locator(`.wz-day:not([hidden]) [data-dia="${selectedDate}"]`).first(),
  ).toBeAttached({ timeout: 8000 });

  // Picking a slot advances the wizard for real (into the order step,
  // since the order is still missing) -- proves the step transition
  // wiring, not just that the grid painted.
  await slotButtons.first().click();
  const title = page.locator("#svc-title");
  await expect(title).not.toHaveText("Elegí un horario", { timeout: 8000 });
  await expect(title, "wizard should show a real next-step title after picking a slot").toHaveText(/\S/);
});

// Tier 3 slice 2 (docs/roadmap/tier3-harness-design.md Area 1) -- the actual
// gap the two tests above leave open: neither ever uploads a referral or
// reaches a real submitted request. This one drives the order-first branch
// all the way through: upload → TURNOS_FAKE_AI's canned reading resolves →
// pick a slot → Confirmá → submit → a real booking_requests row.
//
// Pre-authenticated via a session cookie (same rationale as
// cancel-waitlist.e2e.mjs: the login/register screen stepCuenta() would
// otherwise show is the native Account module, exercised in
// account-delayed.spec.mjs -- this test's job
// is what the wizard does with an order and an AI reading, not the login
// form). The account was seeded with a phone AND email already set
// (fixture.py's --account), so Confirmá's "¿Cómo te avisamos?" section
// shows them read-only instead of asking for one -- #wz-send needs no
// typing before it can be clicked.
test("uploading a referral resolves the AI reading and lands a real request", async ({ app, newContext }) => {
  const { account_dni, account_session } = app.fixture;
  expect(account_dni && account_session, "fixture did not return a --account patient session").toBeTruthy();

  const context = await newContext();
  await context.addCookies([patientCookie(app.baseUrl, account_session)]);
  const page = await context.newPage();
  await page.goto(`/service/${SERVICE_ID}/book`, { waitUntil: "load", timeout: 15000 });
  const title = page.locator("#svc-title");
  await expect(title).toBeVisible({ timeout: 8000 });
  await expect(title).toHaveText("Subí tu orden");

  // #wz-file is a real (hidden) <input type=file> -- wz-upload.js's own
  // markup, wired to analyze() on "change". Playwright's setInputFiles
  // doesn't need it visible, same as a native file picker doesn't care
  // that the styled dropzone sits on top of it.
  const referralImage = path.join(
    ROOT, "packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg",
  );
  await page.locator("#wz-file").setInputFiles(referralImage);

  // analyze() replaces the step with a "Estamos leyendo tu orden"
  // processing screen while POST /api/analyze-order resolves
  // (TURNOS_FAKE_AI=1's ai_referral._fake_result_3pass -- this filename
  // contains neither "factura" nor "extranjer", so it falls through to
  // the default `parece_correcta` stub). A verdict that parses as a real
  // order goes STRAIGHT to the next step (wizard-design rule 4: no
  // intermediate "here's what we read" screen on the happy path) --
  // reaching real slot buttons IS the proof the reading resolved and
  // didn't stop at stepDocDudoso.
  const slotButtons = page.locator(".wz-seg-opt");
  await expect(slotButtons.first()).toBeVisible({ timeout: 15000 });
  await slotButtons.first().click();

  // Already-authenticated session -> stepCuenta() skips straight past
  // the login/register screen into Confirmá.
  const sendBtn = page.locator("#wz-send");
  await expect(sendBtn).toBeVisible({ timeout: 10000 });
  await sendBtn.click();

  // Not auto-confirmed (solicitud_con_orden has no auto_confirmar) --
  // renderSuccess() sets #svc-title to "Solicitud enviada" in place, no
  // redirect.
  await expect(title).toHaveText("Solicitud enviada", { timeout: 15000 });

  // The real observable side effect: a booking_requests row for this
  // patient, pending review, carrying the fake AI's verdict -- through
  // db._connect()/_wire() (fixture.py's query-request), never re-parsed
  // from the DOM.
  const req = await queryRequest(app.dbName, account_dni);
  expect(req.found, "expected a real booking_requests row after submit").toBe(true);
  expect(req.estado).toBe("pendiente");
  expect(req.servicio_id).toBe(SERVICE_ID);
  expect(req.ai_verdict).toBe("parece_correcta");
});

test("patient account opens the submitted request through canonical preparation module", async ({ app, newContext }) => {
  const context = await newContext();
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  const loadedPrep = page.waitForResponse((response) =>
    /\/static\/platform\/prep\.[0-9a-f]{8}\.mjs$/.test(new URL(response.url()).pathname));
  await page.goto("/account#turnos", { waitUntil: "load" });
  expect((await loadedPrep).ok(), "the account consumer must load the real module").toBeTruthy();
  await expect(page.locator("#ms-host").getByText(SERVICE_NAME, { exact: true }).first()).toBeVisible({ timeout: 10000 });
  const proof = await page.evaluate(() => ({
    prepUrl: window.SANA_STATIC["platform/prep.mjs"],
    classicPrep: [...document.scripts].some((script) => /\/prep\.[0-9a-f]{8}\.js$/.test(script.src)),
    obsoleteGlobal: typeof window.SanaPrep,
  }));
  expect(proof.prepUrl).toMatch(/\/static\/platform\/prep\.[0-9a-f]{8}\.mjs$/);
  expect(proof.classicPrep).toBe(false);
  expect(proof.obsoleteGlobal).toBe("undefined");
  await page.reload({ waitUntil: "load" });
  await expect(page.locator("#ms-host").getByText(SERVICE_NAME, { exact: true }).first()).toBeVisible({ timeout: 10000 });
  expect(errors).toEqual([]);
});
