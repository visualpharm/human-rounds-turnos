// «Confirmá» — el teléfono del turno y el de la cuenta (regla del responsable del producto, hub,
// 2026-10-23: el teléfono de la cuenta no se pisa sin consentimiento del
// paciente; elegido el 2026-10-24: tilde «Guardar también en mi cuenta»).
// Un navegador real contra el servidor real: se escribe un número en el paso,
// se envía, y se lee lo que quedó en la solicitud y en la cuenta.
//
// Differences from the node:test original (tests/e2e/confirma-telefono.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), same setupHarness
//     options: account); the 390x900 viewport is test.use({ viewport }) and the patient session
//     cookie is added to the runner's context in a beforeEach (the original built a context per test).
//     The original opened an extra page per scenario; each test uses the runner's `page`.
//   - The four tests hand state to each other through the account (the first expects the fixture's
//     phone, the later ones change it), so the file is serial, as the original ran them.
//   - Waits are web-first: the «Solicitud enviada» title wait (waitForFunction) is
//     expect(#svc-title).toHaveText with the original 15 s timeout; the other waitFor({state:"visible"})
//     are toBeVisible with the original timeouts.
//   - The last scenario delayed every account save by a 1200 ms timer and then waited 150 ms before
//     looking at what had been sent. The delay is the subject (a save left half-done when «Enviar»
//     is pressed), so it is now a held route: the first save stays pending behind a promise gate
//     (holdGate) until the test releases it right after pressing «Enviar», which makes that state
//     deterministic instead of timing luck; "the blur started exactly one save" is checked with
//     holdsFor over the original 150 ms window once the held save has arrived. Later saves go through
//     the open gate at once.
//   - Snapshots that guard a race stay snapshots (the account phone right after leaving the field, the
//     missing tilde of an empty account).
//   - Locators: CSS ids/classes and data-contact-edit stay (#wz-*, .wz-*): they are the wizard's markup
//     contract and the tilde's computed font size / box are measured on it.
import { test, expect, queryRequest, patientCookie, holdGate, holdsFor } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

const SERVICE_ID = "medicina-general-riverside-clinic";
const CUENTA = "+5492254100002"; // fixture.py --account
const ESCRITO = "2254 40-1234";
const ESCRITO_DIGITOS = "2254401234";
const HIJO = { dni: "40111500", nombre: "Lola", apellido: "Reserva", nacimiento: "1985-03-02", sexo: "F",
  vinculo: "otro", declaracion_jurada: true };
const OTRO = { ...HIJO, dni: "40111501", nombre: "Mateo" };
const TERCERO = { ...HIJO, dni: "40111502", nombre: "Bruno" };
const CUARTO = { ...HIJO, dni: "40111503", nombre: "Sofia" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_confirma_telefono", account: true },
  viewport: { width: 390, height: 900 },
});
test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ context, app }) => {
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
});

async function abrirConfirma(page, app, { paciente } = {}) {
  await page.goto(app.baseUrl + `/service/${SERVICE_ID}/book`, { waitUntil: "load", timeout: 20_000 });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15_000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#wz-send")).toBeVisible({ timeout: 10_000 });
  if (paciente) {
    await page.locator("#wz-change-paciente").click();
    await page.locator(".wz-center-choice", { hasText: paciente }).click();
    await expect(page.locator(".wz-pac", { hasText: paciente })).toBeVisible({ timeout: 5_000 });
  }
  return page;
}

async function escribirTelefono(page, numero) {
  const campo = page.locator("#wz-contact-tel");
  if (!(await campo.count())) await page.locator('[data-contact-edit="tel"]').click();
  await campo.fill(numero);
  await campo.blur();
}

async function esperarEnviada(page) {
  await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada", { timeout: 15_000 });
}

async function enviar(page) {
  await page.locator("#wz-send").click();
  await esperarEnviada(page);
}

const cuenta = async (context, app) => (await (await context.request.get(app.baseUrl + "/api/account/me")).json()).usuario;

