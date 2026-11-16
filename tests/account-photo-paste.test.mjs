// Photo upload robustness on the client: explicit Cmd/Ctrl+V paste on the
// photo areas (never on text inputs), drag & drop, replacement, and the HEIC
// raw-bytes fallback that lets the SERVER decide. All files and images are
// synthetic; nothing touches a network or a real account.
//
// Three layers, real modules throughout (no reimplemented markup):
//   (a) the real account-form.mjs photo helpers, unit-level;
//   (b) the real account.mjs dashboard rail avatar — paste/drop/change
//       through the real wiring, fetch stubbed at the boundary;
//   (c) the real account-form.mjs registration card — a pasted or dropped
//       HEIC gets the HEIC remedy message, not the "couldn't read" failure.
// The paste/drop rules mirror the doctor photo control
// (doctor-workspace.mjs photoFileFromEvent).
import test from "node:test";
import assert from "node:assert/strict";

const AccountForm = await import(new URL("../static/platform/account-form.mjs", import.meta.url).href);
const AccountDash = await import(new URL("../static/platform/account.mjs", import.meta.url).href);

// ---- synthetic clipboard files ---------------------------------------------

const HEIC_BYTES = new Uint8Array([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x68, 0x65, 0x69, 0x63, 1, 2, 3, 4, 5, 6, 7, 8]);
const JPEG_BYTES = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16, 74, 70, 73, 70, 0, 1, 2, 3, 4, 5]);

function fakeFile(bytes, type, name) {
  return {
    name: name || ("synthetic." + (type.split("/")[1] || "bin")),
    type, size: bytes.length, _bytes: bytes,
    slice(a, b) { return fakeFile(bytes.slice(a, b), type, name); },
  };
}

function imageItem(file) {
  return { kind: "file", type: file.type, getAsFile: () => file };
}
function textItem() {
  return { kind: "string", type: "text/plain", getAsFile: () => null };
}
function pasteEvent(items, target) {
  let prevented = false;
  return {
    target: target || { tagName: "DIV" },
    clipboardData: { items },
    get defaultPrevented() { return prevented; },
    preventDefault() { prevented = true; },
  };
}

// ---- tiny DOM seam (same shape as account-native-lifecycle.test.mjs) -------

