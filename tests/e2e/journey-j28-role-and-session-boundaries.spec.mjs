// tests/e2e/journey-j28-role-and-session-boundaries.spec.mjs -- J28 ("who may do what: the SERVER
// refuses what a role, a visitor or a dead session must not do") cross-role journey acceptance.
//
// On the first day the front desk (admisionista), the doctors (medico), the reports staff (reportes),
// the schedule managers (gestor_agendas), the admin and the patients share ONE server. The rule
// (docs/architecture/admin-panel-y-auth.md, the product owner's vision 2026-10-23):
// the server checks the right, a hidden menu item is not a right, and what is not yours answers as if
// it did not exist -- 404, never 403, never the data. J15 pins one slice (a doctor and the other
// doctor's patients); admin-role-screens-no-403 pins that the menus open. This journey pins the
// refusals themselves, door by door, derived from the product and not from a hand-kept list.
//
// The matrix is the app's own: `journey_fixture.py access-matrix` walks every /admin and /api/admin
// route the running app mounts, with the permission core/permissions.py maps it to and the permission
// set of each built-in role. A route added tomorrow shows up here with no edit; an UNMAPPED or public
// route must be classified below on purpose, so a new door cannot be left open by accident.
//
// Terminal outcome:
//   - a visitor with no session, with a PATIENT's session cookie or with a forged staff cookie is
//     refused at every panel door: a screen redirects to /admin/login?next=<screen>, an API answers
//     401 {"error":"no_autenticado"} as JSON, never HTML, never data;
//   - each staff role is let through exactly where its permissions say and refused with 403 everywhere
//     else (screens, reads AND mutations), the refusals carrying none of the seeded patients' data;
//     hand-written anchors (desk vs. admin screens, doctor vs. reports, reports vs. mutations, schedule
//     manager vs. clinical data) keep the map itself honest;
//   - a doctor cannot read, approve, assign, reject, cancel, edit or mark attendance on another doctor's
//     request: 404, the very answer for a request that does not exist, and nothing changes in the database;
//   - a patient cannot reach another patient's request, appointment file (.ics), calendar feed or
//     dependents: 404 (401 with no session), the very answer for an id that does not exist;
//   - signing out ends the browser's session; a disabled operator's open session dies on the next request
//     (401, the login page); a role change applies on the next request, not at the next login.
//
// Steps: test 1 the visitor, the patient's cookie and the forged cookie; test 2 the staff matrix, the
// anchors and the doctor's boundary; test 3 the patients and the lifecycle of a staff session.
//
// What the matrix does NOT prove: the expected rights come from the product itself (the permission map
// and the role sets), so a WRONG assignment in those tables is only caught where a hand-written anchor
// names it; a permitted MUTATION is not sent (it would really happen), so for those doors only the
// refusal side is exercised; a held read door must open and not answer 5xx, its content is pinned elsewhere.
//
// «Cerrar sesión» ends the session, not only the browser's copy of the cookie (step 5, TASK-55): a copy of
// the cookie made earlier is refused too. Unit-level proofs of every kind of session: test_session_revocation.py.
import {
  test, expect, runFixture, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS, DESK,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const CENTER = "sampleton-hospital";
const A = { id: "levin-camila", nombre: "Camila Levin", usuario: "e2e-dr-levin" };
const B = { id: "ruiz-sergio", nombre: "Sergio Ruiz", usuario: "e2e-dr-ruiz" };
const ADMIN = { usuario: "e2e-admin", password: LOGIN_PHRASE, rol: "admin" };
const GESTOR = { usuario: "e2e-gestor", password: LOGIN_PHRASE, rol: "gestor_agendas" };
const REPORTES = { usuario: "e2e-reportes", password: LOGIN_PHRASE, rol: "reportes" };
const STAFF_ROLES = [
  { rol: "admin", usuario: ADMIN.usuario },
  { rol: "admisionista", usuario: DESK.usuario },
  { rol: "gestor_agendas", usuario: GESTOR.usuario },
  { rol: "medico", usuario: A.usuario },
  { rol: "reportes", usuario: REPORTES.usuario },
];

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_j28" },
  journey: {
    staff: [
      ADMIN, DESK, GESTOR, REPORTES,
      { usuario: A.usuario, password: LOGIN_PHRASE, rol: "medico", medico_id: A.id },
      { usuario: B.usuario, password: LOGIN_PHRASE, rol: "medico", medico_id: B.id },
    ],
  },
});

test.describe.configure({ mode: "serial" });

