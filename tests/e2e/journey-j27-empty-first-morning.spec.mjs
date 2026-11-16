// tests/e2e/journey-j27-empty-first-morning.spec.mjs -- J27 ("The first morning with no data:
// every role, every screen, empty states and error pages") cross-role journey acceptance.
//
// On the first morning of a launch the installation has its staff accounts and its catalog and
// NOTHING else: no requests, no patients, no appointments, no consultations, no report data.
// Most e2e screens are exercised on populated or stubbed data; an empty table, an empty report
// or an empty CSV export is exactly where a divide-by-zero, a missing key or a template that
// assumes a first row would turn into a blank pane, a literal «undefined»/«NaN» or a 500.
//
// Product rules this pins (docs/architecture/admin-cola-y-agenda.md, reportes.md, AGENTS.md
// "The patient gets the SOLUTION, never the error", "Walk-in NEVER offers appointments"):
//   - every screen of a role's menu opens (200) with no page error, no 5xx from the server and no
//     literal «undefined» / «NaN» / «null» / «[object Object]» / «Traceback» on screen, and the
//     empty screens SAY what is (not) there, in the product's own words;
//   - the admin's reports answer an empty database with a valid empty shape: JSON that parses (so no
//     bare NaN) and CSV exports that are exactly their header row, with the right content type;
//   - the patient site has no dead end on an empty account (a way to book, a way to add a child), a
//     service with no appointments to offer (walk-in) says so instead of showing an empty wizard,
//     and an unknown address gets the product's friendly 404 page, never raw JSON or a trace;
//   - a signed-out knock on a staff screen leads to the login and keeps where it was going.
//
// Steps:
//   1. Front desk, scheduling manager, doctor and admin each sign in and open every screen of their
//      own menu (the menu is read from the panel); the empty ones say so
//   2. The admin's report views and every report/export endpoint answer on an empty database
//   3. The patient site: public pages, a service with no appointments, an account with nothing in it,
//      the 404 pages, and the staff login that remembers `next`
//
// Time-independence: no clock-dependent text is asserted (the 2-step-verification banner counts
// down to a date, the day queue differs between a weekday and a weekend, the statistics month moves).
import {
  test, expect, signInStaff, seedPatient, gotoUntil, DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS,
} from "./lib/journey.mjs";
import { holdsFor } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

const GESTOR = { usuario: "e2e-gestor", password: LOGIN_PHRASE, rol: "gestor_agendas" };
const MEDICO = { usuario: "e2e-dr-levin", password: LOGIN_PHRASE, rol: "medico", medico_id: "levin-camila" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_j27" },
  journey: { staff: [DESK, GESTOR, MEDICO] },
});

// What must never be painted for a person to read.
const BAD_TEXT = /\bundefined\b|\bNaN\b|\bnull\b|\[object Object\]|Traceback/;

/** Collects the 5xx answers of the app's own origin that a page receives. */
function watchServerErrors(page, origin) {
  const failed = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === origin && response.status() >= 500) {
      failed.push(`${response.status()} ${response.request().method()} ${url.pathname}`);
    }
  });
  return failed;
}

/** The paths of the panel's own menu, as the signed-in role sees them. */
async function menuOf(page) {
  await expect(page.locator("a[data-nav]").first()).toBeAttached();
  return page.locator("a[data-nav]").evaluateAll((links) =>
    [...new Set(links.map((a) => new URL(a.href).pathname).filter((p) => p.startsWith("/admin")))]);
}

/**
 * Opens one panel screen and holds the «nothing broken on the empty installation» invariants:
 * 200, the screen's heading painted, then for a while no forbidden literal on screen.
 */
async function openScreen(page, path, who, expected, absent) {
  const response = await page.goto(path, { waitUntil: "load" });
  expect(response.status(), `${who}: ${path} answers 200`).toBe(200);
  expect(new URL(page.url()).pathname, `${who}: ${path} stays where it is`).toBe(path);
  await expect(page.locator("#adm-h1"), `${who}: ${path} paints its heading`).not.toBeEmpty();
  for (const phrase of expected || []) {
    await expect(page.locator("body"), `${who}: ${path} says «${phrase}»`).toContainText(phrase);
  }
  await holdsFor(async () => {
    const text = await page.locator("body").innerText();
    expect(text.match(BAD_TEXT)?.[0] ?? null, `${who}: ${path} shows no placeholder/trace literal`).toBeNull();
    if (absent) expect(text, `${who}: ${path} does not show «${absent}» with nothing behind it`).not.toContain(absent);
  }, { during: 400 });
  // Leave a screen only when its modules have been imported: a cancelled dynamic import is a page error that
  // says nothing about the product.
  await page.waitForLoadState("networkidle");
}

