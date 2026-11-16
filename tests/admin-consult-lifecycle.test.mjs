import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../app/modules/intake/admin-consult.mjs";
import { API_ERROR_STUB } from "./fixtures/api-error-stub.mjs";

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(turns = 8) { while (turns--) await tick(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function dataUrl(source) { return "data:text/javascript," + encodeURIComponent(source); }
function response(status, body) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }

class Host {
  constructor() { this.innerHTML = ""; this.listeners = new Map(); this.isConnected = true; this.style = {}; this.controls = new Map(); }
  set innerHTML(value) {
    this.markup = value;
    this.regions = new Map();
    for (const match of value.matchAll(/<div[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/div>/g)) {
      this.regions.set("#" + match[1], { innerHTML: match[2] });
    }
  }
  get innerHTML() { return this.markup; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  querySelector(selector) { return this.controls.get(selector) || this.regions.get(selector) || null; }
  emit(name, target) { const fn = this.listeners.get(name); if (fn) fn({ target, preventDefault() {} }); }
}
function actionMarkup(host) { return host.querySelector("#cst-actions")?.innerHTML || ""; }
function target(attrs = {}, value = "") {
  return { value, checked: !!attrs.checked, id: attrs.id || "", getAttribute(name) { return attrs[name] == null ? null : String(attrs[name]); }, closest(selector) { return matches(selector, attrs, this) ? this : null; } };
}
function matches(selector, attrs, node) {
  if (selector.startsWith('#')) return node.id === selector.slice(1);
  const name = /^\[([^\]]+)\]$/.exec(selector);
  return !!name && attrs[name[1]] != null;
}

test('AI profile proposal needs a doctor action; unsaved edits cannot disappear silently', async t => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, {...CONSULT,
    perfil: {alergias:['Confirmed allergy']}, perfil_propuesta: {alergias:['Proposed allergy']}}))] });
  const dispose = page.run(); await dispose.ready;
  assert.match(page.host.innerHTML, /Propuesta de la IA, revisala/);
  assert.match(page.host.innerHTML, /cst-profile-apply/);
  assert.match(page.host.innerHTML, /textarea[^>]*data-profile-field="alergias"[^>]*>Confirmed allergy/);
  // Same control as «Indicaciones»: its padding keeps two rows above the 44px
  // target whatever the platform's textarea font is (C02 measured 42px).
  assert.match(page.host.innerHTML, /<textarea class="cst-in" data-profile-field="alergias"/);
  const field = target({'data-profile-field':'alergias'}, 'Edited allergy');
  field.dataset = {profileField:'alergias'};
  page.host.emit('input', field);
  page.host.emit('click', target({id:'cst-review'}));
  page.host.emit('click', target({id:'cst-confirmar'}));
  assert.equal(page.calls.length, 1, 'unreviewed edits block confirmation before network');
  assert.match(actionMarkup(page.host), /perfil sin guardar/);
  let warned = false;
  page.events.get('beforeunload')({preventDefault(){warned=true;}});
  assert.ok(warned);
  page.host.emit('click', target({id:'cst-profile-apply'}));
  assert.match(page.host.innerHTML, /textarea[^>]*data-profile-field="alergias"[^>]*>Proposed allergy/);
  assert.match(page.host.innerHTML, /id="cst-profile-reviewed" checked/);
  dispose();
  assert.equal(page.events.has('beforeunload'),false);
});

