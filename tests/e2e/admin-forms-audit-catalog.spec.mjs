// TASK-3 form audit, catalog: the services, centers, doctors, preparations and pre-visit
// interview writes the 2026-10-25 inventory found without a «save → cold reload → the value is
// on the page» test (docs/architecture/admin-forms-audit.md). Real controls → ASGI → an isolated
// PostgreSQL on the example-town pack. Disable/restore use pack items: disabling an item made in
// the admin deletes it for good when nothing references it, so there would be nothing to restore.
// Photos land in the checkout's data/ folders and are removed by each test. Those folders are
// shared by every worker while each worker has its own database, and a new record's id comes
// from its name: two copies of a photo test (--repeat-each, a retry next to a first run) would
// write, restore and delete the same files. Each photo test therefore names its record uniquely.
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { test, expect, write, saveShot, artifactPath, ROOT } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_forms_audit_catalog" },
  viewport: { width: 1280, height: 900 },
});

const PHOTO = new URL("../fixtures/center-photo-jfxx.jpeg", import.meta.url).pathname;
const WALK_IN = "demanda-espontanea-riverside-clinic";
const isPreferencesPut = (response) => new URL(response.url()).pathname === "/api/admin/preferences"
  && response.request().method() === "PUT";
const franjasOf = (doctor) => (doctor.centros || []).flatMap((c) => c.franjas || []);

const uniqueName = (name) => name + " " + randomUUID().slice(0, 8);

async function removePhotos(kind, id) {
  if (kind === "center") {
    for (const ext of ["jpg", "jpeg", "png", "webp"]) await fs.rm(path.join(ROOT, "data", "centro-fotos", id + "." + ext), { force: true });
  } else {
    await fs.rm(path.join(ROOT, "data", "medico-fotos", id + ".webp"), { force: true });
    await fs.rm(path.join(ROOT, "data", "medico-fotos", ".originals", id), { force: true, recursive: true });
  }
}

async function openEditor(page, url) {
  await page.goto(url);
  await expect(page.locator("#cnt-form")).toBeVisible();
}

// The location note, expiry date, zone, coordinates and HSI number of a
// center sit folded under «Ubicación exacta e HSI» until one of them is set.
async function showCenterMore(page) {
  const more = page.locator("#cen-more");
  if ((await more.getAttribute("open")) === null) await more.locator("summary").click();
  await expect(page.locator("#cen-hsi")).toBeVisible();
}

const saveButton = (page) => page.locator('button[type="submit"][form="cnt-form"]');