const EMPTY_QUEUE = [/Hoy: 0 turnos\./, /No hay turnos aprobados para hoy\.|No se encontraron turnos\./];
// An empty list shows its title and what will appear there, and none of the list's data tools
// (tests/e2e/admin-progressive-disclosure.spec.mjs pins the hidden tools).
const NO_REQUESTS = ["Todavía no hay solicitudes.", "Cuando un paciente pide un turno desde el sitio, su solicitud aparece acá."];
const NO_CONTACT = ["Todavía no hay pacientes.", "Acá aparece cada persona que pide un turno o que cargás con «Nuevo paciente»."];

/** Role by role: [who, credentials, menu entries that must be there, empty-state phrases per screen]. */
const ROLES = [
  {
    who: "front desk", user: DESK,
    menu: ["/admin/solicitudes", "/admin/cola", "/admin/pacientes", "/admin/account"],
    says: { "/admin/solicitudes": NO_REQUESTS, "/admin/cola": EMPTY_QUEUE, "/admin/pacientes": NO_CONTACT },
  },
  {
    who: "scheduling manager", user: GESTOR,
    menu: ["/admin/solicitudes", "/admin/cola", "/admin/pacientes", "/admin/doctors", "/admin/account"],
    says: { "/admin/solicitudes": NO_REQUESTS, "/admin/cola": EMPTY_QUEUE, "/admin/pacientes": NO_CONTACT },
  },
  {
    who: "doctor", user: MEDICO,
    menu: ["/admin/cola", "/admin/mis-turnos", "/admin/my-patients", "/admin/my-schedules", "/admin/account"],
    says: {
      "/admin/cola": EMPTY_QUEUE,
      "/admin/mis-turnos": ["No tenés turnos en los próximos 14 días", "Cuando te den un turno, aparece acá por día y hora."],
      "/admin/my-patients": ["Todavía no hay pacientes referidos a este médico."],
    },
  },
  {
    who: "admin", user: { usuario: ADMIN_USER, password: ADMIN_PASS },
    menu: ["/admin/solicitudes", "/admin/cola", "/admin/pacientes", "/admin/doctors", "/admin/services",
      "/admin/centers", "/admin/chats", "/admin/reportes", "/admin/settings", "/admin/system", "/admin/users", "/admin/roles",
      "/admin/ai-register", "/admin/account"],
    says: {
      "/admin/solicitudes": NO_REQUESTS, "/admin/cola": EMPTY_QUEUE, "/admin/pacientes": NO_CONTACT,
      "/admin/chats": ["Todavía no hubo conversaciones."],
    },
    // «Notificaciones pendientes» is a line that exists only while something is pending (ai-register.html).
    absent: { "/admin/ai-register": "Notificaciones pendientes" },
  },
];

test("every role opens every screen of its own menu on an empty installation, and the empty ones say so", async ({ role, app }) => {
  test.setTimeout(240_000);
  for (const entry of ROLES) {
    await test.step(`${entry.who}: every screen of the menu opens, shows no placeholder and names what is empty`, async () => {
      const person = await role();
      const failed = watchServerErrors(person.page, app.baseUrl);
      await signInStaff(person.page, entry.user.usuario, entry.user.password);
      const menu = await menuOf(person.page);
      for (const path of entry.menu) {
        expect(menu, `${entry.who}'s menu offers ${path}`).toContain(path);
      }
      for (const path of menu) {
        await openScreen(person.page, path, entry.who, entry.says[path], entry.absent?.[path]);
      }
      expect(failed, `${entry.who}: no 5xx from the server`).toEqual([]);
      expect(person.errors, `${entry.who}: no page error`).toEqual([]);
      await person.context.close();
    });
  }
});

const REPORT_VIEWS = ["resumen", "gestion", "recorrido", "medicos", "pacientes", "busquedas", "centros", "recupero", "sumar",
  "estadistica", "capacidad", "ia", "login"];
const CSV_EXPORTS = {
  "requests.csv": "fecha,paciente,servicio,especialidad,centro,estado,motivo_rechazo,resuelta_por,resuelta_en,turno",
  "cost-recovery.csv": "fecha,dni,paciente,obra_social,nro_afiliado,servicio,especialidad,centro,turno,asistencia",
  "sumar.csv": "fecha,dni,paciente,servicio,especialidad,centro,turno,asistencia,trazadora",
  "statistics.csv": "especialidad,consultas,presentes,ausentes,sin_marca,horas_ofrecidas,consultas_por_hora",
};
const JSON_REPORTS = ["", "/patients", "/login-panel", "/searches", "/reviews", "/cost-recovery", "/sumar", "/statistics", "/capacity", "/funnel"];

