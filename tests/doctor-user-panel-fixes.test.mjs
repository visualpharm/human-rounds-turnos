// Médico = usuario — panel bugs seen on prod (2026-10-19).
//   1. The doctor's «Usuario» tab hid the ACCOUNT's buttons (reset, 2FA,
//      Desactivar, Usuario y contraseña) and left the doctor form's own
//      Guardar/Deshabilitar row visible: wireMedicoTabs picked the first
//      .cnt-actions in the editor, which is the account block's.
//   4. «Nuevo médico» painted its account outcome ~1,370px down, under the
//      Horarios tab, where nobody was looking.
//   5. After «Invitar médico» the Centro column read the raw slug: the save
//      drops every catalog cache and the list refresh never reloaded centers.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static/platform/admin-content.mjs"), "utf8");

function fnSource(name) {
  const start = JS.indexOf("  function " + name + "(");
  assert.ok(start >= 0, name + " exists");
  const end = JS.indexOf("\n  }\n", start);
  return JS.slice(start, end + 4);
}

// A hand-rolled DOM (this repo has no jsdom): compound simple selectors
// (.class, #id, [attr], [attr="v"]) and a leading ":scope > ".
function el(tag, { cls = "", attrs = {}, children = [] } = {}) {
  const node = { tag, classes: new Set(cls.split(" ").filter(Boolean)), attrs: { ...attrs }, style: {}, children: [], handlers: {} };
  node.classList = {
    contains: (c) => node.classes.has(c),
    toggle: (c, on) => { if (on) node.classes.add(c); else node.classes.delete(c); },
  };
  node.getAttribute = (k) => (k in node.attrs ? node.attrs[k] : null);
  node.setAttribute = (k, v) => { node.attrs[k] = String(v); };
  node.querySelectorAll = (sel) => queryAll(node, sel);
  node.querySelector = (sel) => queryAll(node, sel)[0] || null;
  children.forEach((child) => node.children.push(child));
  return node;
}
function matches(node, sel) {
  const parts = sel.match(/[.#][\w-]+|\[[^\]]+\]/g) || [];
  return parts.join("") === sel && parts.every((p) => {
    if (p[0] === ".") return node.classes.has(p.slice(1));
    if (p[0] === "#") return node.attrs.id === p.slice(1);
    const m = p.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    return m && m[1] in node.attrs && (m[2] === undefined || node.attrs[m[1]] === m[2]);
  });
}
function queryAll(root, sel) {
  if (sel.startsWith(":scope > ")) return root.children.filter((c) => matches(c, sel.slice(9)));
  const out = [];
  (function walk(node) { node.children.forEach((c) => { if (matches(c, sel)) out.push(c); walk(c); }); })(root);
  return out;
}

function editorFixture() {
  const tab = (name, on) => el("button", { cls: "cnt-tab" + (on ? " on" : ""), attrs: { "data-tab": name } });
  const panel = (name, children = []) => el("div", { cls: "cnt-tabpanel", attrs: { "data-tabpanel": name }, children });
  // Same nesting as renderForm + admin-users.renderDoctorUser: the account
  // block lives inside the Usuario panel, BEFORE the editor's own row.
  const accountRow = el("div", { cls: "cnt-actions doctor-user-actions", children: [
    el("button", { attrs: { "data-doctor-user-action": "reset-password" } }),
    el("button", { attrs: { "data-doctor-user-action": "disable" } }),
  ] });
  const editorRow = el("div", { cls: "cnt-actions", children: [el("button", { attrs: { type: "submit" } })] });
  // The AI translation caption right above «Guardar» (renderForm, 2026-11-08 B5).
  const notice = el("p", { cls: "adm-ai-notice cnt-ai-notice" });
  const sec = el("section", { children: [
    el("form", { attrs: { id: "cnt-form" }, children: [
      el("div", { cls: "cnt-tabs", children: [tab("agenda", true), tab("calendario"), tab("info"), tab("intake"), tab("usuario")] }),
      panel("agenda"), panel("calendario"), panel("info"), panel("intake"),
      panel("usuario", [el("div", { attrs: { id: "med-user-panel" }, children: [accountRow] })]),
    ] }),
    el("p", { attrs: { id: "cnt-save-status" } }),
    el("div", { attrs: { id: "med-account-outcome" } }),
    notice,
    editorRow,
  ] });
  return { sec, accountRow, editorRow, notice, tabs: queryAll(sec, ".cnt-tab") };
}

function wireTabs(sec) {
  const context = {
    $: (s, r) => (r || sec).querySelector(s),
    $$: (s, r) => Array.prototype.slice.call((r || sec).querySelectorAll(s)),
    listen: (target, type, handler) => { target.handlers[type] = handler; },
    // the edge fade of the row is a browser-only concern (admin-tab-row-edges)
    wireTabEdges: () => {},
  };
  vm.runInNewContext(fnSource("wireMedicoTabs") + "\nwireMedicoTabs(sec);", { ...context, sec });
}
const click = (tab) => tab.handlers.click({ preventDefault() {} });

test("Usuario tab hides the doctor form's Guardar row and leaves the account buttons visible", () => {
  const { sec, accountRow, editorRow, notice, tabs } = editorFixture();
  wireTabs(sec);
  assert.notEqual(editorRow.style.display, "none", "Horarios is editable: Guardar shows");
  click(tabs.find((t) => t.attrs["data-tab"] === "usuario"));
  assert.equal(editorRow.style.display, "none", "nothing of the doctor form to save on Usuario");
  assert.notEqual(accountRow.style.display, "none", "reset / 2FA / Desactivar stay visible");
  assert.equal(notice.hidden, true, "no translated field on Usuario: no translation notice");
  click(tabs.find((t) => t.attrs["data-tab"] === "info"));
  assert.notEqual(editorRow.style.display, "none");
  assert.notEqual(accountRow.style.display, "none");
  assert.equal(notice.hidden, false, "«Información básica» has the translated field (specialty): its caption shows above Guardar, under the form, so the tabs never move");
});

test("«Nuevo médico» outcome sits at the top of the editor and is brought into view", () => {
  const render = fnSource("renderForm");
  const host = render.indexOf('id="med-account-outcome"');
  const form = render.indexOf('<form id="cnt-form">');
  assert.ok(host > 0 && form > 0 && host < form, "outcome host renders before the form, under the title");
  const show = fnSource("showCreatedAccount");
  assert.match(show, /scrollIntoView/);
  assert.match(show, /\.focus\(/);
});

test("the doctor account's action pills wrap at phone width instead of running past the screen or stacking one per line", () => {
  const css = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
  // The phone rule keeps every .cnt-actions on ONE row (Guardar + danger);
  // the account row carries five pills, so it needs its own, stronger wrap —
  // at 390px «Restablecer contraseña» and «Restablecer 2FA» share a line.
  assert.match(css, /\.doctor-user-actions\.cnt-actions\{flex-wrap:wrap;\}/);
  assert.doesNotMatch(css, /\.doctor-user-actions\.cnt-actions\{flex-wrap:nowrap;\}/);
  assert.doesNotMatch(css, /\.doctor-user-actions\.cnt-actions\{flex-direction:column/);
  // The grid rules .cnt-actions always overrode are gone.
  assert.doesNotMatch(css, /\.doctor-user-actions\{display:grid/);
  assert.doesNotMatch(css, /\.doctor-user-actions\{grid-template-columns/);
  assert.ok(
    css.indexOf(".cnt-actions{align-items:center;justify-content:flex-start;flex-direction:row;flex-wrap:nowrap;}")
      > 0,
    "the phone nowrap rule for Guardar + danger still exists");
});

test("the doctor account's buttons are the panel's buttons: shared height and type, 44px on touch", () => {
  const css = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
  // The catalog form's 48px Guardar is not the account's (audit 2026-11-05).
  assert.match(css, /\.adm5-main \.doctor-user-actions\.cnt-actions \.btn\{min-height:var\(--adm-btn-h\);\}/);
  assert.match(css, /@media\(pointer:coarse\)\{\.adm5-main \.doctor-user-actions\.cnt-actions \.btn\{min-height:44px;\}\}/);
  // No size of their own on the invitation and «Desactivar» buttons either.
  assert.doesNotMatch(css, /\.doctor-user-danger \.btn\{/);
  const link = css.match(/\.doctor-user-link-actions \.btn\{([^}]*)\}/);
  assert.ok(link, "the invitation button keeps its width rule");
  assert.doesNotMatch(link[1], /min-height|font-size|padding/);
});

test("the Médicos list refresh reloads centers and services so names, not slugs, render", () => {
  const refresh = fnSource("refreshMedicosList");
  assert.match(refresh, /ensureCentros\(\)/);
  assert.match(refresh, /ensureServicios\(\)/);
});