test('a profile proposal cannot replace the editor while its confirmation is in flight', async t => {
  const saving = deferred();
  const page = fixture(t, {routes:[Promise.resolve(response(200,{...CONSULT,
    perfil:{alergias:['Confirmed allergy']},perfil_propuesta:{alergias:['Proposed allergy']}})),saving.promise]});
  const dispose=page.run(); await dispose.ready;
  page.host.emit('click',target({id:'cst-review'}));
  page.host.emit('click',target({id:'cst-confirmar'}));
  page.host.emit('click',target({id:'cst-profile-apply'}));
  assert.match(page.host.innerHTML,/textarea[^>]*data-profile-field="alergias"[^>]*>Confirmed allergy/);
  saving.resolve(response(500,{}));await settle();dispose();
});
const I18N = "export const t=(k,v,f)=>f||k" + API_ERROR_STUB;
const DATES = "export const relativo=()=>''";
const SUMMARY = "export const mount=()=>Promise.resolve()";
const SESSION = "export const confirmar=(r,b,current)=>{globalThis.__consultConfirm.push({status:r.status,body:b,current:current()});return Promise.resolve(globalThis.__consultTransition && current())}";
const CONSULT = {
  id: "A", nombre: "Ana", dni: "12", status: "done", triage: "VERDE", analyzed_at: "2026-01-01",
  catalogos: { diagnosticos: [{ id: "dx", label: "Diagnóstico", codigo: "X", sistema: "CIE", estudios_sugeridos: ["exam"] }], estudios: [{ id: "exam", label: "Estudio" }], medicamentos: [], frecuencias: [] },
  differential: [{ id: "dx", prob: .8 }], suggested_exams: [], mensajes: []
};

function fixture(t, { routes = [], deps = null, interview = "A" } = {}) {
  const host = new Host(), events = new Map(), calls = [];
  const saved = Object.fromEntries(["window", "document", "fetch", "location", "AbortController", "__consultConfirm", "__consultTransition"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const defaultManifest = {
    "platform/dates.mjs": dataUrl(DATES), "platform/i18n.mjs": dataUrl(I18N),
    "platform/admin-patient-summary.mjs": dataUrl(SUMMARY), "platform/admin-session.mjs": dataUrl(SESSION)
  };
  const manifest = Object.assign({}, defaultManifest, deps || {});
  globalThis.__consultConfirm = []; globalThis.__consultTransition = false;
  globalThis.document = { head: { appendChild() {} }, getElementById() { return null; }, createElement() { return {}; } };
  globalThis.location = { search: "?interview=" + interview };
  globalThis.window = { SANA_STATIC: manifest, location: globalThis.location, addEventListener(name, fn) { events.set(name, fn); }, removeEventListener(name) { events.delete(name); } };
  globalThis.fetch = (url, options = {}) => { calls.push({ url, options }); const next = routes.shift(); assert.ok(next, "unexpected request " + url); return next; };
  t.after(() => { for (const [key, descriptor] of Object.entries(saved)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key]; });
  return { host, calls, events, run(options) { return mount(host, options); } };
}

// Review 03/11: /admin/consult without an interview stopped on «Falta el id»;
// the first load now sends the doctor to Pacientes de hoy without a read.
test("a first load without an interview goes to the queue without reading one", async (t) => {
  const page = fixture(t, { interview: "" });
  const replaced = [];
  globalThis.location.replace = (url) => replaced.push(url);
  await page.run().ready;
  assert.deepEqual(replaced, ["/admin/cola"]);
  assert.equal(page.calls.length, 0);
  assert.doesNotMatch(page.host.innerHTML, /Falta el id/);
});

test("a back step to a bare URL keeps the in-page missing-id state", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT))] });
  const replaced = [];
  globalThis.location.replace = (url) => replaced.push(url);
  await page.run().ready;
  globalThis.location.search = ""; await page.events.get("popstate")();
  assert.deepEqual(replaced, []);
  assert.match(page.host.innerHTML, /Falta el id/);
});

test("font settlement owns the first form paint and cannot revive a disposed mount", async (t) => {
  const fonts = deferred();
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT))] });
  globalThis.document.fonts = { ready: fonts.promise };
  const dispose = page.run(); await settle();
  assert.equal(page.calls.length, 1, 'data loads while fonts settle');
  assert.doesNotMatch(page.host.innerHTML, /cst-confirmar/);
  dispose(); fonts.resolve(); await dispose.ready;
  assert.doesNotMatch(page.host.innerHTML, /cst-confirmar/);
  assert.equal(page.host.listeners.size, 0);
});

