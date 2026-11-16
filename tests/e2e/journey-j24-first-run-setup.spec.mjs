// tests/e2e/journey-j24-first-run-setup.spec.mjs -- J24 ("Someone deploys the app for a new hospital:
// the first-run wizard takes them from an empty installation to a signed-in administrator, a town
// of their own and a site in the languages they chose -- and the wizard is gone afterwards")
// cross-role journey acceptance.
//
// The only role is the deployer. The installation is genuinely unconfigured: no operator in the
// database, no environment admin (`appOptions.fresh`). The town pack the wizard scaffolds is written
// to a throwaway directory (TURNOS_PRIVATE_PACKS_DIR), never to the checkout.
//
// Terminal outcome: after «Terminar instalación» the administrator they typed can sign in to the panel,
// the public site speaks the town they named in the languages they ticked, and every address of the
// wizard answers the same 404 as any unknown page -- the wizard cannot be run again to take over.
//
// Steps:
//   1. The welcome step, then the location step; a request with no country or town is refused
//   2. A town with no data pack: the wizard offers to create one, named after the town
//   3. Languages: none ticked is refused; Español and English ticked, Español by default
//   4. The administrator: each way of getting it wrong is refused with its own sentence; then the real one
//   5. Backups skipped, API key left empty, «Terminar instalación»: the done page names the account
//   6. The wizard is gone: every step is a 404, GET and POST
//   7. The administrator signs in to the panel with what they typed
//   8. The public site carries the town and offers both languages
// and, in its own test, a town that already has a data pack in the app is matched instead of created.
//
// External services: none (no key typed, geo-IP lookup off).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect } from "./lib/journey.mjs";

// One directory per process (the runner and each worker load this file): the worker's app writes the town pack
// there, and it is removed when the process ends.
const PACKS_DIR = path.join(os.tmpdir(), `sana-e2e-j24-packs-${process.pid}`);
fs.mkdirSync(PACKS_DIR, { recursive: true });
process.on("exit", () => fs.rmSync(PACKS_DIR, { recursive: true, force: true }));

// A throwaway sign-in phrase for an administrator that only exists in a database this harness drops.
const ADMIN = { username: "admin-ejemplo", phrase: "Vamos-2026-ok", email: "admin.ejemplo@example.invalid" };
// The province is typed the way a person writes it; its data pack is called "Buenos Aires (provincia)".
const TOWN = { country: "Argentina", province: "Buenos Aires", name: "Villa Ejemplo" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j24", fresh: true, env: { TURNOS_PRIVATE_PACKS_DIR: PACKS_DIR } },
});

/** Posts a wizard form straight to the server (what a client that skips the browser's own checks would send). */
async function postStep(context, step, form) {
  const response = await context.request.post(`/setup/${step}`, { form, maxRedirects: 0 });
  return { status: response.status(), body: await response.text() };
}
const errorOf = (body) => (/<p class="setup-error"[^>]*>([^<]*)<\/p>/.exec(body) || [])[1] || "";
const decode = (text) => text.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

