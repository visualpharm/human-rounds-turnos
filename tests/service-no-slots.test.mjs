/* service-no-slots.test.mjs — «Ahora no hay turnos» (contraparte técnica, hospital,
   2026-10-17): the wizard's empty-grid step offers two things and nothing
   it cannot back with data — when turnos appear again, and «Contanos tu
   caso», whose text travels with the same slotless request the desk already
   handles. The center phone was a third option until the reference-deployment counterpart dropped it
   (2026-10-18).

   The wizard isn't a loadable module, so the real stepFranja() source runs in
   a vm with the real dates/format/i18n modules and a tiny host stub. */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")).phone;
const Dates = await import("../static/platform/dates.mjs");
const Format = (await import("../static/platform/format.mjs")).default;
const I18n = await import("../static/platform/i18n.mjs");

const src = readFileSync(new URL("../app/modules/turnos/service.mjs", import.meta.url), "utf8");
const start = src.indexOf("    function stepFranja(d) {");
const end = src.indexOf("\n    }\n", start) + "\n    }\n".length;
const stepSrc = src.slice(start, end);

function el() {
  const listeners = {};
  return {
    value: "", textContent: "", focused: false, hidden: true,
    addEventListener(type, fn) { listeners[type] = fn; },
    fire(type) { listeners[type] && listeners[type]({ target: this }); },
    focus() { this.focused = true; },
  };
}

/* «Contanos tu caso» as a conversation (the product owner, 2026-11-07): casoChatStart is
   stubbed here — `chat` null is the no-AI path (the plain box), otherwise the
   chat the patient answered. The conversation itself is the e2e's
   (journey-j21) and the casoChatStart vm test below. */
function chatStub(palabras, token = "tok") {
  return {
    focused: false, terminado: 0, token,
    activo: () => true, focus() { this.focused = true; },
    palabras: () => palabras, terminar() { this.terminado += 1; },
  };
}

function run(apiPayload, { mode = "turno", catalog = {}, chat = null } = {}) {
  I18n.setCatalog(catalog);
  const els = { "#wz-caso": el(), "#wz-caso-msg": el(), "#wz-horario-next": el() };
  const ctx = {
    casoChatStart(host, msg, sinChat) {
      if (!chat) { sinChat(); return { activo: () => false }; }
      ctx.wiz.casoChatToken = chat.token;
      return chat;
    },
    wiz: {}, MODE: mode, Dates, Format, sub: "", advanced: 0, SUB_EL: null,
    host: { innerHTML: "" },
    T: (k, v, f) => I18n.t(k, v, f),
    esc: (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])),
    $: (sel) => els[sel],
    formatPhone: (s) => Format.telTexto(s),
    ensureStep() {}, setStep() {}, settleScheduleFallback() {}, stepHeader: () => "",
  };
  ctx.setSub = (h) => { ctx.sub = h; };
  ctx.afterHorario = () => { ctx.advanced += 1; };
  vm.createContext(ctx);
  vm.runInContext(stepSrc + "\nstepFranja(__payload);", Object.assign(ctx, { __payload: apiPayload }));
  return { ctx, els, html: ctx.host.innerHTML };
}

const payload = {
  sin_turnos: {
    apertura: { estado: "fecha", dia: "2031-03-11", hora: "07:00" },
    telefono: "(02254) 49-1234", centro_nombre: "CAPS Ostende",
  },
};

test("the franja/«A partir de» are gone", () => {
  const { html } = run(payload);
  assert.doesNotMatch(html, /wz-franja|wz-desde|Franja horaria|A partir de|Más flexibilidad/);
});

test("«no hay turnos» is said once, in the lead; the card repeats nothing (TASK-15)", () => {
  for (const sin of [payload, { sin_turnos: { apertura: { estado: "sin_fechas" } } }, { sin_turnos: {} }, {}]) {
    const { ctx, html } = run(sin);
    assert.equal(ctx.sub, "Ahora no hay turnos.", JSON.stringify(sin));
    assert.doesNotMatch(html, /No hay turnos disponibles|Ahora no hay turnos/, JSON.stringify(sin));
  }
});

