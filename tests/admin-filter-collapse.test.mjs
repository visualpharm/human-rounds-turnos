// tests/admin-filter-collapse.test.mjs — phone filter collapse (2026-09-15).
//
// Proves three things, at the same altitude the real UI uses them:
//   1. admin-direct.css hides the selects of a CLOSED row at ≤640px and the
//      toggle itself above 640px (desktop stays inline, no toggle at all).
//   2. admin-control-fit.mjs exports enhanceFilterBars, plants exactly ONE
//      icon toggle per row (idempotent), counts selects with selectedIndex>0
//      in its badge, keeps the change-event count live, and remembers the
//      open state across a row re-render (the modules rebuild innerHTML).
//   3. Solicitudes' existing text button keeps its i18n aria-label and swaps
//      the "Filtros" label span for the same icon, without a window global.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const directCss = fs.readFileSync(new URL("../static/root/admin-direct.css", import.meta.url), "utf8");
const colaScreen = fs.readFileSync(new URL("../templates/admin/cola.html", import.meta.url), "utf8");

// ---- minimal fake DOM: only the selectors admin-control-fit.mjs uses ----
class ClassList {
  constructor(el) { this.el = el; }
  contains(c) { return this.el.classes.has(c); }
  add(c) { this.el.classes.add(c); }
  remove(c) { this.el.classes.delete(c); }
  toggle(c, force) {
    const on = force === undefined ? !this.el.classes.has(c) : !!force;
    if (on) this.el.classes.add(c); else this.el.classes.delete(c);
    return on;
  }
}
class FakeEl {
  constructor(tag, cls) {
    this.tagName = tag;
    this.classes = new Set((cls || "").split(/\s+/).filter(Boolean));
    this.classList = new ClassList(this);
    this.children = []; this.parentNode = null;
    this.attributes = {}; this.listeners = {};
    this.selectedIndex = 0; this.hidden = false;
    this.innerHTML = ""; this.textContent = ""; this.type = "";
    this.style = { setProperty() {} };
  }
  // The module (and the screens it mimics) assign `el.className = "…"` —
  // keep the class Set and the string property the same source of truth.
  get className() { return Array.from(this.classes).join(" "); }
  set className(v) { this.classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  insertBefore(node, ref) {
    node.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(node); else this.children.splice(i, 0, node);
    return node;
  }
  replaceChild(next, prev) {
    next.parentNode = this;
    this.children.splice(this.children.indexOf(prev), 1, next);
    return prev;
  }
  get nextSibling() {
    const p = this.parentNode;
    if (!p) return null;
    const i = p.children.indexOf(this);
    return i >= 0 && i + 1 < p.children.length ? p.children[i + 1] : null;
  }
  get firstChild() { return this.children[0] || null; }
  addEventListener(name, cb) { (this.listeners[name] = this.listeners[name] || []).push(cb); }
  // Compound-selector matcher: tag, .class, [attr], [attr^="value"],
  // tag[type="value"] — every shape the module queries, nothing more.
  matches(sel) {
    return sel.split(",").map(s => s.trim()).some(part => {
      if (part.includes(" ")) return false; // descendant handled below
      let m = part.match(/^([a-z]+)?\[([a-zA-Z-]+)(\^?=)?(?:"([^"]*)"|'([^']*)')?\]$/);
      if (m) {
        const [, tag, attr, op, v1, v2] = m;
        if (tag && this.tagName !== tag) return false;
        if (!(attr in this.attributes)) return false;
        const val = v1 !== undefined ? v1 : (v2 !== undefined ? v2 : "");
        if (op === "^=") return String(this.attributes[attr]).startsWith(val);
        return val === "" || String(this.attributes[attr]) === val;
      }
      m = part.match(/^([a-z]+)?\.([A-Za-z0-9_-]+)$/);
      if (m) return (!m[1] || this.tagName === m[1]) && this.classes.has(m[2]);
      return this.tagName === part;
    });
  }
  descendants() {
    const out = [];
    const walk = el => { el.children.forEach(c => { out.push(c); walk(c); }); };
    walk(this);
    return out;
  }
  querySelectorAll(sel) {
    const parts = sel.split(",").map(s => s.trim());
    const found = [];
    for (const part of parts) {
      if (part.includes(" ")) {
        const [anc, desc] = part.split(/\s+/);
        for (const el of this.descendants()) {
          if (!el.matches(desc)) continue;
          let p = el.parentNode;
          while (p && p.matches) { if (p.matches(anc)) { found.push(el); break; } p = p.parentNode; }
        }
      } else {
        this.descendants().forEach(el => { if (el.matches(part)) found.push(el); });
        if (this.matches(part)) found.unshift(this);
      }
    }
    return found;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) {
    let el = this;
    while (el) { if (el.matches && el.matches(sel)) return el; el = el.parentNode; }
    return null;
  }
}

const listeners = new Map();
globalThis.document = {
  readyState: "loading",
  body: new FakeEl("body"),
  querySelectorAll() { return []; },
  addEventListener(name, cb) { listeners.set(name, cb); },
  createElement(tag) {
    if (tag === "canvas") return { getContext: () => ({ font: "", measureText: t => ({ width: t.length * 8 }) }) };
    return new FakeEl(tag);
  },
};
globalThis.window = {};
globalThis.MutationObserver = class { observe() {} };
globalThis.getComputedStyle = () => ({
  fontStyle: "normal", fontVariant: "normal", fontWeight: "400",
  fontSize: "16px", fontFamily: "sans-serif",
  paddingLeft: "8px", paddingRight: "32px",
  borderLeftWidth: "1px", borderRightWidth: "1px",
});

const ControlFit = await import("../static/platform/admin-control-fit.mjs");

function buildRow() {
  const row = new FakeEl("div", "queue-controls");
  const search = new FakeEl("input", "adm-search queue-search");
  search.setAttribute("type", "search");
  const sel1 = new FakeEl("select", "adm-sel");
  const sel2 = new FakeEl("select", "adm-sel");
  sel2.selectedIndex = 1; // one active filter
  row.appendChild(search);
  row.appendChild(sel1);
  row.appendChild(sel2);
  return { row, search, sel1, sel2 };
}
function buildSection() {
  const sec = new FakeEl("div", "");
  sec.setAttribute("id", "sec-cola");
  return sec;
}

// ---- CSS contract -------------------------------------------------------
test("closed filter rows hide their selects at ≤640px and no toggle survives above 640px", () => {
  assert.match(directCss, /@media\(min-width:641px\)\{\s*\.adm5-main \.adm-filter-toggle,\.adm5-main #adm-filter-toggle\{display:none;\}/);
  // admin-solicitudes.mjs injects «#sec-solicitudes .adm-filter-toggle» AFTER
  // this stylesheet (same 1-1-0 specificity: order wins), so hiding the
  // Solicitudes toggle needs a strictly higher one.
  assert.match(directCss, /\.adm5-main #adm-filter-toggle\.adm-filter-toggle\{display:none;\}/);
  assert.match(directCss, /\.adm5-main #tools:not\(\.filters-open\) \.adm-filter-fields\{display:none;\}/);
  // A chooser marked .adm-picker («Mis pacientes» doctor) is not a filter: never folded.
  assert.match(directCss, /\.adm5-main :is\(\.cnt-toolbar-filters,\.queue-controls\):not\(\.filters-open\) select\.adm-sel:not\(\.adm-picker\)\{display:none;\}/);
  // The phone toggle is a 44px secondary icon button; when open, each select
  // returns inline with its measured width instead of the queue's full-width stack.
  assert.match(directCss, /@media\(max-width:640px\)\{\s*\.adm5-main \.adm-filter-toggle,\.adm5-main #adm-filter-toggle\{width:44px;min-width:44px;height:44px;min-height:44px;padding:0;/);
  assert.match(directCss, /\.adm5-main :is\(\.cnt-toolbar-filters,\.queue-controls\)\.filters-open select\.adm-sel,\s*\.adm5-main \.cnt-toolbar-filters:has\(#pac-q\)\.filters-open select\.adm-sel\{flex:0 1 auto;width:var\(--control-fit-width,auto\);min-width:0;max-width:100%;\}/);
  assert.match(directCss, /\.adm5-main :is\(\.cnt-toolbar-filters,\.queue-controls\) :is\(input\[type=search\],\.cnt-search,\.adm-search\),\s*\.adm5-main \.cnt-toolbar-filters:has\(#pac-q\) #pac-q\{flex:1 1 auto;min-width:0;/);
});

test("the filter funnel is the Icons8 file, never a drawn svg", () => {
  const controlFit = fs.readFileSync(new URL("../static/platform/admin-control-fit.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(controlFit, /<svg/);
  assert.match(directCss, /\.adm5-main \.adm-filter-toggle \.adm-filter-icon\{[^}]*mask:url\(\/static\/icons\/filter-sf-regular\.svg\) center\/contain no-repeat/);
  assert.ok(fs.existsSync(new URL("../static/icons/filter-sf-regular.svg", import.meta.url)));
});

test("the cola screen loads the canonical control-fit module", () => {
  assert.match(colaScreen, /type="module" src="\{\{ static\('platform\/admin-control-fit\.mjs'\) \}\}"/);
});

// ---- module behaviour ---------------------------------------------------
test("enhanceFilterBars exists without a compatibility global and guards bad roots", () => {
  assert.equal(typeof ControlFit.enhanceFilterBars, "function");
  assert.equal(window.SanaControlFit, undefined);
  assert.doesNotThrow(() => ControlFit.enhanceFilterBars(null));
  assert.doesNotThrow(() => ControlFit.enhanceFilterBars({}));
});

test("one icon toggle lands right after the search, badge counts active selects, re-runs do not duplicate", () => {
  const sec = buildSection();
  const { row, search } = buildRow();
  sec.appendChild(row);
  ControlFit.enhanceFilterBars(sec);

  const toggles = row.querySelectorAll("[data-filter-toggle]");
  assert.equal(toggles.length, 1);
  const btn = toggles[0];
  assert.equal(btn.getAttribute("aria-expanded"), "false");
  assert.equal(btn.getAttribute("aria-label"), "Filtros");
  assert.equal(search.nextSibling, btn, "toggle sits next to the search so both share the visible row");

  const icon = btn.querySelector(".adm-filter-icon");
  assert.ok(icon, "icon holder exists");
  // Icons are never drawn (rule 04.10): the funnel is the Icons8 file,
  // painted through a CSS mask in the button's own ink — no inline <svg>.
  assert.equal(icon.innerHTML, "");
  assert.equal(icon.getAttribute("aria-hidden"), "true");

  const badge = btn.querySelector(".adm-filter-count");
  assert.ok(badge, "badge exists");
  assert.equal(badge.textContent, "1"); // exactly one select has selectedIndex > 0
  assert.equal(badge.hidden, false);

  ControlFit.enhanceFilterBars(sec); // MutationObserver re-runs are idempotent
  assert.equal(row.querySelectorAll("[data-filter-toggle]").length, 1);
});

test("a queue row whose only folded control is «Mostrar horarios libres» still gets its funnel", () => {
  const sec = buildSection();
  const row = new FakeEl("div", "queue-controls");
  const search = new FakeEl("input", "adm-search queue-search");
  search.setAttribute("type", "search");
  const filters = new FakeEl("div", "queue-filters");
  filters.appendChild(new FakeEl("label", "queue-free-chk"));
  row.appendChild(search);
  row.appendChild(filters);
  sec.appendChild(row);
  ControlFit.enhanceFilterBars(sec);
  assert.equal(row.querySelectorAll("[data-filter-toggle]").length, 1);
  const bare = new FakeEl("div", "queue-controls");
  bare.appendChild(new FakeEl("input", "adm-search"));
  const other = buildSection();
  other.appendChild(bare);
  ControlFit.enhanceFilterBars(other);
  assert.equal(bare.querySelectorAll("[data-filter-toggle]").length, 0, "nothing to fold, no funnel");
});

test("changing a select keeps the badge live through the shared change listener", () => {
  const sec = buildSection();
  const { row, sel1, sel2 } = buildRow();
  sec.appendChild(row);
  ControlFit.enhanceFilterBars(sec);
  const btn = row.querySelector("[data-filter-toggle]");
  const badge = btn.querySelector(".adm-filter-count");

  ControlFit.boot(); // registers the document-level change listener
  const change = listeners.get("change");
  assert.equal(typeof change, "function");

  sel1.selectedIndex = 1;
  change({ target: sel1 });
  assert.equal(badge.textContent, "2");

  sel1.selectedIndex = 0;
  sel2.selectedIndex = 0;
  change({ target: sel2 });
  assert.equal(badge.textContent, "0");
  assert.equal(badge.hidden, true, "an empty count hides the badge");
});

test("opening a row survives the module re-rendering that row", () => {
  const sec = buildSection();
  const first = buildRow();
  sec.appendChild(first.row);
  ControlFit.enhanceFilterBars(sec);
  const btn = first.row.querySelector("[data-filter-toggle]");

  btn.listeners.click[0]();
  assert.ok(first.row.classList.contains("filters-open"));
  assert.equal(btn.getAttribute("aria-expanded"), "true");

  // admin-queue.mjs rebuilds sec.innerHTML: same section, brand-new row.
  sec.children.length = 0;
  const second = buildRow();
  second.sel1.selectedIndex = 2;
  sec.appendChild(second.row);
  ControlFit.enhanceFilterBars(sec);
  assert.ok(second.row.classList.contains("filters-open"), "open state re-applied per section");
  const btn2 = second.row.querySelector("[data-filter-toggle]");
  assert.equal(btn2.getAttribute("aria-expanded"), "true");
  assert.equal(btn2.querySelector(".adm-filter-count").textContent, "2");
});

test("Solicitudes' text button keeps its aria-label and swaps the label span for the icon", () => {
  const tools = new FakeEl("div", "adm-tools");
  tools.setAttribute("id", "tools");
  const btn = new FakeEl("button", "adm-filter-toggle");
  btn.setAttribute("id", "adm-filter-toggle");
  btn.setAttribute("aria-label", "Filtros");
  const label = new FakeEl("span", "adm-filter-label");
  label.textContent = "Filtros";
  btn.appendChild(label);
  tools.appendChild(btn);

  ControlFit.enhanceFilterBars(tools);
  assert.equal(btn.querySelector(".adm-filter-label"), null, "text label replaced");
  assert.equal(btn.querySelector(".adm-filter-icon").innerHTML, "");
  assert.equal(btn.querySelector(".adm-filter-icon").getAttribute("aria-hidden"), "true");
  assert.equal(btn.getAttribute("aria-label"), "Filtros", "i18n aria-label survives");
  // The button itself is not re-wired: admin-app.js already owns its click.
  assert.equal(btn.listeners.click, undefined);
});