class Element {
  constructor(documentRef, id = "") {
    this.documentRef = documentRef;
    this.id = id;
    this.attrs = {};
    this.children = [];
    this.className = "";
    this.isConnected = true;
    this.listeners = new Map();
    this.style = {};
    this.textContent = "";
    this._html = "";
    this.classList = {
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  set innerHTML(value) {
    this.documentRef.disconnect(this.children);
    this.children = [];
    this._html = String(value);
    this.documentRef.install(this._html, this);
  }
  get innerHTML() { return this._html; }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) {
    child.isConnected = this.isConnected;
    this.children.push(child);
    if (child.id) this.documentRef.nodes.set(child.id, child);
    return child;
  }
  insertBefore(child) { return this.appendChild(child); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type, listener) { if (this.listeners.get(type) === listener) this.listeners.delete(type); }
  dispatch(type, event) { const fn = this.listeners.get(type); if (fn) fn(event); }
  click() { this.dispatch("click", { preventDefault() {}, target: this }); }
  focus() { this.focused = true; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) { return this.documentRef.query(selector, this); }
  scrollIntoView() {}
}

class FakeDocument {
  constructor() {
    this.nodes = new Map();
    for (const id of ["cuenta-host", "cuenta-lede", "cuenta-title", "cuenta-head"]) {
      this.nodes.set(id, new Element(this, id));
    }
    this.head = { appendChild() {} };
    this.body = { appendChild() {} };
  }
  getElementById(id) { return this.nodes.get(id) || null; }
  createElement() { return new Element(this); }
  createTextNode(text) { const node = new Element(this); node.textContent = String(text); return node; }
  querySelector(selector) { return this.query(selector)[0] || null; }
  disconnect(nodes) {
    for (const node of nodes) {
      node.isConnected = false;
      this.disconnect(node.children);
      if (node.id && this.nodes.get(node.id) === node) this.nodes.delete(node.id);
    }
  }
  install(html, parent) {
    for (const match of html.matchAll(/<([a-z][\w-]*)([^>]*)>/gi)) {
      const node = new Element(this);
      const attrs = match[2] || "";
      const id = /\bid="([^"]+)"/.exec(attrs);
      const classes = /\bclass="([^"]+)"/.exec(attrs);
      node.id = id ? id[1] : "";
      node.className = classes ? classes[1] : "";
      for (const attribute of attrs.matchAll(/\b(data-[\w-]+)="([^"]*)"/g)) node.attrs[attribute[1]] = attribute[2];
      parent.appendChild(node);
    }
  }
  descendants(parent) {
    const found = [];
    const visit = (node) => { for (const child of node.children || []) { found.push(child); visit(child); } };
    visit(parent);
    return found;
  }
  query(selector, parent) {
    const pool = parent ? this.descendants(parent) : [...this.nodes.values()];
    if (selector.startsWith("#")) {
      const id = selector.slice(1).split(/[ .[]/, 1)[0];
      const node = this.nodes.get(id);
      return node && node.isConnected !== false ? [node] : [];
    }
    if (selector.startsWith(".")) {
      const className = selector.slice(1);
      return pool.filter((node) => node.className.split(/\s+/).includes(className));
    }
    const data = /^\[data-([\w-]+)(?:="([^"]*)")?\]$/.exec(selector);
    if (data) return pool.filter((node) => node.attrs["data-" + data[1]] != null && (data[2] == null || node.attrs["data-" + data[1]] === data[2]));
    return [];
  }
}

// FileReader shim for isHeicFile / fileAsDataUrl (canvas stays out: the
// dashboard's fileToJpeg comes from the seam fixture).
class FakeFileReader {
  readAsArrayBuffer(blob) {
    queueMicrotask(() => {
      this.result = blob._bytes.slice().buffer;
      if (this.onload) this.onload();
    });
  }
  readAsDataURL(blob) {
    queueMicrotask(() => {
      this.result = "data:" + (blob.type || "application/octet-stream") + ";base64,"
        + Buffer.from(blob._bytes).toString("base64");
      if (this.onload) this.onload();
    });
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle(turns = 24) { for (let i = 0; i < turns; i += 1) await tick(); }

function dataModule(source) { return "data:text/javascript," + encodeURIComponent(source); }
const i18n = dataModule('export function t(key, vars, fallback) { return fallback || key; }');
const dates = dataModule('export function amigable(day, hour) { return day + (hour ? " " + hour : ""); }');
const prep = dataModule('export function ayuno() { return { hay: false }; } export function antes() { return []; }');
const dependents = dataModule('export function renderCuenta() {}');
const barcode = dataModule('export function readDni() { return null; }');
const FORM_SEAM = new URL("fixtures/photo-paste-form-seam.mjs", import.meta.url).href;

function baseMap() {
  return {
    "platform/account-form.mjs": FORM_SEAM,
    "platform/i18n.mjs": i18n,
    "platform/dates.mjs": dates,
    "platform/prep.mjs": prep,
    "platform/dependents.mjs": dependents,
    "platform/dni-barcode.mjs": barcode,
  };
}

function installEnvironment({ bootstrap = null, map = {}, fetchImpl } = {}) {
  const previous = {
    document: globalThis.document, window: globalThis.window, location: globalThis.location,
    history: globalThis.history, localStorage: globalThis.localStorage, fetch: globalThis.fetch,
    FileReader: globalThis.FileReader,
  };
  const document = new FakeDocument();
  const location = { hash: "#datos", search: "", href: "UNCHANGED" };
  globalThis.document = document;
  globalThis.window = {
    SANA_STATIC: map,
    __ACCOUNT_BOOTSTRAP__: bootstrap,
    addEventListener() {}, removeEventListener() {}, scrollTo() {},
  };
  globalThis.location = location;
  globalThis.history = {
    replaceState(_s, _t, next) { location.hash = next; },
    pushState(_s, _t, next) { location.hash = next; },
  };
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.FileReader = FakeFileReader;
  globalThis.fetch = fetchImpl || (() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }));
  return {
    document, location,
    restore() {
      Object.assign(globalThis, previous);
      delete globalThis.__photoPasteFileToJpeg;
    },
  };
}

