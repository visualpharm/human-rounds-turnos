// TASK-3 form audit, front desk: the Solicitudes, Pacientes de hoy and Pacientes writes the
// 2026-10-25 inventory found without a «save → cold reload → the value is on the page» test
// (docs/architecture/admin-forms-audit.md). Real controls → ASGI → an isolated PostgreSQL with
// six fictional pending requests and the day-queue appointments of fixture.py.
import { test, expect, write } from "./lib/fixtures.mjs";
import { openRequest, doctorCombo } from "./lib/journey.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_forms_audit_desk", requests: 7, queue: true, queueLater: true },
  viewport: { width: 1280, height: 900 },
});
// Not serial: every test spends its own seeded rows, so one failure does not hide the others.

const isPreferencesPut = (response) => new URL(response.url()).pathname === "/api/admin/preferences"
  && response.request().method() === "PUT";
const requestApi = (action) => (response) => new RegExp("/api/admin/requests/\\d+/" + action + "$").test(new URL(response.url()).pathname)
  && response.request().method() === "POST";

async function showStatus(page, estado) {
  await page.locator("#f-estado").selectOption(estado);
  await expect(page.locator("#f-estado")).toHaveValue(estado);
}

/** /admin/solicitudes on its pending list: the status filter is a saved preference, and a
 *  previous test of this file may have left another one. */
async function openPending(page) {
  await page.goto("/admin/solicitudes");
  // The saved filter is applied with the first list paint: read it only after that.
  await expect(page.locator("#list [data-sol-row]").first()).toBeVisible();
  if (await page.locator("#f-estado").inputValue() !== "pendiente") await showStatus(page, "pendiente");
}

test("Solicitudes: a rejected request is under «Rechazadas» with its reason after a reload", async ({ adminPage: page, app }) => {
  const target = app.fixture.requests.find((r) => r.nombre.startsWith("Pilar"));
  await openPending(page);
  const detail = await openRequest(page, { hasText: target.nombre });
  // The option's label is a short name; its value is the sentence that is stored and shown.
  const reason = (await detail.locator(".motivo-sel").inputValue()).trim();
  expect(reason).toBeTruthy();
  const rejected = page.waitForResponse(requestApi("reject"));
  await detail.locator('[data-act="rechazar"]').click();
  expect((await rejected).status()).toBe(200);
  await page.reload();
  await expect(page.locator("#list")).not.toContainText(target.nombre);
  await showStatus(page, "rechazada");
  const row = page.locator("#list [data-sol-row]").filter({ hasText: target.nombre });
  await row.click();
  await expect(page.locator("aside.sol-det[data-id]")).toContainText(reason);
  const back = page.waitForResponse(isPreferencesPut);
  await showStatus(page, "pendiente");
  expect((await back).ok()).toBe(true);
});

test("Solicitudes: the doctor picked in the detail pane is still picked after a reload", async ({ adminPage: page, app }) => {
  const target = app.fixture.requests.find((r) => r.nombre.startsWith("Tomas"));
  await openPending(page);
  let detail = await openRequest(page, { hasText: target.nombre });
  const combo = doctorCombo(detail);
  const current = await combo.value();
  const names = await combo.names();
  // Picking the doctor already shown changes nothing (no request): pick another one.
  const other = names.find((name) => name && name !== current);
  expect(other, "the service offers another doctor than the default: " + names.join(", ")).toBeTruthy();
  const assigned = page.waitForResponse(requestApi("assign"));
  await combo.pick(other);
  expect((await assigned).status()).toBe(200);
  await page.reload();
  detail = await openRequest(page, { hasText: target.nombre });
  await expect(detail.locator("input.med-sel")).toHaveValue(other);
});