// ---- the classification of the doors that are NOT «permission-gated staff doors» -----------------------

// Doors a visitor with no session is MEANT to reach: the login page and its API, the passkey challenge, the
// reset-password link, the public config the login page reads, the logout (idempotent), the demo entrances
// (404 on an installation without the demo) and a static asset.
const PUBLIC = new Set([
  "GET /api/admin/config", "POST /api/admin/login", "POST /api/admin/logout",
  "POST /api/admin/passkeys/login-options", "POST /api/admin/passkeys/login",
  "GET /admin", "GET /admin/login", "GET /admin/reset-password", "POST /api/admin/reset-password",
  "GET /admin/demo-access", "GET /admin/demo-doctor-access", "GET /admin-tabla.js", "GET /admin-tabla.js/",
  // The old doctor-import address: a bare redirect to Médicos, which gates itself (2026-11-03).
  "GET /admin/users/import",
]);
// The usability study is off on this installation (manifest `requires_settings`): its doors answer a plain 404.
const FEATURE_OFF = (route) => route.template.includes("/usability");
// Screens that exist only where a deployment has the thing they show (the Pinamar plan page):
// on the example-town installation they answer 404 to a role that may open them -- and still 403 to one that may not.
// The HSI screen is not one of them: without the connector it opens (200) and names that state (2026-11-10).
// Nested pages the panel shell does not know as a screen (the printable register): after the login they have
// nowhere to «come back to» (admin_navigation.canonical_admin_target).
const PRINT_PAGES = new Set(["/admin/ai-register/print", "/admin/cola/print"]);
const NOT_DEPLOYED_SCREENS = new Map([["/admin/plan", 404]]);
// Routes the permission map does not know: the handler's own flags decide (legacy roles only; any custom
// role is refused). Each one is classified here; an unclassified one fails the journey.
// Admin only: the legacy rule (require_admin solo_admin) decides because no permission maps them. Mapping them to a
// permission would let any custom role holding it through, past solo_admin.
const AI_REGISTER = (route) => route.template.includes("/ai-register") || route.template === "/api/admin/analytics/keys";
const EXTERNAL_HISTORY = "/api/admin/patient-external-history";                    // admin + doctor (scoped inside)
const ANY_STAFF_UNMAPPED = new Set(["POST /api/admin/client-errors"]);              // the panel's error beacon
// The personal doctor workspace: the admin holds «atención médica» but has no agenda of his own, the
// handlers answer 403 solo_medico (admin_panel/doctor_workspace_screen.py, doctor_appointments_router.py).
const DOCTOR_PERSONAL = ["/api/admin/my-appointments", "/admin/mis-turnos", "/admin/onboarding", "/api/admin/onboarding", "/api/admin/doctor-profile"];
const isDoctorPersonal = (route) => DOCTOR_PERSONAL.some((p) => route.template === p || route.template.startsWith(p + "/"));
const DESK_NOT_DOCTOR = (route) => route.template === "/api/admin/dni-claims" || route.template.startsWith("/api/admin/dni-claims/");
const GATE_ERRORS =["rol_no_autorizado", "solo_admin"];
const key = (route) => `${route.method} ${route.template}`;
const READ = new Set(["GET", "HEAD"]);

// One seeding per test and per run: DNIs and the appointment minute move on, so repeated runs on one
// worker (one database) never collide.
let seq = 0;
async function seedWorld(app) {
  seq += 1;
  const dni = (k) => String(30290000 + seq * 10 + k);
  const at = `09:${String(seq % 60).padStart(2, "0")}`;
  const visit = (spec) => runFixture(app, "seed-visit", { spec: {
    servicio_id: SERVICE_ID, centro: CENTER, interview: false, when: "+2", at, ...spec } });
  const world = {
    dniB: dni(1), dniA: dni(2), dniPending: dni(3), nameB: "Zuberbuhler", nameA: "Yacuzzi", namePending: "Wasserman",
  };
  world.b = await visit({ dni: world.dniB, nombre: "Beto", apellido: world.nameB, doctor_id: B.id, doctor_name: B.nombre, account: true });
  world.a = await visit({ dni: world.dniA, nombre: "Ana", apellido: world.nameA, doctor_id: A.id, doctor_name: A.nombre, account: true });
  world.pending = await visit({ dni: world.dniPending, nombre: "Pia", apellido: world.namePending,
    doctor_id: B.id, doctor_name: B.nombre, pending: true });
  world.secrets = [world.dniB, world.dniA, world.dniPending, world.nameB, world.nameA, world.namePending];
  return world;
}

