// tests/e2e/cancel-waitlist.spec.mjs -- Tier 3 slice 1
// (docs/roadmap/tier3-harness-design.md Area 1): a patient cancelling an
// approved appointment must free the slot for the waitlist -- the one
// booking flow with NO test at all before this file (booking.spec.mjs only
// ever creates requests, never cancels one).
//
// Fixture (tests/e2e/fixture.py --waitlist) seeds through the app's own
// functions, never hand-built rows: two approved appointments for the
// SAME service+center via db_booking.create_booking_request + db_booking.approve --
// patient A ("waitlist_dni"/"waitlist_session") nearer, patient B later
// and already opted into waitlist.opt_in() for something sooner. Patient A
// is pre-authenticated via a session cookie (accounts.py's `st_ses`,
// db_accounts.create_session -- the exact function a real login/register also
// calls) rather than driving the login form: the login form itself is
// covered by admin.spec.mjs's TOTP/wrong-password tests and my-requests.js's
// own account gate is the same native Account module the wizard uses elsewhere --
// what THIS test exercises is what happens after identity is established
// (the cancel click and the waitlist promotion it triggers), not the login
// screen again.
//
// Port of tests/e2e/cancel-waitlist.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName and waitlist option); there is no requireOk()/skip path. The patient's
//   session cookie is added to the runner's own (per-test) context instead of a hand-made
//   context closed in try/finally.
// - Waits are web-first (`toBeVisible` for the cancel button 10 s, the in-place confirmation
//   5 s and the «Cancelada por vos» status 10 s). The two database reads (no offer before the
//   cancel, a live `ofrecida` offer after it) are the point-in-time side-effect checks they
//   were, and the second one still runs after the page work, from the test body.
// - Locators: `[data-cancel]` and `[data-si]` stay CSS - the data attributes are my-requests.js's
//   own action hooks; the status text already used getByText.
import { test, expect, queryOffers, patientCookie } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_cancel_waitlist", waitlist: true } });

test("cancelling an approved appointment offers the freed slot to the waitlist", async ({ page, context, app }) => {
  const { waitlist_dni, waitlist_session } = app.fixture;
  expect(waitlist_dni && waitlist_session, "fixture did not return a --waitlist patient session").toBeTruthy();

  // No offer exists yet -- the fixture only seeded the two appointments and
  // the waitlist opt-in, never an appointment_offers row. Asserting this
  // BEFORE the cancel is what makes the after-cancel assertion mean
  // something (otherwise a fixture that pre-seeded an offer would pass
  // trivially).
  expect(await queryOffers(app.dbName), "no offer should exist before the cancel").toEqual([]);

  await context.addCookies([patientCookie(app.baseUrl, waitlist_session)]);
  await page.goto("/account", { waitUntil: "load", timeout: 15_000 });

  // The dashboard renders straight into "Mis turnos" (accounts.py's
  // buildDash: defTab="turnos" whenever the account has an appointment) --
  // my-requests.js mounts lazily into #ms-host the first time that tab is
  // shown, which happens immediately here.
  // my-requests.js now paints #ms-host exactly once, after
  // loadServicios()/loadCentros() both resolve (my-requests.js:1184-1197)
  // -- no more render-then-replace, so there's no window where a click
  // lands on a card that's about to be wiped out from under it.
  const cancelBtn = page.locator("[data-cancel]").first();
  await expect(cancelBtn).toBeVisible({ timeout: 10_000 });
  await cancelBtn.click();

  // my-requests.js's own confirm-in-place UI (no browser dialog): "¿Cancelar
  // el turno?" with two buttons, «Sí, cancelar el turno» and «No».
  const confirmYes = page.locator("[data-si]").first();
  await expect(confirmYes).toBeVisible({ timeout: 5_000 });
  await confirmYes.click();

  // After a successful POST /api/requests/<id>/cancel, my-requests.js
  // reloads the list -- the request moves into "Anteriores", dimmed, with
  // this exact status text (account.mine.requests.status_cancelled_by_patient).
  await expect(page.getByText("Cancelada por vos").first()).toBeVisible({ timeout: 10_000 });

  // The real observable side effect (docs/roadmap/tier3-harness-design.md's
  // own phrasing): booking_requests.py's _cancel() called
  // waitlist.release_slot(), which -- because patient B's own appointment is
  // LATER than the slot patient A just freed -- created a live
  // appointment_offers row for patient B, status 'ofrecida'.
  const offers = await queryOffers(app.dbName);
  expect(
    offers.some(([estado, cantidad]) => estado === "ofrecida" && cantidad >= 1),
    `expected a live ('ofrecida') appointment offer after the cancel, got ${JSON.stringify(offers)}`,
  ).toBe(true);
});
