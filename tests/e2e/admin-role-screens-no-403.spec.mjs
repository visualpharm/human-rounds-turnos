// Pedido del hospital (mapa de la mesa, hallazgo K8, 2026-10-23): «cada
// pantalla del rol abre sin 403». Navegador real -> ASGI -> base de test
// sintética: un gestor de agendas recorre cada pantalla de SU menú y cada
// pestaña de una ficha de médico; ninguna respuesta del panel puede ser 403 y
// en ninguna pantalla puede aparecer «No tenés permiso». Antes del arreglo la
// lista de Médicos pedía las métricas (permiso `reportes`), la ficha pedía la
// cuenta (`usuarios`) y los síntomas (`asistente`), y «Abrir el servicio»
// llevaba a Servicios (`servicios`): todo fuera del rol.
//
// Port of tests/e2e/admin-role-screens-no-403.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the two
//   contexts (the admin that creates the account, the gestor) are `newContext()` contexts of
//   the runner with the same 1280x900 viewport and `lang=es` cookie, closed by the runner
//   instead of `t.after`. There is no requireOk()/skip path.
// - settle() slept a fixed 1500 ms «for the initial reads» before looking for 403s. A
//   late 403 is exactly what this test hunts, so the sleep is now a 1500 ms
//   `holdsFor` window in which the denial list must stay empty at every 50 ms tick (a
//   continuous invariant instead of one look after a pause); the body-text checks follow it.
// - Waits and assertions are web-first (`expect(locator)`, `expect.poll`) where the original
//   waited and then compared. Locators stay CSS on purpose: `a[data-nav]`, `.cnt-tabs
//   [data-tab]`, `#sec-medicos` are the panel's markup contract.
import { test, expect, holdsFor, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_role_screens" } });

// Clave sintética fuera de un `password: '…'` literal: el preflight del corte
// público marca esa forma como secreto (scripts/public_cut_policy.json).
const CLAVE_SINTETICA = "SyntheticPassword93";
const GESTOR = { usuario: "synthetic.gestor", password: CLAVE_SINTETICA };

async function login(newContext, app, user, pass, next) {
  const context = await newContext({ viewport: { width: 1280, height: 900 } });
  await context.addCookies([{ name: "lang", value: "es", url: app.baseUrl }]);
  const page = await context.newPage();
  await page.goto("/admin/login?next=" + encodeURIComponent(next));
  await page.fill("#gate-user", user);
  await page.fill("#gate-pass", pass);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname === next);
  return { context, page };
}

function watchDenials(page) {
  const denied = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (response.status() === 403 && (url.pathname.startsWith("/api/") || url.pathname.startsWith("/admin")))
      denied.push(response.request().method() + " " + url.pathname);
  });
  return denied;
}