function okJson(body) { return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }); }

// ---- (a) the real helpers, unit level --------------------------------------

function dropEvent(files, extra = {}) {
  let prevented = false;
  return {
    target: { tagName: "DIV" },
    dataTransfer: { files, types: files.length ? ["Files"] : [] },
    get defaultPrevented() { return prevented; },
    preventDefault() { prevented = true; },
    ...extra,
  };
}

test("photoFileFromEvent takes the first image file and nothing else", () => {
  const png = fakeFile(new Uint8Array([1, 2, 3]), "image/png");
  assert.equal(AccountForm.photoFileFromEvent(pasteEvent([textItem(), imageItem(png)])), png);
  assert.equal(AccountForm.photoFileFromEvent(pasteEvent([textItem()])), null);
  // a clipboard pdf is not a photo
  const pdf = fakeFile(new Uint8Array([1]), "application/pdf");
  assert.equal(AccountForm.photoFileFromEvent(pasteEvent([{ kind: "file", type: "application/pdf", getAsFile: () => pdf }])), null);
  // a HEIC with an empty mime (Windows, some Androids) still counts by name
  const bare = fakeFile(HEIC_BYTES, "", "IMG_0001.HEIC");
  assert.equal(AccountForm.photoFileFromEvent(dropEvent([bare])), bare);
  assert.equal(AccountForm.photoFileFromEvent(null), null);
});

test("photoFileFromEvent never takes a text field's paste or a consumed event", () => {
  const png = fakeFile(new Uint8Array([1, 2, 3]), "image/png");
  for (const tagName of ["INPUT", "textarea", "SELECT"]) {
    assert.equal(AccountForm.photoFileFromEvent(pasteEvent([imageItem(png)], { tagName })), null, tagName);
  }
  assert.equal(AccountForm.photoFileFromEvent(pasteEvent([imageItem(png)], { tagName: "DIV", isContentEditable: true })), null);
  // a real browser target answers through closest(), like the doctor control
  const inField = { tagName: "SPAN", closest: (sel) => (sel.includes("input") ? {} : null) };
  assert.equal(AccountForm.photoFileFromEvent(pasteEvent([imageItem(png)], inField)), null);
  const onPhoto = { tagName: "SPAN", closest: () => null };
  assert.equal(AccountForm.photoFileFromEvent(pasteEvent([imageItem(png)], onPhoto)), png);
  const consumed = pasteEvent([imageItem(png)]);
  consumed.preventDefault();
  assert.equal(AccountForm.photoFileFromEvent(consumed), null);
});

test("eventCarriesFiles tells a file drop from a text drag", () => {
  assert.equal(AccountForm.eventCarriesFiles(dropEvent([fakeFile(JPEG_BYTES, "image/jpeg")])), true);
  assert.equal(AccountForm.eventCarriesFiles({ dataTransfer: { types: ["Files"] } }), true);
  assert.equal(AccountForm.eventCarriesFiles({ dataTransfer: { types: ["text/plain"] } }), false);
  assert.equal(AccountForm.eventCarriesFiles({}), false);
});

