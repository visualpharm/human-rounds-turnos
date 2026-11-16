// Actual browser -> ASGI -> throwaway PostgreSQL role and assignment round trip.
//
// Port of tests/e2e/admin-roles-save.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The two tests are independent (the second only uses the seeded `gestor_agendas` role), so the
//   file is not serial. Each test gets the runner's `page` (390x900 through `test.use`) and signs in
//   with `loginAdmin(..., next=/admin/roles)`, the same form login as the original; the staff
//   account's second context is the `newContext` fixture, closed by the runner (no `t.after`).
// - API calls go through `page.request` / `staff.request` with relative URLs (the context's baseURL).
// - Screenshots go through `saveShot` to the same files as before (artifactPath("pinamar-turnos",
//   "forms3-20261015", "roles-<timestamp>")) and are attached to the report; the closing
//   `console.log("Roles evidence:", ...)` is a report diagnostic (`note`).
// - Waits and field checks are web-first (`toBeVisible`, `toHaveValue`, `toBeChecked`, `toContainText`,
//   `toHaveCount`); status codes, the stored role, the overflow/bounds measurements and the
//   absence checks (`count()` of 0) stay point-in-time reads.
// - «duplicate submissions issue only one mutation» was a single read of the write counter once the
//   first request arrived; it now holds for 300 ms (`holdsFor`), so a second write that lands a
//   moment later is caught as well. Everything else in that test keeps its original moment.
// - The second test keeps `page.setDefaultTimeout(8000)` for its actions/waits.
// - Locators that stay CSS: the role/user editor ids, `input[name=...][value=...]` permission and role
//   checkboxes, `a[data-nav=...]` sidebar entries and `.cnt-table-wrap` are the markup contract the
//   round trip is about (the sidebar and bounds are measured, not just clicked).
import { join } from "node:path";
import {
  test, expect, loginAdmin, saveShot, holdGate, holdsFor, note, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_roles_save" },
  viewport: { width: 390, height: 900 },
});

const evidence = artifactPath("pinamar-turnos", "forms3-20261015", `roles-${Date.now()}`);

