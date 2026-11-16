// The English panel, swept screen by screen: every visible label, button,
// heading, option, placeholder and aria-label of every admin screen the
// sidebar offers must read as English. A key missing from locales/en, a
// literal never wrapped in t(), a label computed before the catalog loaded —
// all three render Spanish here, and none of them shows up for a developer
// browsing in Spanish. (on-site tester's follow-ups, 21/10: ~70 Spanish strings in
// the English panel, the 2FA banner first among them.)
// Real ASGI app, synthetic example-town database. Catalog content (pack data:
// service, center, doctor names) is excluded — it is translated by its own
// pipeline, not by locales/.
//
// Port of tests/e2e/english-panel-sweep.e2e.mjs (4 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `requests: true, queue: true`). The original's `test.before` (pack strings, sign in, enable
//   the English catalog through the settings API) is a `test.beforeAll` on a throwaway context
//   of the runner's browser; each browser test gets the runner's context and page, adds the
//   `lang=en` cookie and signs in with loginAdmin() (which waits for the landing page load).
// - settle(): the original waited for network idle and then slept 400 ms. The sleep is
//   replaced by waiting until the screen's interface strings stop changing (two consecutive
//   reads equal; capped at 5 s and then ignored, like the network-idle wait, so a screen with
//   a live clock cannot hang the sweep). The sweep itself is unchanged.
// - The sweep is one test that visits ~30 pages: it gets test.setTimeout(240 s) (the original
//   had no limit; it took ~50 s).
// - sweep-pages.txt / sweep-findings.txt are still written to the evidence directory
//   (~/.claude/artifacts/pinamar-turnos/shots-en-panel/) and are also attached to the report;
//   the 2FA screenshots go through saveShot() to the same directory.
// - The detector test and the sweep share no state; the module-level pack lists are filled
//   once in beforeAll, in the same worker.
// - Locators left as CSS: `#adm-nav a[href^='/admin/']` (the sidebar IS the list of screens to
//   sweep), `main a[href*='?']` (deep links), `.adm5-menu > summary`, `.adm-2fa-nudge`.
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test, expect, loginAdmin, saveShot, ROOT, GATE_PACK_CHAIN } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_english_panel_sweep", requests: true, queue: true } });

const evidence = path.join(os.homedir(), ".claude/artifacts/pinamar-turnos/shots-en-panel");

// Words that only Spanish uses. A label is Spanish when it holds one STRONG
// word, or two of the function words English never borrows.
const STRONG = new Set(`
guardar cancelar agregar buscar editar eliminar borrar cerrar volver siguiente anterior
configurar configuracion configurala ingresar ingresa salir aprobar rechazar enviar subir
turno turnos medico medicos medica paciente pacientes solicitud solicitudes usuario usuarios
contrasena cuenta reportes servicios centros horarios horario estado fecha nombre apellido
telefono correo hoy manana ayer semana semanas dias ano anos todavia ningun ninguna
ninguno todos todas nuevo nueva cargando sistema ajustes idiomas idioma ubicacion
especialidad especialidades derivacion orden ordenes entrevista consultorio sede sedes
preparacion preparaciones resultados evolucion copias seguridad desde hasta
dominio aplicacion almacenamiento integracion sincronizacion conectividad metricas
sesion proveedor remitente escritura respaldo disponible conectada conectado rojo
`.trim().split(/\s+/));
const FUNCTION = new Set(`
el la los las del al una unos unas para con sin por que como cuando donde pero tambien
esta estan hay tiene tenes podes vas va van su sus tu tus mi mis se nos les lo y
`.trim().split(/\s+/));

const fold = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
function isSpanish(text) {
  // A lower-case word carrying a Spanish accent ("más", "días") is never a
  // name; a capitalized one ("González") may be, and stays to the lists below.
  if (/(^|[^\p{L}])\p{Ll}*[\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]\p{Ll}*/u.test(text.replace(/(^|[^\p{L}])\p{Lu}\p{L}*/gu, "$1"))) return true;
  // A list closed with the Spanish «y»: "Mo, Tu, We, Th y Fr".
  if (/,\s*\S+ y \S+/.test(text)) return true;
  const words = fold(text).match(/[a-zñ]+/g) || [];
  let fn = 0;
  const seen = new Set();
  for (const w of words) {
    if (STRONG.has(w)) return true;
    if (FUNCTION.has(w) && !seen.has(w)) { seen.add(w); fn += 1; }
  }
  return fn >= 2;
}