test("isHeicFile sniffs bytes, not file names (real FileReader path)", async () => {
  const env = installEnvironment();
  try {
    const heic = fakeFile(HEIC_BYTES, "image/heic", "IMG_0001.heic");
    const lyingJpg = fakeFile(HEIC_BYTES, "image/jpeg", "foto.jpg");
    const realJpg = fakeFile(JPEG_BYTES, "image/jpeg", "foto.jpg");
    assert.equal(await AccountForm.isHeicFile(heic), true);
    assert.equal(await AccountForm.isHeicFile(lyingJpg), true, "the bytes decide, not the extension");
    assert.equal(await AccountForm.isHeicFile(realJpg), false);
  } finally { env.restore(); }
});

test("fileAsDataUrl passes the raw bytes through (no canvas)", async () => {
  const env = installEnvironment();
  try {
    const du = await AccountForm.fileAsDataUrl(fakeFile(HEIC_BYTES, "image/heic", "IMG.heic"));
    assert.match(du, /^data:image\/heic;base64,/);
    assert.equal(Buffer.from(du.split(",")[1], "base64").compare(HEIC_BYTES), 0);
  } finally { env.restore(); }
});

// ---- (b) the rail avatar in the real dashboard ------------------------------

function photoPosts(fetches) {
  return fetches.filter(([url, init]) => url === "/api/account/photo" && init && init.method === "POST");
}

async function mountDashboard(fetchLog, fetchImpl) {
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: "Syn", apellido: "Thetic" }, solicitudes: [] },
    map: baseMap(),
    fetchImpl: fetchImpl || ((url, init) => {
      fetchLog.push([url, init]);
      if (url === "/api/account/photo?probe=1") return okJson({ ok: true, source: "none" });
      return okJson({ ok: true });
    }),
  });
  const dispose = AccountDash.mount(env.document.getElementById("cuenta-host"));
  await settle();
  return { env, dispose };
}

test("the avatar is focusable so a Cmd/Ctrl+V can land on it", async () => {
  const { env, dispose } = await mountDashboard([]);
  try {
    assert.match(env.document.getElementById("cuenta-host").innerHTML, /id="rail-av" tabindex="0"/);
  } finally { dispose(); env.restore(); }
});

test("paste an image on the avatar uploads it (file-picker-free path)", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    assert.ok(av, "rail avatar element rendered");
    assert.ok(av.listeners.has("paste"), "the paste handler is on the photo area");
    const ev = pasteEvent([imageItem(fakeFile(JPEG_BYTES, "image/jpeg"))]);
    av.dispatch("paste", ev);
    await settle();
    assert.ok(ev.defaultPrevented, "the image paste is consumed by the photo area");
    const posts = photoPosts(fetchLog);
    assert.equal(posts.length, 1);
    const body = JSON.parse(posts[0][1].body);
    assert.match(body.data_b64, /^data:image\/jpeg;base64,/);
  } finally { dispose(); env.restore(); }
});

test("pasting text on the avatar does nothing (no hijack, no upload)", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    const ev = pasteEvent([textItem()]);
    av.dispatch("paste", ev);
    await settle();
    assert.equal(ev.defaultPrevented, false, "a text paste keeps its default behavior");
    assert.equal(photoPosts(fetchLog).length, 0);
  } finally { dispose(); env.restore(); }
});

test("a paste aimed at a text input never reaches the photo upload", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    av.dispatch("paste", pasteEvent([imageItem(fakeFile(JPEG_BYTES, "image/jpeg"))], { tagName: "INPUT" }));
    await settle();
    assert.equal(photoPosts(fetchLog).length, 0, "typing fields keep their paste");
  } finally { dispose(); env.restore(); }
});

