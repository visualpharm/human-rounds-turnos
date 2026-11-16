// Every place a turno is made in the panel offers ONE «Cargar en HSI» control
// (the product owner, 2026-10-27, d1122568): the queue row, «Turno creado», the «Recién
// aprobadas» card above Solicitudes and the detail of an approved
// request. The REAL
// content script and stylesheet of extensions/hsi-humanizador paint it against
// the real ASGI app; only the Chrome API is stubbed — `sendMessage` records the
// load the extension hands to its service worker, and the test plays the
// service worker's answers (`{tipo:"estado"}`) back through the listener.
//
// One test per entry: the button, the turning icon while it works, the check
// once HSI saved it; with «Cargar en HSI apenas apruebo o creo un turno» on,
// «Envío automático a HSI» without a click and one load at a time; without the
// extension, the DNI copied and the install link.
//
// No HSI is reached: the stub stands where the service worker would open it.
// The extension's own end to end against the TEST copy of HSI is
// extensions/hsi-humanizador/test/e2e-hsi.mjs.
import { readFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { test, expect, saveShot, ROOT } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_hsi_sync_entries", queue: true, requests: 7 } });
// The approvals of one test are the rows the next one must not find pending.
test.describe.configure({ mode: "serial" });

const EXT = join(ROOT, "extensions", "hsi-humanizador");
const SHARED_JS = readFileSync(join(EXT, "lib", "shared.js"), "utf8");
const CONTENT_JS = readFileSync(join(EXT, "content-turnos.js"), "utf8");
const CONTENT_CSS = readFileSync(join(EXT, "content-turnos.css"), "utf8");
const SHOTS = join(homedir(), ".claude/artifacts/pinamar-turnos/hsi-sync-entries");
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

const isApprove = (r) => /\/api\/admin\/requests\/\d+\/approve$/.test(new URL(r.url()).pathname)
  && r.request().method() === "POST";

// What the manifest does on /admin/*: the stylesheet and the two content
// scripts, with the Chrome API answered in memory.
async function installExtension(page, { auto = false } = {}) {
  await page.addStyleTag({ content: CONTENT_CSS });
  await page.evaluate((cargarAlAprobar) => {
    window.__hrSent = [];
    window.chrome = {
      runtime: {
        id: "stub",
        sendMessage: (msg) => { window.__hrSent.push(msg); return Promise.resolve({ ok: true }); },
        onMessage: { addListener: (fn) => { window.__hrListen = fn; } },
      },
      storage: {
        local: { get: () => Promise.resolve({ turnosCargados: {}, modo: "boton", cargarAlAprobar }), set: () => Promise.resolve() },
        onChanged: { addListener: () => {} },
      },
    };
  }, auto);
  await page.addScriptTag({ content: SHARED_JS });
  await page.addScriptTag({ content: CONTENT_JS });
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.hrHumanizador)).toBe("1");
}

// The ids the extension handed to its service worker, in order.
const loads = (page) => page.evaluate(() => window.__hrSent.filter((m) => m.tipo === "cargar").map((m) => String(m.turno.id)));

// The service worker's answer about one turno (what background.js sends back).
const answer = (page, id, estado, detalle) =>
  page.evaluate(([id, estado, detalle]) => window.__hrListen({ tipo: "estado", id, estado, detalle }), [String(id), estado, detalle || ""]);

// A row may lack what HSI needs (sex, birth date): the extension asks for it
// under the button before it loads. Answer it, as the desk would.
async function answerIfAsked(page) {
  const form = page.locator(".hr-hsi-datos");
  if (!(await form.count())) return;
  const sexo = form.locator('select[name="sexo"]');
  if (await sexo.count()) await sexo.selectOption("F");
  const nacimiento = form.locator('input[name="nacimiento"]');
  if (await nacimiento.count()) await nacimiento.fill("1980-05-04");
  const paciente = form.locator('input[name="paciente"]');
  if (await paciente.count()) await paciente.fill("Ana Prueba");
  await form.locator(".hr-hsi-seguir").click();
}

// Until the turno has been handed to HSI, answering the extension's question
// on the way if it asks one.
async function untilLoaded(page, id) {
  await expect.poll(async () => {
    await answerIfAsked(page);
    return (await loads(page)).includes(String(id));
  }, { timeout: 15000 }).toBe(true);
}

