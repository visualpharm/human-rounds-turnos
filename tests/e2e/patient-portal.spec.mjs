// Patient login through the municipal portal (TURNOS_PATIENT_PORTAL_LOGIN),
// end to end in a real browser against the real app.
//
// The gateway is simulated the way production sees it: the server binds a
// private LAN address of this machine and trusts exactly that socket peer
// (the boundary rejects loopback), and a loopback reverse proxy plays the
// gateway (startGateway). Proves: an anonymous visitor
// sees only the portal entry (no password, sign-up, recovery or DNI photo);
// a portal visitor's first request creates the account from the CUIT; the
// first booking asks for the name and lands a real request with it saved.
//
// Port of tests/e2e/patient-portal.e2e.mjs (3 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture with the original options
//   (`host` = the machine's private IPv4, the gateway env). The loopback gateway is the
//   `serve` fixture (closed after each test, also when it fails) instead of a hand-made
//   server closed in `finally`. Contexts/pages come from the runner (`newContext`).
// - A machine without a private IPv4 address is still a missing prerequisite: it fails
//   the run in gate mode and skips only under `npm run e2e:dev` (TURNOS_E2E_ALLOW_SKIP=1),
//   like `requireOk` did; the app is then started on loopback and never used.
// - The review shots go through `saveShot` (same files under artifacts/e2e-patient-portal/,
//   also attached to the report). The 500 ms `waitForTimeout` before each shot («the nav
//   re-lays out on resize: let it settle») became two animation frames after the resize,
//   which is when the resize handlers' layout is painted; the shots are still never
//   asserted on.
// - Waits and assertions are web-first (`expect(locator)...` with the original explicit
//   timeouts). The «Registrarse»/form/password absences stay point-in-time counts on the
//   loaded page, as the original `count() === 0`.
// - The diagnostic of the last test (visible messages + `/api/` answers when «Solicitud
//   enviada» never shows) is attached to the report as `portal-booking-diagnostic.txt` and
//   the original error is re-thrown.
// - The file is chosen once, as soon as `#svc-title` is visible, exactly as in the original (no re-delivery
//   helper: the wizard re-rendering its upload step while it boots used to lose a file set on the replaced
//   input, which made this test float; cabc540d fixed that in the product and
//   booking-upload-chooser.spec.mjs pins it).
// - Locators: the markup contract stays CSS (`a.sc-provider[href=...]`, `#sc-form`,
//   `input[type=password]`, `.sc-portal-p a[href^="/center/"]`, `#wz-*`, `.wz-seg-opt`);
//   texts are found with `getByText`.
import os from "node:os";
import http from "node:http";
import path from "node:path";
import {
  test, expect, saveShot, queryRequest, holdsFor,
} from "./lib/fixtures.mjs";

const SERVICE_ID = "electrocardiograma-hospital";
const ORIGIN = "https://salud.example.invalid";
const LOGIN_URL = "https://portal.example.invalid/login";
const GATEWAY = {
  "x-user-id": "portal-e2e-ana",
  "x-user-cuit": "27-30111222-4",
  "x-user-email": "ana.portal@example.invalid",
};

function privateIPv4() {
  for (const list of Object.values(os.networkInterfaces())) {
    for (const a of list || []) {
      if (a.family !== "IPv4" || a.internal) continue;
      const [p, q] = a.address.split(".").map(Number);
      if (p === 10 || (p === 172 && q >= 16 && q <= 31) || (p === 192 && q === 168)) return a.address;
    }
  }
  return "";
}

const HOST = privateIPv4();

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_patient_portal",
    host: HOST || undefined,
    env: {
      TURNOS_PORTAL_IDENTITY_MODE: "private-gateway-v1",
      TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS: JSON.stringify([HOST]),
      TURNOS_PORTAL_IDENTITY_GATEWAY_HOST: "",
      TURNOS_PORTAL_IDENTITY_BINDINGS: "",
      TURNOS_PUBLIC_ORIGIN: ORIGIN,
      TURNOS_PATIENT_PORTAL_LOGIN: "1",
      TURNOS_PORTAL_LOGIN_URL: LOGIN_URL,
    },
  },
});