test("J24 — a new installation goes from empty to a signed-in administrator, and the wizard disappears", async ({ app, role }) => {
  test.setTimeout(180_000);
  const deployer = await role();
  const { page, context } = deployer;

  await test.step("1. welcome and location; a request with no country or town is refused", async () => {
    await page.goto("/setup", { waitUntil: "load" });
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Vamos a configurar tu instalación");
    await expect(page.locator(".lede")).toContainText("sólo aparece una vez");
    await page.getByRole("link", { name: /Empezar/ }).click();
    await expect(page.locator(".setup-progress")).toHaveText(/Paso 1 de 6 · Ubicación/);

    const refused = await postStep(context, "location", { country: "", province: "", town: "" });
    expect(refused.status).toBe(200);
    expect(errorOf(refused.body)).toBe("Completá al menos el país y la localidad.");

    await page.locator("#su-country").fill(TOWN.country);
    await page.locator("#su-province").fill(TOWN.province);
    await page.locator("#su-town").fill(TOWN.name);
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.locator(".setup-progress")).toHaveText(/Paso 2 de 6 · Paquete de datos/);
  });

  await test.step("2. no data pack for this town: the wizard creates one, named after the town", async () => {
    await expect(page.locator(".lede")).toContainText(`Todavía no hay un paquete de datos para ${TOWN.name}`);
    await expect(page.locator("#su-packname"), "the name proposed is the town").toHaveValue(TOWN.name);
    await expect(page.locator(".setup-note")).toContainText("Cadena de paquetes: global, ar, ar-b, <villa-ejemplo>");
    // A blank name is not an error: the town's own name is used.
    const blank = await postStep(context, "packs", { mode: "scaffold", pack_name: "   " });
    expect(blank.status, "a blank name falls back to the town").toBe(303);
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.locator(".setup-progress")).toHaveText(/Paso 3 de 6 · Idioma/);
  });

  await test.step("3. languages: none is refused; Español and English, Español by default", async () => {
    const refused = await postStep(context, "language", { lang_default: "es" });
    expect(errorOf(refused.body)).toBe("Elegí al menos un idioma.");
    await expect(page.locator('input[name="languages"][value="es"]'), "Español is ticked to begin with").toBeChecked();
    await page.locator('input[name="languages"][value="en"]').check();
    await expect(page.locator("#su-langdefault")).toHaveValue("es");
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.locator(".setup-progress")).toHaveText(/Paso 4 de 6 · Cuenta de administración/);
  });

  await test.step("4. the administrator: every mistake has its own sentence", async () => {
    const good = { username: ADMIN.username, password: ADMIN.phrase, password2: ADMIN.phrase, email: ADMIN.email, totp: "later" };
    const cases = [
      [{ ...good, username: "a b" }, "Escribí un nombre de usuario de al menos 2 caracteres, sin espacios."],
      [{ ...good, password: "corta", password2: "corta" }, "La contraseña tiene que tener al menos 8 caracteres."],
      [{ ...good, password2: ADMIN.phrase + "x" }, "Las contraseñas no coinciden."],
      [{ ...good, email: "no-es-un-email" }, "Ingresá un email válido."],
    ];
    for (const [form, sentence] of cases) {
      const refused = await postStep(context, "admin", form);
      expect(refused.status).toBe(200);
      expect(decode(errorOf(refused.body)), sentence).toBe(sentence);
    }
    await page.locator("#su-user").fill(ADMIN.username);
    await page.locator("#su-pass").fill(ADMIN.phrase);
    await page.locator("#su-pass2").fill(ADMIN.phrase);
    await page.locator("#su-email").fill(ADMIN.email);
    await expect(page.locator('input[name="totp"][value="later"]'), "two-step verification is set up later by default").toBeChecked();
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.locator(".setup-progress")).toHaveText(/Paso 5 de 6 · Copias de seguridad/);
  });

  await test.step("5. backups skipped, API key left empty: the done page names the account", async () => {
    await page.getByRole("button", { name: "Sin copias externas por ahora" }).click();
    await expect(page.locator(".setup-progress")).toHaveText(/Paso 6 de 6 · Claves de API/);
    await page.getByRole("button", { name: "Terminar instalación" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Listo");
    await expect(page.locator(".lede")).toContainText(`Instalación lista. Ya podés entrar al panel con el usuario «${ADMIN.username}».`);
    await expect(page.getByRole("link", { name: /Ir al panel de administración/ })).toHaveAttribute("href", "/admin");
    expect(fs.existsSync(path.join(PACKS_DIR, "villa-ejemplo", "pack.json")), "the town pack was written to the pack directory").toBe(true);
  });

  // The POSTs come first and stand on their own: a wizard that closed its pages but still took a form
  // would let anyone who finds the URL take the installation over, and the GET loop below would never say.
  await test.step("6a. the wizard cannot be run again: every POST answers the 404 of an unknown page", async () => {
    const forms = {
      apikeys: {},
      admin: { username: "intruso", password: "Otra-clave-99", password2: "Otra-clave-99", totp: "later" },
      location: { country: "Chile", province: "", town: "Intrusa" },
      language: { languages: "en", lang_default: "en" },
    };
    for (const [step, form] of Object.entries(forms)) {
      const answer = await postStep(context, step, form);
      expect([answer.status, answer.body], `POST /setup/${step} is a 404, not a wizard step`).toEqual([404, "<!doctype html><title>404</title><p>No encontrado.</p>"]);
    }
  });

  await test.step("6b. the wizard is gone: every page answers the 404 of an unknown page", async () => {
    for (const step of ["", "location", "packs", "language", "admin", "backups", "apikeys", "anything-else"]) {
      const get = await context.request.get(`/setup${step ? "/" + step : ""}`, { maxRedirects: 0 });
      expect([get.status(), await get.text()], `GET /setup/${step}`).toEqual([404, "<!doctype html><title>404</title><p>No encontrado.</p>"]);
    }
  });

  await test.step("7. the administrator signs in to the panel with what they typed", async () => {
    await page.goto("/admin/login", { waitUntil: "load" });
    await page.fill("#gate-user", ADMIN.username);
    await page.fill("#gate-pass", ADMIN.phrase);
    await page.click("#gate-btn");
    await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login");
    const me = await context.request.get("/api/admin/whoami");
    expect(me.status(), "the panel knows who is signed in").toBe(200);
    expect(JSON.stringify(await me.json())).toContain(ADMIN.username);
    // A wrong phrase for the same account is refused.
    const other = await role();
    await other.page.goto("/admin/login", { waitUntil: "load" });
    await other.page.fill("#gate-user", ADMIN.username);
    await other.page.fill("#gate-pass", ADMIN.phrase + "x");
    await other.page.click("#gate-btn");
    await expect(other.page).toHaveURL(/\/admin\/login/);
  });

  await test.step("8. the public site carries the town and offers both languages", async () => {
    const site = await role();
    await site.page.goto("/", { waitUntil: "load" });
    await expect(site.page.locator("body")).toContainText(TOWN.name, { useInnerText: true });
    const switcher = site.page.locator("select.stznav-language");
    await expect(switcher, "the visitor can choose a language").toBeVisible();
    expect(await switcher.locator("option").evaluateAll((o) => o.map((x) => x.value).sort()), "exactly the two languages the deployer ticked").toEqual(["en", "es"]);
    await expect(switcher, "…and the one chosen by default is selected").toHaveValue("es");
    await switcher.selectOption("en");
    await expect(site.page.locator("html")).toHaveAttribute("lang", "en");
    expect(site.errors).toEqual([]);
  });

  expect(deployer.errors, "no uncaught page errors").toEqual([]);
});