test("Solicitudes: bulk approve and bulk reject move the ticked rows to their new status after a reload", async ({ adminPage: page, app }) => {
  const [a, b, c] = app.fixture.requests.filter((r) => r.nombre.startsWith("Audit")).slice(0, 3);
  await openPending(page);
  const tick = (who) => page.locator("#list [data-sol-row]").filter({ hasText: who.nombre }).locator(".sol-ck");
  // One row for the bulk approval: two requests of one service with no time the patient
  // chose both get the same default (next weekday 09:00) and the second answers 409 —
  // reported separately (docs/architecture/admin-forms-audit.md), not this form's loss.
  await tick(a).check();
  const approved = page.waitForResponse(requestApi("approve"));
  await page.locator('[data-bulk-action="aprobar"]').click();
  expect((await approved).status()).toBe(200);
  await page.reload();
  await expect(page.locator("#list")).toContainText(c.nombre);
  await tick(b).check();
  await tick(c).check();
  await page.locator('[data-bulk-action="rechazar"]').click();
  await expect(page.locator("#bulk-reject-dialog")).toBeVisible();
  const reason = await page.locator("#bulk-reject-reason option").nth(1).getAttribute("value");
  await page.locator("#bulk-reject-reason").selectOption(reason);
  const rejections = [];
  page.on("response", (response) => { if (requestApi("reject")(response)) rejections.push(response.status()); });
  await page.click("#bulk-reject-confirm");
  await expect.poll(() => rejections.length).toBe(2);
  expect(rejections).toEqual([200, 200]);
  await page.reload();
  for (const who of [a, b, c]) await expect(page.locator("#list")).not.toContainText(who.nombre);
  await showStatus(page, "aprobada");
  await expect(page.locator("#list")).toContainText(a.nombre);
  await showStatus(page, "rechazada");
  for (const who of [b, c]) await expect(page.locator("#list")).toContainText(who.nombre);
});

test("Solicitudes: bulk approve of two same-service requests gives each the next free time of the service", async ({ adminPage: page, app }) => {
  // Found by the audit (2026-10-25): every row without a time the patient chose got the same
  // default (next weekday 09:00), so the second answered 409 — and both did once an earlier
  // approval had taken that 09:00. Decided (pinamar-turnos, 2026-10-25): each row of the
  // batch takes the next free time of its service.
  const [d, e] = app.fixture.requests.filter((r) => r.nombre.startsWith("Audit")).slice(3, 5);
  await openPending(page);
  // A time the agenda offers but the doctor already has answers 409 and the row tries the
  // next one, so count the approvals that succeeded, one per request.
  const approved = new Map();
  page.on("response", (response) => {
    if (!requestApi("approve")(response) || response.status() !== 200) return;
    approved.set(new URL(response.url()).pathname, JSON.parse(response.request().postData() || "{}").turno_dt);
  });
  for (const who of [d, e]) await page.locator("#list [data-sol-row]").filter({ hasText: who.nombre }).locator(".sol-ck").check();
  await page.locator('[data-bulk-action="aprobar"]').click();
  await expect(page.locator("#toast")).toContainText("2 aprobadas");
  expect(approved.size, "both requests approved").toBe(2);
  const [first, second] = [...approved.values()];
  expect(first, "two different times").not.toBe(second);
  await page.reload();
  for (const who of [d, e]) await expect(page.locator("#list")).not.toContainText(who.nombre);
  await showStatus(page, "aprobada");
  for (const who of [d, e]) await expect(page.locator("#list")).toContainText(who.nombre);
  await showStatus(page, "pendiente");
});

test("Solicitudes: the chosen status filter is the one shown after a reload", async ({ adminPage: page }) => {
  const puts = [], errors = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/admin/preferences" && request.method() === "PUT") puts.push(request.postData());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error" || message.type() === "warning") errors.push(message.text()); });
  await page.goto("/admin/solicitudes");
  await expect(page.locator("#list")).toBeVisible();
  await showStatus(page, "aprobada");
  await expect.poll(() => puts.some((body) => /requests\.status/.test(body) && /aprobada/.test(body)),
    { message: "the status filter is saved; PUTs " + JSON.stringify(puts) + " errors " + JSON.stringify(errors) }).toBe(true);
  await page.reload();
  await expect(page.locator("#f-estado")).toHaveValue("aprobada");
  await showStatus(page, "pendiente");
});

