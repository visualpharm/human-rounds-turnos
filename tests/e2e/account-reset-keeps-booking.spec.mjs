// tests/e2e/account-reset-keeps-booking.spec.mjs -- the password-recovery link
// returns the patient to the booking she had started (the product owner, 2026-10-25,
// m908541: «I recovered the password … it dropped me from the previous booking route
// and just sent me to Mis turnos»).
//
// A patient with an account but no session picks a time in the booking
// wizard, reaches «Tu cuenta», switches to «Ingresar», asks for the recovery
// link and opens the link from the email. After the new password she is back
// on the SAME wizard URL (same service, same ?slot=), already signed in:
//   - same browser (the hold is hers) -> straight to Confirmá with that time;
//   - another browser (the phone that reads the email) -> the same service,
//     signed in; the time travels in the link, so the wizard either keeps it or
//     says it is no longer free -- never Mis turnos.
//
// External services: mail is the local receiver (lib/journey.mjs,
// `journey: { sink: true }`); the link is read from what the app "sent".
import { test, expect, seedPatient } from "./lib/journey.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_account_reset_keeps_booking" },
  journey: { sink: true },
});
test.describe.configure({ mode: "serial" });

const SERVICE_ID = "cardiologia-sampleton-hospital";
const NEW_PASSWORD = "nueva-clave-e23";

async function startBookingAndAskForLink(page, sink, patient) {
  await page.goto(`/service/${SERVICE_ID}/book?slots=1`, { waitUntil: "load" });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#wz-auth #sc-form")).toBeVisible();
  const bookingUrl = new URL(page.url());
  expect(bookingUrl.searchParams.get("slot"), "the chosen time is in the wizard URL").toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);

  await page.locator("#sc-swap").click();
  await page.locator("#sc-forgot").click();
  await page.locator("#sc-dni").fill(patient.dni);
  const before = sink.mails.length;
  await page.locator("#sc-submit").click();
  await expect(page.locator("#sc-msg")).toContainText("te llegó un link", { useInnerText: true });

  let mail = null;
  await expect.poll(() => {
    mail = sink.mails.slice(before).find((m) => m.to === patient.email && /Recuperar/.test(m.subject));
    return !!mail;
  }, { message: "the recovery email" }).toBe(true);
  const link = new URL((mail.text.match(/https?:\/\/\S+\/account\/reset\?\S+/) || [""])[0]);
  expect(link.pathname).toBe("/account/reset");
  return { bookingUrl, link };
}

async function chooseNewPassword(page, link) {
  await page.goto(link.pathname + link.search, { waitUntil: "load" });
  await page.locator("#sc-pass").fill(NEW_PASSWORD);
  await page.locator("#sc-submit").click();
}

test("same browser: after the new password the patient is back on Confirmá with the time she chose", async ({ app, sink, role }) => {
  const patient = {
    dni: "30222101", nombre: "Elena", apellido: "Olvido", nacimiento: "1980-05-04", sexo: "F",
    contacto: "+5492254200101", email: "elena.olvido@example.invalid",
  };
  await seedPatient(app, patient); // account exists; this browser has no session
  const { page, context, errors } = await role();
  const { bookingUrl, link } = await startBookingAndAskForLink(page, sink, patient);

  const mailPage = await context.newPage(); // the email opened in another tab
  await chooseNewPassword(mailPage, link);
  await mailPage.waitForURL((url) => url.pathname === bookingUrl.pathname);
  const back = new URL(mailPage.url());
  expect(back.searchParams.get("slot"), "the same time").toBe(bookingUrl.searchParams.get("slot"));
  await expect(mailPage.locator("#wz-send"), "Confirmá, not Mis turnos").toBeVisible();
  await expect(mailPage.locator("#svc-form-host")).toContainText("Elena Olvido", { useInnerText: true });
  expect(errors).toEqual([]);
});

test("another browser: the link alone brings the patient back to the same service, signed in", async ({ app, sink, role }) => {
  const patient = {
    dni: "30222102", nombre: "Tomás", apellido: "Telefono", nacimiento: "1975-01-20", sexo: "M",
    contacto: "+5492254200102", email: "tomas.telefono@example.invalid",
  };
  await seedPatient(app, patient);
  const desktop = await role();
  const { bookingUrl, link } = await startBookingAndAskForLink(desktop.page, sink, patient);

  const phone = await role({ viewport: { width: 390, height: 844 } });
  await chooseNewPassword(phone.page, link);
  await phone.page.waitForURL((url) => url.pathname === bookingUrl.pathname);
  expect(new URL(phone.page.url()).searchParams.get("slot")).toBe(bookingUrl.searchParams.get("slot"));
  // The first browser still holds that time (15 min), so for the phone it is
  // taken: the wizard names the lost time on the same service's times, and the
  // patient is signed in -- picking another time goes straight to Confirmá.
  await expect(phone.page.locator("#svc-form-host")).toContainText("ese turno ya no está libre", { useInnerText: true });
  await phone.page.locator(".wz-seg-opt").first().click();
  await expect(phone.page.locator("#wz-send")).toBeVisible();
  await expect(phone.page.locator("#svc-form-host")).toContainText("Tomás Telefono", { useInnerText: true });
  await expect(phone.page.locator("#wz-auth #sc-form")).toHaveCount(0);
});

test("a recovery link never sends the patient to another site", async ({ request, sink, role }) => {
  // The return point is accepted only as a path of this site: a foreign one
  // is dropped from the link, and the page itself refuses one typed into it.
  for (const next of ["https://evil.example/x", "//evil.example/x", "/\\evil.example"]) {
    const before = sink.mails.length;
    const r = await request.post("/api/account/recover", { data: { dni: "30222101", next } });
    expect(r.status()).toBe(200);
    await expect.poll(() => sink.mails.length).toBe(before + 1);
    const link = new URL(sink.mails[before].text.match(/https?:\/\/\S+\/account\/reset\?\S+/)[0]);
    expect(link.searchParams.get("next"), `dropped: ${next}`).toBeNull();
  }
  const before = sink.mails.length;
  await request.post("/api/account/recover", { data: { dni: "30222101" } });
  await expect.poll(() => sink.mails.length).toBe(before + 1);
  const link = new URL(sink.mails[before].text.match(/https?:\/\/\S+\/account\/reset\?\S+/)[0]);
  const { page } = await role();
  await chooseNewPassword(page, { pathname: link.pathname, search: link.search + "&next=" + encodeURIComponent("//evil.example/x") });
  await page.waitForURL((url) => url.pathname === "/account");
  expect(new URL(page.url()).hostname, "still this site").not.toBe("evil.example");
});
