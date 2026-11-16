// A new center created with a photo chosen BEFORE the first save must end up
// with that photo (staff feedback, 2026-10-14: «Foto de Oncología» did not load).
//
// Port of tests/e2e/center-photo-create.e2e.mjs (2 tests, same titles, same order; the
// second one is independent of the first and uses an existing center).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//   page replaces the hand-made context (viewport 390x900 via test.use) and the sign-in goes
//   through the shared loginAdmin() helper. The photo fixture path (CENTER_PHOTO_FIXTURE or
//   tests/fixtures/center-photo-jfxx.jpeg) is resolved exactly as before.
// - The fixed sleeps are gone. Test 1: after the save redirects to the list (same 15 s wait),
//   the created center and its stored photo are polled from the API instead of sleeping 1.5 s.
//   Test 2: «stored on pick» is polled for the same 3 s the original slept; when it is not
//   stored by then, the explicit Save is pressed and the stored state is polled again (3 s
//   before, now up to the default expect timeout, so the check can only get less flaky).
// - Waits are web-first (retrying expects with the original 10 s / 8 s timeouts for the
//   preview avatar and the reopened editor). Locators left as CSS: the editor ids (#cen-*,
//   #cnt-form) and the avatar's inline `style` (the photo preview IS a background-image).
//
// Storage: center photos land in the checkout's data/centro-fotos/ (shared by every run on
// the machine, not by the throwaway database). Each test removes the photo it stores, and
// the second one checks there is none BEFORE picking -- otherwise a photo stored by an
// earlier run answers has_upload=true at once and the test passes without storing anything.
// The first test's center name carries a random tag, so the slug the server derives from it
// (and with it the photo's file name) is not shared by two copies of the test running in
// different workers (same fix as admin-forms-audit-catalog, 2026-11-06). The second test
// creates its own center through the API, with a tagged slug, before opening its editor:
// on the pack's fixed riverside-clinic, ten copies on four workers failed five times (one
// copy's cleanup removed the photo another had just stored).
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { test, expect, loginAdmin, ADMIN_USER, ADMIN_PASS, ROOT } from "./lib/fixtures.mjs";

const PHOTO = process.env.CENTER_PHOTO_FIXTURE
  || new URL("../fixtures/center-photo-jfxx.jpeg", import.meta.url).pathname;

/** Delete the stored upload of center `slug` (every extension doctor_photos accepts). */
async function removeCenterPhoto(slug) {
  for (const ext of ["jpg", "jpeg", "png", "webp"]) {
    await fs.rm(path.join(ROOT, "data", "centro-fotos", slug + "." + ext), { force: true });
  }
}

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_center_photo_create" },
  viewport: { width: 390, height: 900 },
});

/** Collect failed `/photo` responses of the page (the original's `failures` listener). */
function watchPhotoFailures(page) {
  const failures = [];
  page.on("response", (response) => {
    if (response.url().includes("/photo") && !response.ok()) failures.push(response.status() + " " + response.url());
  });
  return failures;
}

test("a new center keeps the photo picked before saving", async ({ page, app, cleanup }) => {
  const failures = watchPhotoFailures(page);
  const tag = randomUUID().slice(0, 8);
  let slug = null;
  cleanup(() => slug && removeCenterPhoto(slug));
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/account");

  await page.goto("/admin/centers?item=new", { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  await page.fill("#cen-nombre", "Hospital de día Oncología Prueba " + tag);
  await page.fill("#cen-direccion", "Calle Sintética 1");
  await page.setInputFiles("#cen-foto-input", PHOTO);
  await expect(page.locator('#cen-foto-av[style*="background-image"]')).toBeVisible({ timeout: 10000 });
  await page.locator('button[type="submit"]').click();
  await page.waitForURL((url) => url.pathname === "/admin/centers" && !url.search, { timeout: 15000 });

  const findCreated = async () => {
    const list = await (await page.request.get("/api/admin/content/centers")).json();
    return list.items.find((row) => (row.name || row.nombre || "").includes("Oncología Prueba " + tag));
  };
  await expect.poll(async () => !!(await findCreated()), { message: "the center exists" }).toBe(true);
  const created = await findCreated();
  slug = created.slug;
  const photoUrl = "/api/admin/centers/" + encodeURIComponent(created.slug) + "/photo";
  expect((await page.request.get(photoUrl)).status()).toBe(200);
  await expect.poll(async () => (await (await page.request.get(photoUrl)).json()).has_upload,
    { message: "the photo was stored: " + failures.join(", ") }).toBe(true);
  expect(failures).toEqual([]);
});

test("an existing center keeps a photo picked in its editor", async ({ page, app, cleanup }) => {
  const failures = watchPhotoFailures(page);
  const slug = "photo-existing-" + randomUUID().slice(0, 8);
  cleanup(() => removeCenterPhoto(slug));
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/account");
  expect((await page.request.post("/api/admin/content/centers", {
    data: { slug, name: "Centro Existente Foto" },
  })).status()).toBe(200);

  const hasUpload = async () => (await (await page.request.get("/api/admin/centers/" + slug + "/photo")).json()).has_upload;
  expect(await hasUpload(), "no photo left over before the pick").toBe(false);
  await page.goto("/admin/centers?item=" + slug, { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  await page.setInputFiles("#cen-foto-input", PHOTO);
  // Stored on pick (the editor uploads at once) within the window the original slept.
  const storedOnPick = await expect.poll(hasUpload, { timeout: 3000 }).toBe(true).then(() => true, () => false);
  const toasts = await page.locator("body").innerText();
  if (!storedOnPick) {
    // Not stored on pick: it must at least be stored by the explicit save.
    await page.locator('button[type="submit"]').click();
  }
  await expect.poll(hasUpload, {
    message: "photo stored (failures: " + failures.join(", ") + "; page: " + toasts.slice(0, 200).replace(/\s+/g, " ") + ")",
  }).toBe(true);
  expect(failures).toEqual([]);
  // Reopening the editor must SHOW the stored photo and offer to remove it
  // (a center is keyed by `slug`, not `id`: the editor used to treat every
  // existing center as new and never read the server state).
  await page.goto("/admin/centers?item=" + slug, { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  await expect(page.locator("#cen-foto-av")).toHaveAttribute("style", /(avatar-sprite|center-photo)\//, { timeout: 8000 });
  await expect(page.locator("#cen-foto-quitar"), "remove-photo link is offered").toBeVisible();
});