test("Pacientes de hoy: attendance is marked on the row and a cancelled appointment is gone after a reload", async ({ adminPage: page, app }) => {
  // The queue lists doctors first; a doctor's row opens that doctor's appointments.
  // The day is one list (2026-11-08): the unassigned rows are already there.
  const openGroup = async (group) => {
    // The rows arrive after the controls (one fetch of the day): wait for
    // them before looking for the doctor link inside one.
    await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 15000 });
    await expect(page.locator("#sec-cola tr[data-cola-row]").first()).toBeVisible({ timeout: 15000 });
    await expect(group).toBeVisible({ timeout: 15000 });
  };
  const unassigned = page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="_sin_medico"]').first();
  await page.goto("/admin/cola");
  await openGroup(unassigned);
  const pending = page.locator("tr.cnt-tr", { hasText: app.fixture.queue_pendiente }).first();
  await expect(pending).toBeVisible();
  // Attendance is marked in the appointment card the name opens (2026-11-09).
  const posted = page.waitForResponse(requestApi("attendance"));
  await pending.locator(".queue-pat-link").click();
  await page.locator('.queue-card [data-asis][data-valor="presente"]').click();
  expect((await posted).status()).toBe(200);
  await page.reload();
  await openGroup(unassigned);
  await expect(pending).toHaveClass(/\bqueue-tr-done\b/);
  await pending.locator(".queue-pat-link").click();
  await expect(page.locator(".queue-card .queue-attendance-marked")).toContainText(/Atendi[oó]/i);
  await page.locator(".queue-card .solmod-x").click();
  await expect(page.locator(".queue-card")).toHaveCount(0);

  // «Cancelar turno» is offered only while the appointment is still ahead.
  const later = page.locator("tr.cnt-tr", { hasText: app.fixture.queue_later }).first();
  await expect(later).toBeVisible();
  await later.locator("[data-cola-abrir]").first().click();
  await page.locator("[data-cola-cancel]").click();
  const cancelled = page.waitForResponse(requestApi("cancel"));
  await page.locator("[data-cola-cancel-yes]").click();
  expect((await cancelled).status()).toBe(200);
  await page.reload();
  await openGroup(unassigned);
  await expect(page.locator("tr.cnt-tr", { hasText: app.fixture.queue_pendiente }).first()).toBeVisible();
  await expect(page.locator("#sec-cola")).not.toContainText(app.fixture.queue_later);
});

test("Pacientes de hoy: the column choice survives a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/cola");
  const gear = page.locator("[data-cola-gear]").first();
  const box = page.locator("[data-cola-col]").last();
  const shown = await box.isChecked();
  const key = await box.getAttribute("data-cola-col");
  const saved = page.waitForResponse(isPreferencesPut);
  // The day's data arriving repaints the controls and closes a popover opened
  // a moment earlier: open and tick in one retried step.
  await expect(async () => {
    if (!(await box.isVisible())) await gear.click();
    await box.setChecked(!shown, { timeout: 1000 });
  }).toPass({ timeout: 10_000 });
  expect((await saved).ok()).toBe(true);
  await page.reload();
  const reopened = page.locator(`[data-cola-col="${key}"]`);
  await expect(async () => {
    if (!(await reopened.isVisible())) await gear.click();
    await expect(reopened).toBeChecked({ checked: !shown, timeout: 1000 });
  }).toPass({ timeout: 10_000 });
});

test("Pacientes: archive and reactivate are what the reopened file shows; the search box keeps its text", async ({ adminPage: page }) => {
  const dni = "30111777";
  const created = await page.request.post("/api/admin/patients", {
    data: { dni, nombre: "Audit", apellido: "Archivo", nacimiento: "1980-02-02", sexo: "F" },
  });
  expect(created.status(), await created.text()).toBe(200);
  await page.goto("/admin/pacientes?patient=" + dni);
  const archive = page.locator("#pac-arch");
  await page.click("#pac-edit");
  await expect(archive).toBeVisible();
  const activeLabel = (await archive.innerText()).trim();
  // Archive and reactivate both leave for the list on their own (location.assign).
  const backToList = () => page.waitForURL((u) => u.pathname === "/admin/pacientes" && !u.search);
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/patients/" + dni, () => archive.click());
  await backToList();
  await page.goto("/admin/pacientes?patient=" + dni);
  await page.click("#pac-edit");
  await expect(archive).toBeVisible();
  await expect(archive).not.toHaveText(activeLabel);
  const archivedLabel = (await archive.innerText()).trim();
  await write(page, "/api/admin/patients/" + dni, () => archive.click());
  await backToList();
  await page.goto("/admin/pacientes?patient=" + dni);
  await page.click("#pac-edit");
  await expect(archive).toHaveText(activeLabel);
  expect(archivedLabel).not.toBe(activeLabel);

  await page.goto("/admin/pacientes");
  const saved = page.waitForResponse(isPreferencesPut);
  await page.locator("#pac-q").fill("Archivo");
  expect((await saved).ok()).toBe(true);
  await page.reload();
  await expect(page.locator("#pac-q")).toHaveValue("Archivo");
});