// Responsable del producto, 2026-10-27: Confirmá es una página blanca con un solo recuadro (el turno),
// sin sello «Cuenta verificada», y WhatsApp + email en una línea con íconos.
test("Confirmá: página blanca, un solo recuadro y el contacto en una línea con íconos", async ({ page, app }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await abrirConfirma(page, app);
  const vista = await page.evaluate(() => {
    const css = (sel) => getComputedStyle(document.querySelector(sel));
    const panel = getComputedStyle(document.documentElement).getPropertyValue("--panel").trim();
    const probe = document.createElement("i");
    probe.style.color = panel;
    document.body.appendChild(probe);
    const panelRgb = getComputedStyle(probe).color;
    probe.remove();
    return {
      fondo: css("body").backgroundColor, panelRgb,
      tarjeta: css(".wz-card--confirm").borderTopWidth,
      tarjetaFondo: css(".wz-card--confirm").backgroundColor,
      avisos: css(".wz-avisos").borderTopWidth,
    };
  });
  expect(vista.fondo, "la página de Confirmá es del color del panel (blanca)").toBe(vista.panelRgb);
  expect(vista.tarjeta, "el paso no va dentro de otra tarjeta").toBe("0px");
  expect(vista.tarjetaFondo).toBe("rgba(0, 0, 0, 0)");
  expect(vista.avisos, "«¿Cómo te avisamos?» no tiene recuadro").toBe("0px");
  await expect(page.locator(".wz-chip")).toHaveCount(0);
  await expect(page.locator("#wz-confirm")).not.toContainText("Cuenta verificada");
  const tel = page.getByRole("img", { name: "WhatsApp" });
  const mail = page.getByRole("img", { name: "Email" });
  await expect(tel).toBeVisible();
  await expect(mail).toBeVisible();
  const [a, b] = [await stableBox(tel, "WhatsApp icon"), await stableBox(mail, "email icon")];
  expect(Math.abs(a.y - b.y), "WhatsApp y email en la misma línea").toBeLessThan(4);
  // Al volver a otro paso la página recupera su fondo.
  await page.locator(".wz-turno .wz-change-link").first().click();
  await expect.poll(() => page.evaluate(() => document.body.classList.contains("wz-pagina-blanca"))).toBe(false);
});

// «Agregar al calendario» vive solo después de enviar (plan 2026-11-10): en Confirmá
// todavía no hay nada que agendar, y el bloque empujaba «Pedir el turno» fuera de la
// primera pantalla del teléfono. La pantalla de éxito lo lleva pegado a la fecha y baja
// el archivo de la solicitud guardada, sin ningún dato personal del contacto.
test("«Agregar al calendario» no está en Confirmá y sí en la pantalla de éxito", async ({ page, context, app }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const alta = await context.request.post(app.baseUrl + "/api/account/dependents", { data: CUARTO });
  expect(alta.status(), await alta.text()).toBe(200);
  await abrirConfirma(page, app, { paciente: CUARTO.nombre });
  await expect(page.locator("#wz-cal")).toHaveCount(0);
  await expect(page.locator("#wz-confirm")).not.toContainText("Agregar al calendario");
  await enviar(page);
  const fila = page.locator(".wz-success-row").filter({ hasText: "Fecha y hora" });
  const enlace = fila.locator('a[href$=".ics"]');
  await expect(enlace).toHaveText("Agregar al calendario");
  expect(await enlace.getAttribute("href")).toMatch(/^\/api\/appointment\/\d+\.ics$/);
  const archivo = await page.request.get(app.baseUrl + (await enlace.getAttribute("href")));
  expect(archivo.status(), "con la sesión de la cuenta el archivo se baja").toBe(200);
  const texto = await archivo.text();
  expect(texto).toContain("BEGIN:VEVENT");
  expect(texto).not.toContain(CUENTA);
});

// En un teléfono de 390 x 844 «Pedir el turno» se ve en la primera pantalla de
// Confirmá, sin desplazarse (plan 2026-11-10).
test("a 390 x 844 «Pedir el turno» entra en la primera pantalla", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await abrirConfirma(page, app);
  await page.evaluate(() => window.scrollTo(0, 0));
  const boton = await stableBox(page.locator("#wz-send"), "send button");
  // La demo pone encima su franja de 88 px: el botón entra también con ella.
  expect(boton.y + boton.height, "el botón termina dentro de 844 px menos la franja de la demo").toBeLessThanOrEqual(844 - 88);
  await expect(page.locator(".svc-crumb")).toBeHidden();
  const titulo = await page.locator("#svc-title").evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(titulo, "en el teléfono el título del asistente usa su tamaño de teléfono").toBe(28);
});

test("un número escrito en Confirmá va al turno y la cuenta conserva el suyo", async ({ page, context, app }) => {
  await abrirConfirma(page, app);
  await escribirTelefono(page, ESCRITO);
  const tilde = page.locator("#wz-guardar-tel");
  await expect(tilde).toBeVisible({ timeout: 5_000 });
  expect(await tilde.isChecked(), "sin consentimiento la tilde arranca sin marcar").toBe(false);
  const etiqueta = page.locator("label", { has: tilde });
  await expect(etiqueta).toHaveText(/Guardar también en mi cuenta/);
  const letra = await etiqueta.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(letra, `la tilde se lee a ${letra}px (mínimo 16)`).toBeGreaterThanOrEqual(16);
  const fila = await stableBox(etiqueta, "fila de la tilde");
  expect(fila.height, `la fila de la tilde mide ${fila.height}px (mínimo 44)`).toBeGreaterThanOrEqual(44);
  // Salir del campo ya NO escribe en la cuenta.
  expect((await cuenta(context, app)).contacto, "al salir del campo la cuenta no se toca").toBe(CUENTA);
  // Tildar y destildar antes de enviar: el consentimiento es el estado final.
  await tilde.check();
  await tilde.uncheck();
  await enviar(page);
  expect((await cuenta(context, app)).contacto, "enviado sin tilde, la cuenta sigue igual").toBe(CUENTA);
  const req = await queryRequest(app.dbName, app.fixture.account_dni);
  expect(String(req.contacto).replace(/\D/g, "").endsWith(ESCRITO_DIGITOS), "la solicitud lleva el número escrito").toBe(true);
});

