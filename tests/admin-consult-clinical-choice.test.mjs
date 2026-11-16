import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../app/modules/intake/admin-consult.mjs";

// Elección clínica explícita (2026-10-21): nada viene preseleccionado, cada
// estudio de la orden es una elección del clínico (un diagnóstico nunca marca
// sus estudios_sugeridos), la revisión gatea la generación de documentos y
// una consulta sin salida de IA se resuelve entera a mano. El andamiaje
// (Host, target, fixture) espeja admin-consult-lifecycle.test.mjs.

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(turns = 8) { while (turns--) await tick(); }
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
function target(attrs = {}, value = "") {
  return { value, checked: !!attrs.checked, id: attrs.id || "", getAttribute(name) { return attrs[name] == null ? null : String(attrs[name]); }, closest(selector) { return matches(selector, attrs, this) ? this : null; } };
}
function matches(selector, attrs, node) {
  if (selector.startsWith('#')) return node.id === selector.slice(1);
  const name = /^\[([^\]]+)\]$/.exec(selector);
  return !!name && attrs[name[1]] != null;
}

const I18N = "export const t=(k,v,f)=>f||k";
const DATES = "export const relativo=()=>''";
const SUMMARY = "export const mount=()=>Promise.resolve()";
const SESSION = "export const confirmar=(r,b,current)=>{globalThis.__consultConfirm.push({status:r.status,body:b,current:current()});return Promise.resolve(globalThis.__consultTransition && current())}";
const CONSULT = {
  id: "A", nombre: "Ana", dni: "12", status: "done", triage: "VERDE", analyzed_at: "2026-01-01",
  medico: { nombre: "Dra. Fernández", matricula: "1234" },
  catalogos: { diagnosticos: [{ id: "dx", label: "Diagnóstico", codigo: "X", sistema: "CIE", estudios_sugeridos: ["exam"] }], estudios: [{ id: "exam", label: "Estudio" }], medicamentos: [], frecuencias: [] },
  differential: [{ id: "dx", prob: .8 }], suggested_exams: ["exam"], mensajes: []
};
const NO_AI = { ...CONSULT, differential: [], suggested_exams: [] };
const ACK = { ok: true, receta_url: "/r/a", orden_url: "/o/a" };

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
const posts = (page) => page.calls.filter(({ options }) => options.method === "POST");

test("nada viene preseleccionado: la IA sugiere, el aviso aclara y el clínico elige", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT))] });
  const dispose = page.run(); await dispose.ready;
  assert.doesNotMatch(page.host.innerHTML, /data-dx="dx" checked/, "el diferencial no se marca solo");
  assert.doesNotMatch(page.host.innerHTML, /data-exam="exam" checked/, "el estudio sugerido no se marca solo");
  assert.doesNotMatch(page.host.innerHTML, /\b(?:80|0)%|Probabilidad estimada/, "raw model probabilities are not clinical estimates");
  assert.equal(page.host.innerHTML.split("data-ai-notice-inline=").length - 1, 1, "un solo aviso de IA, el del registro");
  assert.match(page.host.innerHTML, /data-ai-notice-inline="clinical">[^<]*no son un diagnóstico ni una recomendación de prescripción/, "el aviso único dice que no es un diagnóstico");
  assert.doesNotMatch(page.host.innerHTML, /cst-aviso/, "sin segundo aviso bajo el diferencial");
  assert.match(page.host.innerHTML, /cst-pick-code">Sugerido</, "el estudio lleva la etiqueta Sugerido");
  dispose();
});

test("un diagnóstico nunca marca sus estudios_sugeridos: la orden sale sólo con elecciones explícitas", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, ACK))] });
  const dispose = page.run(); await dispose.ready;
  page.host.emit("change", target({ "data-dx": "dx", checked: true }));
  page.host.emit("click", target({ id: "cst-review" }));
  const card = page.host.regions.get("#cst-review-card");
  assert.match(card.innerHTML, /Diagnóstico/, "el diagnóstico elegido queda en la revisión");
  assert.match(card.innerHTML, /Estudios a solicitar:<\/b> Ninguno/, "el estudio sugerido NO se marcó solo");
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(posts(page)[0].options.body);
  assert.equal(payload.diagnosticos.length, 1);
  assert.deepEqual(payload.estudios, [], "la orden no hereda estudios del diagnóstico");
  dispose();
});

