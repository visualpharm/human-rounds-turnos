import test from "node:test";
import assert from "node:assert/strict";
import { mountMyAppointments } from "../static/platform/admin-my-appointments.mjs";

// Execute the complete shipped native controller, doubling only external
// boundaries (deps, fetch, DOM). The module is imported whole and unchanged;
// every mount is disposed and every global restored before the section is
// read.
async function runRoute(response, confirmar) {
  const section = {
    innerHTML: "<p>existing appointments</p>", style: {},
    querySelector() { return null; },
  };
  const document = {
    readyState: "loading",
    addEventListener() {},
    removeEventListener() {},
    querySelector(s) { return s === "#sec-mis-turnos" ? section : null; },
    querySelectorAll() { return []; },
    getElementById(id) {
      if (id === "sec-mis-turnos") return section;
      return { style: {}, textContent: "" };
    },
    createElement() { return { set innerHTML(value) { this.firstChild = { html: value }; } }; },
  };
  const window = {
    SANA_STATIC: {}, document,
    sanaImportStatic(name) {
      if (name === "platform/format.mjs") return Promise.resolve({ nombre: v => v, dni: v => v, edadTexto: () => "", contactoHtml: () => "" });
      if (name === "platform/i18n.mjs") return Promise.resolve({ t: (key, vars, fallback) => fallback });
      if (name === "platform/admin-session.mjs") return Promise.resolve({ confirmar });
      if (name === "platform/admin-table.mjs") return Promise.resolve({ crear: () => ({ programarFit() {}, dispose() {} }), columnas: () => ({ activas: () => [], gearHtml: () => "", dispose() {} }) });
      return Promise.reject(new Error("unexpected module " + name));
    },
  };
  const saved = Object.fromEntries(["window", "document", "fetch"]
    .map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  globalThis.window = window;
  globalThis.document = document;
  globalThis.fetch = () => Promise.resolve(response);
  let dispose = null;
  try {
    dispose = mountMyAppointments({ root: document });
    await dispose.ready;
  } finally {
    if (dispose) dispose();
    for (const [k, v] of Object.entries(saved)) v ? Object.defineProperty(globalThis, k, v) : delete globalThis[k];
  }
  return { section };
}

test("confirmed session expiry does not let the actual route catch overwrite the redirecting page", async () => {
  const response = new Response(JSON.stringify({ error: "no_autenticado" }), {
    status: 401, headers: { "Content-Type": "application/json" },
  });
  const { section } = await runRoute(response, () => Promise.resolve(true));
  assert.equal(section.innerHTML, "<p>existing appointments</p>");
});

test("ordinary 403 still reaches the actual route catch and shows its local permission message", async () => {
  const response = new Response(JSON.stringify({ error: "rol_no_autorizado" }), {
    status: 403, headers: { "Content-Type": "application/json" },
  });
  const { section } = await runRoute(response, () => Promise.resolve(false));
  assert.match(section.innerHTML, /No tenés permiso para ver esta sección/);
});
