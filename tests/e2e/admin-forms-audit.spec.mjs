// TASK-3 form audit: the admin writes the 2026-10-25 inventory found without a
// «save → cold reload → the value is on the page» test (docs/architecture/admin-forms-audit.md).
// Every write goes through the real control to ASGI and an isolated PostgreSQL; the proof
// is what the reloaded page shows, the API is only read to name what the page must show.
// Mail goes to a loopback Mailgun receiver (lib/journey.mjs startSink), nothing leaves.
import { expect, loginAdmin, write, totpCode, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";
import { test } from "./lib/journey.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_forms_audit" },
  journey: { sink: true },
  viewport: { width: 1280, height: 900 },
});

const KNOWLEDGE_API = "/api/admin/content/knowledge";
const isPreferencesPut = (response) => new URL(response.url()).pathname === "/api/admin/preferences"
  && response.request().method() === "PUT";

async function createStaff(page, usuario, extra = {}) {
  const res = await page.request.post("/api/admin/users", {
    data: { usuario, password: "SynthPassword93", rol: "admisionista", email: usuario + "@example.invalid", ...extra },
  });
  expect(res.status(), await res.text()).toBe(200);
}

async function staffLogin(context, usuario, password) {
  return (await context.request.post("/api/admin/login", { data: { usuario, password } })).status();
}

async function saveUser(page) {
  await write(page, new URL(page.url()).searchParams.get("user")
    ? "/api/admin/users/" + new URL(page.url()).searchParams.get("user") : "/api/admin/users",
  () => page.locator('button[type="submit"][form="usr-form"]').click());
  await page.waitForURL((url) => url.pathname === "/admin/users" && !url.search);
}

test("Users: centers, active, forced 2FA, removed 2FA and an admin-set password reopen as saved; Deactivate shows Inactivo", async ({ adminPage: page, newContext }) => {
  const usuario = "audit.desk";
  await createStaff(page, usuario);
  const centers = await (await page.request.get("/api/admin/content/centers")).json();
  const slug = (centers.items || centers)[0].slug || (centers.items || centers)[0].id;

  // Centers + forced 2FA, one save.
  await page.goto("/admin/users?user=" + usuario);
  await expect(page.locator("#usr-form")).toBeVisible();
  await page.locator(`.usr-centro[value="${slug}"]`).check();
  await page.locator("#usr-force-2fa").check();
  await saveUser(page);
  await page.goto("/admin/users?user=" + usuario);
  await expect(page.locator(`.usr-centro[value="${slug}"]`)).toBeChecked();
  await expect(page.locator("#usr-force-2fa")).toBeChecked();
  await page.locator("#usr-force-2fa").uncheck();
  await saveUser(page);
  await page.goto("/admin/users?user=" + usuario);
  await expect(page.locator("#usr-force-2fa")).not.toBeChecked();

  // Admin-set password: the old one stops working, the new one signs in through the form.
  await page.locator("#usr-pass").fill("AdminChosen8812");
  await saveUser(page);
  const probe = await newContext();
  expect(await staffLogin(probe, usuario, "SynthPassword93")).toBe(401);
  const staffPage = await (await newContext()).newPage();
  await staffPage.goto("/admin/login?next=/admin/account");
  await staffPage.fill("#gate-user", usuario); await staffPage.fill("#gate-pass", "AdminChosen8812");
  await staffPage.click("#gate-btn");
  await staffPage.waitForURL((url) => url.pathname === "/admin/account");

  // The account turns 2FA on itself; the admin removes it from the user form.
  const setup = await (await staffPage.request.post("/api/admin/account/2fa/start", { data: {} })).json();
  expect((await staffPage.request.post("/api/admin/account/2fa/confirm", {
    data: { codigo: await totpCode(setup.secret), setup_token: setup.setup_token },
  })).status()).toBe(200);
  await page.goto("/admin/users?user=" + usuario);
  await expect(page.locator("#usr-totp-quitar")).toBeVisible();
  await page.click("#usr-totp-quitar");
  await saveUser(page);
  await page.goto("/admin/users?user=" + usuario);
  await expect(page.locator("#usr-form")).toBeVisible();
  await expect(page.locator("#usr-totp-quitar")).toHaveCount(0);

  // Active off, then on.
  for (const active of [false, true]) {
    await page.locator("#usr-activo").setChecked(active);
    await saveUser(page);
    await page.goto("/admin/users?user=" + usuario);
    await expect(page.locator("#usr-activo")).toBeChecked({ checked: active });
  }

  // Deactivate from the form: the list row says Inactivo after a reload.
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/users/" + usuario, () => page.click("#usr-desactivar"));
  await page.goto("/admin/users");
  await page.reload();
  await expect(page.locator("tr").filter({ hasText: usuario })).toContainText("Inactivo");
});

