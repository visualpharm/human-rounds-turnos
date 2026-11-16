import { test, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { chromium } from "playwright";

let browser;
test.before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
test.after(async () => { await browser?.close(); });

// Real native controller and DOM; synthetic API/dependencies, no clinical service.
// This supplements, not replaces, the real authenticated ASGI acceptance gate.
const consultation = {
  id: "fixture-A", nombre: "Fixture Alpha", dni: "synthetic", status: "done",
  analyzed_at: "2026-01-01", mensajes: [], differential: [], suggested_exams: [],
  perfil: { alergias: ["Synthetic allergy"] },
  catalogos: {
    diagnosticos: [{ id: "angina", label: "Angina", estudios_sugeridos: ["ecg"] },
      { id: "unstable", label: "Unstable angina", nivel: "specific", parent: "angina" },
      { id: "other", label: "Other diagnosis" }],
    estudios: [{ id: "ecg", label: "Electrocardiogram", categoria: "Cardiology" },
      { id: "lab", label: "Blood test", categoria: "Laboratory" }],
    medicamentos: [{ nombre: "Fixture medication", dosis: "Fixture dose", dosis_opciones: ["Fixture dose", "Alternate fixture dose"], frecuencia: "Fixture frequency", duracion: "Fixture duration" }], frecuencias: ["Fixture frequency", "Alternate frequency"],
  },
};

async function fixture(t, { lang = "es", nativeEntry = false, rejectFirstEntry = false, holdEntry = null, holdFonts = false, data = {}, expectedName = "Fixture Alpha" } = {}) {
  // One Chromium per file, a fresh context per test. Every wait is bounded by
  // the tier's ceiling (helpers/ceiling.mjs): these pages are synthetic
  // fixtures, so a short budget measured the machine's load, not the product.
  // The product's one timer (the 8s import watchdog) runs on the page clock below.
  const page = await openPage(browser);
  t.after(() => page.context().close());
  const watchdog = holdEntry || holdFonts;
  if (watchdog) await page.clock.install();
  const errors = [], unknown = [], posts = [];
  const entryStats = { requests: 0 };
  const template = readFileSync(new URL("../app/modules/intake/admin_consult.html", import.meta.url), "utf8");
  const entries = [...template.matchAll(/<script type="module">([\s\S]*?)<\/script>/g)];
  if (nativeEntry) assert.equal(entries.length, 1, "exercise the entire production entry, not selected functions");
  const entry = nativeEntry ? entries[0][1].replace("{{ module_static('modules/intake/admin-consult.mjs') }}", "/native.mjs")
    .replace("{{ static('platform/screen-boot.mjs') }}", "/screen-boot.mjs")
    : "import {mount} from '/native.mjs';window.owner=mount(document.getElementById('sec-consulta'));";
  page.on("pageerror", error => errors.push(error.message));
  const dependencies = {
    "platform/dates.mjs": "export const relativo=value=>{(window.dateArguments??=[]).push(value);return 'Formatted analysis time';};",
    "platform/i18n.mjs": readFileSync(new URL("../static/platform/i18n.mjs", import.meta.url), "utf8"),
    "platform/admin-session.mjs": "export const confirmar=()=>Promise.resolve(false);",
    "platform/admin-patient-summary.mjs": "export function mount(host){window.summaryMounts++;host.textContent='Synthetic summary';}",
  };
  const manifest = Object.fromEntries(Object.keys(dependencies).map(name => [name, "/static/" + name]));
  const catalog = Object.assign({}, ...["admin.reports", "admin.queue"].map(namespace =>
    JSON.parse(readFileSync(new URL("../locales/" + lang + "/" + namespace + ".json", import.meta.url), "utf8"))));
  await page.route("**/*", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/admin/consult") return route.fulfill({ contentType: "text/html", body:
      `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>
      <h1>Consultation fixture</h1><section id="sec-consulta"></section>
      <p id="consult-status" role="status" hidden></p><script>window.summaryMounts=0;window.SANA_STATIC=${JSON.stringify(manifest)};window.SANA_I18N=${JSON.stringify({ lang, catalog })};${holdFonts ? 'Object.defineProperty(document,"fonts",{value:{ready:new Promise(resolve=>{window.releaseFonts=resolve})}});' : ''}</script>
      <template id="consult-load-recovery"><p role="alert">Fixture load failure</p><button data-retry>Reload</button></template>
      <script type="module">${entry}</script></body></html>` });
    if (path === "/screen-boot.mjs") return route.fulfill({ contentType: "text/javascript",
      body: readFileSync(new URL("../static/platform/screen-boot.mjs", import.meta.url), "utf8") });
    if (path === "/native.mjs") {
      entryStats.requests++;
      if (rejectFirstEntry && entryStats.requests === 1) return route.abort();
      if (holdEntry) await holdEntry;
      return route.fulfill({ contentType: "text/javascript",
        body: readFileSync(new URL("../app/modules/intake/admin-consult.mjs", import.meta.url), "utf8") });
    }
    const name = path.replace(/^\/static\//, "");
    if (Object.hasOwn(dependencies, name)) return route.fulfill({ contentType: "text/javascript", body: dependencies[name] });
    if (path === "/api/admin/intake/consult/fixture-A") return route.fulfill({ json: { ...consultation, ...data } });
    if (path === "/api/admin/intake/consult/fixture-A/confirm") {
      assert.equal(route.request().method(), "POST"); posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { ok: true, receta_url: "/r/fixture", orden_url: "/o/fixture" } });
    }
    unknown.push(path); return route.abort();
  });
  // The held step must be reached before the watchdog fires: the import is
  // pending (held entry) or the mount is awaiting fonts (it fetched its data).
  const heldStep = watchdog && page.waitForRequest((request) => new URL(request.url()).pathname
    === (holdFonts ? "/api/admin/intake/consult/fixture-A" : "/native.mjs"));
  await page.goto("https://consultation.test/admin/consult?interview=fixture-A");
  if (watchdog) {
    await heldStep;
    await page.clock.runFor(8000);
  }
  if (nativeEntry) await page.locator(rejectFirstEntry || watchdog ? "[data-retry]" : "#cst-review").waitFor();
  else { await page.waitForFunction(() => !!window.owner); await page.evaluate(() => window.owner.ready); }
  if (!rejectFirstEntry && !holdEntry && !holdFonts) assert.equal(await page.locator(".cst-head h2").textContent(), expectedName);
  t.after(() => { assert.deepEqual(errors, []); assert.deepEqual(unknown, []); });
  return { page, posts, entryStats };
}