test("the admin's report views and exports answer an empty database with a valid empty shape", async ({ role, app }) => {
  test.setTimeout(240_000);
  const admin = await role();
  const failed = watchServerErrors(admin.page, app.baseUrl);

  await test.step("a signed-out knock on a report or an export is refused, never served", async () => {
    for (const path of ["/api/admin/reports", "/api/admin/reports/requests.csv"]) {
      const response = await admin.context.request.get(path, { failOnStatusCode: false });
      expect([401, 403], `${path} refuses a visitor`).toContain(response.status());
    }
  });
  await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);

  await test.step("the summary view draws only the report that has data: the pack's Google reviews, and no empty card", async () => {
    // A report is drawn only when it has data (admin-reports.mjs hasData*; a zero is not data, product owner 2026-10-28),
    // so on an installation without patients the overview holds two cards: Centros and the login panel (the admin's own sign-in is a session); Centros's reviews come from the
    // data pack (packs/example-town/centros-resenas.json: 157 reviews of 5 centers), not from any patient. The
    // others (requests, doctors, patients, searches, cost recovery, SUMAR+, statistics, capacity, AI cost)
    // say nothing here: no «s/d» card and none of their old «nothing yet» sentences.
    // The page asks the server for twelve reports at once and a card whose request fails (the two heaviest,
    // statistics and capacity, answer 503 when a loaded database is slow) is left out like one with no data: a
    // whole load is judged, and a load that hit a busy server is repeated (gotoUntil) rather than failing the
    // journey. The Centros card is read from a file, so it never depends on the database being quick.
    await gotoUntil(admin.page, "/admin/reportes?view=resumen", async () => {
      const body = admin.page.locator("#rep-body");
      const cards = body.locator(".rep-sumcard");
      await expect(cards.first()).toBeVisible();
      await expect(cards, "two cards: the reports whose data does not come from patients").toHaveCount(2, { timeout: 3000 });
      // The second is the login panel: this very sign-in of the admin is one real session, without errors.
      await expect(cards.nth(1), "the second is the login panel, drawn by the admin's own session").toContainText("Panel de login", { timeout: 3000 });
      await expect(cards.nth(1)).toContainText("0%", { timeout: 3000 });
      await expect(cards.first(), "the first is Centros, with the pack's reviews").toContainText("Centros", { timeout: 3000 });
      await expect(cards.first()).toContainText(/Reseñas de Google analizadas en \d+ centros\./, { timeout: 3000 });
      await holdsFor(async () => {
        const text = await body.innerText();
        for (const phrase of [
          "s/d",
          "No hay solicitudes pendientes: la cola está al día.",
          "Sin búsquedas registradas todavía.",
          "Todavía no hay consultas con obra social registrada en este período.",
          "Todavía no hubo prestaciones a población exclusivamente pública en este período.",
          "Todavía no se registró ninguna llamada de IA en este período.",
        ]) {
          expect(text, `the summary does not draw an empty card saying «${phrase}»`).not.toContain(phrase);
        }
        expect(text, "nor an empty monthly statistics card").not.toMatch(/Todavía no hay consultas registradas para/);
      }, { during: 400 });
    }, { timeout: 45_000 });
    await admin.page.waitForLoadState("networkidle");
  });

  await test.step("every one of the thirteen report views paints without an alert or a placeholder", async () => {
    for (const view of REPORT_VIEWS) {
      const response = await admin.page.goto(`/admin/reportes?view=${view}`, { waitUntil: "load" });
      expect(response.status(), `reportes?view=${view} answers 200`).toBe(200);
      await expect(admin.page.locator("#rep-body"), `${view} paints its body`).toContainText(/\S{3}/);
      await holdsFor(async () => {
        expect(await admin.page.locator('#sec-reportes [role="alert"]').count(), `${view}: no error alert`).toBe(0);
        const text = await admin.page.locator("#rep-body").innerText();
        expect(text.match(BAD_TEXT)?.[0] ?? null, `${view}: no placeholder literal`).toBeNull();
      }, { during: 400 });
      // Let the view finish importing its modules before the next goto cancels them (a cancelled dynamic import
      // is a page error that says nothing about the product).
      await admin.page.waitForLoadState("networkidle");
    }
  });

  await test.step("every JSON report endpoint answers 200 with JSON that parses (no bare NaN)", async () => {
    for (const suffix of JSON_REPORTS) {
      const path = `/api/admin/reports${suffix}`;
      const response = await admin.context.request.get(path, { failOnStatusCode: false });
      expect(response.status(), `${path} answers 200`).toBe(200);
      expect(response.headers()["content-type"], `${path} is JSON`).toMatch(/^application\/json/);
      const text = await response.text();
      expect(() => JSON.parse(text), `${path} is valid JSON`).not.toThrow();
      expect(text, `${path} carries no Python-only literal`).not.toMatch(/\bNaN\b|\bInfinity\b/);
    }
  });

  await test.step("every CSV export is exactly its header row, as a download with the right type", async () => {
    for (const [name, header] of Object.entries(CSV_EXPORTS)) {
      const path = `/api/admin/reports/${name}`;
      const response = await admin.context.request.get(path, { failOnStatusCode: false });
      expect(response.status(), `${path} answers 200`).toBe(200);
      expect(response.headers()["content-type"], `${path} is CSV`).toMatch(/^text\/csv/);
      expect(response.headers()["content-disposition"], `${path} is a download`).toMatch(/^attachment; filename="[^"]+\.csv"$/);
      const body = (await response.text()).replace(/^﻿/, "");
      const lines = body.split(/\r?\n/).filter(Boolean);
      expect(lines[0], `${path} starts with its header row`).toBe(header);
      expect(body, `${path} carries no Python-only literal`).not.toMatch(/\bNaN\b|\bInfinity\b|\bNone\b/);
      if (name !== "statistics.csv") expect(lines, `${path} has no data rows on an empty database`).toHaveLength(1);
    }
  });

  expect(failed, "no 5xx from the server").toEqual([]);
  expect(admin.errors, "no page error").toEqual([]);
});