test("font settlement preserves route ownership across navigation", async (t) => {
  const fonts = deferred();
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, { ...CONSULT, id: 'B', nombre: 'Beatriz' }))] });
  globalThis.document.fonts = { ready: fonts.promise };
  const dispose = page.run(); await settle();
  globalThis.location.search = '?interview=B'; page.events.get('popstate')(); await settle();
  fonts.resolve(); await settle();
  assert.match(page.host.innerHTML, /Beatriz/); assert.doesNotMatch(page.host.innerHTML, /Ana/);
  dispose();
});

test("later unrelated font loads cannot block navigation after initial readiness", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, { ...CONSULT, id: 'B', nombre: 'Beatriz' }))] });
  globalThis.document.fonts = { ready: Promise.resolve() };
  const dispose = page.run(); await dispose.ready;
  globalThis.document.fonts.ready = new Promise(() => {});
  globalThis.location.search = '?interview=B'; page.events.get('popstate')(); await settle();
  assert.match(page.host.innerHTML, /Beatriz/); dispose();
});

test("import is inert until mount resolves its owned dependencies", async (t) => {
  const pending = deferred(); globalThis.__consultDependencyGate = pending.promise;
  t.after(() => { delete globalThis.__consultDependencyGate; });
  const page = fixture(t, { routes: [], deps: { "platform/dates.mjs": dataUrl("await globalThis.__consultDependencyGate;export const relativo=()=>''") } });
  const dispose = page.run();
  await settle(); assert.equal(page.calls.length, 0);
  dispose(); pending.resolve({ relativo() {} });
  await dispose.ready.catch(() => {}); assert.equal(page.calls.length, 0);
});

test("dependency rejection propagates through ready without a request", async (t) => {
  const page = fixture(t, { deps: { "platform/dates.mjs": dataUrl("throw new Error('dates rejected')") } });
  await assert.rejects(page.run().ready, /dates rejected/); assert.equal(page.calls.length, 0);
});

test("positive control renders one consultation and retains the clinical payload", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, { ok: true, receta_url: "/r/a", orden_url: "/o/a" }))] });
  const dispose = page.run(); await dispose.ready;
  assert.match(page.host.innerHTML, /Ana/);
  page.host.emit("input", target({ id: "cst-notes" }, "Reposo"));
  page.host.emit("change", target({ "data-dx": "dx", checked: true }));
  // The exam is its own explicit choice now: nothing is preselected and a
  // diagnosis never checks its estudios_sugeridos behind the clinician.
  page.host.emit("change", target({ "data-exam": "exam", checked: true }));
  page.host.emit("click", target({ "data-add-med": "" }));
  page.host.emit("input", target({ "data-med-field": "nombre", "data-med-index": "0" }, "Ibuprofeno"));
  page.host.emit("input", target({ "data-med-field": "dosis", "data-med-index": "0" }, "400 mg"));
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(page.calls[1].options.body);
  assert.deepEqual(payload.diagnosticos, [{ id: "dx", label: "Diagnóstico", codigo: "X", sistema: "CIE", custom: false }]);
  assert.deepEqual(payload.estudios, [{ id: "exam", label: "Estudio", custom: false }]);
  assert.deepEqual(payload.medicacion, [{ nombre: "Ibuprofeno", dosis: "400 mg", frecuencia: "", duracion: "", custom: true }]);
  assert.equal(payload.notas, "Reposo"); assert.match(page.host.innerHTML, /Borradores generados/);
});

for (const late of [200, 503]) test("late read " + late + " cannot replace the current route", async (t) => {
  const a = deferred(), b = deferred(); const page = fixture(t, { routes: [a.promise, b.promise] });
  const dispose = page.run(); await settle(); globalThis.location.search = "?interview=B"; page.events.get("popstate")(); await settle();
  b.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" })); await settle(); assert.match(page.host.innerHTML, /Beatriz/);
  a.resolve(response(late, late === 200 ? CONSULT : { error: "base de datos no disponible" })); await settle();
  assert.match(page.host.innerHTML, /Beatriz/); assert.doesNotMatch(page.host.innerHTML, /Ana/); dispose();
});

