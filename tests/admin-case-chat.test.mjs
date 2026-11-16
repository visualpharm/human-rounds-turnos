/* admin-case-chat.test.mjs — «Su caso» in the request detail (the product owner,
   2026-11-07): the desk reads the summary first (urgency with its dot, what
   the patient wants, why, key facts), then opens the whole conversation on
   demand — a read the server writes to the clinical access log
   (tests/test_case_chat.py). The real casoHtml/casoChatHtml/casoVer source of
   admin-app.js runs in a vm; the panel slot is the real admin-solicitudes. */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")).phone;
const { detalleHtml, detalleResHtml } = await import("../static/platform/admin-solicitudes.mjs");

const APP = readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
const start = APP.indexOf("const CASO_URGENCIA = {");
const verStart = APP.indexOf("async function casoVer(", start);
const end = APP.indexOf("\n}\n", verStart) + 3;
assert.ok(start > 0 && verStart > start && end > verStart, "casoHtml block not found in admin-app.js");

function load(api) {
  const ctx = {
    T: (k, v, f) => f,
    esc: (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    api, encodeURIComponent,
  };
  vm.createContext(ctx);
  vm.runInContext(APP.slice(start, end) + "\nthis.casoHtml = casoHtml; this.casoVer = casoVer;", ctx);
  return ctx;
}

const RESUMEN = {
  quiere: "Mostrarle al médico unos análisis", urgencia: "alta",
  por_que: "Le pidieron verlos antes de la cirugía", hechos: ["Análisis del lunes", ""],
};

test("summary first: urgency with its dot, what they want, why, the facts", () => {
  const h = load().casoHtml({ id: 7, caso_resumen: RESUMEN, caso_chat: true });
  // amber, never red: red is for errors
  assert.match(h, /<p class="sol-caso-urg"><span class="sol-dot sol-d-warn"><\/span>Urgencia alta<\/p>/);
  assert.match(h, /<p class="sol-caso-quiere">Mostrarle al médico unos análisis<\/p>/);
  assert.match(h, /Le pidieron verlos antes de la cirugía/);
  assert.match(h, /<ul class="sol-caso-hechos"><li>Análisis del lunes<\/li><\/ul>/);
  assert.ok(h.indexOf("sol-caso-urg") < h.indexOf("data-caso-ver"), "the summary comes before the button");
  assert.match(h, /<button type="button" class="btn sol-caso-ver" data-caso-ver="7" aria-expanded="false">Ver conversación<\/button><div class="sol-caso-chat" hidden><\/div>/);
});

test("every urgency level reads as words, an emergency names guardia or 107", () => {
  const { casoHtml } = load();
  // only what is urgent carries a dot, and never a red one
  const want = { emergencia: ["warn", /guardia o 107/], media: ["", /Urgencia media/], baja: ["", /Puede esperar/] };
  for (const [nivel, [dot, texto]] of Object.entries(want)) {
    const h = casoHtml({ id: 1, caso_resumen: { quiere: "x", urgencia: nivel } });
    if (dot) assert.match(h, new RegExp(`sol-d-${dot}`), nivel);
    else assert.doesNotMatch(h, /sol-dot/, nivel);
    assert.doesNotMatch(h, /sol-d-bad/, nivel);
    assert.match(h, texto, nivel);
  }
  // urgency never said: no dot, no empty line
  assert.doesNotMatch(casoHtml({ id: 1, caso_resumen: { quiere: "x", urgencia: null } }), /sol-caso-urg/);
});

test("no case chat → nothing; the patient's words are escaped", () => {
  const { casoHtml } = load();
  assert.equal(casoHtml({ id: 1 }), "");
  const h = casoHtml({ id: 1, caso_resumen: { quiere: "<img src=x>", urgencia: "baja" } });
  assert.match(h, /&lt;img src=x&gt;/);
  assert.doesNotMatch(h, /data-caso-ver/, "no button without a stored conversation");
});

test("the panel shows «Su caso» in the pending and the resolved detail", () => {
  const casoHtml = load().casoHtml({ id: 7, caso_resumen: RESUMEN, caso_chat: true });
  const pend = detalleHtml({ id: "7", sug: "ok", nombre: "Ana Pérez", meta: "", pide: [], aprobarHtml: "", rechazarHtml: "", casoHtml });
  assert.match(pend, /<div class="sol-b sol-caso-b"><div class="sol-lbl">Su caso<\/div><div class="sol-caso-box">/);
  const res = detalleResHtml({ id: "7", nombre: "Ana Pérez", meta: "", kv: [], casoHtml });
  assert.match(res, /sol-caso-b/);
  assert.doesNotMatch(detalleHtml({ id: "8", sug: "ok", nombre: "B", meta: "", pide: [], aprobarHtml: "", rechazarHtml: "" }), /sol-caso-b/);
});

function button(id) {
  const box = { hidden: true, innerHTML: "", dataset: {} };
  const attrs = {};
  return {
    box, attrs, disabled: false, textContent: "Ver conversación", nextElementSibling: box,
    dataset: { casoVer: String(id) },
    setAttribute(k, v) { attrs[k] = v; },
  };
}

test("«Ver conversación» loads the chat once, then toggles", async () => {
  const calls = [];
  const api = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ mensajes: [
      { role: "asistente", texto: "¿Qué necesitás?" },
      { role: "paciente", texto: "Mostrar <análisis>" },
    ] }) };
  };
  const { casoVer } = load(api);
  const btn = button(7);
  await casoVer(btn);
  assert.deepEqual(calls, ["/api/admin/requests/7/case-chat"]);
  assert.equal(btn.box.hidden, false);
  assert.equal(btn.attrs["aria-expanded"], "true");
  assert.equal(btn.textContent, "Ocultar conversación");
  assert.match(btn.box.innerHTML, /<p class="sol-caso-msg sol-caso-asi"><b>Asistente<\/b> ¿Qué necesitás\?<\/p>/);
  assert.match(btn.box.innerHTML, /<b>Paciente<\/b> Mostrar &lt;análisis&gt;/);
  await casoVer(btn);
  assert.equal(btn.box.hidden, true);
  assert.equal(btn.textContent, "Ver conversación");
  await casoVer(btn);
  assert.equal(calls.length, 1, "one logged read per opening of the detail, not per toggle");
});

