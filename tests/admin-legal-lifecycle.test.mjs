// Runtime test for «Términos y privacidad» (static/platform/admin-legal.mjs):
// a 401 that the session does NOT confirm (the site's Basic Auth password, a
// proxy) must say so on screen — never leave «Cargando…» forever or swallow
// the Guardar / Restaurar click. A CONFIRMED expiry stays silent: the shared
// session module is already moving the page. The real module is imported; only
// the DOM, fetch and the session dependency are faked.
import assert from "node:assert/strict";
import test from "node:test";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function drain(turns = 24) { for (let i = 0; i < turns; i += 1) await tick(); }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }

class Element {
  constructor(id = "") {
    this.id = id; this._html = ""; this.listeners = new Map(); this.children = new Map();
    this.value = ""; this.disabled = false; this.hidden = false; this.className = ""; this.textContent = ""; this.style = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = String(html);
    this.children = new Map();
    const tagRe = /<([a-z0-9]+)\b([^>]*)>/g;
    let m;
    while ((m = tagRe.exec(this._html))) {
      const idMatch = /\bid="([^"]+)"/.exec(m[2]);
      if (idMatch) this.children.set(idMatch[1], new Element(idMatch[1]));
    }
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  contains() { return true; }
  appendChild() {}
}

const SESSION_SRC = "export const confirmar=(response,parsedBody,isCurrent)=>Promise.resolve(globalThis.__legalConfirm(response))";
const I18N_SRC = "export const t=(key,vars,fallback)=>String(fallback)";
// format.mjs refuses to load without the data pack's phone table; legal only needs the date.
const FORMAT_SRC = "export const fechaCorta=iso=>String(iso)";

let probe = 0;
async function fixture(t, { search = "", routes, confirmed = false }) {
  const sec = new Element("sec-legal");
  const status = new Element("legal-status");
  const h1 = new Element("adm-h1");
  const requests = [];
  const keys = ["window", "document", "fetch", "location", "history", "__legalConfirm"];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__legalConfirm = () => confirmed;
  globalThis.document = {
    head: new Element("head"),
    getElementById(id) { return { "sec-legal": sec, "legal-status": status, "adm-h1": h1 }[id] || sec.children.get(id) || null; },
    createElement(tag) { return new Element(tag); },
    createTextNode() { return {}; },
    querySelectorAll() { return []; },
  };
  globalThis.location = { pathname: "/admin/legal", search };
  globalThis.history = { replaceState() {}, pushState() {} };
  globalThis.window = {
    SANA_STATIC: {
      "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent(SESSION_SRC),
      "platform/i18n.mjs": "data:text/javascript," + encodeURIComponent(I18N_SRC),
      "platform/format.mjs": "data:text/javascript," + encodeURIComponent(FORMAT_SRC),
    },
    addEventListener() {}, removeEventListener() {}, confirm: () => true,
  };
  globalThis.fetch = (url, options = {}) => {
    const method = options.method || "GET";
    requests.push({ url, method });
    return Promise.resolve(routes(url, method));
  };
  const mod = await import("../static/platform/admin-legal.mjs?probe=" + (++probe));
  t.after(() => {
    mod.destroy();
    for (const key of keys) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]); else delete globalThis[key];
    }
  });
  await mod.mount();
  await drain();
  return { sec, status, requests, child: id => sec.children.get(id) };
}

const DOC = { id: "terminos", titulo: "Términos", pagina: "/terminos", cuerpo: "texto", editado: true };

test("an unconfirmed 401 on the list says it could not load instead of staying on «Cargando…»", async t => {
  const f = await fixture(t, { routes: () => response(401, { error: "no_autenticado" }) });
  assert.match(f.sec.innerHTML, /No se pudieron cargar los documentos/);
  assert.doesNotMatch(f.sec.innerHTML, /Cargando/);
});

test("a confirmed 401 on the list leaves the screen alone (the session module is leaving the page)", async t => {
  const f = await fixture(t, { confirmed: true, routes: () => response(401, { error: "no_autenticado" }) });
  assert.doesNotMatch(f.sec.innerHTML, /No se pudieron cargar/);
});

test("an unconfirmed 401 on a document says it could not load", async t => {
  const f = await fixture(t, { search: "?doc=terminos", routes: () => response(401, { error: "no_autenticado" }) });
  assert.match(f.sec.innerHTML, /No se pudo cargar/);
  assert.doesNotMatch(f.sec.innerHTML, /Cargando/);
});

for (const [name, method, button] of [["Guardar", "PUT", "lg-guardar"], ["Restaurar el original", "DELETE", "lg-restaurar"]]) {
  test(`an unconfirmed 401 on ${name} shows the failure beside the button`, async t => {
    const f = await fixture(t, {
      search: "?doc=terminos",
      routes: (url, m) => (m === "GET" ? response(200, { items: [DOC] }) : response(401, { error: "no_autenticado" })),
    });
    const click = f.child(button).listeners.get("click");
    assert.ok(click, button + " is wired");
    click({ preventDefault() {} });
    await drain();
    assert.ok(f.requests.some(r => r.method === method), method + " was sent");
    const msg = f.child("lg-msg");
    assert.match(msg.textContent, /No se pudo completar la acción/);
    assert.match(msg.className, /lg-msg--error/);
  });
}