test("Users list: role and status filters, sort, column choice and the last chosen role survive a reload", async ({ adminPage: page, app, newContext }) => {
  await createStaff(page, "audit.list");
  // A status filter shows only when the list holds both states (progressive
  // disclosure, 2026-10-26): one inactive account gives it a choice.
  await createStaff(page, "audit.gone");
  expect((await page.request.delete("/api/admin/users/audit.gone")).status()).toBe(200);
  // Filters only on a list worth filtering (FILTER_MIN_ROWS = 10 rows in
  // admin-filter-controls.mjs).
  for (let i = 1; i <= 8; i++) await createStaff(page, "audit.fill" + i);
  await page.goto("/admin/users");
  await expect(page.locator("#usr-f-rol")).toBeVisible();
  for (const [id, value] of [["#usr-f-rol", "admisionista"], ["#usr-f-estado", "activo"]]) {
    const saved = page.waitForResponse(isPreferencesPut);
    await page.locator(id).selectOption(value);
    expect((await saved).ok()).toBe(true);
  }
  const header = page.locator("#sec-usuarios th[data-sort]").first();
  const sorted = page.waitForResponse(isPreferencesPut);
  await header.click();
  expect((await sorted).ok()).toBe(true);
  const sortState = await header.getAttribute("aria-sort");
  expect(sortState).toBeTruthy();

  await page.locator("#sec-usuarios [data-tblcol-gear]").click();
  const box = page.locator("#sec-usuarios [data-tblcol]").last();
  const shown = await box.isChecked();
  const columns = page.waitForResponse(isPreferencesPut);
  await box.setChecked(!shown);
  expect((await columns).ok()).toBe(true);
  const key = await box.getAttribute("data-tblcol");

  await page.reload();
  await expect(page.locator("#usr-f-rol")).toHaveValue("admisionista");
  await expect(page.locator("#usr-f-estado")).toHaveValue("activo");
  await expect(page.locator("#sec-usuarios th[data-sort]").first()).toHaveAttribute("aria-sort", sortState);
  await page.locator("#sec-usuarios [data-tblcol-gear]").click();
  await expect(page.locator(`#sec-usuarios [data-tblcol="${key}"]`)).toBeChecked({ checked: !shown });

  // The role chosen for the last new user is preselected on the next one.
  await page.goto("/admin/users?new=1");
  await page.locator("#usr-usuario").fill("audit.reports");
  await page.locator("#usr-pass").fill("SynthPassword93");
  await page.locator('input[name="usr-rol"][value="reportes"]').check();
  await saveUser(page);
  await page.goto("/admin/users?new=1");
  await expect(page.locator('input[name="usr-rol"][value="reportes"]')).toBeChecked();

  // Stored on the server, not only replayed by this tab: a separate browser (its own
  // sessionStorage) signing in as the same admin sees every choice.
  const other = await newContext();
  const fresh = await other.newPage();
  await loginAdmin(fresh, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/users");
  await expect(fresh.locator("#usr-f-rol")).toHaveValue("admisionista");
  await expect(fresh.locator("#usr-f-estado")).toHaveValue("activo");
  await expect(fresh.locator("#sec-usuarios th[data-sort]").first()).toHaveAttribute("aria-sort", sortState);
  await fresh.goto("/admin/users?new=1");
  await expect(fresh.locator('input[name="usr-rol"][value="reportes"]')).toBeChecked();
});

test("Account: the username field renames the account; the new name is on the reloaded form and signs in", async ({ adminPage, newContext }) => {
  await createStaff(adminPage, "audit.rename", { rol: "admin" });
  const page = await (await newContext()).newPage();
  await page.goto("/admin/login?next=/admin/account");
  await page.fill("#gate-user", "audit.rename"); await page.fill("#gate-pass", "SynthPassword93");
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname === "/admin/account");
  await page.click("#acct-password-edit");
  await page.fill("#acct-username", "audit.renamed");
  await page.fill("#acct-pass-current", "SynthPassword93");
  await write(page, "/api/admin/account/password", () => page.locator('#acct-password-form button[type="submit"]').click());
  await page.reload();
  await expect(page.locator("#acct-username")).toHaveValue("audit.renamed");
  const fresh = await newContext();
  expect(await staffLogin(fresh, "audit.rename", "SynthPassword93")).toBe(401);
  expect(await staffLogin(fresh, "audit.renamed", "SynthPassword93")).toBe(200);
});