test("roles can be created, edited, reloaded and assigned without losing an existing disabled role", async ({ page, app, newContext }, testInfo) => {
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/roles");
  await page.goto("/admin/roles");
  // Regression: the old page is only a static title, with no executable editor.
  await expect(page.locator("#role-new")).toBeVisible({ timeout: 5000 });
  await page.locator("#role-new").click();
  await page.locator("#role-name").fill("Test agenda team");
  await page.locator("#role-description").fill("Synthetic schedule team");
  await page.locator('input[name="role-permission"][value="medicos"]').check();
  await page.locator("#role-save").click();
  await page.waitForURL((url) => !!url.searchParams.get("role"));
  const slug = new URL(page.url()).searchParams.get("role");
  expect(slug).toBeTruthy();
  await page.reload();
  await expect(page.locator("#role-name")).toBeVisible();
  await expect(page.locator("#role-name")).toHaveValue("Test agenda team");
  await expect(page.locator('input[name="role-permission"][value="medicos"]')).toBeChecked();
  await page.locator("#role-name").fill("Test agenda edited");
  await page.locator('input[name="role-permission"][value="servicios"]').check();
  await page.locator("#role-save").click();
  await expect(page.locator("#roles-status")).toContainText("Guardado");
  await page.reload();
  await expect(page.locator("#role-name")).toBeVisible();
  await expect(page.locator("#role-name")).toHaveValue("Test agenda edited");
  const roles = await page.request.get("/api/admin/roles");
  expect(roles.status()).toBe(200);
  const stored = (await roles.json()).find((role) => role.slug === slug);
  expect(stored.permissions).toEqual(["medicos", "servicios"]);
  expect(stored.description).toBe("Synthetic schedule team");
  for (const enabled of [false, true]) {
    await page.locator("#role-enabled").setChecked(enabled);
    await page.locator("#role-save").click();
    await expect(page.locator("#roles-status")).toContainText("Guardado");
    await page.reload();
    await expect(page.locator("#role-enabled")).toBeVisible();
    await expect(page.locator("#role-enabled")).toBeChecked({ checked: enabled });
  }
  for (const width of [360, 390, 768, 834, 1024, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `no overflow at ${width}`).toBe(true);
    if ([390, 1280].includes(width)) await saveShot(testInfo, page, join(evidence, `editor-${width}.png`), { fullPage: true });
  }
  await page.goto("/admin/users?new=1");
  await expect(page.locator("#usr-form")).toBeVisible();
  await page.locator("#usr-usuario").fill("test.agenda");
  await page.locator("#usr-pass").fill("SyntheticPassword93");
  await page.locator(`input[name="usr-rol"][value="${slug}"]`).check();
  await page.locator('button[type="submit"][form="usr-form"]').click();
  await page.waitForURL((url) => url.pathname === "/admin/users" && !url.search);
  await page.goto("/admin/users?user=test.agenda");
  await expect(page.locator("#usr-form")).toBeVisible();
  await expect(page.locator(`input[name="usr-rol"][value="${slug}"]`)).toBeChecked();
  const users = await page.request.get("/api/admin/users");
  expect((await users.json()).items.find((user) => user.usuario === "test.agenda").rol).toBe(slug);
  const staff = await newContext({ viewport: { width: 390, height: 900 } });
  const staffPage = await staff.newPage();
  await staffPage.goto("/admin/login?next=/admin/doctors");
  await staffPage.locator("#gate-user").fill("test.agenda");
  await staffPage.locator("#gate-pass").fill("SyntheticPassword93");
  await staffPage.locator("#gate-btn").click();
  await staffPage.waitForURL((url) => url.pathname === "/admin/doctors");
  await expect(staffPage.locator("#sec-medicos .cnt-table")).toBeVisible();
  await expect(staffPage.locator('a[data-nav="medicos"]')).toHaveCount(1);
  await expect(staffPage.locator('a[data-nav="servicios"]')).toHaveCount(1);
  expect(await staffPage.locator('a[data-nav="roles"]').count()).toBe(0);
  await expect(page.locator('a[data-nav="roles"]'), "administrator retains its sidebar").toHaveCount(1);
  expect((await staff.request.get("/api/admin/content/doctors")).status()).toBe(200);
  expect((await staff.request.get("/api/admin/roles")).status(), "assigned permissions do not grant role management").toBe(403);
  // A disabled role remains selected while editing its existing user; never default to doctor.
  expect((await page.request.put("/api/admin/roles/" + slug, { data: { enabled: false } })).status()).toBe(200);
  expect((await staff.request.get("/api/admin/content/doctors")).status(), "disabling the role revokes the existing session access").toBe(403);
  await page.reload();
  await expect(page.locator("#usr-form")).toBeVisible();
  await expect(page.locator(`input[name="usr-rol"][value="${slug}"]`)).toBeChecked();
  await page.locator("#usr-email").fill("synthetic@example.test");
  await page.locator('button[type="submit"][form="usr-form"]').click();
  await page.waitForURL((url) => url.pathname === "/admin/users" && !url.search);
  const retained = (await (await page.request.get("/api/admin/users")).json()).items.find((user) => user.usuario === "test.agenda");
  expect(retained.rol).toBe(slug);
  expect(retained.email).toBe("synthetic@example.test");
  await page.goto("/admin/users?user=test.agenda");
  await expect(page.locator("#usr-email")).toBeVisible();
  await expect(page.locator("#usr-email")).toHaveValue("synthetic@example.test");
  await page.goto("/admin/roles?role=admin");
  await expect(page.locator("#role-name")).toBeVisible();
  expect(await page.locator("#role-save").count(), "protected admin has no edit action").toBe(0);
  expect((await page.request.put("/api/admin/roles/admin", { data: { enabled: false } })).status()).toBe(400);
  await page.context().clearCookies();
  await page.reload();
  await expect(page.locator("#gate-user")).toBeVisible();
  expect(new URL(page.url()).searchParams.get("next")).toBe("/admin/roles?role=admin");
  await page.locator("#gate-user").fill(ADMIN_USER);
  await page.locator("#gate-pass").fill(ADMIN_PASS);
  await page.locator("#gate-btn").click();
  await page.waitForURL((url) => url.pathname === "/admin/roles" && url.searchParams.get("role") === "admin");
  await expect(page.locator("#role-name")).toBeVisible();
  expect(await page.locator("#role-save").count()).toBe(0);
  await page.goto("/admin/roles");
  await expect(page.locator("#role-new")).toBeVisible();
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (width === 390) {
      // One left edge (--adm-gutter, admin-left-edge.spec.mjs): on the phone the table sits
      // inside the screen gutter like the title, not full-bleed, and spans to the right gutter.
      const gutter = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--adm-gutter")));
      expect(gutter).toBeGreaterThan(0);
      const bounds = await stableBox(page.locator(".cnt-table-wrap"), "roles table wrap");
      expect(Math.round(bounds.x)).toBe(gutter);
      expect(Math.round(bounds.width)).toBe(width - 2 * gutter);
    }
    await saveShot(testInfo, page, join(evidence, `list-${width}.png`), { fullPage: true });
  }
  note(testInfo, "Roles evidence: " + evidence);
});