// 2026-11-16 (static/platform/screen-boot.mjs): the 8 s deadline only says the
// load is late, beside the screen; a late paint still lands and clears the copy.
test('native entry deadline says a hung font promise is late and lets its late paint land', async t => {
  const { page } = await fixture(t, { nativeEntry: true, holdFonts: true });
  assert.equal(await page.locator('.screen-load-slow [data-retry]').count(), 1);
  assert.equal(await page.locator('#sec-consulta [data-retry]').count(), 0);
  await page.evaluate(async () => { window.releaseFonts(); await document.fonts.ready; });
  await page.clock.runFor(100);  // the frames the late paint uses (rAF is on the page clock)
  await page.locator('#cst-review').waitFor();
  assert.equal(await page.locator('.screen-load-slow').count(), 0);
});

for (const [label, data, expectedName, summaryCount] of [
  ["resolved name", { entered_name: "Ignored", entered_surname: "Fallback" }, "Fixture Alpha", 1],
  ["entered full name", { nombre: "", entered_name: "Entered", entered_surname: "Person" }, "Entered Person", 1],
  ["DNI only", { nombre: "" }, "DNI synthetic", 1],
  ["unmatched QR", { nombre: "", dni: "" }, "Patient without a name", 0],
]) {
  test("patient header fallback: " + label, async t => {
    const { page } = await fixture(t, { lang: "en", data, expectedName });
    assert.equal(await page.evaluate(() => window.summaryMounts), summaryCount);
    if (label === "DNI only") assert.equal((await page.locator(".cst-head").textContent()).match(/DNI synthetic/g).length, 1);
  });
}

test("reopened consultation displays formatted analysis time, never raw timestamps", async t => {
  const { page } = await fixture(t, { data: { reopened_at: "2026-02-02", analyzed_at: "2026-03-03" } });
  assert.match(await page.locator("#sec-consulta").textContent(), /Formatted analysis time/);
  assert.doesNotMatch(await page.locator("#sec-consulta").textContent(), /2026-02-02|2026-03-03/);
  const argumentsUsed = await page.evaluate(() => window.dateArguments);
  assert.ok(argumentsUsed.length > 0);
  assert.ok(argumentsUsed.every(value => value === "2026-03-03"));
});