test("la revisión muestra paciente, médico y exactamente lo que se va a generar; cancelar conserva la edición", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, ACK))] });
  const dispose = page.run(); await dispose.ready;
  page.host.emit("change", target({ "data-dx": "dx", checked: true }));
  page.host.emit("change", target({ "data-exam": "exam", checked: true }));
  page.host.emit("click", target({ id: "cst-review" }));
  const card = page.host.regions.get("#cst-review-card");
  assert.ok(card, "la tarjeta de revisión está pintada");
  assert.match(card.innerHTML, /Revisá antes de generar/);
  assert.match(card.innerHTML, /Ana — DNI 12/);
  assert.match(card.innerHTML, /Dra\. Fernández/);
  assert.match(card.innerHTML, /Diagnóstico/);
  assert.match(card.innerHTML, /no se firma ni se envía solo/);
  page.host.emit("input", target({ id: "cst-notes" }, "Reposo"));
  assert.match(page.host.regions.get("#cst-review-card").innerHTML, /Reposo/, "la tarjeta se actualiza en vivo");
  assert.equal(posts(page).length, 0, "revisar no genera ni envía nada");
  page.host.emit("click", target({ "data-review-cancel": "" }));
  assert.ok(!page.host.regions.get("#cst-review-card"), "cancelar vuelve al editor");
  assert.match(page.host.innerHTML, /data-dx="dx" checked/, "las elecciones siguen ahí");
  page.host.emit("click", target({ id: "cst-review" }));
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(posts(page)[0].options.body);
  assert.deepEqual(payload.diagnosticos, [{ id: "dx", label: "Diagnóstico", codigo: "X", sistema: "CIE", custom: false }]);
  assert.deepEqual(payload.estudios, [{ id: "exam", label: "Estudio", custom: false }]);
  assert.equal(payload.notas, "Reposo");
  assert.deepEqual(payload.patient_reviewed, { nombre: "Ana", dni: "12" });
  assert.deepEqual(payload.choices_reviewed, {
    diagnosticos: ["Diagnóstico (CIE X)"], estudios: ["Estudio"], medicacion: [], notas: "Reposo"
  });
  assert.match(card.innerHTML, /Diagnóstico \(CIE X\)/, "el código persistido también está en la revisión");
  dispose();
});

test("una consulta sin salida de IA se resuelve entera a mano, sin nada extra saliendo de la página", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, NO_AI)), Promise.resolve(response(200, ACK))] });
  const dispose = page.run(); await dispose.ready;
  assert.doesNotMatch(page.host.innerHTML, /no son un diagnóstico/, "sin sugerencias el aviso no habla de ellas");
  assert.doesNotMatch(page.host.innerHTML, /Sugerido/);
  page.host.controls.set("#cst-dx-custom-input", { value: "Mi diagnóstico manual" });
  page.host.emit("click", target({ "data-add-dx": "" }));
  page.host.controls.set("#cst-exam-custom-input", { value: "Mi estudio manual" });
  page.host.emit("click", target({ "data-add-exam": "" }));
  page.host.emit("click", target({ "data-add-med": "" }));
  page.host.emit("input", target({ "data-med-field": "nombre", "data-med-index": "0" }, "Ibuprofeno"));
  page.host.emit("input", target({ "data-med-field": "dosis", "data-med-index": "0" }, "400 mg"));
  page.host.emit("click", target({ id: "cst-review" }));
  const card = page.host.regions.get("#cst-review-card");
  assert.match(card.innerHTML, /Mi diagnóstico manual/);
  assert.match(card.innerHTML, /Mi estudio manual/);
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  const payload = JSON.parse(posts(page)[0].options.body);
  assert.deepEqual(payload.diagnosticos, [{ label: "Mi diagnóstico manual", custom: true }]);
  assert.deepEqual(payload.estudios, [{ label: "Mi estudio manual", custom: true }]);
  assert.deepEqual(payload.medicacion, [{ nombre: "Ibuprofeno", dosis: "400 mg", frecuencia: "", duracion: "", custom: true }]);
  assert.deepEqual(payload.choices_reviewed, {
    diagnosticos: ["Mi diagnóstico manual"], estudios: ["Mi estudio manual"],
    medicacion: ["Ibuprofeno — 400 mg"], notas: ""
  });
  assert.equal(page.calls.length, 2, "sólo el GET inicial y el POST: nada más sale de la página");
  dispose();
});