// Every string of the active pack chain: catalog content, not interface.
async function packStrings() {
  const out = new Set();
  const walk = (v) => {
    if (typeof v === "string") { if (v.trim()) out.add(fold(v.trim())); }
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  for (const pack of GATE_PACK_CHAIN.split(",")) {
    const dir = path.join(ROOT, "packs", pack);
    for (const f of await readdir(dir)) {
      if (!f.endsWith(".json")) continue;
      try { walk(JSON.parse(await readFile(path.join(dir, f), "utf8"))); } catch { /* not a catalog */ }
    }
  }
  return [...out];
}

let PACK = [];
let PACK_SHORT = [];
const fromPack = (text) => {
  const t = fold(text.trim().replace(/^[“"«]|[”"»]$/g, ""));
  return PACK.some((s) => s === t || (t.length > 3 && s.includes(t)));
};
// The interface around catalog content: "Filter by <specialty>",
// "<doctor> — <specialty>". Removes every pack string a label embeds, and the
// machine identifiers (TURNOS_X, ids_with_underscores) that sit in a detail.
const interfacePart = (text) => {
  // Identifiers first: a pack word blanked inside "entrevista_receta" would
  // leave "entrevista" behind as if it were prose.
  let raw = text.normalize("NFC").replace(/\b[A-Za-z0-9]+(_[A-Za-z0-9]+)+\b/g, " ").replace(/\{\w+\}/g, " ");
  let folded = fold(raw);
  if (folded.length === raw.length) {  // precomposed text: folding keeps positions
    for (const s of PACK_SHORT) {
      for (let i = folded.indexOf(s); i >= 0; i = folded.indexOf(s)) {
        const blank = " ".repeat(s.length);
        folded = folded.slice(0, i) + blank + folded.slice(i + s.length);
        raw = raw.slice(0, i) + blank + raw.slice(i + s.length);
      }
    }
  }
  return raw.replace(/\b[A-Za-z0-9]+(_[A-Za-z0-9]+)+\b/g, " ");
};

/** English panel session at `width`: lang cookie, signed in, landing screen loaded. */
async function signInEnglish({ context, page, app }, width) {
  await page.setViewportSize({ width, height: 900 });
  await context.addCookies([{ name: "lang", value: "en", url: app.baseUrl }]);
  // The landing screen's own load, not just its URL: the URL flips at commit,
  // before the new document has parsed its sidebar.
  await loginAdmin(page, app.baseUrl);
  await page.waitForLoadState("load");
}

// Values a person typed (inputs, textareas) and machine strings (paths, env
// names, ids) are data, not interface; everything else, the AI register's
// dossier and the prompts' descriptions included, must read English.

// Visible text nodes + the attributes a screen reader or a field shows.
async function interfaceStrings(page) {
  return page.evaluate(() => {
    const out = [];
    const visible = (el) => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!el || ["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "TEXTAREA"].includes(el.tagName)) continue;
      // The prompt's technical contract is shown verbatim, as sent to the model:
      // its JSON keys are Spanish on purpose (the code reads them by name).
      if (el.closest("#sec-prompts pre")) continue;
      const text = n.textContent.replace(/\s+/g, " ").trim();
      if (!text) continue;
      if (el.tagName === "OPTION" || visible(el)) out.push({ text, where: el.tagName.toLowerCase() + (el.id ? "#" + el.id : el.className && typeof el.className === "string" ? "." + el.className.split(" ")[0] : "") });
    }
    for (const el of document.querySelectorAll("[placeholder],[aria-label],[title],img[alt]")) {
      for (const attr of ["placeholder", "aria-label", "title", "alt"]) {
        const v = el.getAttribute(attr);
        if (v && v.trim()) out.push({ text: v.trim(), where: el.tagName.toLowerCase() + "[" + attr + "]" });
      }
    }
    out.push({ text: document.title, where: "title" });
    return out;
  });
}

// Machine strings: a path, an env name, an id with underscores, an email.
const MACHINE = /^(\/[\w./-]+|[\w.+-]+@[\w.-]+|[A-Z0-9_]{3,}(=\S*)?(\s.*)?|[a-z0-9]+(_[a-z0-9]+)+)$/;

/** Network idle, then until the screen's interface strings stop changing (best effort). */
async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  let previous;
  await expect.poll(async () => {
    const current = JSON.stringify(await interfaceStrings(page));
    const stable = current === previous;
    previous = current;
    return stable;
  }, { timeout: 5000, intervals: [100, 200, 400] }).toBe(true).catch(() => {});
}