test("route-owned drafts survive a refresh and never leak to a new patient", async (t) => {
  const first = deferred(), refresh = deferred(), b = deferred(); const page = fixture(t, { routes: [first.promise, refresh.promise, b.promise] });
  const dispose = page.run(); first.resolve(response(200, CONSULT)); await dispose.ready;
  page.host.emit("input", target({ id: "cst-notes" }, "Draft A"));
  page.events.get("popstate")(); await settle(); refresh.resolve(response(200, { ...CONSULT, summary: "Updated" })); await settle();
  assert.match(page.host.innerHTML, /Draft A/);
  globalThis.location.search = "?interview=B"; page.events.get("popstate")(); await settle(); b.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" })); await settle();
  assert.doesNotMatch(page.host.innerHTML, /Draft A/); dispose();
});

for (const outcome of ["success", "failure"]) test("late confirmation " + outcome + " cannot mutate replacement consultation", async (t) => {
  const post = deferred(), b = deferred(); const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), post.promise, b.promise] });
  const dispose = page.run(); await dispose.ready; page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  globalThis.location.search = "?interview=B"; page.events.get("popstate")(); await settle(); b.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" })); await settle();
  post.resolve(response(outcome === "success" ? 200 : 500, outcome === "success" ? { ok: true, receta_url: "/r/a", orden_url: "/o/a" } : { error: "server failed" })); await settle();
  assert.match(page.host.innerHTML, /Beatriz/); assert.doesNotMatch(page.host.innerHTML, /Borradores generados/); dispose();
});

test("confirmation is single-flight, malformed acknowledgement preserves the actionable draft", async (t) => {
  const post = deferred(); const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), post.promise, Promise.resolve(response(200, { ok: true, receta_url: "/r/a", orden_url: "/o/a" }))] });
  const dispose = page.run(); await dispose.ready; page.host.emit("input", target({ id: "cst-notes" }, "Keep me"));
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); assert.equal(page.calls.length, 2);
  post.resolve(response(200, { ok: true })); await settle(); assert.match(actionMarkup(page.host), /invalid confirmation response/);
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.equal(page.calls.length, 3);
  assert.equal(JSON.parse(page.calls[2].options.body).notas, "Keep me");
  assert.match(page.host.innerHTML, /Borradores generados/); dispose();
});

test("ordinary 403 stays local while an actual session transition disposes the owner", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(403, { error: "rol_no_autorizado" })), Promise.resolve(response(401, { error: "no_autenticado" }))] });
  const first = page.run(); await first.ready; assert.match(page.host.innerHTML, /No se pudo cargar/); assert.equal(globalThis.__consultConfirm[0].current, true);
  globalThis.__consultTransition = true; const second = page.run(); await second.ready; assert.equal(page.host.listeners.size, 0); assert.equal(page.host.innerHTML, "");
});

test("repeated mounts retire old listeners and an obsolete activation cannot submit", async (t) => {
  const first = deferred(); const page = fixture(t, { routes: [first.promise, Promise.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" }))] });
  const old = page.run(); await settle(); const oldClick = page.host.listeners.get("click"); const current = page.run({ interviewId: "B" }); await current.ready;
  oldClick({ target: target({ id: "cst-confirmar" }), preventDefault() {} }); assert.equal(page.calls.length, 2);
  first.resolve(response(200, CONSULT)); await settle(); assert.match(page.host.innerHTML, /Beatriz/); old(); current();
});

for (const outcome of ["success", "failure"]) test("pending confirmation settles its original draft after navigation and return: " + outcome, async (t) => {
  const post = deferred();
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), post.promise,
    Promise.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" })),
    Promise.resolve(response(200, CONSULT))] });
  const owner = page.run(); await owner.ready;
  page.host.emit("input", target({ id: "cst-notes" }, "Original draft"));
  page.host.emit("click", target({ id: "cst-review" }));
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  globalThis.location.search = "?interview=B"; await page.events.get("popstate")();
  assert.match(page.host.innerHTML, /Beatriz/);
  globalThis.location.search = "?interview=A"; await page.events.get("popstate")();
  assert.match(page.host.innerHTML, /Original draft/);
  assert.match(actionMarkup(page.host), /id="cst-confirmar" disabled/);
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  assert.equal(page.calls.filter(({ options }) => options.method === "POST").length, 1);
  post.resolve(response(outcome === "success" ? 200 : 500, outcome === "success"
    ? { ok: true, receta_url: "/r/a", orden_url: "/o/a" } : { error: "confirmation failed" }));
  await settle();
  if (outcome === "success") assert.match(page.host.innerHTML, /Borradores generados/);
  else { assert.match(actionMarkup(page.host), /confirmation failed/); assert.match(page.host.innerHTML, /Original draft/); }
  assert.doesNotMatch(actionMarkup(page.host), /id="cst-confirmar" disabled/);
  owner();
});