// A photo picked in the editor of a record that is not saved yet stays pending in the page
// until the file has been read; the avatar shows it at that moment. Saving before then saves
// the record without the photo, which is what a user who clicks Save a moment too early gets
// and not what these tests are about.
async function pickPendingPhoto(page, prefix) {
  await page.setInputFiles(`#${prefix}-foto-input`, PHOTO);
  await expect(page.locator(`#${prefix}-foto-av`)).toHaveAttribute("style", /data:image\//);
}

test("Services: a service-owned schedule is still on, with its day, after a reload", async ({ adminPage: page }) => {
  const doctors = await (await page.request.get("/api/admin/content/doctors")).json();
  const owner = doctors.items.find((d) => d.especialidad && franjasOf(d).length && d.centros && d.centros[0] && d.centros[0].centro);
  const created = await page.request.post("/api/admin/content/services", {
    data: { nombre: owner.especialidad + " Auditoría", centro: owner.centros[0].centro, modo: "solicitud",
            cluster: owner.cluster || "otras", horario: "Lunes de 9 a 12 h" } });
  expect(created.status(), await created.text()).toBe(200);
  const id = (await created.json()).item.id;
  await openEditor(page, "/admin/services?item=" + encodeURIComponent(id));
  await page.locator("#svc-schedule-owner").check();
  await page.locator('input[data-agenda-dia="svc"][value="1"]').check();
  await saveButton(page).click();
  const dialog = page.locator("dialog.cnt-schedule-confirm");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Continuar" }).click();
  await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible();
  await page.reload();
  await expect(page.locator("#cnt-form")).toBeVisible();
  await expect(page.locator("#svc-schedule-owner")).toBeChecked();
  await expect(page.locator('input[data-agenda-dia="svc"][value="1"]')).toBeChecked();
  await expect(page.locator("#svc-owner-status")).toContainText("atienden en este horario");
});

test("Services: an edit, «Restaurar original», Deshabilitar and the list's restore each show after a reload", async ({ adminPage: page }) => {
  const url = "/admin/services?item=" + WALK_IN;
  await openEditor(page, url);
  const original = await page.locator("#svc-horario").inputValue();
  await page.locator("#svc-horario").fill("Horario de auditoría");
  await write(page, "/api/admin/content/services/" + WALK_IN, () => saveButton(page).click());
  await page.reload();
  await expect(page.locator("#svc-horario")).toHaveValue("Horario de auditoría");
  await expect(page.locator("#cnt-restore")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/content/services/" + WALK_IN + "/restaurar", () => page.click("#cnt-restore"));
  await openEditor(page, url);
  await expect(page.locator("#svc-horario")).toHaveValue(original);
  await expect(page.locator("#cnt-restore")).toHaveCount(0);

  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/content/services/" + WALK_IN, () => page.click("#cnt-del"));
  await page.goto("/admin/services");
  await page.reload();
  const row = page.locator("tr", { has: page.locator(`a[href="/admin/services?item=${WALK_IN}"]`) });
  await expect(row).toHaveClass(/cnt-tr-borrado/);
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/content/services/" + WALK_IN + "/restaurar",
    () => row.locator(`button[data-restaurar="servicios"][data-id="${WALK_IN}"]`).click());
  await page.reload();
  await expect(row).toBeVisible();
  await expect(row).not.toHaveClass(/cnt-tr-borrado/);
});

test("Centers: a photo picked before the first save is on the reopened center; removing it leaves none", async ({ adminPage: page, cleanup }) => {
  await openEditor(page, "/admin/centers?item=new");
  await page.fill("#cen-nombre", uniqueName("Centro Auditoría Fotos"));
  await page.fill("#cen-direccion", "Calle Sintética 2");
  await pickPendingPhoto(page, "cen");
  const created = await write(page, "/api/admin/content/centers", () => saveButton(page).click(), true);
  const slug = created.item.slug || created.item.id;
  cleanup(() => removePhotos("center", slug));
  await page.waitForURL((u) => u.pathname === "/admin/centers" && !u.search);
  await openEditor(page, "/admin/centers?item=" + slug);
  // the 64 px circle shows the center's tile from the shared atlas (the full upload only when the atlas can't be built)
  await expect(page.locator("#cen-foto-av")).toHaveAttribute("style", /(avatar-sprite|center-photo)\//);
  await expect(page.locator("#cen-foto-quitar")).toBeVisible();
  await write(page, "/api/admin/centers/" + slug + "/photo", () => page.click("#cen-foto-quitar"));
  await page.reload();
  await expect(page.locator("#cnt-form")).toBeVisible();
  await expect(page.locator("#cen-foto-quitar")).toBeHidden();
  await expect(page.locator("#cen-foto-av")).not.toHaveAttribute("style", /(avatar-sprite|center-photo)\//);
});

test("Centers: «Institución en HSI» is on the reopened center; cleared, it is gone",async ({ adminPage: page }) => {
  const slug = "riverside-clinic";
  await openEditor(page, "/admin/centers?item=" + slug);
  await showCenterMore(page);
  await page.fill("#cen-hsi", "1077");
  await write(page, "/api/admin/content/centers/" + slug, () => saveButton(page).click());
  await page.reload();
  await expect(page.locator("#cnt-form")).toBeVisible();
  // a center that carries one of the folded fields shows them open
  await expect(page.locator("#cen-hsi")).toBeVisible();
  await expect(page.locator("#cen-hsi")).toHaveValue("1077");
  const center = (await (await page.request.get("/api/admin/content/centers")).json()).items.find((c) => c.slug === slug);
  expect(center.hsi_institution).toBe("1077");
  await page.fill("#cen-hsi", "");
  await write(page, "/api/admin/content/centers/" + slug, () => saveButton(page).click());
  await page.reload();
  await expect(page.locator("#cen-hsi")).toHaveValue("");
});

test("Centers: a non-number «Institución en HSI» is refused at the field, not a toast", async ({ adminPage: page }) => {
  const slug = "riverside-clinic";
  const endpoint = "/api/admin/content/centers/" + slug;
  await openEditor(page, "/admin/centers?item=" + slug);
  await showCenterMore(page);
  await page.fill("#cen-hsi", "Hospital");
  const refused = page.waitForResponse((r) => new URL(r.url()).pathname === endpoint && r.request().method() !== "GET");
  await saveButton(page).click();
  expect((await refused).status()).toBe(400);
  const field = page.locator("#cen-hsi");
  await expect(field).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#cen-hsi-msg")).toContainText("1086");
  // correcting the field clears the notice and the save goes through
  await page.fill("#cen-hsi", "1086");
  await expect(page.locator("#cen-hsi-msg")).toHaveCount(0);
  await write(page, endpoint, () => saveButton(page).click());
});

test("Services and doctors: empty optional fields say «opcional», the email and the schedule show no gray sample", async ({ adminPage: page }) => {
  // a gray «PAP» or «Más solicitado» read on a screenshot as a configured value
  await openEditor(page, "/admin/services?item=new");
  for (const id of ["#svc-palabras", "#svc-palabras-exc", "#svc-top-label"]) {
    await expect(page.locator(id)).toHaveAttribute("placeholder", "opcional");
  }
  await expect(page.locator("#svc-horario")).not.toHaveAttribute("placeholder", /./);
  await openEditor(page, "/admin/doctors?item=new");
  await expect(page.locator("#med-palabras")).toHaveAttribute("placeholder", "opcional");
  // required where staff log in through the portal: neither a sample nor «opcional»
  await expect(page.locator("#med-email")).not.toHaveAttribute("placeholder", /./);
});

test("Centers: empty optional fields say «opcional»; coordinates take a decimal comma, a bad pair is refused at the field, blank clears them", async ({ adminPage: page }) => {
  const slug = "riverside-clinic";
  const endpoint = "/api/admin/content/centers/" + slug;
  const stored = async () => {
    const c = (await (await page.request.get("/api/admin/content/centers")).json()).items.find((x) => x.slug === slug);
    return [c.lat ?? null, c.lng ?? null];
  };
  await openEditor(page, "/admin/centers?item=" + slug);
  // a gray sample reads as the center's own value on a screenshot
  for (const id of ["#cen-hsi", "#cen-coords", "#cen-jobs"]) {
    await expect(page.locator(id)).toHaveAttribute("placeholder", "opcional");
  }
  // the es-AR decimal keypad types commas
  await showCenterMore(page);
  await page.fill("#cen-coords", "-37,1009, -56,8731");
  await write(page, endpoint, () => saveButton(page).click());
  await page.reload();
  await expect(page.locator("#cen-coords")).toHaveValue("-37.1009, -56.8731");
  // out of range or not two numbers: refused at the field, the stored pair stays
  for (const bad of ["95, 1", "-37.1009 -56.8731"]) {
    await page.fill("#cen-coords", bad);
    const refused = page.waitForResponse((r) => new URL(r.url()).pathname === endpoint && r.request().method() !== "GET");
    await saveButton(page).click();
    expect((await refused).status()).toBe(400);
    await expect(page.locator("#cen-coords")).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator("#cen-coords-msg")).toContainText("-37.1009, -56.8731");
    expect(await stored()).toEqual([-37.1009, -56.8731]);
  }
  await page.fill("#cen-coords", "");
  await write(page, endpoint, () => saveButton(page).click());
  expect(await stored()).toEqual([null, null]);
});

test("Doctors: a new doctor's photo, «Restaurar original», «Volver a procesar» and Quitar each show after a reload", async ({ adminPage: page, cleanup }, testInfo) => {
  await openEditor(page, "/admin/doctors?item=new");
  await page.fill("#med-nombre-pila", "Auditoría");
  await page.fill("#med-apellido", uniqueName("Retrato"));
  await pickPendingPhoto(page, "med");
  await write(page, "/api/admin/content/doctors", () => saveButton(page).click());
  await page.waitForURL((u) => !!u.searchParams.get("item") && u.searchParams.get("item") !== "new");
  const id = new URL(page.url()).searchParams.get("item");
  cleanup(() => removePhotos("doctor", id));
  const photoApi = "/api/admin/doctors/" + id + "/photo";
  await expect.poll(async () => (await (await page.request.get(photoApi)).json()).has_upload).toBe(true);
  const info = async () => {
    await page.reload();
    await expect(page.locator("#cnt-form")).toBeVisible();
    await page.locator('[data-tab="info"]').click();
  };
  await info();
  await expect(page.locator("#med-foto-av")).toHaveAttribute("style", new RegExp("/(avatar-sprite|doctor-photo/" + id + ")"));
  await expect(page.locator("#med-foto-quitar")).toBeVisible();
  // Found by this audit: «Volver a procesar» sat under the upload hint and could not be
  // pressed. Every visible portrait button must be the element that receives its click.
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const button of await page.locator("[data-portrait-action]:visible").all()) {
      await button.scrollIntoViewIfNeeded();
      const hit = await button.evaluate((node) => {
        const box = node.getBoundingClientRect();
        const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return !!top && (top === node || node.contains(top));
      });
      expect(hit, (await button.innerText()) + " is pressable at " + width + " px").toBe(true);
    }
    await page.locator("#med-foto-av").scrollIntoViewIfNeeded();
    await saveShot(testInfo, page, path.join(artifactPath("pinamar-turnos", "forms-audit"), `doctor-photo-${width}.png`));
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  for (const [action, status] of [["restore", /^Original$/], ["process", /^(Procesada|Retocada automáticamente)$/]]) {
    await write(page, photoApi, () => page.click(`[data-portrait-action="${action}"]`));
    await info();
    await expect(page.locator("#med-foto-status")).toHaveText(status);
  }
  await write(page, photoApi, () => page.click("#med-foto-quitar"));
  await info();
  await expect(page.locator("#med-foto-quitar")).toBeHidden();
});

test("Doctors: «Invitar» after creating a doctor and «Desactivar» on its User tab are its account state after a reload", async ({ adminPage: page }) => {
  await openEditor(page, "/admin/doctors?item=new");
  await page.fill("#med-nombre-pila", "Auditoría");
  await page.fill("#med-apellido", "Cuenta");
  await write(page, "/api/admin/content/doctors", () => saveButton(page).click());
  await page.waitForURL((u) => !!u.searchParams.get("item") && u.searchParams.get("item") !== "new");
  await expect(page.locator("#med-account-invite")).toBeVisible();
  await write(page, "/api/admin/users/actions/invite", () => page.click("#med-account-invite"));
  const userTab = async () => {
    await page.reload();
    await expect(page.locator("#cnt-form")).toBeVisible();
    await page.locator('[data-tab="usuario"]').click();
    await expect(page.locator("#med-user-panel")).toBeVisible();
  };
  await userTab();
  await expect(page.locator("#med-user-panel")).toContainText("Invitado");
  await write(page, "/api/admin/users/actions/disable", () => page.click('[data-doctor-user-action="disable"]'));
  await userTab();
  await expect(page.locator("#med-user-panel")).toContainText("Inactiva");
  await expect(page.locator('[data-doctor-user-action="disable"]')).toHaveCount(0);
});

test("Doctors: the «Invitar médico» form adds an invited doctor that the reloaded list shows", async ({ adminPage: page }) => {
  await page.goto("/admin/doctors");
  await page.click("#med-invite-open");
  await page.fill("#med-inv-nombre", "Auditoría");
  await page.fill("#med-inv-apellido", "Invitada");
  await write(page, "/api/admin/content/doctors", () => page.locator('#med-invite-form button[type="submit"]').click());
  await expect(page.locator("#med-invite-result")).not.toBeEmpty();
  await page.reload();
  const row = page.locator("tr", { hasText: "Auditoría Invitada" });
  await expect(row).toBeVisible();
  await expect(row.locator('td[data-col="cuenta"]')).toHaveText("Invitado");
});

test("Doctors: bulk invite and bulk symptom capture on ticked rows are what the reloaded screens show", async ({ adminPage: page }) => {
  const doctors = (await (await page.request.get("/api/admin/content/doctors")).json()).items;
  // Pack doctors only: an incomplete doctor made earlier in this file (no center) is not in
  // the intake catalog, and one such id fails the whole symptom-capture batch with 404
  // (reported in docs/architecture/admin-forms-audit.md).
  const ids = doctors.filter((d) => !d._borrado && d.nombre && String(d._origen || "").startsWith("json"))
    .slice(-2).map((d) => d.id);
  expect(ids).toHaveLength(2);
  await page.goto("/admin/doctors");
  for (const action of ["invite", "enable-intake"]) {
    // Each applied action clears the selection: tick the rows again.
    await expect(page.locator("#med-bulk")).toBeHidden();
    for (const id of ids) await page.locator(`input[data-med-select="${id}"]`).check();
    await page.locator("#med-bulk-action").selectOption(action);
    const endpoint = action === "invite" ? "/api/admin/users/actions/invite" : "/api/admin/intake/settings";
    await write(page, endpoint, () => page.locator("[data-med-bulk-apply]").click());
  }
  await page.reload();
  for (const id of ids) {
    await expect(page.locator("tr", { has: page.locator(`input[data-med-select="${id}"]`) })
      .locator('td[data-col="cuenta"]')).toHaveText("Invitado");
  }
  await page.goto("/admin/entrevistas");
  for (const id of ids) await expect(page.locator(`input.ent-check[data-effective-medico="${id}"]`)).toBeChecked();
});

test("Doctors: bulk symptom capture saves the complete doctors and counts an incomplete one as pending", async ({ adminPage: page }) => {
  // Found by the audit (2026-10-25): a doctor without a center is not in the intake catalog,
  // and the whole batch answered 404 and saved nobody. Decided (pinamar-turnos, 2026-10-25):
  // skip it as «con datos pendientes», like the other bulk actions.
  const created = await page.request.post("/api/admin/content/doctors", { data: { nombre_pila: "Auditoría", apellido: "Incompleta" } });
  expect(created.status(), await created.text()).toBe(200);
  const incomplete = (await created.json()).item.id;
  const pack = (await (await page.request.get("/api/admin/content/doctors")).json()).items
    .find((d) => !d._borrado && String(d._origen || "").startsWith("json") && !d.entrevista_habilitada).id;
  await page.goto("/admin/doctors");
  for (const id of [incomplete, pack]) await page.locator(`input[data-med-select="${id}"]`).check();
  await page.locator("#med-bulk-action").selectOption("enable-intake");
  const intakeOf = async (id) => ((await (await page.request.get("/api/admin/content/doctors")).json()).items
    .find((d) => d.id === id) || {}).entrevista_habilitada;
  expect(await intakeOf(pack)).toBeFalsy();
  await write(page, "/api/admin/intake/settings", () => page.locator("[data-med-bulk-apply]").click());
  await expect(page.locator("#toast")).toContainText("1 con datos pendientes");
  await page.goto("/admin/entrevistas");
  await expect(page.locator(`input.ent-check[data-effective-medico="${pack}"]`)).toBeChecked();
  expect(await intakeOf(pack), "the complete doctor was saved").toBe(true);
});

test("Doctors: the Síntomas tab button reopens in the state it was saved", async ({ adminPage: page }) => {
  const id = "wu-carlos";
  // One button: «Activar captura de síntomas» (data-…="1") while off,
  // «Desactivar» (data-…="0") while on.
  for (const enabled of [true, false]) {
    await openEditor(page, "/admin/doctors?item=" + id);
    await page.locator('[data-tab="intake"]').click();
    const want = page.locator(`#med-intake-panel [data-med-intake-enabled="${enabled ? 1 : 0}"]`);
    const undo = page.locator(`#med-intake-panel [data-med-intake-enabled="${enabled ? 0 : 1}"]`);
    await expect(want.or(undo)).toBeVisible();
    if (await want.isVisible()) await write(page, "/api/admin/intake/settings", () => want.click());
    await page.reload();
    await expect(page.locator("#cnt-form")).toBeVisible();
    await page.locator('[data-tab="intake"]').click();
    await expect(undo).toBeVisible();
    await expect(want).toHaveCount(0);
  }
});

test("Entrevistas: a shift-click range and «select all» are the checkbox states after a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/entrevistas");
  const boxes = page.locator("input.ent-check[data-effective-medico]");
  await expect(boxes.first()).toBeVisible();
  const first = boxes.nth(0), third = boxes.nth(2);
  const firstId = await first.getAttribute("data-effective-medico");
  const thirdId = await third.getAttribute("data-effective-medico");
  const secondId = await boxes.nth(1).getAttribute("data-effective-medico");
  const start = await first.isChecked();
  await write(page, "/api/admin/intake/settings", () => first.click());
  await expect(page.locator(`input.ent-check[data-effective-medico="${firstId}"]:not([disabled])`)).toBeVisible();
  await write(page, "/api/admin/intake/settings",
    () => page.locator(`input.ent-check[data-effective-medico="${thirdId}"]`).click({ modifiers: ["Shift"] }));
  await page.reload();
  for (const id of [firstId, secondId, thirdId]) {
    await expect(page.locator(`input.ent-check[data-effective-medico="${id}"]`)).toBeChecked({ checked: !start });
  }
  await expect(page.locator("#ent-all")).toBeVisible();
  const all = await page.locator("#ent-all").isChecked();
  await write(page, "/api/admin/intake/settings", () => page.locator("#ent-all").setChecked(!all));
  await page.reload();
  const states = await boxes.evaluateAll((nodes) => nodes.map((node) => node.checked));
  expect(states.every((checked) => checked === !all)).toBe(true);
  await write(page, "/api/admin/intake/settings", () => page.locator("#ent-all").setChecked(false));
});