test("medication catalogs remain suggestions and a custom prescription reaches the payload", async t => {
  const { page, posts } = await fixture(t);
  assert.deepEqual(await page.locator("#cst-med-list option").evaluateAll(nodes => nodes.map(n => n.value)), ["Fixture medication"]);
  assert.deepEqual(await page.locator("#cst-freq-list option").evaluateAll(nodes => nodes.map(n => n.value)), ["Fixture frequency", "Alternate frequency"]);
  await page.locator("[data-add-med]").click();
  await page.locator('[data-med-field="nombre"]').fill("Fixture medication");
  await page.locator('[data-med-field="nombre"]').press("Tab");
  assert.equal(await page.locator("#cst-dose-0 option").count(), 2);
  const medication = { nombre: "Custom synthetic medication", dosis: "Custom dose", frecuencia: "Custom frequency", duracion: "Custom duration" };
  for (const [field, value] of Object.entries(medication)) {
    await page.locator('[data-med-field="' + field + '"]').fill(value);
    await page.locator('[data-med-field="' + field + '"]').press("Tab");
  }
  assert.equal(await page.locator("#cst-dose-0 option").count(), 0, "unknown medication must not retain the preceding catalog's dose options");
  await page.locator("#cst-review").click();
  await page.locator("#cst-confirmar").click();
  await page.locator('a[href="/r/fixture"]').waitFor();
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].medicacion, [{ ...medication, custom: true }]);
});

test("native consultation positive control submits the displayed choices", async t => {
  const { page, posts } = await fixture(t);
  await page.locator('[data-exam="lab"]').check();
  await page.locator("#cst-notes").fill("Synthetic note");
  await page.locator("#cst-review").click();
  await page.locator("#cst-confirmar").click();
  await page.locator('a[href="/r/fixture"]').waitFor();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].notas, "Synthetic note");
  assert.deepEqual(posts[0].estudios, [{ id: "lab", label: "Blood test", custom: false }]);
});

test("diagnosis and exam searches retain the actual focused input and summary owner", async t => {
  const { page } = await fixture(t);
  for (const [selector, query, visible, hidden] of [
    ["#cst-dx-search", "Angina", '[data-dx="angina"]', '[data-dx="other"]'],
    ["#cst-exam-search", "Electro", '[data-exam="ecg"]', '[data-exam="lab"]'],
  ]) {
    const input = page.locator(selector);
    await input.focus();
    await page.evaluate(selector => { window.originalInput = document.querySelector(selector); }, selector);
    await input.pressSequentially(query);
    assert.equal(await input.inputValue(), query);
    assert.equal(await page.evaluate(() => document.activeElement === window.originalInput && window.originalInput.isConnected), true);
    assert.equal(await page.locator(visible).count(), 1);
    assert.equal(await page.locator(hidden).count(), 0);
    assert.equal(await page.evaluate(() => window.summaryMounts), 1);
  }
});

test("a suggested exam stays unchecked until the clinician opts in; a manual deselection stays authoritative", async t => {
  const { page, posts } = await fixture(t, { data: { differential: [{ id: "angina", prob: 0.7 }], suggested_exams: ["ecg"] } });
  assert.equal(await page.locator("[data-ai-notice-inline]").count(), 1, "un solo aviso de IA acompaña las sugerencias");
  await page.locator('[data-dx="angina"]').check();
  assert.equal(await page.locator('[data-exam="ecg"]').isChecked(), false, "marcar el diagnóstico nunca marca su estudio sugerido");
  assert.match(await page.locator('[data-exam="ecg"]').locator("xpath=..").textContent(), /Sugerido/, "la sugerencia sigue visible como etiqueta");
  await page.locator('[data-exam="ecg"]').check();
  await page.locator('[data-exam="ecg"]').uncheck();
  await page.locator("#cst-exam-search").fill("Blood");
  await page.locator("#cst-exam-search").fill("");
  assert.equal(await page.locator('[data-exam="ecg"]').isChecked(), false);
  await page.locator("#cst-review").click();
  await page.locator("#cst-confirmar").click();
  await page.locator('a[href="/r/fixture"]').waitFor();
  assert.equal(posts.length, 1); assert.deepEqual(posts[0].estudios, []);
});