test.beforeEach(() => {
  if (HOST) return;
  const reason = "no private IPv4 address on this machine to stand in for the gateway peer";
  test.skip(process.env.TURNOS_E2E_ALLOW_SKIP === "1", reason);
  throw new Error(
    "E2E gate prerequisite missing (this FAILS the gate on purpose -- for a local " +
      `convenience run that skips instead, use \`npm run e2e:dev\`): ${reason}`,
  );
});

// The gateway: a loopback reverse proxy in front of the app. Like the real
// one it drops any client-sent identity header and writes its own, and —
// standing in for the public TLS front — presents the browser's Origin as the
// installation's public origin. Its upstream connection leaves from the
// private address, so the app sees the trusted socket peer.
async function startGateway(serve, app, identity = GATEWAY) {
  const upstream = new URL(app.baseUrl);
  return serve((req, res) => {
    const headers = { ...req.headers, host: upstream.host };
    for (const name of Object.keys(headers)) if (name.startsWith("x-user-")) delete headers[name];
    Object.assign(headers, identity);
    if (headers.origin) headers.origin = ORIGIN;
    const forward = http.request({
      host: upstream.hostname, port: upstream.port, localAddress: HOST,
      method: req.method, path: req.url, headers,
    }, (answer) => {
      res.writeHead(answer.statusCode, answer.headers);
      answer.pipe(res);
    });
    forward.on("error", () => { res.statusCode = 502; res.end(); });
    req.pipe(forward);
  });
}

// Review shots of each screen at phone and desktop width; never asserted on.
const SHOTS = new URL("../../artifacts/e2e-patient-portal/", import.meta.url).pathname;
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

async function shoot(testInfo, page, name) {
  const back = page.viewportSize();
  for (const [label, size] of [["phone", PHONE], ["desktop", DESKTOP]]) {
    await page.setViewportSize(size);
    // The nav re-lays out on resize: let it settle before the shot.
    await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    await saveShot(testInfo, page, path.join(SHOTS, `${name}-${label}.png`), { fullPage: true });
  }
  await page.setViewportSize(back);
}

test("an anonymous visitor sees only the portal entry", async ({ newContext }, testInfo) => {
  const context = await newContext({ viewport: PHONE });
  const page = await context.newPage();
  // /account goes to the portal by itself, with the page to come back to
  // (the product owner 26.10: the bare portal URL left him on the portal's home page). The
  // portal is out of reach here, so the entry route's own answer is the proof:
  // its redirect is read without following it and the page gets a stub instead
  // (following it into an unreachable host leaves the tab on chrome-error,
  // which then aborts the next goto — red in CI every run).
  let portalLocation = null;
  await page.route("**/account/portal-login?**", async (route) => {
    const answer = await route.fetch({ maxRedirects: 0 });
    portalLocation = answer.headers().location || `status ${answer.status()}`;
    await route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>portal</title>" });
  });
  await page.goto("/account", { waitUntil: "load", timeout: 15000 });
  await page.waitForURL("**/account/portal-login?next=%2Faccount", { timeout: 15000 });
  expect(portalLocation).toBe(`${LOGIN_URL}?return=${encodeURIComponent(ORIGIN + "/account")}`);
  // Back without a portal identity within two minutes: the page stays, with
  // its button, instead of bouncing between the two sites.
  await page.goto("/account", { waitUntil: "load", timeout: 15000 });
  const entry = page.locator('a.sc-provider[href="/account/portal-login?next=%2Faccount"]');
  await expect(entry).toBeVisible({ timeout: 10000 });
  await expect(entry).toHaveText("Ingresar con el portal");
  // The nav's «Ingresar» is the same entry, back to the page it is on.
  await expect(page.locator(".stznav a").filter({ hasText: /^Ingresar$/ }).first())
    .toHaveAttribute("href", "/account/portal-login?next=%2Faccount");
  await shoot(testInfo, page, "1-sin-sesion");
  expect(await page.locator("#sc-form, #sc-forgot, #sc-swap, input[type=password]").count()).toBe(0);
  expect(await page.getByText("Registrarse", { exact: true }).count()).toBe(0);
  // The native entries are closed on the server too.
  const login = await page.request.post("/api/account/login", {
    data: { identifier: "30111222", password: "whatever-123" },
  });
  expect(login.status()).toBe(403);
});

