// A referral the patient picks while the "Subí tu orden" wizard is still
// booting must not vanish. The server paints the upload zone first; the wizard
// then replaces it with its own. A native file chooser opened on the painted
// input answers to THAT input: when the wizard swapped it out, the chosen file
// landed on a detached node, no `change` reached the page, and nothing happened
// -- the patient believed the referral had gone. Deterministic: the wizard's
// first dependency (format.mjs) is held, the chooser is opened on the painted
// zone, the wizard is released and has re-rendered, and only then does the
// patient pick the file.
//
// Port of tests/e2e/booking-upload-chooser.e2e.mjs (3 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), same dbName,
//   `account: true` and upload-cooldown env); each test opens its page in a `newContext()` of the
//   runner (closed by the runner instead of `t.after`) with the seeded patient's cookie. There is
//   no requireOk()/skip path.
// - The hand-made `deferred()` gates are `holdGate(cleanup)`: the held format module is also
//   released on cleanup, so a failing test never leaves the route hanging.
// - Waits are web-first (`expect(locator).toBeVisible`, `expect.poll` on the page's own
//   «the wizard replaced the painted zone» condition with the same 15 s limit). The in-page
//   marker (`window.__paintedZone`) and the order of events (chooser opened -> module released ->
//   wizard re-rendered -> file chosen) are exactly the original's.
// - Locators stay CSS: `#wz-upzone`, `#wz-file` and `.wz-seg-opt` are the upload wizard's markup contract.
import path from "node:path";
import { test, expect, patientCookie, holdGate, ROOT } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_booking_upload_chooser",
    account: true,
    // Each test uploads once from the same loopback address: lift the per-IP
    // upload cooldown (ai/ai_cooldown.py, ai/abuse_guard.py) so they don't throttle each other.
    env: { TURNOS_IMG_BURST: "100", TURNOS_AI_PER_HOUR: "100", TURNOS_AI_PER_DAY: "100" },
  },
});

const SERVICE_ID = "electrocardiograma-hospital";
const REFERRAL = path.join(ROOT, "packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg");

// Opens the booking wizard with its format module held: the server-painted
// upload zone is on screen, the wizard has not rendered yet.
async function openHeldWizard({ newContext, app, cleanup }) {
  const context = await newContext();
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const page = await context.newPage();
  const requested = holdGate(cleanup);
  const release = holdGate(cleanup);
  await page.route("**/static/platform/format.*.mjs", async (route) => {
    requested.arrive();
    await release.held;
    await route.continue();
  });
  await page.goto(`/service/${SERVICE_ID}/book`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await expect(page.locator("#wz-upzone")).toBeVisible({ timeout: 8000 });
  await requested.arrived;
  await page.evaluate(() => { window.__paintedZone = document.getElementById("wz-upzone"); });
  return { page, release: release.release };
}

async function waitWizardRendered(page) {
  await expect.poll(
    () => page.evaluate(() => !!document.getElementById("wz-upzone") && document.getElementById("wz-upzone") !== window.__paintedZone),
    { timeout: 15000 },
  ).toBe(true);
}

async function waitReadingResolved(page) {
  // The reading replaces the step and goes straight to the slots on the happy
  // path: reaching real slot buttons proves the file was analysed.
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
}

test("a file chosen in a chooser opened before the wizard rendered is still analysed", async ({ newContext, app, cleanup }) => {
  const { page, release } = await openHeldWizard({ newContext, app, cleanup });
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 8000 }),
    page.locator("#wz-upzone").click(),
  ]);
  release();
  await waitWizardRendered(page);
  await chooser.setFiles(REFERRAL);
  await waitReadingResolved(page);
});

test("a file chosen on the painted input before the wizard rendered is still analysed", async ({ newContext, app, cleanup }) => {
  const { page, release } = await openHeldWizard({ newContext, app, cleanup });
  await page.locator("#wz-file").setInputFiles(REFERRAL);
  release();
  await waitReadingResolved(page);
});

test("after the wizard rendered, the same file can be chosen again", async ({ newContext, app, cleanup }) => {
  const { page, release } = await openHeldWizard({ newContext, app, cleanup });
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 8000 }),
    page.locator("#wz-upzone").click(),
  ]);
  release();
  await waitWizardRendered(page);
  // Cancel the early chooser (no file), then use the wizard's own control.
  await chooser.setFiles([]);
  const [second] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 8000 }),
    page.locator("#wz-upzone").click(),
  ]);
  await second.setFiles(REFERRAL);
  await waitReadingResolved(page);
});