// The turning icon is the state's ::before mask with an animation; the check
// is the saved state's mask, without one.
const iconOf = (control) => control.evaluate((b) => {
  const s = getComputedStyle(b, "::before");
  return { mask: s.maskImage || s.webkitMaskImage || "", animation: s.animationName };
});

async function expectTurning(control) {
  await expect.poll(async () => {
    const icon = await iconOf(control);
    return icon.mask.includes("hsi-sync") && icon.animation !== "none";
  }).toBe(true);
}

async function expectChecked(control) {
  await expect(control).toHaveAttribute("data-estado", "guardado");
  await expect(control).toHaveText("Guardado en HSI");
  await expect(control).toBeDisabled();
  const icon = await iconOf(control);
  expect(icon.mask, "the saved state shows the check").toContain("hsi-checkmark");
  expect(icon.animation, "the check does not turn").toBe("none");
}

// How far a control runs past the box that holds it (0 when it fits).
const overflowOf = (control, box) => control.evaluate((b, sel) => {
  const host = b.closest(sel);
  const r = b.getBoundingClientRect(), h = host.getBoundingClientRect();
  return Math.max(0, Math.round(r.right - h.right), Math.round(h.left - r.left));
}, box);

// The boxes around an element that cut it: an ancestor up to the list whose
// overflow hides (not scrolls) and that the element runs past.
const cutBy = (el) => el.evaluate((n) => {
  const r = n.getBoundingClientRect(), out = [];
  for (let a = n.parentElement; a && !a.matches(".sol-queue"); a = a.parentElement) {
    const s = getComputedStyle(a);
    if (!/hidden|clip/.test(s.overflowX + " " + s.overflowY)) continue;
    const b = a.getBoundingClientRect();
    if (r.left < b.left - 0.5 || r.right > b.right + 0.5 || r.top < b.top - 0.5 || r.bottom > b.bottom + 0.5) out.push(a.className);
  }
  return out;
});

// The cells of a list row that end past the list or sit off their header.
// A cell and its header button share a grid track: the track starts at the
// box's left edge minus its own left margin (the header button and the status
// dot both hang 5px out on purpose).
const rowOutside = (row) => row.evaluate((r) => {
  const queue = r.closest(".sol-queue"), q = queue.getBoundingClientRect(), out = [];
  const track = (n) => n.getBoundingClientRect().left - parseFloat(getComputedStyle(n).marginLeft || "0");
  for (const c of r.children) {
    const key = [...c.classList].find((k) => k.startsWith("sol-cell-"));
    if (!key || getComputedStyle(c).display === "none") continue;
    const b = c.getBoundingClientRect();
    if (b.right > q.right + 0.5) out.push(`${key}: ${Math.round(b.right - q.right)}px past the list`);
    const h = queue.querySelector(".sol-cols .sol-ch-" + key.slice("sol-cell-".length));
    if (h && getComputedStyle(h).display !== "none" && Math.abs(track(h) - track(c)) > 1.5) out.push(`${key}: not under its header`);
  }
  return out;
});

async function shot(testInfo, page, name) {
  mkdirSync(SHOTS, { recursive: true });
  await saveShot(testInfo, page, join(SHOTS, name));
}

async function openQueue(page) {
  await page.goto("/admin/cola", { waitUntil: "load" });
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  // One list (2026-11-08): the unassigned rows are there, nothing to open.
  await expect(page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="_sin_medico"]').first()).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#sec-cola tr[data-cola-row]").first()).toBeVisible({ timeout: 12000 });
}

async function openPending(page) {
  await page.goto("/admin/solicitudes", { waitUntil: "load" });
  await expect(page.locator("#list [data-sol-row]").first()).toBeVisible({ timeout: 12000 });
  if (await page.locator("#f-estado").inputValue() !== "pendiente") await page.locator("#f-estado").selectOption("pendiente");
}

