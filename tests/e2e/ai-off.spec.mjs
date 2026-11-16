// An installation with no AI key (salud.pinamar.gob.ar until its OPENROUTER_API_KEY is loaded):
// the chat entrance is not drawn, and «Subí tu orden» never answers «Hiciste varias consultas
// seguidas» -- it saves the order and shows where to book. The app starts with no key and
// TURNOS_FAKE_AI blanked (the harness turns the fake on by default).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect, saveShot, artifactPath } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_ai_off", env: { TURNOS_FAKE_AI: "", OPENROUTER_API_KEY: "" } },
});

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const REFERRAL = path.join(ROOT, "packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg");
const EVIDENCE = artifactPath("pinamar-turnos", "chat-ia-2026-10-28");
const VIEWPORTS = { phone: { width: 390, height: 844 }, desktop: { width: 1280, height: 900 } };

test("no AI key: the home page draws no chat entrance", async ({ app, page }) => {
  expect((await page.request.get(`${app.baseUrl}/api/ai-status`)).ok()).toBeTruthy();
  const status = await (await page.request.get(`${app.baseUrl}/api/ai-status`)).json();
  expect(status.disponible).toBe(false);
  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(viewport);
    await page.goto("/", { waitUntil: "load" });
    await expect(page.locator("footer")).toBeVisible();
    await expect(page.locator("#stz-chat-launcher")).toHaveCount(0);
    await expect(page.locator("#stz-chat-panel")).toHaveCount(0);
    await expect(page.locator(".dir-card--sk")).toHaveCount(0);
    await saveShot(test.info(), page, path.join(EVIDENCE, `home-sin-chat-${name}.png`));
  }
});

test("no AI key: reading an order many times in a row is never rate-limited and ends in where to book", async ({ app, page }) => {
  test.setTimeout(120_000);
  await page.goto("/preparacion", { waitUntil: "load" });
  const body = JSON.stringify({
    servicio_id: "electrocardiograma-hospital",
    orden: { filename: "orden.jpg", mime: "image/jpeg", data_b64: fs.readFileSync(REFERRAL).toString("base64") },
  });
  // The old per-IP limit was 10 per hour: twelve in a row from one browser would have been refused.
  const statuses = [];
  for (let i = 0; i < 12; i += 1) {
    const r = await page.request.post(`${app.baseUrl}/api/analyze-order`, {
      headers: { "Content-Type": "application/json" }, data: body,
    });
    statuses.push(r.status());
  }
  expect(statuses.filter((s) => s === 429)).toEqual([]);

  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(viewport);
    // The standalone reader says plainly that it is off and links to the services.
    await page.goto("/preparacion", { waitUntil: "load" });
    await expect(page.getByText("El lector automático de órdenes no está disponible")).toBeVisible();
    await expect(page.getByRole("link", { name: "Ver servicios y sacar turno" })).toBeVisible();
    await saveShot(test.info(), page, path.join(EVIDENCE, `preparacion-sin-ia-${name}.png`));

    // The booking wizard's «Subí tu orden» (the page of the reference-deployment counterpart's screenshot) takes the file and moves on.
    await page.goto("/service/electrocardiograma-hospital/book", { waitUntil: "load" });
    await expect(page.locator("#svc-title")).toHaveText("Subí tu orden");
    await page.locator("#wz-file").setInputFiles(REFERRAL);
    await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Hiciste varias consultas seguidas")).toHaveCount(0);
    await saveShot(test.info(), page, path.join(EVIDENCE, `subi-tu-orden-sin-ia-${name}.png`));
  }
});