test("the patient site has no dead end on an empty account, and unknown addresses get the friendly 404", async ({ role, app }) => {
  test.setTimeout(240_000);
  const visitor = await role();
  const failed = watchServerErrors(visitor.page, app.baseUrl);
  const page = visitor.page;

  await test.step("the home, the doctors list, a doctor and a service open without a placeholder", async () => {
    for (const path of ["/", "/doctors", "/doctor/levin-camila", "/service/cardiologia-sampleton-hospital"]) {
      const response = await page.goto(path, { waitUntil: "load" });
      expect(response.status(), `${path} answers 200`).toBe(200);
      await expect(page.locator("h1").first(), `${path} paints a heading`).toBeVisible();
      const text = await page.locator("body").innerText();
      expect(text.match(BAD_TEXT)?.[0] ?? null, `${path}: no placeholder literal`).toBeNull();
    }
  });

  await test.step("a walk-in service offers no wizard: it says no appointment is needed, and its /book goes back to it", async () => {
    await page.goto("/service/demanda-espontanea-riverside-clinic", { waitUntil: "load" });
    await expect(page.getByText("No necesitás turno.")).toBeVisible();
    await page.goto("/service/demanda-espontanea-riverside-clinic/book?slots=1", { waitUntil: "load" });
    expect(new URL(page.url()).pathname, "the wizard of a walk-in service leads to its page").toBe("/service/demanda-espontanea-riverside-clinic");
    await expect(page.locator(".wz-seg-opt"), "no appointment is offered on a walk-in service").toHaveCount(0);
    await expect(page.getByText("No necesitás turno.")).toBeVisible();
  });

  await test.step("an unknown service, doctor or centre leads back to the list, not to an error", async () => {
    for (const [path, lands] of [["/service/no-such-service", "/"], ["/doctor/no-such-doctor", "/doctors"], ["/center/no-such", "/centers"]]) {
      const response = await page.goto(path, { waitUntil: "load" });
      expect(response.status(), `${path} lands on a page that opens`).toBe(200);
      expect(new URL(page.url()).pathname, `${path} leads to ${lands}`).toBe(lands);
    }
  });

  await test.step("an unknown address, public or under /admin, gets the friendly 404 page with a way home", async () => {
    for (const path of ["/no/such/page", "/admin/no-such-screen"]) {
      const response = await page.goto(path, { waitUntil: "load" });
      expect(response.status(), `${path} is a 404`).toBe(404);
      expect(response.headers()["content-type"], `${path} is a page, not JSON`).toMatch(/^text\/html/);
      await expect(page.getByRole("heading", { name: "No encontramos esa página" })).toBeVisible();
      await expect(page.getByText("Puede que el link esté roto o que la página se haya movido.")).toBeVisible();
      const home = page.getByRole("link", { name: "Volver al inicio" });
      await expect(home).toHaveAttribute("href", "/");
      const text = await page.locator("body").innerText();
      expect(text, `${path}: no raw JSON or trace`).not.toMatch(/Traceback|\{"error"|\bnot found\b/);
    }
    await page.getByRole("link", { name: "Volver al inicio" }).click();
    await expect(page.getByText("Turnos de la medicina pública de Sampleton")).toBeVisible();
    await page.goto("/no/such/page", { waitUntil: "load" });
    await page.locator("main").getByRole("link", { name: "Médicos", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Médicos y profesionales" }), "«Médicos» on the 404 page leads to the doctors list").toBeVisible();
  });

  await test.step("an unknown API address answers a small JSON 404, not a page", async () => {
    for (const path of ["/api/nope", "/api/admin/nope"]) {
      const response = await page.request.get(path, { failOnStatusCode: false });
      expect(response.status(), `${path} is a 404`).toBe(404);
      expect(response.headers()["content-type"]).toMatch(/^application\/json/);
    }
  });

  await test.step("a signed-out knock on a staff screen leads to the login and remembers where it was going", async () => {
    await page.goto("/admin/reportes", { waitUntil: "load" });
    const here = new URL(page.url());
    expect(here.pathname).toBe("/admin/login");
    expect(here.searchParams.get("next")).toBe("/admin/reportes");
    await expect(page.locator("#gate-form")).toBeVisible();
    await page.fill("#gate-user", ADMIN_USER);
    await page.fill("#gate-pass", ADMIN_PASS);
    await page.click("#gate-btn");
    await page.waitForURL((url) => url.pathname === "/admin/reportes");
  });

  await test.step("a signed-in staff member sees the same friendly 404 on an unknown panel screen", async () => {
    const response = await page.goto("/admin/no-such-screen", { waitUntil: "load" });
    expect(response.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "No encontramos esa página" })).toBeVisible();
  });

  await test.step("a patient with an empty account is told there is nothing yet and shown how to book and how to add a child", async () => {
    const dni = String(30_000_000 + Math.floor(Math.random() * 9_000_000));
    const seeded = await seedPatient(app, {
      dni, nombre: "Vacia", apellido: "Mañana", nacimiento: "1985-03-02", sexo: "F", password: LOGIN_PHRASE,
    });
    const patient = await role({ session: seeded.session });
    const mine = watchServerErrors(patient.page, app.baseUrl);
    // A new account with no request still has «Mis turnos» in the rail and opens on it (2026-10-28):
    // the empty list is an instruction with ONE big «Sacar un turno» button (decision of 2026-10-28), not "no data".
    await patient.page.goto("/account", { waitUntil: "load" });
    await expect(patient.page.locator('.rail-btn[data-tab="deps"]'), "the rail is there").toBeVisible();
    await expect(patient.page.locator('.rail-btn[data-tab="turnos"]'), "a new account has its «Mis turnos» entry").toBeVisible();
    await expect(patient.page.locator('.rail-btn[data-tab="turnos"]'), "and it is the open tab").toHaveClass(/\bon\b/);
    await expect(patient.page.getByText("Todavía no tenés turnos. Para sacar el primero, elegí una especialidad y un horario.")).toBeVisible();
    const book = patient.page.getByRole("link", { name: "Sacar un turno", exact: true });
    await expect(book, "the empty list has one «Sacar un turno» button and no second, smaller one").toHaveCount(1);
    await expect(book).toBeVisible();
    await expect(patient.page.locator(".mt-sacar"), "the header's small button is not repeated").toHaveCount(0);
    expect(await book.evaluate((el) => parseFloat(getComputedStyle(el).fontSize)), "the button text is at least 16px").toBeGreaterThanOrEqual(16);
    expect((await stableBox(book, "the book button")).height, "and the button is a big target").toBeGreaterThanOrEqual(56);
    await book.click();
    await expect(patient.page, "it leads to the specialities").toHaveURL(/\/$/);
    await patient.page.goto("/account", { waitUntil: "load" });
    await patient.page.locator('.rail-btn[data-tab="deps"]').click();
    await expect(patient.page.getByRole("heading", { name: "Personas a cargo", exact: true })).toBeVisible();
    await expect(patient.page.getByText("Agregalos una vez")).toBeVisible();
    await expect(patient.page.getByRole("button", { name: /Agregar persona a cargo/ })).toBeVisible();
    const text = await patient.page.locator("body").innerText();
    expect(text.match(BAD_TEXT)?.[0] ?? null, "the account shows no placeholder literal").toBeNull();
    expect(mine, "no 5xx on the account").toEqual([]);
    expect(patient.errors, "no page error on the account").toEqual([]);
  });

  expect(failed, "no 5xx from the server").toEqual([]);
  expect(visitor.errors, "no page error").toEqual([]);
});