test("an obsolete confirm control cannot submit an already acknowledged consultation twice", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)),
    Promise.resolve(response(200, { ok: true, receta_url: "/r/a", orden_url: "/o/a" }))] });
  const owner = page.run(); await owner.ready;
  const confirm = target({ id: "cst-confirmar" });
  page.host.emit("click", target({id: "cst-review"}));
  page.host.emit("click", confirm); await settle();
  assert.match(page.host.innerHTML, /Borradores generados/);
  page.host.emit("click", confirm); await settle();
  assert.equal(page.calls.filter(({ options }) => options.method === "POST").length, 1);
  owner();
});

test("a stale confirmation auth failure cannot navigate a different interview", async (t) => {
  const post = deferred();
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), post.promise,
    Promise.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" })),
    Promise.resolve(response(200, CONSULT))] });
  const owner = page.run(); await owner.ready;
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  globalThis.location.search = "?interview=B"; await page.events.get("popstate")();
  globalThis.__consultTransition = true;
  post.resolve(response(401, { error: "session expired" })); await settle();
  assert.deepEqual(globalThis.__consultConfirm, []);
  assert.match(page.host.innerHTML, /Beatriz/);
  globalThis.location.search = "?interview=A"; await page.events.get("popstate")();
  assert.match(actionMarkup(page.host), /session expired/);
  assert.doesNotMatch(actionMarkup(page.host), /id="cst-confirmar" disabled/);
  owner();
});

test("cached drafts cannot be submitted through old controls while their new read is pending or failed", async (t) => {
  const refresh = deferred();
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), refresh.promise,
    Promise.resolve(response(200, CONSULT))] });
  const owner = page.run(); await owner.ready;
  const pending = page.events.get("popstate")();
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  refresh.resolve(response(503, { error: "base de datos no disponible" })); await pending;
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  assert.equal(page.calls.filter(({ options }) => options.method === "POST").length, 0);
  page.host.emit("click", target({ "data-consult-retry": "" })); await settle();
  assert.match(page.host.innerHTML, /Ana/);
  assert.equal(page.calls.length, 3);
  owner();
});

test("late transport rejection settles the saved draft without touching the current patient", async (t) => {
  const post = deferred();
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), post.promise,
    Promise.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz" })),
    Promise.resolve(response(200, CONSULT))] });
  const owner = page.run(); await owner.ready;
  page.host.emit("input", target({ id: "cst-notes" }, "Draft A"));
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" }));
  globalThis.location.search = "?interview=B"; await page.events.get("popstate")();
  post.reject(new Error("transport disconnected")); await settle();
  assert.match(page.host.innerHTML, /Beatriz/);
  assert.doesNotMatch(actionMarkup(page.host), /transport disconnected/);
  globalThis.location.search = "?interview=A"; await page.events.get("popstate")();
  assert.match(page.host.innerHTML, /Draft A/);
  assert.match(actionMarkup(page.host), /transport disconnected/);
  assert.doesNotMatch(actionMarkup(page.host), /id="cst-confirmar" disabled/);
  owner();
});