test("J24 — a town that already has a data pack is matched, not created", async ({ launchApp, role }) => {
  test.setTimeout(120_000);
  const second = await launchApp({
    dbName: "sana_turnos_test_e2e_journey_j24b", fresh: true,
    env: { TURNOS_PRIVATE_PACKS_DIR: PACKS_DIR },
  });
  const person = await role();
  const { page } = person;
  const packsBefore = fs.readdirSync(PACKS_DIR).sort();

  await page.goto(`${second.baseUrl}/setup/location`, { waitUntil: "load" });
  await page.locator("#su-country").fill("Argentina");
  await page.locator("#su-province").fill("Buenos Aires (provincia)");
  await page.locator("#su-town").fill("Sampleton");
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.locator(".lede")).toContainText("Encontramos un paquete de datos para Sampleton: «example-town».");
  await expect(page.locator(".setup-note")).toContainText("Cadena de paquetes: global, ar, ar-b, example-town");
  await expect(page.locator("#su-packname"), "no name is asked for: nothing is created").toHaveCount(0);
  await page.getByRole("button", { name: "Continuar" }).click();

  await expect(page.locator(".setup-progress")).toHaveText(/Paso 3 de 6 · Idioma/);
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.locator("#su-user").fill("admin-sampleton");
  await page.locator("#su-pass").fill(ADMIN.phrase);
  await page.locator("#su-pass2").fill(ADMIN.phrase);
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Sin copias externas por ahora" }).click();
  await page.getByRole("button", { name: "Terminar instalación" }).click();
  await expect(page.locator(".lede")).toContainText("Instalación lista. Ya podés entrar al panel con el usuario «admin-sampleton».");

  expect(fs.readdirSync(PACKS_DIR).sort(), "a matched pack writes nothing to the pack directory").toEqual(packsBefore);
  await page.goto(`${second.baseUrl}/`, { waitUntil: "load" });
  await expect(page.locator("body"), "the site is the matched town's").toContainText("Sampleton", { useInnerText: true });
  expect(person.errors).toEqual([]);
});