test("a matching specific diagnosis keeps its parent context and exam categories stay grouped", async t => {
  const { page } = await fixture(t);
  await page.locator("#cst-dx-search").fill("Unstable");
  assert.deepEqual(await page.locator("[data-dx]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-dx"))), ["angina", "unstable"]);
  assert.equal(await page.locator('[data-dx="unstable"]').evaluate(node => node.closest("label").classList.contains("cst-pick-row--hijo")), true);
  assert.deepEqual(await page.locator(".cst-group-title").allTextContents(), ["Cardiology", "Laboratory"]);
  await page.locator("#cst-exam-search").fill("Blood");
  assert.deepEqual(await page.locator(".cst-group-title").allTextContents(), ["Laboratory"]);
});

test("custom Enter and click additions preserve other drafts, focus and summary ownership", async t => {
  const { page, posts } = await fixture(t);
  const diagnosis = page.locator("#cst-dx-custom-input"), exam = page.locator("#cst-exam-custom-input");
  await exam.fill("Exam draft"); await diagnosis.fill("Custom diagnosis");
  await diagnosis.press("Enter");
  assert.equal(await diagnosis.inputValue(), "");
  assert.equal(await exam.inputValue(), "Exam draft");
  assert.equal(await page.evaluate(() => document.activeElement.id), "cst-dx-custom-input");
  assert.equal(await page.locator("#consult-status").textContent(), "Diagnóstico agregado");
  assert.equal(await page.locator("#consult-status").isVisible(), true);
  assert.equal(await page.locator("[data-remove-dx]").count(), 1);
  await page.locator("[data-add-exam]").click();
  assert.equal(await exam.inputValue(), "");
  assert.equal(await page.locator("[data-remove-exam]").count(), 1);
  assert.equal(await page.evaluate(() => window.summaryMounts), 1);
  await page.locator("#cst-review").click();
  await page.locator("#cst-confirmar").click(); await page.locator('a[href="/r/fixture"]').waitFor();
  assert.deepEqual(posts[0].diagnosticos, [{ label: "Custom diagnosis", custom: true }]);
  assert.deepEqual(posts[0].estudios, [{ label: "Exam draft", custom: true }]);
});

test("medication add, catalog defaults and removal never replace unrelated form controls", async t => {
  const { page } = await fixture(t);
  await page.locator("#cst-dx-custom-input").fill("Unsaved diagnosis");
  await page.locator("#cst-notes").fill("Retained notes");
  await page.evaluate(() => { window.notesNode = document.querySelector("#cst-notes"); });
  await page.locator("[data-add-med]").click();
  assert.equal(await page.locator("#cst-dx-custom-input").inputValue(), "Unsaved diagnosis");
  assert.equal(await page.evaluate(() => window.notesNode === document.querySelector("#cst-notes")), true);
  await page.locator('[data-med-field="nombre"]').fill("Fixture medication");
  await page.locator('[data-med-field="nombre"]').press("Tab");
  assert.equal(await page.locator('[data-med-field="dosis"]').evaluate(el => document.activeElement === el), true, "Tab must continue into dose after catalog defaulting");
  assert.equal(await page.locator('[data-med-field="dosis"]').inputValue(), "Fixture dose");
  assert.deepEqual(await page.locator('#cst-dose-0 option').evaluateAll(nodes => nodes.map(node => node.value)), ["Fixture dose", "Alternate fixture dose"]);
  assert.equal(await page.locator('[data-med-field="frecuencia"]').inputValue(), "Fixture frequency");
  assert.equal(await page.locator("#cst-dx-custom-input").inputValue(), "Unsaved diagnosis");
  await page.locator("[data-remove-med]").click();
  assert.equal(await page.locator('[data-med-field="nombre"]').count(), 0);
  assert.equal(await page.evaluate(() => window.notesNode === document.querySelector("#cst-notes")), true);
  assert.equal(await page.locator("#cst-notes").inputValue(), "Retained notes");
  assert.equal(await page.evaluate(() => window.summaryMounts), 1);
});