test("drag & drop a photo on the avatar uploads it", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    let overPrevented = false;
    av.dispatch("dragover", { dataTransfer: { types: ["Files"] }, preventDefault() { overPrevented = true; } });
    assert.ok(overPrevented, "a file dragover is cancelled so the drop can land");
    let textOver = false;
    av.dispatch("dragover", { dataTransfer: { types: ["text/plain"] }, preventDefault() { textOver = true; } });
    assert.equal(textOver, false, "dragging text over the avatar keeps its default");
    const drop = dropEvent([fakeFile(JPEG_BYTES, "image/jpeg")]);
    av.dispatch("drop", drop);
    await settle();
    assert.ok(drop.defaultPrevented, "the browser never opens the dropped file");
    assert.equal(photoPosts(fetchLog).length, 1);
  } finally { dispose(); env.restore(); }
});

test("dropping a non-photo on the avatar is cancelled and says why", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    const drop = dropEvent([fakeFile(new Uint8Array([1]), "application/pdf", "orden.pdf")]);
    av.dispatch("drop", drop);
    await settle();
    assert.ok(drop.defaultPrevented, "the pdf never replaces the account page");
    assert.equal(photoPosts(fetchLog).length, 0);
    assert.equal(env.document.getElementById("rail-av-cambiar").textContent, "Formato no soportado");
  } finally { dispose(); env.restore(); }
});

test("the file input keeps working and replaces (second upload posts again)", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const file = env.document.getElementById("rail-av-file");
    file.files = [fakeFile(JPEG_BYTES, "image/jpeg")];
    file.dispatch("change", {});
    await settle();
    file.files = [fakeFile(new Uint8Array([9, 9, 9]), "image/png")];
    file.dispatch("change", {});
    await settle();
    const posts = photoPosts(fetchLog);
    assert.equal(posts.length, 2, "choosing again replaces the photo, same single endpoint");
  } finally { dispose(); env.restore(); }
});

test("HEIC the browser can't decode goes up raw for the server to judge", async () => {
  const fetchLog = [];
  globalThis.__photoPasteFileToJpeg = () => Promise.reject(new Error("no canvas decode"));
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    av.dispatch("paste", pasteEvent([imageItem(fakeFile(HEIC_BYTES, "image/heic"))]));
    await settle();
    const posts = photoPosts(fetchLog);
    assert.equal(posts.length, 1, "a HEIC paste still uploads, as raw bytes");
    const body = JSON.parse(posts[0][1].body);
    assert.match(body.data_b64, /^data:image\/heic;base64,/);
  } finally { dispose(); env.restore(); }
});

test("a non-HEIC the browser can't decode never uploads and says so", async () => {
  const fetchLog = [];
  globalThis.__photoPasteFileToJpeg = () => Promise.reject(new Error("no canvas decode"));
  const { env, dispose } = await mountDashboard(fetchLog);
  try {
    const av = env.document.getElementById("rail-av");
    av.dispatch("paste", pasteEvent([imageItem(fakeFile(JPEG_BYTES, "image/jpeg"))]));
    await settle();
    assert.equal(photoPosts(fetchLog).length, 0);
    assert.equal(env.document.getElementById("rail-av-cambiar").textContent, "Formato no soportado");
  } finally { dispose(); env.restore(); }
});

test("the server's remedy message reaches the label (HEIC refusal included)", async () => {
  const fetchLog = [];
  const { env, dispose } = await mountDashboard(fetchLog, (url, init) => {
    fetchLog.push([url, init]);
    if (url === "/api/account/photo?probe=1") return okJson({ ok: true, source: "none" });
    return Promise.resolve({
      ok: false, status: 400,
      json: () => Promise.resolve({ error: "La foto está en HEIC (formato de iPhone) y este servidor todavía no lo lee. Guardala como JPG o PNG y volvé a subirla." }),
    });
  });
  try {
    const av = env.document.getElementById("rail-av");
    av.dispatch("paste", pasteEvent([imageItem(fakeFile(JPEG_BYTES, "image/jpeg"))]));
    await settle();
    assert.match(env.document.getElementById("rail-av-cambiar").textContent, /HEIC/);
  } finally { dispose(); env.restore(); }
});

// ---- (c) the DNI card in the real registration form --------------------------