// A request's card opens with the server's suggested time (the same one for every card) and fills the
// doctor's real agenda a moment later (admin-app.js poblarTurnoSel). Approving before that sends the stale
// suggestion, so the second approval of the file answered 409 whenever the agenda was slower than the click
// (a loaded runner). Wait for the agenda, then take this approval's OWN slot: the `slot`-th time counted
// from the third day offered on (a doctor's short day may hold fewer times than `slot`). Each approval of
// the file uses a different `slot`, so none can meet another's.
async function ownSlot(detail, slot) {
  const select = detail.locator("select.turno-sel");
  await expect.poll(() => select.locator("option").count(), { message: "the doctor's agenda is loaded" }).toBeGreaterThan(2);
  const values = await select.locator("option").evaluateAll((os) => os.map((o) => o.value).filter((v) => v && v !== "__otro"));
  const days = [...new Set(values.map((v) => v.slice(0, 10)))];
  expect(days.length, "the agenda offers at least three days").toBeGreaterThanOrEqual(3);
  const mine = values.filter((v) => v.slice(0, 10) >= days[2])[slot];
  expect(mine, `slot ${slot} from the third day on`).toBeTruthy();
  await select.selectOption(mine);
  await expect(detail.locator("input.turno")).toHaveValue(mine);
}

async function approveRow(page, nombre, slot) {
  await page.locator("#list .sol-row", { hasText: nombre }).first().click();
  const detail = page.locator("aside.sol-det[data-id]");
  await expect(detail).toBeVisible();
  const id = await detail.getAttribute("data-id");
  await ownSlot(detail, slot);
  const approved = page.waitForResponse(isApprove);
  await detail.locator('[data-act="aprobar"]').click();
  expect((await approved).status(), "the approval is accepted").toBe(200);
  return id;
}

for (const [label, viewport] of [["telefono", PHONE], ["escritorio", DESKTOP]]) {
  test(`queue row (${label}): «Cargar en HSI» turns while it loads, then shows the check`, async ({ adminPage: page }, testInfo) => {
    await page.setViewportSize(viewport);
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await openQueue(page);
    await installExtension(page);
    const row = page.locator("#sec-cola tr[data-cola-row]").first();
    const id = await row.getAttribute("data-cola-row");
    const button = row.locator(`[data-hr-hsi="${id}"]`);
    await expect(button).toHaveText("Cargar en HSI");
    await button.click();
    await expect(button).toHaveAttribute("data-estado", /abriendo|preguntando/);
    await untilLoaded(page, id);
    await expect(button).toHaveAttribute("data-estado", "abriendo");
    await expectTurning(button);
    await shot(testInfo, page, `cola-${label}-cargando.png`);
    // The longest wording a row shows fits its cell.
    await answer(page, id, "alta", "HSI no tiene a este paciente: terminá el alta y sigue solo.");
    await expect(button).toHaveText("Terminá el alta en HSI");
    expect(await overflowOf(button, "td"), "«Terminá el alta en HSI» stays inside its cell").toBe(0);
    await answer(page, id, "guardado");
    await expectChecked(button);
    await shot(testInfo, page, `cola-${label}-guardado.png`);
    expect(errors).toEqual([]);
  });
}

// A repaint of the list takes the row's control away for a beat (decorar() puts it back). When the
// press asks its question in that beat, the press used to be dropped: the state fell back to
// «reposo», nothing was loaded, and the operator's click was lost. Here the control is removed
// the moment it is pressed (the test's probabilistic pre-fix red is the panel's own repaint
// sometimes putting it back first; the fixed code passes either way).
test("queue row: a repaint that takes the control away does not drop the press", async ({ adminPage: page }) => {
  await page.setViewportSize(PHONE);
  await openQueue(page);
  await installExtension(page);
  // A row whose patient has no sex on file: pressing it makes the extension ask (the seed's first row has one).
  const row = page.locator("#sec-cola tr[data-cola-row]", { hasText: "Turno Ausente" });
  const id = await row.getAttribute("data-cola-row");
  const button = row.locator(`[data-hr-hsi="${id}"]`);
  await expect(button).toHaveText("Cargar en HSI");
  await page.evaluate(() => {
    // Capture phase on the same node, registered after the extension's own listener: it runs
    // once the press has started (the extension stops the event, so a bubble listener never would).
    document.addEventListener("click", (e) => {
      const b = e.target.closest && e.target.closest("[data-hr-hsi]");
      if (!b) return;
      const box = b.closest(".hr-hsi") || b;
      // The row is mid-repaint: no control, and decorar() has no row to give one to (the
      // control does not come back inside this test; the repaint's own return is the app's).
      const tr = b.closest("tr[data-cola-row]");
      if (!tr) throw new Error("the control is not inside a queue row");
      tr.removeAttribute("data-cola-row");
      box.remove();
    }, { once: true, capture: true });
  });
  await button.click();
  const form = page.locator(".hr-hsi-datos");
  await expect(form, "the question stands although its control was away").toBeVisible();  await answerIfAsked(page);
  await untilLoaded(page, id);
});