test("a person the portal vouched for but who gets no account sees the remedy, not a form", async ({ app, serve, newContext }, testInfo) => {
  const gateway = await startGateway(serve, app, { ...GATEWAY, "x-user-id": "portal-e2e-empresa", "x-user-cuit": "30-71234567-1" });
  const context = await newContext({ viewport: PHONE });
  const page = await context.newPage();
  await page.goto(gateway.url + "/account", { waitUntil: "load", timeout: 15000 });
  await expect(page.getByText("No pudimos abrir tu cuenta")).toBeVisible({ timeout: 10000 });
  expect(await page.locator("#sc-form, a.sc-provider, input[type=password]").count()).toBe(0);
  // The admission desk is a named place with its page (the product owner 23.10), not «tu centro».
  const center = page.locator('.sc-portal-p a[href^="/center/"]');
  await expect(center).toBeVisible({ timeout: 10000 });
  await expect(center).toHaveCount(1);
  expect(await page.getByText("tu centro de salud").count()).toBe(0);
  await shoot(testInfo, page, "3-rechazado");
});

test("signed in before, the portal session lapsed: any page goes through the portal by itself, once", async ({ app, serve, newContext }) => {
  // The product owner m1008550: signed in on the portal = signed in here, no «Ingresar».
  // The portal's cookie lasts 8 h and only the portal page renews it, so the
  // gateway can stop sending an identity while the person is still signed in
  // on the portal. The identity is mutable here: the gateway reads it on every
  // request, and emptying it is that lapse.
  // Its own person: Ana's CUIT bound here would refuse her in the next test.
  const identity = { "x-user-id": "portal-e2e-vuelve", "x-user-cuit": "20-30111333-2",
    "x-user-email": "vuelve.portal@example.invalid" };
  const gateway = await startGateway(serve, app, identity);
  const context = await newContext({ viewport: DESKTOP });
  const page = await context.newPage();
  await page.goto(gateway.url + "/doctors", { waitUntil: "load", timeout: 15000 });
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("st_nav") || "{}").loggedIn),
    { timeout: 10000 }).toBe(true);

  for (const name of Object.keys(identity)) delete identity[name];
  let portalLocation = null;
  await page.route("**/account/portal-login?**", async (route) => {
    const answer = await route.fetch({ maxRedirects: 0 });
    portalLocation = answer.headers().location || `status ${answer.status()}`;
    await route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>portal</title>" });
  });
  await page.goto(gateway.url + "/doctors", { waitUntil: "load", timeout: 15000 });
  await page.waitForURL("**/account/portal-login?next=%2Fdoctors", { timeout: 15000 });
  expect(portalLocation).toBe(`${LOGIN_URL}?return=${encodeURIComponent(ORIGIN + "/doctors")}`);
  // Back with no identity still: the page stays, with «Ingresar».
  await page.goto(gateway.url + "/doctors", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator(".stznav a").filter({ hasText: /^Ingresar$/ }).first())
    .toHaveAttribute("href", "/account/portal-login?next=%2Fdoctors", { timeout: 10000 });
  await expect(page).toHaveURL(/\/doctors$/);
});