test("a refused or failed read says so in place and can be retried", async () => {
  let ok = false;
  const api = async () => (ok
    ? { ok: true, json: async () => ({ mensajes: [{ role: "paciente", texto: "hola" }] }) }
    : { ok: false, status: 403 });
  const { casoVer } = load(api);
  const btn = button(9);
  await casoVer(btn);
  assert.match(btn.box.innerHTML, /No se pudo abrir la conversación/);
  assert.equal(btn.disabled, false);
  btn.box.hidden = true;
  ok = true;
  await casoVer(btn);
  assert.match(btn.box.innerHTML, /hola/);
});

test("the reading's dot is never red: a suggestion to reject is a warning, like one to look twice", () => {
  const from = APP.indexOf("function dotDe(s){");
  const to = APP.indexOf("\n}\n", from) + 3;
  assert.ok(from > 0 && to > from, "dotDe not found in admin-app.js");
  const ctx = { suggestFor: (s) => s.sug };
  vm.createContext(ctx);
  vm.runInContext(APP.slice(from, to) + "\nthis.dotDe = dotDe;", ctx);
  assert.equal(ctx.dotDe({ tiene_orden: true, sug: "rechazar" }), "sol-d-warn");
  assert.equal(ctx.dotDe({ tiene_orden: true, sug: "revisar" }), "sol-d-warn");
  assert.equal(ctx.dotDe({ tiene_orden: true, sug: "aprobar" }), "sol-d-ok");
  assert.equal(ctx.dotDe({ tiene_orden: false, sug: "rechazar" }), "sol-d-none");
  assert.doesNotMatch(APP.slice(from, to), /sol-d-bad/);
});