test("Wiki: a disabled article shows Deshabilitado after a reload and Restore brings it back", async ({ adminPage: page }) => {
  const created = await page.request.post(KNOWLEDGE_API, {
    data: { type: "articulo", title: "Audit restore article", content: "Synthetic body.", jurisdiction: "interno" },
  });
  expect(created.status(), await created.text()).toBe(200);
  const id = (await created.json()).item.id;
  await page.goto("/admin/knowledge?item=" + encodeURIComponent(id));
  await expect(page.locator("#kn-form")).toBeVisible();
  await page.locator(".kn-more").evaluate((node) => { node.open = true; });
  await write(page, KNOWLEDGE_API + "/" + id, () => page.click("[data-kn-delete]"));
  await page.reload();
  const node = page.locator("#kn-tree a.kn-node").filter({ hasText: "Audit restore article" });
  await expect(node).toContainText("Deshabilitado");
  await page.goto("/admin/knowledge?item=" + encodeURIComponent(id));
  await page.locator(".kn-more").evaluate((el) => { el.open = true; });
  await write(page, KNOWLEDGE_API + "/" + id + "/restaurar", () => page.click("[data-kn-restore]"));
  await page.reload();
  await expect(node).toBeVisible();
  await expect(node).not.toContainText("Deshabilitado");
});

test("Prompts: «Esta versión» in the history restores an earlier text that survives a reload", async ({ adminPage: page }) => {
  const key = (await (await page.request.get("/api/admin/prompts")).json()).items[0].clave;
  await page.goto("/admin/prompts?prompt=" + key);
  await expect(page.locator("#pr-en")).toBeAttached();
  await page.locator('[data-tab="en"]').click();
  const original = await page.inputValue("#pr-en");
  for (const suffix of ["\nAudit version one.", "\nAudit version two."]) {
    await page.locator('[data-tab="en"]').click();
    await page.fill("#pr-en", original + suffix);
    await write(page, "/api/admin/prompts/" + key + "/guardar", () => page.click("#pr-guardar"));
    await expect(page.locator("#pr-guardar:not([disabled])")).toBeVisible();
    await page.reload();
    await expect(page.locator("#pr-en")).toBeAttached();
  }
  await expect(page.locator("#pr-en")).toHaveValue(original + "\nAudit version two.");
  const history = (await (await page.request.get("/api/admin/prompts/" + key + "/historial")).json()).items;
  const index = history.findIndex((item) => item.texto_en === original + "\nAudit version one.");
  expect(index, "version one is in the history").toBeGreaterThanOrEqual(0);
  await page.locator("#pr-det-hist").evaluate((node) => { node.open = true; });
  await write(page, "/api/admin/prompts/" + key + "/guardar", () => page.locator(`#pr-hist button[data-hist="${index}"]`).click());
  await page.reload();
  await expect(page.locator("#pr-en")).toBeAttached();
  await expect(page.locator("#pr-en")).toHaveValue(original + "\nAudit version one.");
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/prompts/" + key, () => page.click("#pr-restaurar"));
});