test("«Turno creado»: with the automatic send on, the new turno goes to HSI without a click", async ({ adminPage: page }, testInfo) => {
  await page.setViewportSize(DESKTOP);
  await page.goto("/admin/cola", { waitUntil: "load" });
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  await installExtension(page, { auto: true });
  await page.click("#adm-crear-turno");
  await expect(page.locator("#ct-modal")).toBeVisible();
  // A new patient registered at the desk, with all HSI asks for: the
  // automatic send then needs no question.
  await page.fill("#ct-pac", "30111888");
  await page.click("#ct-pac-sug li[data-alta]");
  await page.fill("#ct-alta-apellido", "Creado"); await page.fill("#ct-alta-nombre", "Turno");
  await page.fill("#ct-alta-nac", "01/02/1990"); await page.locator("#ct-alta-sexo").selectOption("F");
  const alta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/patients" && r.request().method() === "POST");
  await page.click("#ct-alta-ok");
  expect((await alta).ok(), "the patient is registered").toBe(true);
  await page.fill("#ct-svc", "medicina");
  await page.click('#ct-svc-sug [data-svc="medicina-general-riverside-clinic"]');
  await expect(page.locator(".ct-prop-cuando").first()).toBeVisible({ timeout: 10000 });
  if (await page.locator("#ct-med-turno").count()) await page.selectOption("#ct-med-turno", { index: 1 });
  await page.locator("#ct-ok").click();
  await expect(page.locator("#ct-modal h2")).toHaveText(/creado/);
  const control = page.locator("#ct-hsi");
  const id = await control.getAttribute("data-hr-hsi");
  expect(id, "«Turno creado» names its turno").toBeTruthy();
  await untilLoaded(page, id);
  await expect(control).toHaveText("Envío automático a HSI");
  await expectTurning(control);
  await shot(testInfo, page, "turno-creado-escritorio-auto.png");
  await answer(page, id, "guardado");
  await expectChecked(control);
  await page.setViewportSize(PHONE);
  await shot(testInfo, page, "turno-creado-telefono-guardado.png");
});

test("Solicitudes: with the automatic send on, two approvals go one after the other under «Recién aprobadas»", async ({ adminPage: page, app }, testInfo) => {
  await page.setViewportSize(DESKTOP);
  const [a, b] = app.fixture.requests.filter((r) => r.nombre.startsWith("Audit")).slice(0, 2);
  await openPending(page);
  await installExtension(page, { auto: true });
  const first = await approveRow(page, a.nombre, 0);
  // A card above the queue (blind review 2026-11-05, C4: the card won over a
  // group of the queue): its title and no counter; each approval is one line,
  // «name · service · turno», with its control.
  const recientes = page.locator("#hsi-recientes");
  await expect(recientes).toBeVisible();
  await expect(recientes.locator("h2")).toHaveText("Recién aprobadas");
  await expect(recientes.locator(".sol-grp-c"), "no counter").toHaveCount(0);
  await expect(recientes.locator(".hsi-recientes-f")).toHaveCount(1);
  await expect(recientes.locator(".hsi-recientes-q")).toContainText(a.nombre);
  await expect(recientes.locator(".hsi-recientes-q")).toHaveText(/^[^·]+ · [^·]+ · .+$/);
  const one = recientes.locator(`[data-hr-hsi="${first}"]`);
  await untilLoaded(page, first);
  await expect(one).toHaveText("Envío automático a HSI");
  const second = await approveRow(page, b.nombre, 1);
  const two = recientes.locator(`[data-hr-hsi="${second}"]`);
  // HSI takes one at a time: the second waits for the first.
  await expect(two).toHaveAttribute("data-estado", "en-cola");
  await expect(two).toHaveText("Envío automático a HSI");
  await expectTurning(two);
  expect(await overflowOf(two, "#hsi-recientes"), "«Envío automático a HSI» stays inside the card").toBe(0);
  // Newest first.
  await expect(recientes.locator(".hsi-recientes-f")).toHaveCount(2);
  await expect(recientes.locator(".hsi-recientes-f").first().locator(".hsi-recientes-q")).toContainText(b.nombre);
  expect(await loads(page)).toEqual([first]);
  await shot(testInfo, page, "recien-aprobadas-escritorio-en-cola.png");
  await answer(page, first, "guardado");
  await expectChecked(recientes.locator(`[data-hr-hsi="${first}"]`));
  await untilLoaded(page, second);
  expect(await loads(page), "each turno goes once").toEqual([first, second]);
  await answer(page, second, "guardado");
  await expectChecked(recientes.locator(`[data-hr-hsi="${second}"]`));
  await page.setViewportSize(PHONE);
  // The phone drops the HSI column of the list, never the card's control.
  await expect(recientes.locator(`[data-hr-hsi="${second}"]`)).toBeVisible();
  expect(await overflowOf(recientes.locator(`[data-hr-hsi="${second}"]`), "#hsi-recientes"), "the control fits the phone card").toBe(0);
  await shot(testInfo, page, "recien-aprobadas-telefono-guardado.png");
});