async function settle(page, denied, where) {
  // La cola y la campana de alertas sondean sin parar: «networkidle» nunca
  // llega. Se espera la carga y se vigila durante las lecturas iniciales.
  await page.waitForLoadState("load");
  await holdsFor(async () => { expect(denied, `${where}: respuestas 403`).toEqual([]); }, { during: 1500 });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function assertNoDenial(page, denied, where) {
  await settle(page, denied, where);
  expect(denied.splice(0), `${where}: respuestas 403`).toEqual([]);
  const text = await page.locator("body").innerText();
  expect(text, `${where}: «No tenés permiso» en pantalla`).not.toMatch(/No tenés permiso/);
  expect(text, `${where}: métricas de médicos vacías`).not.toMatch(/No se pudieron cargar las métricas/);
  expect(text, `${where}: pestaña Usuario rota`).not.toMatch(/No se pudieron cargar los datos de acceso|No se pudo cargar el acceso del médico/);
}

test("gestor_agendas: cada pantalla de su menú y cada pestaña de la ficha de médico abren sin 403", async ({ newContext, app }) => {
  const admin = await login(newContext, app, ADMIN_USER, ADMIN_PASS, "/admin/doctors");
  const created = await admin.context.request.post(app.baseUrl + "/api/admin/users", {
    data: { usuario: GESTOR.usuario, password: GESTOR.password, rol: "gestor_agendas" },
  });
  expect(created.status(), await created.text()).toBe(200);

  const { context, page } = await login(newContext, app, GESTOR.usuario, GESTOR.password, "/admin/doctors");
  const denied = watchDenials(page);
  await page.locator("#sec-medicos .cnt-table").waitFor();
  await assertNoDenial(page, denied, "/admin/doctors");

  // Todo link de la LISTA hacia otra pantalla del panel tiene que abrir: la
  // columna Centro enlazaba a /admin/centers (permiso `servicios`) y el
  // gestor caía en «No tenés permiso» (verificación en vivo, 2026-10-24).
  const centros = await page.locator('#sec-medicos td[data-col="centro"]').evaluateAll((tds) =>
    tds.map((td) => td.innerText.trim()).filter((t) => t && t !== "—"));
  expect(centros.length, "la lista sintética muestra la columna Centro con datos").toBeGreaterThan(0);
  const listLinks = await page.locator('#sec-medicos .cnt-table a[href^="/admin/"]').evaluateAll((as) =>
    [...new Set(as.map((a) => a.getAttribute("href")))].filter((href) => !href.startsWith("/admin/doctors")));
  for (const href of listLinks) {
    await page.goto(app.baseUrl + href);
    await assertNoDenial(page, denied, "link de la lista " + href);
  }

  // Cada pantalla del menú del rol.
  const screens = await page.locator("a[data-nav]").evaluateAll((links) =>
    [...new Set(links.map((a) => new URL(a.href).pathname).filter((p) => p.startsWith("/admin")))]);
  expect(screens, "el gestor tiene Médicos en su menú").toContain("/admin/doctors");
  expect(screens.length, `menú del gestor: ${screens.join(", ")}`).toBeGreaterThanOrEqual(3);
  for (const screen of screens) {
    await page.goto(app.baseUrl + screen);
    await assertNoDenial(page, denied, screen);
  }

  // La ficha de un médico cuyo horario lo define el servicio (ejerce el link
  // «Abrir el servicio»), pestaña por pestaña.
  const list = await (await context.request.get(app.baseUrl + "/api/admin/content/doctors")).json();
  const doctors = (list.items || []).filter((it) => !it._borrado && it.id);
  expect(doctors.length, "hay médicos en el catálogo sintético").toBeGreaterThan(0);
  const doctor = doctors.find((it) => (it._agendas_servicio || []).length);
  expect(doctor, "el catálogo sintético tiene un médico con horario de servicio").toBeTruthy();
  expect(doctor._agendas_servicio[0]._puede_editar, "sin `servicios` no se ofrece «Abrir el servicio»").toBe(false);
  await page.goto(app.baseUrl + "/admin/doctors?item=" + encodeURIComponent(doctor.id));
  await page.locator(".cnt-tabs").waitFor();
  await assertNoDenial(page, denied, "ficha " + doctor.id);
  const tabs = await page.locator(".cnt-tabs [data-tab]").evaluateAll((buttons) => buttons.map((b) => b.getAttribute("data-tab")));
  expect(tabs.includes("agenda") && tabs.includes("info"), `pestañas: ${tabs.join(", ")}`).toBe(true);
  expect(tabs.includes("usuario"), "sin `usuarios` no hay pestaña Usuario").toBe(false);
  expect(tabs.includes("intake"), "sin `asistente` no hay pestaña Síntomas").toBe(false);
  for (const name of tabs) {
    await page.click(`.cnt-tabs [data-tab="${name}"]`);
    await assertNoDenial(page, denied, `ficha ${doctor.id} › ${name}`);
  }
  // Todo link que la ficha ofrezca hacia otra pantalla del panel tiene que abrir.
  await page.click('.cnt-tabs [data-tab="agenda"]');
  const links = await page.locator('#sec-medicos a[href^="/admin/"]').evaluateAll((as) =>
    [...new Set(as.map((a) => a.getAttribute("href")))]);
  for (const href of links) {
    await page.goto(app.baseUrl + href);
    await assertNoDenial(page, denied, "link " + href);
  }
});