test("una revisión abierta no se pinta sobre otra paciente y se recupera al volver", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)),
    Promise.resolve(response(200, { ...CONSULT, id: "B", nombre: "Beatriz", dni: "34", medico: undefined })),
    Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, ACK))] });
  const dispose = page.run(); await dispose.ready;
  page.host.emit("change", target({ "data-dx": "dx", checked: true }));
  page.host.emit("click", target({ id: "cst-review" }));
  assert.match(page.host.regions.get("#cst-review-card").innerHTML, /Ana/);
  globalThis.location.search = "?interview=B"; await page.events.get("popstate")();
  assert.match(page.host.innerHTML, /Beatriz/);
  assert.ok(!page.host.regions.get("#cst-review-card"), "la revisión de Ana no se pinta sobre Beatriz");
  assert.doesNotMatch(page.host.innerHTML, /Confirmar datos y generar borrador/, "Beatriz está en el editor, no en revisión");
  globalThis.location.search = "?interview=A"; await page.events.get("popstate")();
  assert.match(page.host.regions.get("#cst-review-card").innerHTML, /Ana/, "la revisión de Ana se recupera al volver");
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.equal(posts(page).length, 1);
  const payload = JSON.parse(posts(page)[0].options.body);
  assert.equal(payload.diagnosticos.length, 1);
  dispose();
});

test("tras confirmar, un montaje fresco arranca vacío y puede generar de nuevo", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, ACK)),
    Promise.resolve(response(200, CONSULT)), Promise.resolve(response(200, ACK))] });
  const first = page.run(); await first.ready;
  page.host.emit("change", target({ "data-dx": "dx", checked: true }));
  page.host.emit("click", target({ id: "cst-review" }));
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.match(page.host.innerHTML, /Borradores generados/);
  const second = page.run(); await second.ready;
  assert.doesNotMatch(page.host.innerHTML, /Borradores generados/, "el montaje fresco no hereda la confirmación");
  assert.doesNotMatch(page.host.innerHTML, /data-dx="dx" checked/, "el montaje fresco no preselecciona nada");
  assert.ok(!page.host.regions.get("#cst-review-card"));
  page.host.emit("click", target({ id: "cst-review" }));
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.equal(posts(page).length, 2);
  assert.deepEqual(JSON.parse(posts(page)[1].options.body).diagnosticos, []);
  first(); second();
});


test("direct confirmation cannot bypass review and changing exams refreshes the reviewed draft", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, CONSULT))] });
  const dispose = page.run(); await dispose.ready;
  page.host.emit("click", target({ id: "cst-confirmar" })); await settle();
  assert.equal(posts(page).length, 0);
  page.host.emit("click", target({ id: "cst-review" }));
  page.host.emit("change", target({ "data-exam": "exam", checked: true }));
  assert.match(page.host.regions.get("#cst-review-card").innerHTML, /Estudios a solicitar:<\/b> Estudio/);
  page.host.emit("change", target({ "data-exam": "exam", checked: false }));
  assert.match(page.host.regions.get("#cst-review-card").innerHTML, /Estudios a solicitar:<\/b> Ninguno/);
  dispose();
});

test("missing model probability has no zero-percent fallback", async (t) => {
  const page = fixture(t, { routes: [Promise.resolve(response(200, { ...CONSULT, differential: [{ id: "dx" }] }))] });
  const dispose = page.run(); await dispose.ready;
  assert.doesNotMatch(page.host.innerHTML, /\b(?:80|0)%|Probabilidad estimada/);
  dispose();
});


test("catalog medication defaults are visible in an already open review", async (t) => {
  const consultation = { ...CONSULT, catalogos: { ...CONSULT.catalogos,
    medicamentos: [{ nombre: "Synthetic medicine", dosis: "fixture dose", frecuencia: "fixture frequency", duracion: "fixture duration" }] } };
  const page = fixture(t, { routes: [Promise.resolve(response(200, consultation))] });
  const dispose = page.run(); await dispose.ready;
  page.host.emit("click", target({ "data-add-med": "" }));
  page.host.emit("click", target({ id: "cst-review" }));
  const medication = target({ "data-med-field": "nombre", "data-med-index": "0" }, "Synthetic medicine");
  page.host.emit("input", medication);
  page.host.emit("change", medication);
  assert.match(page.host.regions.get("#cst-review-card").innerHTML, /Synthetic medicine — fixture dose — fixture frequency — fixture duration/);
  assert.equal(posts(page).length, 0);
  dispose();
});