let matrixOnce = null;
const loadMatrix = (app) => (matrixOnce ??= runFixture(app, "access-matrix"));

/** One request through a context, never throwing on 4xx; screens ask for HTML like a browser does. */
async function knock(context, route, { path = route.path, data } = {}) {
  const screen = route.kind === "screen";
  const response = await context.request.fetch(path, {
    method: route.method, maxRedirects: 0, failOnStatusCode: false, timeout: 20_000,
    headers: { accept: screen ? "text/html" : "application/json" },
    data: data ?? (READ.has(route.method) || route.method === "DELETE" ? undefined : {}),
  });
  return { status: response.status(), headers: response.headers(), text: await response.text() };
}

const json = (text) => { try { return JSON.parse(text); } catch { return null; } };
const leaks = (text, secrets) => secrets.filter((s) => text.includes(s));

async function signIn(role, who) {
  const person = await role();
  const login = await person.context.request.post("/api/admin/login", { data: { usuario: who.usuario, password: LOGIN_PHRASE } });
  expect(login.status(), `${who.usuario} signs in`).toBe(200);
  return person;
}

// ---- 1 -------------------------------------------------------------------------------------------------

test("J28.1 — no session, a patient's cookie or a forged staff cookie is refused at every panel door", async ({ app, role, baseURL }) => {
  test.setTimeout(240_000);
  const matrix = await loadMatrix(app);
  const world = await seedWorld(app);
  const doors = matrix.routes.filter((r) => !PUBLIC.has(key(r)));

  await test.step("0. the matrix is the app's own and is not trivial", async () => {
    expect(matrix.routes.length, "every /admin and /api/admin route of the running app").toBeGreaterThanOrEqual(150);
    expect(matrix.routes.filter((r) => r.kind === "screen").length, "panel screens").toBeGreaterThanOrEqual(25);
    expect(doors.length, "doors to knock on").toBeGreaterThanOrEqual(140);
    const known = new Set(matrix.routes.map(key));
    expect([...PUBLIC].filter((k) => !known.has(k)), "every public door named here exists").toEqual([]);
  });

  async function sweep(context) {
    const failures = [];
    for (const route of doors) {
      if (route.template.endsWith("/events")) continue; // a server-sent stream: refused the same way, but it never ends when allowed
      const res = await knock(context, route);
      const where = `${key(route)} -> ${res.status}`;
      if (leaks(res.text, world.secrets).length) failures.push(`${where}: the body carries a patient's data`);
      if (FEATURE_OFF(route)) {
        if (res.status !== 404) failures.push(`${where}: a feature that is off must answer 404`);
      } else if (route.kind === "screen") {
        // A screen the shell knows (admin_navigation.canonical_admin_target) comes back to itself after the
        // login; a printable page the shell does not know lands on the bare login page.
        const want = PRINT_PAGES.has(route.template) ? "/admin/login" : `/admin/login?next=${encodeURIComponent(route.path)}`;
        if (res.status !== 303 || res.headers.location !== want) failures.push(`${where} ${res.headers.location ?? ""}: want 303 ${want}`);
      } else {
        const body = json(res.text);
        if (res.status !== 401 || !body || body.error !== "no_autenticado" || !/json/.test(res.headers["content-type"] || "")) {
          failures.push(`${where} ${res.text.slice(0, 60)}: want a JSON 401 no_autenticado`);
        }
      }
    }
    return failures;
  }

  await test.step("1. with no session every screen redirects to /admin/login?next=… and every API answers a JSON 401, with no data", async () => {
    const visitor = await role();
    expect(await sweep(visitor.context)).toEqual([]);
  });

  await test.step("2. a patient's session cookie is not a staff session: same refusals on every door", async () => {
    const patient = await role({ session: world.b.session });
    const me = await patient.context.request.get("/api/account/me");
    expect(me.status(), "the patient's cookie is a real patient session").toBe(200);
    expect(await sweep(patient.context)).toEqual([]);
  });

  await test.step("3. a forged staff cookie is refused: garbage, and a real cookie with its role rewritten to admin", async () => {
    const desk = await signIn(role, { usuario: DESK.usuario });
    const real = (await desk.context.cookies()).find((c) => c.name === "st_admin").value;
    const [payload, ...rest] = real.split(".");
    const claims = JSON.parse(Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
    expect(claims.r, "the real desk cookie says admisionista").toBe("admisionista");
    const promoted = Buffer.from(JSON.stringify({ ...claims, r: "admin" })).toString("base64url");
    for (const value of ["not-a-session", "x.y.z", [promoted, ...rest].join(".")]) {
      const forger = await role();
      await forger.context.addCookies([{ name: "st_admin", value, url: baseURL }]);
      for (const path of ["/api/admin/whoami", "/api/admin/users", "/api/admin/requests"]) {
        const res = await forger.context.request.get(path, { failOnStatusCode: false });
        expect(res.status(), `${path} with a forged cookie`).toBe(401);
        expect((await res.json()).error).toBe("no_autenticado");
      }
      const screen = await forger.context.request.get("/admin/users", { maxRedirects: 0, failOnStatusCode: false, headers: { accept: "text/html" } });
      expect([screen.status(), screen.headers().location]).toEqual([303, "/admin/login?next=%2Fadmin%2Fusers"]);
    }
    // …while the real desk cookie, untouched, still works: the refusals above are about the forgery.
    expect((await desk.context.request.get("/api/admin/requests")).status()).toBe(200);
  });
});

// ---- 2 -------------------------------------------------------------------------------------------------

test("J28.2 — each staff role is refused by the server everywhere outside its permissions; a doctor cannot touch another doctor's request", async ({ app, role }) => {
  test.setTimeout(300_000);
  const matrix = await loadMatrix(app);
  const world = await seedWorld(app);
  const doors = matrix.routes.filter((r) => !PUBLIC.has(key(r)) && !FEATURE_OFF(r) && !r.template.endsWith("/events"));
  const perms = matrix.roles;
  const held = (rol, need) => need === "*" || (Array.isArray(need) ? need.some((p) => perms[rol].includes(p)) : perms[rol].includes(need));
  const people = {};
  for (const who of STAFF_ROLES) people[who.rol] = await signIn(role, who);
  const tally = { allowed: 0, refused: 0 };

  await test.step("1. every route the app mounts is classified: mapped to a permission, public, or one of the named exceptions", async () => {
    const unclassified = doors.filter((r) => r.needs.admin === null
      && !AI_REGISTER(r) && r.template !== EXTERNAL_HISTORY && !ANY_STAFF_UNMAPPED.has(key(r))).map(key);
    expect(unclassified, "an unmapped door nobody has classified").toEqual([]);
    expect(Object.keys(perms).sort()).toEqual(["admin", "admisionista", "gestor_agendas", "medico", "reportes"]);
    expect(perms.reportes, "reports staff hold only reports").toEqual(["reportes"]);
    expect(perms.gestor_agendas, "the schedule manager is the desk plus the doctors' schedules").toEqual([...new Set([...perms.admisionista, "medicos"])].sort());
  });

  for (const { rol } of STAFF_ROLES) {
    await test.step(`2. ${rol}: let through exactly where its permissions say, refused (403, no data) everywhere else`, async () => {
      const failures = [];
      for (const route of doors) {
        const need = route.needs[rol];
        let allow;
        if (need !== null) allow = held(rol, need);
        else if (AI_REGISTER(route)) allow = rol === "admin";
        else if (route.template === EXTERNAL_HISTORY) allow = rol === "admin" || rol === "medico";
        else continue; // the error beacon: any staff, nothing to pin
        // The desk's claims on a document number: mapped to `crear_turnos`, which the médico preset also holds,
        // but the handler refuses a médico with rol_no_autorizado (TASK-68, admin_patients_router.native_dni_claims).
        if (rol === "medico" && DESK_NOT_DOCTOR(route)) allow = false;
        const personal = rol === "admin" && isDoctorPersonal(route);
        if (allow && !personal && !READ.has(route.method)) continue; // a permitted mutation is not sent: it would really happen
        const res = await knock(people[rol].context, route);
        const where = `${rol} ${key(route)} -> ${res.status}`;
        if (allow && !personal) {
          tally.allowed += 1;
          const opens = route.kind === "screen"
            ? res.status === (NOT_DEPLOYED_SCREENS.get(route.template) ?? 200)
            : ![401, 403].includes(res.status) && res.status < 500; // a held door that answers 5xx is not open either
          if (!opens) failures.push(`${where}: the permission is held but the door is shut`);
          continue;
        }
        tally.refused += 1;
        if (personal && route.template === "/admin/mis-turnos") {
          // An admin who opens a doctor's own appointments lands on his start page, not on an error.
          if (res.status !== 303 || !res.headers.location) failures.push(`${where}: want a redirect to the start page`);
          continue;
        }
        const body = json(res.text);
        if (res.status !== 403) failures.push(`${where}: want 403`);
        else if (route.kind === "api" && !(personal ? body?.error === "solo_medico" : GATE_ERRORS.includes(body?.error))) failures.push(`${where} ${res.text.slice(0, 60)}: want a gate refusal`);
        if (route.kind === "screen" && !personal && !/html/.test(res.headers["content-type"] || "")) failures.push(`${where}: a refused screen is a page`);
        if (leaks(res.text, world.secrets).length) failures.push(`${where}: the refusal carries a patient's data`);
      }
      expect(failures).toEqual([]);
    });
  }

  await test.step("3. the matrix was really exercised: hundreds of refusals across the roles, and the menu screens of every role open", async () => {
    expect(tally.refused, "refused cells (~440 today)").toBeGreaterThanOrEqual(400);
    expect(tally.allowed, "allowed cells (~180 today)").toBeGreaterThanOrEqual(150);
    // The manifest of every module declares which roles a screen is for; each of them must be able to open it.
    const unopened = [];
    for (const section of matrix.manifest) {
      const route = matrix.routes.find((r) => r.method === "GET" && r.template === section.path);
      for (const rol of section.roles) {
        if (!people[rol]) continue; // a role this journey does not log in as
        if (section.id === "usability") continue;
        const res = await knock(people[rol].context, route);
        if (res.status !== 200) unopened.push(`${rol} ${section.path} -> ${res.status}`);
      }
    }
    expect(unopened, "a screen the manifest offers to a role that the server then refuses").toEqual([]);
  });

  await test.step("4. anchors written by hand, so the map itself is checked: the desk, the doctor, reports and the schedule manager", async () => {
    const refused = [
      ["admisionista", "GET", "/api/admin/users"], ["admisionista", "POST", "/api/admin/roles"], ["admisionista", "GET", "/admin/settings"],
      ["admisionista", "GET", "/api/admin/prompts"], ["admisionista", "GET", "/api/admin/system"], ["admisionista", "GET", "/api/admin/hsi-sync"],
      ["admisionista", "GET", "/api/admin/reports"], ["admisionista", "POST", "/api/admin/theme"], ["admisionista", "GET", "/api/admin/doctor-patients"],
      ["admisionista", "GET", "/api/admin/intake/consult/1"], ["admisionista", "PUT", "/api/admin/content/doctors/ruiz-sergio"],
      ["medico", "GET", "/api/admin/reports"], ["medico", "GET", "/api/admin/users"], ["medico", "GET", "/admin/pacientes"],
      ["medico", "GET", `/api/admin/patients/${world.dniB}`], ["medico", "DELETE", `/api/admin/patients/${world.dniB}`],
      ["medico", "GET", `/api/admin/patients/${world.dniB}/data-export`], ["medico", "POST", "/api/admin/settings"],
      ["medico", "POST", "/api/admin/intake/settings"], ["medico", "POST", "/api/admin/content/doctors"],
      ["reportes", "POST", "/api/admin/requests/1/approve"], ["reportes", "POST", "/api/admin/requests/1/cancel"],
      ["reportes", "POST", "/api/admin/appointments"], ["reportes", "GET", "/api/admin/day-queue"], ["reportes", "GET", "/api/admin/requests"],
      ["reportes", "GET", "/api/admin/patients"], ["reportes", "GET", `/api/admin/patients/${world.dniB}`], ["reportes", "PUT", "/api/admin/content/doctors/ruiz-sergio"],
      ["reportes", "GET", "/api/admin/doctors/1/appointments"], ["reportes", "GET", "/admin/cola"],
      ["gestor_agendas", "GET", "/api/admin/intake/consult/1"], ["gestor_agendas", "POST", `/api/admin/patients/${world.dniB}/clinical-history`],
      ["gestor_agendas", "GET", `/api/admin/patient-summary?dni=${world.dniB}`], ["gestor_agendas", "GET", "/api/admin/doctor-patients"],
      ["gestor_agendas", "GET", `/api/admin/patients/${world.dniB}/access-log`], ["gestor_agendas", "GET", "/api/admin/reports"],
      ["gestor_agendas", "GET", "/api/admin/users"], ["gestor_agendas", "GET", "/admin/consult"],
    ];
    const failures = [];
    for (const [rol, method, path] of refused) {
      const res = await knock(people[rol].context, { method, kind: "api" }, { path });
      if (res.status !== 403 || !GATE_ERRORS.includes(json(res.text)?.error) || leaks(res.text, world.secrets).length) {
        failures.push(`${rol} ${method} ${path} -> ${res.status} ${res.text.slice(0, 50)}`);
      }
    }
    expect(failures, "refused by the server").toEqual([]);
    const opened = [
      ["admisionista", "/api/admin/day-queue"], ["admisionista", "/api/admin/requests"], ["admisionista", `/api/admin/patients/${world.dniB}`],
      ["medico", "/api/admin/doctor-patients"], ["medico", "/api/admin/requests"], ["reportes", "/api/admin/reports"],
      ["reportes", "/api/admin/doctors/stats"], ["gestor_agendas", "/api/admin/doctors/stats"], ["gestor_agendas", "/api/admin/requests"],
    ];
    for (const [rol, path] of opened) {
      const res = await people[rol].context.request.get(path, { failOnStatusCode: false });
      expect(res.status(), `${rol} ${path}`).toBe(200);
    }
  });

  await test.step("5. a doctor cannot read, approve, assign, reject, cancel, edit or mark attendance on another doctor's request: 404 as if it did not exist, and nothing changes", async () => {
    const doctor = people.medico;
    const desk = people.admisionista;
    const mine = world.a.request_id;
    const theirs = [world.b.request_id, world.pending.request_id];
    const ghost = 987654321;
    const verbs = ["approve", "assign", "reject", "cancel", "campos", "patient-data", "attendance"];
    for (const id of theirs) {
      for (const verb of verbs) {
        const real = await doctor.context.request.post(`/api/admin/requests/${id}/${verb}`, { data: {}, failOnStatusCode: false });
        const nothing = await doctor.context.request.post(`/api/admin/requests/${ghost}/${verb}`, { data: {}, failOnStatusCode: false });
        expect(real.status(), `${verb} on another doctor's request ${id}`).toBe(404);
        expect(await real.text(), `…the very answer for a request that does not exist (${verb})`).toBe(await nothing.text());
      }
      const order = await doctor.context.request.get(`/api/admin/order/${id}`, { failOnStatusCode: false });
      expect(order.status(), `the referral file of ${id}`).toBe(404);
      expect(await order.text()).toBe(await (await doctor.context.request.get(`/api/admin/order/${ghost}`, { failOnStatusCode: false })).text());
    }
    // Nothing moved: read back through the app's own reader.
    const approved = (await runFixture(app, "request", { id: String(world.b.request_id) })).request;
    const pending = (await runFixture(app, "request", { id: String(world.pending.request_id) })).request;
    expect([approved.estado, approved.medico_asignado_id], "the approved request is untouched").toEqual(["aprobada", B.id]);
    expect([pending.estado, pending.medico_asignado_id], "the pending request is untouched").toEqual(["pendiente", B.id]);

    // His own is another matter: the desk's queue lists every doctor's pending request, his lists none of the other's.
    const deskList = await (await desk.context.request.get("/api/admin/requests")).text();
    expect(deskList, "the desk sees the pending request (the seed is not an empty list)").toContain(world.dniPending);
    const doctorList = await (await doctor.context.request.get("/api/admin/requests")).text();
    expect(leaks(doctorList, [world.dniPending, world.namePending, world.dniB, world.nameB]), "the doctor's queue names none of the other doctor's patients").toEqual([]);
    const ownFile = await doctor.context.request.post(`/api/admin/requests/${mine}/attendance`, { data: { asistencia: "presente" }, failOnStatusCode: false });
    expect(ownFile.status(), "his own request is reachable (404 is about whose it is, not about the route)").not.toBe(404);
  });
});

// ---- 3 -------------------------------------------------------------------------------------------------

test("J28.3 — a patient cannot reach another patient's data; sign-out, a disabled operator and a changed role take effect on the next request", async ({ app, role, baseURL }) => {
  test.setTimeout(240_000);
  const world = await seedWorld(app);
  const today = new Date();
  const child = { dni: String(Number(world.dniB) + 500), nombre: "Nico", apellido: "Zuberbuhler",
    nacimiento: `${today.getUTCFullYear() - 6}-01-15` };
  await runFixture(app, "seed-dependent", { spec: { holder: world.dniB, ...child, vinculo: "hijo" } });
  const patientA = await role({ session: world.a.session });
  const patientB = await role({ session: world.b.session });
  const visitor = await role();
  const get = (who, path) => who.context.request.get(path, { failOnStatusCode: false });
  const post = (who, path, data = {}) => who.context.request.post(path, { data, failOnStatusCode: false });
  const bId = world.b.request_id;
  const ghost = 987654321;

  await test.step("1. patient A on patient B's request, appointment file and coverage: 404, the very answer for an id that does not exist", async () => {
    const asks = [
      ["GET", (id) => `/api/requests/${id}`], ["POST", (id) => `/api/requests/${id}/cancel`],
      ["POST", (id) => `/api/requests/${id}/reprogramar`], ["POST", (id) => `/api/requests/${id}/waitlist`],
      ["GET", (id) => `/api/requests/${id}/coverage`], ["POST", (id) => `/api/requests/${id}/coverage`],
      ["GET", (id) => `/api/appointment/${id}.ics`],
    ];
    for (const [method, path] of asks) {
      const theirs = await patientA.context.request.fetch(path(bId), { method, data: method === "POST" ? {} : undefined, failOnStatusCode: false });
      const nothing = await patientA.context.request.fetch(path(ghost), { method, data: method === "POST" ? {} : undefined, failOnStatusCode: false });
      const text = await theirs.text();
      expect(theirs.status(), `${method} ${path(bId)} as another patient`).toBe(404);
      expect(text, "…the same body as for a request that does not exist").toBe(await nothing.text());
      expect(leaks(text, world.secrets)).toEqual([]);
    }
    const intact = (await runFixture(app, "request", { id: String(bId) })).request;
    expect(intact.estado, "B's appointment survived A's attempts to cancel it").toBe("aprobada");
  });

  await test.step("2. A's own data opens, and only A's: the request, the .ics, the list, the account and the calendar feed", async () => {
    const own = await get(patientA, `/api/requests/${world.a.request_id}`);
    expect(own.status()).toBe(200);
    const ics = await get(patientA, `/api/appointment/${world.a.request_id}.ics`);
    expect(ics.status()).toBe(200);
    const list = await (await get(patientA, "/api/requests")).json();
    expect(list.solicitudes.map((r) => r.id), "the list holds A's request and nobody else's").toEqual([world.a.request_id]);
    const me = await (await get(patientA, "/api/account/me")).json();
    expect(me.usuario.dni, "the account is A's").toBe(world.dniA);
    const feedUrl = (await (await get(patientA, "/api/calendar/feed")).json()).url;
    const feed = await get(visitor, new URL(feedUrl).pathname); // the secret link needs no session: that is its contract
    expect(feed.status()).toBe(200);
    const text = await feed.text();
    expect((text.match(/BEGIN:VEVENT/g) || []).length, "A's feed holds A's one appointment").toBe(1);
    expect(text, "…and it is A's").toContain(`UID:turno-${world.a.request_id}@sanaturnos`);
    expect(text).toContain(`Turno de Ana ${world.nameA}`);
    expect(text, "…and nothing of B's").not.toContain(`turno-${bId}@`);
    expect(leaks(text, [world.dniB, world.nameB])).toEqual([]);
    expect((await get(visitor, "/feed/appointments/not-a-real-feed-token.ics")).status(), "a made-up feed token").toBe(404);
  });

  await test.step("3. with no session the same doors answer 401 auth_requerida, with nothing of anybody's", async () => {
    for (const path of [`/api/requests/${bId}`, `/api/appointment/${bId}.ics`, "/api/requests", "/api/calendar/feed"]) {
      const res = await get(visitor, path);
      expect([res.status(), (await res.json()).error], path).toEqual([401, "auth_requerida"]);
    }
    for (const path of ["/api/account/me", "/api/account/dependents"]) {
      const res = await get(visitor, path);
      expect([res.status(), (await res.json()).error], path).toEqual([401, "no_autenticado"]);
    }
    expect((await post(visitor, `/api/requests/${bId}/cancel`)).status()).toBe(401);
  });

  await test.step("4. A cannot see or delete B's dependent; B can see his own", async () => {
    const listed = await (await get(patientA, "/api/account/dependents")).json();
    expect(JSON.stringify(listed), "A's account lists no dependent of B's").not.toContain(child.dni);
    const attempt = await post(patientA, "/api/account/dependents/delete", { dni: child.dni });
    expect((await attempt.json()).ok, "A's delete finds nothing to delete").toBe(false);
    const kept = (await runFixture(app, "dependents", { dni: world.dniB })).dependents;
    expect(kept.map((d) => d.dni), "B's dependent is still there").toEqual([child.dni]);
    const mine = await (await get(patientB, "/api/account/dependents")).json();
    expect(JSON.stringify(mine)).toContain(child.dni);
  });

  const owner = await role();
  await test.step("5. «Cerrar sesión» ends the browser's session: the same context is signed out for the API and the screens", async () => {
    const desk = await signIn(role, { usuario: DESK.usuario });
    expect((await get(desk, "/api/admin/requests")).status()).toBe(200);
    const copied = (await desk.context.cookies()).find((c) => c.name === "st_admin").value;
    const out = await post(desk, "/api/admin/logout");
    expect(out.status()).toBe(200);
    expect(out.headers()["set-cookie"] ?? "", "the cookie is cleared in the browser").toMatch(/st_admin=;.*Max-Age=0/);
    const after = await get(desk, "/api/admin/requests");
    expect([after.status(), (await after.json()).error]).toEqual([401, "no_autenticado"]);
    const screen = await desk.context.request.get("/admin/cola", { maxRedirects: 0, failOnStatusCode: false, headers: { accept: "text/html" } });
    expect([screen.status(), screen.headers().location]).toEqual([303, "/admin/login?next=%2Fadmin%2Fcola"]);
    const thief = await role();
    await thief.context.addCookies([{ name: "st_admin", value: copied, url: baseURL }]);
    const stolen = await thief.context.request.get("/api/admin/requests", { failOnStatusCode: false });
    expect([stolen.status(), (await stolen.json()).error], "a copy of the cookie made before signing out is refused").toEqual([401, "no_autenticado"]);
    const login = await owner.context.request.post("/api/admin/login", { data: { usuario: ADMIN_USER, password: ADMIN_PASS } });
    expect(login.status(), "the owner signs in").toBe(200);
  });

  await test.step("6. the admin disables an operator: the session he had open dies on his next request, the others' do not", async () => {
    const name = `e2e-temp-${world.dniB}`;
    const created = await owner.context.request.post("/api/admin/users", { data: { usuario: name, password: LOGIN_PHRASE, rol: "admisionista" } });
    expect(created.status(), await created.text()).toBe(200);
    const temp = await signIn(role, { usuario: name });
    expect((await get(temp, "/api/admin/requests")).status(), "the operator works while active").toBe(200);
    expect((await temp.context.request.get("/admin/cola", { maxRedirects: 0, failOnStatusCode: false, headers: { accept: "text/html" } })).status()).toBe(200);
    const bystander = await signIn(role, { usuario: DESK.usuario });

    const disabled = await owner.context.request.delete(`/api/admin/users/${name}`);
    expect(disabled.status(), "the panel's «Desactivar»").toBe(200);

    const dead = await get(temp, "/api/admin/requests");
    expect([dead.status(), (await dead.json()).error], "the open session answers 401 on its next request").toEqual([401, "no_autenticado"]);
    const deadScreen = await temp.context.request.get("/admin/cola", { maxRedirects: 0, failOnStatusCode: false, headers: { accept: "text/html" } });
    expect([deadScreen.status(), deadScreen.headers().location]).toEqual([303, "/admin/login?next=%2Fadmin%2Fcola"]);
    const again = await temp.context.request.post("/api/admin/login", { data: { usuario: name, password: LOGIN_PHRASE } });
    expect(again.status(), "…and he cannot sign in again").toBe(401);
    expect((await get(bystander, "/api/admin/requests")).status(), "another operator's session is untouched").toBe(200);
  });

  await test.step("7. the admin changes an operator's role: it applies on the next request with the old cookie, not at the next login", async () => {
    const name = `e2e-shift-${world.dniB}`;
    const created = await owner.context.request.post("/api/admin/users", { data: { usuario: name, password: LOGIN_PHRASE, rol: "admisionista" } });
    expect(created.status(), await created.text()).toBe(200);
    const person = await signIn(role, { usuario: name });
    expect((await get(person, "/api/admin/requests")).status(), "as a desk operator").toBe(200);
    expect((await get(person, "/api/admin/reports")).status(), "…who has no reports").toBe(403);
    const edited = await owner.context.request.put(`/api/admin/users/${name}`, { data: { rol: "reportes" } });
    expect(edited.status(), await edited.text()).toBe(200);
    const queue = await get(person, "/api/admin/requests");
    expect([queue.status(), (await queue.json()).error], "the desk door closes at once").toEqual([403, "rol_no_autorizado"]);
    expect((await get(person, "/api/admin/reports")).status(), "…and the reports door opens at once").toBe(200);
  });
});