// ---- consulta ya confirmada (problems.md №1, 2026-10-23) ------------------
const EMITIDA = {
  id: 7, confirmada_at: "2026-10-23T10:00:00-03:00", confirmada_por: "dra.ana",
  diagnosticos: ["Bronquitis aguda"], estudios: ["Hemograma completo"],
  medicacion: ["Paracetamol 500 mg — 1 comprimido"], notas: "Reposo.",
  receta_url: "/r/vieja", orden_url: "/o/vieja", reemplazos: 0,
  seleccion: { diagnosticos: [{ id: "dx", label: "Diagnóstico", codigo: "X", sistema: "CIE", custom: false }, { id: null, label: "Bronquitis aguda", custom: true }],
    estudios: [{ id: "exam", label: "Estudio", custom: false }],
    medicacion: [{ nombre: "Paracetamol 500 mg", dosis: "1 comprimido", frecuencia: "", duracion: "", custom: true }], notas: "Reposo." }
};

test("a confirmed consultation reopens read-only with its documents, never an empty form", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, { ...CONSULT, confirmada: EMITIDA }))] });
  const owner = page.run(); await owner.ready;
  assert.match(page.host.innerHTML, /href="\/r\/vieja"/);
  assert.match(page.host.innerHTML, /href="\/o\/vieja"/);
  assert.match(page.host.innerHTML, /Bronquitis aguda/);
  assert.match(page.host.innerHTML, /Paracetamol 500 mg — 1 comprimido/);
  assert.doesNotMatch(page.host.innerHTML, /id="cst-review"/);
  assert.doesNotMatch(page.host.innerHTML, /id="cst-dx-search"/);
  assert.match(page.host.innerHTML, /id="cst-reemplazar"/);
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.equal(page.calls.filter(({ options }) => options.method === "POST").length, 0);
  owner();
});

test("replacing is an explicit, confirmed choice that names the consultation it voids", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, { ...CONSULT, confirmada: EMITIDA })),
    Promise.resolve(response(200, { ok: true, receta_url: "/r/nueva", orden_url: "/o/nueva" }))] });
  const owner = page.run(); await owner.ready;
  const asked = [];
  globalThis.window.confirm = (text) => { asked.push(text); return asked.length > 1; };
  page.host.emit("click", target({ id: "cst-reemplazar" }));
  assert.equal(asked.length, 1); assert.match(asked[0], /ANULADAS/);
  assert.doesNotMatch(page.host.innerHTML, /id="cst-review"/, "a declined prompt keeps the read-only view");
  page.host.emit("click", target({ id: "cst-reemplazar" }));
  assert.match(page.host.innerHTML, /id="cst-review"/);
  // the form starts from what was issued, so a correction is one change
  assert.match(page.host.innerHTML, /data-dx="dx" checked/);
  assert.match(page.host.innerHTML, /data-exam="exam" checked/);
  assert.match(page.host.innerHTML, /Bronquitis aguda/);
  assert.match(page.host.innerHTML, /value="Paracetamol 500 mg"/);
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(page.calls[1].options.body);
  assert.equal(payload.reemplaza_id, 7);
  assert.equal(payload.notas, "Reposo.");
  assert.match(page.host.innerHTML, /href="\/r\/nueva"/);
  owner();
});

test("a fresh consultation never sends reemplaza_id", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, { ...CONSULT, confirmada: null })),
    Promise.resolve(response(200, { ok: true, receta_url: "/r/a", orden_url: "/o/a" }))] });
  const owner = page.run(); await owner.ready;
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.equal("reemplaza_id" in JSON.parse(page.calls[1].options.body), false);
  owner();
});

test("a 409 because someone else confirmed first shows what was issued instead of the form", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)),
    Promise.resolve(response(409, { error: "Esta consulta ya tiene receta y orden emitidas.", confirmada: EMITIDA }))] });
  const owner = page.run(); await owner.ready;
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.match(page.host.innerHTML, /href="\/r\/vieja"/);
  assert.match(page.host.innerHTML, /ya tiene receta y orden emitidas/);
  assert.doesNotMatch(page.host.innerHTML, /id="cst-review"/);
  owner();
});

// ---- diagnóstico a mano con código CIE-10 (doctor.json m14, 2026-10-23) ----
test("a specialty without a diagnosis catalog says so and points to the manual entry", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, { ...CONSULT, differential: [], catalogos: { ...CONSULT.catalogos, diagnosticos: [] } }))] });
  const owner = page.run(); await owner.ready;
  assert.match(page.host.innerHTML, /No hay diagnósticos cargados para esta especialidad/);
  assert.match(page.host.innerHTML, /id="cst-dx-custom-code"/);
  owner();
});

