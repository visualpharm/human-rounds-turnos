// tests/e2e/journey-j15-doctor-sees-own-patients.spec.mjs -- J15 ("A doctor sees
// his own patients and nobody else's; somebody else's is simply not there")
// cross-role journey acceptance.
//
// Two doctors each have a patient (the same kind of approved appointment, a
// finished pre-visit interview). A colleague role the admin made by hand holds
// "atención médica" but is linked to no doctor. The rule (docs/architecture/
// admin-cola-y-agenda.md, the product owner's vision 2026-10-23 "Someone else's — 404, not 403"):
// a doctor reads his own patients; what is not his answers exactly as if it did
// not exist -- 404, never 403, never the data.
//
// Terminal outcome: for every door a doctor can walk through to a patient (the
// roster, the agenda, the day queue, the interview, the consultation link, the
// AI chart summary by DNI, the patient's card, the referral file and the actions
// on a request by id), the other doctor's patient is absent, and the answer to a
// direct knock is the same as for an id that does not exist. A hand-made
// clinical role with no doctor sees no patient at all.
//
// Steps (the front desk's approval is covered by J11; here the appointments are seeded
// exactly as an approval leaves them, one per doctor, each with its referral file):
//   1. Doctor A's roster, agenda and day queue name A's patient and not B's;
//      a ?doctor= pointing at B changes nothing
//   2. Doctor B signs in and sees the mirror image: his patient, not A's
//   3. A's direct knocks on B's patient -- interview page and API, the referral file and the
//      cancel / attendance actions of his request -- answer 404 exactly as for an id that does
//      not exist, change nothing; A's own referral file opens (so the 404 is the guard, not a
//      missing route)
//   4. A reads the AI summary of his own patient but not of B's (404)
//   5. The admin lets A open patient cards: A's own patient's card opens, B's patient's card
//      answers like a card that does not exist, the export is the administrator's alone, and
//      the HSI history of B's patient is refused as it is for a DNI that is nobody's
//   6. The admin makes a clinical role and an account with no doctor behind it
//   7. That account sees no roster, no picker of doctors, no interview of B's
//
// Port of tests/e2e/journey-j15-doctor-sees-own-patients.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app and the three staff accounts come from the worker fixtures (`appOptions` + `journey`);
//   the nested `t.test` subtests are `test.step`s; the K10 fix (!47) is on main, so steps 3 and 5
//   are plain gates.
// - Two fixed waits are state waits: the roster is loaded until the seeded visit shows (the server's
//   3 s catalog cache), and the colleague's roster is awaited until it has said something
//   («Cargando…» replaced by the list or its empty notice) before «B's patient is not there» is read.
import path from "node:path";
import {
  test, expect, runFixture, signInStaff, gotoUntil, DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS, ROOT,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const A = { id: "levin-camila", nombre: "Camila Levin", usuario: "e2e-dr-levin", password: LOGIN_PHRASE };
const B = { id: "ruiz-sergio", nombre: "Sergio Ruiz", usuario: "e2e-dr-ruiz", password: LOGIN_PHRASE };
const COLEGA = { usuario: "e2e-colega", password: LOGIN_PHRASE };
// The pack's sample referral: the file both appointments carry, so the door to it has something to open.
const REFERRAL = path.join(ROOT, "packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg");

test.use({
  // The HSI history is an opt-in read (off by default): on, so its refusal of a foreign patient is
  // the access rule speaking and not the flag.
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j15", env: { TURNOS_HSI_EXTERNAL_HISTORY: "1" } },
  journey: {
    staff: [
      DESK,
      { usuario: A.usuario, password: A.password, rol: "medico", medico_id: A.id },
      { usuario: B.usuario, password: B.password, rol: "medico", medico_id: B.id },
    ],
  },
});

/** GET as a signed-in context; returns {status, text}. Never throws on 4xx. */
async function knock(context, path) {
  const response = await context.request.get(path, { failOnStatusCode: false });
  return { status: response.status(), text: await response.text() };
}

test("J15 — each doctor reaches only his own patients; the other's are not there", async ({ app, role }) => {
  const pa = await runFixture(app, "seed-visit", { spec: {
    dni: "30444001", nombre: "Ana", apellido: "DeCamila", doctor_id: A.id, doctor_name: A.nombre,
    servicio_id: SERVICE_ID, centro: "sampleton-hospital", when: "+1", at: "09:20", account: true } });
  const pb = await runFixture(app, "seed-visit", { spec: {
    dni: "30444002", nombre: "Beto", apellido: "DeSergio", doctor_id: B.id, doctor_name: B.nombre,
    servicio_id: SERVICE_ID, centro: "sampleton-hospital", when: "+1", at: "09:40", account: true } });
  for (const visit of [pa, pb]) {
    await runFixture(app, "attach-referral-file", { request: String(visit.request_id), file: REFERRAL });
  }
  const doctorA = await role();
  const doctorB = await role();
  const admin = await role();
  const colega = await role();
  const NAME_A = "Ana DeCamila";
  const NAME_B = "Beto DeSergio";

  const tomorrow = new Date(Date.now() + 86_400_000).toLocaleDateString("en-CA", { timeZone: "America/Argentina/Buenos_Aires" });

  await test.step("1. doctor A's roster, agenda and day queue name his patient and not B's", async () => {
    const page = doctorA.page;
    await signInStaff(page, A.usuario, A.password);

    const roster = page.locator("#sec-mis-pacientes");
    await gotoUntil(page, "/admin/my-patients", () => expect(roster.locator("table tbody tr").first()).toBeVisible({ timeout: 2000 }));
    await expect(roster, "the roster names his patient").toContainText(NAME_A, { useInnerText: true });
    await expect(roster, "…and not the other doctor's").not.toContainText(NAME_B, { useInnerText: true });

    // A URL that names the other doctor changes nothing: the screen is his, the picker does not exist.
    await page.goto(`/admin/my-patients?doctor=${B.id}`, { waitUntil: "load" });
    await expect(roster.locator("table tbody tr").first()).toBeVisible();
    await expect(roster, "?doctor=<other doctor> shows the same roster").toContainText(NAME_A, { useInnerText: true });
    await expect(roster).not.toContainText(NAME_B, { useInnerText: true });
    expect(new URL(page.url()).search, "the foreign query is dropped from the address").toBe("");
    await expect(page.locator("#dp-medico"), "a doctor is never offered a picker of doctors").toHaveCount(0);

    await page.goto("/admin/mis-turnos", { waitUntil: "load" });
    const week = page.locator("#sec-mis-turnos");
    await expect(week.locator(".mt-day table tbody tr").first()).toBeVisible();
    await expect(week, "Mis turnos lists only his appointments").toContainText(NAME_A, { useInnerText: true });
    await expect(week).not.toContainText(NAME_B, { useInnerText: true });

    // The day queue, read for tomorrow (where both appointments are): one block, his.
    const queue = await knock(doctorA.context, `/api/admin/day-queue?dia=${tomorrow}`);
    expect(queue.status).toBe(200);
    const groups = JSON.parse(queue.text).grupos;
    expect(groups.map((g) => g.medico_id), "the day queue of a doctor is his block alone").toEqual([A.id]);
    expect(queue.text.includes(NAME_B) || queue.text.includes("30444002"), "…with nothing of the other doctor's patient").toBe(false);
  });

  await test.step("2. doctor B signs in and sees his own patient, not A's", async () => {
    const page = doctorB.page;
    await signInStaff(page, B.usuario, B.password);

    const roster = page.locator("#sec-mis-pacientes");
    await gotoUntil(page, "/admin/my-patients", () => expect(roster.locator("table tbody tr").first()).toBeVisible({ timeout: 2000 }));
    await expect(roster, "B's roster names his patient").toContainText(NAME_B, { useInnerText: true });
    await expect(roster, "…and not A's").not.toContainText(NAME_A, { useInnerText: true });

    await page.goto("/admin/mis-turnos", { waitUntil: "load" });
    const week = page.locator("#sec-mis-turnos");
    await expect(week.locator(".mt-day table tbody tr").first()).toBeVisible();
    await expect(week, "B's Mis turnos lists only his appointments").toContainText(NAME_B, { useInnerText: true });
    await expect(week).not.toContainText(NAME_A, { useInnerText: true });

    const queue = await knock(doctorB.context, `/api/admin/day-queue?dia=${tomorrow}`);
    expect(queue.status).toBe(200);
    expect(JSON.parse(queue.text).grupos.map((g) => g.medico_id), "B's day queue is his block alone").toEqual([B.id]);
    expect(queue.text.includes(NAME_A) || queue.text.includes("30444001"), "…with nothing of A's patient").toBe(false);
  });

  await test.step("3. direct knocks on B's patient answer 404 and carry no data", async () => {
    const api = await knock(doctorA.context, `/api/admin/intake/consult/${pb.interview_id}`);
    const nobody = await knock(doctorA.context, "/api/admin/intake/consult/99999");
    expect(api.status, "B's interview by id is 404, not 403").toBe(404);
    expect(api.text, "…the very answer for an interview that does not exist").toBe(nobody.text);
    expect(api.text.includes("DeSergio") || api.text.includes("30444002")).toBe(false);

    // The doors to a request by id are the referral file and the actions on it (there is no
    // GET of the request itself). Each answers a foreign id exactly as an id that is nobody's.
    const knockPost = async (url, data) => {
      const response = await doctorA.context.request.post(url, { data, failOnStatusCode: false });
      return { status: response.status(), text: await response.text() };
    };
    const ownFile = await doctorA.context.request.get(`/api/admin/order/${pa.request_id}`, { failOnStatusCode: false });
    expect(ownFile.status(), "his own referral file opens (so a 404 below is the guard, not a missing route)").toBe(200);
    expect(ownFile.headers()["content-type"] || "").toMatch(/^image\//);
    const nobodysFile = await knock(doctorA.context, "/api/admin/order/99999");
    const foreignFile = await knock(doctorA.context, `/api/admin/order/${pb.request_id}`);
    expect(nobodysFile.status, "an id that is nobody's has no file").toBe(404);
    expect(foreignFile.status, "B's referral file is 404, not 403").toBe(404);
    expect(foreignFile.text, "…the very answer for a request that does not exist").toBe(nobodysFile.text);

    const nobodysCancel = await knockPost("/api/admin/requests/99999/cancel", {});
    const foreignCancel = await knockPost(`/api/admin/requests/${pb.request_id}/cancel`, {});
    expect(nobodysCancel.status).toBe(404);
    expect(foreignCancel.status, "A cannot cancel B's appointment").toBe(404);
    expect(foreignCancel.text, "…and is told exactly what a missing request is told").toBe(nobodysCancel.text);

    const nobodysMark = await knockPost("/api/admin/requests/99999/attendance", { asistencia: "presente" });
    const foreignMark = await knockPost(`/api/admin/requests/${pb.request_id}/attendance`, { asistencia: "presente" });
    expect(nobodysMark.status).toBe(404);
    expect(foreignMark.status, "A cannot mark B's patient present").toBe(404);
    expect(foreignMark.text).toBe(nobodysMark.text);

    const kept = (await runFixture(app, "request", { id: String(pb.request_id) })).request;
    expect(kept.estado, "B's appointment is untouched by the knocks").toBe("aprobada");
    expect(kept.asistencia, "…and B's patient is not marked").toBeFalsy();

    const page = doctorA.page;
    await page.goto(`/admin/consult?interview=${pb.interview_id}`, { waitUntil: "load" });
    await expect(page.locator("#sec-consulta")).toContainText("No se encontró esa entrevista.", { useInnerText: true });
    await expect(page.locator("body"), "the consultation screen of B's interview shows nothing of B's patient")
      .not.toContainText(NAME_B, { useInnerText: true });

    const own = await knock(doctorA.context, `/api/admin/intake/consult/${pa.interview_id}`);
    expect(own.status, "his own interview opens").toBe(200);
  });

  await test.step("4. A reads the AI summary of his own patient by DNI, and B's answers 404", async () => {
    const own = await knock(doctorA.context, "/api/admin/patient-summary?dni=30444001");
    expect(own.status, "his own patient's summary opens").toBe(200);
    const foreign = await knock(doctorA.context, "/api/admin/patient-summary?dni=30444002");
    expect(foreign.status, "another doctor's patient has no summary for him").toBe(404);
    expect(foreign.text.includes("Entrevista previa"), "…and no content of one").toBe(false);
  });

  await test.step("5. patient cards: A's own opens, B's answers like a card that does not exist", async () => {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    // The «Médico» preset does not open patient cards by itself; the admin grants A that one permission.
    const granted = await admin.context.request.put(`/api/admin/users/${A.usuario}`, { data: { permisos_agregados: ["pacientes"] } });
    expect(granted.status(), await granted.text()).toBe(200);

    const own = await knock(doctorA.context, "/api/admin/patients/30444001");
    expect(own.status, "A opens the card of his own patient").toBe(200);
    expect(own.text, "…and it is that patient's").toContain("DeCamila");
    const nobody = await knock(doctorA.context, "/api/admin/patients/30999999");
    const foreign = await knock(doctorA.context, "/api/admin/patients/30444002");
    expect(nobody.status, "a card that does not exist is 404").toBe(404);
    expect(foreign.status, "B's patient's card is 404, not 403").toBe(404);
    expect(foreign.text.includes("DeSergio") || foreign.text.includes("Beto"), "…and carries nothing of him").toBe(false);

    // The export of a person's whole record is the administrator's alone (no doctor, not even for his own patient).
    for (const dni of ["30444001", "30444002"]) {
      const exported = await knock(doctorA.context, `/api/admin/patients/${dni}/data-export`);
      expect(exported.status, `a doctor never receives the export (${dni})`).toBe(403);
      expect(exported.text.includes("DeCamila") || exported.text.includes("DeSergio")).toBe(false);
    }

    // The HSI history: his own patient's read is answered (with whatever the connector has), another
    // doctor's is refused, and exactly as a DNI that belongs to nobody is.
    const ownHistory = await knock(doctorA.context, "/api/admin/patient-external-history?dni=30444001");
    expect(ownHistory.status, "A reads the HSI history of his own patient").toBe(200);
    const nobodysHistory = await knock(doctorA.context, "/api/admin/patient-external-history?dni=30999999");
    const foreignHistory = await knock(doctorA.context, "/api/admin/patient-external-history?dni=30444002");
    expect(foreignHistory.status, "the HSI history of B's patient is refused").toBe(403);
    expect(JSON.parse(foreignHistory.text).reason).toBe("forbidden");
    expect(foreignHistory.text, "…the very answer for a DNI that is nobody's").toBe(nobodysHistory.text);
  });

  await test.step("6. the admin makes a clinical role and an account with no doctor behind it", async () => {
    await admin.page.goto("/admin/roles", { waitUntil: "load" });
    await admin.page.locator("#role-new").click();
    await admin.page.fill("#role-name", "Colega clínico");
    await admin.page.fill("#role-description", "Synthetic clinical colleague with no doctor");
    await admin.page.check('input[name="role-permission"][value="atencion_medica"]');
    await admin.page.click("#role-save");
    await admin.page.waitForURL((url) => !!url.searchParams.get("role"));
    const slug = new URL(admin.page.url()).searchParams.get("role");
    const created = await admin.context.request.post("/api/admin/users", {
      data: { usuario: COLEGA.usuario, password: COLEGA.password, rol: slug } });
    expect(created.status(), await created.text()).toBe(200);
  });

  await test.step("7. that account sees no roster, no picker of doctors and no interview of B's", async () => {
    await signInStaff(colega.page, COLEGA.usuario, COLEGA.password);
    await colega.page.goto(`/admin/my-patients?doctor=${B.id}`, { waitUntil: "load" });
    // The section says «Cargando…» first: wait until it has said what it has to say.
    await expect(colega.page.locator("#sec-mis-pacientes .adm-empty, #sec-mis-pacientes table").first()).toBeVisible();
    await expect(colega.page.locator("#sec-mis-pacientes .adm-empty")).toHaveText(/^\s*(?!Cargando)\S/);
    await expect(colega.page.locator("main"), "B's patient is not on the colleague's roster").not.toContainText(NAME_B, { useInnerText: true });
    await expect(colega.page.locator("#dp-medico"), "a colleague with no doctor is not offered a picker of every doctor").toHaveCount(0);
    const interview = await knock(colega.context, `/api/admin/intake/consult/${pb.interview_id}`);
    expect(interview.status, "B's interview answers 404 for an account that is nobody's doctor").toBe(404);
  });

  for (const [who, label] of [[doctorA, "doctor A"], [doctorB, "doctor B"], [admin, "admin"], [colega, "colleague"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