test("Preparations: a new one, Deshabilitar and Habilitar each show after a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/preparations");
  await page.click("#pa-nueva");
  await page.fill("#pa-nombre", "Preparación de auditoría");
  await page.fill("#pa-resumen", "Resumen sintético.");
  await page.fill("#pa-match", "auditoria sintetica");
  await write(page, "/api/admin/content/preparations", () => page.click("#pa-guardar"));
  await page.waitForURL((u) => !!u.searchParams.get("item"));
  await page.reload();
  await expect(page.locator("#pa-nombre")).toHaveValue("Preparación de auditoría");
  await expect(page.locator("#pa-deshabilitar")).toBeVisible();

  await page.goto("/admin/preparations?item=glucemia");
  await expect(page.locator("#pa-deshabilitar")).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/content/preparations/glucemia", () => page.click("#pa-deshabilitar"));
  await page.goto("/admin/preparations?item=glucemia");
  await expect(page.locator("#pa-habilitar")).toBeVisible();
  await expect(page.locator("#pa-deshabilitar")).toHaveCount(0);
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/content/preparations/glucemia/restaurar", () => page.click("#pa-habilitar"));
  await page.goto("/admin/preparations?item=glucemia");
  await expect(page.locator("#pa-deshabilitar")).toBeVisible();
  await expect(page.locator("#pa-habilitar")).toHaveCount(0);
});