test("a portal visitor's first booking creates the account, asks the name and lands a request", async ({ app, serve, newContext }, testInfo) => {
  const gateway = await startGateway(serve, app);
  const context = await newContext({ viewport: DESKTOP });
  const page = await context.newPage();
  const me = await page.request.get(gateway.url + "/api/account/me");
  expect(me.status()).toBe(200);
  const user = (await me.json()).usuario;
  expect(user.dni).toBe("30111222");
  expect(user.email).toBe(GATEWAY["x-user-email"]);

  await page.goto(gateway.url + `/service/${SERVICE_ID}/book`, { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#svc-title")).toBeVisible({ timeout: 8000 });
  await page.locator("#wz-file").setInputFiles(
    new URL("../../packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg", import.meta.url).pathname);
  const slots = page.locator(".wz-seg-opt");
  await expect(slots.first()).toBeVisible({ timeout: 15000 });
  await slots.first().click();

  const send = page.locator("#wz-send");
  await expect(send).toBeVisible({ timeout: 10000 });
  await expect(page.locator("#wz-paciente-otra-cuenta")).toHaveCount(0);
  await expect(page.locator("#wz-name-nombre")).toBeVisible({ timeout: 5000 });
  await shoot(testInfo, page, "2-confirma-primera-vez");
  // Sending without a name asks for it instead of submitting.
  await send.click();
  await expect(page.locator("#wz-name-error")).toHaveText(/\S/, { timeout: 5000 });
  await page.fill("#wz-name-nombre", "Ana");
  await page.fill("#wz-name-apellido", "Pérez");
  await page.fill("#wz-contact-tel", "2254 401234");
  const answers = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/")) answers.push(`${r.request().method()} ${new URL(r.url()).pathname} ${r.status()}`);
  });
  await send.click();
  try {
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada", { timeout: 15000 });
  } catch (error) {
    const shown = await page.evaluate(() => ["wz-msg", "wz-contact-error", "wz-name-error"]
      .map((id) => `${id}=${document.getElementById(id)?.textContent || ""}`).join(" | "));
    await testInfo.attach("portal-booking-diagnostic.txt", {
      body: `${shown}\n${answers.join("\n")}`, contentType: "text/plain",
    });
    throw error;
  }

  const after = await (await page.request.get(gateway.url + "/api/account/me")).json();
  expect(after.usuario.nombre).toBe("Ana");
  expect(after.usuario.apellido).toBe("Pérez");
  expect(after.usuario.contacto).toBeTruthy();
  const req = await queryRequest(app.dbName, "30111222");
  expect(req.found, "expected a real booking_requests row after submit").toBe(true);
  expect(req.servicio_id).toBe(SERVICE_ID);

  // «Solicitud enviada» (the product owner 26.10): the name typed on Confirmá is the
  // patient's on the receipt, the slot goes to the calendar from its row,
  // the coverage card carries the page's one primary button, and nothing in
  // the top bar claims this page («Centros» did, through the breadcrumb).
  const coverage = page.locator(".sc-postcoverage form[data-coverage-form]");
  await expect(coverage).toBeVisible({ timeout: 10000 });
  await shoot(testInfo, page, "4-solicitud-enviada");
  const pacienteRow = page.locator(".wz-success-row").filter({ hasText: "Paciente" });
  await expect(pacienteRow.locator(".wz-success-val")).toHaveText("Ana Pérez");
  const fechaRow = page.locator(".wz-success-row").filter({ hasText: "Fecha y hora" });
  const ics = fechaRow.locator('a[href$=".ics"]');
  await expect(ics).toHaveText("Agregar al calendario");
  const icsHref = await ics.getAttribute("href");
  expect(icsHref).toMatch(/^\/api\/appointment\/\d+\.ics$/);
  const icsAnswer = await page.request.get(gateway.url + icsHref);
  expect(icsAnswer.status()).toBe(200);
  expect(await icsAnswer.text()).toContain("BEGIN:VEVENT");
  await expect(page.locator("[data-coverage-later], [data-coverage-none]")).toHaveCount(0);
  await expect(page.locator('a[href="/my-requests"].wz-cta-lg')).toHaveCount(0);
  await expect(coverage.locator("button[type=submit]")).toHaveClass(/sc-cta/);
  await expect(page.locator("#stznav")).toHaveAttribute("data-section", "");
  const gap = await page.evaluate(() => document.querySelector(".wz-success").getBoundingClientRect().top -
    document.getElementById("svc-title").getBoundingClientRect().bottom);
  expect(gap, "title → status gap (was ~100 px)").toBeLessThanOrEqual(40);

  // «Mis solicitudes» (the product owner 26.10): a cancelled request still says which
  // appointment it was for, and with no future appointment left there is
  // nothing to add to a calendar.
  // The pending request holds its slot: the whole-calendar button is offered.
  await page.goto(gateway.url + "/account#turnos", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator(".ms-feed-link")).toBeVisible({ timeout: 10000 });
  await page.locator(`.req[data-idx] [data-cancel]`).first().click();
  await page.locator(".mt-confirm [data-si]").click();
  const past = page.locator(".req-muted").first();
  await expect(past).toBeVisible({ timeout: 10000 });
  await expect(past.locator(".req-when")).toHaveText(/\d/);
  await holdsFor(async () => expect(await page.locator(".ms-feed-link").count()).toBe(0), { during: 1500 });
  await shoot(testInfo, page, "5-mis-solicitudes-cancelada");
});

