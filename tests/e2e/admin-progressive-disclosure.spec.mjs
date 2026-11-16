// Progressive disclosure on Solicitudes (the product owner, 2026-10-26: «if there's no content, why to
// show all the ui? all over the admin panel»). Real controls → ASGI → isolated PostgreSQL:
// one app with no request at all, one with the fixture's pending requests. The rule itself is
// static/platform/admin-filter-controls.mjs (unit tests: tests/admin-disclosure.test.mjs).
// Screenshots at phone and desktop width go to the artifacts tree as review evidence.
import { mkdirSync } from "node:fs";
import { test, expect, loginAdmin, artifactPath } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_progressive_disclosure" } });
test.describe.configure({ mode: "serial" });

const SHOTS = artifactPath("pinamar-turnos", "progressive-disclosure");
const SHOTS2 = artifactPath("pinamar-turnos", "progressive-disclosure", "round2");
const STAGE = "after";
const SIZES = [["phone", 390, 844], ["desktop", 1440, 900]];

async function shots(page, name, dir = SHOTS) {
  mkdirSync(dir, { recursive: true });
  for (const [label, width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await page.screenshot({ path: `${dir}/${name}-${STAGE}-${label}.png` });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

const painted = "#list :is(.sol-empty, .adm-empty-state, [data-sol-row])";
const dataTools = ["#q", "#f-estado", "#f-medico", "#f-sug", "[data-ai-notice]", "#adm-agrupar", "[data-tblcol-gear]", ".sol-cols"];

test("Solicitudes with no request shows the title, «Crear turno» and what will appear here — no data tools", async ({ adminPage: page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/solicitudes");
  await expect(page.locator(painted).first()).toBeVisible();
  await shots(page, "solicitudes-empty");
  await expect(page.locator("#sec-solicitudes")).toHaveAttribute("data-disclosure", "empty");
  await expect(page.locator('#list [data-empty-state="empty"]')).toContainText("Todavía no hay solicitudes.");
  for (const selector of dataTools) await expect(page.locator(selector)).toBeHidden();
  await expect(page.locator("#adm-crear-turno")).toBeVisible();
});

// Review evidence for the sibling screens on the same empty database: Pacientes has nobody
// (title, «Nuevo paciente» and the empty state only), Usuarios lists only the admin (one role,
// one status: no filters), Médicos lists the pack's doctors. Their own assertions live in the
// node suites and admin-screen.spec.mjs; here only the empty Pacientes is checked.
test("Pacientes, Usuarios and Médicos on the same database: screenshots, no data tools on an empty Pacientes", async ({ adminPage: page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/pacientes");
  await expect(page.locator('#sec-pacientes [data-empty-state="empty"]')).toBeVisible();
  await expect(page.locator("#pac-q")).toHaveCount(0);
  await shots(page, "pacientes-empty");
  await page.goto("/admin/users");
  await expect(page.locator("#sec-usuarios :is(table, [data-empty-state])").first()).toBeVisible();
  await shots(page, "usuarios-one");
  await page.goto("/admin/doctors");
  // The folded «Invitar médico» form comes first in the DOM and is hidden.
  await expect(page.locator("#sec-medicos").locator("table, [data-empty-state], form").filter({ visible: true }).first()).toBeVisible();
  await shots(page, "medicos");
  // Round 2: the short user list has no filter selects and no phone «Filtros»
  // toggle (FILTER_MIN_ROWS); the «Mis pacientes» doctor picker is a plain .adm-sel.
  await page.goto("/admin/users");
  await expect(page.locator("#sec-usuarios table").first()).toBeVisible();
  await expect(page.locator("#usr-q")).toBeVisible();
  for (const selector of ["#usr-f-rol", "#usr-f-estado"]) await expect(page.locator(selector)).toBeHidden();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#sec-usuarios [data-filter-toggle]")).toBeHidden();
  await shots(page, "usuarios-short", SHOTS2);
  await page.goto("/admin/my-patients");
  const picker = page.locator("#dp-medico");
  await expect(picker).toBeVisible();
  expect(await picker.evaluate((el) => getComputedStyle(el).borderTopColor)).toBe("rgba(0, 0, 0, 0)");
  // The picker is not a filter: on the phone it stays in sight, no «Filtros» toggle.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(picker).toBeVisible();
  await expect(page.locator("#sec-mis-pacientes [data-filter-toggle]")).toHaveCount(0);
  await shots(page, "mis-pacientes-picker", SHOTS2);
});

test("Solicitudes with requests shows its tools; a search that hides every row keeps them and clears back", async ({ launchApp, browser }) => {
  const app = await launchApp({ dbName: "sana_turnos_test_e2e_progressive_disclosure_rows", requests: 7 });
  const context = await browser.newContext({ baseURL: app.baseUrl, viewport: { width: 1440, height: 900 } });
  try {
    const page = await context.newPage();
    await loginAdmin(page, app.baseUrl);
    await page.goto("/admin/solicitudes");
    await expect(page.locator("#list [data-sol-row]").first()).toBeVisible();
    await shots(page, "solicitudes-rows");
    await expect(page.locator("#sec-solicitudes")).toHaveAttribute("data-disclosure", "data");
    await expect(page.locator("#q")).toBeVisible();
    // No AI notice over the list: it rides with the reading of an open request (2026-11-09).
    await expect(page.locator("#sec-solicitudes [data-ai-notice]")).toHaveCount(0);
    await expect(page.locator(".sol-cols")).toBeVisible();
    // Seven requests: a list read at a glance — no doctor/specialty/service/
    // centre/analysis/suggestion select (FILTER_MIN_ROWS = 10), labels are
    // the field names; on the phone the «Filtros» toggle goes with them
    // unless the state select (two states) is left.
    for (const id of ["#f-medico", "#f-especialidad", "#f-servicio", "#f-centro", "#f-verdict", "#f-sug"]) {
      await expect(page.locator(id)).toBeHidden();
    }
    await expect(page.locator("#f-medico option").first()).toHaveText("Médico");
    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.locator("#f-estado").evaluate((el) => el.hidden)) await expect(page.locator("#adm-filter-toggle")).toBeHidden();
    await page.setViewportSize({ width: 1440, height: 900 });
    await shots(page, "solicitudes-short", SHOTS2);

    await page.fill("#q", "nadie-se-llama-asi");
    const noMatch = page.locator('#list [data-empty-state="no-match"]');
    await expect(noMatch).toBeVisible();
    await expect(page.locator("#q")).toBeVisible();
    await expect(page.locator("[data-ai-notice]")).toBeHidden();
    await expect(page.locator(".sol-cols")).toHaveCount(0);
    await noMatch.locator("[data-clear-filters]").click();
    await expect(page.locator("#list [data-sol-row]").first()).toBeVisible();
    await expect(page.locator("#q")).toHaveValue("");

    await page.goto("/admin/pacientes");
    await expect(page.locator("#sec-pacientes :is(table, [data-empty-state])").first()).toBeVisible();
    await shots(page, "pacientes-rows");
  } finally {
    await context.close();
  }
});