test("Catalog lists: service and doctor filters, the sort column and the centers' column choice survive a reload", async ({ adminPage: page }) => {
  const choose = async (selector, pick) => {
    const value = await page.locator(selector + " option").nth(pick).getAttribute("value");
    const saved = page.waitForResponse(isPreferencesPut);
    await page.locator(selector).selectOption(value);
    expect((await saved).ok()).toBe(true);
    return value;
  };
  await page.goto("/admin/services");
  const modo = await choose("#svc-f-modo", 1);
  const header = page.locator("#cnt-thead-servicios th[data-sort]").nth(1);
  const sortKey = await header.getAttribute("data-sort");
  const sorted = page.waitForResponse(isPreferencesPut);
  await header.click();
  expect((await sorted).ok()).toBe(true);
  await page.reload();
  await expect(page.locator("#svc-f-modo")).toHaveValue(modo);
  await expect(page.locator(`#cnt-thead-servicios th[data-sort="${sortKey}"]`)).toHaveClass(/\bon\b/);

  await page.goto("/admin/doctors");
  const especialidad = await choose("#med-f-especialidad", 1);
  await page.reload();
  await expect(page.locator("#med-f-especialidad")).toHaveValue(especialidad);

  await page.goto("/admin/centers");
  // The shared column picker of admin-table.mjs (one picker for every list).
  const picker = '#sec-centros [data-tblcol-grupo="catalog-centros"]';
  await page.click(`${picker} [data-tblcol-gear]`);
  const box = page.locator(`${picker} input[data-tblcol]`).last();
  const key = await box.getAttribute("data-tblcol");
  const shown = await box.isChecked();
  const columns = page.waitForResponse(isPreferencesPut);
  await box.setChecked(!shown);
  expect((await columns).ok()).toBe(true);
  await page.reload();
  await page.click(`${picker} [data-tblcol-gear]`);
  await expect(page.locator(`${picker} input[data-tblcol="${key}"]`)).toBeChecked({ checked: !shown });
});