test("a manual diagnosis carries its normalized CIE-10 code; a malformed code is not added", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)),
    Promise.resolve(response(200, { ok: true, receta_url: "/r/a", orden_url: "/o/a" }))] });
  const owner = page.run(); await owner.ready;
  const label = { value: "Bronquitis aguda" }, code = { value: "bronq", attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; }, removeAttribute(name) { delete this.attrs[name]; }, focus() { this.focused = true; } };
  page.host.controls.set("#cst-dx-custom-input", label); page.host.controls.set("#cst-dx-custom-code", code);
  page.host.emit("click", target({ "data-add-dx": "" }));
  assert.equal(label.value, "Bronquitis aguda", "a malformed code keeps the typed text for correction");
  // under its own row, not next to the confirm button far below (live demo 2026-10-25)
  assert.match(page.host.querySelector("#cst-dx-custom")?.innerHTML || "", /role="alert">«bronq» no es un código CIE-10/);
  assert.doesNotMatch(actionMarkup(page.host), /no es un código CIE-10/);
  assert.equal(code.attrs?.["aria-invalid"], "true"); assert.equal(code.focused, true);
  code.value = " j209 ";
  page.host.emit("click", target({ "data-add-dx": "" }));
  assert.equal(label.value, ""); assert.equal(code.value, "");
  assert.equal(code.attrs["aria-invalid"], undefined);
  assert.doesNotMatch(page.host.querySelector("#cst-dx-custom")?.innerHTML || "", /no es un código CIE-10/);
  page.host.emit("click", target({ id: "cst-review" })); page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(page.calls[1].options.body);
  assert.deepEqual(payload.diagnosticos, [{ label: "Bronquitis aguda", codigo: "J20.9", sistema: "CIE-10", custom: true }]);
  assert.deepEqual(payload.choices_reviewed.diagnosticos, ["Bronquitis aguda (CIE-10 J20.9)"]);
  owner();
});

test("an English page shows ICD-10 on the picker, chip and review; what is sent and stored stays CIE-10", async (t) => {
  const english = "export const t=(k,v,f)=>k==='admin.reports.consulta.sistema_cie10'?'ICD-10':(f||k)" + API_ERROR_STUB;
  const coded = { ...CONSULT, differential: [], catalogos: { ...CONSULT.catalogos, diagnosticos: [{ id: "dx", label: "Diagnóstico", codigo: "J20.9", sistema: "CIE-10" }] } };
  const page = fixture(t, { deps: { "platform/i18n.mjs": dataUrl(english) }, routes: [Promise.resolve(response(200, coded)),
    Promise.resolve(response(200, { ok: true, receta_url: "/r/a", orden_url: "/o/a" }))] });
  const owner = page.run(); await owner.ready;
  assert.match(page.host.innerHTML, /ICD-10 J20\.9/); assert.doesNotMatch(page.host.innerHTML, /CIE-10 J20\.9/);
  page.host.emit("change", target({ "data-dx": "dx" }));
  const label = { value: "Bronquitis aguda" }, code = { value: "j209", attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; }, removeAttribute(name) { delete this.attrs[name]; }, focus() {} };
  page.host.controls.set("#cst-dx-custom-input", label); page.host.controls.set("#cst-dx-custom-code", code);
  page.host.emit("click", target({ "data-add-dx": "" }));
  assert.match(page.host.querySelector("#cst-dx-custom")?.innerHTML || "", /ICD-10 J20\.9/);
  page.host.emit("click", target({ id: "cst-review" }));
  assert.match(page.host.innerHTML, /Bronquitis aguda \(ICD-10 J20\.9\)/);
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(page.calls[1].options.body);
  assert.equal(payload.diagnosticos[0].sistema, "CIE-10");
  assert.deepEqual(payload.choices_reviewed.diagnosticos, ["Bronquitis aguda (CIE-10 J20.9)"]);
  owner();
});
