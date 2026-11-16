// tests/e2e/intake.spec.mjs -- Tier-2 pre-move gate: the two entrevista
// previa entry points (docs/architecture/entrevista-previa.md), against a
// real running server and a real headless browser. Runs against the
// public synthetic pack (packs/example-town, GATE_PACK_CHAIN in
// lib/harness.mjs) -- never the private Pinamar pack (item 4/5 of Codex's
// review).
//
// Fixtures (tests/e2e/fixture.py --intake) seed straight through the app's
// own functions, not hand-built rows:
//   - db_clinical.interview_create(entry_mode="post_booking", request_id=<a real booking request>)
//     -- the post-booking entry point, authorized here by its own opaque
//     patient_token via `?t=` (the same trust model as an emailed CTA link,
//     added 2026-08-03 -- no booking/account/session needed to test the
//     PAGE itself).
//   - intake_admin.get_or_create_door_token("gonzalez-juan") --
//     packs/example-town/medicos.json's Juan González (CAPS Ashgrove
//     norte). The inherited default is OFF. The synthetic harness must
//     explicitly opt in its own fixture doctor; production/demo catalogs
//     are not rewritten to manufacture professional consent.
//
// Differences from the node:test original (tests/e2e/intake.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), the same
//     setupHarness options: intake + doorMedico) and each test takes the runner's `page`
//     instead of opening and closing one by hand; a missing prerequisite fails the run
//     (no requireOk()/skip path, the `_gate` fixture does it).
//   - assert.match(await body.innerText(), re) is expect(body).toContainText(re, { useInnerText: true }),
//     which retries until the chat text is there (the original waited for the topbar first, which is kept).
//   - The explicit waitFor/goto timeouts are kept as { timeout } on expect/goto.
//   - Locators: the CSS ids/classes (.iv-topbar-title, a.iv-primary-btn, #iv-id-dni, #iv-id-btn) stay,
//     they are the entrevista page's own markup contract.
import { test, expect } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_intake",
    intake: true,
    doorMedico: "gonzalez-juan",
  },
});

test("entrevista previa entry page loads and opens the chat via its link token", async ({ page, app }) => {
  const { interview_request_id, interview_token } = app.fixture;
  expect(interview_request_id && interview_token, "fixture did not return an interview token").toBeTruthy();

  await page.goto(
    `${app.baseUrl}/entrevista/${interview_request_id}?t=${interview_token}`,
    { waitUntil: "load", timeout: 15_000 },
  );

  // entrevista.js renders the topbar + seeds the opening question only
  // after GET /api/intake/t/<token> resolves -- wait for that, not just
  // the static page shell ("Cargando…" until then).
  await expect(page.locator(".iv-topbar-title")).toBeVisible({ timeout: 10_000 });
  const body = page.locator("body");
  // The patient-facing name (2026-08-03 rename) + the one first message
  // that discloses the AI, carries the mandatory never-medical-advice
  // scope and asks the current reason -- real chat content under
  // TURNOS_FAKE_AI, not a shell.
  await expect(body).toContainText(/Contale al médico cómo te sentís/, { useInnerText: true });
  await expect(body).toContainText(/Soy el asistente de inteligencia artificial/, { useInnerText: true });
  await expect(body).toContainText(/no la reemplaza ni da consejos médicos/i, { useInnerText: true });
  await expect(body).toContainText(/¿Qué te trae a esta consulta\?/, { useInnerText: true });
});

test("QR door entry route responds: DNI identification opens the chat", async ({ page, app }) => {
  const { door_token } = app.fixture;
  expect(door_token, "fixture did not return a door token").toBeTruthy();

  await page.goto(`${app.baseUrl}/e/${door_token}`, { waitUntil: "load", timeout: 15_000 });

  // Login-first, DNI-only backup (2026-08-01 redesign): both affordances
  // present before any identity is given.
  const primaryLogin = page.locator("a.iv-primary-btn");
  await expect(primaryLogin).toBeVisible({ timeout: 8_000 });
  await expect(primaryLogin).toHaveText(/Ingresá a tu cuenta/);

  const dniInput = page.locator("#iv-id-dni");
  await expect(dniInput).toBeVisible({ timeout: 5_000 });

  await dniInput.fill("30111222");
  await page.click("#iv-id-btn");

  // No autocomplete on the match result (the response never reveals
  // whether the DNI matched an appointment) -- either way the chat opens.
  await expect(page.locator(".iv-topbar-title")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".iv-topbar-title")).toHaveText(/Contale al médico/);
});

test("an unknown door token degrades to a friendly page, never a raw error", async ({ page, app }) => {
  const resp = await page.goto(`${app.baseUrl}/e/this-token-does-not-exist`, {
    waitUntil: "load",
    timeout: 15_000,
  });
  expect(resp.status()).toBe(404);
  await expect(page.locator("body")).toContainText(/No encontramos ese cartel/, { useInnerText: true });
});