test("Settings: holidays added, renamed and removed keep their date and name after a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/settings");
  const card = () => page.locator('.set-card[data-grupo="feriados"]');
  await expect(card()).toBeVisible();
  const rows = () => card().locator(".set-hol-row");
  const before = await rows().count();
  for (const [fecha, nombre] of [["2031-03-02", "Audit holiday one"], ["2031-03-03", "Audit holiday two"]]) {
    await card().locator("[data-feriado-agregar]").click();
    const row = rows().last();
    await row.locator("[data-feriado-fecha]").fill(fecha);
    await row.locator("[data-feriado-nombre]").fill(nombre);
  }
  await write(page, "/api/admin/settings", () => card().locator("[data-feriados-guardar]").click());
  await expect(page.locator('[data-msg="feriados"]').filter({ hasText: "Guardado" })).toBeVisible();
  await page.reload();
  await expect(rows()).toHaveCount(before + 2);
  const one = rows().filter({ has: page.locator('[data-feriado-fecha][value="2031-03-02"]') });
  await expect(one.locator("[data-feriado-nombre]")).toHaveValue("Audit holiday one");
  await one.locator("[data-feriado-nombre]").fill("Audit holiday renamed");
  await rows().filter({ has: page.locator('[data-feriado-fecha][value="2031-03-03"]') }).locator("[data-feriado-quitar]").click();
  await write(page, "/api/admin/settings", () => card().locator("[data-feriados-guardar]").click());
  await expect(page.locator('[data-msg="feriados"]').filter({ hasText: "Guardado" })).toBeVisible();
  await page.reload();
  await expect(rows()).toHaveCount(before + 1);
  await expect(rows().filter({ has: page.locator('[data-feriado-fecha][value="2031-03-02"]') })
    .locator("[data-feriado-nombre]")).toHaveValue("Audit holiday renamed");
});

test("Settings: the location, languages and booking-window fields the round-trip file left out keep their values after a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/settings");
  const otherOption = async (key) => {
    const select = page.locator(`select[data-key="${key}"]`);
    await expect(select).toBeVisible();
    const current = await select.inputValue();
    const values = await select.locator("option").evaluateAll((nodes) => nodes.map((node) => node.value));
    const next = values.find((value) => value && value !== current);
    expect(next, key + " offers another choice").toBeTruthy();
    await select.selectOption(next);
    return next;
  };
  const save = async (group) => {
    await write(page, "/api/admin/settings", () => page.locator(`.set-card[data-grupo="${group}"] .set-guardar`).click());
    await expect(page.locator(`[data-msg="${group}"]`).filter({ hasText: "Guardado" })).toBeVisible();
  };
  const expected = {};
  expected.SITE_ID_MAIN_CENTER = await otherOption("SITE_ID_MAIN_CENTER");
  for (const [key, value] of [["SITE_ID_CITY", "Ciudad Auditoría"], ["SITE_ID_PROVINCE", "Provincia Auditoría"],
    ["SITE_ID_EMERGENCY_NUMBER", "107"]]) {
    await page.locator(`[data-key="${key}"]`).fill(value);
    expected[key] = value;
  }
  await save("location");
  // The default-language choice lists only enabled languages: enable English first.
  const english = page.locator('[data-lang-check][value="en"]');
  const englishBefore = await english.isChecked();
  if (!englishBefore) {
    await english.check();
    await save("languages");
    await page.reload();
    await expect(english).toBeChecked();
  }
  const languagesBefore = {
    LANG_DEFAULT: await page.locator('select[data-key="LANG_DEFAULT"]').inputValue(),
    LANG_SWITCHER_MODE: await page.locator('select[data-key="LANG_SWITCHER_MODE"]').inputValue(),
  };
  expected.LANG_DEFAULT = await otherOption("LANG_DEFAULT");
  expected.LANG_SWITCHER_MODE = await otherOption("LANG_SWITCHER_MODE");
  await save("languages");
  await page.locator('[data-key="BOOKING_HORIZON_DAYS"]').fill("45");
  await page.locator('[data-key="BOOKING_OPENS_AT"]').fill("07:30");
  expected.BOOKING_HORIZON_DAYS = "45";
  expected.BOOKING_OPENS_AT = "07:30";
  await save("turnos");
  await page.reload();
  for (const [key, value] of Object.entries(expected)) {
    await expect(page.locator(`[data-key="${key}"]`), key).toHaveValue(value);
  }
  // The later tests of this file read Spanish labels: put the language defaults back.
  for (const [key, value] of Object.entries(languagesBefore)) await page.locator(`select[data-key="${key}"]`).selectOption(value);
  await english.setChecked(englishBefore);
  await write(page, "/api/admin/settings", () => page.locator('.set-card[data-grupo="languages"] .set-guardar').click());
});