// An empty name is never drawn as a dash. «Para —» and a «Paciente —» row came
// from the account being re-read (the name typed here is saved and the account
// comes back as a new object) while the chosen patient stayed the old, nameless
// copy of it.
test("a nameless portal account never shows «Para —»: «Para vos» first, the typed name after", async ({ app, serve, newContext }, testInfo) => {
  const gateway = await startGateway(serve, app, {
    "x-user-id": "portal-e2e-bea", "x-user-cuit": "27-30111223-3", "x-user-email": "bea.portal@example.invalid",
  });
  const context = await newContext({ viewport: PHONE });
  const page = await context.newPage();
  await page.goto(gateway.url + `/service/${SERVICE_ID}/book`, { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#svc-title")).toBeVisible({ timeout: 8000 });
  await page.locator("#wz-file").setInputFiles(
    new URL("../../packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg", import.meta.url).pathname);
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await page.locator(".wz-seg-opt").first().click();

  const para = page.locator(".wz-pac > span").first();
  await expect(page.locator("#wz-name-nombre")).toBeVisible({ timeout: 10000 });
  await expect(para).toHaveText("Para vos");

  await shoot(testInfo, page, "4-confirma-sin-nombre");

  // The name is saved with the request, and the account comes back as a new object.
  await page.fill("#wz-name-nombre", "Bea");
  await page.fill("#wz-name-apellido", "Gómez");
  await page.fill("#wz-contact-tel", "2254 401234");
  // Leaving the phone saves the name typed above; the screen drawn again from the
  // saved account says the name, not a dash.
  const saved = page.waitForResponse((r) => r.url().includes("/api/account/update") && r.ok());
  await page.locator("#wz-contact-tel").blur();
  await saved;
  await page.locator("#wz-change-time").click();
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#wz-send")).toBeVisible({ timeout: 10000 });
  await expect(para).toHaveText("Para Bea Gómez");
  await page.locator("#wz-send").click();
  await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada", { timeout: 15000 });
  const paciente = page.locator(".wz-success-row", { hasText: "Paciente" }).locator(".wz-success-val");
  await expect(paciente).toHaveText("Bea Gómez");
});

test("a nameless portal account on a service with an age range still reads «Para vos», never «Para —»", async ({ app, serve, newContext }, testInfo) => {
  const gateway = await startGateway(serve, app, {
    "x-user-id": "portal-e2e-eli", "x-user-cuit": "27-30111224-1", "x-user-email": "eli.portal@example.invalid",
  });
  const context = await newContext({ viewport: PHONE });
  const page = await context.newPage();
  await page.goto(gateway.url + "/service/pediatria-riverside-clinic/book", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#wz-send")).toBeVisible({ timeout: 10000 });
  await shoot(testInfo, page, "5-confirma-con-edad");
  expect(await page.locator(".wz-pac").innerText()).not.toContain("—");
});