test("option 1: the server-computed opening as a human date, via the shared formatter", () => {
  const { html } = run(payload);
  assert.match(html, /Se habilitan a partir de:/);
  assert.match(html, /<p class="wz-noslots-val">Martes 11 de marzo de 2031, 07:00<\/p>/);
});

test("option 1 without future dates or without data is omitted: the lead already says it", () => {
  // una sola frase cuando no hay fecha de apertura (responsable del producto, 20/10)
  const sin = run({ sin_turnos: { apertura: { estado: "sin_fechas" } } }).html;
  assert.doesNotMatch(sin, /wz-noslots-when|fechas nuevas/);
  const none = run({ sin_turnos: {} }).html;
  assert.doesNotMatch(none, /wz-noslots-when/);
  assert.doesNotMatch(run({}).html, /wz-noslots-when|wz-noslots-call/);
});

test("urgency: 107 or the guardia, next to «Contanos tu caso» (the reference-deployment counterpart, 2026-10-19)", () => {
  const { html } = run(payload);
  assert.match(html, /Si es una urgencia, llamá al 107 o acercate a la guardia\./);
  assert.match(html, /sobreturno/);
  // the sobreturno line no longer says «urgente» — that word belongs to the
  // 107/guardia line under it (the reference-deployment counterpart, 2026-10-20)
  assert.match(html, /Si necesitás un sobreturno, contanos qué te pasa y por qué no podés esperar al próximo turno disponible\./);
  assert.doesNotMatch(html, /es urgente/);
  // said once, inside the case option, not as a third option
  assert.equal((html.match(/llamá al 107/g) || []).length, 1);
  assert.equal((html.match(/class="wz-noslots-opt"/g) || []).length, 2);
});

test("no phone option, even when an old payload still carries one (the reference-deployment counterpart, 2026-10-18)", () => {
  const { html } = run(payload);
  assert.doesNotMatch(html, /wz-noslots-call|¿No podés esperar|tel:/);
  assert.equal((html.match(/class="wz-noslots-opt"/g) || []).length, 2);
});

test("option 2: the case is required without an order, then travels as wiz.caso", () => {
  const { ctx, els } = run(payload);
  els["#wz-horario-next"].fire("click");
  assert.equal(ctx.advanced, 0);
  assert.equal(els["#wz-caso-msg"].textContent, "Escribí tu caso para que el hospital lo evalúe.");
  assert.ok(els["#wz-caso"].focused);
  els["#wz-caso"].value = "  Tengo un control post operatorio la semana que viene.  ";
  els["#wz-horario-next"].fire("click");
  assert.equal(ctx.advanced, 1);
  assert.equal(ctx.wiz.caso, "Tengo un control post operatorio la semana que viene.");
  assert.equal(ctx.wiz.sinTurno, true);
  // the slotless request contract the desk already reads is unchanged
  assert.equal(ctx.wiz.preferencia, "Cualquier horario");
});

test("without the AI the plain box is shown and the case travels the old way", () => {
  const { ctx, els } = run(payload);
  assert.equal(els["#wz-caso"].hidden, false);
  els["#wz-caso"].value = "Necesito el control.";
  els["#wz-horario-next"].fire("click");
  assert.equal(ctx.wiz.caso, "Necesito el control.");
  assert.equal(ctx.wiz.casoChat, null);
});