test("Solicitudes: with the automatic send off, an approval waits for the desk's click", async ({ adminPage: page, app }) => {
  await page.setViewportSize(DESKTOP);
  const [c] = app.fixture.requests.filter((r) => r.nombre.startsWith("Audit")).slice(2, 3);
  await openPending(page);
  await installExtension(page);
  const id = await approveRow(page, c.nombre, 2);
  const control = page.locator(`#hsi-recientes [data-hr-hsi="${id}"]`);
  await expect(control).toHaveText("Cargar en HSI");
  await expect(control).toHaveAttribute("data-estado", "reposo");
  expect(await loads(page), "nothing goes by itself").toEqual([]);
  await control.click();
  await untilLoaded(page, id);
  await expect(control).toHaveAttribute("data-estado", "abriendo");
});

test("an approved request's detail offers «Cargar en HSI», and the click loads it", async ({ adminPage: page, app }, testInfo) => {
  await page.setViewportSize(DESKTOP);
  const target = app.fixture.requests.find((r) => r.nombre.startsWith("Pilar"));
  await openPending(page);
  const id = await approveRow(page, target.nombre, 3);
  await page.reload();
  await expect(page.locator("#list [data-sol-row]").first()).toBeVisible({ timeout: 12000 });
  await page.locator("#f-estado").selectOption("aprobada");
  // The column is there before the extension marks the page: it follows the
  // push or the extension serving this site, like every other entry point
  // (2026-10-28, d1122568; blind review 2026-11-05, C2).
  const row = page.locator(`#list [data-sol-row="${id}"]`);
  await expect(row).toBeVisible();
  await expect(page.locator("#list .sol-ch-hsi"), "the column needs no page mark").toBeVisible();
  const cell = row.locator(".sol-cell-hsi");
  await expect(cell.locator(`[data-hr-hsi="${id}"]`)).toHaveText("Cargar en HSI");
  await installExtension(page);
  await expect(cell.locator(`[data-hr-hsi="${id}"]`)).toHaveText("Cargar en HSI");
  // The longest label the column takes fits it in one line (C1: 108px cut it).
  await answer(page, id, "en-cola");
  await expect(cell.locator(`[data-hr-hsi="${id}"]`)).toHaveText("Envío automático a HSI");
  const fit = await cell.evaluate((c) => {
    const q = c.closest(".sol-queue").getBoundingClientRect(), r = c.getBoundingClientRect();
    return { scroll: c.scrollWidth, client: c.clientWidth, inside: r.right <= q.right + 0.5 };
  });
  expect(fit.scroll, "«Envío automático a HSI» is not cut by its cell").toBeLessThanOrEqual(fit.client);
  expect(fit.inside, "the HSI column is inside the list").toBe(true);
  await shot(testInfo, page, "aprobadas-escritorio-columna-hsi.png");
  // The extension's hint opens under the button, over the next rows: the HSI
  // cell lets it out instead of cutting it to a sliver.
  await answer(page, id, "alta", "HSI no tiene a este paciente: terminá el alta y sigue solo.");
  const hint = cell.locator(".hr-hsi-aviso");
  await expect(hint).toContainText("alta");
  expect(await cutBy(hint), "no box around the hint cuts it").toEqual([]);
  await answer(page, id, "reposo");
  await row.click();
  const detail = page.locator(`aside.sol-det[data-id="${id}"]`);
  await expect(detail).toBeVisible();
  // With the request open the list keeps every cell it shows inside itself and
  // under its own header (C1: hiding Centro alone slid each later cell one
  // track left and «Cargar en HSI» past the list's edge), and the HSI column
  // stays, its longest label whole (blind review 2026-11-05, C1).
  expect(await rowOutside(row), "every cell of the open list is inside it, under its header").toEqual([]);
  await expect(page.locator("#list .sol-ch-hsi"), "the open list keeps the HSI column").toBeVisible();
  await expect(cell).toBeVisible();
  for (const estado of ["reposo", "en-cola"]) {
    await answer(page, id, estado);
    const cut = await cell.locator(`[data-hr-hsi="${id}"]`).evaluate((b) => ({
      w: b.scrollWidth - b.clientWidth, h: b.scrollHeight - b.clientHeight,
      past: b.getBoundingClientRect().right - b.closest(".sol-cell-hsi").getBoundingClientRect().right,
    }));
    expect(cut.w, `«${estado}» label is not cut across`).toBeLessThanOrEqual(0);
    expect(cut.h, `«${estado}» label is not cut down`).toBeLessThanOrEqual(0);
    expect(cut.past, `«${estado}» control stays in its cell`).toBeLessThanOrEqual(0.5);
  }
  await answer(page, id, "reposo");
  await shot(testInfo, page, "aprobadas-escritorio-detalle-abierto.png");
  const control = detail.locator(`[data-hr-hsi="${id}"]`);
  await expect(control).toHaveText("Cargar en HSI");
  await control.click();
  await untilLoaded(page, id);
  await expectTurning(control);
  // The click is the control's own: the detail stays open.
  await expect(detail).toBeVisible();
  await shot(testInfo, page, "detalle-aprobada-escritorio-cargando.png");
  await answer(page, id, "guardado");
  await expectChecked(control);
  // The status filter is a saved preference: the next test opens on «pendiente»
  // only once the save has landed (on the phone the filter is folded away).
  const saved = page.waitForResponse((res) => new URL(res.url()).pathname === "/api/admin/preferences" && res.request().method() !== "GET");
  await page.locator("#f-estado").selectOption("pendiente");
  await saved;
});

