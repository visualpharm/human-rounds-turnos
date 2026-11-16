import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import { installDom, seed } from "./fixtures/entrevistas-dom.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PHONE = JSON.parse(readFileSync(join(ROOT, "packs", "ar", "country.json"), "utf8")).phone;
const MODULE = join(ROOT, "app", "modules", "intake", "admin-entrevistas.mjs");
const TEMPLATE = readFileSync(join(ROOT, "app", "modules", "intake", "admin_entrevistas.html"), "utf8");
const STORY = readFileSync(join(ROOT, "stories", "Interviews.stories.js"), "utf8");
const MODULE_SRC = readFileSync(MODULE, "utf8");

function staticMap() {
  return {
    "platform/format.mjs": pathToFileURL(join(ROOT, "static/platform/format.mjs")).href,
    "platform/admin-session.mjs": pathToFileURL(join(ROOT, "static/platform/admin-session.mjs")).href,
    "platform/i18n.mjs": pathToFileURL(join(ROOT, "static/platform/i18n.mjs")).href,
    "platform/admin-filter-controls.mjs": pathToFileURL(join(ROOT, "static/platform/admin-filter-controls.mjs")).href,
  };
}

async function loadModule() {
  globalThis.window = globalThis.window || globalThis;
  globalThis.SANA_COUNTRY_PHONE = PHONE;
  globalThis.window.SANA_STATIC = staticMap();
  globalThis.window.sanaImportStatic = (name) => import(globalThis.window.SANA_STATIC[name]);
  return import(`${pathToFileURL(MODULE).href}?entrevistas-test=${Date.now()}-${Math.random()}`);
}

test("the classic controller is physically retired", () => {
  assert.equal(existsSync(join(ROOT, "app", "modules", "intake", "admin-entrevistas.js")), false);
  assert.equal(existsSync(MODULE), true);
  assert.doesNotMatch(MODULE_SRC, /window\.SanaEntrevistas|window\.sanaRouteEntrevistas|module\.exports/);
  assert.match(MODULE_SRC, /export function preview/);
  assert.match(MODULE_SRC, /export function boot/);
});

test("the template imports the canonical module with an explicit readiness boundary", () => {
  assert.match(TEMPLATE, /<section id="sec-entrevistas" aria-labelledby="adm-h1"/);
  assert.match(TEMPLATE, /module_static\('modules\/intake\/admin-entrevistas\.mjs'\)/);
  assert.match(TEMPLATE, /import\(url\)\.then\(function \(m\) \{ return m\.boot\(\); \}\)\.catch\(showLoadError\)/);
  assert.doesNotMatch(TEMPLATE, /admin-entrevistas\.js|SanaEntrevistas|sanaRouteEntrevistas|<script src=/);
  assert.doesNotMatch(TEMPLATE, /<hr\b|border-top|border-bottom/);
});