test("the conversation: Continuar asks for an answer, then sends the patient's words and the chat", () => {
  const vacio = chatStub("");
  const first = run(payload, { chat: vacio });
  assert.notEqual(first.els["#wz-caso"].hidden, false, "the plain box stays hidden while the chat runs");
  first.els["#wz-horario-next"].fire("click");
  assert.equal(first.ctx.advanced, 0);
  assert.equal(first.els["#wz-caso-msg"].textContent, "Contestá en la conversación para que el hospital evalúe tu caso.");
  assert.ok(vacio.focused);
  assert.equal(vacio.terminado, 0);

  // One answer is enough: the patient may stop before the last question.
  const respondido = chatStub("Tengo resultados para mostrar · Esta semana", "tok-1");
  const { ctx, els } = run(payload, { chat: respondido });
  els["#wz-horario-next"].fire("click");
  assert.equal(ctx.advanced, 1);
  assert.equal(ctx.wiz.caso, "Tengo resultados para mostrar · Esta semana");
  assert.equal(ctx.wiz.casoChat, "tok-1");
  assert.equal(respondido.terminado, 1, "the summary is computed once, on leaving the step");
  assert.match(src, /if \(!wiz\.slotDia && wiz\.sinTurno && wiz\.casoChat\) payload\.caso_chat = wiz\.casoChat;/);
});

test("casoChatStart: no AI on the server (or no network) falls back to the plain box", async () => {
  const cStart = src.indexOf("    function casoChatStart(");
  const cEnd = src.indexOf("\n    }\n", cStart) + "\n    }\n".length;
  function box() {
    const clases = new Set();
    return {
      hidden: true, textContent: "",
      classList: { add: (c) => clases.add(c), remove: (c) => clases.delete(c), contains: (c) => clases.has(c) },
      querySelectorAll: () => [],
    };
  }
  for (const respuesta of [
    () => Promise.resolve({ ok: true, json: () => Promise.resolve({ disponible: false }) }),
    () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }),
    () => Promise.reject(new Error("offline")),
  ]) {
    const els = { "#wz-casochat": box(), "#wz-casochat-msgs": box(), "#wz-casochat-form": box() };
    let plain = 0, posted = null;
    const ctx = {
      wiz: {}, T: (k, v, f) => f,
      $: (sel) => els[sel],
      chatUi: () => Promise.resolve(null),
      fetch: (url) => { posted = url; return respuesta(); },
      __host: { contains: () => true },
      __sinChat: () => { plain += 1; },
    };
    vm.createContext(ctx);
    const chat = vm.runInContext(src.slice(cStart, cEnd) + "\ncasoChatStart(__host, { textContent: '' }, __sinChat);", ctx);
    assert.equal(posted, "/api/intake/case/start");
    assert.equal(els["#wz-casochat"].hidden, false, "the chat's place is reserved while the first question loads");
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    await new Promise((ok) => setImmediate(ok));
    assert.equal(plain, 1, "the plain box comes back");
    assert.equal(els["#wz-casochat"].hidden, true);
    assert.equal(chat.activo(), false);
    assert.equal(ctx.wiz.casoChatToken, undefined);
  }
});

test("with the order already uploaded the case is optional", () => {
  const { ctx, els, html } = run(payload, { mode: "orden" });
  assert.match(html, /Contanos tu caso <span class="wz-opt">\(opcional\)<\/span>/);
  els["#wz-horario-next"].fire("click");
  assert.equal(ctx.advanced, 1);
  assert.equal(ctx.wiz.caso, null);
});

test("English reads from the catalog, Spanish stays the fallback", () => {
  const en = JSON.parse(readFileSync(new URL("../locales/en/service.json", import.meta.url), "utf8"));
  const { ctx, html } = run(payload, { catalog: en });
  assert.equal(ctx.sub, "There are no appointments right now.");
  assert.match(html, /Tell us about your case/);
  assert.match(html, /why you cannot wait for the next available appointment/);
  I18n.setCatalog({});
});

test("submit sends the case only for a slotless request, and the desk renders every extra line", () => {
  assert.match(src, /if \(!wiz\.slotDia && wiz\.sinTurno && wiz\.caso\) payload\.caso = wiz\.caso;/);
  const admin = readFileSync(new URL("../static/platform/admin-solicitudes.mjs", import.meta.url), "utf8");
  assert.match(admin, /d\.pide\.slice\(2\)\.map/);
});