test("without the extension, the control copies the DNI and offers to install it", async ({ adminPage: page, app, context }, testInfo) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.setViewportSize(PHONE);
  const target = app.fixture.requests.find((r) => r.nombre.startsWith("Tomas"));
  await openPending(page);
  const id = await approveRow(page, target.nombre, 4);
  const control = page.locator(`#hsi-recientes [data-hr-hsi="${id}"]`);
  await expect(control).toHaveText("Cargar en HSI");
  await control.click();
  const aviso = page.locator(`#hsi-recientes .hsi-sync:has([data-hr-hsi="${id}"]) .hr-hsi-aviso`);
  await expect(aviso).toContainText("Copiamos el DNI");
  // Where to install it is one line under the list (the reference-deployment counterpart, 2026-10-27), so the
  // hint copies the DNI and does not repeat the link.
  await expect(aviso.locator("a")).toHaveCount(0);
  // One link on the screen; the steps, the store and the PDF live in its pop-up (the product owner, 2026-11-09).
  const instalar = page.locator("#hsi-recientes .hsi-instalar");
  const link = instalar.locator(".hsi-instalar-link");
  await expect(link).toHaveText("Cargar turnos en HSI con un toque");
  const pop = instalar.locator(".hsi-instalar-pop");
  await expect(pop).toBeHidden();
  await link.click();
  await expect(pop).toBeVisible();
  await expect(pop.locator("a").first()).toHaveAttribute("href", /chromewebstore\.google\.com/);
  await expect(pop.locator('a[href="/api/admin/hsi-extension/guide.pdf"]')).toHaveText("Cómo se instala (PDF)");
  await page.keyboard.press("Escape");
  await expect(pop).toBeHidden();
  // The extension marks the page once installed: the line goes away.
  await page.evaluate(() => { document.documentElement.dataset.hrHumanizador = "1"; });
  await expect(instalar).toBeHidden();
  await page.evaluate(() => { delete document.documentElement.dataset.hrHumanizador; });
  // The hint is a line of the card, whole, not a popover cut by a cell.
  expect(await overflowOf(aviso, "#hsi-recientes"), "the hint fits the phone card").toBe(0);
  expect(await aviso.evaluate((a) => a.scrollWidth <= a.clientWidth), "the hint wraps instead of being cut").toBe(true);
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(String(target.dni));
  await shot(testInfo, page, "sin-extension-telefono.png");
  await page.setViewportSize(DESKTOP);
  await shot(testInfo, page, "sin-extension-escritorio.png");
});