test.beforeAll(async ({ browser, app }) => {
  await mkdir(evidence, { recursive: true });
  PACK = await packStrings();
  PACK_SHORT = PACK.filter((s) => s.length >= 4 && s.length <= 60).sort((a, b) => b.length - a.length);
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await loginAdmin(page, app.baseUrl);
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.ok(), "enable the English catalog").toBe(true);
  } finally { await context.close(); }
});

test("the detector tells the two languages apart", () => {
  for (const es of ["Configurar ahora", "El panel va a pedir verificación en 2 pasos", "Mo, Tu, We, Th y Fr", "más"]) {
    expect(isSpanish(es), es).toBe(true);
  }
  for (const en of ["Set up now", "Menu", "Filter by", "Juan González", "Mo, Tu and We", "Doctors"]) {
    expect(isSpanish(en), en).toBe(false);
  }
});

test("every admin screen of the sidebar reads English", async ({ context, page, app }, testInfo) => {
  test.setTimeout(240_000);
  await signInEnglish({ context, page, app }, 1280);
  const findings = [];
  const screens = await page.locator("#adm-nav a[href^='/admin/']").evaluateAll(
    (els) => [...new Set(els.map((a) => a.getAttribute("href")))]);
  expect(screens.length, `sidebar lists the screens (${screens.length})`).toBeGreaterThanOrEqual(8);
  // Each screen, then the views and first records it links to (an editor
  // `?item=`, a report `?view=`): most labels live one click deep.
  const queue = [...screens, "/admin/account"];
  const seen = new Set(queue);
  while (queue.length) {
    const href = queue.shift();
    await page.goto(href, { waitUntil: "load", timeout: 20000 });
    await settle(page);
    for (const { text, where } of await interfaceStrings(page)) {
      if (MACHINE.test(text) || fromPack(text) || !isSpanish(interfacePart(text))) continue;
      findings.push(`${href} ${where}: ${text.slice(0, 140)}`);
    }
    if (href.includes("?")) continue;
    const deeper = await page.locator("main a[href^='/admin/'][href*='?']").evaluateAll(
      (els) => els.map((a) => a.getAttribute("href")));
    const perScreen = new Map();
    for (const link of deeper) {
      const kind = link.split("=")[0];
      const n = perScreen.get(kind) || 0;
      if (seen.has(link) || n >= (kind.endsWith("view") ? 8 : 1)) continue;
      perScreen.set(kind, n + 1);
      seen.add(link);
      queue.push(link);
    }
  }
  expect(seen.size, `the sweep reached the editors and report views (${seen.size} pages)`).toBeGreaterThanOrEqual(25);
  const pages = [...seen].join("\n") + "\n";
  await writeFile(path.join(evidence, "sweep-pages.txt"), pages);
  await testInfo.attach("sweep-pages.txt", { body: pages, contentType: "text/plain" });
  const unique = [...new Set(findings)];
  const report = unique.join("\n") + "\n";
  await writeFile(path.join(evidence, "sweep-findings.txt"), report);
  await testInfo.attach("sweep-findings.txt", { body: report, contentType: "text/plain" });
  expect(unique, `Spanish in the English panel:\n${unique.join("\n")}`).toEqual([]);
});

for (const width of [390, 1280]) {
  test(`English 2FA reminder and sidebar at ${width}px`, async ({ context, page, app }, testInfo) => {
    await signInEnglish({ context, page, app }, width);
    await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 20000 });
    await settle(page);
    const summary = page.locator(".adm5-menu > summary");
    if (await summary.count()) expect((await summary.innerText()).trim()).toBe("Menu");
    const nudge = page.locator(".adm-2fa-nudge");
    if (await nudge.count()) {
      const text = await nudge.innerText();
      expect(text).not.toMatch(/verificación|Configurar| de (enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)/);
      expect(text).toMatch(/Set up now/);
    }
    await saveShot(testInfo, page, path.join(evidence, `local-admin-requests-en-${width}.png`));
  });

  test(`English AI register and prompts at ${width}px`, async ({ context, page, app }, testInfo) => {
    await signInEnglish({ context, page, app }, width);
    for (const [screen, name] of [["/admin/ai-register", "ai-register"], ["/admin/prompts?prompt=orden_analisis", "prompts"]]) {
      await page.goto(screen, { waitUntil: "load", timeout: 20000 });
      await settle(page);
      if (name === "ai-register") await page.locator("#air-table details summary").first().click();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${screen} fits ${width}px`).toBeLessThanOrEqual(0);
      await saveShot(testInfo, page, path.join(evidence, `local-admin-${name}-en-${width}.png`));
    }
  });
}