test("English consultation uses the real catalog for table, profile, transcript and input accessibility", async t => {
  const { page } = await fixture(t, { lang: "en" });
  assert.deepEqual(await page.locator(".cst-med-table th").allTextContents(), ["Medication", "Dose", "Frequency", "Duration", ""]);
  assert.match(await page.locator("#sec-consulta").textContent(), /Allergies: Synthetic allergy/);
  assert.equal(await page.locator(".cst-transcript summary").textContent(), "Interview transcript (0 messages)");
  assert.match(await page.locator("#cst-dx-search").getAttribute("placeholder"), /^Search diagnosis/);
  assert.match(await page.locator("#cst-exam-search").getAttribute("placeholder"), /^Search/);
  for (const selector of ["#cst-dx-search", "#cst-exam-search", "#cst-dx-custom-input", "#cst-exam-custom-input", "#cst-notes"]) {
    assert.ok(await page.locator(selector).getAttribute("aria-label"), selector + " needs an accessible name");
  }
  await page.locator("[data-add-med]").click();
  assert.equal(await page.locator("[data-remove-med]").getAttribute("aria-label"), "Remove medication row");
  assert.deepEqual(await page.locator(".cst-med-table tbody td[data-label]").evaluateAll(nodes => nodes.map(n => n.dataset.label)), ["Medication", "Dose", "Frequency", "Duration"]);
});

test("direct route canonicalization keeps a Queue destination on missing, error and confirmed states", async t => {
  const { page } = await fixture(t, { lang: "en" });
  await page.evaluate(() => { history.pushState({}, "", "?interview=fixture-A&unused=1"); dispatchEvent(new PopStateEvent("popstate")); });
  await page.locator("#cst-review").waitFor();
  assert.equal(new URL(page.url()).search, "?interview=fixture-A");
  await page.locator("#cst-review").click(); await page.locator("#cst-confirmar").click(); await page.locator('a[href="/r/fixture"]').waitFor();
  assert.equal(await page.locator('#sec-consulta a[href="/admin/cola"]').textContent(), "Today's patients");
  await page.route("**/api/admin/intake/consult/fixture-A", route => route.fulfill({ status: 503, json: { error: "base de datos no disponible" } }));
  await page.evaluate(() => dispatchEvent(new PopStateEvent("popstate")));
  await page.locator("[data-consult-retry]").waitFor();
  assert.equal(await page.locator('#sec-consulta a[href="/admin/cola"]').textContent(), "Today's patients");
  await page.evaluate(() => { history.pushState({}, "", "?unused=1"); dispatchEvent(new PopStateEvent("popstate")); });
  await page.waitForFunction(() => !location.search);
  assert.equal(await page.locator('#sec-consulta a[href="/admin/cola"]').textContent(), "Today's patients");
});

test("custom-action status does not survive the consultation owner", async t => {
  const { page } = await fixture(t);
  await page.locator("#cst-dx-custom-input").fill("Fixture custom");
  await page.locator("#cst-dx-custom-input").press("Enter");
  assert.equal(await page.locator("#consult-status").isVisible(), true);
  await page.evaluate(() => window.owner());
  assert.equal(await page.locator("#consult-status").isVisible(), false);
  assert.equal(await page.locator("#consult-status").textContent(), "");
});

test("pending and failed confirmation retain unsent inputs and summary ownership", async t => {
  const { page } = await fixture(t);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  await page.route("**/api/admin/intake/consult/fixture-A/confirm", async route => {
    await gate;
    await route.fulfill({ status: 503, json: { error: "Synthetic confirmation failure" } });
  });
  await page.locator("#cst-dx-custom-input").fill("Unsent custom draft");
  await page.locator("#cst-notes").fill("Saved draft note");
  await page.evaluate(() => { window.keptCustom = document.querySelector("#cst-dx-custom-input"); window.keptNotes = document.querySelector("#cst-notes"); });
  await page.locator("#cst-review").click();
  await page.locator("#cst-confirmar").click();
  assert.equal(await page.locator("#cst-confirmar").isDisabled(), true);
  assert.equal(await page.locator("#cst-dx-custom-input").inputValue(), "Unsent custom draft");
  assert.equal(await page.evaluate(() => window.summaryMounts), 1);
  release();
  await page.getByText("Synthetic confirmation failure", { exact: true }).waitFor();
  assert.equal(await page.locator("#cst-confirmar").isEnabled(), true);
  assert.equal(await page.locator("#cst-dx-custom-input").inputValue(), "Unsent custom draft");
  assert.equal(await page.locator("#cst-notes").inputValue(), "Saved draft note");
  assert.equal(await page.evaluate(() => window.keptCustom === document.querySelector("#cst-dx-custom-input") && window.keptNotes === document.querySelector("#cst-notes")), true);
  assert.equal(await page.evaluate(() => window.summaryMounts), 1);
});