// renderAuth guards every element lookup; only the scan card's elements
// exist, so wireScan runs against exactly the real selectors it uses.
function scanHost() {
  const selectors = [".sc-scan", "#sc-scan-status", "#sc-scan-f", "#sc-scan-f-file", "#sc-scan-d", "#sc-scan-d-file"];
  const made = new Map();
  for (const selector of selectors) {
    const node = new Element({ install() {}, disconnect() {}, query: () => [] }, selector.slice(1));
    node.tagName = "DIV";
    made.set(selector, node);
  }
  return {
    _innerHTML: "",
    get innerHTML() { return this._innerHTML; },
    set innerHTML(v) { this._innerHTML = v; },
    querySelector(selector) { return made.get(selector) || null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    els: made,
  };
}

async function withScanForm(run) {
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  const previousFileReader = globalThis.FileReader;
  globalThis.window = { SANA_STATIC: { "platform/dni-barcode.mjs": barcode } };
  globalThis.document = {
    getElementById: () => null,
    createElement: () => ({}),
    head: { appendChild() {} },
    documentElement: { lang: "es" },
  };
  globalThis.FileReader = FakeFileReader;
  globalThis.fetch = () => okJson({});
  try {
    const host = scanHost();
    await AccountForm.renderAuth(host, {});
    await settle(4);
    return await run(host);
  } finally {
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
    globalThis.fetch = previousFetch;
    globalThis.FileReader = previousFileReader;
  }
}

// node has no Image/canvas: the OCR re-encode rejects and lands in the exact
// catch a HEIC-unfriendly browser (Chrome, Firefox) takes.
test("a pasted HEIC on the DNI card gets the HEIC remedy, not a generic failure", async () => {
  await withScanForm(async (host) => {
    const card = host.els.get(".sc-scan");
    assert.ok(card.listeners.has("paste"), "the DNI card listens for paste");
    assert.equal(card.attrs.tabindex, "0", "the card is focusable so the paste can land");
    const ev = pasteEvent([imageItem(fakeFile(HEIC_BYTES, "image/heic"))]);
    card.dispatch("paste", ev);
    await settle(8);
    assert.ok(ev.defaultPrevented);
    const status = host.els.get("#sc-scan-status");
    assert.match(status.textContent, /HEIC/);
    assert.match(status.textContent, /guardala como JPG/i);
  });
});

test("a pasted non-HEIC that fails to decode keeps the front's own remedy", async () => {
  await withScanForm(async (host) => {
    host.els.get(".sc-scan").dispatch("paste", pasteEvent([imageItem(fakeFile(JPEG_BYTES, "image/jpeg"))]));
    await settle(8);
    assert.match(host.els.get("#sc-scan-status").innerHTML, /No pude leer el frente/);
  });
});

test("a paste aimed at a form input never triggers the DNI scan", async () => {
  await withScanForm(async (host) => {
    host.els.get(".sc-scan").dispatch("paste", pasteEvent([imageItem(fakeFile(HEIC_BYTES, "image/heic"))], { tagName: "INPUT" }));
    await settle(8);
    assert.equal(host.els.get("#sc-scan-status").textContent, "", "the scan never ran");
  });
});

test("a photo dropped on the DNI card is read as the front", async () => {
  await withScanForm(async (host) => {
    const card = host.els.get(".sc-scan");
    let overPrevented = false;
    card.dispatch("dragover", { dataTransfer: { types: ["Files"] }, preventDefault() { overPrevented = true; } });
    assert.ok(overPrevented, "a file dragover is cancelled so the drop can land");
    const drop = dropEvent([fakeFile(HEIC_BYTES, "image/heic", "IMG_0002.heic")]);
    card.dispatch("drop", drop);
    await settle(8);
    assert.ok(drop.defaultPrevented, "the browser never opens the dropped file");
    assert.match(host.els.get("#sc-scan-status").textContent, /HEIC/);
  });
});