// One column picker on every list (the product owner 28.07, 05.09): Médicos uses the shared picker, at the
// right end of its toolbar before the create actions, its button is the Icons8 file, and the
// choice survives a reload. Screenshots of the open picker at phone and desktop.
test("Catalog lists: Médicos chooses its columns with the shared picker, saved across a reload", async ({ adminPage: page }, testInfo) => {
  const picker = '#sec-medicos .cnt-toolbar-right [data-tblcol-grupo="catalog-medicos"]';
  await page.goto("/admin/doctors");
  await expect(page.locator(picker)).toHaveCount(1);
  await expect(page.locator("#sec-medicos svg")).toHaveCount(0);
  const icon = page.locator(`${picker} [data-tblcol-gear] .adm-icon`);
  expect(await icon.evaluate((el) => getComputedStyle(el).maskImage || getComputedStyle(el).webkitMaskImage))
    .toContain("table-columns-sf-regular.svg");
  await page.click(`${picker} [data-tblcol-gear]`);
  const box = page.locator(`${picker} input[data-tblcol="matricula"]`);
  const shown = await box.isChecked();
  const saved = page.waitForResponse(isPreferencesPut);
  await box.setChecked(!shown);
  expect((await saved).ok()).toBe(true);
  await expect(page.locator('#cnt-thead-medicos th[data-col="matricula"]')).toHaveCount(shown ? 0 : 1);
  await page.reload();
  await expect(page.locator('#cnt-thead-medicos th[data-col="matricula"]')).toHaveCount(shown ? 0 : 1);
  const dir = artifactPath("pinamar-turnos", "progressive-disclosure", "round2");
  for (const [label, width, height] of [["desktop", 1440, 900], ["phone", 390, 844]]) {
    await page.setViewportSize({ width, height });
    const pop = page.locator(`${picker} [data-tblcol-pop]`);
    if (!(await pop.isVisible())) await page.click(`${picker} [data-tblcol-gear]`);
    await expect(pop).toBeVisible();
    const area = await stableBox(pop, `${label}: the column list`);
    expect(area.x, `${label}: the column list opens inside the screen`).toBeGreaterThanOrEqual(0);
    expect(area.x + area.width, `${label}: the column list opens inside the screen`).toBeLessThanOrEqual(width);
    await saveShot(testInfo, page, path.join(dir, `medicos-columns-${label}.png`));
  }
});
