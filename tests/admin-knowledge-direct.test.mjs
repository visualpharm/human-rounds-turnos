// admin-knowledge-direct.test.mjs — Tier 5 Knowledge/Wiki direct screen
// source contracts. Same regex-on-source pattern as
// admin-preparations.test.mjs: node --test over admin-knowledge.js and
// templates/admin/knowledge.html, no browser, no network.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static", "platform", "admin-knowledge.mjs"), "utf8");
const TPL = readFileSync(join(ROOT, "templates", "admin", "knowledge.html"), "utf8");

test("direct screen boots only for its server-declared owner", () => {
  assert.match(JS, /export function mount\(\)/);
  assert.doesNotMatch(JS, /sanaRouteKnowledge|directScreen\(/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
});

test("direct screen boots on DOMContentLoaded and uses popstate for Back/Forward", () => {
  assert.match(JS, /export function mount\(\)/);
  assert.match(JS, /window\.addEventListener\("popstate"/);
  assert.match(
    JS,
    /window\.addEventListener\("beforeunload", onBeforeUnload\);/,
  );
  assert.match(JS, /function rememberHistoryReturnFocus\(\)/);
  assert.match(JS, /dialog\.returnHref = itemHref\(id\)/);
  assert.match(JS, /function onPopstate\(\) \{ if \(mounted && sec\(\)\) \{ rememberHistoryReturnFocus\(\); renderEditor\(\); \} \}/);
});

test("knowledge owns direct query state without a hash compatibility branch", () => {
  assert.match(JS, /function itemHref\(id\) \{ return location\.pathname \+ "\?item=" \+ encodeURIComponent\(id\); \}/);
  assert.match(JS, /setDirectItemUrl\(""\); renderEditor\(\);/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
});

test("direct URL state is ?item=<id>, never a hash shim", () => {
  assert.match(JS, /searchParams\.set\("item"/);
  assert.match(JS, /new URLSearchParams\(location\.search\)\.get\("item"\)/);
  assert.match(JS, /location\.pathname \+ "\?item=" \+ encodeURIComponent/);
  assert.doesNotMatch(TPL, /location\.hash\s*=/);
});

test("CRUD, soft-disable, restore and AI-assist stay on the existing API", () => {
  assert.match(JS, /\/api\/admin\/content\/knowledge/);
  assert.match(JS, /method: creating \? "POST" : "PUT"/);
  assert.match(JS, /method: "DELETE"/);
  assert.match(JS, /\/restaurar"/);
  assert.match(JS, /knowledge\/properties/);
  assert.match(JS, /data-kn-title/);
  assert.match(JS, /function generateTitle\(form\)/);
  assert.doesNotMatch(JS, /data-kn-properties/);
  assert.match(JS, /data-kn-delete/);
  assert.match(JS, /data-kn-restore/);
});

test("knowledge.html loads fingerprinted i18n then admin-knowledge, no hash shim", () => {
  assertSharedShellContract(assert, TPL, []);
  assert.match(TPL, /SANA_STATIC\["platform\/admin-knowledge\.mjs"\]/);
  assert.match(TPL, /id="sec-knowledge"/);
  assert.match(TPL, /extends "admin\/base.html"/);
  assert.doesNotMatch(TPL, /location\.hash\s*=/);
  assert.doesNotMatch(TPL, /<hr\b/);
  assert.doesNotMatch(TPL, /border-top|border-bottom/);
});

test("layout stays stacked without overflow and without dividers", () => {
  assert.match(JS, /container:knowledge \/ inline-size/);
  assert.match(JS, /grid-template-columns:minmax\(220px,260px\) minmax\(0,1fr\)/);
  assert.match(JS, /@container knowledge \(max-width:900px\)/);
  assert.match(JS, /@container knowledge \(max-width:600px\)/);
  assert.match(JS, /\.kn-editor\.is-open\{position:fixed;inset:0/);
  assert.doesNotMatch(JS, /<hr/);
});

test("wiki navigation starts compact and only expands the useful branch", () => {
  assert.match(JS, /treeInitialized: false/);
  assert.match(JS, /state\.collapsed\.add\(item\.id\)/);
  assert.match(JS, /if \(item && item\.parent_id\) state\.collapsed\.delete\(item\.parent_id\)/);
  assert.match(JS, /\.kn-node--article\{margin-left:20px/);
  assert.match(JS, /\.kn-node small\{display:none;\}/);
  assert.doesNotMatch(JS, /subtitulo_wiki/);
  assert.doesNotMatch(JS, /<h2 class=\"adm-h1\">' \+ T\("admin\.content\.knowledge\.wiki"/);
});

test("wiki search and editor form controls are at least 16px", () => {
  assert.match(JS, /\.kn-search\{[^}]*font-size:16px/);
  assert.match(JS, /\.kn-field input,\.kn-field select,\.kn-field textarea,\.kn-row input,\.kn-row select\{[^}]*font-size:16px/);
  // The toolbar's 44px height is the shared list row rule in admin-direct.css
  // (tests/admin-list-row-height.test.mjs); the wiki keeps no height of its own.
  assert.match(JS, /\.kn-toolbar \.btn\{padding:0 14px/);
  assert.doesNotMatch(JS, /\.kn-toolbar \.btn\{min-height/);
  assert.doesNotMatch(JS, /\.kn-search\{[^}]*min-height/);
});

test("<=900px editor dialog has focus entry, containment, inert cover, Escape, and restore", () => {
  assert.match(JS, /wrap\.getBoundingClientRect\(\)\.width <= 900 && editor\.classList\.contains\("is-open"\)/);
  assert.match(JS, /setAttribute\("inert"/);
  assert.match(JS, /removeAttribute\("inert"/);
  assert.match(JS, /e\.key === "Escape"/);
  assert.match(JS, /e\.key !== "Tab"|e\.key === "Tab"/);
  assert.match(JS, /rememberReturnFocus/);
  assert.match(JS, /restoreReturnFocus/);
  assert.match(JS, /enterDialogFocus/);
  assert.match(JS, /setDirectItemUrl\(saved\.id\)/);
  assert.match(JS, /setDirectItemUrl\(""\)/);
});

test("2026-09-05 brief: tree like the sidebar, one toolbar line, labels left, friendly dates, tips, golden check", () => {
  // tree reads like the admin sidebar: 14px, dark --hint-ink, small aligned chevron column
  assert.match(JS, /\.kn-node\{[^}]*color:var\(--hint-ink,var\(--text-2\)\);font-size:14px/);
  assert.match(JS, /\.kn-category-row\{display:grid;grid-template-columns:20px minmax\(0,1fr\)/);
  // The chevron is the Icons8 expand arrow of the project pack as a mask, in a
  // 44 px touch zone, never a typed glyph (09.11). The icon starts on the panel's
  // left edge; the zone reaches one gutter to the left, so on a phone it ends at
  // the screen edge instead of 8 px past it (10.11).
  assert.match(JS, /<span aria-hidden="true"><\/span><\/button><a class="kn-node kn-node--category/);
  assert.match(JS, /\.kn-toggle\{[^}]*justify-content:flex-start;[^}]*width:44px;height:44px;margin:-6px 0 -6px calc\(-1 \* var\(--adm-gutter,16px\)\);padding:0 0 0 var\(--adm-gutter,16px\);/);
  assert.match(JS, /\.kn-toggle span\{[^}]*mask:url\(\/static\/icons\/expand-arrow-sf-regular\.svg\)/);
  assert.match(JS, /\.kn-toggle\[aria-expanded=false\] span\{transform:rotate\(-90deg\);\}/);
  // The golden check marks: the Icons8 «ok» / «cancel» pair (a check and a
  // cross in a circle) as masks, named for a screen reader — not the close
  // button's cross, which only .hr-x draws (tests/close-icon.test.mjs).
  assert.match(JS, /\.kn-golden-mark\{[^}]*mask:url\(\/static\/icons\/golden-ok-sf-regular\.svg\)/);
  assert.match(JS, /\.kn-golden-list li\.fail \.kn-golden-mark\{[^}]*mask-image:url\(\/static\/icons\/golden-fail-sf-regular\.svg\)/);
  assert.match(JS, /<span class="kn-golden-mark" role="img" aria-label="/);
  assert.doesNotMatch(JS, /[⌄›✓✕]/);
  // no boxed panes
  assert.doesNotMatch(JS, /\.kn-list,\.kn-editor\{border:1px solid/);
  // One screen header (the product owner, 2026-11-08): the shared «Wiki» title stays,
  // «Nuevo artículo» on its line; ONE toolbar line: search, then new category
  // and the golden check as bare icons named by their tooltip.
  assert.match(JS, /<div class="kn-toolbar"><input id="kn-search"[\s\S]*iconButton\('data-kn-new="categoria"', "folder-add"[\s\S]*iconButton\("data-data-tool data-kn-golden", "chat"/);
  assert.doesNotMatch(JS, /h1\.classList\.add\("kn-sr-only"\)/);
  assert.match(JS, /button\.className = "btn btn-accent adm-title-action";\s*button\.setAttribute\("data-kn-new", "articulo"\)/);
  // labels left of narrow inputs in one grid
  assert.match(JS, /\.kn-rows\{display:grid;grid-template-columns:max-content minmax\(0,1fr\)/);
  assert.match(JS, /<label class="kn-row" for="kn-' \+ name \+ '"><span>' \+ label \+ '<\/span>/);
  // friendly dates through the shared formatter
  assert.match(JS, /importDep\("platform\/dates\.mjs"/);
  assert.match(JS, /Dates\.dia\(iso\)/);
  assert.match(JS, /data-kn-when="review"/);
  // why + tips where the editor would be
  assert.match(JS, /<details><summary>' \+ T\("admin\.content\.knowledge\.about_tips"/);
  assert.doesNotMatch(JS, /admin\.content\.knowledge\.about_titulo/);
  assert.match(JS, /admin\.content\.knowledge\.tip_4/);
  // golden conversations against the live wiki
  assert.match(JS, /endpoint\(\) \+ "\/golden"/);
  assert.match(JS, /admin\.content\.knowledge\.golden_fallan/);
});