test("con la tilde, el número del turno también queda en la cuenta de quien saca el turno (a nombre de otra persona)", async ({ page, context, app }) => {
  const alta = await context.request.post(app.baseUrl + "/api/account/dependents", { data: HIJO });
  expect(alta.status(), await alta.text()).toBe(200);
  await abrirConfirma(page, app, { paciente: HIJO.nombre });
  await escribirTelefono(page, ESCRITO);
  await page.locator("#wz-guardar-tel").check();
  await enviar(page);
  const titular = await cuenta(context, app);
  expect(String(titular.contacto).replace(/\D/g, "").endsWith(ESCRITO_DIGITOS), "la cuenta del titular recibe el número").toBe(true);
  const req = await queryRequest(app.dbName, HIJO.dni);
  expect(req.found).toBe(true);
  expect(String(req.contacto).replace(/\D/g, "").endsWith(ESCRITO_DIGITOS)).toBe(true);
});

test("una cuenta sin teléfono lo completa con el escrito: no hay tilde que marcar", async ({ page, context, app }) => {
  const actual = await cuenta(context, app);
  const vacia = await context.request.post(app.baseUrl + "/api/account/update", {
    data: { nombre: actual.nombre, apellido: actual.apellido, nacimiento: actual.nacimiento || "",
      sexo: actual.sexo || "", contacto: "", email: actual.email } });
  expect(vacia.status(), await vacia.text()).toBe(200);
  expect((await cuenta(context, app)).contacto, "la cuenta quedó sin teléfono").toBeFalsy();
  const alta = await context.request.post(app.baseUrl + "/api/account/dependents", { data: OTRO });
  expect(alta.status(), await alta.text()).toBe(200);
  await abrirConfirma(page, app, { paciente: OTRO.nombre });
  await escribirTelefono(page, "2254 55-0713");
  expect(await page.locator("#wz-guardar-tel").count(), "completar una cuenta vacía no pide permiso").toBe(0);
  await enviar(page);
  expect(String((await cuenta(context, app)).contacto).replace(/\D/g, "").endsWith("2254550713")).toBe(true);
});

test("«Enviar» con la tilde puesta espera el guardado a medias del email y no pierde el teléfono", async ({ page, context, app, cleanup }) => {
  const antes = await cuenta(context, app);
  const alta = await context.request.post(app.baseUrl + "/api/account/dependents", { data: TERCERO });
  expect(alta.status(), await alta.text()).toBe(200);
  await abrirConfirma(page, app, { paciente: TERCERO.nombre });
  // Cada guardado de la cuenta tarda: el del email queda a medias cuando se toca «Enviar».
  const gate = holdGate(cleanup);
  const guardados = [];
  await page.route("**/api/account/update", async (route) => {
    guardados.push(JSON.parse(route.request().postData() || "{}"));
    gate.arrive();
    await gate.held;
    await route.continue();
  });
  await escribirTelefono(page, "2254 66-7788");
  await page.locator("#wz-guardar-tel").check();
  await page.locator('[data-contact-edit="email"]').click();
  await page.locator("#wz-contact-email").fill("nuevo-contacto@example.invalid");
  await page.locator("#wz-contact-email").blur();
  await gate.arrived;
  await holdsFor(async () => {
    expect(guardados.length, "salir del campo del email empezó un guardado").toBe(1);
  }, { during: 150 });
  expect(guardados[0].contacto.replace(/\D/g, ""), "salir de un campo no escribe el teléfono tildado: la tilde vale al enviar")
    .toBe(antes.contacto.replace(/\D/g, ""));
  // «Enviar» se toca con el guardado del email todavía a medias; recién después se lo deja terminar.
  await page.locator("#wz-send").click();
  gate.release();
  await esperarEnviada(page);
  const despues = await cuenta(context, app);
  expect(despues.email).toBe("nuevo-contacto@example.invalid");
  expect(String(despues.contacto).replace(/\D/g, "").endsWith("2254667788"), "la tilde se cumplió al enviar").toBe(true);
  const req = await queryRequest(app.dbName, TERCERO.dni);
  expect(String(req.contacto).replace(/\D/g, "").endsWith("2254667788")).toBe(true);
});
