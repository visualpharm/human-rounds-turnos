// TASK-68 step c, end to end: a person signs up on a document number somebody already holds, gets a
// pending claim (not the old 409), the front desk confirms it in Pacientes, and the claimant — who
// logs in with that number and their own password — sees the appointments under it.
//
// The harness forces TURNOS_DNI_CLAIMS=0 for every other spec; this one turns it on for its own app
// (`env` is spread last in setupHarness). Data: the waitlist fixture's patient Ana (DNI 30111100) holds
// an approved appointment; the claimant is a fictional person who signs up on that same number.
import { test, expect, loginAdmin, saveShot, artifactPath } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_dni_claims", waitlist: true, env: { TURNOS_DNI_CLAIMS: "1" } } });

const evidence = artifactPath("human-rounds", "dni-claims-e2e");
const CLAIMANT_PASSWORD = "claimant-e2e-1";

test("a sign-up on a taken DNI becomes a claim the desk confirms, then the claimant sees the appointments", async ({
  page, app, newContext,
}, testInfo) => {
  const dni = app.fixture.waitlist_dni;

  // 1) The claimant signs up on the taken number: a pending claim, never the old 409.
  const claimantCtx = await newContext();
  const signup = await claimantCtx.request.post(app.baseUrl + "/api/account/register", {
    headers: { Origin: app.baseUrl },
    data: {
      dni, nombre: "Claudia", apellido: "Reclama", nacimiento: "1990-05-05",
      email: "e2e-claimant@example.invalid", password: CLAIMANT_PASSWORD, contacto: "",
    },
  });
  expect(signup.status(), await signup.text()).toBe(200);
  const user = (await signup.json()).usuario;
  expect(user.dni_verificacion).toBe("pending");
  expect(user.dni, "the claimant lives under a minted key, never the number").not.toBe(dni);

  // 2) While the claim is open she sees none of Ana's appointments.
  const claimantPage = await claimantCtx.newPage();
  await claimantPage.goto(app.baseUrl + "/account#turnos", { waitUntil: "load" });
  await expect(claimantPage.locator(".rail-nav")).toBeAttached();
  await claimantPage.waitForLoadState("networkidle"); // the list is fetched after the shell renders
  expect(await claimantPage.locator("[data-cancel]").count()).toBe(0);

  // 3) The desk sees the claim in Pacientes and confirms it.
  await loginAdmin(page, app.baseUrl, undefined, undefined, "/admin/pacientes");
  const claims = page.locator("#pat-claims");
  await expect(claims).toBeVisible();
  await expect(claims).toContainText("Claudia");
  await expect(claims).toContainText(dni);
  await saveShot(testInfo, page, evidence + "/claim-desk.png", { animations: "disabled" });
  page.once("dialog", (dialog) => dialog.accept());
  const resolved = page.waitForResponse((r) => /\/api\/admin\/dni-claims\/\d+\/resolve$/.test(new URL(r.url()).pathname)
    && r.request().method() === "POST");
  await claims.locator('[data-claim-resolve="1"]').click();
  expect((await resolved).status()).toBe(200);
  await expect(page.locator("#pat-claims")).toHaveCount(0);

  // 4) She logs in with the number and her own password and sees the appointment under it.
  const patientCtx = await newContext();
  const login = await patientCtx.request.post(app.baseUrl + "/api/account/login", {
    headers: { Origin: app.baseUrl },
    data: { dni, password: CLAIMANT_PASSWORD },
  });
  expect(login.status(), await login.text()).toBe(200);
  const patientPage = await patientCtx.newPage();
  await patientPage.goto(app.baseUrl + "/account#turnos", { waitUntil: "load" });
  await expect(patientPage.locator("[data-cancel]").first()).toBeVisible();
  await saveShot(testInfo, patientPage, evidence + "/claimant-turnos.png", { animations: "disabled" });
});