test("Settings: generating the backup recovery key leaves its fingerprint and «Rotar clave» after a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/settings?view=integrations");
  const row = page.locator('[data-campo="TURNOS_BACKUP_RECOVERY_KEY"]');
  await expect(row.locator("[data-backup-generate]")).toBeVisible();
  const ack = await write(page, "/api/admin/settings", () => row.locator("[data-backup-generate]").click(), true);
  const fingerprint = ack.backup_key.fingerprint;
  expect(fingerprint).toBeTruthy();
  await expect(row.locator("[data-recovery-panel]")).not.toBeEmpty();
  await page.reload();
  await expect(row).toContainText(fingerprint);
  await expect(row.locator("[data-backup-generate]")).toHaveText(/Rotar clave/);
  // «Rotar clave» replaces it: the reloaded row shows the new fingerprint, not the old one.
  const rotated = await write(page, "/api/admin/settings", () => row.locator("[data-backup-generate]").click(), true);
  expect(rotated.backup_key.fingerprint).toBeTruthy();
  expect(rotated.backup_key.fingerprint).not.toBe(fingerprint);
  await page.reload();
  await expect(row).toContainText(rotated.backup_key.fingerprint);
  await expect(row).not.toContainText(fingerprint);
});

test("Mail links: an invited user and a forgotten password each set a password on the link page that signs in through the form", async ({ browser, cleanup, app: fixture, sink }) => {
  sink.mails.length = 0;
  const context = await browser.newContext({ baseURL: fixture.baseUrl, viewport: { width: 1280, height: 900 } });
  cleanup(() => context.close());
  const page = await context.newPage();
  await loginAdmin(page, fixture.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/users");

  const linkFor = async (to) => {
    await expect.poll(() => sink.mails.filter((mail) => mail.to.includes(to)).length).toBeGreaterThan(0);
    const mail = sink.mails.filter((m) => m.to.includes(to)).pop();
    sink.mails.length = 0;
    const match = (mail.text + " " + mail.html).match(/https?:\/\/[^\s"'<>]+reset-password\?token=[^\s"'<>&]+/);
    expect(match, "the mail carries the link").toBeTruthy();
    return new URL(match[0]).pathname + new URL(match[0]).search;
  };
  const setOnLinkAndSignIn = async (link, usuario, password) => {
    const visitor = await browser.newContext({ baseURL: fixture.baseUrl });
    cleanup(() => visitor.close());
    const tab = await visitor.newPage();
    await tab.goto(link);
    await tab.fill("#reset-pass", password);
    await tab.fill("#reset-confirm", password);
    await write(tab, "/api/admin/reset-password", () => tab.locator('#reset-form button[type="submit"]').click());
    // The link page may sign the visitor in and leave on its own (/admin); the proof
    // is a separate, signed-out browser entering through the login form.
    await expect(tab.locator("#reset-form")).toBeHidden();
    const signedOut = await browser.newContext({ baseURL: fixture.baseUrl });
    cleanup(() => signedOut.close());
    const login = await signedOut.newPage();
    await login.goto("/admin/login?next=/admin/account");
    await signIn(login, usuario, password);
  };
  const signIn = async (tab, usuario, password) => {
    await tab.fill("#gate-user", usuario); await tab.fill("#gate-pass", password);
    await tab.click("#gate-btn");
    await tab.waitForURL((url) => url.pathname === "/admin/account");
  };

  // Invite: «Elegir contraseña por email».
  await page.goto("/admin/users?new=1");
  await page.locator("#usr-usuario").fill("audit.invited");
  await page.locator("#usr-email").fill("audit.invited@example.test");
  await page.locator("#usr-invite").check();
  await page.locator('input[name="usr-rol"][value="admisionista"]').check();
  await write(page, "/api/admin/users", () => page.locator('button[type="submit"][form="usr-form"]').click());
  await setOnLinkAndSignIn(await linkFor("audit.invited@example.test"), "audit.invited", "InvitedChosen5521");

  // Recovery: «¿Olvidaste tu contraseña?» on the login gate.
  const visitor = await browser.newContext({ baseURL: fixture.baseUrl });
  cleanup(() => visitor.close());
  const gate = await visitor.newPage();
  await gate.goto("/admin/login");
  await gate.fill("#gate-user", "audit.invited");
  await write(gate, "/api/admin/reset-password", () => gate.click("#gate-forgot"));
  await expect(gate.locator("#gate-reset-msg")).not.toBeEmpty();
  await setOnLinkAndSignIn(await linkFor("audit.invited@example.test"), "audit.invited", "RecoveredChosen7730");
});