test("the actual story imports preview and returns a Node synchronously", () => {
  execFileSync(process.execPath, [
    "--no-warnings",
    "--experimental-loader", new URL("./fixtures/entrevistas-story-loader.mjs", import.meta.url).pathname,
    new URL("./fixtures/entrevistas-story-boundary-runner.mjs", import.meta.url).pathname,
  ], { stdio: "pipe" });
  assert.match(STORY, /await import\(["']\.\.\/app\/modules\/intake\/admin-entrevistas\.mjs["']\)/);
  assert.match(STORY, /preview\(/);
  assert.doesNotMatch(STORY, /SanaEntrevistas|sanaRouteEntrevistas|admin-entrevistas\.js|async function|async \(\)/);
});

test("preview paints effective checkboxes from the real module and never POSTs", async () => {
  const world = installDom();
  try {
    const api = await loadModule();
    assert.equal(typeof globalThis.SanaEntrevistas, "undefined");
    assert.equal(typeof globalThis.sanaRouteEntrevistas, "undefined");
    await api.preview(world.host, seed());
    assert.match(world.host.innerHTML, /Ana Alvarez/);
    assert.match(world.host.innerHTML, /data-effective-medico="doctor-0"/);
    assert.match(world.host.innerHTML, /id="ent-all"/);
    assert.match(world.host.innerHTML, /format=pdf/);
    assert.doesNotMatch(world.host.innerHTML, /ent-sistema|regenerar|Nuevo código QR/);
    const posts = world.fetches.filter((item) => (item.opts.method || "GET") === "POST");
    assert.equal(posts.length, 0);
    assert.equal(typeof globalThis.window.SanaEntrevistas, "undefined");
  } finally {
    world.restore();
  }
});

test("boot fetches the direct-route settings and preserves query/history state", async () => {
  const data = seed();
  const world = installDom({
    pathname: "/admin/entrevistas",
    search: "?keep=1",
    fetchImpl: async (url, opts = {}) => {
      world.fetches.push({ url, opts });
      assert.equal(url, "/api/admin/intake/settings");
      assert.equal((opts.method || "GET").toUpperCase(), "GET");
      return { ok: true, status: 200, json: async () => data };
    },
  });
  try {
    const before = { path: world.location.pathname, search: world.location.search, hash: world.location.hash };
    const api = await loadModule();
    await api.boot();
    assert.match(world.host.innerHTML, /Beatriz Benitez/);
    assert.equal(world.location.pathname, before.path);
    assert.equal(world.location.search, before.search);
    assert.equal(world.location.hash, "");
    assert.equal(typeof globalThis.SanaEntrevistas, "undefined");
    assert.equal(typeof globalThis.sanaRouteEntrevistas, "undefined");
  } finally {
    world.restore();
  }
});

test("a pending direct-screen load cannot paint a later preview host", async () => {
  let resolveSettings;
  const settings = new Promise((resolve) => { resolveSettings = resolve; });
  const world = installDom({ fetchImpl: () => settings });
  try {
    const api = await loadModule();
    const boot = api.boot();
    await Promise.resolve();
    const previewHost = document.createElement("section");
    document.body.appendChild(previewHost);
    await api.preview(previewHost, seed(["Preview only"]));
    resolveSettings({
      ok: true,
      status: 200,
      json: async () => seed(["Stale network result"]),
    });
    await boot;
    assert.match(previewHost.innerHTML, /Preview only/);
    assert.doesNotMatch(previewHost.innerHTML, /Stale network result/);
  } finally {
    world.restore();
  }
});

test("an empty roster exposes the existing doctor-creation action", async () => {
  const world = installDom();
  try {
    const api = await loadModule();
    await api.preview(world.host, { medicos: [] });
    assert.match(world.host.innerHTML, /data-admin-create="true"/);
    assert.match(world.host.innerHTML, /data-href="\/admin\/doctors\?item=new"/);
    assert.doesNotMatch(world.host.innerHTML, /data-ent-clear/);
  } finally {
    world.restore();
  }
});

// Progressive disclosure (the product owner, 2026-10-26: «if there's no content, why to
// show all the ui?»): an empty roster shows one Spanish empty state, never the
// counter, search, state filter, select-all or table header.
test("an empty roster shows only the empty state, in Spanish", async () => {
  const world = installDom();
  try {
    const api = await loadModule();
    await api.preview(world.host, { medicos: [] });
    const html = world.host.innerHTML;
    assert.match(html, /class="adm-empty-state"/);
    assert.match(html, /Todavía no hay médicos/);
    assert.match(html, /Nuevo médico/);
    assert.doesNotMatch(html, /New doctor|Clear filters/);
    assert.doesNotMatch(html, /id="ent-count"|id="ent-search"|id="ent-filter"|id="ent-all"|<thead/);
    assert.equal(world.host.getAttribute("data-disclosure"), "empty");
  } finally {
    world.restore();
  }
});

test("a search that hides every doctor keeps the search and offers to clear it", async () => {
  const world = installDom();
  try {
    const api = await loadModule();
    await api.preview(world.host, seed());
    world.host.dispatchEvent({ type: "input", target: { id: "ent-search", value: "zzzz" } });
    const html = world.host.querySelector("#ent-list").innerHTML;
    assert.match(html, /data-empty-state="no-match"/);
    assert.match(html, /data-clear-filters="" data-ent-clear=""/);
    assert.match(html, /Limpiar filtros/);
    assert.doesNotMatch(html, /<thead|id="ent-all"/);
    assert.ok(world.host.querySelector("#ent-search"), "the search stays to be cleared");
    assert.equal(world.host.getAttribute("data-disclosure"), "no-match");
    assert.equal(world.host.querySelector(".ent-mobile-all").hidden, true);
  } finally {
    world.restore();
  }
});

// A roster worth filtering (Filters.FILTER_MIN_ROWS = 10 doctors).
const TWELVE = Array.from({ length: 12 }, (_, i) => "Doctor " + String.fromCharCode(65 + i));

test("the Activa/Inactiva filter appears only when the roster has both", async () => {
  const world = installDom();
  try {
    const api = await loadModule();
    const mixed = seed(TWELVE);
    await api.preview(world.host, mixed);
    assert.match(world.host.innerHTML, /id="ent-filter"/);
    const allOff = seed(TWELVE);
    allOff.medicos.forEach((d) => { d.efectivo = false; d.override = false; d.pdf_url = null; });
    await api.preview(world.host, allOff);
    assert.doesNotMatch(world.host.innerHTML, /id="ent-filter"/);
    assert.match(world.host.innerHTML, /id="ent-search"/);
    assert.equal(world.host.getAttribute("data-disclosure"), "data");
  } finally {
    world.restore();
  }
});

// Product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not draw filters
// at all»; 15.07, 28.07: the default option is the field name, never «Todos».
test("THRESHOLD: under ten doctors there is no state filter even with both states; its label is the field name", async () => {
  const world = installDom();
  try {
    const api = await loadModule();
    await api.preview(world.host, seed());
    assert.doesNotMatch(world.host.innerHTML, /id="ent-filter"/, "three doctors: nothing to filter");
    assert.match(world.host.innerHTML, /id="ent-search"/, "the search is not a filter");
    await api.preview(world.host, seed(TWELVE));
    const first = world.host.innerHTML.match(/id="ent-filter"[^>]*><option value="all"[^>]*>([^<]*)</);
    assert.ok(first, "twelve doctors with both states: the filter");
    assert.equal(first[1], "Estado");
  } finally {
    world.restore();
  }
});