test("roles retain failed drafts, reject malformed acknowledgements and ignore an abandoned save", async ({ page, app, cleanup }) => {
  page.setDefaultTimeout(8000);
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/roles");
  await page.goto("/admin/roles?role=gestor_agendas");
  await expect(page.locator("#role-name")).toBeVisible();
  await page.locator("#role-name").fill("Synthetic retry draft");
  const endpoint = "**/api/admin/roles/gestor_agendas";
  for (const failure of [
    { status: 503, json: { error: "Synthetic temporary failure" } },
    { status: 200, json: {} },
    { status: 200, json: { slug: "other_role", permissions: [] } },
    { status: 200, json: { slug: "gestor_agendas", name: "Invalid response", description: "Synthetic", permissions: [], enabled: "false" } },
  ]) {
    await page.route(endpoint, (route) => route.fulfill(failure), { times: 1 });
    await page.locator("#role-save").click();
    await page.waitForFunction(() => !document.querySelector("#role-save").disabled);
    expect(await page.locator("#role-name").inputValue()).toBe("Synthetic retry draft");
    expect(await page.locator("#roles-status").textContent()).not.toBe("Guardado.");
  }
  await page.locator("#role-save").click();
  await expect(page.locator("#roles-status")).toContainText("Guardado");
  await page.reload();
  await expect(page.locator("#role-name")).toBeVisible();
  await expect(page.locator("#role-name")).toHaveValue("Synthetic retry draft");
  await page.locator("#role-name").fill("Administrador");
  await page.locator("#role-save").click();
  await expect(page.locator("#role-name-error")).toBeVisible();
  await expect(page.locator("#role-name")).toHaveValue("Administrador");
  await page.locator("#role-name").fill("Synthetic retry draft");
  const gate = holdGate(cleanup);
  let writes = 0;
  let drained;
  const completed = new Promise((resolve) => { drained = resolve; });
  await page.route(endpoint, async (route) => {
    writes++;
    gate.arrive();
    await gate.held;
    try {
      await route.fulfill({ json: { slug: "gestor_agendas", name: "Late old response", description: "Synthetic", permissions: ["medicos"] } });
    } finally { drained(); }
  });
  await page.evaluate(() => { document.querySelector("#role-form").requestSubmit(); document.querySelector("#role-form").requestSubmit(); });
  await gate.arrived;
  await holdsFor(async () => {
    expect(writes, "duplicate submissions issue only one mutation").toBe(1);
  }, { during: 300 });
  await page.locator("#adm-h1 a.adm-crumb").click();
  await expect(page.locator("#role-new")).toBeVisible();
  gate.release();
  await completed;
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.locator("#role-form").count()).toBe(0);
  expect(await page.locator("#role-new").isVisible()).toBe(true);
  expect(await page.locator("#roles-status").textContent()).toBe("");
});