test("whole native entry clears clinical DOM on exit and remounts once on restoration", async t => {
  const { page } = await fixture(t, { nativeEntry: true });
  await page.locator("#cst-dx-custom-input").fill("Synthetic private draft");
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  assert.equal(await page.locator("#sec-consulta").textContent(), "");
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await page.locator("#cst-review").waitFor();
  assert.equal(await page.locator("#cst-dx-custom-input").inputValue(), "");
  assert.equal(await page.locator("#cst-review").count(), 1);
  assert.equal(await page.evaluate(() => window.summaryMounts), 2);
});

test("whole native entry offers real reload after a rejected module import", async t => {
  const { page, entryStats } = await fixture(t, { nativeEntry: true, rejectFirstEntry: true });
  assert.equal(await page.locator("#sec-consulta .cst-head").count(), 0);
  await page.locator("[data-retry]").click();
  await page.locator("#cst-review").waitFor();
  assert.equal(entryStats.requests, 2);
  assert.equal(await page.locator(".cst-head h2").textContent(), "Fixture Alpha");
});

test("whole native entry held past the deadline shows the late copy, then mounts", async t => {
  let release;
  const held = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const { page } = await fixture(t, { nativeEntry: true, holdEntry: held });
  assert.equal(await page.locator(".screen-load-slow [data-retry]").count(), 1);
  assert.equal(await page.locator("#sec-consulta [data-retry]").count(), 0);
  release();
  await page.locator("#cst-review").waitFor();
  assert.equal(await page.locator(".screen-load-slow").count(), 0);
  assert.equal(await page.locator(".cst-head h2").textContent(), "Fixture Alpha");
});


test("explicit clinical choices omit model percentages and review a manual study before drafting", async t => {
  const { page, posts } = await fixture(t, { data: {
    differential: [{ id: "angina", prob: 0.8 }, { id: "other" }], suggested_exams: ["ecg"]
  } });
  assert.equal(await page.locator("[data-dx]:checked, [data-exam]:checked").count(), 0);
  assert.doesNotMatch(await page.locator("#sec-consulta").innerText(), /\b\d+%/);
  assert.equal(await page.locator(".cst-aviso").count(), 0, "un solo aviso de IA en la tarjeta");
  assert.match(await page.locator("[data-ai-notice-inline]").innerText(), /no son un diagnóstico ni una recomendación de prescripción/);
  await page.locator("#cst-exam-custom-input").fill("Manual synthetic study");
  await page.locator("[data-add-exam]").click();
  await page.locator("#cst-review").click();
  assert.match(await page.locator("#cst-review-card").innerText(), /Fixture Alpha.*DNI synthetic/);
  assert.match(await page.locator("#cst-review-card").innerText(), /Manual synthetic study/);
  assert.equal(posts.length, 0);
  if (process.env.CLINICAL_SCREENSHOT_DIR) {
    mkdirSync(process.env.CLINICAL_SCREENSHOT_DIR, { recursive: true });
    for (const [name, width] of [["desktop",1280],["mobile",390]]) {
      await page.setViewportSize({width,height:900});
      await page.screenshot({path:process.env.CLINICAL_SCREENSHOT_DIR + "/clinical-choice-" + name + ".png",fullPage:true});
    }
  }
  await page.locator("[data-review-cancel]").click();
  assert.equal(posts.length, 0);
  await page.locator("#cst-review").click();
  await page.locator("#cst-confirmar").click();
  // The click returns before the route handler records the POST: wait for the
  // confirmed receipt it produces, as the other confirmation tests do.
  await page.locator('a[href="/r/fixture"]').waitFor();
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].estudios, [{label:"Manual synthetic study",custom:true}]);
  assert.deepEqual(posts[0].patient_reviewed, {nombre:"Fixture Alpha",dni:"synthetic"});
  assert.deepEqual(posts[0].choices_reviewed, {
    diagnosticos: [], estudios: ["Manual synthetic study"], medicacion: [], notas: ""
  });
});
